# 🧠 NeuroMechFly-3D-Pond

**A 60 FPS, engine-free 3D pond in ~2.3k lines of Python** — software-projected, software-shaded,
no OpenGL and no asset files beyond the source itself. Nine fly agents inhabit the pond; exactly
one of them is driven by a **spiking neural network** of leaky integrate-and-fire neurons
(giant-fibre escape reflex, central-complex steering, rest-gated feeding). The same repository
assembles, simulates and renders the **real NeuroMechFly** body model — micro-CT meshes,
126 rotational DoF, compound-eye readout — on MuJoCo.

![python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![pygame-ce](https://img.shields.io/badge/pygame--ce-2.5-6cbf4a)
![renderer](https://img.shields.io/badge/3D%20renderer-software%20rasteriser-2f6f5f)
![loc](https://img.shields.io/badge/code-~2.3k%20lines-informational)
![FlyGym](https://img.shields.io/badge/FlyGym-NeuroMechFly%202.1%20%2F%20MuJoCo%203.9-8a5cf6)
![license](https://img.shields.io/badge/license-MIT-4c8f4c)

[English](README.md) · [简体中文](README.zh-CN.md)

![NeuroMechFly: the real fruit fly model rendered from its micro-CT meshes in MuJoCo](docs/images/neuromechfly.png)

*MuJoCo offscreen render of the model assembled by [`real_fly_demo.py`](real_fly_demo.py): 70 body
segments, 126 rotational DoF, 132 actuators (126 position + 6 tarsal adhesion), 2 eye cameras.
Not a game asset — see [§5](#5--the-real-fly-flygym--neuromechfly-pipeline).*

![The fly's compound-eye readout: fisheye camera view and the 721-ommatidia mosaic](docs/images/fly-vision.png)

*Vision interface of the same model. Left: left-eye camera (512 × 450 px, 157° FOV, fisheye
corrected). Right: the same frame resampled onto the hexagonal ommatidia mosaic — 721 per eye —
split by ommatidia type (R = yellow, G = pale). This mosaic is the retina's actual output; a
rectangular image never reaches the brain.*

---

## Contents

| § | Section |
|---|---|
| 1 | [System overview](#1--system-overview) |
| 2 | [The spiking agent](#2--the-spiking-agent) |
| 3 | [Renderer](#3--renderer) |
| 4 | [Scenery, modelling and materials](#4--scenery-modelling-and-materials) |
| 5 | [The real fly: FlyGym / NeuroMechFly pipeline](#5--the-real-fly-flygym--neuromechfly-pipeline) |
| 6 | [Related work](#6--related-work) |
| 7 | [Build, run, controls](#7--build-run-controls) |
| 8 | [Verification](#8--verification) |
| 9 | [Known limitations](#9--known-limitations) |

---

## 1 · System overview

Two independent stacks live in one repository. Stack A is the game: a deterministic simulation
loop plus a hand-written rasteriser. Stack B is the FlyGym bridge: it composes a scientific
MuJoCo model and renders it offline. They share no code beyond `V3 = pygame.math.Vector3`.

| Subsystem | File | LoC | Responsibility |
|---|---|---|---|
| Game loop, agents, HUD | [`main.py`](main.py) | 1016 | 60 FPS fixed-step loop, frog kinematics, both fly classes, HUD, headless selftest |
| Software renderer | [`render3d.py`](render3d.py) | 333 | `Camera3D` perspective projection, `Painter` depth sorter, material primitives |
| Neural agent | [`fly_brain.py`](fly_brain.py) | 155 | LIF neuron, GF / CX / REST circuits, sensory integration, motor commands |
| Scene | [`scenery.py`](scenery.py) | 654 | Water, caustics, ripples, lily pads, crumbs, banks, props, sky, camera-keyed cache |
| Audio | [`sounds.py`](sounds.py) | 127 | Procedural synthesis at 22.05 kHz (croak, splash, crunch, wing buzz, tongue snap) |
| FlyGym bridge | [`real_fly_demo.py`](real_fly_demo.py) | 166 | Composes NeuroMechFly in MuJoCo, renders body + retina readouts |

Runtime invariants: window 1280 × 800 (`SCALED │ RESIZABLE`), 60 FPS cap, simulation timestep
clamped to 1/20 s to survive stalls, world units are ~mm-equivalent pond units, and every
randomised layout is seeded.

## 2 · The spiking agent

`fly_brain.py` implements `dv/dt = (−(v − v_rest) + I)/τ` with threshold-reset dynamics, integrated
in sub-steps of τ/2 for stability. Defaults: `v_rest = −70 mV`, `v_thr = −52 mV`,
`v_reset = −76 mV`; a spike is one sample of `fired`, and `activation ∈ [0,1]` is the normalised
distance to threshold — this is what the `B` panel plots.

| Circuit | FlyGym-free analogue | τ | Drive | Fires when |
|---|---|---|---|---|
| **GF** giant fibre | escape reflex | 0.05 s | `55 · max(0, 1 − d_frog/260)`, ×3 while airborne | threat inside ~170 units, or a hop shadow overhead → 0.85 s escape thrust ×3.1, then 1.6 s refractory |
| **CX_L / CX_R** central complex | heading control | 0.25 s | competing slow oscillators | read out as an amplitude difference; produces smooth, non-periodic cruising |
| **REST** rest/arousal | feeding gate | 0.6 s | `45 · rest_fill` while hovering and threat < 0.25 | crossing threshold releases the landing manoeuvre; abort after 2.5 s without food or 12 s of resting |
| Olfactory bias | chemotaxis | — | heading nudge toward the nearest crumb | hunger state |

The eight remaining agents (`ScriptedFly`) are finite-state waypoint controllers: forage → land →
chew, flee below 110 units (re-arm at 260). The spiking agent flees at ~170 units, which is the
observable difference between a threshold detector and a scripted trigger.

| | `ScriptedFly` | `BrainFly` |
|---|---|---|
| Decision source | state machine + tuned constants | LIF membrane potentials |
| Escape trigger | distance < 110 | GF threshold crossing (~170) |
| Feeding | timer | REST gate |
| Introspection | none | `B` panel: per-neuron activation, live |
| Count in pond | 8 | 1 (gold bracket + label) |

## 3 · Renderer

No GPU, no depth buffer. The pipeline is: world → view transform → near-plane clip
(Sutherland–Hodgman, `NEAR = 14`) → perspective projection (`focal = 1050 px`) → deferred painter
submission → per-layer depth sort → blit. Polygons are queued as closures and executed in
`Painter.flush`, which is what makes explicit ordering control possible.

| Layer | Contents | Ordering rule |
|---|---|---|
| 0 `WATER` | water body (40 × 8 colour cells) | flat, always first |
| 1 `FX` | ripple dashes, caustics, foam ring, ripples, duckweed | above water only |
| 2 `PAD` | reserved for pad decals | — |
| 3 `BANK` | bank walls, meadow, pebbles, rocks, bushes (cached), reeds, grass | depth-sorted, camera-keyed cache for static props |
| 4 `MAIN` | frog, flies, particles, tongue | depth-sorted, drawn last |

**Why two layers matter.** Sorting a small creature against a huge ground polygon by mean depth
breaks as soon as the creature is on the far half of that polygon. Splitting the scene into
"flat, always underneath" and "volumetric, depth-sorted" removes the failure mode, and explicit
per-object `bias` values separate coplanar parts (leaf top / veins / underside, shadow vs. body).

**Materials.** A single world-space key light (`LIGHT_XY`) drives all shading, so highlights stay
consistent under camera orbit:

| Primitive | Use | Shading model |
|---|---|---|
| `sphere()` | bushes, rocks, eyes, crumbs, joints | rim-darkened base → inset light-shifted layers → specular dot → outline; small-radius LOD |
| `dome()` | frog back/head, lily pads, pebbles, wings | edge darkening → inset highlight toward the light → sheen |
| `soft_shadow()` | every grounded object | cached radial-falloff sprite scaled to the projected ellipse |
| `flat_polygon()` / `segment()` / `polyline()` | water, veins, stems, tongue | flat fill with depth bias |

Measured cost, headless software rendering with the entire pond in frame: **≈ 17.7 ms/frame**
(1024 × 800 dummy SDL, Python 3.14, pygame-ce 2.5.8). The static bank layer is re-rendered only
when the camera rig changes, which is where most of the headroom comes from.

## 4 · Scenery, modelling and materials

![Mid-zoom view of the pond: the gold-outlined neural fly with its GF/CX/REST panel open](docs/images/pond-neural.png)

*Whole-system view, mid zoom. Everything on screen is generated at run time — water body, pads
with radial veins and lifted rims, wet-to-dry sand ramps, reeds and bushes. In the middle of the
pond sits the **neural individual**: the gold bracket labels its state (`巡航`), and the panel on
the right reads its four circuits live — GF is charging because the frog is inside the 170-unit
escape radius, CX_L/CX_R show the steering oscillators, REST is quiet.*

All geometry is generated at run time from seeded RNGs; the repository contains no mesh, no
texture and no audio file.

| Element | Model | Texture / shading |
|---|---|---|
| Water | pond plane ±620 × ±350, activity region ±545 × ±285 | 3 layers: atmosphere gradient (40 × 8 cells) + drifting ripple dashes + caustic sparkle field; shoreline foam ring |
| Lily pads | 6 pads, r 46–66, notch + veins + optional lotus, bobbing on `sin(1.2t + φ) · 1.6` | radial veins tapering outward, lifted near-edge rim, leaf underside, soft contact shadow |
| Frog | dome body + head, 6 jointed legs, 3 toes each, gold eye pair with pupil glint, tongue as a 7-bead chain | dorsal ridge highlight, camouflage spots, belly shading, contact shadow |
| Flies | 3-segment abdomen, thorax with bristles, head + antennae + halteres, 6 two-segment legs with 7 links total, veined wings | per-part shading, wing veins and leading-edge highlight, soft shadows, translucent folded wings |
| Banks | 4 walls × 8 segments, 44-unit rim, meadow to 2600 units | wet→dry sand gradient, pebbles, rock speckle, bush clusters, haze-faded meadow, drifting clouds, horizon glow |

Pose and gait logic covers four fly states — flight (wing beat, legs tucked), walking (tripod
gait, step frequency ∝ speed), feeding (mouthparts on the crumb, front legs rubbing), grooming
(front legs circling the eyes for 1.2 s, the real *Drosophila* cleaning behaviour).

## 5 · The real fly: FlyGym / NeuroMechFly pipeline

[`real_fly_demo.py`](real_fly_demo.py) composes EPFL's
[NeuroMechFly](https://neuromechfly.org) through the FlyGym 2.x composition API and renders it
offscreen — the two images at the top of this page are its output.

| Stage | API call | Result |
|---|---|---|
| Body | `NeuroMechFly()` + `colorize(visuals.yaml)` | 70 micro-CT body segments, materials applied |
| Articulation | `Skeleton(AxisOrder.PITCH_YAW_ROLL, JointPreset.ALL_BIOLOGICAL)` + `add_joints()` | 126 rotational DoF, per-joint stiffness / damping / armature |
| Actuation | `add_actuators(..., ActuatorType.POSITION)` | 126 position actuators — the muscle proxy |
| Adhesion | `add_leg_adhesion()` | 6 tarsus5 adhesion actuators, `ctrl ∈ [0,1]` |
| Vision | `add_vision()` | 2 eye cameras → `get_raw_vision()` and `get_ommatidia_readouts()` |
| Physics | `FlatGroundWorld` / `BlocksTerrainWorld` + `Simulation` | MuJoCo 3.9, 1 ms timestep |

Interfaces exposed to a controller (i.e. what a neural circuit would read and write):

| Direction | Channel | Resolution |
|---|---|---|
| sense | vision | 2 × 721 ommatidia, yellow/pale split, 157° FOV per eye, fisheye model |
| sense | proprioception | joint angles + velocities for all 126 DoF |
| sense | mechanosensation | per-segment contact forces (per-leg ground sensors on simple worlds) |
| act | motor | 126 joint targets + 6 adhesion channels |

```bash
python3.12 -m venv .venv-flygym                  # FlyGym 2.1.0 requires Python 3.12+
.venv-flygym/bin/pip install -r requirements-flygym.txt
.venv-flygym/bin/python real_fly_demo.py         # → docs/images/{neuromechfly,fly-vision}.png
```

```
NeuroMechFly: 70 个体节 / 126 个关节自由度 / 132 个执行器（含 6 个足端附着）/ 2 个复眼相机
```

First run is dominated by Numba JIT on the retina path (~1 min); later runs render in seconds.
`pip install "flygym[warp]"` enables the MuJoCo-Warp backend for batched simulation;
`"flygym[rl]"` adds Gymnasium + Stable-Baselines3 for locomotion policies.

### Game agent vs. scientific model

| | Game fly ([`fly_brain.py`](fly_brain.py)) | NeuroMechFly |
|---|---|---|
| Body | a few dozen generated polygons | 70 micro-CT meshes |
| Articulation | kinematic, scripted gaits | 126 DoF with joint dynamics |
| Controller | 4 hand-tuned LIF circuits | user-supplied circuit / CPG / RL policy |
| Vision | distance checks | 721-ommatidia retina per eye |
| Timestep | 1/60 s, pure Python | 1 ms, MuJoCo |
| Purpose | interaction and pedagogy | sensorimotor neuroscience |

## 6 · Related work

Context for the spiking agent: *Drosophila* is the only animal with a complete adult brain wiring
diagram, and those diagrams are now being coupled to simulated bodies.

| Work | Contribution | Link |
|---|---|---|
| FlyWire whole-brain connectome | 139,255 neurons, ~5 × 10⁷ chemical synapses | [Dorkenwald et al., *Nature* 634, 124–138 (2024)](https://doi.org/10.1038/s41586-024-07558-y) · [flywire.ai](https://flywire.ai) · [annotations](https://github.com/flyconnectome/flywire_annotations) |
| Whole-brain annotation and cell typing | cell types and cross-individual stereotypy | [Schlegel et al., *Nature* 634, 139–152 (2024)](https://doi.org/10.1038/s41586-024-07686-5) |
| Computational whole-brain model | connectome-constrained model reproducing sugar sensing and feeding | [Shiu et al., *Nature* 634, 210–219 (2024)](https://doi.org/10.1038/s41586-024-07763-9) |
| Hemibrain connectome | the central-brain reconstruction that opened the field | [Scheffer et al., *eLife* 9:e57443 (2020)](https://doi.org/10.7554/eLife.57443) |
| Male nerve cord connectome | descending commands → motor output | [Takemura et al., *eLife* (2024)](https://doi.org/10.7554/eLife.97769) |
| **NeuroMechFly v2 / FlyGym** | embodied neuromechanics — the model installed and rendered here | [Wang-Chen et al., *Nature Methods* 21, 2353–2362 (2024)](https://doi.org/10.1038/s41592-024-02497-y) · [neuromechfly.org](https://neuromechfly.org) · [NeLy-EPFL/flygym](https://github.com/NeLy-EPFL/flygym) |
| FlyBody | whole-body physics with connectome-derived neuromuscular wiring | [Vaxenburg et al., *Nature* (2025)](https://doi.org/10.1038/s41586-025-09029-4) · [TuragaLab/flybody](https://github.com/TuragaLab/flybody) |
| Connectome-constrained deep mechanistic networks | single-neuron visual responses from optic-lobe wiring | [Lappalainen et al., *Nature* 634 (2024)](https://doi.org/10.1038/s41586-024-07939-3) · [TuragaLab/flyvis](https://github.com/TuragaLab/flyvis) |

## 7 · Build, run, controls

```bash
git clone https://github.com/yjiao286/NeuroMechFly-3D-Pond.git
cd NeuroMechFly-3D-Pond

python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip

.venv/bin/python main.py
```

Requires Python 3.10+ (developed on 3.14); dependencies are `pygame-ce` and `numpy` only.

| Input | Action | Implementation detail |
|---|---|---|
| Arrows / `WASD` | swim | velocity = 175 u/s, heading smoothed with an exponential turn rate |
| `Space` | hop | 310 u over 0.62 s, quadratic arc, 62 u peak; 60 u kill radius on landing |
| Left click / `F` | tongue strike | 200 u range, 17 u capture radius, ±1.15 rad cone, 0.30 s cycle |
| Left-drag | pan camera | grab-style pan, speed ∝ zoom |
| Right-drag | orbit camera | yaw free, pitch clamped 18°–62° |
| Wheel | zoom | distance clamped 420–3200, default 1526 |
| `B` | neural panel | GF / CX_L / CX_R / REST activation bars + legend |
| `M` / `F11` / `Esc` | mute / fullscreen / quit | — |

## 8 · Verification

```bash
.venv/bin/python main.py --selftest      # 1800-frame headless autopilot, asserts ≥ 1 catch
.venv/bin/python fly_brain.py            # isolated circuit probe: reports GF spike time/distance
```

```
[selftest] 吃掉=5 跳跃=1 存活=5 神经元果蝇GF逃逸反射=0次
[selftest] PASS ✓
t=3.80s 距离=171px → 巨纤维发放，状态=逃逸
```

The selftest drives the frog with a scripted policy under `SDL_VIDEODRIVER=dummy`, so rendering
and physics are exercised without a display. Frame-cost figures quoted above were measured the
same way (`Game.draw()` in a 60-iteration loop, warm cache).

## 9 · Known limitations

- **UI strings are Chinese.** Fonts are resolved from macOS system CJK faces, then
  `pygame.font.match_font`; on Windows/Linux add a CJK `.ttf` to `FONT_PATHS` (`main.py:39`).
- **Audio is best-effort.** Effects are synthesized into numpy buffers and played via
  `pygame.sndarray`; with no audio device the game runs silently.
- **The game agent is a behavioural model, not a biophysical one** — four hand-tuned LIF circuits
  chosen for readability and interaction latency. The scientific model is
  [§5](#5--the-real-fly-flygym--neuromechfly-pipeline).
- **FlyGym 2.1.0 sensor naming.** Per-leg ground-contact sensors reference element names that do
  not exist, so `real_fly_demo.py` composes with `add_ground_contact_sensors=False`; contact
  forces remain available through `get_bodysegment_contact_forces()`. Upstream issue.
- Verified on macOS (Python 3.14, pygame-ce 2.5.8, MuJoCo 3.9 / FlyGym 2.1.0 on Python 3.12);
  other platforms are untested.

## License

[MIT](LICENSE). Cited third-party projects and datasets remain under their own licences.
