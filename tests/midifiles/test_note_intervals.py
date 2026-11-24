# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

import pytest

from mido import Message, MetaMessage, MidiFile, MidiTrack
from mido.midifiles.notes import NoteInterval, NoteIntervalList


def make_file(type, tracks, ticks_per_beat=100):
    return MidiFile(
        type=type,
        ticks_per_beat=ticks_per_beat,
        tracks=[MidiTrack(track) for track in tracks],
    )


def test_basic_interval_ticks_and_seconds():
    mid = make_file(0, [[
        Message('note_on', channel=2, note=60, velocity=100, time=0),
        Message('note_off', channel=2, note=60, velocity=0, time=200),
    ]])

    intervals = mid.note_intervals()

    assert isinstance(intervals, NoteIntervalList)
    assert len(intervals) == 1
    interval = intervals[0]
    assert interval.channel == 2
    assert interval.note == 60
    assert interval.pitch == 60
    assert interval.velocity == 100
    assert interval.track == 0
    assert interval.start_tick == 0
    assert interval.end_tick == 200
    assert interval.start_seconds == pytest.approx(0.0)
    assert interval.end_seconds == pytest.approx(1.0)
    assert interval.release_tick == 200
    assert interval.release_seconds == pytest.approx(1.0)
    assert interval.end_reason == 'note_off'
    assert interval.status == 'closed'


def test_tempo_changes_are_applied_to_seconds():
    mid = make_file(1, [
        [
            Message('note_on', note=60, velocity=80, time=0),
            Message('note_off', note=60, time=300),
        ],
        [
            MetaMessage('set_tempo', tempo=250000, time=100),
        ],
    ], ticks_per_beat=100)

    interval = mid.note_intervals()[0]

    # 100 ticks at 500000 us/beat + 200 ticks at 250000 us/beat.
    assert interval.start_tick == 0
    assert interval.end_tick == 300
    assert interval.start_seconds == pytest.approx(0.0)
    assert interval.end_seconds == pytest.approx(1.0)


def test_note_on_with_velocity_zero_is_a_release():
    mid = make_file(0, [[
        Message('note_on', note=64, velocity=90, time=0),
        Message('note_on', note=64, velocity=0, time=150),
    ]])

    interval = mid.note_intervals()[0]

    assert interval.velocity == 90
    assert interval.release_velocity == 0
    assert interval.end_tick == 150
    assert interval.end_reason == 'note_off'


def test_repeated_notes_are_paired_in_note_on_order():
    mid = make_file(0, [[
        Message('note_on', note=60, velocity=10, time=0),
        Message('note_on', note=60, velocity=20, time=100),
        Message('note_off', note=60, time=50),
        Message('note_off', note=60, time=100),
    ]])

    first, second = mid.note_intervals()
    assert (first.velocity, first.start_tick, first.end_tick) == (10, 0, 150)
    assert (second.velocity, second.start_tick, second.end_tick) == (
        20, 100, 250)


def test_channels_are_shared_across_tracks():
    mid = make_file(1, [
        [
            Message('note_on', channel=5, note=60, velocity=80, time=0),
        ],
        [
            Message('note_off', channel=5, note=60, time=200),
        ],
    ])

    interval = mid.note_intervals()[0]

    assert interval.channel == 5
    assert interval.track == 0
    assert interval.start_tick == 0
    assert interval.end_tick == 200


def test_sustain_pedal_extends_note_and_preserves_release_time():
    mid = make_file(1, [
        [
            Message('control_change', channel=0, control=64, value=127,
                    time=0),
            Message('control_change', channel=0, control=64, value=0,
                    time=300),
        ],
        [
            Message('note_on', channel=0, note=60, velocity=80, time=0),
            Message('note_off', channel=0, note=60, time=100),
        ],
    ])

    interval = mid.note_intervals()[0]

    assert interval.start_tick == 0
    assert interval.release_tick == 100
    assert interval.end_tick == 300
    assert interval.start_seconds == pytest.approx(0.0)
    assert interval.release_seconds == pytest.approx(0.5)
    assert interval.end_seconds == pytest.approx(1.5)
    assert interval.release_reason == 'note_off'
    assert interval.end_reason == 'sustain_pedal'


def test_sustain_pedal_release_ends_all_pending_notes_on_channel():
    mid = make_file(0, [[
        Message('note_on', channel=0, note=60, velocity=80, time=0),
        Message('control_change', channel=0, control=64, value=127,
                time=0),
        Message('note_off', channel=0, note=60, time=100),
        Message('note_on', channel=0, note=64, velocity=80, time=0),
        Message('note_off', channel=0, note=64, time=100),
        Message('note_on', channel=1, note=67, velocity=80, time=0),
        Message('note_off', channel=1, note=67, time=100),
        Message('control_change', channel=0, control=64, value=0,
                time=200),
    ]])

    by_note = {(interval.channel, interval.note): interval
               for interval in mid.note_intervals()}
    assert by_note[(0, 60)].end_tick == 500
    assert by_note[(0, 64)].end_tick == 500
    assert by_note[(1, 67)].end_tick == 300


