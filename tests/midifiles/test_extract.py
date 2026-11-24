# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

import io

import pytest

import mido
from mido import Message, MetaMessage, MidiFile, MidiTrack
from mido.midifiles.midifiles import SustainPedalError


def test_extract_cross_track_tempo_and_note_boundaries():
    mid = MidiFile(type=1, ticks_per_beat=480)

    tempo_track = MidiTrack([
        MetaMessage('set_tempo', tempo=500000, time=0),
        MetaMessage('set_tempo', tempo=1000000, time=480),
        MetaMessage('set_tempo', tempo=250000, time=480),
    ])
    mid.tracks.append(tempo_track)

    note_track = MidiTrack()
    note_track.name = 'Lead'
    note_track.append(
        Message('program_change', channel=2, program=42, time=0))
    note_track.append(
        Message('control_change', channel=2, control=7, value=80, time=0))
    note_track.append(
        Message('control_change', channel=2, control=10, value=20, time=0))
    note_track.append(
        Message('pitchwheel', channel=2, pitch=1000, time=0))
    note_track.append(
        Message('note_on', channel=2, note=60, velocity=90, time=480))
    note_track.append(
        Message('note_off', channel=2, note=60, velocity=20, time=960))
    mid.tracks.append(note_track)

    original_tracks = repr(mid.tracks)
    segment = mid.extract(1.0, 2.0)

    assert repr(mid.tracks) == original_tracks
    assert segment.type == 1
    assert segment.ticks_per_beat == 480
    assert len(segment.tracks) == 2
    assert segment.length == pytest.approx(1.0)

    assert segment.tracks[0] == MidiTrack([
        MetaMessage('set_tempo', tempo=1000000, time=0),
        MetaMessage('set_tempo', tempo=250000, time=240),
        MetaMessage('end_of_track', time=960),
    ])
    assert segment.tracks[1] == MidiTrack([
        MetaMessage('track_name', name='Lead', time=0),
        Message('program_change', channel=2, program=42, time=0),
        Message('control_change', channel=2, control=7, value=80, time=0),
        Message('control_change', channel=2, control=10, value=20, time=0),
        Message('pitchwheel', channel=2, pitch=1000, time=0),
        Message('note_on', channel=2, note=60, velocity=90, time=0),
        Message('note_off', channel=2, note=60, velocity=20, time=720),
        MetaMessage('end_of_track', time=480),
    ])

    file = io.BytesIO()
    segment.save(file=file)
    file.seek(0)
    loaded = MidiFile(file=file)
    assert loaded.tracks == segment.tracks


def test_extract_includes_start_and_excludes_end_events():
    mid = MidiFile(type=0, ticks_per_beat=100)
    mid.tracks.append(MidiTrack([
        Message('note_on', note=1, velocity=10, time=100),
        Message('note_on', note=2, velocity=20, time=100),
        MetaMessage('text', text='at start'),
        Message('note_off', note=1, velocity=30, time=100),
        MetaMessage('text', text='at end', time=100),
        Message('note_off', note=2, velocity=40, time=0),
    ]))

    segment = mid.extract(1.0, 2.0)

    assert segment.tracks[0] == MidiTrack([
        Message('note_on', note=1, velocity=10, time=0),
        Message('note_on', note=2, velocity=20, time=0),
        MetaMessage('text', text='at start', time=0),
        Message('note_off', note=1, velocity=30, time=100),
        Message('note_off', note=2, velocity=64, time=100),
        MetaMessage('end_of_track', time=0),
    ])


def test_extract_restores_channel_and_meta_state_at_zero():
    mid = MidiFile(type=0, ticks_per_beat=480)
    track = MidiTrack([
        MetaMessage('set_tempo', tempo=1000000, time=0),
        MetaMessage('time_signature',
                    numerator=3,
                    denominator=4,
                    clocks_per_click=24,
                    notated_32nd_notes_per_beat=8),
        Message('program_change', channel=3, program=42, time=0),
        Message('control_change', channel=3, control=7, value=80, time=0),
        Message('control_change', channel=3, control=10, value=20, time=0),
        Message('pitchwheel', channel=3, pitch=1000, time=0),
        Message('note_on', channel=3, note=60, velocity=90, time=0),
        Message('note_off', channel=3, note=60, velocity=20, time=1440),
    ])
    mid.tracks.append(track)

    segment = mid.extract(1.0, 2.0)

    assert segment.length == pytest.approx(1.0)
    assert segment.tracks[0] == MidiTrack([
        MetaMessage('set_tempo', tempo=1000000, time=0),
        MetaMessage('time_signature',
                    numerator=3,
                    denominator=4,
                    clocks_per_click=24,
                    notated_32nd_notes_per_beat=8,
                    time=0),
        Message('program_change', channel=3, program=42, time=0),
        Message('control_change', channel=3, control=7, value=80, time=0),
        Message('control_change', channel=3, control=10, value=20, time=0),
        Message('pitchwheel', channel=3, pitch=1000, time=0),
        Message('note_on', channel=3, note=60, velocity=90, time=0),
        Message('note_off', channel=3, note=60, velocity=64, time=480),
        MetaMessage('end_of_track', time=0),
    ])

    file = io.BytesIO()
    segment.save(file=file)
    file.seek(0)
    loaded = MidiFile(file=file)
    assert loaded.tracks == segment.tracks


