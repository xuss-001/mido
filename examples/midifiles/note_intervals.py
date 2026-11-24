# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""Print note press/release/sustain intervals from a MIDI file.

This example builds a small type 1 file in memory.  The sustain pedal is on
track 1 while the notes are on track 0 to show that channel state is shared
between tracks.
"""

from mido import Message, MetaMessage, MidiFile, MidiTrack


mid = MidiFile(type=1, ticks_per_beat=480)

# Notes and a tempo change.
notes = MidiTrack()
notes.append(Message('note_on', channel=0, note=60, velocity=90, time=0))
notes.append(Message('note_off', channel=0, note=60, time=480))
notes.append(Message('note_on', channel=0, note=60, velocity=70, time=0))
notes.append(Message('note_off', channel=0, note=60, time=480))
notes.append(MetaMessage('set_tempo', tempo=250000, time=0))
notes.append(Message('note_on', channel=0, note=64, velocity=80, time=480))
notes.append(Message('note_off', channel=0, note=64, time=480))
mid.tracks.append(notes)

# Sustain pedal for channel 0.  It is deliberately in another track.
pedal = MidiTrack()
pedal.append(Message('control_change', channel=0, control=64, value=127,
                     time=0))
pedal.append(Message('control_change', channel=0, control=64, value=0,
                     time=1440))
mid.tracks.append(pedal)

for interval in mid.note_intervals():
    print(
        'channel={channel} note={note} velocity={velocity} track={track} '
        'start={start_tick} ticks ({start_seconds:.3f}s) '
        'release={release_tick} ticks ({release_seconds:.3f}s) '
        'end={end_tick} ticks ({end_seconds:.3f}s) '
        'end_reason={end_reason}'.format(**interval.as_dict(
            include_metadata=True)))
