import io
import struct

import pytest

from mido import Message, MidiFile, MidiTrack
from mido.midifiles.meta import (
    MetaMessage,
    encode_variable_int,
)
from mido.midifiles.units import (
    normalize_frame_rate,
    second2tick,
    second2tick_smpte,
    tick2second,
    tick2second_smpte,
)


def make_division(marker, ticks_per_frame):
    return (((256 - marker) & 0xff) << 8) | ticks_per_frame


def make_track(events):
    data = bytearray()
    for delta, event in events:
        data.extend(encode_variable_int(delta))
        data.extend(event)
    return bytes(data)


def make_file(division, tracks):
    data = bytearray(struct.pack('>4sLhhH', b'MThd', 6, 1, len(tracks),
                                 division))
    for track in tracks:
        data.extend(struct.pack('>4sL', b'MTrk', len(track)))
        data.extend(track)
    return MidiFile(file=io.BytesIO(bytes(data)))


def note_on(note=64, velocity=64):
    return bytes([0x90, note, velocity])


def note_off(note=64, velocity=64):
    return bytes([0x80, note, velocity])


def set_tempo(tempo=500000):
    return bytes([0xff, 0x51, 0x03,
                  (tempo >> 16) & 0xff,
                  (tempo >> 8) & 0xff,
                  tempo & 0xff])


END_OF_TRACK = bytes([0xff, 0x2f, 0x00])


@pytest.mark.parametrize(
    'marker,ticks_per_frame,frame_rate',
    [(24, 80, 24),
     (25, 40, 25),
     (29, 100, 29.97),
     (30, 80, 30)])
def test_read_smpte_timing(marker, ticks_per_frame, frame_rate):
    duration_ticks = int(round(frame_rate * ticks_per_frame))
    tempo_at = duration_ticks // 4

    mid = make_file(
        make_division(marker, ticks_per_frame),
        [make_track([
            (0, note_on()),
            (tempo_at, set_tempo(250000)),
            (duration_ticks - tempo_at, note_off()),
            (0, END_OF_TRACK),
        ])])

    assert mid.is_smpte
    assert mid.is_frame_based
    assert mid.ticks_per_beat is None
    assert mid.frame_rate == pytest.approx(frame_rate)
    assert mid.smpte_fps == pytest.approx(frame_rate)
    assert mid.frames_per_second == pytest.approx(frame_rate)
    assert mid.ticks_per_frame == ticks_per_frame
    assert mid.division == make_division(marker, ticks_per_frame)
    assert mid.length == pytest.approx(1.0)

    messages = list(mid)
    assert messages[0].type == 'note_on'
    assert messages[0].time == 0
    # The tempo message is preserved and arrives at the right frame-based
    # time, but its value must not affect the following interval.
    assert messages[1].type == 'set_tempo'
    assert messages[1].tempo == 250000
    assert messages[1].time == pytest.approx(tempo_at /
                                             (frame_rate * ticks_per_frame))
    assert messages[2].type == 'note_off'
    assert messages[2].time == pytest.approx(
        (duration_ticks - tempo_at) /
        (frame_rate * ticks_per_frame))
    assert sum(msg.time for msg in messages) == pytest.approx(1.0)


@pytest.mark.parametrize(
    'marker,ticks_per_frame',
    [(24, 80),
     (25, 40),
     (29, 100),
     (30, 80)])
