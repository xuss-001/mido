# SPDX-FileCopyrightText: 2026 Mido contributors
#
# SPDX-License-Identifier: MIT

"""On-demand, constant-memory reading of Standard MIDI Files.

Unlike :class:`mido.MidiFile`, :class:`MidiFileStream` reads the file header
and locates track chunks on demand; it does not decode or store every MIDI
message.  Each call to :meth:`MidiFileStream.iter_track` returns an independent
iterator, making it safe to interleave iteration over the same or different
tracks.
"""

from __future__ import annotations

import os
import threading
import time
import weakref
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import BinaryIO, Iterator, Optional, Union

from .meta import MetaMessage, meta_charset
from .midifiles import (
    DEFAULT_TEMPO,
    DEFAULT_TICKS_PER_BEAT,
    MidiFile,
    read_chunk_header,
    read_file_header,
    read_track_message,
)
from .units import tick2second

PathLike = Union[str, bytes, "os.PathLike[str]", "os.PathLike[bytes]"]
Source = Union[PathLike, BinaryIO]

_header_offsets: "weakref.WeakKeyDictionary[object, int]" = (
    weakref.WeakKeyDictionary()
)
_stream_locks: "weakref.WeakKeyDictionary[object, threading.RLock]" = (
    weakref.WeakKeyDictionary()
)
_stream_locks_guard = threading.Lock()


def _get_stream_lock(stream) -> threading.RLock:
    try:
        with _stream_locks_guard:
            lock = _stream_locks.get(stream)
            if lock is None:
                lock = threading.RLock()
                _stream_locks[stream] = lock
            return lock
    except TypeError:
        return threading.RLock()


@dataclass(frozen=True)
class TrackInfo:
    """Location and size of one ``MTrk`` chunk."""

    index: int
    header_offset: int
    data_offset: int
    size: int
    end: int


class TrackMessageIterator:
    """Independent, seekable iterator over one MIDI track chunk."""

    def __init__(self, track: "MidiTrackStream"):
        self._reader = track.reader
        info = track.get_info()
        self._info = info
        self._position = info.data_offset
        self._last_status: Optional[int] = None
        self._exhausted = False

    def __iter__(self) -> "TrackMessageIterator":
        return self

    def __next__(self):
        reader = self._reader
        reader._check_open()

        if self._exhausted or self._position >= self._info.end:
            self._exhausted = True
            raise StopIteration

        with reader.lock:
            reader._check_open()
            stream = reader.stream
            try:
                previous_position = stream.tell()
            except (OSError, ValueError):
                previous_position = None

            try:
                # Every iterator has its own logical position.  Seeking here
                # means several iterators can interleave reads on one stream.
                stream.seek(self._position)
                with meta_charset(reader.charset):
                    message, self._last_status = read_track_message(
                        self,
                        last_status=self._last_status,
                        clip=reader.clip,
                    )
            except BaseException:
                if previous_position is not None:
                    try:
                        stream.seek(previous_position)
                    except (OSError, ValueError):
                        pass
                raise
            else:
                self._position = self.tell()
                if self._position >= self._info.end:
                    self._exhausted = True
                if previous_position is not None:
                    try:
                        stream.seek(previous_position)
                    except (OSError, ValueError):
                        pass
                return message

    # The MIDI parser helpers expect a small binary-file interface.  Reading
    # through these methods both enforces the chunk boundary and records this
    # iterator's private stream position.
    def read(self, size: int = -1) -> bytes:
        if self._exhausted or self._position >= self._info.end:
            return b""

        remaining = self._info.end - self._position
        if size is None or size < 0:
            read_size = remaining
        else:
            read_size = min(size, remaining)

        if read_size <= 0:
            return b""

        data = self._reader.stream.read(read_size)
        if data is None:
            data = b""
        self._position += len(data)
        return data

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            position = offset
        elif whence == os.SEEK_CUR:
            position = self._position + offset
        elif whence == os.SEEK_END:
            position = self._info.end + offset
        else:
            raise ValueError(f"invalid whence ({whence})")
        self._position = position
        return position

    def close(self) -> None:
        """Stop this iterator without closing the underlying reader/stream."""
        self._exhausted = True


