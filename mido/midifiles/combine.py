# SPDX-FileCopyrightText: 2026 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

"""Combine several MIDI files into one type 1 file.

Each input file is shifted by a non-negative offset given in seconds.
Tempo changes and different ticks-per-beat resolutions of the inputs are
handled by converting every event to absolute playback time and back to
the ticks of the output file. Melodic channels are reassigned so that
every input gets its own set of channels, while the drum channel
(channel 9 in the zero-based numbering used by Mido) stays exclusive.
"""

from math import isfinite

from .meta import MetaMessage
from .midifiles import DEFAULT_TEMPO, DEFAULT_TICKS_PER_BEAT, MidiFile
from .tracks import MidiTrack
from .units import tick2second

# General MIDI percussion lives on channel 10, which is channel 9 in
# Mido's zero-based channel numbering.
DRUM_CHANNEL = 9


class ChannelAllocationError(ValueError):
    """Raised when the 16 MIDI channels are not enough for the inputs."""


def _validate_offset(offset):
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        raise TypeError(
            f'offset must be a non-negative number of seconds, '
            f'got {offset!r}')
    if offset < 0 or not isfinite(offset):
        raise ValueError(
            f'offset must be a non-negative number of seconds, '
            f'got {offset!r}')


def _collect_channels(mid):
    """Return the set of channels used by the channel messages in mid."""
    channels = set()
    for track in mid.tracks:
        for msg in track:
            if not msg.is_meta and hasattr(msg, 'channel'):
                channels.add(msg.channel)
    return channels


def _allocate_channels(midifiles):
    """Build a channel mapping for every input file.

    The drum channel is only handed out once. Each file receives its
    own melodic channels picked from the remaining fifteen channels.
    """
    drum_owner = None
    free_melodic = [
        channel for channel in range(16) if channel != DRUM_CHANNEL]
    mappings = []

    for index, (mid, _offset) in enumerate(midifiles):
        used = _collect_channels(mid)
        mapping = {}

        if DRUM_CHANNEL in used:
            if drum_owner is not None:
                raise ChannelAllocationError(
                    'drum channel 10 can only be used by one input file, '
                    f'but input {drum_owner} and input {index} both use it')
            drum_owner = index
            mapping[DRUM_CHANNEL] = DRUM_CHANNEL

        for channel in sorted(used):
            if channel == DRUM_CHANNEL:
                continue
            if not free_melodic:
                raise ChannelAllocationError(
                    f'not enough melodic channels for input {index}: '
                    'all 15 non-drum channels are already in use')
            mapping[channel] = free_melodic.pop(0)

        mappings.append(mapping)

    return mappings


def _event_times_seconds(mid):
    """Yield (message, absolute_playback_time_seconds) pairs in order.

    Tempo changes inside the input file are taken into account. The
    relative order of messages that happen at the same time is kept.
    """
    tempo = DEFAULT_TEMPO
    now = 0.0
    for msg in mid.merged_track:
        now += tick2second(msg.time, mid.ticks_per_beat, tempo)
        yield msg, now
        if msg.type == 'set_tempo':
            tempo = msg.tempo


def _build_input_track(mid, offset, channel_mapping, ticks_per_beat):
    """Convert one input file into one track of the output file."""
    events = []

    for msg, when in _event_times_seconds(mid):
        # The output uses a single constant tempo, so seconds map to
        # ticks through a fixed scale. Rounding keeps the error below
        # one output tick and preserves simultaneity.
        tick = int(round(
            (when + offset) * ticks_per_beat
            / (DEFAULT_TEMPO / 1_000_000.0)))
        if msg.type == 'set_tempo':
            # Tempo is owned by the conductor track.
            continue
        if msg.type == 'end_of_track':
            continue

        if hasattr(msg, 'channel'):
            msg = msg.copy(
                channel=channel_mapping[msg.channel], time=tick)
        else:
            msg = msg.copy(time=tick)
        events.append(msg)

    # Python's sort is stable, so simultaneous messages keep the order
    # they had in the input file.
    events.sort(key=lambda event: event.time)

    track = MidiTrack()
    previous = 0
    for msg in events:
        track.append(msg.copy(time=msg.time - previous))
        previous = msg.time

    return track


def combine_midifiles(midifiles, ticks_per_beat=DEFAULT_TICKS_PER_BEAT):
    """Combine several MIDI files into a single type 1 MIDI file.

    ``midifiles`` is a sequence of ``(MidiFile, offset)`` tuples where
    ``offset`` is a non-negative number of seconds by which the file is
    delayed in the combined output. Both type 0 and type 1 files are
    accepted; type 2 files can not be combined because their tracks are
    not synchronized.

    The returned :class:`MidiFile` is a type 1 file with one track per
    input file (plus a conductor track that sets a constant 120 BPM
    tempo). Each input file is assigned its own melodic channels, and
    the General MIDI drum channel (channel 10) is kept exclusive to a
    single input. :class:`ChannelAllocationError` is raised when the
    channels are insufficient.

    Event times are preserved within one output tick of their original
    playback time. The input files are never modified.
    """
    entries = list(midifiles)
    if not entries:
        raise ValueError('at least one MIDI file is required')

    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise TypeError(
                'each entry must be a (MidiFile, offset) tuple, '
                f'got {entry!r}')
        mid, offset = entry
        if not isinstance(mid, MidiFile):
            raise TypeError(
                f'first element must be a MidiFile, got {type(mid)!r}')
        if mid.type == 2:
            raise ValueError(
                'type 2 (asynchronous) MIDI files cannot be combined')
        _validate_offset(offset)

    if isinstance(ticks_per_beat, bool) \
            or not isinstance(ticks_per_beat, int) \
            or ticks_per_beat <= 0:
        raise ValueError(
            'ticks_per_beat must be a positive integer, '
            f'got {ticks_per_beat!r}')

    channel_mappings = _allocate_channels(entries)

    conductor = MidiTrack([
        MetaMessage('track_name', name='Conductor', time=0),
        MetaMessage('set_tempo', tempo=DEFAULT_TEMPO, time=0),
    ])

    output = MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    output.tracks.append(conductor)

    for (mid, offset), channel_mapping in zip(
            entries, channel_mappings):
        output.tracks.append(
            _build_input_track(
                mid, offset, channel_mapping, ticks_per_beat))

    return output
