"""Build the edited voice from the cut, then lay music and effects under it."""

from __future__ import annotations

import json
import re
import tempfile
import wave
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from . import beats, clean, ffmpeg
from .cut.snap import frame_count
from .errors import AltocueError
from .models import Edl
from .project import Project, write_json

RATE = 48000
FADE_MS = 30.0
SFX_DIR = Path(__file__).parent / "data" / "sfx"

VOICE_LUFS = -16.0  # the level the constants below were proven at
MUSIC_UNDER_VOICE_LU = 5.0  # before ducking
MUSIC_FADE_IN = 0.5
MUSIC_FADE_OUT = 2.6
MUSIC_LAND_FADE = 0.6  # keeps the track's own ending, and still never stops on a click
MUSIC_LOOP_BLEND = 0.5
MUSIC_SILENT_DB = -50.0
MUSIC_ENDS = ("land", "fade", "own")  # "own" is a score that ends itself: no loop, no backtiming, no fade
MUSIC_GUARD = 0.005  # against a click, and no more
FRAMES_PER_SECOND = 100
SYLLABLE_FRAMES = 5
SYLLABLE_HOLD = 15  # 150 ms either side
DUCK_UNDER_VOICE_DB = 18.0  # the default `[audio] music_under`. 12 is a montage with a line over it, and speech needs more room
DUCK_UNDER_RANGE = (6.0, 30.0)
DUCK_LEAST_DB = 7.0  # a dip the ear can hear, even when the level alone would not ask for one
DUCK_BEFORE = 0.25  # a word lands on music that is already down
DUCK_AFTER = 0.8  # coming back is slower than going down
DUCK_HOLD = 0.1
DUCK_BRIDGE = 1.2  # a shorter gap is a pause inside the talk, and the music stays down through it
DUCK_RETURN = 2.5  # a gap this long is a real break, and the music comes all the way back
DUCK_PARTIAL_DB = 4.0  # between the two the music may rise this far, so a kept pause never swells
DUCK_WINDOW = 2  # seconds of voice and music that set the depth
DUCK_TRUST = 0.25  # seconds of speech in that window before its level is believed
CARVE_HZ, CARVE_Q, CARVE_DB = 2500.0, 0.8, 4.0  # under speech the bed also loses this much where consonants live
END_FADE_FRAMES = 6  # the room fades over this many frames at the end of a film that has no tail
RIDER_GATE_BELOW_VOICE_DB = 26.0
SFX_UNDER_DB = 10.0
SFX_UNDER_RANGE = (8.0, 12.0)
SFX_PEAK_OVER_VOICE_DB = 7.0
PEAK_LIMIT = 10 ** (-1 / 20)
# With no voice the music leads. These start from one repo's measurements of its own films: effect
# peaks 3 to 10 dB over the music, a closing fade of about 0.27 s, a 6 to 12 dB dip before a big hit.
BED_LUFS = -18.0  # leaves the hits room over the music, and the master sets the final level
BED_OVER_DB = 6.0  # for a sound with no row in the table
BED_OVER_RANGE = (0.0, 14.0)
BED_WINDOW = 0.4  # how much music before a hit it is levelled against
BED_QUIET_DB = 6.0  # a fade-in or a rest does not take a hit down further than this
HIT_PEAK_DB = -3.0
TIGHT_HEAD = 0.03
TIGHT_TAIL = 0.27
DIP_RANGE = (6.0, 12.0)
DIP_SECONDS = 0.4
DIP_BACK = 0.02
ALIGNS = ("start", "peak", "end")  # what meets the event: the onset, the loudest sample, or the end, all as `marks` finds them
SYNCS = ("picture", "free")  # picture: a change on screen, and the sound keeps its frame. free: it may move into clear air
ONSET_DB = 20.0  # a sound's onset is its first sample within this of its peak, and its end is its last
TAIL_DB = 30.0  # past its last sample within this of its peak, a sound is not heard over a voice
EVENT_SLACK = 0.02  # frames: a time this close to a frame is on it
# Effects and words. All house values, and nobody has listened: a hit on the first sound of a word takes its
# consonant, and one on the last half second of a line, or the breath after it, is heard as a stray noise on the voice.
ONSET_BEFORE = 0.05
ONSET_AFTER = 0.1
LINE_GAP = 0.5  # a pause this long ends a spoken line, as `cut` counts one
LINE_TAIL = 0.5
LINE_BREATH = 0.35
SENTENCE_END = (".", "?", "!")
NUDGE_MOST = 0.2  # a free sound moves this far at most to find clear air
NUDGE_STEP = 0.005
UNDER_WORD_DB = 6.0  # a sound that keeps its frame on a word comes down this much further
SHORTEN_FADE = 0.02
SHORTEN_LEAST = 0.03  # a sound is cut short only if this much of it is left after its hit
# A sound heard again is a little quieter, and an untuned one a little higher or lower, so a repeat is not a machine.
VARY_GAIN_DB = (0.0, -1.0, -0.5, -1.5, -0.25, -0.75)
VARY_CENTS = (0, 35, -25, 50, -40, 15)
# Music that ends on a bar. House values.
BAR_REACH = 1.0  # seconds the track may enter later under a voice, or open further in, so a bar line meets the last frame
BAR_END_FADE = (0.25, 0.6)  # the last beat fades, within these seconds, ending on the bar line
SILENT_VOICE = 1e-4
ROOM_SECONDS = 8  # long enough that no ear catches the repeat
ROOM_FRAME = 2048
ROOM_CLEAR_DB = 20.0  # under the speech by this much, or the quietest stretch is not a room
ROOM_LOUDEST_DB = -40.0
ROOM_QUIETEST_DB = -96.0  # under this it is rounding, not a room
ROOM_BLEND = 0.015  # between one clip's room and the next
ROOM_HOP = 240
ROOM_HOLE_DB = 3.0  # how far the voice must fall under the room to count as a hole
ROOM_LEAD = 3  # frames
ROOM_SMOOTH = 5  # frames
ROOM_TAIL_FADE = 1.0
STEMS = ("voice", "music", "effects")


@dataclass
class Piece:
    clip: str
    source: np.ndarray
    src: int
    start: int
    end: int
    gain: float
    lead: int = 0
    trail: int = 0
    fade_in: int = 0
    fade_out: int = 0
    linear_in: bool = False
    linear_out: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def spare_after(self) -> int:
        return max(len(self.source) - self.src - self.length, 0) if len(self.source) else self.length

    @property
    def spare_before(self) -> int:
        return self.src if len(self.source) else self.length


def _take(source: np.ndarray, first: int, count: int) -> np.ndarray:
    """A slice that reads as silence past either end of the source."""
    out = np.zeros(count, dtype=np.float64)
    low, high = max(first, 0), min(first + count, len(source))
    if high > low:
        out[low - first : high - first] = source[low:high]
    return out


def _curve(count: int, linear: bool) -> np.ndarray:
    position = (np.arange(count) + 0.5) / count
    return position if linear else np.sin(position * np.pi / 2)


