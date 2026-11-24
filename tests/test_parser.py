# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

import random

from pytest import raises

from mido.messages import Message, specs
from mido.parser import Parser, parse, parse_all


def test_parse():
    """Parse a note_on msg and compare it to one created with Message()."""
    parsed = parse(b'\x90\x4c\x20')
    other = Message('note_on', channel=0, note=0x4c, velocity=0x20)
    assert parsed == other


def test_parse_stray_data():
    """The parser should ignore stray data bytes."""
    assert parse_all(b'\x20\x30') == []


def test_parse_stray_status_bytes():
    """The parser should ignore stray status bytes."""
    assert parse_all(b'\x90\x90\xf0') == []


def test_encode_and_parse():
    """Encode a message and then parse it.

    Should return the same message.
    """
    note_on = Message('note_on')
    assert note_on == parse(note_on.bytes())


def test_feed_byte():
    """Put various things into feed_byte()."""
    parser = Parser()

    parser.feed_byte(0)
    parser.feed_byte(255)

    with raises(TypeError):
        parser.feed_byte([1, 2, 3])

    with raises(ValueError):
        parser.feed_byte(-1)

    with raises(ValueError):
        parser.feed_byte(256)


def test_feed():
    """Put various things into feed()."""
    parser = Parser()

    parser.feed([])
    parser.feed([1, 2, 3])
    # TODO: add more valid types.

    with raises(TypeError):
        parser.feed(1)

    with raises(TypeError):
        parser.feed(None)

    with raises(TypeError):
        parser.feed()


def test_parse_random_bytes():
    """Parser should not crash when parsing random data."""
    randrange = random.Random('a_random_seed').randrange
    parser = Parser()
    for _ in range(10000):
        byte = randrange(256)
        parser.feed_byte(byte)


def test_parse_channel():
    """Parser should not discard the channel in channel messages."""
    assert parse([0x90, 0x00, 0x00]).channel == 0
    assert parse([0x92, 0x00, 0x00]).channel == 2


def test_one_byte_message():
    """Messages that are one byte long should not wait for data bytes."""
    messages = parse_all([0xf6])  # Tune request.
    assert len(messages) == 1
    assert messages[0].type == 'tune_request'


def test_undefined_messages():
    """The parser should ignore undefined status bytes and sysex_end."""
    messages = parse_all([0xf4, 0xf5, 0xf7, 0xf9, 0xfd])
    assert messages == []


def test_realtime_inside_sysex():
    """Realtime message inside sysex should be delivered first."""
    messages = parse_all([0xf0, 0, 0xfb, 0, 0xf7])
    assert len(messages) == 2
    assert messages[0].type == 'continue'
    assert messages[1].type == 'sysex'


def test_undefined_realtime_inside_sysex():
    """Undefined realtime message inside sysex should ignored."""
    messages = parse_all([0xf0, 0, 0xf9, 0xfd, 0, 0xf7])
    assert len(messages) == 1
    assert messages[0].type == 'sysex'


def test_encode_and_parse_all():
    """Encode and then parse all message types.

    This checks mostly for errors in the parser.
    """

    parser = Parser()
    for type_ in sorted(specs.SPEC_BY_TYPE.keys()):
        msg = Message(type_)
        parser.feed(msg.bytes())
        assert parser.get_message() == msg

    assert parser.get_message() is None


def test_parser_ascii_text():
    assert parse_all(b'7 bit ASCII should not produce any messages') == []


def _feed_in_chunks(data, sizes, **kwargs):
    parser = Parser(**kwargs)
    position = 0
    size_index = 0
    while position < len(data):
        size = sizes[size_index % len(sizes)]
        parser.feed(data[position:position + size])
        position += size
        size_index += 1
    return list(parser)


def test_compatibility_is_opt_in():
    """Default mode must keep its historical running status behaviour."""
    assert parse_all([0x90, 60, 64, 62, 65]) == [
        Message('note_on', note=60, velocity=64)]

    assert parse_all([0x90, 60, 64, 62, 65], compatibility=True) == [
        Message('note_on', note=60, velocity=64),
        Message('note_on', note=62, velocity=65)]


def test_compatibility_running_status_chunking():
    """All chunkings of the same stream must produce the same messages."""
    data = [
        0x90, 60, 64, 62, 65,
        0xf8,
        64, 66,
        0xb0, 7, 127,
        10, 64,
        0xf1, 0x12,
        0x90, 67, 68,
        0xf0, 1, 2, 0xf8, 3, 0xf7,
        0x80, 69, 70,
        71, 72,
    ]
    expected = [
        Message('note_on', note=60, velocity=64),
        Message('note_on', note=62, velocity=65),
        Message('clock'),
        Message('note_on', note=64, velocity=66),
        Message('control_change', control=7, value=127),
        Message('control_change', control=10, value=64),
        Message('quarter_frame', frame_type=1, frame_value=2),
        Message('note_on', note=67, velocity=68),
        Message('clock'),
        Message('sysex', data=(1, 2, 3)),
        Message('note_off', note=69, velocity=70),
        Message('note_off', note=71, velocity=72),
    ]

    assert parse_all(data, compatibility=True) == expected
    for size in range(1, len(data) + 1):
        assert _feed_in_chunks(
            data, [size], compatibility=True) == expected
    assert _feed_in_chunks(data, [2, 5, 1, 3], compatibility=True) == expected


