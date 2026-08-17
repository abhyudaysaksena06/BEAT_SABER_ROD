# AR Beat Saber — Eye Of The Tiger

2D augmented-reality rhythm game. Webcam hand tracking, no headset, no network.

**Abhyuday Saksena · 1025030923 · COE · LEAD Society Tech Operations**

---

## Running it

Double-click **`start.bat`**, or:

```bash
python -m http.server 8000
```

then open <http://localhost:8000>.

**It has to be served — opening `index.html` directly will not work.** Browsers
only grant camera access on a secure context, and `file://` is not one.
`http://localhost` is, which is why a plain static server is enough. Nothing
leaves the machine either way.

Everything MediaPipe needs is vendored in `lib/`, so the stall build runs with
the network unplugged.

---

## Files

| Path | What it is |
| --- | --- |
| `index.html` | The whole game — rendering, tracking, scoring |
| `beatmap.json` | Generated chart: 458 blocks + 12 bombs |
| `audio/eye-of-the-tiger.mp3` | The track |
| `lib/` | MediaPipe Hands + camera utils, vendored for offline use |
| `tools/analyze_beats.py` | Regenerates `beatmap.json` from the mp3 |
| `start.bat` | Local server launcher |

---

## The beatmap

Generated from the audio, not hand-authored. `python tools/analyze_beats.py`
rebuilds it.

```
duration 249.8s   tracked bpm 109.96
453 beats tracked, interval iqr 0.5457-0.5573s
snapped to onsets: mean shift 8.6 ms
alignment to nearest onset: median 0.3 ms
458 targets + 12 bombs  (110.0 notes/min)
```

**How the beat is found.** A spectral-flux onset envelope feeds a
dynamic-programming beat tracker (Ellis 2007). The important detail is that the
tracker runs on a **30–220 Hz band**, not the full spectrum: the kick drum is
what defines the pulse here, while the full-band envelope is crowded with guitar
and vocal onsets that pull the grid off the beat. Autocorrelation with a
log-normal prior around 120 BPM supplies the seed tempo and kills half/double
octave errors.

A fixed grid is not good enough over four minutes — a constant 109 BPM ruler
accumulates hundreds of milliseconds of error by the outro. The DP tracker
follows the drift instead, and every block is then **snapped onto a real onset**
within ±55 ms. Median distance from a block to an actual onset in the audio is
**0.3 ms**.

**Patterns.**

- **Lines** — the 20 loudest bars are rewritten as a run of 8 blocks at
  half-beat spacing, holding one row and cycling lanes 0-2-1-3 so the run sweeps
  across the highway as a single figure. Direction flips every two blocks, which
  gives each hand a clean down-up-down-up reset.
  Thresholding individual offbeats does not work on this track — it is close to
  four-on-the-floor, so offbeat onset strength (median 0.04) sits far below the
  downbeats (0.22), and any threshold either catches nothing or scatters lone
  blocks that never read as a run.
- **Stacks** — the 6 biggest accents get a mirrored partner block, hit with both
  hands at once.
- **Bombs** — 12, dropped into gaps, always at least one lane clear of both
  neighbouring blocks.

**Playability is enforced, not hoped for.** Every pattern passes a final filter:
no two blocks closer than 0.22 s overall, and **no hand asked to swing twice
inside 0.42 s**. Per-hand minimum in the shipped map is 0.534 s. Hand
alternation is what makes the half-beat runs playable at all — each hand only
sees one block per beat even inside a line.

---

## Where the blocks live

Blocks used to travel down the screen and exit the bottom edge, which forced
the player to chase them below the camera's view — the hand leaves frame,
tracking dies, and the block becomes unhittable through no fault of the player.

The field now sits in the **middle band of the frame**. Blocks emerge from a
vanishing point at the centre and expand outward toward the player. Measured
over the whole map, nothing ever leaves **y 179–421** of a 640 px frame, or
**x 199–761** of 960 — the entire play area is where raised hands actually are,
with the bottom third of the frame left clear.

A block that is not hit does not continue past and slide off the edge. It
holds near the strike position, scales up and **fades out in place**.

### Depth

Notes travel toward the camera at constant speed, so depth falls linearly while
apparent size goes as **1/z** — real perspective, not a linear ramp. A block is:

| Distance | Size |
| --- | --- |
| Just spawned | 26 px |
| Halfway | 41 px |
| At the strike point | 92 px |
| Passing you | 167 px |

So a distant block is small and creeps along; a close one is big and moves
fast. That 3.5× growth is what gives the field a sense of depth, and it makes
the timing readable — you can see how close a block is.

### Reading the timing

Size alone tells you a block is getting closer, but not *when* to swing, and it
says nothing about whether your saber is anywhere near it. Three cues fix that.

