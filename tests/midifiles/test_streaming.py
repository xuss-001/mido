# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

import gc
import io
import tracemalloc

import pytest

import mido
from mido import Message, MetaMessage, MidiFile, MidiTrack
from mido.midifiles.streaming import MidiFileStream


def tempo_order_file():
    mid = MidiFile(type=1, ticks_per_beat=480)

    track0 = mid.add_track()
    track0.append(MetaMessage("set_tempo", tempo=500000, time=0))
    track0.append(Message("note_on", note=60, velocity=64, time=0))
    track0.append(MetaMessage("set_tempo", tempo=250000, time=480))
    track0.append(Message("note_off", note=60, velocity=64, time=480))

    track1 = mid.add_track()
    track1.append(Message("note_on", note=64, velocity=64, time=0))
    track1.append(Message("note_off", note=64, velocity=64, time=960))

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def type2_file():
    mid = MidiFile(type=2, ticks_per_beat=120)
    track0 = mid.add_track()
    track0.append(Message("note_on", note=60, time=12))
    track0.append(Message("note_off", note=60, time=24))
    track1 = mid.add_track()
    track1.append(Message("control_change", control=7, value=100, time=36))

    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def test_stream_track_returns_tick_deltas():
    reader = MidiFileStream(file=io.BytesIO(tempo_order_file()))
    messages = list(reader.iter_track(0))

    assert [message.type for message in messages] == [
        "set_tempo",
        "note_on",
        "set_tempo",
        "note_off",
        "end_of_track",
    ]
    assert [message.time for message in messages[:4]] == [0, 0, 480, 480]
    reader.close()


def test_stream_tracks_are_reiterable():
    reader = MidiFileStream(io.BytesIO(tempo_order_file()))
    track = reader.tracks[1]

    first = list(track)
    second = list(reader.iter_track(1))

    assert first == second
    assert [message.time for message in first[:2]] == [0, 960]


def test_independent_iterators_same_track_can_interleave():
    reader = MidiFileStream(file=io.BytesIO(tempo_order_file()))
    first = reader.iter_track(0)
    second = reader.iter_track(0)

    first_messages = [next(first), next(first)]
    second_message = next(second)
    first_messages.append(next(first))
    second_messages = [second_message, next(second), next(second)]

    assert [message.time for message in first_messages] == [0, 0, 480]
    assert [message.time for message in second_messages] == [0, 0, 480]
    assert list(second) == list(reader.iter_track(0))[3:]


def test_different_track_iterators_and_shared_stream_readers_are_independent():
    stream = io.BytesIO(tempo_order_file())
    reader1 = MidiFileStream(file=stream)
    reader2 = mido.MidiFileReader(file=stream)

    track0 = reader1.iter_track(0)
    track1 = reader2.iter_track(1)

    assert next(track0).type == "set_tempo"
    assert next(track1).type == "note_on"
    assert next(track0).type == "note_on"
    assert next(track1).type == "note_off"
    assert next(track0).type == "set_tempo"

    assert list(reader1.iter_track(1))[1].type == "note_off"
    assert list(reader2.iter_track(0))[2].type == "set_tempo"

    reader1.close()
    reader2.close()
    assert not stream.closed


def test_stopped_iteration_can_be_restarted_completely():
    data = tempo_order_file()
    reader = MidiFileStream(file=io.BytesIO(data))

    track_iterator = reader.iter_track(0)
    next(track_iterator)
    next(track_iterator)
    track_iterator.close()

    expected_tracks = [
        list(MidiFile(file=io.BytesIO(data)).tracks[index])
        for index in range(2)
    ]
    assert list(reader.iter_track(0)) == expected_tracks[0]

    merged = reader.iter_merged()
    for _ in range(3):
        next(merged)
    merged.close()

    assert list(reader.iter_merged()) == list(
        MidiFile(file=io.BytesIO(data))
    )


def test_merged_order_and_tempo_times():
    reader = MidiFileStream(file=io.BytesIO(tempo_order_file()))
    messages = [
        message
        for message in reader.iter_merged()
        if message.type != "end_of_track"
    ]

    assert [
        (message.type, getattr(message, "note", None))
        for message in messages
    ] == [
        ("set_tempo", None),
        ("note_on", 60),
        ("note_on", 64),
        ("set_tempo", None),
        ("note_off", 60),
        ("note_off", 64),
    ]
    assert [round(message.time, 6) for message in messages] == [
        0.0,
        0.0,
        0.0,
        0.5,
        0.25,
        0.0,
    ]


