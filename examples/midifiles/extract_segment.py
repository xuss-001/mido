#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""Extract a time range from a MIDI file.

Run with:

    ./extract_segment.py input.mid output.mid 12.5 30
"""

import argparse

import mido


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', help='source MIDI file')
    parser.add_argument('output', help='destination MIDI file')
    parser.add_argument('start', type=float, help='start time in seconds')
    parser.add_argument('end', type=float, help='end time in seconds')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()

    midifile = mido.MidiFile(args.input)
    segment = midifile.extract(args.start, args.end)
    segment.save(args.output)
