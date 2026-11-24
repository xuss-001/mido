# SPDX-FileCopyrightText: 2013 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

"""
MIDI Parser

There is no need to use this module directly. All you need is
available in the top level module.
"""
from collections import deque

from .messages import Message
from .tokenizer import Tokenizer


class Parser:
    """
    MIDI byte stream parser

    Parses a stream of MIDI bytes and produces messages.

    Data can be put into the parser in the form of
    integers, byte arrays or byte strings.

    By default the parser keeps its historical behaviour. Passing
    ``compatibility=True`` enables support for running status and lets
    realtime messages appear in the middle of channel and SysEx messages
    without discarding the incomplete message.

    ``sysex_max_size`` limits the number of data bytes in a SysEx message.
    A ``ValueError`` is raised when the limit is exceeded, and the parser
    remains usable for the following messages.
    """
    def __init__(self, data=None, compatibility=False, sysex_max_size=None,
                 **kwargs):
        # For historical reasons self.messages is public and must be a
        # deque(). (It is referenced directly inside ports.)
        self.messages = deque()
        self._tok = Tokenizer(
            compatibility=compatibility,
            sysex_max_size=sysex_max_size,
            **kwargs)
        if data:
            self.feed(data)

    def _decode(self):
        for midi_bytes in self._tok:
            self.messages.append(Message.from_bytes(midi_bytes))

    @property
    def compatibility(self):
        """True if compatibility mode is enabled."""
        return self._tok.compatibility

    @property
    def running_status(self):
        """True if running status is supported."""
        return self._tok.running_status

    @property
    def sysex_max_size(self):
        """Maximum SysEx data size, or None for unlimited."""
        return self._tok.sysex_max_size

    def feed(self, data):
        """Feed MIDI data to the parser.

        Accepts any object that produces a sequence of integers in
        range 0..255, such as:

            [0, 1, 2]
            (0, 1, 2)
            [for i in range(256)]
            (for i in range(256)]
            bytearray()
        """
        try:
            self._tok.feed(data)
        finally:
            # Keep messages completed before an error (for example an
            # oversized SysEx message) available to the caller.
            self._decode()

    def feed_byte(self, byte):
        """Feed one MIDI byte into the parser.

        The byte must be an integer in range 0..255.
        """
        try:
            self._tok.feed_byte(byte)
        finally:
            self._decode()

    def reset(self):
        """Reset the parser and discard pending messages.

        This clears incomplete messages, running status and any messages
        that have been parsed but not fetched yet. It is useful after
        reconnecting to a MIDI device.
        """
        self._tok.reset()
        self.messages.clear()

    def get_message(self):
        """Get the first parsed message.

        Returns None if there is no message yet. If you don't want to
        deal with None, you can use pending() to see how many messages
        you can get before you get None, or just iterate over the
        parser.
        """
        for msg in self:
            return msg
        else:
            return None

    def pending(self):
        """Return the number of pending messages."""
        return len(self.messages)

    __len__ = pending

    def __iter__(self):
        """Yield messages that have been parsed so far."""
        while len(self.messages) > 0:
            yield self.messages.popleft()


def parse_all(data, **kwargs):
    """Parse MIDI data and return a list of all messages found.

    This is typically used to parse a little bit of data with a few
    messages in it. It's best to use a Parser object for larger
    amounts of data. Also, tt's often easier to use parse() if you
    know there is only one message in the data.
    """
    return list(Parser(data, **kwargs))


def parse(data, **kwargs):
    """ Parse MIDI data and return the first message found.

    Data after the first message is ignored. Use parse_all()
    to parse more than one message.
    """
    return Parser(data, **kwargs).get_message()