**Approach ring.** A ring shrinks onto each block and meets its edge exactly at
the moment the block becomes hittable — the same idea as the approach circle in
osu!. It is a hard visual deadline rather than something you have to estimate:

| Block progress | Ring sits outside the block by |
| --- | --- |
| 0.35 (first appears) | 96 px |
| 0.50 | 77 px |
| 0.70 | 52 px |
| 0.85 | 35 px |
| 1.00 (strike) | 20 px |

**Armed.** A gold outline appears the instant a block enters the hit window, so
"live now" is unambiguous.

**Locked.** A white outline appears when *your* saber is close enough that a
swing right now would connect, and the saber's contact ring swells and turns
white at the same moment. This is the direct answer to "where would my saber
actually strike" — you can see you are lined up before committing to the swing.

### The saber has depth

The saber used to be a flat line of constant width, which sits *on* the picture
rather than *in* it. It now:

- **Scales with how far forward your hand is.** Apparent hand size stands in for
  distance, so reaching toward the camera thickens and lengthens the blade and
  pulling back thins it. Measured across a close/normal/far hand: blade width
  4 → 6 → 10 px, length 86 → 144 → 190 px.
- **Tapers** from a wide grip to a narrow tip, which reads as receding.
- Has a **hot white core offset toward one edge**, giving it a lit side and a
  shaded side instead of a flat bar, a **rounded tip cap**, and a **grip** below
  the palm so it reads as a held object with a near end.

## Ending a run

A run ends early on any of:

- **5 missed blocks**
- **3 bombs hit**
- **3 wrong-hand cuts**

Cutting a block with the wrong-colour saber used to be a non-event — the game
simply ignored it. It is now a real mistake: the block is destroyed, the combo
resets, and it counts against the run. All three counters are live in the HUD,
and the results screen names which limit ended the run.

**Bombs only react to the hand, not the blade.** Once hitting three bombs ends a
run, bombs have to be genuinely avoidable. Testing them against the full blade
made them nearly impossible to dodge — a simulated player actively retreating
from every bomb still clipped three of them with a tip they were not really
steering, and failed. Bombs now test the hand's swept path alone, and are live
only while visually in the strike zone. The same retreating player now takes
**zero** bomb hits across the track, while deliberately grabbing at them still
fails the run at three.

### Practice mode

**MODE** on the home screen switches between:

- **Normal** — the limits above apply.
- **Practice** — nothing ends the run. Misses and bombs still cost score and
  break the combo, so the feedback is unchanged, but the player always reaches
  the end of the track.

Practice is the one to use for a first-timer, or when someone just wants to feel
the song out. A green **PRACTICE — NO FAIL** badge sits on screen for the whole
run, the HUD shows plain tallies instead of `0/5`, and the results screen is
stamped so a practice score is never mistaken for a normal-mode one.

The limits themselves are also tunable without editing code, if you want normal
mode to be a little kinder rather than switching to practice:

```javascript
__debug.set({ maxMisses: 10, maxBombs: 5, maxWrong: 5 })
```

## Hand tracking

This is where most of the work went. The tracking in the first draft was sloppy
for several specific reasons, all fixed:

**Swing speed is measured in px/second, not px/frame.** This was the worst of
them. The same physical swing read roughly three times larger at 60 fps than at
20 fps, so the game silently got harder or easier depending on how busy the
laptop was. Verified: a 1100 px/s swing now registers 30/30 at 15, 24, 30, 45
and 60 fps, and a 250 px/s swing correctly registers 0/25 at every rate.

**Collision tests the swept path, not a single point.** A fast swing covers
100+ px between frames, so the blade tip could be on one side of a block on one
frame and past it on the next, never once landing inside the hit radius — the
"I definitely hit that" problem. Contact is now measured against the segment the
tip travelled this frame *and* against the blade itself, so a hit anywhere along
the saber counts. A swing that crosses a block entirely between two frames now
registers.

**The saber is anchored to the palm centre.** This is what fixed the "saber
isn't lined up with my hand" problem. The contact point used to sit 2.3× beyond
the wrist along the hand axis — you were aiming with a spot in mid-air, several
inches from anything you could see or feel.

The hit point is now the **palm centre**: the average of the wrist and all four
knuckles. Averaging five rigid landmarks cancels most of the per-landmark noise,
and it is the point a player actually thinks of as "my hand", so what they aim
with is what registers. A white contact ring is drawn exactly on it, so the
thing that scores hits is visible rather than implied.

The blade still extends outward for looks and extra reach, running along the
rigid wrist → middle-knuckle axis (landmark 8, the index tip, is the noisiest
point on the hand and moves when a finger curls). It is smoothed harder than the
palm, because it is a long lever — a degree of landmark noise swings the tip far
more than it moves the palm.