def test_smpte_save_roundtrip_preserves_timing(marker, ticks_per_frame):
    duration_ticks = 2 * int(round(normalize_frame_rate(marker) *
                                   ticks_per_frame))
    mid = make_file(
        make_division(marker, ticks_per_frame),
        [make_track([
            (0, set_tempo(400000)),
            (0, note_on()),
            (duration_ticks, note_off()),
            (0, END_OF_TRACK),
        ])])

    outfile = io.BytesIO()
    mid.save(file=outfile)
    data = outfile.getvalue()
    saved_division = struct.unpack('>H', data[12:14])[0]
    assert saved_division == make_division(marker, ticks_per_frame)

    outfile.seek(0)
    reloaded = MidiFile(file=outfile)
    assert reloaded.is_smpte
    assert reloaded.frame_rate == pytest.approx(mid.frame_rate)
    assert reloaded.ticks_per_frame == mid.ticks_per_frame
    assert reloaded.division == mid.division
    assert reloaded.length == pytest.approx(mid.length)
    assert reloaded.length == pytest.approx(2.0)

    reloaded_tempo = [msg for msg in reloaded.merged_track
                      if msg.type == 'set_tempo']
    assert len(reloaded_tempo) == 1
    assert reloaded_tempo[0].tempo == 400000


def test_smpte_cross_track_events():
    # 25 fps * 40 ticks per frame = 1000 ticks per second.
    track0 = make_track([
        (0, note_on(60)),
        (480, END_OF_TRACK),
    ])
    track1 = make_track([
        (240, note_on(67)),
        (360, note_off(67)),
        (360, END_OF_TRACK),
    ])
    mid = make_file(make_division(25, 40), [track0, track1])

    messages = list(mid)
    assert messages[0].time == 0
    assert messages[1].time == pytest.approx(0.240)
    assert messages[2].time == pytest.approx(0.360)
    assert messages[3].time == pytest.approx(0.360)
    assert mid.length == pytest.approx(0.960)


def test_create_smpte_file_and_read_back():
    mid = MidiFile(type=1, frame_rate=25, ticks_per_frame=40)
    track = mid.add_track()
    track.append(Message('note_on', note=60, time=0))
    track.append(Message('note_off', note=60, time=500))

    assert mid.is_smpte
    assert mid.ticks_per_beat is None
    assert mid.length == pytest.approx(0.5)

    outfile = io.BytesIO()
    mid.save(file=outfile)
    outfile.seek(0)
    reloaded = MidiFile(file=outfile)

    assert reloaded.is_smpte
    assert reloaded.frame_rate == 25
    assert reloaded.ticks_per_frame == 40
    assert reloaded.length == pytest.approx(0.5)


def test_smpte_repr_roundtrip():
    mid = MidiFile(type=1, frame_rate=29.97, ticks_per_frame=80,
                   tracks=[MidiTrack([
                       Message('note_on', note=60, time=0),
                       Message('note_off', note=60, time=2397),
                   ])])
    evaluated = eval(repr(mid))  # noqa: S307
    assert evaluated.is_smpte
    assert evaluated.frame_rate == pytest.approx(29.97)
    assert evaluated.ticks_per_frame == 80
    assert evaluated.length == pytest.approx(mid.length)


@pytest.mark.parametrize('marker', [23, 26, 27, 28, 31, 32])
def test_invalid_smpte_frame_rate_marker(marker):
    with pytest.raises(ValueError, match='frame rate'):
        make_file(make_division(marker, 40),
                  [make_track([(0, END_OF_TRACK)])])


def test_zero_ticks_per_frame():
    with pytest.raises(ValueError, match='ticks per frame'):
        make_file(make_division(25, 0),
                  [make_track([(0, END_OF_TRACK)])])


def test_zero_ticks_per_beat():
    with pytest.raises(ValueError, match='ticks per beat'):
        make_file(0, [make_track([(0, END_OF_TRACK)])])


@pytest.mark.parametrize('frame_rate', [0, 23, 26, 27, 28, 31, -24])
def test_invalid_frame_rate_argument(frame_rate):
    with pytest.raises(ValueError, match='frame rate'):
        MidiFile(type=1, frame_rate=frame_rate, ticks_per_frame=40)


@pytest.mark.parametrize('ticks_per_frame', [0, -1, 256, 1.5, True])
def test_invalid_ticks_per_frame_argument(ticks_per_frame):
    with pytest.raises((TypeError, ValueError), match='ticks_per_frame'):
        MidiFile(type=1, frame_rate=25, ticks_per_frame=ticks_per_frame)


