"""
Beatmap generator for the AR Beat Saber stall build.

Pipeline
--------
1. ffmpeg decodes the mp3 to mono 22.05 kHz 16-bit PCM.
2. A log-magnitude STFT feeds a half-wave-rectified spectral-flux onset
   envelope, median-filtered so loud sections don't drown out quiet ones.
3. Autocorrelation with a log-normal tempo prior gives a seed BPM.
4. A dynamic-programming beat tracker (Ellis 2007) walks the onset envelope
   and returns per-beat times. Unlike a fixed grid this follows tempo drift,
   which matters over a 4-minute track -- a constant grid accumulates
   hundreds of milliseconds of error by the outro.
5. Beats are gated by local energy (skips the quiet intro), then assigned
   lane / row / direction under playability rules: left hand owns lanes 0-1,
   right owns 2-3, hands alternate, and cut direction never fights the row.

Output: beatmap.json next to index.html.

Run:  python tools/analyze_beats.py
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MP3 = ROOT / "audio" / "eye-of-the-tiger.mp3"
OUT = ROOT / "beatmap.json"

SR = 22050
HOP = 256
NFFT = 1024


def decode(path):
    """mp3 -> mono float32 PCM at SR."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(SR), "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def spectrogram(y):
    win = np.hanning(NFFT).astype(np.float32)
    n_frames = 1 + (len(y) - NFFT) // HOP
    frames = np.lib.stride_tricks.as_strided(
        y, shape=(n_frames, NFFT), strides=(y.strides[0] * HOP, y.strides[0])
    )
    return np.log1p(np.abs(np.fft.rfft(frames * win, axis=1)) * 20.0)


def _flux_to_env(flux):
    """Half-wave rectify, flatten with a local median, normalise to 0-1."""
    flux = np.concatenate([[0.0], flux])
    w = 40
    pad = np.pad(flux, (w, w), mode="edge")
    local = np.array([np.median(pad[i:i + 2 * w + 1]) for i in range(len(flux))])
    env = np.maximum(flux - local, 0.0)
    if env.max() > 0:
        env /= env.max()
    return env


def onset_envelope(y, band=None):
    """Spectral-flux onset strength.

    band=None uses the full spectrum, which is what note placement wants --
    it fires on guitar stabs and snares as readily as on the kick.

    band=(lo, hi) in Hz restricts the flux to those bins. Beat *tracking* uses
    a low band, because in this track the kick drum is what actually defines
    the pulse; the full-band envelope is dense with guitar and vocal onsets
    that pull the grid off the beat.
    """
    logspec = spectrogram(y)
    if band is not None:
        binw = SR / NFFT
        lo, hi = int(band[0] / binw), int(np.ceil(band[1] / binw))
        logspec = logspec[:, lo:hi + 1]
    return _flux_to_env(np.maximum(np.diff(logspec, axis=0), 0.0).sum(axis=1))


def peak_times(env, fps, thresh=0.05):
    """Local maxima of the onset envelope, in seconds."""
    prev, nxt = env[:-2], env[2:]
    mid = env[1:-1]
    idx = np.nonzero((mid > thresh) & (mid >= prev) & (mid > nxt))[0] + 1
    return idx / fps


def snap_to_onsets(times, onsets, tol):
    """Pull each grid time onto the nearest real onset within tol seconds.

    The DP tracker gives an even pulse, but a live-feel recording pushes and
    pulls around it. Snapping means a block arrives when the player actually
    hears the hit, not when a constant grid says it should.
    """
    if not len(onsets):
        return times, 0.0
    out, moved = [], []
    for t in times:
        j = int(np.argmin(np.abs(onsets - t)))
        d = onsets[j] - t
        if abs(d) <= tol:
            out.append(onsets[j])
            moved.append(abs(d))
        else:
            out.append(t)
    return np.array(out), (float(np.mean(moved)) if moved else 0.0)