def _pieces(project: Project, edl: Edl) -> list[Piece]:
    """Lay segments end to end. Each lasts exactly as many frames as the plate gives it."""
    clips = {clip.id: clip for clip in project.clips()}
    loaded: dict[str, np.ndarray] = {}
    pieces: list[Piece] = []
    frames = 0
    for segment in edl.segments:
        if segment.end <= segment.start:
            raise AltocueError(f"Segment {segment.id} ends before it starts.", fix="Fix edl.json, or run `altocue cut`.")
        length = frame_count(segment.start, segment.end, edl.fps)
        if length < 1:  # the cut refuses this too. A piece of no frames would leave the sound a shot behind the picture
            raise AltocueError(f"Segment {segment.id} is shorter than one frame at {edl.fps} fps.", fix="Lengthen or delete it in edl.json, or run `altocue cut`.")
        if segment.clip not in loaded:
            path = project.audio_dir / f"{segment.clip}.wav"
            if segment.clip in clips and not clips[segment.clip].has_audio:
                loaded[segment.clip] = np.zeros(0, dtype=np.float32)
            elif path.exists():
                loaded[segment.clip], rate = ffmpeg.read_wav(path)
                if rate != RATE:
                    raise AltocueError(f"{path} is {rate} Hz, not {RATE}.", fix="Run `altocue clean --force`.")
            else:
                raise AltocueError(f"No cleaned audio for clip {segment.clip}.", fix="Run `altocue clean`.")
        start = round(frames / edl.fps * RATE)
        frames += length
        end = round(frames / edl.fps * RATE)
        pieces.append(Piece(segment.clip, loaded[segment.clip], round(segment.start * RATE), start, end, segment.gain))
    return pieces