def test_compatibility_realtime_inside_incomplete_messages():
    """Realtime messages are emitted immediately without losing state."""
    messages = parse_all([0x90, 60, 0xf8, 64], compatibility=True)
    assert messages == [Message('clock'),
                        Message('note_on', note=60, velocity=64)]

    messages = parse_all([0xf0, 1, 0xf8, 2, 0xf7], compatibility=True)
    assert messages == [Message('clock'),
                        Message('sysex', data=(1, 2))]

    messages = parse_all([0xf1, 0xf8, 0x12], compatibility=True)
    assert messages == [Message('clock'),
                        Message('quarter_frame', frame_type=1, frame_value=2)]


def test_compatibility_realtime_is_ready_immediately():
    """A realtime message is available before the other message ends."""
    parser = Parser(compatibility=True)
    parser.feed([0x90, 60])
    assert parser.pending() == 0

    parser.feed([0xf8])
    assert parser.get_message() == Message('clock')

    parser.feed([64])
    assert parser.get_message() == Message('note_on', note=60, velocity=64)

    parser.feed([0xf0, 1])
    parser.feed([0xfa])
    assert parser.get_message() == Message('start')

    parser.feed([2, 0xf7])
    assert parser.get_message() == Message('sysex', data=(1, 2))


def test_compatibility_running_status_boundaries():
    """System messages must not continue an earlier channel status."""
    assert parse_all(
        [0x90, 60, 64, 0xf6, 62, 65], compatibility=True) == [
            Message('note_on', note=60, velocity=64),
            Message('tune_request')]

    assert parse_all(
        [0x90, 60, 64, 0xf1, 0, 62, 65], compatibility=True) == [
            Message('note_on', note=60, velocity=64),
            Message('quarter_frame')]

    assert parse_all(
        [0x90, 60, 64, 0xf0, 1, 0xf7, 62, 65], compatibility=True) == [
            Message('note_on', note=60, velocity=64),
            Message('sysex', data=(1,))]

    assert parse_all(
        [0x90, 60, 64, 0xf4, 62, 65], compatibility=True) == [
            Message('note_on', note=60, velocity=64)]

    assert parse_all(
        [0x90, 60, 64, 0x80, 62, 65, 64, 66], compatibility=True) == [
            Message('note_on', note=60, velocity=64),
            Message('note_off', note=62, velocity=65),
            Message('note_off', note=64, velocity=66)]


def test_compatibility_reset():
    """reset() discards partial data, running status and pending messages."""
    parser = Parser(compatibility=True)
    parser.feed([0x90, 60, 64, 62])
    assert parser.pending() == 1
    parser.reset()
    assert parser.pending() == 0

    # The leftover running status and partial message must be gone.
    parser.feed([63, 64])
    assert parser.pending() == 0

    parser.feed([0x90, 65, 66])
    assert list(parser) == [Message('note_on', note=65, velocity=66)]


def test_compatibility_sysex_size_limit_and_recovery():
    """An oversized SysEx raises and does not poison later messages."""
    parser = Parser(compatibility=True, sysex_max_size=2)
    parser.feed([0xf0, 1, 2, 0xf7])
    assert list(parser) == [Message('sysex', data=(1, 2))]

    parser.feed([0x90, 60, 64])
    with raises(ValueError, match='sysex.*maximum size'):
        parser.feed([0xf0, 1, 2, 3, 4, 0xf7])

    # Messages completed before the oversized SysEx are still available.
    assert list(parser) == [Message('note_on', note=60, velocity=64)]

    parser.feed([0x90, 65, 66])
    assert list(parser) == [Message('note_on', note=65, velocity=66)]


def test_compatibility_sysex_limit_ignores_realtime():
    """Realtime bytes inside SysEx do not count towards the size limit."""
    messages = parse_all(
        [0xf0, 1, 0xf8, 2, 0xf7],
        compatibility=True, sysex_max_size=2)
    assert messages == [Message('clock'),
                        Message('sysex', data=(1, 2))]


def test_compatibility_aliases():
    """The running status and max size aliases enable the same behaviour."""
    assert parse_all([0x90, 60, 64, 62, 65], running_status=True) == [
        Message('note_on', note=60, velocity=64),
        Message('note_on', note=62, velocity=65)]

    with raises(ValueError, match='sysex.*maximum size'):
        parse_all([0xf0, 1, 2, 3, 0xf7], max_sysex_size=2)
