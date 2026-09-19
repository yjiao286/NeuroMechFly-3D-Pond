# Fly Pond · Complete Rulebook

This is the **authoritative reference** for every behavioural rule in the simulation —
each rule lists its implementation location and current parameter values. When you tune
anything, update this page to match. For an overview and screenshots see
[README.md](../README.md) · 中文版：[RULES.zh-CN.md](RULES.zh-CN.md).

Contents: [World](#1--world-and-time) · [Pads & crumbs](#2--lily-pads-and-crumbs) ·
[Food competition](#3--food-competition) · [Territorial contests](#4--territorial-contests) ·
[Scripted fly](#5--scripted-fly-state-machine) · [Neural fly](#6--the-neural-fly) ·
[Frog](#7--the-frog) · [Shared body rules](#8--shared-fly-body-rules) ·
[Respawns](#9--respawns-and-death) · [Camera & controls](#10--camera-and-controls) ·
[Parameter table](#11--quick-tuning-table) · [Debug tools](#12--debug-and-test-tools)

---

## 1 · World and time

| Rule | Value | Location |
|---|---|---|
| Window / frame rate | 1280×800 @ 60 FPS (frame length capped at 1/20 s — slow motion on frame drops, never time jumps) | `main.py` `W,H,FPS` |
| Pond surface | ±620 × ±350 world units | `scenery.py` `POND_W2/H2` |
| Activity bounds | frog and flies confined to ±545 × ±285; crossing pushes back and flips heading 180° | `main.py` `MARGIN_X/Y` |
| Ground height | on a lily pad = pad surface (bobs with the wave); otherwise water level z=0 | `main.py` `ground_z()` |
| Ambient ripples | a decorative ripple every 0.7–2.2 s at a random spot | `Game.simulate` |
| Ripple body | 3 rings (delays 0/0.16/0.36 s), expanding 52+26×strength px/s, life 1.0 s | `scenery.Ripples` |
| Units | 1 world unit ≈ 1 px; flies are globally scaled by `FLY_SCALE = 1.85` | `models.py` |

## 2 · Lily pads and crumbs

**Pads** (`scenery.LilyPad3D`)
- Exactly 6, radius 46–66, pairwise spacing >200 (guaranteed at generation), 45% carry a flower.
- Surface height `PAD_TOP = 2.2`, bobbing `±1.6 × sin(1.2t + φ)`; on-pad test uses an ellipse correction (y ×0.92).
- On flower pads crumbs sit at 0.55–0.8 × radius (clear of the petals); plain pads 0.15–0.5.

**Crumbs** (`scenery.FoodCrumb`)
- **Two per pad**, on opposite halves (`side=0/1`, respawn angle side×π ± 0.6) — they never overlap.
- **`CAPACITY = 1`**: exactly one feeder at a time per crumb.
- Amount `6.0`; eaten at 0.8/s; regrows **2.5–4 s** after depletion (new spot on the same half).
- Crumbs with amount ≤0.3 are not selectable; waiters abandon one below 0.8 immediately.
- Drawn in the **creature layer** (4): within-layer depth sorting buries far-side crumbs
  under the leaf disc, so the whole crumb was promoted a layer.

## 3 · Food competition

**Picking** (`main.pick_food`, shared by both fly kinds)
- Cost = `distance ÷ (free seats × (0.25 + amount/6)) × individual bias`; take the minimum.
  - Freshness weight: a fresh crumb is worth a longer flight; a nearly-bare one only if en route.
  - Per-fly bias `pick_bias ∈ 0.8–1.2` (fixed for life): simultaneous pickers split targets
    instead of herd-rushing the single free seat.
- Everything full → return the nearest crumb (the caller enters loiter logic).

**Reservation and seating** (`FoodCrumb.seats_for` / `seated`)
- **Closest claimant wins**: seats are counted from the fly's own distance outwards; only
  claimants *strictly closer* count against it. Two equidistant flies can never each see the
  other as blocking — the mutual-lock deadlock is impossible by construction at capacity 1.
- **Seated registry `seated`**: rebuilt at frame start from last frame's eating state, then
  mutated live by the eating gates within the frame — of two flies landing in the *same*
  frame, only the first registers (`feeders` is a frame-start snapshot and cannot arbitrate).
- Fleeing / being displaced / leaving satiated releases claim and seat immediately.

**Loitering and giving up**
- Full house: loiter on a wide slow circle at 0.55× cruise (turn rate 0.3× per-fly jitter,
  circles don't overlap), re-weighing every 0.45 s.
- Immediate bail-out when: target amount <0.8, or after **3.5 s** of waiting; then wander
  **0.8 s** before re-picking (so the herd doesn't pile back onto the seat that just opened).

**Approach** (scripted flies; the neural fly lands via its rest circuit instead)
- Far (>45 px): pure pursuit, turn ≤2.6 rad/s.
- Terminal (≤45 px): exponential alignment (rate 7 rad/s — above the line-of-sight rotation
  v/d at any distance, so a pursuit limit cycle **cannot exist**) plus distance-proportional
  slowdown `v = 2.2d + 14` (floor 18).
- Safety net: **6 s** of near-field (<70 px) stall without landing → drop the claim and retry
  (covers rare cases such as the crumb respawning to a new offset mid-approach).

## 4 · Territorial contests

**Pairing** (`Game._update_contests`, rebuilt every frame)
- Attacker = the neural fly, when `resting` (landed), **not eating**, and `z < LAND_Z+6`.
- Victim = a scripted fly in state `进食` (feeding) within 72 px of the attacker (≈ same pad).
- Both sides carry `contest_t` (duration, accumulated across frames via a (foe, time)
  snapshot), `contest_foe`, `contest_dir`, `contest_role`.

**Attacker behaviour** (`BrainFly.update` contest branch + pose layer `models.draw_fly`)
- Always faces the opponent; strides in at `min(60, 2d+18)` px/s while farther than 20 px,
  stands and delivers at ≤20 px.
- **Spoils of war**: if the opponent's bowl is clearly closer (+20 px margin) with amount
  >0.8, retarget to it — a won fight ends with the winner eating the spoils.
- **Lunge**: ~1/s (phase rate 7 rad/s, sin^1.5 envelope); amplitude ramps with distance —
  full power ≤22 px, zero beyond 32 px (mid-power jabs while chasing).
  A full lunge = rear up (0.5 rad pitch) + 5-unit body thrust + both front legs reaching
  4+ body-lengths over the opponent's head + wing threat (0.52 spread).
- `rest_t` is frozen during a contest (fighting is not "no progress").

**Victim behaviour**
- Crouches (body 1.7 units lower, head down 0.08 rad), trembles at 30 Hz (amplitude 0.7).
- From 0.7 s of pressure backs away from the attacker at 55 px/s, but **no farther than
  16 px from its own crumb** (clings to the bowl; never herded off the pad).
- After **1.8 s** cumulative pressure (two full lunges) → abandons the crumb and flees at
  150 px/s for 0.8 s (back to cruise altitude — pressured, not panicked).

## 5 · Scripted fly state machine

Eight flies, finite-state machine + waypoint control, no neurons (`main.ScriptedFly`).

```
        ┌──────────────────────────────────────────┐
        ▼                                          │
   foraging ──d<9──▶ feeding ──done/bare/displaced─┤
        │                │                         │
        │                └── contest>1.8s ─▶ fleeing│
        └── frog<110px ───────────────▶ fleeing ───┘
                                    (1.1 s, or frog>260 px)
```

| State | Rules |
|---|---|
| Foraging | re-pick target every 0.45 s (see §3); with no target, sine-wander at 0.6× speed |
| Feeding | a meal lasts **2.2–3.2 s**; no bites while descending or grooming (`eating_now=False`); ends on timer, empty crumb, or displacement |
| Fleeing | from frog: 240 px/s, 1.1 s, climb to escape altitude, heading noise ±0.3 rad; displaced: 150 px/s, 0.8 s, cruise altitude |

## 6 · The neural fly

Exactly one per pond, marked with the gold brackets (`main.BrainFly` + `fly_brain.FlyBrain`).

### 6.1 Spiking network (LIF circuits)

Firing model: `dv/dt = (−(v − v_rest) + I) / τ`, spike at `v ≥ v_thr` and reset to `v_reset`;
each frame integrates in sub-steps of τ/2. Defaults `v_rest=−70, v_thr=−52, v_reset=−76 mV`.

| Circuit | τ | Drive I | Effect |
|---|---|---|---|
| **GF giant fibre** (escape) | 0.05 s | `55 × threat`, threat = `max(0, 1−d_frog/260)`, ×3 while the frog is airborne and <160 px (hop shadow) | fires → 0.85 s escape thrust ×3.1 + 1.6 s refractory |
| **CX_L/R central complex** (steering) | 0.25 s | `12 × (0.5+0.45sin(t·f+φ))`, left/right phase offset 0.9π, f∈0.25–0.5 Hz | amplitude difference × 2.6 × wander gain (0.8–1.3) + N(0,0.12) → smooth, inertial random walk |
| **REST rest/arousal** (feeding gate) | 0.6 s | hovering within 40 px of a crumb: `45 × (1−d_crumb/40)`, gated by effective threat <0.32 | fires → landing (lands even onto an occupied crumb and contests it, §4) |

### 6.2 Habituation (a real GF property)

- A frog parked within threat >0.3 (<~182 px) → sensitivity decays at `0.5×threat/s`,
  **floor ×0.45**.
- Threat gone → recovers at 0.06/s.
- A **hop shadow** → habituation instantly resets (shadows cannot be habituated).
- Effect: fresh escape radius ~170, fully habituated ~70 (still flees point-blank — warier
  than the scripted 110); but it will land and feed next to a motionless, habituated-to frog.

### 6.3 Hunger and the feeding pipeline

- Hunger starts 0.6; −0.22/s while feeding, +0.05/s while flying; **≤0.05 = satiated, leaves**
  (a meal ≈2.5 s; never eats a crumb down to nothing).
- Odour-taxis while cruising: turn `1.6 × (0.5+hunger)` rad/s; ×2.2 gain inside 50 px
  (so the rest neuron can charge past threshold); brakes to 0.45× within 45 px to hover.
- **Target stickiness**: locked while eating / resting within 30 px of the crumb /
  approaching within 60 px — at capacity 1 the crumb you occupy is always "full" to the
  picker, so without a lock the fly would be interrupted by its own reservation.
- After landing: crawls to the crumb (≤36 px/s); **3.5 s without food** or **9 s of resting**
  → voluntary take-off (the 3.5 s timer is frozen during contests).

## 7 · The frog

Player-controlled (`main.Frog`). Autopilot (for smoke tests): walks to the nearest fly,
tongues inside range, jump-predicts landings.

| Rule | Value |
|---|---|
| Walk | 175 px/s, heading eases toward the move direction |
| Jump | 0.62 s airborne / 310 px / apex 62; landing crushes all flies within r=60 + 0.32 s camera shake |
| Tongue | range 200, heading cone ±1.15 rad, cooldown 1.1 s; tip catch radius `round(17×1.85)=31`; state machine 0.11/0.05/0.14 s (out/hold/back) |
| Aiming | nearest target inside the cone gets the white ring (shared with tongue) |
| Blink | every 2.5–6.5 s, 0.13 s each |
| Fear radius | scripted flies always flee inside 110 px (clear at 260); the neural fly per GF habituation (§6.2) |

## 8 · Shared fly body rules

`main.FlyBase` + `models.draw_fly`:

- Cruise speed `85×1.85/1.42 ≈ 111 px/s` (per-fly jitter ×0.85–1.15); altitudes: cruise 29.6 /
  escape 37 / ground 5.55, easing at 5/s.
- 22 Hz wing beat; spread interpolates continuously with altitude (spread on take-off, fold
  on landing); walk gait frequency ∝ speed.
- **Grooming**: every 2.5–5 s while landed, 1.6 s each — front legs circle over the eyes;
  interrupted by fleeing/take-off; no eating while grooming; grooming yields to lunging
  (front legs are busy punching).
- Poses interpolate between fly/walk/feed/groom/contest — no pops on state changes.
- Shadow fades with altitude (scale ∝ 1−z/55, floor 0.25).

## 9 · Respawns and death

- A eaten fly (crushed or tongued) → particle burst + "+1" popup + crunch; the population
  is checked every **5 s** and topped back up to 9 (8 scripted + 1 neural) from the edges —
  the neural fly respawns first if it was the one eaten.
- A depleted crumb regrows after 2.5–4 s at a fresh spot on the **same pad, same half**
  (full 6.0 amount).
- Wing-buzz volume ∝ proximity of the nearest flying fly (430 px falloff).

## 10 · Camera and controls

- Over-the-shoulder camera: target (80,100), yaw 225°, elevation 25° (adjustable 18°–62°),
  distance 1526 (wheel 420–3200).
- Keys: WASD/arrows swim · Space jump · F/click tongue · B brain panel · M mute · F11
  fullscreen · Esc quit; left-drag pan · right-drag orbit · wheel zoom.
- Brain panel (B): live activation and firing readout of the GF / CX_L / CX_R / REST circuits.

## 11 · Quick tuning table

The most-tuned parameters (file · symbol · current value):

| Topic | Parameter | Value | Location |
|---|---|---|---|
| Competition | crumb capacity | 1 | `scenery.FoodCrumb.CAPACITY` |
| Competition | crumbs per pad | 2 (opposite halves) | `main.Game.__init__` |
| Competition | amount / bite / regrow | 6.0 / 0.8/s / 2.5–4 s | `scenery.FoodCrumb` |
| Competition | repick / loiter cap / wander | 0.45 s / 3.5 s / 0.8 s | `main.ScriptedFly.update` |
| Competition | scripted meal | 2.2–3.2 s | ibid. `eat_t` |
| Competition | terminal approach | <45 px: e^−7 alignment, v=2.2d+14 | ibid. |
| Contest | pairing / stand-off radius | 72 px / 20 px | `main._update_contests` / `BrainFly` |
| Contest | yield threshold | 1.8 s | `ScriptedFly.update` |
| Contest | lunge rate / full-power radius | 7 rad/s (≈1.1 Hz) / ≤22 px | `models.draw_fly` |
| Contest | retreat speed / clamp | 55 px/s / 16 px from own crumb | `ScriptedFly.update` |
| Neural | escape thrust / refractory | 0.85 s ×3.1 / 1.6 s | `fly_brain.FlyBrain.step` |
| Neural | habituation floor / recovery | ×0.45 / 0.06 per s | ibid. |
| Neural | rest gate / sensing radius | threat_eff<0.32 / 40 px | ibid. |
| Neural | hunger rates | feed −0.22/s, fly +0.05/s, satiety 0.05 | `main.BrainFly` |
| Neural | resting timeouts | 3.5 s no food / 9 s total | ibid. |
| Frog | jump / crush / tongue | 310 px·62 apex / r60 / range 200·cone 1.15·cd 1.1 s | `main.Frog`, constants |
| World | pads / spacing | 6 / >200 | `scenery.make_pads` |
| World | population / respawn check | 9 (8+1) / 5 s | `main.TARGET_FLIES` |

## 12 · Debug and test tools

All run headless (`SDL_VIDEODRIVER=dummy`); usage in each file header:

| Tool | Purpose |
|---|---|
| `tools/feed_probe.py` | feeding totals / meals / shares / overfill / loiter / deadlock stats |
| `tools/orbit_probe.py` | pursuit-orbit detection; `--harass` teleports the frog between crumbs |
| `tools/contest_probe.py` | contest pairing rate / contest_t distribution / yields |
| `tools/threat_probe.py` | both fly kinds feeding vs frog parked 120/140/160/999 px away |
| `tools/trap_experiment.py` | controlled approaches: 12 distance×heading-error combos, landing time |
| `tools/soak_test.py` | chaos soak: teleporting frog + tongue/jump spam |
| `tools/state_dump.py` | 4 s pond snapshots (states/targets/distances/occupancy) |
| `tools/preview.py` | offscreen turntables/close-ups/panoramas (frog/fly/pond/wide/doc/hero) |
| `tools/debug_contest.py` / `debug_groom.py` / `debug_crumbs.py` | contest / grooming legs / per-pad crumb visual checks |
| `main.py --selftest` | smoke test: autopilot must eat; `fly_brain.py` runs the GF approach self-test |

---

*Last synchronised: 2026-09-19 (code state after commit 578201b). If you change a rule,
update this page — and its Chinese twin.*
