# SPDX-FileCopyrightText: 2026 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

import copy
import io

import pytest

from mido import (
    ChannelAllocationError,
    Message,
    MetaMessage,
    MidiFile,
    MidiTrack,
    combine_midifiles,
)


def make_file(channels=(0,), tpb=480, tempo=500000, note=60,
              type=1, extra_tempo=None):
    """Build a small file with one note per used channel."""
    mid = MidiFile(type=type, ticks_per_beat=tpb)
    track = MidiTrack()
    track.append(MetaMessage('set_tempo', tempo=tempo, time=0))
    if extra_tempo is not None:
        track.append(
            MetaMessage('set_tempo', tempo=extra_tempo, time=tpb))
    for channel in channels:
        track.append(Message(
            'note_on', channel=channel, note=note, velocity=80, time=tpb))
        track.append(Message(
            'note_off', channel=channel, note=note, velocity=80, time=tpb))
    mid.tracks.append(track)
    return mid


def channel_messages(mid):
    return [msg for track in mid.tracks for msg in track
            if hasattr(msg, 'channel')]


def absolute_ticks(mid):
    now = 0
    result = []
    for msg in mid.merged_track:
        now += msg.time
        result.append(now)
    return result


def merged_absolute(mid):
    now = 0
    result = []
    for msg in mid.merged_track:
        now += msg.time
        result.append((msg, now))
    return result


def save_and_reload(mid):
    buffer = io.BytesIO()
    mid.save(file=buffer)
    buffer.seek(0)
    return MidiFile(file=buffer)


def test_returns_type_one_with_conductor_and_one_track_per_input():
    first = make_file()
    second = make_file(channels=(1,), tpb=120)

    combined = combine_midifiles(
        [(first, 0), (second, 0.5)], ticks_per_beat=240)

    assert combined.type == 1
    assert combined.ticks_per_beat == 240
    assert len(combined.tracks) == 3
    assert combined.tracks[0][1] == MetaMessage(
        'set_tempo', tempo=500000, time=0)


def test_accepts_type_zero_files():
    type_zero = make_file(type=0)

    combined = combine_midifiles([(type_zero, 0)])

    assert combined.type == 1
    assert len(combined.tracks) == 2
    assert any(msg.type == 'note_on'
               for msg in channel_messages(combined))


def test_type_two_files_are_rejected():
    mid = MidiFile(type=2)
    mid.tracks.append(MidiTrack())

    with pytest.raises(ValueError, match='type 2'):
        combine_midifiles([(mid, 0)])


def test_resolution_and_tempo_changes_are_respected():
    # One beat at 120 BPM in a 480 tpb file lasts 0.5 s.
    slow = make_file(tpb=480, tempo=500000)
    # One beat at 240 BPM in a 120 tpb file lasts 0.25 s.
    fast = make_file(tpb=120, tempo=250000, channels=(1,))

    combined = combine_midifiles(
        [(slow, 0), (fast, 0.0)], ticks_per_beat=240)
    reloaded = save_and_reload(combined)

    slow_note_on = next(
        (msg, tick) for msg, tick in merged_absolute(reloaded)
        if msg.type == 'note_on' and msg.channel == 0)
    fast_note_on = next(
        (msg, tick) for msg, tick in merged_absolute(reloaded)
        if msg.type == 'note_on' and msg.channel == 1)

    assert slow_note_on[1] == 240
    assert fast_note_on[1] == 120


def test_tempo_change_inside_a_file_is_taken_into_account():
    # The first note starts after two beats at 120 BPM (1.0 s = 720
    # ticks). From then on the tempo is 240 BPM, so each later beat
    # lasts 0.25 s and the second note starts at 1.5 s = 1200 ticks.
    mid = make_file(
        channels=(0, 1), tpb=480, tempo=500000, extra_tempo=250000)

    combined = combine_midifiles([(mid, 0)], ticks_per_beat=480)
    note_ons = [
        tick for msg, tick in merged_absolute(combined)
        if msg.type == 'note_on']

    assert note_ons == [720, 1200]