def _join(a: Piece, b: Piece, half: int) -> None:
    """Blend two neighbours around their join, using sound from just outside each cut."""
    gap = b.src - (a.src + a.length)
    if a.clip == b.clip and abs(gap) <= 1:
        b.src = a.src + a.length
        return
    half = min(half, a.length // 2, b.length // 2)
    if half < 1:
        return
    correlated = a.clip == b.clip and abs(gap) < 2 * half  # equal power would bump the level
    trail = min(half, a.spare_after)
    lead = min(2 * half - trail, b.spare_before)
    trail = min(2 * half - lead, a.spare_after)
    if lead + trail < half:
        a.fade_out, b.fade_in = half, half
        return
    a.trail, b.lead = trail, lead
    a.fade_out, b.fade_in = lead + trail, lead + trail
    a.linear_out, b.linear_in = correlated, correlated


def room_loop(samples: np.ndarray) -> np.ndarray | None:
    """A seamless loop of this clip's own room, or None if it has no quiet stretch to sample.

    The stretch can be a quarter second, and repeating that flutters. So the loop keeps its
    spectrum and level with fresh phases, which also makes the join exact. Fixed seed, same loop.
    """
    if len(samples) < ROOM_FRAME * 2:
        return None
    stretch = clean.quietest(samples, RATE).astype(np.float64)
    level = 20 * np.log10(np.sqrt(np.mean(stretch**2)) + 1e-12)
    if not ROOM_QUIETEST_DB < level <= ROOM_LOUDEST_DB or clean.speech_level(samples, RATE) - level < ROOM_CLEAR_DB:
        return None
    count = (len(stretch) - ROOM_FRAME) // (ROOM_FRAME // 2) + 1
    frames = np.stack([stretch[i * ROOM_FRAME // 2 : i * ROOM_FRAME // 2 + ROOM_FRAME] for i in range(max(count, 1))])
    power = np.mean(np.abs(np.fft.rfft(frames * np.hanning(frames.shape[1]), axis=1)) ** 2, axis=0)
    size = ROOM_SECONDS * RATE
    magnitude = np.interp(np.fft.rfftfreq(size, 1 / RATE), np.fft.rfftfreq(frames.shape[1], 1 / RATE), np.sqrt(power))
    phases = np.random.default_rng(0).uniform(0, 2 * np.pi, len(magnitude))
    loop = np.fft.irfft(magnitude * np.exp(1j * phases), size)
    return loop * 10 ** (level / 20) / np.sqrt(np.mean(loop**2))


def _room_layer(pieces: list[Piece], loops: dict[str, np.ndarray | None], count: int) -> tuple[np.ndarray, np.ndarray]:
    """Each piece's room at its own gain, and the level it should hold, sample by sample.

    A piece with no room of its own, such as a clip with no sound, borrows the nearest neighbour's.
    """
    room, held = np.zeros(count), np.zeros(count)
    blend = round(ROOM_BLEND * RATE)
    chain = [loops.get(piece.clip) for piece in pieces]
    for order in (range(1, len(chain)), range(len(chain) - 2, -1, -1)):
        for index in order:
            before = chain[index - 1 if order.step > 0 else index + 1]
            chain[index] = chain[index] if chain[index] is not None else before
    for piece, loop in zip(pieces, chain, strict=True):
        if loop is None:
            continue
        low, high = max(piece.start - blend, 0), min(piece.end + blend, count)
        at = np.arange(low, high)
        shape = np.clip(np.minimum(at - piece.start + blend, piece.end + blend - at) / (2 * blend), 0, 1)
        room[low:high] += loop[at % len(loop)] * shape * piece.gain
        held[low:high] += np.sqrt(np.mean(loop**2)) * shape * piece.gain
    return room, held


def _smooth(values: np.ndarray) -> np.ndarray:
    return np.convolve(values, np.ones(ROOM_SMOOTH) / ROOM_SMOOTH)[ROOM_SMOOTH // 2 : ROOM_SMOOTH // 2 + len(values)]


def fill_holes(voice: np.ndarray, room: np.ndarray, held: np.ndarray) -> tuple[np.ndarray, float]:
    """Lay the room in wherever the voice falls under it. Where the voice holds up, nothing is added."""
    count = len(voice) // ROOM_HOP
    if not count or not held.any():
        return voice, 0.0
    size = (count, ROOM_HOP)
    have = np.mean(voice[: count * ROOM_HOP].reshape(size) ** 2, axis=1)
    want = np.mean(held[: count * ROOM_HOP].reshape(size) ** 2, axis=1) * 10 ** (-ROOM_HOLE_DB / 10)
    short = np.sqrt(np.clip(1 - have / (want + 1e-20), 0, 1)) * (want > 0)
    missing = _smooth(np.lib.stride_tricks.sliding_window_view(np.pad(short, ROOM_LEAD, mode="edge"), 2 * ROOM_LEAD + 1).max(axis=1))  # arrives before the voice has gone
    times = (np.arange(count) + 0.5) * ROOM_HOP
    return voice + room * np.interp(np.arange(len(voice)), times, missing), float(np.sum(missing > 0.5) * ROOM_HOP / RATE)


def mix_voice(project: Project, edl: Edl, dest: str | Path, fade_ms: float = FADE_MS) -> Path:
    """Write the edited voice: sample-accurate, frame-exact, and faded at every join.

    Room tone goes in beside it, so no cut drops to digital silence. The last clip's room is
    written to `<dest>-room.wav`, for `mix_bed` to run on under anything past the voice.
    """
    if not edl.segments:
        raise AltocueError("The cut is empty.", fix="Run `altocue cut` and check edl.json.")
    pieces = _pieces(project, edl)
    half = max(1, round(fade_ms / 1000 * RATE / 2))
    for a, b in zip(pieces, pieces[1:], strict=False):
        _join(a, b, half)
    pieces[0].fade_in = min(half, pieces[0].length // 2)
    pieces[-1].fade_out = min(half, pieces[-1].length // 2)

    out = np.zeros(pieces[-1].end, dtype=np.float64)
    for piece in pieces:
        first, last = piece.start - piece.lead, piece.end + piece.trail
        chunk = _take(piece.source, piece.src - piece.lead, last - first) * piece.gain
        if piece.fade_in:
            chunk[: piece.fade_in] *= _curve(piece.fade_in, piece.linear_in)
        if piece.fade_out:
            chunk[-piece.fade_out :] *= _curve(piece.fade_out, piece.linear_out)[::-1]
        out[max(first, 0) : min(last, len(out))] += chunk[max(-first, 0) : len(chunk) - max(last - len(out), 0)]
    dest = Path(dest)
    report: dict[str, Any] = {"room": {}, "filled_seconds": 0.0}
    room_file = dest.with_name(f"{dest.stem}-room.wav")
    room_file.unlink(missing_ok=True)
    if project.setting("audio", "room_tone", True):
        loops = {piece.clip: room_loop(piece.source) for piece in {piece.clip: piece for piece in pieces}.values()}
        report["room"] = {clip: round(float(20 * np.log10(np.sqrt(np.mean(loop**2)))), 1) for clip, loop in loops.items() if loop is not None}
        room, held = _room_layer(pieces, loops, len(out))
        room[: pieces[0].fade_in] *= _curve(pieces[0].fade_in, False)
        room[len(room) - pieces[-1].fade_out :] *= _curve(pieces[-1].fade_out, False)[::-1]
        out, report["filled_seconds"] = fill_holes(out, room, held)
        last = next((loops[piece.clip] for piece in reversed(pieces) if loops[piece.clip] is not None), None)
        if last is not None:
            ffmpeg.write_wav(room_file, last, RATE)
    peak = np.abs(out).max()
    write_json(dest.with_suffix(".json"), report)
    return ffmpeg.write_wav(dest, out / peak if peak > 1 else out, RATE)


def sfx_names() -> list[str]:
    return sorted(path.stem for path in SFX_DIR.glob("*.ogg"))


def sfx_path(name: str) -> Path:
    path = SFX_DIR / f"{name}.ogg"
    if not path.exists():
        raise AltocueError(f"No effect named {name}.", fix=f"Use one of {', '.join(sfx_names())}.")
    return path


def max_momentary(path: str | Path) -> float:
    """Loudest 400 ms in LUFS. Effects are too short for an integrated reading."""
    done = ffmpeg.run(["-i", str(path), "-af", "apad=whole_dur=1,ebur128=framelog=info", "-f", "null", "-"])
    values = [float(v) for v in re.findall(r"\bM:\s*(-?[\d.]+)", done.stderr)]
    return max(values, default=-120.0)


def _read_stereo(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64).reshape(-1, 2) / 32768.0


def _write_stereo(path: Path, samples: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(np.rint(np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return path


def _voice_level(path: Path) -> float:
    level = ffmpeg.loudness(path)["integrated"]
    return level if level > -69 else VOICE_LUFS


def _level_db(samples: np.ndarray, hop: int) -> np.ndarray:
    count = len(samples) // hop
    frames = samples[: count * hop].reshape(count, hop)
    return 20 * np.log10(np.sqrt(np.mean(frames**2, axis=1)) + 1e-9)


def _raised(position: np.ndarray) -> np.ndarray:
    return 0.5 - 0.5 * np.cos(np.pi * np.clip(position, 0, 1))


def _duck_shape(speaking: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How far down the music is, 0 to 1 per frame: down before the word, back up after it, slowly.

    A gap under `DUCK_BRIDGE` keeps the music down. The second array marks the gaps from there up to
    `DUCK_RETURN`, where the music may come up a little and no further. A longer gap lets it back.
    """
    bridged = speaking.copy()
    partial = np.zeros(len(speaking), dtype=bool)
    edges = np.flatnonzero(np.diff(np.r_[0, speaking.astype(np.int8), 0]))
    starts, ends = edges[::2], edges[1::2]
    for end, start in zip(ends[:-1], starts[1:], strict=True):
        if start - end < DUCK_BRIDGE * FRAMES_PER_SECOND:
            bridged[end:start] = True
        elif start - end < DUCK_RETURN * FRAMES_PER_SECOND:
            partial[end:start] = True
    edges = np.flatnonzero(np.diff(np.r_[0, bridged.astype(np.int8), 0]))
    shape = np.zeros(len(speaking))
    frames = np.arange(len(speaking))
    before, after = DUCK_BEFORE * FRAMES_PER_SECOND, DUCK_AFTER * FRAMES_PER_SECOND
    hold = DUCK_HOLD * FRAMES_PER_SECOND
    for start, end in zip(edges[::2], edges[1::2], strict=True):
        shape = np.maximum(shape, _raised((frames - (start - before)) / before) * _raised(((end + hold + after) - frames) / after))
    return shape, partial


def _mean_db(power: np.ndarray, weight: np.ndarray) -> np.ndarray:
    """Level over the last and next second, counting only the weighted frames."""
    kernel = np.ones(DUCK_WINDOW * FRAMES_PER_SECOND + 1)
    total, count = np.convolve(power * weight, kernel), np.convolve(weight.astype(float), kernel)
    middle = DUCK_WINDOW * FRAMES_PER_SECOND // 2
    return 10 * np.log10((total / (count + 1e-9))[middle : middle + len(power)] + 1e-12)


def _ride(bed: np.ndarray, voice: np.ndarray, gate_db: float, under: float = DUCK_UNDER_VOICE_DB, carved: np.ndarray | None = None) -> tuple[np.ndarray, float]:
    """Duck the bed `under` dB below speech. Gain only ever comes down, and it moves on a smooth ramp.

    The file is known in advance, so the ramp starts before the first word and is finished as it
    lands. Depth follows the voice and the music over seconds, not syllables, so nothing pumps.
    `carved` is the same bed with its presence band cut. It is blended in on the same ramp, so the
    music gives the consonants room only while someone is speaking.
    """
    hop = RATE // FRAMES_PER_SECOND
    count = len(bed) // hop
    spoken = np.zeros(count * hop)
    spoken[: min(len(voice), count * hop)] = voice[: count * hop]
    syllable = np.ones(SYLLABLE_FRAMES) / SYLLABLE_FRAMES
    voice_power = np.convolve(10 ** (_level_db(spoken, hop) / 10), syllable)[SYLLABLE_FRAMES // 2 : SYLLABLE_FRAMES // 2 + count]
    bed_power = 10 ** (_level_db(bed[: count * hop].mean(axis=1), hop) / 10)
    voice_db = 10 * np.log10(voice_power + 1e-12)
    speaking = voice_db > gate_db
    if not speaking.any():
        return bed, 0.0
    spoken_db = _mean_db(voice_power, speaking)
    middle = DUCK_WINDOW * FRAMES_PER_SECOND // 2
    heard = np.convolve(speaking.astype(float), np.ones(2 * middle + 1))[middle : middle + count]
    near = np.flatnonzero(heard >= min(DUCK_TRUST * FRAMES_PER_SECOND, heard.max()) - 0.5)
    # Deep in a pause the window holds too little speech to level by, so the level moves from one line to the next.
    spoken_db = np.interp(np.arange(count), near, spoken_db[near])
    depth = np.minimum(spoken_db - under - _mean_db(bed_power, np.ones(count, dtype=bool)), -DUCK_LEAST_DB)
    shape, partial = _duck_shape(speaking)
    shape = np.maximum(shape, np.clip(1 + DUCK_PARTIAL_DB / depth, 0, 1) * partial)
    gain_db = depth * shape
    times = (np.arange(count) + 0.5) * hop
    at = np.arange(len(bed))
    curve = 10 ** (np.interp(at, times, gain_db) / 20)
    ridden = bed * curve[:, None]
    if carved is not None:
        ridden += (carved - bed) * (curve * np.interp(at, times, shape))[:, None]
    held = np.lib.stride_tricks.sliding_window_view(np.pad(voice_db, SYLLABLE_HOLD, mode="edge"), 2 * SYLLABLE_HOLD + 1).max(axis=1)
    after = 10 * np.log10(np.convolve(bed_power * 10 ** (gain_db / 10), syllable)[SYLLABLE_FRAMES // 2 : SYLLABLE_FRAMES // 2 + count] + 1e-12)
    return ridden, float(np.median((held - after)[speaking]))


def _carved(bed: np.ndarray, folder: Path) -> np.ndarray:
    """The bed with a broad cut where consonants live, for `_ride` to blend in under speech."""
    source, shaped = folder / "bed.f32", folder / "carved.f32"
    bed.astype("<f4").tofile(source)
    raw = ["-f", "f32le", "-ar", str(RATE), "-ac", "2"]
    ffmpeg.run([*raw, "-i", str(source), "-af", f"equalizer=f={CARVE_HZ:g}:t=q:w={CARVE_Q:g}:g=-{CARVE_DB:g}", *raw, str(shaped)])
    carved = np.fromfile(shaped, dtype="<f4").astype(np.float64).reshape(-1, 2)
    if len(carved) != len(bed):
        raise AltocueError("The carved music came back a different length.", fix="Report this with the ffmpeg version from `altocue doctor`.")
    return carved


def _trimmed_music(path: Path) -> np.ndarray:
    """The file as stereo 48 kHz, up to its last sound, so an ending lands on music and not on padding."""
    samples = _read_stereo(path)
    hop = RATE // FRAMES_PER_SECOND
    level = _level_db(samples[: len(samples) // hop * hop].mean(axis=1), hop) if len(samples) >= hop else np.array([-180.0])
    heard = np.flatnonzero(level > MUSIC_SILENT_DB)
    if not len(heard):
        raise AltocueError(f"The music at {path} is silent.", fix="Check the file, or remove it from motion.json.")
    return samples[: (int(heard[-1]) + 1) * hop]


def _looped(track: np.ndarray, total: int) -> tuple[np.ndarray, int]:
    """Enough of the track to cover `total`, each pass blended into the next. Returns the passes too."""
    if len(track) >= total:
        return track, 1
    blend = min(round(MUSIC_LOOP_BLEND * RATE), len(track) // 4)
    step = len(track) - blend
    passes = (total - blend + step - 1) // step
    out = np.zeros((passes * step + blend, 2))
    rise, fall = _curve(blend, False)[:, None], _curve(blend, False)[::-1, None]
    for index in range(passes):
        piece = track.copy()
        if index:
            piece[:blend] *= rise
        if index < passes - 1:
            piece[-blend:] *= fall
        out[index * step : index * step + len(track)] += piece
    return out, passes


def _shaped(
    track: np.ndarray, total: int, end: str, tight: bool = False, start: int | None = None, enter: int = 0, *, shift: int | None = None, tail: int | None = None
) -> tuple[np.ndarray, int, int]:
    """The bed for a film of `total` samples, with its head and tail, and where it started in the track.

    `start` is a sample of the track to begin on, such as a downbeat. The film then opens on it with no fade in, and the bed fades out at the end.
    `enter` holds the track silent until that sample of the film, still running from `start`, so it comes in on a beat of its own grid.
    `shift`, for a "fade" ending, lays the top of the track that many samples into the film, or opens that far into the track when it is
    negative, so a bar line meets the last sample. The bed then fades over `tail` samples, ending on that bar line.
    """
    if shift is not None and start is None and end == "fade":
        bed = np.zeros((total, 2))
        begin, entry = max(-shift, 0), max(shift, 0)
        count = max(min(total - entry, len(track) - begin), 0)
        bed[entry : entry + count] = track[begin : begin + count]
        head = min(round((TIGHT_HEAD if tight else MUSIC_FADE_IN) * RATE), count // 4)
        fade = min(tail if tail is not None else round(MUSIC_FADE_OUT * RATE), count // 3)
        bed[entry : entry + head] *= _curve(head, False)[:, None]
        bed[total - fade :] *= _curve(fade, False)[::-1, None]
        return bed, begin, 1
    if start is not None:
        if not 0 <= start < len(track):
            raise AltocueError(f"The music starts {start / RATE:.2f} s in, and the track runs {len(track) / RATE:.2f} s.", fix="Start it inside the track.")
        stream, passes = _looped(track, start + total)
        bed = stream[start : start + total].copy()
        head, tail = min(round(MUSIC_GUARD * RATE), total // 4), min(round((TIGHT_TAIL if tight else MUSIC_FADE_OUT) * RATE), total // 3)
        bed[:head] *= _curve(head, False)[:, None]
        bed[total - tail :] *= _curve(tail, False)[::-1, None]
        if 0 < enter < total:
            bed[:enter] = 0.0
            guard = min(round(MUSIC_GUARD * RATE), total - enter)
            bed[enter : enter + guard] *= _curve(guard, False)[:, None]
        return bed, start, passes
    if end == "own":
        bed = np.zeros((total, 2))
        count = min(total, len(track))
        bed[:count] = track[:count]
        guard = min(round(MUSIC_GUARD * RATE), count // 2)
        bed[:guard] *= _curve(guard, False)[:, None]
        bed[count - guard : count] *= _curve(guard, False)[::-1, None]
        return bed, 0, 1
    stream, passes = _looped(track, total)
    start = len(stream) - total if end == "land" else 0  # backtimed, as an editor would
    bed = stream[start : start + total].copy()
    head = min(round((TIGHT_HEAD if tight else MUSIC_FADE_IN) * RATE), total // 4)
    tail = min(round((TIGHT_TAIL if tight else MUSIC_LAND_FADE if end == "land" else MUSIC_FADE_OUT) * RATE), total // 3)
    bed[:head] *= _curve(head, False)[:, None]
    bed[total - tail :] *= _curve(tail, False)[::-1, None]
    return bed, start, passes


def _bar_ending(found: dict[str, Any], length: int, total: int, end: str, start: int | None, tight: bool) -> tuple[int | None, int | None, dict[str, Any]]:
    """Where a "fade" ending can meet a bar line: the shift that puts one on the film's last sample, the fade, and the facts.

    Only a "fade" from the top of a track that covers the film can move. "land" already ends on the track's own
    ending, "own" ends itself, and `music_from` fixes where the track starts, so those keep their ending and say so.
    """
    if end != "fade" or start is not None:
        why = {"land": "the track's own ending is on the last sample", "own": "a score that ends itself"}.get(end, "music_from fixes where the track starts")
        return None, None, {"rule": end if start is None else "from", "moved": False, "why": why}
    if length < total:
        return None, None, {"rule": "fade", "moved": False, "why": "the track is shorter than the film, so it loops"}
    if not found["confident"]:
        return None, None, {"rule": "fade", "moved": False, "why": f"no beat grid to end on: {found.get('why', 'not confident')}"}
    seconds, beat = total / RATE, 60 / found["bpm"]
    choices = [("bar", found["downbeats"])] if found.get("bar_sure") else []
    for kind, lines in [*choices, ("beat", found["beats"])]:
        times = np.array(lines, dtype=np.float64)
        shifts = seconds - times  # how far into the film the top of the track goes. Negative opens that far into the track
        usable = (np.abs(shifts) <= BAR_REACH) & (times <= length / RATE)
        if tight:  # with no voice the music leads, so it opens the film, and opens a little further in rather than late
            usable &= shifts <= 0
        if usable.any():
            index = np.flatnonzero(usable)[np.argmin(np.abs(shifts[usable]))]
            fade = min(TIGHT_TAIL, beat) if tight else float(np.clip(beat, *BAR_END_FADE))
            facts = {"rule": kind, "moved": True, "shift": round(float(shifts[index]), 3), "track_at_end": round(float(times[index]), 3), "fade": round(fade, 3)}
            return round(float(shifts[index]) * RATE), round(fade * RATE), facts
    return None, None, {"rule": "fade", "moved": False, "why": f"no bar line or beat within {BAR_REACH:g} s of the end"}


def _music_bed(
    music: Path, target: float, total: int, end: str, folder: Path, tight: bool = False, start: float | None = None, enter: float = 0.0
) -> tuple[np.ndarray, dict[str, Any]]:
    """The track at `target` LUFS, fitted to the film, with its beat grid in film seconds."""
    if not music.exists():
        raise AltocueError(f"No music at {music}.", fix="Check the path in motion.json.")
    if end not in MUSIC_ENDS:
        raise AltocueError(f"Unknown music ending {end}.", fix=f"Use one of {', '.join(MUSIC_ENDS)}.")
    path = folder / "music.wav"
    ffmpeg.run(["-i", str(music), "-vn", "-ar", str(RATE), "-ac", "2", "-c:a", "pcm_s16le", str(path)])
    gain = target - ffmpeg.loudness(music)["integrated"]
    track = _trimmed_music(path)
    found = beats.grid(track, RATE)
    first = None if start is None else round(start * RATE)
    shift, tail, ending = _bar_ending(found, len(track), total, end, first, tight)
    bed, begin, passes = _shaped(track, total, end, tight, first, round(enter * RATE), shift=shift, tail=tail)
    seconds = total / RATE
    # Track seconds plus this offset are film seconds. A looped track has no one grid, and a grid not believed is not given.
    if passes > 1 or not found["confident"]:
        offset = None
    elif end == "land" and first is None:
        offset = seconds - len(track) / RATE
    else:
        offset = ((shift or 0) - begin if first is None else -begin) / RATE
    grid = {key: found[key] for key in ("bpm", "confidence", "confident", "beats_per_bar", "bar_strength", "bar_sure", "why") if found.get(key) is not None}
    placed: dict[str, list[float]] = {"beats": [], "downbeats": []}
    if offset is not None:
        placed = {key: [round(t + offset, 4) for t in found[key] if 0 <= t + offset < seconds] for key in placed}
        if found["downbeats"]:
            lines = np.array(found["downbeats"]) + offset
            ending["end_to_bar"] = round(float(seconds - lines[np.argmin(np.abs(lines - seconds))]), 3)  # 0 is a bar line on the last sample
    info = {"file": str(music), "gain_db": round(gain, 2), "end": end, "starts_at": round(begin / RATE, 3), "loops": passes}
    if shift and shift > 0:
        info["enters_at"] = round(shift / RATE, 3)
    return bed * 10 ** (gain / 20), {**info, "grid": grid, **placed, "ending": ending}


@lru_cache(maxsize=1)
def _sound_table() -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in json.loads((SFX_DIR / "sounds.json").read_text())}


def _placing(cue: dict[str, Any]) -> dict[str, Any]:
    """How this sound is placed: the table's word for a bundled sound, and the cue's over that."""
    row = _sound_table().get(cue.get("name", ""), {})
    found = {key: cue.get(key, row.get(key, default)) for key, default in (("align", "start"), ("offset_ms", 0), ("min_gap_ms", 0), ("dip_db", 0.0))}
    if found["align"] not in ALIGNS:
        raise AltocueError(f"Unknown alignment {found['align']}.", fix=f"Use one of {', '.join(ALIGNS)}.")
    sync = cue.get("sync", "free")
    if sync not in SYNCS:
        raise AltocueError(f"Unknown sync {sync}.", fix='Use "picture" for a sound that belongs to a change on screen, or "free".')
    return {**found, "sync": sync, "over_db": float(cue.get("over_db", row.get("bed_db", BED_OVER_DB)))}


def _key(cue: dict[str, Any]) -> str:
    return str(cue.get("name") or Path(str(cue.get("file"))).stem)


def _spaced(cues: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Cues in time order, less any sound repeated sooner than it should be."""
    kept, skipped = [], []
    last: dict[str, float] = {}
    for cue in sorted(cues, key=lambda row: float(row["at"])):
        name, at = _key(cue), float(cue["at"])
        if name in last and at - last[name] < float(_placing(cue)["min_gap_ms"]) / 1000:
            skipped.append({"name": name, "at": at, "why": "too soon after the last one"})
            continue
        last[name] = at
        kept.append(cue)
    return kept, skipped


def _loaded(cue: dict[str, Any], index: int, total: int, folder: Path) -> tuple[np.ndarray, Path, Path]:
    """The sound as mono samples, its wav, and where it came from."""
    path = Path(cue["file"]) if "file" in cue else sfx_path(cue["name"])
    if not path.exists():
        raise AltocueError(f"No effect at {path}.", fix="Check the path.")
    if not 0 <= round(float(cue["at"]) * RATE) < total:
        raise AltocueError(
            f"Effect {path.stem} at {float(cue['at']):.2f}s is outside the {total / RATE:.2f}s mix.",
            fix="Move it inside the film, or remove it.",
        )
    wav = ffmpeg.extract_wav(path, folder / f"effect{index}.wav")
    return ffmpeg.read_wav(wav)[0].astype(np.float64), wav, path


def marks(samples: np.ndarray) -> dict[str, int]:
    """Where a sound's onset, loudest sample, end and audible tail fall, as sample indices.

    The onset is the first sample within `ONSET_DB` of the peak and the end the last. The tail is the last
    sample within `TAIL_DB` of it. These are what `start`, `peak` and `end` alignment put on an event.
    """
    level = np.abs(np.asarray(samples, dtype=np.float64))
    if not len(level) or level.max() <= 0:
        return {"onset": 0, "peak": 0, "end": max(len(level) - 1, 0), "tail": max(len(level) - 1, 0)}
    loud = np.flatnonzero(level >= level.max() * 10 ** (-ONSET_DB / 20))
    heard = np.flatnonzero(level >= level.max() * 10 ** (-TAIL_DB / 20))
    return {"onset": int(loud[0]), "peak": int(np.argmax(level)), "end": int(loud[-1]), "tail": int(heard[-1])}


def measure(samples: np.ndarray, rate: int = RATE) -> dict[str, float]:
    """A sound's length and its marks in milliseconds, for the purpose index."""
    found = marks(samples)
    return {"seconds": round(len(samples) / rate, 3), **{f"{key}_ms": round(value / rate * 1000, 1) for key, value in found.items()}}


@dataclass(frozen=True)
class Speech:
    """Where words begin and spoken lines end, in film seconds. `tails` holds each line's last half second and the breath after it."""

    onsets: np.ndarray
    line_ends: np.ndarray
    tails: np.ndarray

    def zone(self, at: np.ndarray | float) -> np.ndarray:
        """For each time: 2 on a word's first sound, 1 in a line's tail, 0 in clear air."""
        at = np.atleast_1d(np.asarray(at, dtype=np.float64))
        onset = ((self.onsets[None, :] - ONSET_BEFORE <= at[:, None]) & (at[:, None] <= self.onsets[None, :] + ONSET_AFTER)).any(axis=1)
        tail = ((self.tails[None, :, 0] <= at[:, None]) & (at[:, None] <= self.tails[None, :, 1])).any(axis=1) if len(self.tails) else np.zeros(len(at), dtype=bool)
        return np.where(onset, 2, np.where(tail, 1, 0))


ZONES = ("clear", "line tail", "word onset")


def speech(words: Any) -> Speech | None:
    """Spoken lines from word timings in film seconds. A line ends at a pause of `LINE_GAP`, or at the end of a sentence."""
    rows = sorted((float(word["start"]), float(word["end"]), str(word.get("text", ""))) for word in words or () if float(word["end"]) > float(word["start"]))
    if not rows:
        return None
    ends, tails = [], []
    for index, (_, end, text) in enumerate(rows):
        following = rows[index + 1][0] if index + 1 < len(rows) else np.inf
        if following - end >= LINE_GAP or text.rstrip().endswith(SENTENCE_END):
            ends.append(end)
            tails.append((end - LINE_TAIL, min(end + LINE_BREATH, following - ONSET_BEFORE)))
    return Speech(np.array([row[0] for row in rows]), np.array(ends), np.array(tails, dtype=np.float64).reshape(-1, 2))


def _nearest(values: np.ndarray, at: float) -> float | None:
    """Signed seconds from the nearest of these to `at`: positive when `at` is after it."""
    return None if not len(values) else round(float(at - values[np.argmin(np.abs(values - at))]), 3)


def _repitched(samples: np.ndarray, cents: float) -> np.ndarray:
    """Played a little faster or slower, band-limited, so it is higher or lower and its peak moves with it."""
    factor = 2 ** (cents / 1200)
    padded = np.r_[samples, np.zeros(len(samples) // 8 + 64)]
    count = round(len(padded) / factor)
    return (np.fft.irfft(np.fft.rfft(padded), count) * count / len(padded))[: max(round(len(samples) / factor), 1)]


def _variation(cue: dict[str, Any], occurrence: int) -> dict[str, float] | None:
    """How the nth hearing of the same sound differs from the first. The same film always gets the same turns."""
    if occurrence < 1 or cue.get("vary", True) is False:
        return None
    turn = occurrence % len(VARY_GAIN_DB)
    tuned = _sound_table().get(cue.get("name", ""), {}).get("tuned", True)  # a file of unknown pitch keeps it
    return {"gain_db": VARY_GAIN_DB[turn], "cents": 0 if tuned else VARY_CENTS[turn]}


@dataclass
class Placed:
    """A sound ready to lay: its samples, where its first sample falls in the film, and what was done to it."""

    samples: np.ndarray
    start: int
    peak: int
    loudest_db: float  # of the whole sound, before any lead-in was lost
    trim_db: float
    facts: dict[str, Any]

    @property
    def hit(self) -> int:
        return self.start + self.peak


def _frame_of(seconds: float, fps: float) -> int:
    return int(np.floor(seconds * fps + EVENT_SLACK))


def _clear_nearby(said: Speech, hit: float, total: float) -> float | None:
    """The smallest move, earlier first on a tie, that puts a hit in clear air inside the film. None if there is none."""
    steps = np.arange(-NUDGE_MOST, NUDGE_MOST + NUDGE_STEP / 2, NUDGE_STEP)
    steps = steps[np.lexsort((steps > 0, np.abs(steps)))]
    times = hit + steps
    usable = (said.zone(times) == 0) & (times >= 0) & (times < total)
    return float(steps[np.argmax(usable)]) if usable.any() else None


def _shortened(samples: np.ndarray, start: int, hit: int, said: Speech) -> np.ndarray | None:
    """The sound cut off before the next word it would run over, with a short fade. None if it never reaches one."""
    tail = start + marks(samples)["tail"]
    for onset in said.onsets:
        stop = round((onset - ONSET_BEFORE) * RATE)
        if stop - hit >= round(SHORTEN_LEAST * RATE) and stop < tail:
            keep = stop - start
            out = samples[:keep].copy()
            fade = min(round(SHORTEN_FADE * RATE), keep)
            out[keep - fade :] *= _curve(fade, False)[::-1]
            return out
    return None


def _place(
    samples: np.ndarray, cue: dict[str, Any], place: dict[str, Any], total: int, *, fps: float | None = None, said: Speech | None = None, occurrence: int = 0
) -> Placed:
    """Put a sound on its event, then keep it off the words.

    A `picture` sound belongs to a change on screen. With `fps` its event moves to the frame the picture shows
    it on, never more than a frame, and on a word it keeps that frame: it comes down `UNDER_WORD_DB`, and is
    cut short before the next word it would run over. A `free` sound moves up to `NUDGE_MOST` into clear air,
    and only comes down when there is none that close.
    """
    varied = _variation(cue, occurrence)
    if varied and varied["cents"]:
        samples = _repitched(samples, varied["cents"])
    found = marks(samples)
    align, sync = place["align"], place["sync"]
    at, offset = float(cue["at"]), int(place["offset_ms"]) / 1000
    event_frame = int(np.ceil(at * fps - EVENT_SLACK)) if fps else None
    event = event_frame / fps if fps and event_frame is not None and sync == "picture" else at
    anchor = {"start": found["onset"], "peak": found["peak"], "end": found["end"]}[align]
    start = round((event + offset) * RATE) - anchor
    trim_db = varied["gain_db"] if varied else 0.0
    zone, action, cut = None, "unchecked", None
    if said is not None:
        asked_hit = (start + found["peak"]) / RATE
        zone = ZONES[int(said.zone(asked_hit)[0])]
        action = "kept"
        if zone != "clear" and sync == "free":
            move = _clear_nearby(said, asked_hit, total / RATE)
            if move is not None:
                start += round(move * RATE)
                action = "nudged"
        if zone != "clear" and action != "nudged":
            trim_db -= UNDER_WORD_DB
            cut = _shortened(samples, start, start + found["peak"], said)
            action = "ducked and shortened" if cut is not None else "ducked"
            samples = samples if cut is None else cut
    hit = (start + found["peak"]) / RATE
    landed = (start + anchor) / RATE
    facts: dict[str, Any] = {
        "align": align,
        "sync": sync,
        "asked_at": round(at + offset, 4),
        "landed_at": round(landed, 4),
        "moved_ms": round((landed - at - offset) * 1000, 1),
        "peak_at": round(hit, 4),
        "event_frame": event_frame,
        "landed_frame": _frame_of(landed, fps) if fps else None,
        "zone": zone,
        "action": action,
        "to_word_onset": _nearest(said.onsets, hit) if said else None,
        "to_line_end": _nearest(said.line_ends, hit) if said else None,
        "under_word_db": UNDER_WORD_DB if action.startswith("ducked") else 0.0,
        "shortened_to": round(len(cut) / RATE, 3) if cut is not None else None,
        "variation": varied,
    }
    loudest = float(20 * np.log10(np.abs(samples).max(initial=0.0) + 1e-9))
    return Placed(samples[max(-start, 0) :], max(start, 0), found["peak"] - max(-start, 0), loudest, trim_db, facts)


def _effect(
    cue: dict[str, Any], index: int, level: float, total: int, folder: Path, *, fps: float | None = None, said: Speech | None = None, occurrence: int = 0
) -> tuple[np.ndarray, int, dict[str, Any]]:
    """A sound levelled against the voice, then kept off the words."""
    under = float(cue.get("under_db", SFX_UNDER_DB))
    if not SFX_UNDER_RANGE[0] <= under <= SFX_UNDER_RANGE[1]:
        raise AltocueError(f"Effect level {under:g} dB under the voice is out of range.", fix="Use 8 to 12 dB under the voice.")
    samples, wav, path = _loaded(cue, index, total, folder)
    placed = _place(samples, cue, _placing(cue), total, fps=fps, said=said, occurrence=occurrence)
    gain = level - under - max_momentary(wav)
    ceiling = level + SFX_PEAK_OVER_VOICE_DB - placed.loudest_db
    used = min(gain, ceiling)
    return (
        placed.samples * 10 ** ((used + placed.trim_db) / 20),
        placed.start,
        {
            "name": cue.get("name", path.stem),
            "at": float(cue["at"]),
            "under_db": under,
            "gain_db": round(used + placed.trim_db, 2),
            "capped": ceiling < gain,
            **placed.facts,
        },
    )


def _lufs_like(bed: np.ndarray) -> float:
    """Stereo power on the LUFS scale, with no K-weighting. Within a dB of `ebur128` on a steady bed."""
    return float(10 * np.log10(np.sum(np.mean(bed**2, axis=0)) + 1e-12) - 0.691)


def _level_at(bed: np.ndarray, hit: int) -> float | None:
    """The music's level in the moment before a hit, or None if there is no music there."""
    first = max(hit - round(BED_WINDOW * RATE), 0)
    if hit <= first:
        return None
    level = _lufs_like(bed[first:hit])
    return level if level > -90 else None


def _hit(
    cue: dict[str, Any], index: int, bed: np.ndarray, overall: float, total: int, folder: Path, *, fps: float | None = None, occurrence: int = 0
) -> tuple[np.ndarray, int, int, float, dict[str, Any]]:
    """A sound levelled against the music in the moment it lands. Returns it, its start and hit, and any dip."""
    place = _placing(cue)
    if "under_db" in cue:
        raise AltocueError("under_db sets an effect against a voice, and this film has none.", fix="Use over_db, for how far over the music it sits.")
    if not BED_OVER_RANGE[0] <= place["over_db"] <= BED_OVER_RANGE[1]:
        low, high = BED_OVER_RANGE
        raise AltocueError(f"Effect level {place['over_db']:g} dB over the music is out of range.", fix=f"Use {low:g} to {high:g} dB over the music.")
    dip = float(place["dip_db"])
    if dip and not DIP_RANGE[0] <= dip <= DIP_RANGE[1]:
        raise AltocueError(f"A dip of {dip:g} dB is out of range.", fix=f"Use {DIP_RANGE[0]:g} to {DIP_RANGE[1]:g} dB, or 0 for no dip.")
    samples, _, path = _loaded(cue, index, total, folder)
    placed = _place(samples, cue, place, total, fps=fps, occurrence=occurrence)
    local = _level_at(bed, min(placed.hit, total))
    against = overall if local is None else max(local, overall - BED_QUIET_DB)
    peak = placed.loudest_db
    gain = against + place["over_db"] - peak
    ceiling = HIT_PEAK_DB - peak
    used = min(gain, ceiling)
    entry = {
        "name": cue.get("name", path.stem),
        "at": float(cue["at"]),
        "over_bed_db": round(peak + used - against, 1),
        "gain_db": round(used + placed.trim_db, 2),
        "capped": ceiling < gain,
        "dip_db": dip,
        **placed.facts,
    }
    return placed.samples * 10 ** ((used + placed.trim_db) / 20), placed.start, placed.hit, dip, entry


def _dipped(bed: np.ndarray, dips: list[tuple[int, float]]) -> np.ndarray:
    """Draw the music down before a big hit, and bring it straight back on the hit."""
    if not dips:
        return bed
    gain_db = np.zeros(len(bed))
    back = round(DIP_BACK * RATE)
    for hit, depth in dips:
        first, last = max(hit - round(DIP_SECONDS * RATE), 0), min(hit + back, len(bed))
        at = np.arange(first, last)
        down = _raised((at - first) / max((hit - first) // 2, 1))
        up = 1 - _raised((at - hit) / back)
        gain_db[first:last] = np.minimum(gain_db[first:last], -depth * np.where(at < hit, down, up))
    return bed * 10 ** (gain_db / 20)[:, None]


def _no_voice(
    music: Path | None,
    cues: list[dict[str, Any]],
    total: int,
    end: str,
    folder: Path,
    layers: dict[str, np.ndarray] | None = None,
    music_from: float | None = None,
    music_enter: float = 0.0,
    *,
    fps: float | None = None,
    turns: list[int] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """The music leads. Effects are levelled against it, and a big hit gets a dip in front of it."""
    bed, info = _music_bed(music, BED_LUFS, total, end, folder, tight=True, start=music_from, enter=music_enter) if music else (np.zeros((total, 2)), None)
    overall = _lufs_like(bed) if music else BED_LUFS
    turns = turns or [0] * len(cues)
    hits = [_hit(cue, index, bed, overall, total, folder, fps=fps, occurrence=turns[index]) for index, cue in enumerate(cues)]
    out = _dipped(bed, [(hit, dip) for _, _, hit, dip, _ in hits if dip])
    if layers is not None:
        layers["music"] = out.copy()
    for placed, start, _, _, entry in hits:
        entry["cut_at_end"] = _lay(out, placed, start, total)
    return out, {"mode": "no voice", "voice_lufs": None, "bed_lufs": BED_LUFS, "music": info, "sfx": [entry for *_, entry in hits]}


def _lay(out: np.ndarray, placed: np.ndarray, start: int, total: int) -> bool:
    """Add a sound into the mix. What runs past the end is cut off, and a sound an offset pushed past the end is dropped."""
    stop = min(start + len(placed), total)
    if stop > start:
        out[start:stop] += placed[: stop - start, None]
    return start + len(placed) > total


def _room_tail(room: Path, start: int, total: int) -> np.ndarray | None:
    """The room carried on past the voice, picking up as the voice fades and fading out at the end."""
    if not room.exists() or total <= start:
        return None
    loop, _ = ffmpeg.read_wav(room)
    count = total - start
    bed = loop[np.arange(start, total) % len(loop)].astype(np.float64)
    fade = min(round(ROOM_TAIL_FADE * RATE), count)
    bed[-fade:] *= _curve(fade, False)[::-1]
    pick_up = min(round(FADE_MS / 1000 * RATE), count)
    bed[:pick_up] *= _curve(pick_up, False)
    return bed


def mix_bed(
    voice: str | Path | None,
    dest: str | Path,
    music: str | Path | None = None,
    sfx: Any = (),
    tail: float = 0.0,
    room: str | Path | None = None,
    music_end: str = "land",
    seconds: float | None = None,
    stems: str | Path | None = None,
    music_from: float | None = None,
    music_enter: float = 0.0,
    music_under: float = DUCK_UNDER_VOICE_DB,
    end_fade: float = 0.0,
    fps: float | None = None,
    words: Any = None,
) -> Path:
    """Mix voice, music and effects to stereo 48 kHz, running `tail` seconds past the picture.

    With a voice, music ducks under it and effects sit 8 to 12 dB under it. `voice=None`, with
    `seconds` for the film's length, or a voice track of pure silence, is a film with no voice:
    the music leads at `BED_LUFS`, fades are tight, and effects are levelled against the music.

    Each entry in `sfx` is `{"name": "pop", "at": 1.2}` or `{"file": path, "at": 1.2}`, plus
    `align` ("start", "peak" or "end"), `offset_ms` and `min_gap_ms`, which default to the sound's row in
    `sounds.json`. With a voice a cue can set `under_db`. With none it can set `over_db` and `dip_db`.
    `sync` is "picture" for a sound that belongs to a change on screen, which with `fps` lands on that
    frame and keeps it, or "free", the default, for one that may move into clear air. `words`, each with a
    `start` and `end` in film seconds, keeps effects off the first sound of a word and the tail of a line.
    `vary: false` keeps a repeated sound the same every time.
    `room` is the loop from `mix_voice`, which fills the tail. `music_end` is "land", which
    backtimes the track so its own end falls on the film's last sample, "fade", which starts at the
    top of the track and fades out, or "own", for a score that ends itself. The numbers land in a
    `.json` beside the mix. With `stems`, that folder gets each of `STEMS` as a WAV file at its level
    in this mix, and they add up to it. `music_from` starts the track that many seconds in, on a downbeat
    chosen by ear or by its beat grid, and the bed fades out at the film's end. With `music_from`, `music_enter` holds
    the track silent for that many seconds of the film, so it comes in on a later beat of the same grid.
    `music_under` is how many dB the music sits under the voice while someone speaks. `end_fade` fades the
    voice and its room over that many seconds at the end of a film that has no tail, so it does not stop dead.
    """
    dest = Path(dest)
    low, high = DUCK_UNDER_RANGE
    if isinstance(music_under, bool) or not isinstance(music_under, (int, float)) or not low <= music_under <= high:
        fix = f"Set `[audio] music_under` from {low:g} to {high:g}. The default is {DUCK_UNDER_VOICE_DB:g}."
        raise AltocueError(f"Music cannot sit {music_under} dB under the voice.", fix=fix)
    if voice is None:
        if seconds is None:
            raise AltocueError("A mix with no voice has nothing to set its length.", fix="Pass seconds, the film's length.")
        samples, length = np.zeros(0), round(seconds * RATE)
    else:
        voice = Path(voice)
        if not voice.exists():
            raise AltocueError(f"No voice track at {voice}.", fix="Run `altocue plate`.")
        loaded, _ = ffmpeg.read_wav(voice)
        samples, length = loaded.astype(np.float64), len(loaded)
    total = length + round(tail * RATE)
    cues, skipped = _spaced(sfx)
    heard: Counter[str] = Counter()
    turns = []
    for cue in cues:  # how many times each sound has been heard before this one
        turns.append(heard[_key(cue)])
        heard[_key(cue)] += 1
    layers: dict[str, np.ndarray] | None = {} if stems else None
    silent = voice is None or np.abs(samples).max(initial=0.0) < SILENT_VOICE
    said = None if silent else speech(words)
    with tempfile.TemporaryDirectory(prefix="altocue-") as scratch:
        folder = Path(scratch)
        if voice is None or silent:
            out, report = _no_voice(Path(music) if music else None, cues, total, music_end, folder, layers, music_from, music_enter, fps=fps, turns=turns)
        else:
            room_path = Path(room) if room else None
            out, report = _with_voice(
                Path(voice), samples, Path(music) if music else None, cues, total, tail, room_path, music_end, folder, layers, music_from, music_enter,
                under=float(music_under), end_fade=end_fade, fps=fps, said=said, turns=turns,
            )
    if cues:
        report["placing"] = _placing_report(fps, said, silent)
    peak = float(np.abs(out).max())
    if stems:
        _write_stems(Path(stems), out, layers or {}, PEAK_LIMIT / peak if peak > PEAK_LIMIT else 1.0)
    if peak > PEAK_LIMIT:
        out *= PEAK_LIMIT / peak
    report["skipped"] = skipped
    report["trim_db"] = round(20 * np.log10(PEAK_LIMIT / peak), 2) if peak > PEAK_LIMIT else 0.0
    _write_stereo(dest, out)
    write_json(dest.with_suffix(".json"), report)
    return dest


def _placing_report(fps: float | None, said: Speech | None, silent: bool) -> dict[str, Any]:
    """The rule every effect in the report was placed by, so a gate or an editor can read the facts against it."""
    if silent:
        words = "no voice"
    elif said is None:
        words = "not given, so effects were not checked against speech"
    else:
        words = {"onsets": len(said.onsets), "lines": len(said.line_ends)}
    return {
        "fps": fps,
        "words": words,
        "onset_window": [-ONSET_BEFORE, ONSET_AFTER],
        "line_tail": LINE_TAIL,
        "line_breath": LINE_BREATH,
        "nudge_most": NUDGE_MOST,
        "under_word_db": UNDER_WORD_DB,
        "listen": "Placed by measurement. Nobody has listened to these.",
    }


def _write_stems(folder: Path, mixed: np.ndarray, layers: dict[str, np.ndarray], trim: float) -> None:
    """Voice and music as they went in, and effects as all that is left, so the three always add up to the mix."""
    silent = np.zeros_like(mixed)
    voice, music = layers.get("voice", silent), layers.get("music", silent)
    for name, part in zip(STEMS, (voice, music, mixed - voice - music), strict=True):
        _write_stereo(folder / f"{name}.wav", part * trim)


def _with_voice(
    voice: Path,
    samples: np.ndarray,
    music: Path | None,
    cues: list[dict[str, Any]],
    total: int,
    tail: float,
    room: Path | None,
    end: str,
    folder: Path,
    layers: dict[str, np.ndarray] | None = None,
    music_from: float | None = None,
    music_enter: float = 0.0,
    *,
    under: float = DUCK_UNDER_VOICE_DB,
    end_fade: float = 0.0,
    fps: float | None = None,
    said: Speech | None = None,
    turns: list[int] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """The voice leads. Music ducks under it, and effects are levelled against it and kept off its words."""
    level = _voice_level(voice)
    out = np.zeros((total, 2))
    out[: len(samples)] = samples[:, None]
    fade = 0 if tail else min(round(end_fade * RATE), len(samples))
    if fade > 0:
        out[len(samples) - fade : len(samples)] *= _curve(fade, False)[::-1, None]
    if room and tail:
        start = max(len(samples) - round(FADE_MS / 1000 * RATE) // 2, 0)
        bed = _room_tail(room, start, total)
        if bed is not None:
            out[start:] += bed[:, None]
    if layers is not None:
        layers["voice"] = out.copy()
    report: dict[str, Any] = {"mode": "voice", "voice_lufs": level, "music": None, "sfx": []}
    if music:
        bed, report["music"] = _music_bed(music, level - MUSIC_UNDER_VOICE_LU, total, end, folder, start=music_from, enter=music_enter)
        ridden, measured = _ride(bed, samples, level - RIDER_GATE_BELOW_VOICE_DB, under, _carved(bed, folder))
        report["music"]["under_voice_db"] = round(measured, 1)
        report["music"]["under_target_db"] = under
        out += ridden
        if layers is not None:
            layers["music"] = ridden
    turns = turns or [0] * len(cues)
    for index, cue in enumerate(cues):
        placed, start, entry = _effect(cue, index, level, total, folder, fps=fps, said=said, occurrence=turns[index])
        report["sfx"].append({**entry, "cut_at_end": _lay(out, placed, start, total)})
    return out, report
