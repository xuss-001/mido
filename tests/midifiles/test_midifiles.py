# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

import io

from pytest import raises

from mido.messages import Message
from mido.midifiles.meta import (
    KeySignatureError,
    MetaMessage,
    UnknownMetaMessage,
)
from mido.midifiles.midifiles import (
    MidiFile,
    MidiFileError,
    MidiFileTruncatedError,
    MidiTrack,
)

HEADER_ONE_TRACK = """
4d 54 68 64  # MThd
00 00 00 06  # Chunk size
      00 01  # Type 1
      00 01  # 1 track
      00 78  # 120 ticks per beat
"""

HEADER_TWO_TRACKS = """
4d 54 68 64  # MThd
00 00 00 06  # Chunk size
      00 01  # Type 1
      00 02  # 2 tracks
      00 78  # 120 ticks per beat
"""


def parse_hexdump(hexdump):
    data = bytearray()
    for line in hexdump.splitlines():
        data += bytearray.fromhex(line.split('#')[0])
    return data


def read_file(hexdump, clip=False):
    return MidiFile(file=io.BytesIO(parse_hexdump(hexdump)), clip=clip)


def test_no_tracks():
    assert read_file("""
    4d 54 68 64  # MThd
    00 00 00 06  # Chunk size
    00 01  # Type 1
    00 00  # 0 tracks
    00 78  # 120 ticks per beat
    """).tracks == []


def test_single_message():
    assert read_file(HEADER_ONE_TRACK + """
    4d 54 72 6b  # MTrk
    00 00 00 04
    20 90 40 40  # note_on
    """).tracks[0] == [Message('note_on', note=64, velocity=64, time=32)]


def test_too_long_message():
    with raises(IOError):
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 04
        00 ff 03 ff ff 7f # extremely long track name message
        """)


def test_two_tracks():
    mid = read_file("""
    4d54 6864 0000 0006 0001 0002 0040        # Header
    4d54 726b 0000 0008 00 90 40 10  40 80 40 10   # Track 0
    4d54 726b 0000 0008 00 90 47 10  40 80 47 10   # Track 1
    """)
    assert len(mid.tracks) == 2
    # TODO: add some more tests here.


def test_empty_file():
    with raises(EOFError):
        read_file("")


def test_eof_in_track():
    with raises(EOFError):
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 01  # Chunk size
        # Oops, no data here.
        """)