def test_multiple_tempo_changes_with_offset():
    mid = MidiFile(type=1, ticks_per_beat=480)
    track = MidiTrack([
        MetaMessage('set_tempo', tempo=500000, time=0),
        Message('note_on', channel=0, note=60, velocity=80, time=480),
        MetaMessage('set_tempo', tempo=250000, time=0),
        Message('note_off', channel=0, note=60, velocity=80, time=480),
        MetaMessage('set_tempo', tempo=1_000_000, time=0),
        Message('note_on', channel=0, note=62, velocity=80, time=480),
        Message('note_off', channel=0, note=62, velocity=80, time=480),
    ])
    mid.tracks.append(track)

    # Playback times before the offset:
    # note_on 60 at 0.5 s, note_off at 0.75 s,
    # note_on 62 at 1.75 s, note_off at 2.75 s.
    combined = combine_midifiles([(mid, 0.25)], ticks_per_beat=480)
    events = {
        (msg.type, msg.note): tick
        for msg, tick in merged_absolute(combined)
        if msg.type in ('note_on', 'note_off')}

    assert events == {
        ('note_on', 60): 720,    # 0.75 s
        ('note_off', 60): 960,   # 1.00 s
        ('note_on', 62): 1920,   # 2.00 s
        ('note_off', 62): 2880,  # 3.00 s
    }


def test_offsets_shift_events_in_seconds():
    first = make_file()
    second = make_file(channels=(1,), tpb=120, tempo=250000)

    combined = combine_midifiles(
        [(first, 0), (second, 1.0)], ticks_per_beat=240)
    reloaded = save_and_reload(combined)

    shifted_note_on = next(
        tick for msg, tick in merged_absolute(reloaded)
        if msg.type == 'note_on' and msg.channel == 1)

    # 1.0 s offset plus the 0.25 s lead-in of the second file,
    # converted at 120 BPM with 240 ticks per beat.
    assert shifted_note_on == 600


def test_event_times_are_within_one_output_tick():
    mid = make_file(tpb=333, tempo=314159, channels=(2,))
    offset = 0.37
    tpb = 240

    combined = combine_midifiles([(mid, offset)], ticks_per_beat=tpb)

    original = {
        (msg.type, msg.note): when
        for msg, when in _events_with_seconds(mid)
        if msg.type in ('note_on', 'note_off')}
    produced = [
        (msg, tick) for msg, tick in merged_absolute(combined)
        if msg.type in ('note_on', 'note_off')]

    seconds_per_tick = 0.5 / tpb
    for msg, tick in produced:
        expected_tick = int(round(
            (original[(msg.type, msg.note)] + offset)
            / seconds_per_tick))
        assert abs(tick - expected_tick) <= 1


def _events_with_seconds(mid):
    from mido.midifiles.units import tick2second

    tempo = 500000
    now = 0.0
    for msg in mid.merged_track:
        now += tick2second(msg.time, mid.ticks_per_beat, tempo)
        yield msg, now
        if msg.type == 'set_tempo':
            tempo = msg.tempo


def test_simultaneous_events_keep_their_order():
    mid = MidiFile(type=0, ticks_per_beat=480)
    track = MidiTrack([
        MetaMessage('set_tempo', tempo=500000, time=0),
        Message('control_change', channel=0, control=7, value=100,
                time=480),
        Message('program_change', channel=0, program=12, time=0),
        Message('note_on', channel=0, note=60, velocity=80, time=0),
        Message('note_off', channel=0, note=60, velocity=80, time=480),
    ])
    mid.tracks.append(track)

    combined = combine_midifiles([(mid, 0.25)], ticks_per_beat=480)
    events = [msg for msg in combined.tracks[1]
              if hasattr(msg, 'channel')]

    assert [msg.type for msg in events[:3]] == [
        'control_change', 'program_change', 'note_on']
    assert [msg.time for msg in events[:3]] == [720, 0, 0]


def test_melodic_channels_are_independent_between_files():
    first = make_file(channels=(0, 1))
    second = make_file(channels=(0, 5, 7))

    combined = combine_midifiles([(first, 0), (second, 0)])

    first_channels = {
        msg.channel for msg in combined.tracks[1]
        if hasattr(msg, 'channel')}
    second_channels = {
        msg.channel for msg in combined.tracks[2]
        if hasattr(msg, 'channel')}

    assert first_channels == {0, 1}
    assert second_channels == {2, 3, 4}
    assert first_channels.isdisjoint(second_channels)


def test_drum_channel_stays_exclusive():
    drums = make_file(channels=(9,))
    melody = make_file(channels=(0,))

    combined = combine_midifiles([(drums, 0), (melody, 0)])

    drum_channels = {
        msg.channel for msg in combined.tracks[1]
        if hasattr(msg, 'channel')}
    melody_channels = {
        msg.channel for msg in combined.tracks[2]
        if hasattr(msg, 'channel')}

    assert drum_channels == {9}
    assert 9 not in melody_channels