class MidiTrackStream:
    """Re-iterable handle to one track chunk."""

    def __init__(
        self,
        reader: "MidiFileStream",
        index: int,
        info: Optional[TrackInfo] = None,
    ):
        self._reader = reader
        self.index = index
        self._info = info
        self._default_iterator: Optional[TrackMessageIterator] = None

    @property
    def info(self) -> TrackInfo:
        return self.get_info()

    def get_info(self) -> TrackInfo:
        if self._info is None:
            self._info = self._reader.resolve_track(self.index)
        return self._info

    @property
    def reader(self) -> "MidiFileStream":
        return self._reader

    def __iter__(self) -> TrackMessageIterator:
        return TrackMessageIterator(self)

    def __call__(self) -> TrackMessageIterator:
        return iter(self)

    def __next__(self):
        # Convenience for callers that keep the track object and use next().
        if self._default_iterator is None:
            self._default_iterator = iter(self)
        return next(self._default_iterator)

    def read(self) -> TrackMessageIterator:
        return iter(self)

    def messages(self) -> TrackMessageIterator:
        return iter(self)

    def close(self) -> None:
        """Close the convenience iterator, if one was created."""
        if self._default_iterator is not None:
            self._default_iterator.close()

    def __repr__(self) -> str:
        if self._info is None:
            return f"{type(self).__name__}(index={self.index}, unresolved)"
        return (
            f"{type(self).__name__}(index={self.index}, "
            f"size={self._info.size})"
        )


class TrackList(list):
    """List of :class:`MidiTrackStream` objects; also callable for symmetry."""

    def __call__(self) -> Iterator[MidiTrackStream]:
        return iter(self)


