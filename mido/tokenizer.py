# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

from collections import deque
from numbers import Integral

from .messages.specs import SPEC_BY_STATUS, SYSEX_END, SYSEX_START


class Tokenizer:
    """
    Splits a MIDI byte stream into messages.
    """
    def __init__(self, data=None, compatibility=False, sysex_max_size=None,
                 **kwargs):
        """Create a new decoder.

        By default the tokenizer keeps its historical behaviour. If
        *compatibility* is true, running status is supported and realtime
        messages are allowed inside incomplete channel and SysEx messages
        without discarding the message being received.

        *sysex_max_size* sets the maximum number of data bytes in a SysEx
        message. ``None`` means unlimited. The limit applies in both modes.

        *running_status* and *max_sysex_size* are accepted as aliases for
        *compatibility* and *sysex_max_size*.
        """
        compatibility = kwargs.pop('compatible', compatibility)
        compatibility = kwargs.pop('compatibility_mode', compatibility)
        compatibility = kwargs.pop('running_status', compatibility)
        compatibility = kwargs.pop('allow_running_status', compatibility)
        if 'strict' in kwargs:
            compatibility = not kwargs.pop('strict')
        sysex_max_size = kwargs.pop('max_sysex_size', sysex_max_size)
        sysex_max_size = kwargs.pop('sysex_max_length', sysex_max_size)
        sysex_max_size = kwargs.pop('max_sysex_length', sysex_max_size)
        sysex_max_size = kwargs.pop('sysex_limit', sysex_max_size)
        sysex_max_size = kwargs.pop('sysex_size_limit', sysex_max_size)
        if kwargs:
            name = next(iter(kwargs))
            raise TypeError(
                f"unexpected keyword argument {name!r}")

        if sysex_max_size is not None:
            if not isinstance(sysex_max_size, Integral):
                raise TypeError('sysex_max_size must be an integer')
            if sysex_max_size < 0:
                raise ValueError('sysex_max_size must be non-negative')

        self.compatibility = bool(compatibility)
        self.running_status = self.compatibility
        self.sysex_max_size = sysex_max_size

        self._status = 0
        self._bytes = []
        self._messages = deque()
        self._datalen = 0
        self._running_status = 0

        if data is not None:
            self.feed(data)

    def _feed_status_byte(self, status):
        if self.compatibility:
            return self._feed_status_byte_compat(status)

        if status == SYSEX_END:
            if self._status == SYSEX_START:
                self._bytes.append(SYSEX_END)
                self._messages.append(self._bytes)

            self._status = 0

        elif 0xf8 <= status <= 0xff:
            if self._status != SYSEX_START:
                # Realtime messages are only allowed inside sysex
                # messages. Reset parser.
                self._status = 0

            if status in SPEC_BY_STATUS:
                self._messages.append([status])

        elif status in SPEC_BY_STATUS:
            # New message.
            spec = SPEC_BY_STATUS[status]

            if spec.length == 1:
                self._messages.append([status])
                self._status = 0
            else:
                self._status = status
                self._bytes = [status]
                self._len = spec.length
        else:
            # Undefined message. Reset parser.
            # (Undefined realtime messages are handled above.)
            # self._status = 0
            pass

    def _feed_data_byte(self, byte):
        if self.compatibility:
            return self._feed_data_byte_compat(byte)

        if self._status:
            if self._status == SYSEX_START:
                self._check_sysex_size()
            self._bytes.append(byte)
            if len(self._bytes) == self._len:
                # Complete message.
                self._messages.append(self._bytes)
                self._status = 0
        else:
            # Ignore stray data byte.
            pass

    def _feed_status_byte_compat(self, status):
        if status == SYSEX_END:
            if self._status == SYSEX_START:
                self._bytes.append(SYSEX_END)
                self._messages.append(self._bytes)

            # EOX terminates SysEx and cancels running status, even when
            # there is no SysEx in progress.
            self._reset_state()

        elif 0xf8 <= status <= 0xff:
            # Realtime messages may appear anywhere in the stream,
            # including in the middle of other messages. They do not
            # change the current message or running status.
            if status in SPEC_BY_STATUS:
                self._messages.append([status])

        elif status in SPEC_BY_STATUS:
            spec = SPEC_BY_STATUS[status]

            # Any system common message or new SysEx cancels running
            # status. Channel statuses replace the running status.
            if status < 0xf0:
                self._running_status = status
            else:
                self._running_status = 0

            if spec.length == 1:
                self._messages.append([status])
                self._reset_state()
            else:
                self._status = status
                self._bytes = [status]
                self._len = spec.length

        else:
            # Undefined system common statuses (0xf4 and 0xf5) abort
            # any message in progress and cancel running status.
            self._reset_state()

    def _feed_data_byte_compat(self, byte):
        if not self._status and self._running_status:
            self._status = self._running_status
            self._bytes = [self._status]
            self._len = SPEC_BY_STATUS[self._status].length

        if not self._status:
            # Ignore stray data byte.
            return

        if self._status == SYSEX_START:
            self._check_sysex_size()

        self._bytes.append(byte)
        if len(self._bytes) == self._len:
            # Complete message. Keep running status so that the next
            # data byte starts another message with the same status.
            self._messages.append(self._bytes)
            self._status = 0
            self._bytes = []
            self._len = 0

    def _check_sysex_size(self):
        if (self.sysex_max_size is not None and
                len(self._bytes) - 1 >= self.sysex_max_size):
            # Drop the oversized SysEx so that parsing can continue with
            # the next valid message.
            limit = self.sysex_max_size
            self._reset_state()
            raise ValueError(
                f'sysex message too long (exceeds maximum size of '
                f'{limit} data bytes)')

    def _reset_state(self):
        self._status = 0
        self._bytes = []
        self._datalen = 0
        self._len = 0
        self._running_status = 0

    def reset(self):
        """Reset the tokenizer and discard all pending messages."""
        self._reset_state()
        self._messages.clear()

    def feed_byte(self, byte):
        """Feed MIDI byte to the decoder.

        Takes an int in range [0..255].
        """
        if not isinstance(byte, Integral):
            raise TypeError('message byte must be integer')

        if 0 <= byte <= 255:
            if byte <= 127:
                return self._feed_data_byte(byte)
            else:
                return self._feed_status_byte(byte)
        else:
            raise ValueError(f'invalid byte value {byte!r}')

    def feed(self, data):
        """Feed MIDI bytes to the decoder.

        Takes an iterable of ints in in range [0..255].
        """
        for byte in data:
            self.feed_byte(byte)

    def __len__(self):
        return len(self._messages)

    def __iter__(self):
        """Yield messages that have been parsed so far."""
        while len(self._messages):
            yield self._messages.popleft()
