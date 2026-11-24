# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""Note interval queries for standard MIDI files.

The query in this module follows the note and sustain-pedal messages in a
type 0 or type 1 MIDI file.  It deliberately does not model a synthesizer
envelope: a note ends at its note-off message, at the moment the sustain
pedal is released, or when an all-sound-off message arrives.
"""

from collections import defaultdict, deque

from .midifiles import DEFAULT_TEMPO
from .units import tick2second


SUSTAIN_CONTROLLER = 64
ALL_SOUND_OFF_CONTROLLER = 120
ALL_NOTES_OFF_CONTROLLER = 123


class NoteInterval(dict):
    """A note interval returned by :meth:`MidiFile.note_intervals`.

    The eight core values are also available as dictionary keys.  Metadata
    such as the release cause and completeness flags is available through
    attributes (and through item access as a convenience).

    ``start_tick``/``end_tick`` are absolute ticks from the beginning of the
    file.  ``start_seconds``/``end_seconds`` are absolute seconds from the
    beginning of the file, taking all ``set_tempo`` messages into account.
    ``release_tick``/``release_seconds`` record the actual key release.  For
    a pedalled note, the note may continue sounding after that release, so
    the release and end times can be different.
    """

    __slots__ = ('_end_reason', '_file_end_seconds', '_file_end_tick',
                 '_incomplete', '_orphan', '_release_reason',
                 '_release_seconds', '_release_tick', '_release_velocity',
                 '_sort_key')

    _CORE_KEYS = (
        'channel',
        'note',
        'velocity',
        'track',
        'start_tick',
        'end_tick',
        'start_seconds',
        'end_seconds',
    )

    # Extra names accepted by item access and equality comparisons.
    _ALIASES = {
        'pitch': 'note',
        'note_number': 'note',
        'midi_note': 'note',
        'chan': 'channel',
        'note_on_velocity': 'velocity',
        'velocity_on': 'velocity',
        'source_track': 'track',
        'track_index': 'track',
        'track_id': 'track',

        'start_time': 'start_seconds',
        'end_time': 'end_seconds',
        'release_time': 'release_seconds',
        'start_time_seconds': 'start_seconds',
        'end_time_seconds': 'end_seconds',
        'release_time_seconds': 'release_seconds',
        'start_time_ticks': 'start_tick',
        'end_time_ticks': 'end_tick',
        'release_time_ticks': 'release_tick',
        'start_sec': 'start_seconds',
        'end_sec': 'end_seconds',

        'tick_start': 'start_tick',
        'tick_end': 'end_tick',
        'start': 'start_tick',
        'end': 'end_tick',
        'note_start_tick': 'start_tick',
        'note_end_tick': 'end_tick',
        'stop_tick': 'end_tick',
        'stop_seconds': 'end_seconds',
        'stop_time': 'end_seconds',
        'sound_end_tick': 'end_tick',
        'sound_end_seconds': 'end_seconds',
        'sustain_end_tick': 'end_tick',
        'sustain_end_seconds': 'end_seconds',
        'pedal_end_tick': 'end_tick',
        'pedal_end_seconds': 'end_seconds',
        'note_off_tick': 'release_tick',
        'note_off_seconds': 'release_seconds',
        'key_release_tick': 'release_tick',
        'key_release_seconds': 'release_seconds',

        'missing_note_on': 'orphan',
        'is_orphan': 'orphan',
        'unmatched_note_off': 'orphan',
        'is_unmatched': 'orphan',
        'unfinished': 'incomplete',
        'open': 'incomplete',
        'unclosed': 'incomplete',
        'truncated': 'incomplete',
        'is_incomplete': 'incomplete',
        'open_ended': 'incomplete',
        'is_open': 'incomplete',

        'ended_by': 'end_reason',
        'released_by': 'release_reason',
    }

    def __init__(self, channel, note, velocity, track, start_tick, end_tick,
                 start_seconds, end_seconds, *,
                 release_tick=None, release_seconds=None,
                 release_velocity=None, release_reason=None,
                 end_reason='note_off', orphan=False, incomplete=False,
                 file_end_tick=None, file_end_seconds=None, sort_key=None):
        super().__init__(
            channel=channel,
            note=note,
            velocity=velocity,
            track=track,
            start_tick=start_tick,
            end_tick=end_tick,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        self._release_tick = release_tick
        self._release_seconds = release_seconds
        self._release_velocity = release_velocity
        self._release_reason = release_reason
        self._end_reason = end_reason
        self._orphan = bool(orphan)
        self._incomplete = bool(incomplete)
        self._file_end_tick = file_end_tick
        self._file_end_seconds = file_end_seconds
        self._sort_key = sort_key

    @property
    def release_tick(self):
        """Tick of the key release, or None if the key was never released."""
        return self._release_tick

    @property
    def release_seconds(self):
        """Seconds at the key release, or None if never released."""
        return self._release_seconds

    @property
    def release_velocity(self):
        """Velocity of the note-off message, if there was one."""
        return self._release_velocity

    @property
    def release_reason(self):
        """Cause of the key release (for example ``note_off``)."""
        return self._release_reason

    @property
    def end_reason(self):
        """Cause of the final sounding end of the interval."""
        return self._end_reason

    @property
    def orphan(self):
        """True for a note-off with no matching note-on."""
        return self._orphan

    @property
    def incomplete(self):
        """True if the note was still sounding at the end of the file."""
        return self._incomplete

    @property
    def complete(self):
        """True if the interval has both a note-on and an ordinary end."""
        return not self._orphan and not self._incomplete

    @property
    def file_end_tick(self):
        """End tick of the file, useful for incomplete intervals."""
        return self._file_end_tick

    @property
    def file_end_seconds(self):
        """End time of the file in seconds."""
        return self._file_end_seconds

    @property
    def status(self):
        if self._orphan:
            return 'orphan_note_off'
        elif self._incomplete:
            return 'end_of_file'
        else:
            return 'closed'

    @property
    def duration_ticks(self):
        if self['start_tick'] is None or self['end_tick'] is None:
            return None
        return self['end_tick'] - self['start_tick']

    @property
    def duration_seconds(self):
        if self['start_seconds'] is None or self['end_seconds'] is None:
            return None
        return self['end_seconds'] - self['start_seconds']

    @property
    def source_track(self):
        return self['track']

    @property
    def pitch(self):
        return self['note']

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        key = self._ALIASES.get(name, name)
        if key in self._CORE_KEYS:
            return self[key]
        if key == 'release_tick':
            return self._release_tick
        elif key == 'release_seconds':
            return self._release_seconds
        elif key == 'release_velocity':
            return self._release_velocity
        elif key == 'release_reason':
            return self._release_reason
        elif key == 'end_reason':
            return self._end_reason
        elif key == 'orphan':
            return self._orphan
        elif key == 'incomplete':
            return self._incomplete
        elif key == 'file_end_tick':
            return self._file_end_tick
        elif key == 'file_end_seconds':
            return self._file_end_seconds
        raise AttributeError(name)

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise KeyError(key)
        key = self._ALIASES.get(key, key)
        if key in self._CORE_KEYS:
            return dict.__getitem__(self, key)
        return getattr(self, key)

    def __contains__(self, key):
        try:
            self[key]
        except (AttributeError, KeyError):
            return False
        else:
            return True

    def get(self, key, default=None):
        try:
            return self[key]
        except (AttributeError, KeyError):
            return default

    def keys(self):
        return self._CORE_KEYS

    def items(self):
        return ((key, self[key]) for key in self._CORE_KEYS)

    def values(self):
        return (self[key] for key in self._CORE_KEYS)

    def as_dict(self, include_metadata=False):
        """Return a plain dictionary with the interval values."""
        result = dict(self)
        if include_metadata:
            result.update(
                release_tick=self._release_tick,
                release_seconds=self._release_seconds,
                release_velocity=self._release_velocity,
                release_reason=self._release_reason,
                end_reason=self._end_reason,
                orphan=self._orphan,
                incomplete=self._incomplete,
                file_end_tick=self._file_end_tick,
                file_end_seconds=self._file_end_seconds,
            )
        return result

    to_dict = as_dict
    _asdict = as_dict

    def __eq__(self, other):
        if isinstance(other, NoteInterval):
            return (self.as_dict(include_metadata=True)
                    == other.as_dict(include_metadata=True))

        if isinstance(other, dict):
            for key, value in other.items():
                try:
                    if self[key] != value:
                        return False
                except (AttributeError, KeyError):
                    return False
            return True

        if isinstance(other, (tuple, list)):
            values = tuple(self[key] for key in self._CORE_KEYS)
            return values == tuple(other)

        return NotImplemented

    __hash__ = None

    def __repr__(self):
        fields = ', '.join(
            f'{key}={self[key]!r}' for key in self._CORE_KEYS)
        return f'{self.__class__.__name__}({fields})'


class _OpenNote:
    __slots__ = ('channel', 'note', 'release_reason', 'release_seconds',
                 'release_sequence', 'release_tick', 'release_velocity',
                 'sequence', 'start_seconds', 'start_tick', 'track',
                 'velocity')

    def __init__(self, channel, note, velocity, track, start_tick,
                 start_seconds, sequence):
        self.channel = channel
        self.note = note
        self.velocity = velocity
        self.track = track
        self.start_tick = start_tick
        self.start_seconds = start_seconds
        self.sequence = sequence
        self.release_sequence = None
        self.release_tick = None
        self.release_seconds = None
        self.release_velocity = None
        self.release_reason = None


class NoteIntervalList(list):
    """List returned by :meth:`MidiFile.note_intervals`."""

    def __call__(self):
        # Allows ``mid.note_intervals()` and ``mid.note_intervals`` to be
        # used interchangeably.
        return self

    @property
    def orphans(self):
        """Intervals that are note-offs without a matching note-on."""
        return self.__class__(interval for interval in self
                              if interval.orphan)

    @property
    def incomplete(self):
        """Intervals that were still sounding at the end of the file."""
        return self.__class__(interval for interval in self
                              if interval.incomplete)

    @property
    def complete(self):
        """Intervals with both a note-on and a final sounding end."""
        return self.__class__(interval for interval in self
                              if not interval.orphan
                              and not interval.incomplete)

    def to_dicts(self, include_metadata=False):
        """Return the intervals as plain dictionaries."""
        return [interval.as_dict(include_metadata=include_metadata)
                for interval in self]


def _iter_events(midifile):
    """Yield all file messages in global playback order."""
    events = []

    for track_index, track in enumerate(midifile.tracks):
        tick = 0
        for message_index, message in enumerate(track):
            tick += message.time
            events.append(
                (tick, track_index, message_index, message))

        # A sentinel makes sure that the end of a track contributes to the
        # file timeline even when the track has no end_of_track message.
        events.append((tick, track_index, len(track), None))

    events.sort(key=lambda event: (event[0], event[1], event[2]))
    return events


def _timed_events(midifile):
    """Yield ``(tick, seconds, track, message)`` tuples."""
    tempo = DEFAULT_TEMPO
    last_tick = 0
    last_seconds = 0.0
    ticks_per_beat = midifile.ticks_per_beat

    for tick, track_index, _, message in _iter_events(midifile):
        if tick > last_tick:
            last_seconds += tick2second(
                tick - last_tick, ticks_per_beat, tempo)
            last_tick = tick

        yield tick, last_seconds, track_index, message

        if message is not None and message.type == 'set_tempo':
            tempo = message.tempo


def find_note_intervals(midifile):
    """Return note intervals found in *midifile*.

    Only type 0 and type 1 files with a positive ticks-per-beat resolution
    are supported.  The file tracks are never modified.
    """
    if midifile.type == 2:
        raise ValueError(
            "can't query note intervals in type 2 (asynchronous) file")

    if midifile.ticks_per_beat <= 0:
        raise ValueError(
            'note interval queries require a positive ticks_per_beat')

    active = defaultdict(deque)
    sustained = defaultdict(list)
    pedal_down = defaultdict(bool)
    intervals = []
    sequence = 0
    file_end_tick = 0
    file_end_seconds = 0.0

    def add_interval(note, end_tick, end_seconds, end_reason):
        interval = NoteInterval(
            channel=note.channel,
            note=note.note,
            velocity=note.velocity,
            track=note.track,
            start_tick=note.start_tick,
            end_tick=end_tick,
            start_seconds=note.start_seconds,
            end_seconds=end_seconds,
            release_tick=(
                None if note.release_sequence is None
                else getattr(note, 'release_tick', None)),
            release_seconds=(
                None if note.release_sequence is None
                else getattr(note, 'release_seconds', None)),
            release_velocity=getattr(note, 'release_velocity', None),
            release_reason=getattr(note, 'release_reason', None),
            end_reason=end_reason,
            file_end_tick=file_end_tick,
            file_end_seconds=file_end_seconds,
            sort_key=(note.start_tick, note.sequence),
        )
        intervals.append(interval)

    def release_note(note_channel, note_number, tick, seconds, message,
                     reason):
        key = (note_channel, note_number)
        if not active[key]:
            interval = NoteInterval(
                channel=note_channel,
                note=note_number,
                velocity=message.velocity,
                track=current_track,
                start_tick=None,
                end_tick=tick,
                start_seconds=None,
                end_seconds=seconds,
                release_tick=tick,
                release_seconds=seconds,
                release_velocity=message.velocity,
                release_reason=reason,
                end_reason='orphan_note_off',
                orphan=True,
                file_end_tick=file_end_tick,
                file_end_seconds=file_end_seconds,
                sort_key=(tick, current_sequence),
            )
            intervals.append(interval)
            return

        note = active[key].popleft()
        note.release_sequence = current_sequence
        note.release_tick = tick
        note.release_seconds = seconds
        note.release_velocity = message.velocity
        note.release_reason = reason

        if pedal_down[note_channel]:
            sustained[note_channel].append(note)
        else:
            add_interval(
                note, tick, seconds,
                end_reason=reason)

    for tick, seconds, current_track, message in _timed_events(midifile):
        file_end_tick = tick
        file_end_seconds = seconds
        if message is None:
            continue

        message_type = message.type

        if message_type == 'note_on' and message.velocity > 0:
            sequence += 1
            note = _OpenNote(
                channel=message.channel,
                note=message.note,
                velocity=message.velocity,
                track=current_track,
                start_tick=tick,
                start_seconds=seconds,
                sequence=sequence,
            )
            active[(message.channel, message.note)].append(note)

        elif message_type == 'note_off' or (
                message_type == 'note_on' and message.velocity == 0):
            current_sequence = sequence + 1
            sequence = current_sequence
            release_note(
                message.channel, message.note, tick, seconds, message,
                reason='note_off')

        elif message_type == 'control_change':
            control = message.control
            channel = message.channel
            if control == SUSTAIN_CONTROLLER:
                if message.value >= 64:
                    pedal_down[channel] = True
                else:
                    pedal_down[channel] = False
                    for note in sustained.pop(channel, []):
                        add_interval(
                            note, tick, seconds,
                            end_reason='sustain_pedal')
            elif control == ALL_SOUND_OFF_CONTROLLER:
                keys = [
                    key for key in active
                    if key[0] == channel and active[key]
                ]
                for key in keys:
                    while active[key]:
                        note = active[key].popleft()
                        if note.release_sequence is None:
                            note.release_sequence = sequence + 1
                            sequence = note.release_sequence
                            note.release_tick = tick
                            note.release_seconds = seconds
                            note.release_velocity = message.value
                            note.release_reason = 'all_sound_off'
                        add_interval(
                            note, tick, seconds,
                            end_reason='all_sound_off')
                for note in sustained.pop(channel, []):
                    add_interval(
                        note, tick, seconds,
                        end_reason='all_sound_off')
            elif control == ALL_NOTES_OFF_CONTROLLER:
                keys = [
                    key for key in active
                    if key[0] == channel and active[key]
                ]
                for key in keys:
                    while active[key]:
                        note = active[key].popleft()
                        note.release_sequence = sequence + 1
                        sequence = note.release_sequence
                        note.release_tick = tick
                        note.release_seconds = seconds
                        note.release_velocity = message.value
                        note.release_reason = 'all_notes_off'
                        if pedal_down[channel]:
                            sustained[channel].append(note)
                        else:
                            add_interval(
                                note, tick, seconds,
                                end_reason='all_notes_off')

    for notes in active.values():
        while notes:
            note = notes.popleft()
            add_interval(
                note, file_end_tick, file_end_seconds,
                end_reason='end_of_file')
            intervals[-1]._incomplete = True

    for notes in sustained.values():
        for note in notes:
            add_interval(
                note, file_end_tick, file_end_seconds,
                end_reason='end_of_file')
            intervals[-1]._incomplete = True

    for interval in intervals:
        interval._file_end_tick = file_end_tick
        interval._file_end_seconds = file_end_seconds

    intervals.sort(key=lambda interval: interval._sort_key)
    return NoteIntervalList(intervals)


def note_intervals(midifile):
    """Return the note intervals in *midifile*.

    This is a module-level convenience wrapper for
    :meth:`MidiFile.note_intervals`.
    """
    return find_note_intervals(midifile)


get_note_intervals = note_intervals