def test_extract_restores_state_from_another_track():
    mid = MidiFile(type=1, ticks_per_beat=480)
    mid.tracks.append(MidiTrack([
        Message('program_change', channel=4, program=12, time=0),
        Message('control_change', channel=4, control=7, value=100, time=0),
        Message('pitchwheel', channel=4, pitch=-200, time=0),
    ]))
    mid.tracks.append(MidiTrack([
        Message('note_on', channel=4, note=64, velocity=80, time=480),
        Message('note_off', channel=4, note=64, velocity=20, time=960),
    ]))

    segment = mid.extract(1.0, 2.0)

    assert segment.tracks[0] == MidiTrack([
        Message('program_change', channel=4, program=12, time=0),
        Message('control_change', channel=4, control=7, value=100, time=0),
        Message('pitchwheel', channel=4, pitch=-200, time=0),
        MetaMessage('end_of_track', time=960),
    ])
    assert segment.tracks[1] == MidiTrack([
        Message('note_on', channel=4, note=64, velocity=80, time=0),
        Message('note_off', channel=4, note=64, velocity=20, time=480),
        MetaMessage('end_of_track', time=480),
    ])


def test_extract_empty_segment_keeps_duration_and_tracks():
    mid = MidiFile(type=1, ticks_per_beat=480)
    for _ in range(2):
        mid.tracks.append(MidiTrack([
            Message('note_on', time=0),
            Message('note_off', time=480),
        ]))

    segment = mid.extract(1.0, 2.0)

    assert len(segment.tracks) == 2
    assert segment.length == pytest.approx(1.0)
    for track in segment.tracks:
        assert track == MidiTrack([
            MetaMessage('end_of_track', time=960),
        ])
        assert sum(message.time for message in track) == 960


def test_extract_empty_interval_has_zero_length():
    mid = MidiFile(type=0, ticks_per_beat=480)
    mid.tracks.append(MidiTrack([
        Message('note_on', time=0),
        Message('note_off', time=480),
    ]))

    segment = mid.extract(1.0, 1.0)

    assert segment.length == 0
    assert segment.tracks[0] == MidiTrack([
        MetaMessage('end_of_track', time=0),
    ])


def test_extract_empty_segment_restores_tempo_for_duration():
    mid = MidiFile(type=0, ticks_per_beat=480)
    mid.tracks.append(MidiTrack([
        MetaMessage('set_tempo', tempo=1000000, time=0),
        Message('note_on', time=0),
        Message('note_off', time=479),
    ]))

    segment = mid.extract(1.0, 2.0)

    assert segment.length == pytest.approx(1.0)
    assert segment.tracks[0] == MidiTrack([
        MetaMessage('set_tempo', tempo=1000000, time=0),
        MetaMessage('end_of_track', time=480),
    ])


def test_extract_rejects_sustain_pedal():
    mid = MidiFile(type=0, ticks_per_beat=480)
    mid.tracks.append(MidiTrack([
        Message('control_change', channel=0, control=64, value=127, time=0),
        Message('note_on', time=10),
        Message('note_off', time=10),
    ]))

    with pytest.raises(SustainPedalError, match='sustain pedal'):
        mid.extract(0.0, 1.0)
    with pytest.raises(ValueError, match='sustain pedal'):
        mido.extract_segment(mid, 0.0, 1.0)


def test_extract_rejects_type_2_files():
    mid = MidiFile(type=2)

    with pytest.raises(ValueError, match='type 2'):
        mid.extract(0.0, 1.0)


def test_extract_aliases():
    mid = MidiFile(type=0, ticks_per_beat=480)
    mid.tracks.append(MidiTrack([
        Message('note_on', time=0),
        Message('note_off', time=480),
    ]))

    assert mid.extract_segment(0.0, 1.0).tracks == mid.slice(
        0.0, 1.0).tracks
    assert mid.extract_segment(0.0, 1.0).tracks == mid.cut(
        0.0, 1.0).tracks
    assert mid.extract_segment(0.0, 1.0).tracks == mido.extract_segment(
        mid, 0.0, 1.0).tracks