def test_multiple_drum_files_raise():
    first = make_file(channels=(9,))
    second = make_file(channels=(9,))

    with pytest.raises(ChannelAllocationError, match='drum channel'):
        combine_midifiles([(first, 0), (second, 0)])


def test_running_out_of_melodic_channels_raises():
    inputs = []
    for index in range(8):
        # Two channels per file means the eighth file needs the
        # sixteenth melodic channel, but only fifteen exist.
        inputs.append((make_file(channels=(0, 1)), 0))

    with pytest.raises(ChannelAllocationError,
                       match='not enough melodic channels'):
        combine_midifiles(inputs)


def test_invalid_offsets_are_rejected():
    mid = make_file()

    for bad_offset in (-1, -0.1, float('inf'), float('nan')):
        with pytest.raises((ValueError, TypeError)):
            combine_midifiles([(mid, bad_offset)])

    with pytest.raises(TypeError):
        combine_midifiles([(mid, '1')])


def test_invalid_arguments_are_rejected():
    mid = make_file()

    with pytest.raises(ValueError, match='at least one'):
        combine_midifiles([])

    with pytest.raises(TypeError, match='tuple'):
        combine_midifiles([mid])

    with pytest.raises(TypeError, match='MidiFile'):
        combine_midifiles([('not a midi file', 0)])

    for bad_tpb in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            combine_midifiles([(mid, 0)], ticks_per_beat=bad_tpb)


def test_input_files_are_not_modified():
    mid = make_file(channels=(0, 9), tpb=120, tempo=250000)
    snapshot = copy.deepcopy(mid)

    combine_midifiles([(mid, 0.75)], ticks_per_beat=240)

    assert mid.tracks == snapshot.tracks
    assert mid.ticks_per_beat == snapshot.ticks_per_beat


def test_saved_file_round_trips_with_times_and_channels():
    first = make_file(channels=(0, 1), tpb=480, tempo=500000)
    second = make_file(
        channels=(0, 9), tpb=120, tempo=250000, note=72)

    combined = combine_midifiles(
        [(first, 0), (second, 0.5)], ticks_per_beat=240)
    reloaded = save_and_reload(combined)

    assert reloaded.type == 1
    assert reloaded.ticks_per_beat == 240
    assert len(reloaded.tracks) == len(combined.tracks)
    assert absolute_ticks(reloaded) == absolute_ticks(combined)

    reloaded_channels = {
        (msg.channel, msg.type, getattr(msg, 'note', None))
        for msg in channel_messages(reloaded)}
    combined_channels = {
        (msg.channel, msg.type, getattr(msg, 'note', None))
        for msg in channel_messages(combined)}
    assert reloaded_channels == combined_channels


def test_tempo_messages_from_inputs_are_dropped():
    mid = make_file(extra_tempo=250000)

    combined = combine_midifiles([(mid, 0)])

    tempo_messages = [
        msg for track in combined.tracks for msg in track
        if msg.type == 'set_tempo']
    assert tempo_messages == [
        MetaMessage('set_tempo', tempo=500000, time=0)]


def test_multi_track_file_and_sysex_are_combined_into_one_track():
    mid = MidiFile(type=1, ticks_per_beat=96)
    mid.tracks.append(MidiTrack([
        MetaMessage('track_name', name='Melody', time=0),
        Message('note_on', channel=0, note=60, time=96),
        Message('sysex', data=(1, 2, 3), time=0),
        Message('note_off', channel=0, note=60, time=96),
    ]))
    mid.tracks.append(MidiTrack([
        Message('control_change', channel=2, control=7, value=100,
                time=48),
    ]))

    combined = combine_midifiles([(mid, 0)], ticks_per_beat=192)

    assert len(combined.tracks) == 2
    output_track = combined.tracks[1]
    assert any(msg.type == 'sysex' for msg in output_track)
    assert {msg.channel for msg in output_track
            if hasattr(msg, 'channel')} == {0, 1}
    assert all(msg.time >= 0 for msg in output_track)


def test_file_without_channel_messages_is_accepted():
    meta_only = MidiFile(type=1, ticks_per_beat=480)
    meta_only.tracks.append(MidiTrack([
        MetaMessage('track_name', name='Markers', time=0),
        MetaMessage('marker', text='hello', time=480),
    ]))

    combined = combine_midifiles([(meta_only, 0.5)])

    assert combined.type == 1
    assert len(combined.tracks) == 2
    assert not channel_messages(combined)
    assert combined.tracks[1].name == 'Markers'