def test_invalid_data_byte_no_clipping():
    # TODO: should this raise IOError?
    with raises(IOError):
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 04  # Chunk size
        00 90 ff 40  # note_on note=255 velocity=64
        """)


def test_invalid_data_byte_with_clipping_high():
    midi_file = read_file(HEADER_ONE_TRACK + """
                          4d 54 72 6b  # MTrk
                          00 00 00 04  # Chunk size
                          00 90 ff 40  # note_on note=255 velocity=64
                          """, clip=True)
    assert midi_file.tracks[0][0].note == 127


def test_meta_messages():
    # TODO: should this raise IOError?
    mid = read_file(HEADER_ONE_TRACK + """
    4d 54 72 6b  # MTrk
    00 00 00 0c  # Chunk size
    00 ff 03 04 54 65 73 74  # track_name name='Test'
    00 ff 2f 00  # end_of_track
    """)

    track = mid.tracks[0]

    assert track[0] == MetaMessage('track_name', name='Test')
    assert track[1] == MetaMessage('end_of_track')


def test_meta_message_bad_key_sig_throws_key_signature_error_sharps():
    with raises(KeySignatureError):
        read_file(HEADER_ONE_TRACK + """
            4d 54 72 6b  # MTrk
            00 00 00 09  # Chunk size
            00 ff 59 02 08 # Key Signature with 8 sharps
            00 ff 2f 00  # end_of_track
            """)


def test_meta_message_bad_key_sig_throws_key_signature_error_flats():
    with raises(KeySignatureError):
        read_file(HEADER_ONE_TRACK + """
            4d 54 72 6b  # MTrk
            00 00 00 09  # Chunk size
            00 ff 59 02 f8 # Key Signature with 8 flats
            00 ff 2f 00  # end_of_track
            """)


def test_meta_messages_with_length_0():
    """sequence_number and midi_port with no data bytes should be accepted.

    In rare cases these messages have length 0 and thus no data
    bytes. (See issues 42 and 93.) It is unclear why these messages
    are missing their data. It could be cased by a bug in the software
    that created the files.

    So far this has been fixed by adding a test to each of these two
    meta message classes. If the problem appears with other message
    types it may be worth finding a more general solution.
    """
    mid = read_file(HEADER_ONE_TRACK + """
    4d 54 72 6b  # MTrk
    00 00 00 17

    00 ff 00 00  # sequence_number with no data bytes (should be 2).
    00 ff 21 00  # midi_port with no data bytes (should be 1).

    00 ff 00 02 00 01  # sequence_number with correct number of data bytes (2).
    00 ff 21 01 01     # midi_port with correct number of data bytes (1).

    00 ff 2f 00
    """)

    assert mid.tracks[0] == [
        MetaMessage('sequence_number', number=0),
        MetaMessage('midi_port', port=0),

        MetaMessage('sequence_number', number=1),
        MetaMessage('midi_port', port=1),

        MetaMessage('end_of_track'),
    ]


def test_midifile_invalid_type_error_message():
    # https://github.com/mido/mido/issues/673
    # the error used to interpolate the builtin format function instead of
    # the value that was rejected
    with raises(ValueError) as e:
        MidiFile(type=3)
    assert 'invalid format 3 (must be 0, 1 or 2)' in str(e.value)


def test_midifile_repr():
    midifile = MidiFile(type=1, ticks_per_beat=123, tracks=[
        MidiTrack([
            Message('note_on', channel=1, note=2, time=3),
            Message('note_off', channel=1, note=2, time=3)]),
        MidiTrack([
            MetaMessage('sequence_number', number=5),
            Message('note_on', channel=2, note=6, time=9),
            Message('note_off', channel=2, note=6, time=9)]),
    ])
    midifile_eval = eval(repr(midifile))  # noqa: S307
    for track, track_eval in zip(midifile.tracks, midifile_eval.tracks):
        for m1, m2 in zip(track, track_eval):
            assert m1 == m2


def test_meta_message_length_does_not_read_next_track():
    with raises(MidiFileError) as exc:
        read_file(HEADER_TWO_TRACKS + """
        4d 54 72 6b  # MTrk
        00 00 00 04  # length ends right before the next track
        00 ff 01 0a  # text message declares 10 data bytes

        4d 54 72 6b  # MTrk
        00 00 00 04
        00 90 40 40
        """)

    message = str(exc.value)
    assert 'track 0' in message
    assert 'meta message data' in message
    assert 'position 4' in message


def test_sysex_length_does_not_read_next_track():
    with raises(MidiFileError) as exc:
        read_file(HEADER_TWO_TRACKS + """
        4d 54 72 6b  # MTrk
        00 00 00 03
        00 f0 01  # sysex length consumes a byte from the next track

        4d 54 72 6b  # MTrk
        00 00 00 04
        00 90 40 40
        """)

    message = str(exc.value)
    assert 'track 0' in message
    assert 'sysex message data' in message
    assert 'position 3' in message


def test_event_truncated_at_track_end():
    with raises(MidiFileError) as exc:
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 02  # only delta time and status byte
        00 90
        """)

    message = str(exc.value)
    assert 'track 0' in message
    assert 'MIDI message data' in message
    assert 'position 2' in message


def test_variable_int_too_long():
    with raises(MidiFileError) as exc:
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 07
        81 80 80 80 00  # five-byte delta time
        90 40
        """)

    message = str(exc.value)
    assert 'track 0' in message
    assert 'variable-length integer' in message
    assert 'maximum length of 4 bytes' in message
    assert 'position 0' in message


def test_truncated_track_reports_position():
    with raises(MidiFileTruncatedError) as exc:
        read_file(HEADER_ONE_TRACK + """
        4d 54 72 6b  # MTrk
        00 00 00 08  # declares eight bytes
        00 90 40      # but the file ends here
        """)

    assert isinstance(exc.value, EOFError)
    assert isinstance(exc.value, OSError)
    message = str(exc.value)
    assert 'track 0' in message
    assert 'position 3' in message
    assert 'unexpected end of file' in message


def test_partial_reads_with_running_status_and_unknown_meta():
    midi_file = MidiFile(type=1, ticks_per_beat=96)
    midi_file.tracks.append(MidiTrack([
        Message('note_on', note=60, velocity=64, time=0),
        Message('note_on', note=62, velocity=65, time=32),
        UnknownMetaMessage(0x60, data=(1, 2, 3), time=0),
        MetaMessage('end_of_track'),
    ]))
    midi_file.tracks.append(MidiTrack([
        Message('note_off', note=60, velocity=0, time=64),
    ]))

    buffer = io.BytesIO()
    midi_file.save(file=buffer)

    class PartialReadFile(io.BytesIO):
        def read(self, size=-1):
            if size is None or size < 0:
                size = 1
            return super().read(min(size, 1))

    parsed = MidiFile(file=PartialReadFile(buffer.getvalue()))

    assert parsed.type == 1
    assert parsed.ticks_per_beat == 96
    assert len(parsed.tracks) == 2
    assert parsed.tracks[0][0] == Message(
        'note_on', note=60, velocity=64, time=0)
    assert parsed.tracks[0][1] == Message(
        'note_on', note=62, velocity=65, time=32)
    assert isinstance(parsed.tracks[0][2], UnknownMetaMessage)
    assert parsed.tracks[0][2].type_byte == 0x60
    assert parsed.tracks[0][2].data == (1, 2, 3)
    assert parsed.tracks[1][0] == Message(
        'note_off', note=60, velocity=0, time=64)
