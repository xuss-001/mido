.. SPDX-FileCopyrightText: 2013 Ole Martin Bjorndalen <ombdalen@gmail.com>
..
.. SPDX-License-Identifier: CC-BY-4.0

Parsing MIDI Bytes
------------------

The MIDI protocol is a *binary protocol*. Each message is encoded as a *status*
byte followed by up to three *data* bytes. (Except :term:`SysEx` messages
which can have an arbitrary number of *data* bytes immediately followed by an
EOX status byte.)

.. versionadded:: 1.2 ``mido.Message.from_hex()``

.. note:: To parse a single message you can use the class methods
          ``mido.Message.from_bytes()`` and ``mido.Message.from_hex()``



Mido comes with a *parser* that turns MIDI bytes into messages. You can create
a *parser object* or call one of the *utility functions*::

    >>> mido.parse([0x92, 0x10, 0x20])
    Message('note_on', channel=2, note=16, velocity=32, time=0)

    >>> mido.parse_all([0x92, 0x10, 0x20, 0x82, 0x10, 0x20])
    [Message('note_on', channel=2, note=16, velocity=32, time=0),
     Message('note_off', channel=2, note=16, velocity=32, time=0)]

These functions are just shortcuts for the full ``Parser`` class. This
is the same parser as used inside input ports to parse incoming messages.
Here are a few examples of how it can be used::

    >>> p = mido.Parser()
    >>> p.feed([0x90, 0x10, 0x20])
    >>> p.pending()
    1
    >>> p.get_message()
    Message('note_on', channel=0, note=16, velocity=32, time=0)

    >>> p.feed_byte(0x90)
    >>> p.feed_byte(0x10)
    >>> p.feed_byte(0x20)
    >>> p.feed([0x80, 0x10, 0x20])
    >>> p.pending()
    2
    >>> p.get_message()
    Message('note_on', channel=0, note=16, velocity=32, time=0)
    >>> p.get_message()
    Message('note_off', channel=0, note=16, velocity=32, time=0)

``feed()`` accepts any iterable that generates integers in 0..255. The
parser will skip and stray status bytes or data bytes, so you can
safely feed it random data and see what comes out the other end.

``get_message()`` will return ``None`` if there are no messages ready
to be gotten.

You can also fetch parsed messages out of the parser by iterating over
it::

    >>> p.feed([0x92, 0x10, 0x20, 0x82, 0x10, 0x20])
    >>> for message in p:
    ...    print(message)
    note_on channel=2 note=16 velocity=32 time=0
    note_off channel=2 note=16 velocity=32 time=0

The messages are available in ``p.messages`` (a ``collections.deque``).


Compatibility mode
^^^^^^^^^^^^^^^^^^

Some older MIDI devices use running status: after sending a channel
message they omit the status byte of subsequent messages of the same
type and channel. These devices may also send realtime messages such as
``clock`` in the middle of a channel or SysEx message.

The parser keeps its historical behaviour by default. To enable support
for these streams, create the parser with ``compatibility=True``::

    >>> p = mido.Parser(compatibility=True)
    >>> p.feed([0x90, 0x40, 0x60, 0x41, 0x61])
    >>> list(p)
    [Message('note_on', channel=0, note=64, velocity=96, time=0),
     Message('note_on', channel=0, note=65, velocity=97, time=0)]

Realtime messages are delivered as soon as they occur, even when the
parser is waiting for the remaining bytes of another message. They do
not cancel the incomplete message or running status::

    >>> p = mido.Parser(compatibility=True)
    >>> p.feed([0x90, 0x40, 0xf8, 0x60])
    >>> list(p)
    [Message('clock', time=0),
     Message('note_on', channel=0, note=64, velocity=96, time=0)]

Running status only applies to channel messages. A system common
message, a new SysEx message, or the end of a SysEx message cancels it,
so following data bytes are not interpreted using a stale channel
status. The ``running_status=True`` keyword is available as an alias for
``compatibility=True``.

The result does not depend on how the stream is split between
``feed()`` and ``feed_byte()`` calls.

Resetting the parser
^^^^^^^^^^^^^^^^^^^^

``Parser.reset()`` clears incomplete messages, running status and
messages that have been parsed but not fetched yet. This is useful after
a device is disconnected and reconnected::

    >>> p = mido.Parser(compatibility=True)
    >>> p.feed([0x90, 0x40])
    >>> p.reset()
    >>> p.pending()
    0

The same method is available on ``Tokenizer``.

Limiting SysEx size
^^^^^^^^^^^^^^^^^^^

Use ``sysex_max_size`` to limit the amount of data a SysEx message may
contain. The limit counts the data bytes, excluding the ``F0`` start and
``F7`` end bytes. ``None`` means unlimited::

    >>> p = mido.Parser(compatibility=True, sysex_max_size=2)
    >>> p.feed([0xf0, 1, 2, 0xf7])
    >>> list(p)
    [Message('sysex', data=(1, 2), time=0)]

If the limit is exceeded, ``feed()`` raises ``ValueError`` and discards
the oversized SysEx message. Messages received before the error remain
available, and the parser can continue with the next valid message::

    >>> p.feed([0xf0, 1, 2, 3, 0xf7])
    Traceback (most recent call last):
        ...
    ValueError: sysex message too long (exceeds maximum size of 2 data bytes)
    >>> p.feed([0x90, 0x40, 0x60])
    >>> list(p)
    [Message('note_on', channel=0, note=64, velocity=96, time=0)]

The limit can be used in either parser mode. ``max_sysex_size`` is
accepted as an alias for ``sysex_max_size``. The ``parse()`` and
``parse_all()`` shortcut functions accept the same keyword arguments.
