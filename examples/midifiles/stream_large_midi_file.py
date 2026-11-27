#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""
Read selected events from a large MIDI file without loading every message.

Usage::

    python stream_large_midi_file.py song.mid [max_events_per_track]

The reader keeps chunk locations and, for merged traversal, one pending
message per track.  Iteration can be stopped and restarted; each call to
``iter_track()`` returns an independent iterator.
"""

import sys

import mido


def print_tracks(path, limit):
    with mido.open_midifile(path) as midi:
        print(f"format={midi.type}, ticks_per_beat={midi.ticks_per_beat}")

        for track_index, track in enumerate(midi.tracks):
            print(f"=== Track {track_index} (first {limit} messages)")
            for count, message in enumerate(track):
                if count >= limit:
                    break
                # Track messages retain their delta time in ticks.
                print(f"  +{message.time} ticks: {message!r}")

        if midi.type == 2:
            print("Type 2 files are asynchronous; skipping merged traversal.")
            return

        print("=== First merged playback events")
        for count, message in enumerate(midi.iter_merged()):
            if count >= limit:
                break
            # Merged messages have delta time in seconds after tempo changes.
            print(f"  +{message.time:.6f} s: {message!r}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: stream_large_midi_file.py FILE [LIMIT]")

    event_limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    print_tracks(sys.argv[1], event_limit)
