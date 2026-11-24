#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

"""
Combine two small MIDI files into one type 1 MIDI file.

The two input files use different ticks-per-beat resolutions and
different tempos. The second file is delayed by a fixed offset given
in seconds, so no manual tick conversion is needed.

The result is saved to combined.mid.
"""

from mido import (
    Message,
    MetaMessage,
    MidiFile,
    MidiTrack,
    combine_midifiles,
)


def make_file(filename, ticks_per_beat, tempo, channels):
    mid = MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    track = MidiTrack()
    track.append(MetaMessage('set_tempo', tempo=tempo, time=0))

    for channel in channels:
        track.append(Message(
            'program_change', channel=channel, program=12, time=0))
        track.append(Message(
            'note_on', channel=channel, note=60 + channel,
            velocity=80, time=ticks_per_beat))
        track.append(Message(
            'note_off', channel=channel, note=60 + channel,
            velocity=80, time=ticks_per_beat))

    mid.tracks.append(track)
    mid.save(filename)
    return mid


def main():
    # 480 ticks per beat at 120 BPM (500000 us per beat).
    first = make_file('first.mid', 480, 500000, channels=[0, 1])

    # A different resolution and tempo: 120 ticks per beat at 240 BPM.
    second = make_file('second.mid', 120, 250000, channels=[0])

    # Delay the second file by one and a half seconds.
    combined = combine_midifiles(
        [(first, 0.0), (second, 1.5)], ticks_per_beat=480)

    combined.save('combined.mid')

    print(f'wrote combined.mid with {len(combined.tracks)} tracks '
          f'and {combined.ticks_per_beat} ticks per beat')
    for index, track in enumerate(combined.tracks):
        print(f'track {index}: {track.name!r}')


if __name__ == '__main__':
    main()