def test_merged_tick_mode_preserves_same_time_order():
    reader = MidiFileStream(file=io.BytesIO(tempo_order_file()))
    messages = list(
        reader.iter_merged(time_unit="ticks", end_of_track="keep")
    )

    assert [
        (message.time, message.type, getattr(message, "note", None))
        for message in messages
    ] == [
        (0, "set_tempo", None),
        (0, "note_on", 60),
        (0, "note_on", 64),
        (480, "set_tempo", None),
        (480, "note_off", 60),
        (0, "end_of_track", None),
        (0, "note_off", 64),
        (0, "end_of_track", None),
    ]


def test_type2_supports_tracks_but_not_merging():
    reader = MidiFileStream(file=io.BytesIO(type2_file()))

    assert reader.type == 2
    assert [message.time for message in reader.iter_track(0)] == [12, 24, 0]
    assert [message.time for message in reader.tracks[1]] == [36, 0]

    with pytest.raises(TypeError):
        reader.iter_merged()
    with pytest.raises(TypeError):
        iter(reader)


def test_path_is_opened_and_closed_by_reader(tmp_path):
    path = tmp_path / "streaming.mid"
    path.write_bytes(tempo_order_file())

    reader = mido.open_midifile(path)
    assert not reader.stream.closed
    assert next(reader.iter_track(1)).type == "note_on"

    owned_file = reader.stream
    reader.close()

    assert reader.closed
    assert owned_file.closed


def test_caller_stream_is_not_closed_by_context_manager():
    stream = io.BytesIO(tempo_order_file())

    with MidiFileStream(file=stream) as reader:
        assert next(iter(reader)).type == "set_tempo"

    assert reader.closed
    assert not stream.closed


class CountingStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.bytes_requested = 0

    def read(self, size=-1):
        data = super().read(size)
        self.bytes_requested += len(data)
        return data


def test_first_message_does_not_parse_whole_track():
    mid = MidiFile(type=0, ticks_per_beat=480)
    track = mid.add_track()
    for index in range(1000):
        track.append(
            Message(
                "note_on" if index % 2 == 0 else "note_off",
                note=40 + index % 40,
                velocity=80,
                time=10,
            )
        )
    buf = io.BytesIO()
    mid.save(file=buf)
    data = buf.getvalue()
    stream = CountingStream(data)
    reader = MidiFileStream(file=stream)

    message = next(reader.iter_track(0))

    assert message.type == "note_on"
    assert len(data) > 2000
    assert stream.bytes_requested < 64
    reader.close()


def test_first_merged_message_only_reads_track_heads():
    mid = MidiFile(type=1, ticks_per_beat=480)
    for _ in range(2):
        track = mid.add_track()
        for index in range(1000):
            track.append(
                Message(
                    "note_on" if index % 2 == 0 else "note_off",
                    note=40 + index % 40,
                    velocity=80,
                    time=10,
                )
            )

    buf = io.BytesIO()
    mid.save(file=buf)
    data = buf.getvalue()
    stream = CountingStream(data)
    reader = MidiFileStream(file=stream)

    message = next(reader.iter_merged(time_unit="ticks"))

    assert message.type == "note_on"
    assert len(data) > 5_000
    assert stream.bytes_requested < 256
    reader.close()


def write_large_file(path, event_count):
    mid = MidiFile(type=1, ticks_per_beat=480)
    track = mid.add_track()
    for index in range(event_count):
        track.append(
            Message(
                "note_on" if index % 2 == 0 else "note_off",
                note=40 + index % 40,
                velocity=80,
                time=120,
            )
        )
    mid.save(path)


def reader_memory_after_start(path):
    gc.collect()
    tracemalloc.start()

    reader = MidiFileStream(path)
    iterator = reader.iter_merged(time_unit="ticks")
    for _ in range(10):
        next(iterator)

    _, current = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    reader.close()
    return current


def test_reader_memory_does_not_grow_with_event_count(tmp_path):
    small_path = tmp_path / "small.mid"
    large_path = tmp_path / "large.mid"
    write_large_file(small_path, 300)
    write_large_file(large_path, 12000)

    small_memory = reader_memory_after_start(small_path)
    large_memory = reader_memory_after_start(large_path)

    # Twelve thousand parsed Message objects would take megabytes; the lazy
    # reader should retain only one lookahead message per track.
    assert large_memory - small_memory < 300_000


def test_midifile_lazy_switch_returns_stream_reader():
    reader = MidiFile(
        file=io.BytesIO(tempo_order_file()),
        lazy=True,
    )
    assert isinstance(reader, MidiFileStream)
    assert next(reader.iter_track(0)).type == "set_tempo"
    reader.close()


def test_existing_midifile_still_loads_eagerly():
    data = tempo_order_file()
    mid = MidiFile(file=io.BytesIO(data))

    assert isinstance(mid.tracks[0], MidiTrack)
    assert len(mid.tracks[0]) == 5
    assert len(list(mid)) == 7