**Implausible jumps are rejected.** If the hand appears to move faster than
6500 px/s, that is not an arm — it is the detector swapping which hand is which,
or a hand re-entering frame somewhere else. Treated as a swing it would be an
enormous phantom slash that hits whatever is in the strike zone. The saber snaps
across and restarts from rest instead. Velocity is also divided by the time
since that saber was **last actually sampled**, not by the frame delta: if
tracking dropped it for a few frames the hand covered that distance over the
whole gap, and charging it to one frame invents a swing several times too fast.

**Adaptive smoothing.** Heavy filtering when the hand is nearly still (that is
all sensor jitter), light filtering when it is moving fast (that is a real
swing, and lag there costs hits). Result: jitter of up to 10 px/frame on a
stationary hand produces **0 false hits**, and holding a hand on a block scores
nothing.

**Tracking dropouts are tolerated.** Detection blinks constantly. Dropping the
saber the instant a frame misses made hits vanish mid-swing; it is now held for
180 ms. Re-acquisition snaps to the new position and restarts from rest, so the
jump from a stale position cannot fire as one enormous phantom swing.

**Saber assignment.** By on-screen position, never by MediaPipe's handedness
label — that label assumes a mirrored input and flips depending on how the feed
is fed in. With two hands the assignment is sticky, so crossing your arms does
not swap the saber colours. With one hand it follows you across the screen, with
a 60 px dead band on the centre line so jitter cannot flip it.

Also: the full landmark model (`modelComplexity: 1`) instead of the lite one,
and `maxNumHands: 3` so the crowd filter has a spare to cull without paying for a
fourth.

### Rendering is decoupled from tracking

`camera_utils.js` does not capture the next camera frame until MediaPipe's
`hands.send()` for the current one has fully resolved (see `Q()` in
`lib/camera_utils/camera_utils.js`), so the `hands.onResults` callback fires at
whatever rate the landmark model can actually run — typically well under 60fps.

The first draft ran the entire game — video paint, note spawn/progress,
collision, drawing — from inside that callback, which meant the whole game was
capped to MediaPipe's inference rate. On modest hardware this reads as general
lag: notes visibly stepping instead of gliding, and swings landing later than
they should relative to the block.

`onResults` now does only what genuinely needs a fresh hand sample: building
the hand list, filtering, and updating each rod's smoothed position and
velocity. Everything else — camera paint, note motion, drawing, hit checks —
runs in its own `requestAnimationFrame` loop (`tick()`), independent of
MediaPipe's cadence and typically at full display refresh.

Between hand samples, each rod's on-screen and hit-tested position is the last
confirmed sample extrapolated forward by its measured velocity
(`dispX/dispY/dispTX/dispTY`), capped at 80ms so a stale reading can't run
away. Collision (`saberDist`/`handDist`) reads the same extrapolated position
that gets drawn, so a hit always registers exactly where the rod appears to be
— there is no gap between what's on screen and what the game checks against.

The status bar's FPS reading is now split in two: render rate and hand-track
rate (`60 FPS · 22 TRACK`), so a slow machine's real bottleneck — inference,
not rendering — stays visible instead of being hidden behind one blended
number.

### Cut feedback

A hit no longer just deletes the block. It **splits along the cut line** — the
two halves take the swing's direction, separate perpendicular to it, tumble and
fade — with a white slash streak drawn along the path the saber travelled, plus
the particle burst. The block is halved along the axis you actually swung, so
the slice matches the motion that caused it.

**A block only cuts on an actual swing.** `checkHits` used to register a hit on
overlap alone — resting the rod on a block, or drifting past it slowly, sliced
it exactly the same as a real swing. That made every cut feel identical
regardless of how it was played, and left the CALIBRATE screen's SWING
SENSITIVITY slider adjusting a setting (`settings.minSwing`) nothing actually
read. The speed check is back: a block only cuts once `s.speed` clears
`minSwing`, so the rod has to be moving to register as a slash. In Arcade a
fast swing on the wrong axis now fails the block as a miss instead of a free
hit, rather than only checking direction when the auto-slash path made it
irrelevant. Shard velocity, spin, slash length, and the particle burst all
scale with how hard the swing actually was, so a bare-minimum-speed tap reads
as a lighter cut than a full swing.

### Crowd filter

Three modes, since the stall is the hard case:

- **Closest hands** (default) — keeps the two largest hands. The player stands
  nearest the camera so their hands are biggest. Needs no props.
- **Black gloves** — samples the wrist pixel and culls anything not dark. This
  samples a **separate raw video buffer**, not the visible canvas; by the time
  the check runs the visible canvas already has the dimmed overlay, highway
  lines and glowing blocks painted over the video, which would corrupt every
  reading. It averages a patch rather than one pixel, too.
- **Off** — for testing at a desk.

---

## Tuning at the venue

**CALIBRATE** on the menu gives you three live readouts:

- **Glove threshold** — with a live wrist-brightness number, so you can set it
  above your gloves and below the crowd under the actual stall lighting.
- **Swing sensitivity** — with a live peak-speed number. Swing as you would in
  game and set this to about half your peak.
- **Timing offset** — ±300 ms, if blocks feel early or late against the audio.

From the browser console you can also adjust live mid-run:

```javascript
__debug.set({ minSwing: 300 })
```

`__debug.read()` dumps score, combo, hits and misses.

### Difficulty

- **Casual** (default) — slash any direction, just use the matching colour hand.
- **Arcade** — the slash must follow the arrow, within about 70° of it.

Casual is the right default for a stall. Demanding an exact cut direction is
unfair on webcam tracking, where a fast swing is only ever a handful of noisy
samples.

---

## Verification

Hit detection was exercised without a camera by driving the frame callback with
synthetic landmarks on a pinnable clock, which is how the frame-rate bug was
caught in the first place.

The simulated player keeps both hands visible and moves them continuously, as a
real one does.

| Case | Result |
| --- | --- |
| Correct cuts, casual | 120/120 |
| Correct cuts, arcade | 120/120 |
| Reversed cut in arcade | 0/30 |
| Reversed cut in casual (direction ignored by design) | 30/30 |
| Creeping onto a block below the swing threshold | 0/30 false |
| Hand held still on a block | 0/30 false |
| Jitter, 3 / 6 / 10 px per frame | 0/30 false |
| Wrong-colour saber on a block | 0/50 hits, counted as bad cuts |
| Correct saber, same blocks (control) | 40/40, 0 bad cuts |
| Swing crossing a block between frames | registers |
| Swing through a dropped tracking frame | registers |
| Same swing at 15/24/30/45/60 fps | identical |
| 5-miss limit | ends run, "5 MISSED BLOCKS" |
| 3-bomb limit | ends run, "3 BOMBS HIT" |
| 3-wrong-hand limit | ends run, "3 WRONG-HAND CUTS" |
| Bombs, player retreating from each one | 0 hit, run survives |
| Bombs, player grabbing each one | fails at 3 |
| Practice mode, 118 misses | run continues |
| Practice mode, all 12 bombs hit | run continues |
| Normal mode, same idle run | ends at 5 misses |
| Block positions over the whole map | y 179–421, x 199–761 of 960×640 |
| Approach ring converges on the block | 96 → 20 px gap across the approach |
| Gold "armed" ring | only inside the hit window |
| White "locked" ring + saber lock | only when the saber is in reach |
| Saber scales with hand distance | 4 → 6 → 10 px wide, 86 → 190 px long |

Played end to end under the real 5/3/3 limits, in three segments:

| Section | Blocks | Hits | Misses | Bombs | Bad cuts | Survived |
| --- | --- | --- | --- | --- | --- | --- |
| 0–100 s | 175 | 168 | 2 | 0 | 0 | yes |
| 120–200 s | 166 | 161 | 1 | 0 | 0 | yes |
| 200 s–end | 86 | 83 | 1 | 0 | 0 | yes |

The handful of misses are stacked pairs: the simulated player swings one hand at
a time, so the partner block times out. A real player uses both hands at once.

Two bugs were found by this harness rather than by inspection: the frame-rate
dependence, and the phantom swing produced when tracking jumps. Both only showed
up at particular frame rates, which is exactly why the clock is pinnable.

---

## Notes against the original spec

A few things in the design document are not what shipped, deliberately:

- Velocity is **px/second**, not the documented `v > 15` px/frame. The
  per-frame form is frame-rate dependent, which is a bug rather than a choice.
- Collision is swept-segment against a 72 px radius, not a point test at 55 px.
- Direction matching is a 70° cone, not a strict dominant-axis comparison, and
  is off entirely in Casual.
- Glove sampling reads a raw video buffer and averages a patch — sampling the
  composited canvas at one pixel, as written, would read the game's own
  graphics.
- Handedness comes from screen position, not `multiHandedness`.
- Scale is `1/z` true perspective, not the documented `S = 0.1 + 0.9p`. The
  linear form conveys no depth: a block at half distance looks half size
  instead of the ~45% real perspective gives, and everything moves at a
  constant rate rather than rushing in at the end.
- Blocks are laid out around a central vanishing point in the middle band of
  the frame, not on a highway running to the bottom edge.
- The contact point is the palm centre, not landmark 8 or an extrapolation
  past it.

## If you want the official chart

Video analysis would be lossy guesswork. The real Beat Saber map for this song
is published as machine-readable `.dat` files on
[BeatSaver](https://beatsaver.com) — search "Eye of the Tiger", download the
zip, and the difficulty files inside give exact note times, lanes, rows and cut
directions. If you drop that zip in the folder I can convert it to
`beatmap.json` directly, and the chart becomes the official one rather than one
inferred from audio.