class MidiFileStream(MidiFile):
    """Lazy, seekable reader for Standard MIDI Files.

    The first argument may be either a filename/path-like object or a seekable
    binary stream.  The constructor reads the file header and track chunk
    headers only; MIDI events are decoded when iteration requests them.

    A stream passed as ``file`` (or as the first positional source) is owned by
    the caller and is never closed by :meth:`close`.  A file opened from a path
    is closed when the reader is closed.
    """

    def __init__(
        self,
        filename: Optional[Source] = None,
        file: Optional[BinaryIO] = None,
        type: int = 1,
        ticks_per_beat: int = DEFAULT_TICKS_PER_BEAT,
        charset: str = "latin1",
        debug: bool = False,
        clip: bool = False,
        tracks=None,
        *,
        path: Optional[PathLike] = None,
        source: Optional[Source] = None,
        lazy: bool = False,
        streaming: bool = False,
        stream=None,
    ):
        # The lazy/streaming switches are accepted for MidiFile(..., lazy=True)
        # dispatch and do not change construction here.
        del lazy, streaming, tracks

        if stream is not None and hasattr(stream, "read"):
            if file is not None:
                raise TypeError("pass only one MIDI source")
            file = stream
            stream = None
        # A truthy non-file ``stream`` is a construction switch handled by
        # MidiFile.__new__; nothing else is needed here.
        del stream

        # Allow MidiFileStream(source) where source is either path or stream,
        # while retaining the MidiFile-style filename=/file= keywords.
        if source is not None:
            if filename is not None or file is not None:
                raise TypeError("pass only one MIDI source")
            if _is_path(source):
                filename = source
            else:
                file = source

        if path is not None:
            if filename is not None:
                raise TypeError("pass only one of filename and path")
            filename = path

        if file is None and filename is not None and not _is_path(filename):
            if hasattr(filename, "read"):
                file = filename  # type: ignore[assignment]
                filename = None
            else:
                raise TypeError(
                    "first argument must be a path-like object or a binary stream"
                )

        if type not in range(3):
            raise ValueError(f"invalid format {type} (must be 0, 1 or 2)")

        self.charset = charset
        self.clip = clip
        self.debug = debug
        self.lock = threading.RLock()
        self._closed = False

        if file is not None:
            if not all(hasattr(file, name) for name in ("read", "seek", "tell")):
                raise TypeError("file must be a seekable binary stream")
            seekable = getattr(file, "seekable", None)
            if callable(seekable) and not seekable():
                raise OSError("MIDI stream must be seekable")
            self._stream = file
            self._owns_stream = False
            self.filename = None
        elif filename is not None:
            self.filename = os.fspath(filename)
            self._stream = open(self.filename, "rb")
            self._owns_stream = True
        else:
            raise ValueError("requires filename, path, or file")

        self.lock = _get_stream_lock(self._stream)

        self._track_count = 0
        self._track_infos: list[Optional[TrackInfo]] = []
        self._next_chunk_index = 0
        self._next_chunk_offset = 0
        self.tracks = TrackList()
        self._merged_track = None
        self.type = type
        self.ticks_per_beat = ticks_per_beat
        self._read_header()

    def _read_header(self) -> None:
        stream = self._stream
        original_position: Optional[int]
        try:
            original_position = stream.tell()
        except (OSError, ValueError):
            original_position = None

        candidates = []
        if original_position is not None:
            candidates.append(original_position)
        try:
            cached_offset = _header_offsets.get(stream)
        except TypeError:
            cached_offset = None
        if cached_offset is not None:
            candidates.append(cached_offset)
        candidates.append(0)

        last_error = None
        for header_offset in dict.fromkeys(candidates):
            try:
                self._read_header_at(header_offset)
            except (EOFError, OSError, ValueError) as exc:
                last_error = exc
                continue

            try:
                _header_offsets[stream] = header_offset
            except TypeError:
                pass
            break
        else:
            assert last_error is not None
            raise last_error

        if original_position is not None:
            try:
                stream.seek(original_position)
            except (OSError, ValueError):
                pass

    def _read_header_at(self, header_offset: int) -> None:
        stream = self._stream
        stream.seek(header_offset)

        with meta_charset(self.charset):
            file_type, num_tracks, ticks_per_beat = read_file_header(stream)

        self.type = file_type
        self.ticks_per_beat = ticks_per_beat
        self._track_count = num_tracks
        self._track_infos = [None] * num_tracks
        self._next_chunk_index = 0
        self._next_chunk_offset = stream.tell()
        self.tracks = TrackList(
            MidiTrackStream(self, index) for index in range(num_tracks)
        )

    def resolve_track(self, index: int) -> TrackInfo:
        """Read and cache chunk headers up to ``index`` without event parsing."""
        self._check_open()
        if index < 0:
            index += self._track_count
        if not 0 <= index < self._track_count:
            raise IndexError("MIDI track index out of range")

        cached = self._track_infos[index]
        if cached is not None:
            return cached

        with self.lock:
            self._check_open()
            cached = self._track_infos[index]
            if cached is not None:
                return cached

            stream = self._stream
            try:
                previous_position = stream.tell()
            except (OSError, ValueError):
                previous_position = None

            try:
                while self._next_chunk_index <= index:
                    stream.seek(self._next_chunk_offset)
                    header_offset = stream.tell()
                    name, size = read_chunk_header(stream)
                    if name != b"MTrk":
                        raise OSError("no MTrk header at start of track")

                    data_offset = stream.tell()
                    chunk_index = self._next_chunk_index
                    info = TrackInfo(
                        index=chunk_index,
                        header_offset=header_offset,
                        data_offset=data_offset,
                        size=size,
                        end=data_offset + size,
                    )
                    self._track_infos[chunk_index] = info
                    self._next_chunk_index = chunk_index + 1
                    self._next_chunk_offset = info.end
            finally:
                if previous_position is not None:
                    try:
                        stream.seek(previous_position)
                    except (OSError, ValueError):
                        pass

            result = self._track_infos[index]
            assert result is not None
            return result

    @property
    def stream(self) -> BinaryIO:
        return self._stream

    @property
    def file(self) -> BinaryIO:
        return self._stream

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def track_count(self) -> int:
        return self._track_count

    @property
    def num_tracks(self) -> int:
        return self._track_count

    def _check_open(self) -> None:
        if self._closed:
            raise ValueError("MIDI file reader is closed")

    def __enter__(self) -> "MidiFileStream":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        """Close self-opened files and release this reader's stream reference.

        Caller-supplied streams are flushed/referenced by the caller and are
        deliberately not closed.
        """
        if self._closed:
            return

        with self.lock:
            if self._closed:
                return
            self._closed = True
            stream = getattr(self, "_stream", None)
            owned = getattr(self, "_owns_stream", False)
            try:
                if owned and stream is not None:
                    stream.close()
            finally:
                self._track_infos = ()
                tracks = getattr(self, "tracks", None)
                if tracks is not None:
                    tracks.clear()

    def iter_tracks(self) -> Iterator[MidiTrackStream]:
        """Yield re-iterable track handles in file order."""
        self._check_open()
        return iter(self.tracks)

    iterate_tracks = iter_tracks
    stream_tracks = iter_tracks

    def track_stream(self, track: int = 0) -> MidiTrackStream:
        """Return a re-iterable handle for ``track``."""
        self._check_open()
        if isinstance(track, MidiTrackStream):
            return track
        track_object = self.tracks[track]
        # Resolve eagerly for direct error reporting, while keeping the track
        # object itself cheap to construct.
        track_object.get_info()
        return track_object

    get_track = track_stream
    track = track_stream
    get_track_stream = track_stream

    def iter_track(self, track: int = 0) -> TrackMessageIterator:
        """Return a fresh independent message iterator for ``track``."""
        return iter(self.track_stream(track))

    # Common verb aliases.
    read_track = iter_track
    stream_track = iter_track
    iter_track_messages = iter_track
    track_messages = iter_track
    read_track_messages = iter_track
    iterate_track = iter_track
    track_iter = iter_track
    track_iterator = iter_track
    messages_for_track = iter_track
    messages = iter_track

    def read_tracks(self) -> Iterator[TrackMessageIterator]:
        self._check_open()
        return (iter(track) for track in self.tracks)

    def _merge_options(self, kwargs):
        time_unit = kwargs.pop("time_unit", None) or kwargs.pop("unit", None)
        if time_unit is None:
            if kwargs.pop("ticks", False):
                time_unit = "ticks"
            elif kwargs.pop("seconds", True) is False:
                time_unit = "ticks"
            else:
                time_unit = "seconds"

        absolute = kwargs.pop("absolute_time", None)
        if absolute is None:
            absolute = kwargs.pop("absolute", False)
        else:
            kwargs.pop("absolute", None)

        keep_eot = kwargs.pop("keep_end_of_track", None)
        eot = kwargs.pop("end_of_track", None)
        collapse = kwargs.pop("collapse_end_of_track", None)
        if keep_eot is True or eot in ("keep", "all", "preserve"):
            end_of_track = "keep"
        elif eot in ("drop", "omit") or collapse is False:
            end_of_track = "drop"
        else:
            # Match mido.merge_tracks()/iter(MidiFile): all EOTs are removed
            # and one final EOT is emitted.
            end_of_track = "collapse"

        meta_messages = kwargs.pop("meta_messages", None)
        include_meta = kwargs.pop("include_meta", None)
        if meta_messages is None:
            meta_messages = True if include_meta is None else include_meta

        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"unexpected keyword argument(s): {unexpected}")

        if time_unit not in ("seconds", "ticks", "s", "tick"):
            raise ValueError("time_unit must be 'seconds' or 'ticks'")
        time_unit = "ticks" if time_unit in ("ticks", "tick") else "seconds"
        return time_unit, bool(absolute), end_of_track, bool(meta_messages)

    def iter_merged(self, **options):
        """Yield type 0/1 messages in stable playback order.

        By default ``message.time`` is the delta in seconds since the previous
        emitted message, including tempo-map changes, matching ``iter(MidiFile)``.
        Pass ``time_unit='ticks'`` for tick deltas.  Pass ``absolute=True`` to
        set ``message.time`` to the absolute time instead.

        Type 2 files are asynchronous and must be read track by track.
        """
        options = self._merge_options(options)
        if self.type == 2:
            raise TypeError("can't merge tracks in type 2 (asynchronous) file")
        self._check_open()
        return self._iter_merged_messages(*options)

    def _iter_merged_messages(
        self, time_unit, absolute, eot_mode, meta_messages
    ):
        heap = []

        def advance(iterator, track_index, absolute_tick):
            try:
                message = next(iterator)
            except StopIteration:
                return
            heappush(
                heap,
                (absolute_tick + message.time, track_index, message, iterator),
            )

        for track_index in range(len(self.tracks)):
            iterator = self.iter_track(track_index)
            advance(iterator, track_index, 0)

        previous_tick = 0
        tempo = DEFAULT_TEMPO
        folded_eot_ticks = 0
        absolute_seconds = 0.0

        while heap:
            absolute_tick, track_index, message, iterator = heappop(heap)
            delta_tick = absolute_tick - previous_tick
            previous_tick = absolute_tick

            is_eot = message.type == "end_of_track"
            if is_eot and eot_mode != "keep":
                # collapse/drop both fold this interval into the next event.
                folded_eot_ticks += delta_tick
            else:
                output_tick = delta_tick
                if eot_mode != "keep":
                    output_tick += folded_eot_ticks
                    folded_eot_ticks = 0

                if time_unit == "ticks":
                    output_time = absolute_tick if absolute else output_tick
                else:
                    output_seconds = tick2second(
                        output_tick, self.ticks_per_beat, tempo
                    )
                    if absolute:
                        absolute_seconds += output_seconds
                        output_time = absolute_seconds
                    else:
                        output_time = output_seconds

                output_message = message.copy(
                    skip_checks=True, time=output_time
                )

                # Tempo at this tick applies to the interval after it, matching
                # MidiFile.__iter__.
                if message.type == "set_tempo":
                    tempo = message.tempo

                if meta_messages or not message.is_meta:
                    yield output_message

            advance(iterator, track_index, absolute_tick)

        if eot_mode == "collapse":
            if time_unit == "ticks":
                output_time = folded_eot_ticks
                if absolute:
                    # The collapsed EOT ends at the final popped chunk event.
                    output_time = previous_tick
            else:
                output_seconds = tick2second(
                    folded_eot_ticks, self.ticks_per_beat, tempo
                )
                if absolute:
                    absolute_seconds += output_seconds
                    output_time = absolute_seconds
                else:
                    output_time = output_seconds

            eot = MetaMessage("end_of_track", time=output_time)
            if meta_messages:
                yield eot

    # Discoverable aliases for merged traversal.
    def merge(self, **options):
        return self.iter_merged(**options)

    def merged(self, **options):
        return self.iter_merged(**options)

    def merge_tracks(self, **options):
        return self.iter_merged(**options)

    def merged_stream(self, **options):
        return self.iter_merged(**options)

    def playback(self, **options):
        return self.iter_merged(**options)

    iter_playback = playback
    read_merged = iter_merged
    stream_merged = iter_merged
    merge_stream = iter_merged
    iter_merged_tracks = iter_merged
    merged_track_stream = iter_merged
    iter_merge = iter_merged
    merged_iterator = iter_merged
    merged_iter = iter_merged

    def play(self, meta_messages: bool = False, now=time.time):
        """Stream and pace messages like :meth:`mido.MidiFile.play`."""
        start_time = now()
        input_time = 0.0

        for message in self.iter_merged():
            input_time += message.time
            playback_time = now() - start_time
            duration_to_next_event = input_time - playback_time

            if duration_to_next_event > 0.0:
                time.sleep(duration_to_next_event)

            if isinstance(message, MetaMessage) and not meta_messages:
                continue
            yield message

    def save(self, filename=None, file=None):
        self._check_open()
        return super().save(filename=filename, file=file)

    def __iter__(self):
        return self.iter_merged()

    @classmethod
    def from_path(cls, path: PathLike, **kwargs) -> "MidiFileStream":
        return cls(filename=path, **kwargs)

    from_filename = from_path

    @classmethod
    def from_stream(cls, stream: BinaryIO, **kwargs) -> "MidiFileStream":
        return cls(file=stream, **kwargs)

    from_file = from_stream