def test_conflicting_frame_rate_aliases():
    with pytest.raises(ValueError, match='conflicting'):
        MidiFile(type=1, frame_rate=25, smpte_fps=30,
                 ticks_per_frame=40)


def test_conflicting_timing_modes():
    with pytest.raises(ValueError, match='ticks_per_beat'):
        MidiFile(type=1, ticks_per_beat=480, frame_rate=25,
                 ticks_per_frame=40)


def test_smpte_requires_ticks_per_frame_and_vice_versa():
    with pytest.raises(ValueError, match='ticks_per_frame'):
        MidiFile(type=1, frame_rate=25)
    with pytest.raises(ValueError, match='ticks_per_frame'):
        MidiFile(type=1, ticks_per_frame=40)


def test_smpte_unit_conversions():
    assert tick2second_smpte(100, 25, 40) == pytest.approx(0.1)
    assert tick2second_smpte(2997, 29, 100) == pytest.approx(1.0)
    assert tick2second_smpte(2997, 29.97, 100) == pytest.approx(1.0)
    assert second2tick_smpte(1, 29.97, 100) == 2997

    assert tick2second(100, frame_rate=25, ticks_per_frame=40) == \
        pytest.approx(0.1)
    assert second2tick(0.1, frame_rate=25, ticks_per_frame=40) == 100


@pytest.mark.parametrize('func', [tick2second, second2tick])
def test_frame_and_tempo_conversions_cannot_be_mixed(func):
    with pytest.raises(ValueError, match='cannot be mixed'):
        func(1, ticks_per_beat=100, tempo=500000,
             frame_rate=25, ticks_per_frame=40)


@pytest.mark.parametrize('func', [tick2second, second2tick])
def test_tempo_conversion_requires_timing_information(func):
    with pytest.raises(ValueError, match='ticks_per_beat'):
        func(1, tempo=500000)
    with pytest.raises(ValueError, match='tempo'):
        func(1, ticks_per_beat=100)
    with pytest.raises(ValueError, match='ticks_per_beat'):
        func(1, ticks_per_beat=0, tempo=500000)


@pytest.mark.parametrize('frame_rate', [24, 25, 30])
def test_normalize_integer_frame_rates(frame_rate):
    assert normalize_frame_rate(frame_rate) == frame_rate


def test_normalize_drop_frame_rate():
    assert normalize_frame_rate(29) == pytest.approx(29.97)
    assert normalize_frame_rate(29.97) == pytest.approx(29.97)


def test_smpte_tempo_messages_do_not_change_playback_time():
    division = make_division(25, 40)
    with_tempo = make_file(division, [make_track([
        (0, set_tempo(100000)),
        (0, note_on()),
        (1000, note_off()),
        (0, END_OF_TRACK),
    ])])
    without_tempo = make_file(division, [make_track([
        (0, note_on()),
        (1000, note_off()),
        (0, END_OF_TRACK),
    ])])

    assert with_tempo.length == pytest.approx(1.0)
    assert without_tempo.length == pytest.approx(1.0)
    assert with_tempo.length == pytest.approx(without_tempo.length)


def test_beat_timing_unchanged():
    mid = MidiFile(type=1, ticks_per_beat=480)
    track = mid.add_track()
    track.append(MetaMessage('set_tempo', tempo=500000, time=0))
    track.append(Message('note_on', note=60, time=480))
    track.append(Message('note_off', note=60, time=480))

    assert not mid.is_smpte
    assert mid.frame_rate is None
    assert mid.ticks_per_frame is None
    assert mid.division == 480
    assert mid.length == pytest.approx(1.0)

    outfile = io.BytesIO()
    mid.save(file=outfile)
    outfile.seek(0)
    reloaded = MidiFile(file=outfile)
    assert not reloaded.is_smpte
    assert reloaded.ticks_per_beat == 480
    assert reloaded.length == pytest.approx(1.0)