def seed_tempo(env, fps):
    ac = np.correlate(env - env.mean(), env - env.mean(), mode="full")
    ac = ac[len(ac) // 2:]
    ac[0] = 0.0

    bpms = np.arange(60.0, 200.5, 0.1)
    idx = np.clip(np.round((60.0 / bpms) * fps).astype(int), 1, len(ac) - 1)
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    return float(bpms[int(np.argmax(ac[idx] * prior))])


def track_beats(env, fps, bpm, tightness=100.0):
    """Ellis dynamic-programming beat tracker. Returns beat times in seconds."""
    period = 60.0 / bpm * fps
    n = len(env)
    cumscore = env.copy()
    backlink = np.full(n, -1, dtype=int)

    # Candidate predecessors sit between half and double the target period.
    window = np.arange(-int(round(2 * period)), -int(round(period / 2)) + 1)
    txcost = -tightness * (np.log(-window / period) ** 2)

    for t in range(n):
        idx = t + window
        valid = idx >= 0
        if not valid.any():
            continue
        scores = cumscore[idx[valid]] + txcost[valid]
        b = int(np.argmax(scores))
        cumscore[t] = scores[b] + env[t]
        backlink[t] = idx[valid][b]

    start = int(0.9 * n) + int(np.argmax(cumscore[int(0.9 * n):]))
    beats = [start]
    while backlink[beats[-1]] >= 0:
        beats.append(backlink[beats[-1]])
    return np.array(beats[::-1], dtype=float) / fps


def rms_profile(y, beats):
    """Local loudness at each beat, normalised against the track median."""
    half = int(SR * 0.5)
    med = np.sqrt(np.mean(y ** 2))
    out = []
    for t in beats:
        c = int(t * SR)
        seg = y[max(0, c - half):c + half]
        out.append(float(np.sqrt(np.mean(seg ** 2)) / med) if len(seg) else 0.0)
    return np.array(out)


def build_grid(beats):
    """Half-beat grid interpolated between tracked beats.

    Notes on the beat carry the map; the offbeat slots are what make a run of
    blocks possible when the music is busy enough to justify one.
    """
    times, on_beat = [], []
    for a, b in zip(beats, beats[1:]):
        times.append(a);            on_beat.append(True)
        times.append((a + b) / 2);  on_beat.append(False)
    times.append(beats[-1]);        on_beat.append(True)
    return np.array(times), np.array(on_beat)


def build_notes(beats, env, fps, y, beat_len):
    """Place notes on a half-beat grid and assign lane / row / direction."""
    times, on_beat = build_grid(beats)
    loud = rms_profile(y, times)

    def strength(t):
        i = int(round(t * fps))
        lo, hi = max(0, i - 3), min(len(env), i + 4)
        return float(env[lo:hi].max()) if hi > lo else 0.0

    strengths = np.array([strength(t) for t in times])

    # ---- decide which grid slots carry a note ----
    place = np.zeros(len(times), dtype=bool)
    for i, t in enumerate(times):
        if loud[i] < 0.45:
            continue                       # quiet intro / breakdown
        if on_beat[i]:
            beat_index = i // 2
            if loud[i] >= 0.55:
                place[i] = True            # busy section: every beat
            else:
                place[i] = beat_index % 2 == 0   # calmer: every other beat
        else:
            # STRICT BEAT SYNC: Never place targets on offbeats.
            place[i] = False

    # ---- assign lane / row / direction ----
    notes = []
    hand = 0
    step = 0
    prev_dir = None
    run = 0            # how many consecutive grid slots are filled
    run_lane = 0
    run_row = 1
    run_dir = "DOWN"

    for i, t in enumerate(times):
        if not place[i]:
            run = 0
            continue

        in_run = run >= 1 and place[i - 1]

        if in_run:
            # Inside a run, hold the row and direction and march the lane
            # across so the blocks read as one line sweeping the highway.
            run += 1
            d = run_dir
            row = run_row
            lane = run_lane + (1 if hand == 0 else -1) * 0  # placeholder
            # step outward one lane per note, bouncing at the edges
            lane = run_lane + (1 if (run % 2) else -1)
            lane = max(0, min(3, lane))
        else:
            run = 1
            if prev_dir == "DOWN":
                d = "UP"
            elif prev_dir == "UP":
                d = "DOWN"
            else:
                d = "DOWN"
            if strengths[i] > 0.4 and step % 4 == 3:
                d = "LEFT" if (step // 4) % 2 == 0 else "RIGHT"

            if hand == 0:
                lane = 0 if (step // 2) % 2 == 0 else 1
            else:
                lane = 3 if (step // 2) % 2 == 0 else 2

            if d == "UP":
                row = 2
            elif d == "DOWN":
                row = 0 if strengths[i] > 0.28 else 1
            else:
                row = 1

            run_dir, run_row, run_lane = d, row, lane

        notes.append({"time": round(float(t), 3), "lane": int(lane),
                      "row": int(row), "dir": d, "type": "target"})
        run_lane = lane
        prev_dir = d if d in ("UP", "DOWN") else prev_dir
        hand ^= 1
        step += 1

    notes = drop_orphans(notes, beat_len)
    notes = add_column_drops(notes, times, strengths, loud)
    notes = add_stacks(notes, times, strengths, loud, beat_len)
    notes = enforce_spacing(notes, beat_len)
    return notes


def add_column_drops(notes, times, strengths, loud, spacing=3.0):
    """
    On huge beat drops, add a secondary note in the same column (lane)
    but a different row, keeping the same direction so they can be swept together.
    """
    by_time = {}
    for n in notes:
        by_time.setdefault(round(n["time"], 3), []).append(n)

    extra, last = [], -1e9
    for n in notes:
        if n["type"] != "target":
            continue
        t = round(n["time"], 3)
        if len(by_time[t]) > 1 or t - last < spacing:
            continue
        i = int(np.argmin(np.abs(times - n["time"])))
        if strengths[i] < 0.6 or loud[i] < 1.2:
            continue
        
        # Drop a block in the same lane, but a different row
        new_row = 1 if n["row"] != 1 else (0 if n["dir"] == "DOWN" else 2)
        extra.append({"time": n["time"], "lane": n["lane"], "row": new_row,
                      "dir": n["dir"], "type": "target"})
        last = t

    notes.extend(extra)
    notes.sort(key=lambda n: (n["time"], n["lane"]))
    return notes


def add_lines(notes, beats, y, env, fps, spacing=7.0, limit=18):
    """Turn the loudest bars into a line of blocks at half-beat spacing.

    Thresholding the offbeats individually does not work here -- the track is
    close to four-on-the-floor, so offbeat onset strength sits far below the
    downbeats (median 0.04 against 0.22) and any threshold either catches
    nothing or scatters lone blocks that never read as a run. Instead the
    loudest bars are chosen outright and rewritten as a deliberate stream.

    Within a line the row is held constant and the lanes cycle 0-2-1-3, so
    hands still alternate (each hand gets one block per beat, not per half
    beat) while the blocks sweep across the highway as a single figure.
    Direction flips every two blocks, which gives each hand a clean
    down-up-down-up reset.
    """
    bar_starts = list(range(0, len(beats) - 4, 4))
    if not bar_starts:
        return notes

    def strength(t):
        i = int(round(t * fps))
        return float(env[max(0, i - 3):i + 4].max())

    scored = []
    for i in bar_starts:
        bar = beats[i:i + 5]
        loud = rms_profile(y, bar[:4])
        s = np.mean([strength(t) for t in bar[:4]])
        scored.append((float(np.mean(loud)) + s, i))
    scored.sort(reverse=True)

    chosen, last = [], []
    for score, i in scored:
        t0 = beats[i]
        if any(abs(t0 - t) < spacing for t in last):
            continue
        chosen.append(i)
        last.append(t0)
        if len(chosen) >= limit:
            break

    LANES = [0, 2, 1, 3]
    for i in chosen:
        bar = beats[i:i + 5]
        t0, t1 = bar[0], bar[4]
        notes = [n for n in notes if not (t0 - 1e-6 <= n["time"] < t1 - 1e-6)]

        slots = []
        for k in range(4):
            slots.append(bar[k])
            slots.append((bar[k] + bar[k + 1]) / 2)

        for j, t in enumerate(slots):
            notes.append({
                "time": round(float(t), 3),
                "lane": LANES[j % 4],
                "row": 1,
                "dir": "DOWN" if (j // 2) % 2 == 0 else "UP",
                "type": "target",
                "line": True,
            })

    notes.sort(key=lambda n: (n["time"], n["lane"]))
    return notes


def add_stacks(notes, times, strengths, loud, beat_len, spacing=6.0):
    """Mirror the biggest downbeats into a two-block stack, one per hand.

    Both blocks share a timestamp and sit on opposite sides, so the player
    hits them with both hands at once -- the accent moments of the track.
    """
    by_time = {}
    for n in notes:
        by_time.setdefault(round(n["time"], 3), []).append(n)

    extra, last = [], -1e9
    for n in notes:
        if n["type"] != "target":
            continue
        t = round(n["time"], 3)
        if len(by_time[t]) > 1 or t - last < spacing:
            continue
        i = int(np.argmin(np.abs(times - n["time"])))
        if strengths[i] < 0.45 or loud[i] < 1.0:
            continue
        mirror = 3 - n["lane"]
        extra.append({"time": n["time"], "lane": mirror, "row": n["row"],
                      "dir": n["dir"], "type": "target"})
        last = t

    notes.extend(extra)
    notes.sort(key=lambda n: (n["time"], n["lane"]))
    return notes


def enforce_spacing(notes, beat_len, min_gap=0.22, min_hand_gap=0.42):
    """Drop notes that arrive faster than a person can physically swing.

    Two limits: an overall floor, and a per-hand floor. Alternating hands is
    what makes fast runs playable at all, so the per-hand gap is the one that
    actually decides whether a pattern is fair.
    """
    kept, last_any, last_h = [], -1e9, None
    last_hand = {0: -1e9, 1: -1e9}
    for n in sorted(notes, key=lambda x: (x["time"], x["lane"])):
        t = n["time"]
        h = 0 if n["lane"] < 2 else 1
        stacked = abs(t - last_any) < 1e-6      # deliberate same-time pair

        if stacked:
            # A simultaneous pair is only playable as one block per hand, and
            # the partner still has to respect that hand's own recovery time.
            if h == last_h:
                continue
        elif t - last_any < min_gap:
            continue
        if t - last_hand[h] < min_hand_gap:
            continue

        kept.append(n)
        last_any = t
        last_h = h
        last_hand[h] = t
    return kept


def drop_orphans(notes, beat_len, min_phrase=4):
    """Drop short isolated runs of notes.

    The loudness gate lets a couple of stragglers through during the intro,
    which read as random blocks arriving out of nowhere. Grouping into
    phrases (consecutive notes no more than 3 beats apart) and discarding
    the short ones removes them -- checking each note for a neighbour is not
    enough, since the stragglers come in pairs and vouch for each other.
    """
    if not notes:
        return notes

    phrases, cur = [], [notes[0]]
    for prev, n in zip(notes, notes[1:]):
        if n["time"] - prev["time"] <= beat_len * 3:
            cur.append(n)
        else:
            phrases.append(cur)
            cur = [n]
    phrases.append(cur)

    return [n for p in phrases if len(p) >= min_phrase for n in p]


def add_bombs(notes, beat_len, spacing=12.0, limit=14, min_gap_beats=1.4):
    """Sprinkle bombs into inner lanes between two outer-lane notes.

    A bomb is only fair if the player is not already swinging through that
    space, so both neighbouring notes must sit in the outer lanes and the
    gap must be wide enough to react in. Bombs are also spaced out across
    the track -- at a stall a bomb should be an occasional surprise, not a
    recurring punishment.
    """
    targets = [n for n in notes if n["type"] == "target"]
    bombs = []
    last = -1e9

    for a, b in zip(targets, targets[1:]):
        gap = b["time"] - a["time"]
        if gap < beat_len * min_gap_beats:
            continue
        mid = (a["time"] + b["time"]) / 2.0
        if mid - last < spacing:
            continue
        # Sit as far as possible from both neighbouring notes, and never in
        # either of their lanes -- lanes are 160 px apart against a 52 px bomb
        # radius, so one lane of clearance already keeps the bomb out of the
        # path the player is swinging through.
        lane = max(range(4),
                   key=lambda ln: min(abs(ln - a["lane"]), abs(ln - b["lane"])))
        if min(abs(lane - a["lane"]), abs(lane - b["lane"])) < 1:
            continue
        bombs.append({"time": round(mid, 3), "lane": lane,
                      "row": 1, "type": "bomb"})
        last = mid
        if len(bombs) >= limit:
            break

    notes.extend(bombs)
    notes.sort(key=lambda n: (n["time"], n["lane"]))
    return len(bombs)


def main():
    if not MP3.exists():
        sys.exit(f"missing audio: {MP3}")

    y = decode(MP3)
    duration = len(y) / SR
    fps = SR / HOP

    # Two envelopes: a low-band one to find the pulse, a full-band one to
    # decide where the notes actually go.
    env_beat = onset_envelope(y, band=(30, 220))
    env_full = onset_envelope(y)

    bpm_seed = seed_tempo(env_beat, fps)
    beats = track_beats(env_beat, fps, bpm_seed)
    beats = beats[beats < duration - 1.0]
    intervals = np.diff(beats)
    bpm = 60.0 / float(np.median(intervals))
    beat_len = 60.0 / bpm

    notes = build_notes(beats, env_full, fps, y, beat_len)

    # Snap onto real onsets so blocks land on what the player hears.
    # Blocks belonging to a line are left alone: they are an evenly spaced
    # rhythmic figure, and pulling individual blocks onto nearby onsets would
    # make the run stutter.
    onsets = peak_times(env_full, fps, thresh=0.05)
    free = [n for n in notes if not n.get("line")]
    snapped, mean_shift = snap_to_onsets(
        np.array([n["time"] for n in free]), onsets, tol=0.055)
    for n, t in zip(free, snapped):
        n["time"] = round(float(t), 3)
    notes.sort(key=lambda n: (n["time"], n["lane"]))
    notes = enforce_spacing(notes, beat_len)
    for n in notes:
        n.pop("line", None)

    n_bombs = add_bombs(notes, beat_len)

    # How close the final map sits to real onsets, as a sanity figure.
    final = np.array([n["time"] for n in notes])
    err = np.array([np.min(np.abs(onsets - t)) for t in final])

    data = {
        "title": "Eye Of The Tiger",
        "artist": "Survivor",
        "audio": "audio/eye-of-the-tiger.mp3",
        "bpm": round(bpm, 2),
        "duration": round(duration, 2),
        "beats": [round(float(t), 3) for t in beats],
        "notes": notes,
    }
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")

    targets = len(notes) - n_bombs
    print(f"duration {duration:.1f}s   seed bpm {bpm_seed:.2f}   tracked bpm {bpm:.2f}")
    print(f"{len(beats)} beats tracked, interval iqr "
          f"{np.percentile(intervals, 25):.4f}-{np.percentile(intervals, 75):.4f}s")
    print(f"snapped to onsets: mean shift {mean_shift*1000:.1f} ms")
    print(f"alignment to nearest onset: median {np.median(err)*1000:.1f} ms, "
          f"90th pct {np.percentile(err, 90)*1000:.1f} ms")
    print(f"{targets} targets + {n_bombs} bombs  "
          f"({targets / duration * 60:.1f} notes/min)")
    print(f"first note {notes[0]['time']}s   last note {notes[-1]['time']}s")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