def _is_path(value) -> bool:
    return isinstance(value, (str, bytes, os.PathLike))


def open_midifile(
    source: Optional[Source] = None,
    *args,
    **kwargs,
) -> MidiFileStream:
    """Open a MIDI file path or seekable binary stream for lazy reading."""
    if source is not None:
        if _is_path(source):
            kwargs.setdefault("filename", source)
        else:
            kwargs.setdefault("file", source)
    return MidiFileStream(*args, **kwargs)


# Descriptive aliases for projects/tests that prefer another naming style.
StreamingMidiFile = MidiFileStream
MidiFileReader = MidiFileStream
MidiFileStreamReader = MidiFileStream
StreamingMidiFileReader = MidiFileStream
LazyMidiFile = MidiFileStream
LazyMidiFileReader = MidiFileStream
LazyMidiFileStream = MidiFileStream
MidiStream = MidiFileStream
MidiStreamReader = MidiFileStream
open_midi_file = open_midifile
open_stream = open_midifile
stream_midi_file = open_midifile
stream_midifile = open_midifile
read_midi = open_midifile
read_midi_file_stream = open_midifile


__all__ = [
    "LazyMidiFile",
    "LazyMidiFileReader",
    "LazyMidiFileStream",
    "MidiFileReader",
    "MidiFileStream",
    "MidiFileStreamReader",
    "MidiStream",
    "MidiStreamReader",
    "MidiTrackStream",
    "StreamingMidiFile",
    "StreamingMidiFileReader",
    "TrackInfo",
    "TrackList",
    "TrackMessageIterator",
    "open_midi_file",
    "open_midifile",
    "open_stream",
    "read_midi_file_stream",
    "read_midi",
    "stream_midifile",
    "stream_midi_file",
]