def test_all_sound_off_ends_active_and_sustained_notes_immediately():
    mid = make_file(1, [
        [
            Message('control_change', channel=0, control=64, value=127,
                    time=0),
            Message('control_change', channel=0,
                    control=120, value=0, time=300),
        ],
        [
            Message('note_on', channel=0, note=60, velocity=80, time=0),
            Message('note_off', channel=0, note=60, time=100),
            Message('note_on', channel=0, note=64, velocity=80, time=100),
        ],
    ])

    intervals = mid.note_intervals()
    assert [interval.end_tick for interval in intervals] == [300, 300]
    assert all(interval.end_reason == 'all_sound_off'
               for interval in intervals)
    by_note = {interval.note: interval for interval in intervals}
    assert by_note[60].release_tick == 100
    assert by_note[64].release_tick == 300


def test_all_notes_off_without_pedal_ends_notes_immediately():
    mid = make_file(0, [[
        Message('note_on', channel=0, note=60, velocity=80, time=0),
        Message('note_on', channel=0, note=64, velocity=80, time=0),
        Message('control_change', channel=0, control=123, value=0,
                time=250),
    ]])

    intervals = mid.note_intervals()
    assert [interval.end_tick for interval in intervals] == [250, 250]
    assert all(interval.release_reason == 'all_notes_off'
               for interval in intervals)
    assert all(interval.end_reason == 'all_notes_off'
               for interval in intervals)


def test_all_notes_off_with_pedal_waits_for_pedal_release():
    mid = make_file(1, [
        [
            Message('control_change', channel=0, control=64, value=127,
                    time=0),
            Message('control_change', channel=0, control=123, value=0,
                    time=300),
            Message('control_change', channel=0, control=64, value=0,
                    time=500),
        ],
        [
            Message('note_on', channel=0, note=60, velocity=80, time=0),
        ],
    ])

    interval = mid.note_intervals()[0]
    assert interval.release_tick == 300
    assert interval.release_reason == 'all_notes_off'
    assert interval.end_tick == 800
    assert interval.end_reason == 'sustain_pedal'


def test_orphan_release_and_unfinished_note_are_marked():
    mid = make_file(0, [[
        Message('note_on', channel=1, note=62, velocity=70, time=0),
        Message('note_off', channel=1, note=64, velocity=33, time=100),
        Message('note_on', channel=1, note=65, velocity=71, time=100),
    ]])

    by_note = {interval.note: interval for interval in mid.note_intervals()}

    orphan = by_note[64]
    assert orphan.orphan is True
    assert orphan.status == 'orphan_note_off'
    assert orphan.start_tick is None
    assert orphan.start_seconds is None
    assert orphan.end_tick == 100
    assert orphan.velocity == 33

    unfinished = by_note[65]
    assert unfinished.incomplete is True
    assert unfinished.status == 'end_of_file'
    assert unfinished.release_tick is None
    assert unfinished.end_tick == 200
    assert unfinished.end_reason == 'end_of_file'

    assert mid.note_intervals().orphans == [orphan]
    assert {interval.note for interval in mid.note_intervals().incomplete} == {
        62, 65}


def test_repeated_notes_are_independent_of_sustain():
    mid = make_file(0, [[
        Message('control_change', channel=0, control=64, value=127,
                time=0),
        Message('note_on', channel=0, note=60, velocity=10, time=0),
        Message('note_off', channel=0, note=60, time=100),
        Message('note_on', channel=0, note=60, velocity=20, time=0),
        Message('note_off', channel=0, note=60, time=100),
        Message('control_change', channel=0, control=64, value=0,
                time=100),
    ]])

    first, second = mid.note_intervals()
    assert (first.velocity, first.release_tick, first.end_tick) == (
        10, 100, 300)
    assert (second.velocity, second.release_tick, second.end_tick) == (
        20, 200, 300)


def test_query_does_not_modify_the_file():
    tracks = [
        MidiTrack([
            Message('note_on', channel=0, note=60, velocity=80, time=0),
            Message('note_off', channel=0, note=60, time=100),
        ]),
        MidiTrack([
            MetaMessage('set_tempo', tempo=250000, time=50),
        ]),
    ]
    mid = MidiFile(type=1, ticks_per_beat=100, tracks=tracks)
    before = [[message.copy() for message in track] for track in mid.tracks]

    mid.note_intervals()

    assert mid.tracks == before


def test_type_two_file_is_rejected():
    mid = MidiFile(type=2, tracks=[MidiTrack()])
    with pytest.raises(ValueError):
        mid.note_intervals()


def test_non_positive_ticks_per_beat_is_rejected():
    mid = MidiFile(type=0, ticks_per_beat=0, tracks=[MidiTrack()])
    with pytest.raises(ValueError):
        mid.note_intervals()


def test_interval_is_dict_compatible():
    interval = NoteInterval(
        channel=0, note=60, velocity=80, track=0,
        start_tick=0, end_tick=100,
        start_seconds=0.0, end_seconds=0.5)

    assert interval == {
        'channel': 0,
        'note': 60,
        'velocity': 80,
        'track': 0,
        'start_tick': 0,
        'end_tick': 100,
        'start_seconds': 0.0,
        'end_seconds': 0.5,
    }
    assert interval['pitch'] == 60
    assert interval['start_time'] == 0.0
    assert interval.as_dict() == dict(interval)
