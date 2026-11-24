#!/usr/bin/env python3

"""Create, play back and reload a SMPTE frame-timed MIDI file."""

import io

from mido import Message, MetaMessage, MidiFile


frame_rate = 29.97
ticks_per_frame = 100
ticks_per_second = int(round(frame_rate * ticks_per_frame))

mid = MidiFile(type=1, frame_rate=frame_rate,
               ticks_per_frame=ticks_per_frame)
track = mid.add_track(name='video cues')

# Tempo messages are kept in the file, but do not change frame timing.
track.append(MetaMessage('set_tempo', tempo=250000, time=0))
track.append(Message('note_on', note=60, velocity=100, time=0))
track.append(Message('note_off', note=60, velocity=100,
                     time=ticks_per_second))

print(f'frame_rate={mid.frame_rate}, '
      f'ticks_per_frame={mid.ticks_per_frame}')
print(f'length={mid.length:.6f} seconds')

for msg in mid:
    print(f'{msg.time:.6f}s {msg}')

# Save and reload to show that the SMPTE division survives the round trip.
outfile = io.BytesIO()
mid.save(file=outfile)
outfile.seek(0)
reloaded = MidiFile(file=outfile)

print(f'reloaded is_smpte={reloaded.is_smpte}, '
      f'frame_rate={reloaded.frame_rate}, '
      f'ticks_per_frame={reloaded.ticks_per_frame}, '
      f'length={reloaded.length:.6f}')
