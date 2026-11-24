# SPDX-FileCopyrightText: 2017 Ole Martin Bjorndalen <ombdalen@gmail.com>
#
# SPDX-License-Identifier: MIT

from numbers import Integral


# Supported SMPTE frame rates. The two's complement marker stored in
# the MIDI file header uses 29 for the 30-drop-frame rate, which runs
# at 29.97 frames per second.
SMPTE_FRAME_RATES = {24: 24, 25: 25, 29: 29.97, 30: 30}


def normalize_frame_rate(frame_rate):
    """Return the frames per second for a SMPTE frame rate.

    Both the file header marker 29 and the actual 29.97 frame rate are
    accepted and normalized to 29.97. Other values raise ``ValueError``.
    """
    if frame_rate == 29.97:
        return 29.97

    try:
        normalized = SMPTE_FRAME_RATES[frame_rate]
    except (KeyError, TypeError):
        normalized = None

    if normalized is None:
        raise ValueError(
            'invalid SMPTE frame rate {!r} (must be 24, 25, 29 or 30, '
            'where 29 is 29.97 fps)'.format(frame_rate))

    return normalized


def _check_ticks_per_frame(ticks_per_frame):
    if (not isinstance(ticks_per_frame, Integral) or
            isinstance(ticks_per_frame, bool)):
        raise ValueError('ticks_per_frame must be an integer')
    if ticks_per_frame <= 0:
        raise ValueError('ticks_per_frame must be greater than 0')
    return ticks_per_frame


def tick2second_smpte(tick, frame_rate, ticks_per_frame):
    """Convert absolute SMPTE ticks to seconds.

    The frame rate may be 24, 25, 30 or 29/29.97. Ticks are not
    affected by tempo (set_tempo) messages in the file.
    """
    fps = normalize_frame_rate(frame_rate)
    ticks_per_frame = _check_ticks_per_frame(ticks_per_frame)
    return tick / (fps * ticks_per_frame)


def second2tick_smpte(second, frame_rate, ticks_per_frame):
    """Convert seconds to absolute SMPTE ticks.

    Normal rounding applies.
    """
    fps = normalize_frame_rate(frame_rate)
    ticks_per_frame = _check_ticks_per_frame(ticks_per_frame)
    return int(round(second * fps * ticks_per_frame))


def tick2second(tick, ticks_per_beat=None, tempo=None,
                frame_rate=None, ticks_per_frame=None):
    """Convert absolute time in ticks to seconds.

    For beat-based timing pass ``ticks_per_beat`` and ``tempo``. For
    SMPTE frame-based timing pass ``frame_rate`` and
    ``ticks_per_frame``. The two timing modes cannot be mixed.

    Returns absolute time in seconds for a chosen MIDI file time
    resolution (ticks/pulses per quarter note, also called PPQN) and
    tempo (microseconds per quarter note).
    """
    if frame_rate is not None or ticks_per_frame is not None:
        if ticks_per_beat is not None or tempo is not None:
            raise ValueError(
                'tempo-based and SMPTE frame-based timing cannot be mixed')
        return tick2second_smpte(tick, frame_rate, ticks_per_frame)

    if ticks_per_beat is None:
        raise ValueError('ticks_per_beat is required for tempo-based timing')
    if tempo is None:
        raise ValueError('tempo is required for tempo-based timing')
    if ticks_per_beat <= 0:
        raise ValueError('ticks_per_beat must be greater than 0')

    scale = tempo * 1e-6 / ticks_per_beat
    return tick * scale


def second2tick(second, ticks_per_beat=None, tempo=None,
                frame_rate=None, ticks_per_frame=None):
    """Convert absolute time in seconds to ticks.

    For beat-based timing pass ``ticks_per_beat`` and ``tempo``. For
    SMPTE frame-based timing pass ``frame_rate`` and
    ``ticks_per_frame``. The two timing modes cannot be mixed.

    Returns absolute time in ticks for a chosen MIDI file time
    resolution (ticks/pulses per quarter note, also called PPQN) and
    tempo (microseconds per quarter note). Normal rounding applies.
    """
    if frame_rate is not None or ticks_per_frame is not None:
        if ticks_per_beat is not None or tempo is not None:
            raise ValueError(
                'tempo-based and SMPTE frame-based timing cannot be mixed')
        return second2tick_smpte(second, frame_rate, ticks_per_frame)

    if ticks_per_beat is None:
        raise ValueError('ticks_per_beat is required for tempo-based timing')
    if tempo is None:
        raise ValueError('tempo is required for tempo-based timing')
    if ticks_per_beat <= 0:
        raise ValueError('ticks_per_beat must be greater than 0')

    scale = tempo * 1e-6 / ticks_per_beat
    return int(round(second / scale))


# Convenient aliases using the more common function-name ordering.
smpte_tick2second = tick2second_smpte
smpte_second2tick = second2tick_smpte


def bpm2tempo(bpm, time_signature=(4, 4)):
    """Convert BPM (beats per minute) to MIDI file tempo (microseconds per
    quarter note).

    Depending on the chosen time signature a bar contains a different number of
    beats. These beats are multiples/fractions of a quarter note, thus the
    returned BPM depend on the time signature. Normal rounding applies.
    """
    return int(round(60 * 1e6 / bpm * time_signature[1] / 4.))


def tempo2bpm(tempo, time_signature=(4, 4)):
    """Convert MIDI file tempo (microseconds per quarter note) to BPM (beats
    per minute).

    Depending on the chosen time signature a bar contains a different number of
    beats. The beats are multiples/fractions of a quarter note, thus the
    returned tempo depends on the time signature denominator.
    """
    return 60 * 1e6 / tempo * time_signature[1] / 4.
