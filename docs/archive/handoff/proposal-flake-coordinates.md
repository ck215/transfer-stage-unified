# Proposal: sample-relative flake coordinates ("Sample Map")

Feature request and design, no implementation. Read-only investigation of
`mvc-refactor` at `6b491ca`, 2026-10-04. Paths are relative to
`mvc-refactor/`. Tags: **VERIFIED (file:line)** means read in the tree;
**ASSUMED** means a bench fact I could not check and the owner must.
Written in the shape of `handoff/proposal-probe-zeroing.md`, which this
composes with (section 9).

The owner's idea, verbatim: "Even though we don't have zeroing functions
for samples yet, we can create markers while flake searching by having the
probe draw a coordinate plane for flake shape based on the corners of the
sample, then flag flake locations on a new relative coordinate plane kept in
the background. A future exfoliation db software could then allow for flake
searching using the same labeling system as the red percent function, but
now we can locate where on the chip a flake is, not just the cutting data."

Added at the lead's word the same day: a **guidance mode** for a rig with
no probes connected (manual micrometers), sharing the data model; a
record set the parallel "user system" server can ingest; and a flake
record that a database can sort by date, material, dimensions, thickness,
status and owner.

**Revised 2026-10-04 (later)** at the owner's word, in step with
`handoff/proposal-user-system.md`: flake `quality` and defect tags;
approximate thickness (`thickness_approx_*`, `layers_estimate`) separated
from exact AFM thickness (`thickness_afm_*`) (section 4.1a); the cut
descriptors `feature_height_nm` and `width_optical_um` on the Transfer
Map's trials, in the one store version 6 migration with `sample_id` /
`flake_uid` (section 4.3; the migration itself is specified in
`proposal-user-system.md` §7.3); two owner decisions recorded and removed
from the open list (section 11): **guidance only**, no model commands
another's motion until zeroing lands; **a separate `data/sample_map.sqlite`**.
Later the same day: the legacy data migration is specified in
`proposal-user-system.md` §11 and summarised in section 4.4 here
(`frame_source = "legacy"`, `legacy_ref`).

## 0. What the code says (the facts the design rests on)

| Fact | Finding | Tag |
|---|---|---|
| Position | Every probe-family model (`StepperProbe`, `DCProbe`, `ChuckPositioner`) publishes `position = (x, y, z)` as **integers in counts**, parsed from the firmware's unsolicited `POS:x,y,z` line at 10 Hz; `position_time`, `position_age`, `velocity` beside it. | VERIFIED `src/model/probe.py:668-689, 717-734, 963-975` |
| What a count is | Stepper: 8 microsteps per full step, 5 µm per full step, **0.625 µm per count** (owner-validated 2026-09-26). Chuck: 2 microsteps per full step; its µm per count is not in the tree. DC: encoder counts, scale unknown. | VERIFIED `README.md:98`, `docs/rebuild/STATUS.md:67-69`; chuck and DC scale ASSUMED |
| "Absolute" is boot-relative | The counter is the firmware's open-loop step count, zeroed at every port open (the Mega resets on DTR); the model starts at `(0, 0, 0)` and persists nothing. There is no commanded-position register, no travel limit on any axis (`x_dist` has no bounds), no homing (shelved by the owner, K5). | VERIFIED `probe.py:110-124, 167`; `handoff/proposal-probe-zeroing.md` §2; `docs/rebuild/BUGFIX_PLAN.md` K5 |
| Moves | Relative only: `step()` sends the twelve-field frame with `x_dist, y_dist, z_dist` and enters AUTO; arrival is observed (`STEP_SETTLE` 1 s of unchanged position). The firmware moves x, then y, then z in order; diagonal moves are "unverified" in the manual. Distances cannot be written while AUTO or MANUAL (`_MOTION_GATE`). | VERIFIED `probe.py:77, 147, 498-512, 839-859`; `README.md:101-104` |
| Red Percent's labels | `ANNOTATION_FIELDS`: `specimen_id` ("Specimen ID"), `consumable_id` ("Tip / Consumable ID"), `note`; plus `run_name` ("Run / Cut ID") which becomes `run_id` (default `run_%Y%m%d_%H%M%S`), `probe_name`, `probe_tilt_angle`. They are written to `<run_id>_station_meta.json` under `annotations` (intended) beside what the station did; the CSV `<run_id>_position.csv` is a plain rectangle: `t_s, red_percent, <axis>_position_steps, <axis>_velocity_steps_per_s, position_age_s`. Output root `TRANSFER_STAGE_DATA_ROOT` or `~/transfer-stage-runs`. | VERIFIED `src/model/red_monitor.py:270-298, 379-398, 1058-1144`; `src/model/plot_data.py:41-45` |
| Cutting data | The Transfer Map's SQLite store: `trials` (tilt, speed, marks, red extrema, AFM width and thickness, pictures, video, status), `profile`, `tips`. **No specimen or sample column**: a trial names its tip, not the chip or the flake it cut. `PRAGMA user_version` = 5, additive `ALTER TABLE` migrations, a connection per call and one write lock, pictures beside the file, CSV exports under `data/exports/`. | VERIFIED `src/model/transfer_map.py:83-136, 168-235, 1773-1807` |
| Store location | `STATION_MAP_DB`, else `<repo root>/data/transfer_map.sqlite` (beside the executable in a bundle); `/data/` and `handoff/` are git-ignored; the store is created on `open()`, never at construction. | VERIFIED `transfer_map.py:576-616`; `.gitignore` |
| Reading other models | A model learns of its peers through `on_model_added(name, model)` and reads them by **duck type**, never by class: Red Percent follows anything with `position` + `position_time`; the Transfer Map takes `subscribe` + `grab_frame` (Red Percent), `position_deg` (Rotator), `position` + `position_time` + `mode_name` (a probe). Models never import the Controller or a view. | VERIFIED `red_monitor.py:470-496`; `transfer_map.py:656-664`; `tests/test_architecture.py:26-37` |
| Views | A new model needs no view change if it uses the schema's element types: `readonly entry button toggle checkbox dropdown region_select file_save file_open plot image indicator log_stream internal`. A model returns PNG bytes for `image`; `plot_data` renders with matplotlib Agg. | VERIFIED `src/schema.py:43-47, 252-259`; `plot_data.py:577-589` |
| Screen | `Screen.grab(region)` returns the frame under an mss-shaped `{"left","top","width","height"}` (screen pixels); `screenshot_png` the whole desktop. Red Percent owns one; its `grab_frame()` returns the capture region as PNG. | VERIFIED `src/devices/screen.py:118-169`; `red_monitor.py:893` |
| Gamepad | The pad contract is sticks, triggers, hat and bumpers only (`NEUTRAL`, `EDGE_KEYS`). Face buttons are read into `_button_values` but published nowhere. The hat is XY step, the bumpers are Z step. The pad is claimed by one model (the probe). | VERIFIED `src/devices/gamepad.py:382-391, 529, 1059-1110`; `src/model/gamepad_input.py:262-316` |
| One dashboard | Red Percent is **hosted** on the Transfer Map's page (`HOST = "Transfer Map"`); it has no page of its own while the map is launched. | VERIFIED `red_monitor.py:237`; `docs/rebuild/MODEL_CONTRACT.md` step 1 |
| Zeroing | "No zeroing / homing function for now" (owner, 2026-09-26). The proposal that stays for later is host-side `set_zero` / `go_to_zero` with a persisted offset and a `zero_state` of `none / session / carried / homed`. | VERIFIED `STATUS.md:67-69`; `proposal-probe-zeroing.md` §6-7 |
| Optics and mechanics | Which axes move the sample under the objective (the chuck, presumably, since it holds the sample), whether the camera moves, the objectives, the field of view, the chip size, how the chip is held on the chuck, lead-screw backlash, whether the motorised stage also has hand knobs: **nothing in the tree.** | ASSUMED, section 11 |

## 1. Problem and user story

Today a flake found during a search exists only in the operator's memory
and in the camera's picture. Nothing records *where on the chip* it is, so
after the chip leaves the stage (AFM, storage, another rig) the search is
repeated by eye. The cutting record (a Transfer Map trial) names a tip and
a time, not the flake or the chip, so "which flake on which chip was cut at
12.5 deg and 300 steps/s" cannot be answered from data.

The station cannot give an absolute stage position (section 0: the counter
resets on every connect), and zeroing is shelved. What it can do is define
a frame **on the chip itself**: the chip's corners are physical, stable and
visible, and the operator can put the crosshair on each one. Every flake is
then a point in that frame, in micrometres, and the frame can be rebuilt
after any remount from the same corners.

The flake search, as the operator would do it:

1. Launch with the **Sample Map** row ticked, with the locating axes (the
   Chuck Positioner, presumably) and Red Percent. Mount the chip. Type the
   **Sample ID** (the same string as Red Percent's Specimen ID), the
   material and the substrate; they stay filled in.
2. Lower the magnification. Drive to corner A of the chip (the one nearest
   the scratch mark, or whatever the operator's convention is), centre the
   crosshair on it, let the stage come to rest, press **Mark corner A**.
   The sheet shows A's stage counts and a picture of the corner.
3. Same for B (along the long edge from A), then C and D. After B the
   sheet draws the frame; after three corners it reports the angle at A
   and the derived width and height; after four it reports how far from a
   rectangle the chip is. **Check corner A** (go back to A and mark it
   again) measures the closure: the stage's repeatability plus the
   operator's pointing, in micrometres. The sheet says "good", "check" or
   "poor".
4. Raise the magnification. Search. On a flake: centre it, let the stage
   rest, press **Flag flake**. The station stores its stage counts, its
   sample-frame (x, y) in µm, a picture of the capture region, the red
   percent if Red Percent is running, and a label `F01`, `F02`, … The
   operator may type material (defaults to the sample's), a layer estimate,
   a note, and mark the flake's extent (section 3.5).
5. Flakes appear on the **sample map** figure: the chip outline from the
   corners, the flakes as points with labels, the current position as a
   crosshair. The **Trials** page can later name the flake it cut.
6. Later: remount the chip, mark the same corners, pick a flake from the
   **Flake** dropdown, and the **Guidance** readout says how far and which
   way to move until the flake is in the field of view (section 8).
7. Export: one JSON record set and flat CSVs under `data/exports/`, keyed
   by `sample_id` and `flake_uid`, which the future exfoliation database
   ingests (section 12).

## 2. Corner registration UX

**How many corners.** Two define the frame (origin and x axis); the third
makes the frame checkable (the angle at A, the derived height); the fourth
measures the chip's non-rectangularity and over-determines the fit. The
proposal asks for **three minimum, four recommended**, and accepts two
with a "frame is unchecked" warning. A **closure mark** (A again) is the
one measurement of registration quality that does not depend on the chip
being rectangular; the sheet asks for it under Next step.

**How a corner is captured.** The optical **crosshair**, not the probe
tip: the tip is the consumable, works microns above the specimen, and a
touch at a chip corner risks it for nothing (the zeroing proposal's §4
applies). The corner's position is the locating axes' `position` at the
moment of the press. The press is refused while the stage moves
(`velocity` non-zero or `position_age` > 0.5 s or fewer than two POS
samples since the last change), while latched, and when no locating axes
are open. The 10 Hz position stream is exact at rest; marking while moving
would be wrong by up to one sample (40 counts, 25 µm, at 400 counts/s).

**Which control.** MVP: a key per corner on the sheet ("Mark corner A",
B, C, D, "Check corner A") and one "Flag flake" key. A gamepad button is
the natural control while jogging, but the pad contract has no face
buttons and the pad is claimed by the probe (section 0); it is a Phase 2
core change (section 10).

**Feedback.** A **Sample map** image (matplotlib, like the Transfer Map's
figures): the marked corners, the fitted frame's axes, the derived
rectangle, the live crosshair, the flakes. Readouts: "Corners: A B C —",
"Angle at A: 89.6 deg", "Width x height: 4.98 x 5.02 mm (derived)",
"Rectangularity: 7 µm", "Closure: 4 µm (good)". A picture of each corner
as marked, so the same corner can be recognised at the next mount. Next
step names the one thing to do ("Mark corner B along the long edge").

**A chipped corner.** Phase 2 adds **edge points**: mark two or more
points along each of the two edges that meet at the missing corner; the
station fits a line to each and intersects them, and the virtual corner
carries the fit's residual. The record stores the edge points as corners
labelled `A.1, A.2, …` and the intersection as `A` with
`method = "edge_intersection"`.

**A non-rectangular sample.** The frame needs only A and B (origin and
direction). A chip that is a parallelogram or an irregular quad is
registered the same way; the rectangularity number is then a property of
the chip, not an error. The operator sets **Shape**: rectangle (the
rectangularity residual is a quality check), quad (no shape check,
closure only), irregular (edge points, closure only).

**Re-marking.** Marking a corner that is already marked replaces it after
a confirmation that quotes the distance from the previous mark; the frame
refits.

## 3. The math

Everything here is a pure function in a new `src/model/sample_frame.py`
(no model, no store, no matplotlib), the way `transfer_map_analysis.py`
is, so every rule is testable on synthetic corners.

### 3.1 Frames

- **Stage frame G**: the locating axes' `(x, y)` in counts, boot-relative.
  Z is kept beside it, not in the fit.
- **Sample frame S**: origin at corner A; +x along A→B; +y perpendicular,
  toward the chip's interior (so C and D have positive y); right-handed
  looking down the objective; units µm.
- **Counts to µm**: `K = diag(k_x, k_y)`, one µm-per-count per axis from a
  per-model table (stepper 0.625; chuck and DC are owner facts) or typed.
  On the manual rig `k = 1000` µm per mm of micrometer reading.

### 3.2 The rigid frame from A and B (the default)

With `a = K (p_A)` and `b = K (p_B)` in µm:

    theta = atan2(b_y - a_y, b_x - a_x)
    sample <- stage:  q = R(-theta) K (p - p_A)
    stage <- sample:  p = p_A + K^-1 R(theta) q
    R(theta) = [[cos, -sin], [sin, cos]]

Handedness: with D marked, `h = sign((b - a) x (d - a))`; if `h < 0` the
stage frame is left-handed relative to the operator's corner order and S's
y axis is flipped (`q_y <- -q_y`), stored as `handedness = -1`. The same
flip appears if the camera mirrors the image; the figure draws the frame
the operator named, so the sign is visible.

Derived width `W = |b - a|`, height `H = |R(-theta)(d - a)|_y`, angle at
A from the dot product of `(b - a)` and `(d - a)`.

This fit is exactly determined: it has no residual of its own. Its quality
comes from two measurements:

- **Rectangularity** (shape = rectangle, four corners): the least-squares
  distance from the four marked corners to the best-fitting rectangle,
  solved as a 6-parameter fit (`theta, t_x, t_y, W, H` plus nothing else)
  over 8 observations: 2 degrees of freedom of residual. Reported in µm
  RMS. It mixes the chip's true shape with pointing error, so it is a
  check, not the quality number.
- **Closure**: `|p_A' - p_A|` in µm after the operator returns to A. It
  measures stage repeatability plus pointing, independent of the chip.

Quality words: closure under 10 µm "good", under 30 µm "check", else
"poor"; rectangularity over 50 µm with shape = rectangle asks whether the
shape should be quad. Thresholds are constants in `sample_frame.py` and
owner-adjustable.

### 3.3 The affine fit with known dimensions (the manual rig, or an untrusted scale)

When the operator types the chip's measured width and height (callipers,
or a diced die of known size), the ideal corners are known,
`Q = {(0,0), (W,0), (W,H), (0,H)}`, and the transform can be fitted:

    p_i = A q_i + t,  A a 2x2 matrix, 6 unknowns, least squares over the
    marked corners (3 minimum, 4 over-determined: 2 residual DOF).

The affine form absorbs an unknown or unequal µm-per-count, a non-square
stage, and non-orthogonal manual axes, which is why it is the right fit
for the micrometer rig (section 8). The residual is now meaningful in the
usual way (RMS over the corners). A similarity (`A = s R`) is the same
with `s` fitted and the axes forced orthogonal; the station offers rigid
(scale from the table), similarity (scale fitted) and affine, and
defaults to rigid when no dimensions are typed and affine when they are.

### 3.4 Degenerate cases (refused, with the operator's words)

`|A B| < 50` counts ("Corners A and B are the same point"); the angle at A
under 20 deg or over 160 deg ("B and D are nearly in line with A: not a
corner"); a corner marked while moving or stale; an affine fit from three
collinear points; a flake flagged before two corners exist (allowed, but
stored stage-only and shown hollow until the frame exists, then placed
retroactively, since the counts are from the same epoch).

### 3.5 Extent (the "flake shape")

Three sources, from cheapest to richest; the record stores which:

1. **Stage bounding box** (MVP): the operator jogs to two opposite corners
   of the flake and presses "Mark extent" twice. Lateral size and aspect
   ratio follow; no image calibration needed; works on both rigs.
2. **Traced polygon** on the flake's picture (Phase 2): needs µm per pixel
   and the image-to-stage rotation (3.6) and a polygon picker, which is a
   new schema element type and therefore a view change in all three views
   (the lead's).
3. **Red-mask contour** (Phase 3): Red Percent already thresholds red in
   the region; the largest connected component's contour is a candidate
   outline the operator accepts or rejects.

Area (shoelace), maximum Feret diameter and aspect ratio (ratio of the
minimum-area bounding box's sides) are computed from the polygon in µm at
read time, never stored in its place (the Transfer Map's rule for force).

### 3.6 Image calibration (µm per pixel) and where it comes from

Nothing in the tree knows the camera's magnification. Two sources:

- **Typed**, per objective, from a stage micrometer slide (the operator
  reads it once per objective and types "0.42 µm/px at 20x").
- **Measured by stage shift** (Phase 2, only with a locating axis): grab a
  frame, move +N counts in x, grab again, find the pixel shift by phase
  correlation (numpy FFT, sub-pixel); repeat in y. This gives µm per pixel
  (from `N k_x / shift_px`), the image-to-stage rotation and the mirror
  sign in one go, and it is what makes the viewfinder's guidance arrows
  point the right way on screen. It moves the stage, so it is confirmed
  and refused unless Z is at the registered travel height.

The calibration record names its source and the objective; a flake's
picture stores the calibration that was current, so a polygon can be
re-projected if the calibration is later corrected.

### 3.7 Error budget (per axis, approximate, stepper locating axes)

| Term | Size | Where it enters | Mitigation |
|---|---|---|---|
| Count quantisation | 0.6 µm | every position | none needed |
| Position sampling at rest | 0 | marking | refuse while moving |
| Crosshair pointing on a corner | 2–10 µm (2–5 px at a 10x objective; worse on a chipped or rounded corner) | every corner, every flake | check corner A; edge points for a bad corner |
| Lead-screw backlash, bidirectional | unknown, 5–50 µm is typical for a fine screw without preload | returning to a point from the other direction | approach every mark from the same direction (-x, -y); the closure mark measures what is left |
| Lost steps, hand-moved stage, coils off (the idle interlock sends `'d'` after 300 s) | unbounded | silently, any time | the registration is valid for one position epoch only (section 6); re-check A when in doubt |
| Rotation from corner pointing | σ_pick / |AB| ≈ 5 µm / 5 mm = 1 mrad → 5 µm at the far corner | every flake, grows with distance from A | mark B as far from A as the chip allows; use C and D in the fit |
| Remount | the new registration's pointing error again, plus any chip damage; no stage term, since the frame is rebuilt | re-location | the same corner identities, the corner pictures |

Expected re-location error: about **10–20 µm** in each axis on a 5 mm chip
with careful pointing, well inside a 10x field of view (about 1 mm) and
inside a 50x field (about 200 µm, ASSUMED). The acceptance criterion is
therefore "the flake is in the field of view at the search magnification
after guidance; the operator centres it by eye". Flake records then carry
the operator's *re-observed* position too, which is the measurement of
this budget on the real rig (section 8.3).

## 4. Data model

### 4.1 Records

Sample (`samples`): one per chip. `sample_id` **is** Red Percent's
`specimen_id` string; the operator types it once and both read it.

| field | type | who fills it |
|---|---|---|
| `sample_id` | text, primary key | user |
| `uid` | uuid4 | station |
| `material` | text (default for its flakes) | user |
| `substrate` | text ("SiO2 285 nm / Si") | user |
| `shape` | rectangle / quad / irregular | user |
| `width_um`, `height_um` | measured by hand, else null | user |
| `orientation_note` | how to find corner A | user |
| `exfoliated_at` | date | user |
| `created_at` | ISO | station |
| `owner` | text; the user system maps it to a user | user |
| `status` | active / stored / consumed / discarded | user |
| `note` | text | user |

Registration (`registrations`): one per mounting; the corner fit.

| field | type | who |
|---|---|---|
| `registration_id` | integer | station |
| `sample_id` | text | station (from the sheet) |
| `frame_source` | `stage:<model NAME>` / `manual:micrometer` / `legacy` (an imported record with no corners; always invalid; `proposal-user-system.md` §11) | station |
| `position_epoch` | integer, the locating model's port-open epoch; null for manual | station |
| `k_x_um`, `k_y_um` | µm per count (or per mm) used | station from the table, or user |
| `fit_kind` | rigid / similarity / affine | station (default) or user |
| `origin_stage_x`, `origin_stage_y` | counts (or mm) | station |
| `theta_rad`, `scale`, `a11 a12 a21 a22`, `t_x`, `t_y` | the transform, redundant forms kept together | station |
| `handedness` | +1 / -1 | station |
| `derived_width_um`, `derived_height_um`, `angle_a_deg` | from the corners | station |
| `rectangularity_um`, `closure_um`, `residual_rms_um` | nulls when not measured | station |
| `quality` | good / check / poor / unchecked | station |
| `z_travel` | the Z counts the operator registered at (the safe height) | station |
| `registered_at`, `invalidated_at`, `invalidated_reason` | ISO, text | station |

Corner (`corners`): the raw observations behind a registration.

| field | type | who |
|---|---|---|
| `registration_id`, `label` | `A`..`D`, `A.1`.. for edge points, `A'` for the check | station |
| `stage_x`, `stage_y`, `stage_z` | counts (or mm) | station (typed on the manual rig) |
| `method` | crosshair / typed / edge_intersection | station |
| `image_path` | the capture region at the mark | station |
| `marked_at` | ISO | station |

Flake (`flakes`): the marker.

| field | type | who |
|---|---|---|
| `flake_uid` | uuid4 | station |
| `label` | `F01`.. per sample; editable | station, user may rename |
| `sample_id` | text | station |
| `sample_x_um`, `sample_y_um` | the sample-frame position | station |
| `registration_id`, `stage_x`, `stage_y`, `stage_z` | the raw observation | station |
| `extent_kind` | none / bbox / polygon | station |
| `extent_source` | stage_corners / image_trace / red_mask | station |
| `extent_points_um` | JSON list of `[x, y]` in the sample frame | station |
| `image_path`, `image_region_px` | the picture and the screen region it was | station |
| `um_per_px`, `image_theta_rad`, `calibration_source` | typed / stage_shift / null | station |
| `red_percent`, `red_min`, `red_run_id`, `red_baseline` | Red Percent at the flag, when running | station |
| `quality` | integer 1–5, the operator's rating of the flake as a transfer candidate (4.1a); null = not rated | user |
| `defects` | JSON list from a controlled vocabulary: `cracks`, `bubbles`, `residue`, `folds`, `wrinkles`, `tears`; `[]` = inspected, none seen; null = not inspected | user |
| `layers_estimate` | integer or null; the **approximate** layer count | user, or station when a calibration exists (owner question 8) |
| `thickness_approx_nm`, `thickness_approx_method`, `thickness_approx_source` | the **approximate** thickness from optics; method `optical_contrast` / `red_percent` / `colour` / `eye` / `raman`; source names what it rests on (the `red_run_id`, a calibration curve's name, "eye"). Replaces the earlier `thickness_nm` / `thickness_method`. | user; station fills `red_percent` from the flag when a calibration exists |
| `thickness_afm_nm`, `thickness_afm_sigma_nm`, `afm_measured_at`, `afm_by`, `afm_file` | the **exact** thickness once AFM is done; `afm_file` is a path or reference to the AFM data, kept by hash like a picture; all null until measured | user, or flows back from a linked trial's `thickness_nm` / `thickness_sigma_nm` at AFM attach |
| `material` | defaults to the sample's | user |
| `status` | candidate / selected / transferred / consumed / discarded | user; "transferred" set by a trial link |
| `owner` | text | user |
| `searched_at` | ISO | station |
| `transferred_at` | ISO; the linked trial's `started_at`, else typed | station / user |
| `note` | text | user |
| `trial_ids`, `run_ids`, `tip_ids` | JSON lists (the joins) | station |

Observation (`observations`, Phase 2): every re-location of a flake under
a later registration: `flake_uid, registration_id, stage_x, stage_y,
predicted_x, predicted_y, error_um, observed_at`. This is the measured
error budget and the input to the re-fit of section 8.3.

Derived at read time, never stored: `area_um2`, `lateral_um`,
`aspect_ratio`, the sample-frame position from the stage one (when the
registration is re-fitted, the flakes move with it), and the one-number
thickness `thickness_best_nm` / `thickness_is_afm` (4.1a).

#### 4.1a Quality and thickness (owner, 2026-10-04)

**Quality** is the operator's judgement of the flake as a transfer
candidate, never computed: 5 = clean and uniform over the whole extent,
no defect seen; 4 = minor defects outside the region that would be used;
3 = the usable region is reduced by defects; 2 = marginal, a last resort;
1 = reference only, not for transfer. `defects` names what was seen, from
a controlled vocabulary the server also holds (`cracks`, `bubbles`,
`residue`, `folds`, `wrinkles`, `tears`; owner question 14 may change
it); `[]` means inspected and clean, null means not inspected. Both are
set at the flag or edited later; both travel in the export.

**Thickness** is two things and the record keeps them apart. The
*approximate* thickness comes from optics at the flag or soon after:
`layers_estimate` (an integer), `thickness_approx_nm`, with
`thickness_approx_method` naming how (`optical_contrast`, `red_percent`,
`colour`, `eye`, `raman`) and `thickness_approx_source` what it rests on
(the `red_run_id`, the name of a contrast calibration curve, "eye"). The
*exact* thickness comes from AFM, usually after the cut:
`thickness_afm_nm`, `thickness_afm_sigma_nm`, `afm_measured_at`,
`afm_by`, and `afm_file` (a path or reference to the AFM data, stored by
hash like a picture). A linked trial's AFM attach (`trials.thickness_nm`,
`thickness_sigma_nm`) fills the AFM fields, never the approximate ones;
nothing ever copies one into the other. A reader wanting one number takes
`thickness_afm_nm` when present, else `thickness_approx_nm`, and says
which (the server materialises this as `thickness_best_nm` /
`thickness_is_afm` for sorting and offers "AFM-confirmed only" as a
filter, `proposal-user-system.md` §4.4, §4.7). These replace the earlier
`thickness_nm` / `thickness_method`; no store exists yet, so there is no
migration and `flake-coords/1` is not bumped.

### 4.2 The store

A new SQLite file, `data/sample_map.sqlite`, owned by the new model, with
the Transfer Map's conventions exactly: `PRAGMA user_version` starting at
1, `_CREATE` with `IF NOT EXISTS`, additive `ALTER TABLE` migrations in
`_migrate`, a connection per call and one write lock, created on `open()`
with a "Database Ready" line, `STATION_SAMPLE_DB` and a `--sample-db`
flag overriding it, pictures in `data/sample_map/<sample_id>/`. A separate
file rather than tables in `transfer_map.sqlite`, so the two models'
migrations never cross and either store can be moved to the server alone
(**owner ruling 2026-10-04**: a separate `data/sample_map.sqlite`,
following the Transfer Map store conventions, joined via `sample_id` /
`flake_uid`).

### 4.3 The joins to red-percent and cutting data

- `samples.sample_id` = Red Percent `annotations.specimen_id` (the
  sidecar JSON) = the proposed `trials.sample_id`.
- `flakes.tip_ids` and `trials.tip_id` = Red Percent `consumable_id` =
  `tips.tip_id`.
- A trial names its flake: **Transfer Map store version 6** adds
  `sample_id TEXT` and `flake_uid TEXT` to `trials`, filled from a "Flake
  being cut" dropdown on the trial sheet (the Sample Map publishes
  `flake_options`; the map reads it by duck type as it reads
  `position_deg`). Arming on a flake sets its status to "transferred" and
  its `transferred_at`. Until version 6 lands the link is the operator's
  `run_name` / `note`.
- The same version 6, **one migration** (specified statement by statement
  in `proposal-user-system.md` §7.3), also adds the cut descriptors the
  owner asked for on 2026-10-04: `feature_height_nm` and
  `feature_height_sigma_nm` (the cut's feature height by AFM; its exact
  definition is owner question 13), and `width_optical_um`,
  `width_optical_sigma_um`, `width_optical_method` (an approximate channel
  width by optical microscopy), beside the existing `width_um` (the
  channel width by AFM, documented as `width_afm_um`; the column is not
  renamed, it is an existing store) and `thickness_nm` (the sample
  thickness by AFM at the cut, documented as `thickness_afm_nm`). The
  Transfer Map's figures prefer the AFM width and show the source. A
  linked trial's AFM thickness flows back to the flake's `thickness_afm_*`
  (4.1a); its AFM width, feature height and optical width stay on the
  trial and are reached from the flake through `trial_ids`.
- Red Percent's `run_name` ("Run / Cut ID") is free text today; the Sample
  Map proposes the convention `<sample_id>/<flake label>/<n>` and fills it
  when a flake is selected, so the run folders sort by chip and flake.

### 4.4 Export and import

`Export sample map` (a `file_save`, Phase 1) writes under
`data/exports/`:

- `sample_map_<stamp>.json`: the record set of section 12, one document.
- `sample_map_<stamp>_samples.csv`, `_registrations.csv`, `_corners.csv`,
  `_flakes.csv`: the same rows flat, JSON columns as JSON strings.

`Import sample map` (a `file_open`) reads the JSON back, merging by
`uid` / `flake_uid` (newer `updated_at` wins), so two rigs' exports can be
combined by hand before the server exists.

**Legacy data** (owner, 2026-10-04: "consider how to migrate the legacy db
into the new infra"): there are no legacy flake or registration records
anywhere; the only chip identifiers on disk are Red Percent's
`annotations.specimen_id` strings and the Transfer Map's `tip_id` / `note`
text, and the only structured research data is the bench PC's
`transfer_map.sqlite` (versions 1-5, since 2026-09-27) with its run
folders. The inventory, the field mapping and the importer are
`proposal-user-system.md` §11, shared by both proposals. What it asks of
this record set: a legacy sample becomes a `samples` row with
`legacy_ref`; a legacy cut becomes a `flakes` row **only** when the owner
maps it by hand (a mapping sheet, never guessed from free text), with
`status = transferred`, no `sample_*_um`, no `stage_*`, `extent_kind =
none`, and a reference to one synthetic registration per legacy sample
whose **`frame_source = "legacy"`** (a new value beside `stage:<Model
NAME>` and `manual:micrometer`), no corners, no transform, `quality =
"unchecked"`, invalidated at import ("legacy import: no corners were
marked"), so guidance is withdrawn for it until the chip is registered for
real. `legacy_ref TEXT` is an additive, nullable field on `samples`,
`registrations` and `flakes`, carried by `flake-coords/1`. A trial's
`thickness_nm` flows to the flake's `thickness_afm_*` with `afm_by =
legacy`; old `width_um` values are assumed AFM and flagged on the server
(`trial_links.width_provenance = "assumed_afm"`).

## 5. Architecture

### 5.1 Model

`src/model/sample_map.py`: `class SampleMap(Model)`, `NAME = "Sample
Map"`, `IDENTITY = None`, `RESOURCES = ()`, `HOST = None` (its own page:
searching is a different activity from cutting, and Red Percent is
already hosted by the map). It owns one `Screen` device of its own and
its own capture region, so the viewfinder works with no Red Percent and
on a probe-less rig; when Red Percent is open it reads `current_red`,
`red_min`, `run_id` and `region` from it by duck type. `devices = [screen]`,
`_expects_heartbeat` False, `is_active` False always (it moves nothing and
records nothing continuously), `_halt_hardware` returns True. Registered
with `Setup.register` after `TransferMap`.

Peers, by duck type in `on_model_added` (the pattern of
`transfer_map.py:656-664`):

- locating axes: any model with `position`, `position_time`,
  `position_age` and `velocity`; a "Locating axes" dropdown like Red
  Percent's "Position Source", Controller order, remembered per sample;
- Red Percent: `current_red` + `region` + `grab_frame`;
- the Transfer Map (Phase 2): nothing read; the map reads the Sample
  Map's `flake_options` / `selected_flake`.

Commands (all schema-declared; `NeedsConfirm` where a value is replaced):
`set_source(name)`, `set_sample(...)` (the sheet's entries travel as
inputs), `mark_corner(label)` (buttons share it through `args`),
`check_corner()`, `mark_edge_point(label)` (Phase 2), `clear_corners()`
(confirm), `flag_flake()` (inputs: material, layers, note), `mark_extent()`,
`select_flake(label)`, `set_flake_status(...)`, `delete_flake(label)`
(confirm), `calibrate_um_per_px()` (Phase 2, confirm, moves the stage),
`type_reading(x_mm, y_mm)` (manual rig), `export_json`, `export_csv`,
`import_json`, data commands `viewfinder_image`, `sample_figure`,
`corner_image`, `flake_image`, `flakes_log`, `samples_log`.

New pure modules: `src/model/sample_frame.py` (3.1–3.5) and, additive in
`src/model/plot_data.py`, `sample_map_request` + `render_sample_figure`
(the figure of section 2) following `transfer_request`.

### 5.2 Controller

None. The Controller fans `on_model_added` out already
(`controller.py:54-56`) and serialises commands per model
(`controller.py:165-175`).

### 5.3 Views

None for Phase 1: every control is an existing element type. Two small
core additions the model cannot make itself, filed as CORE CHANGE
REQUESTS the way the Transfer Map filed "armed":

- `views/base.GATE_WORDS`: `"unregistered": (None, "Mark corners A and B
  first")`, `"no_source": ("No locating axes", None)`; and
  `Panel.GATE_REASONS` for the same tokens on the model.
- Phase 2 only: a `polygon_select` element type (3.5) and the gamepad mark
  channel (section 10).

### 5.4 The background store's lifecycle

Construction creates nothing. `open()` ensures the store and announces it.
Marks and flags write immediately (one transaction each); there is no
unsaved state, so a crash loses nothing but the mark in flight. Closing
the tab destructs the model (owner ruling: close = destruct) and the store
is simply reopened next time; the registration's validity survives in the
file through `position_epoch` (section 6), not in memory. `estop` does
nothing to the store. A "New sample database" command mirrors the map's.

### 5.5 Composing with zeroing (when it lands)

Zeroing, as proposed (option F), is a host-side **offset**: `zeroed =
raw - offset`, with `zero_state` naming how trustworthy the offset is.
Rules so the two features never fight:

1. The Sample Map stores and fits **raw counts** always (the firmware's
   number), plus the `position_epoch` of the moment. A zero is a different
   frame, Z (zeroed stage), related to G by a translation (and, with a
   fiducial, a rotation): `z = T(g)`. S is defined from physical corners,
   so `S <- G` composes with `G <- Z` and sample coordinates are invariant
   under any zero change. Nothing in the flake record depends on the zero.
2. When a zero exists, the Sample Map also stores `zero_offset` at each
   mark, so it can show positions in the operator's zeroed numbers and
   can translate a registration across a zero change *within one epoch*.
3. If zeroing one day gives a true session-stable stage frame (home
   switches, option D), the registration becomes **portable across
   epochs**: a remount still needs new corners (the chip moved), but a
   reconnect no longer invalidates the frame. That is the one line to
   change: the invalidation rule reads `zero_state == "homed"` as "same
   epoch".
4. Go-to-zero and a future Go-to-flake would share one move path (section
   7); the Sample Map never implements its own. **Owner ruling 2026-10-04:
   guidance only for now; automatic go-to is revisited after zeroing lands.**

## 6. Validity: the position epoch

A registration is a relation between the chip and **one run of the
firmware's counter**. The counter restarts at every port open, so the
model must know when that happened. Proposed, a small core change to
`Probe` (`probe.py`): `position_epoch`, an integer incremented every time
the port completes a handshake (the model sees it through
`port.identity` / `status` turning `verified` again) and published in
`state`. The Sample Map stores it with every corner and flake and marks a
registration **invalid** when the locating model's epoch changes, when the
model is removed (`on_model_removed`), or when the operator says so. An
invalid registration keeps every record (the sample-frame coordinates are
still right); only the stage-frame numbers and the guidance are withdrawn
until the corners are marked again. Without the epoch (Phase 1 before the
core change), the Sample Map falls back to invalidating on
`on_model_removed` and on a position jump larger than 2000 counts between
two consecutive 10 Hz samples while no move is in flight, and says so.

The idle interlock (coils off after 300 s) and any hand-turned knob
desync the counter without an epoch change. The sheet therefore shows
"Last checked: 14 min ago" and Next step suggests "Check corner A" after
the interlock has fired or after 20 minutes, both constants.

## 7. Re-location: remount the chip, drive to flake N

1. Mount the chip; launch; pick the sample from **Sample** (its corners'
   pictures and `orientation_note` are shown so A is identified the same
   way).
2. Mark A, B (C, D). The sheet reports the fit against the previous
   registration's derived width, height and angle: a chip whose derived
   width differs by more than the pointing budget was probably marked at
   the wrong corners ("Width 5.02 mm here, 4.98 mm last time: check that
   A and B are the same corners").
3. Pick flake N. The **Guidance** readout (section 8.2) shows the stage
   delta from the current position: in counts for the gamepad operator,
   with a distance-to-target that counts down as they jog; the viewfinder
   draws an arrow when the image calibration exists.
4. **Go to flake**: **owner ruling 2026-10-04, guidance only for now.** No
   model commands another model's motion; the operator drives to the
   flake by the Guidance readout and the arrow, and automatic go-to is
   revisited after the zeroing feature lands. The sketch is kept so its
   constraints are not lost for that day: the Sample Map would write
   `x_dist`, `y_dist` (never `z_dist`) on the locating model and call its
   `step()`, after a `NeedsConfirm` that quotes the move ("Move the Chuck
   Positioner X +1234, Y -560 counts to F03? Z stays at 0."), and only
   when Z is at or above the registration's `z_travel`, the model is in
   IDLE (distances are locked in AUTO and MANUAL), the latch is clear and
   the registration is valid. The firmware runs x then y in order, so no
   diagonal move is sent. It would be the first time one model commands
   another's **motion** (today a model only ever starts another's run,
   `transfer_map.py:674-703`), and it bypasses the Controller's per-model
   command lock (`controller.py:165-175`), which is why it waits for a
   motion path of its own, and for zeroing.
5. When the flake is in view, press **Flake in view**: the station stores
   the observation (4.1) and reports the error against the prediction.
   This number, accumulated over sessions, is the real error budget.

Expected accuracy from 3.7: the flake lands within about 10–20 µm of the
prediction when both registrations were "good"; the operator then centres
by eye. Larger errors point at the wrong corner identity, a damaged
corner, or an epoch change nobody noticed.

## 8. Guidance mode for a rig without probes

### 8.1 Position source without probes

There is none: no model exposes an XY position except the probe family;
the Rotator has `position_deg` only (VERIFIED `transfer_map.py:660`). The
screen capture still works (a `Screen` needs no board), and Red Percent
runs with no probe (it refuses nothing without a source, it just logs no
position). So the manual rig has pictures and red percent, and positions
only from what the operator **reads off the micrometers and types**.

The Sample Map therefore has a second kind of locating source,
`manual:micrometer`: two entries "Reading X (mm)" and "Reading Y (mm)"
and a key **Use these readings**, which sets the model's "current
position" in mm. Every corner and flake command then reads that instead
of a model's `position`; the records are identical except for
`frame_source`, `k = 1000` and `method = "typed"`. Phase 2 adds click
capture: a frozen picture with the crosshair where the operator clicks is
no help on its own (the stage does not know where the picture was taken),
but the **stage-shift image calibration** cannot run either without
motion; on the manual rig the calibration is typed from a stage
micrometer slide, or measured once by turning a micrometer a known amount
and clicking the same feature twice (the same phase-correlation code, the
motion supplied by the operator).

Image-feature registration (no corners at all: match the live frame to a
stored overview mosaic) is Phase 3 and needs a mosaic first; it is listed,
not designed.

### 8.2 Guidance UX (shared by both rigs)

Pick a flake; the readout block updates on every position change:

    Target F03     sample (1 234.5, 2 870.0) µm
    Move X  +1.234 mm   ->   right   2.47 turns
    Move Y  -0.560 mm   ->   up      1.12 turns
    Distance to target   1.355 mm

- Units follow the source: counts for a probe model, mm for the
  micrometer, both with the µm equivalent.
- Turns need the micrometer's pitch (mm per turn) and the direction sense
  of each knob; both are typed once per rig under Configure Sample Map
  (0.5 mm per turn is the common head, ASSUMED; owner question).
- Direction words, not glyphs that render differently per toolkit: left /
  right / up / down are **stage** directions; the viewfinder arrow shows
  the **screen** direction once the image calibration exists (3.6),
  since the camera may be rotated or mirrored.
- Backlash advice is part of the guidance: "Approach from the left
  (increasing X)": the model computes the final approach direction from
  the registration's convention and tells the operator to overshoot and
  come back when the delta's sign is against it.
- A distance under the field of view (from the µm-per-px calibration and
  the region size, else a typed FOV) turns the line to "In the field of
  view: look for it" and offers **Flake in view**.

### 8.3 Closed loop

- **Flake in view** stores the observation and the error (section 7).
- **Red percent feedback** (optional, when Red Percent runs): the
  viewfinder shows the live red percent; a step up as the operator closes
  in is a cue, not a proof. No automation on it.
- **Re-fit from confirmed flakes**: with two or more observations under
  the current registration, the station offers to **refine the frame**: a
  least-squares fit over corners and confirmed flakes together (the
  flakes' sample coordinates come from an earlier good registration), each
  weighted by its pointing σ. The corners stay the definition of the
  frame; the refinement adjusts the current `stage <- sample` transform
  only and records `refined_from = [flake_uids]`. On the manual rig this
  is what absorbs a micrometer's zero shift during a session.

### 8.4 Error budget, manual positioning

| Term | Size | Mitigation |
|---|---|---|
| Micrometer graduation | 10 µm per division; 1 µm with a vernier head | read to half a division |
| Reading and typing error | one division, occasionally a whole turn (0.5 mm) | the guidance rejects a reading that jumps more than 2 turns from the last and asks |
| Backlash of the micrometer screw and the stage's spring return | 10–50 µm (ASSUMED) | always approach from the same direction; the closure mark measures it |
| Non-orthogonal or unequal axes | fixed per stage, up to a few mrad | the affine fit with typed chip dimensions (3.3) absorbs it |
| Pitch error of the screw | 10^-3 relative | the fit absorbs scale when dimensions are typed |
| Operator zero shift (knob slips, stage bumped) | unbounded | re-fit from confirmed flakes; re-check A |

Expected: 20–50 µm per axis with a vernier head and typed dimensions,
inside any search field of view.

### 8.5 One data model for both rigs

Nothing in a flake record is rig-specific except the raw observation
(`registration_id`, `stage_*`, `frame_source`). The sample-frame
coordinates in µm are the portable truth; a flake flagged on the probe rig
is located on the manual rig by registering the same corners there (typed
readings, affine fit with the chip's dimensions) and reading the guidance;
the reverse works the same way. The requirement that makes it work is
social, and the record enforces it: the **same corner identities** (A is
A), which is why every corner stores its picture and every sample its
`orientation_note`.

## 9. Scope and phases

| Phase | Delivers | Needs |
|---|---|---|
| 0 | `sample_frame.py` and its tests (3.1–3.5) | nothing; pure functions |
| 1 (MVP) | `SampleMap` model, store, corners A–D and the check, flakes with label, sample and stage coordinates, picture, red percent, bbox extent, the sample figure, guidance readout in counts or mm, the typed-readings source, JSON/CSV export and import | Phase 0; `Setup.register`; the two `GATE_WORDS` lines (lead) |
| 2 | Viewfinder image with crosshair and screen arrow; µm-per-px by stage shift; edge points for a chipped corner; traced polygon; `position_epoch` on the probe; the gamepad mark channel; observations and the frame refinement; `trials.sample_id` + `flake_uid` (map store version 6) and the "Flake being cut" dropdown; `run_name` convention | core changes (section 10), one new schema element type (views) |
| 3 | red-mask candidate outline; image-feature registration; sync to the user-system server. Go to flake (model-to-model motion, safe-Z interlock) is **deferred by ruling** (2026-10-04: guidance only until zeroing lands) | the server's API |

Risks: the chuck's µm per count is unknown (every µm number depends on
it); backlash may dominate the budget; corner identity across remounts is
an operator discipline the software can only support; the first real
chip may not have four usable corners; a model commanding motion, when
go-to returns, is a new category for the safety review; one pad, two
consumers of its buttons.

## 10. Core change requests (not in the model's write set)

1. `src/devices/gamepad.py`: a `mark` edge channel in `NEUTRAL` and
   `EDGE_KEYS`, bound in every `LAYOUTS` row (Xbox A; T.16000M trigger or
   button 1, owner's call); `src/model/gamepad_input.py`: broadcast
   drained edges to subscribers (`subscribe_edges(fn)`), so the Sample
   Map receives the press from the probe that owns the pad. The jog
   packet reads named keys only, so the bytes on the wire are unchanged
   and the golden gate stays green.
2. `src/model/probe.py`: `position_epoch` (section 6).
3. `src/views/base.py` `GATE_WORDS` and `src/panel.py` `GATE_REASONS`: the
   two tokens of 5.3.
4. `src/model/transfer_map.py`: store version 6 (`sample_id`, `flake_uid`,
   and the cut descriptors `feature_height_nm`, `feature_height_sigma_nm`,
   `width_optical_um`, `width_optical_sigma_um`, `width_optical_method`;
   the full migration, UI fields and figure rule are
   `proposal-user-system.md` §7.3), the "Flake being cut" dropdown, the
   "Optical measurement" section and `attach_optical`, the new columns in
   the trials export, and `plot_data`'s width axis preferring AFM over
   optical with the source shown.
5. `src/app.py`: `--sample-db PATH` (sets `STATION_SAMPLE_DB`).
6. Phase 2 views: a `polygon_select` element type, all three renderers.
7. Deferred by ruling (2026-10-04, guidance only): a path for
   model-to-model motion commands, when automatic go-to returns after
   zeroing.

## 11. Owner decisions (bench facts I could not verify)

1. **Which axes locate a flake under the objective**: the Chuck Positioner
   (the sample moves, the camera is fixed) or a probe (the tip or camera
   moves over a fixed sample)? Does anything move the camera? The design
   is agnostic; the default in the dropdown should be the right one.
2. **µm per count** on the chuck's X and Y (and the DC probe's), and
   whether X and Y use the same screw. Measure: move 1000 counts against a
   stage micrometer slide.
3. **Backlash** of the locating axes in each direction, and whether the
   motorised stage also has hand knobs (any hand move breaks the frame
   silently).
4. **The chip**: typical size, diced (rectangular) or cleaved, how it is
   held on the chuck (an edge stop makes remount translation small), and
   how corner A is identified (scratch, asymmetry, a marker pen dot).
5. **Objectives** in use for searching, and the field of view or µm per
   pixel of each (the acceptance criterion and the calibration table).
6. **Crosshair or tip**: the proposal recommends the optical crosshair for
   every mark and never touching the tip to a corner.
7. **The manual rig**: which stage, micrometer pitch (mm per turn),
   graduation, vernier, and the direction sense of each knob.
8. **Thickness**: is there a red-percent (or contrast) to layer-count
   calibration for the lab's materials and substrate? If so, the station
   can fill `layers_estimate` and `thickness_approx_nm` with
   `thickness_approx_method = red_percent`; if not, it stores the red
   value and the operator's estimate separately.
10. **The gamepad mark button**: which button, and now (Phase 2) or
    later?
12. **Trials name their flake**: approve store version 6, now the one
    migration of `proposal-user-system.md` §7.3 with the cut descriptors.
13. **Feature height**: the exact definition of `feature_height_nm` for a
    cut: the AFM step height from the substrate to the top of the
    transferred channel, the depth of the trench the tip left in the
    flake, or both (two columns)? And its sign convention.
14. **Quality**: accept the 1–5 scale of 4.1a and the defect vocabulary
    (`cracks`, `bubbles`, `residue`, `folds`, `wrinkles`, `tears`), or
    name the lab's own; may a flake be rated before it has an extent?
15. **Optical channel width**: how it is measured today (pixels on the
    capture-region picture with the Sample Map's `um_per_px`, an eyepiece
    reticle, the vendor viewer's measuring tool), which fixes the
    `width_optical_method` vocabulary; and whether the Transfer Map's
    slice may use optical widths at all (proposed: AFM only by default,
    optical on request with a larger default sigma).

Decided by the owner, 2026-10-04 (removed from the open list above; the
numbers 9 and 11 are kept free so earlier references still resolve):

- **9, go-to: guidance only for now.** No model commands another model's
  motion. Automatic go-to is revisited after the zeroing feature lands
  (sections 5.5, 7, 9, 10).
- **11, the store: a separate `data/sample_map.sqlite`**, following the
  Transfer Map store conventions, joined via `sample_id` / `flake_uid`
  (section 4.2).

Decided by the owner, 2026-10-04 evening: see `proposal-user-system.md` §10.1a (Q16–Q19 answer decisions 8, 13, 14 and 15 here; decision 6: crosshair only; decision 12: store v6 approved). Still open here: 1–5, 7, 10.

## 12. Interface contract for the future exfoliation database

Only the contract; the server is the user-system proposal's. The export
is one JSON document, independent of where it is stored:

    {
      "schema": "flake-coords/1",
      "exported_at": "2026-10-04T15:40:12",
      "station": {"name": "...", "software_version": "..."},
      "samples": [ {sample record} ],
      "registrations": [ {registration record, "corners": [ ... ]} ],
      "flakes": [ {flake record, "observations": [ ... ]} ],
      "images": [ {"path": "...", "sha256": "...", "flake_uid" | "corner": ...} ]
    }

Rules the server can rely on:

- Field names and types are those of section 4.1; every record carries
  `created_at` / `updated_at` (ISO 8601, local time, as the station writes
  everywhere) and the station's `uid` / `flake_uid` (uuid4) as the merge
  key. `sample_id` and `label` are human keys and may be renamed; uids
  never change.
- Coordinates: `sample_*_um` are the canonical position; `stage_*` are
  raw counts or mm as marked, meaningful only with their
  `registration_id`, `frame_source` and `position_epoch`.
- Joins: `sample_id` ↔ Red Percent sidecar `annotations.specimen_id` and
  `trials.sample_id` (store 6); `flake_uid` ↔ `trials.flake_uid`;
  `tip_ids` ↔ `tips.tip_id` / `consumable_id`; `run_ids` ↔ Red Percent
  `run_id` folders.
- Derived numbers (`area_um2`, `lateral_um`, `aspect_ratio`) are not in
  the document; the server computes them from `extent_points_um`, as the
  station does.
- Images are files beside the document, referenced by path and hash;
  the document never embeds them.
- The schema string is bumped only for an incompatible change; additive
  fields do not bump it (the Transfer Map's rule).
- Legacy records (`proposal-user-system.md` §11) carry `legacy_ref` and
  reference a registration with `frame_source = "legacy"`; a reader treats
  that registration as permanently invalid and the flake as having no
  position. `legacy_ref` is additive and nullable on `samples`,
  `registrations` and `flakes`.
- Sorting and filtering the owner asked for maps to: date range
  (`exfoliated_at`, `searched_at`, `transferred_at`), material
  (`flakes.material`, `samples.material`, `substrate`), dimensions
  (derived from `extent_points_um`; `extent_kind` says how good),
  thickness (approximate: `layers_estimate`, `thickness_approx_nm`,
  `thickness_approx_method`, `red_percent`; exact: `thickness_afm_nm`,
  `afm_measured_at`, `afm_by`; "AFM-confirmed only" is `thickness_afm_nm
  IS NOT NULL`; the server derives `thickness_best_nm` / `thickness_is_afm`
  as its one sortable thickness column), `quality`, `defects`, `status`,
  `owner`; and, through `trial_ids`, the cut descriptors of store version
  6 (`width_um` as the AFM channel width, `feature_height_nm`,
  `width_optical_um`), which the server receives from the Transfer Map's
  trials export, not from this document.
- Thickness names: `thickness_approx_*` is what optics gave,
  `thickness_afm_*` is what AFM gave; neither is ever written into the
  other, and a record with both keeps both. The Transfer Map's `trials`
  keeps its own `thickness_nm` (the AFM sample thickness at the cut,
  documented as `thickness_afm_nm`); a linked trial fills the flake's
  `thickness_afm_*` and nothing else.

## 13. Test plan

Unit (`tests/test_sample_frame.py`, pure, no model):

- Round trip stage → sample → stage is identity to 1e-9 for random rigid,
  similarity and affine transforms.
- Synthetic rectangle under random rotation and translation, 3 and 4
  corners, noise 0, 1, 5 counts: the fitted theta and origin are within
  the noise; the rectangularity RMS tracks the noise; the closure reads
  the injected return error.
- A remount rotated by 90, 180, 270 degrees and mirrored gives identical
  sample coordinates for the same flakes; handedness is detected.
- Degenerate: coincident A and B; collinear A, B, D; three collinear
  points for affine; a frame from two corners is "unchecked".
- Edge points: two lines from 2–5 noisy points each intersect within the
  budget; a chipped corner is reconstructed.
- Affine with typed dimensions absorbs unequal `k_x`, `k_y` and a 20 mrad
  non-orthogonality; the rigid fit on the same data shows the error.
- Manual units: mm readings with `k = 1000` give the same sample
  coordinates as counts with `k = 0.625` for the same physical corners.
- Guidance arithmetic: deltas, turns, approach direction, "in the field
  of view".
- Extent: area, Feret, aspect ratio on known polygons; a bbox from two
  stage corners.

Model (`tests/test_sample_map.py`, fakes as `test_transfer_map.py` uses
them; `tests/test_model_contract.py` must pass unchanged):

- The store is created on `open()` only; `STATION_SAMPLE_DB` overrides;
  an older file is upgraded in place.
- Mark refuses while moving, stale, latched, without a source, and with
  the operator's words; marking an existing corner asks and quotes the
  distance.
- A flake flagged after two corners has both frames; before them it is
  stage-only and gets placed when the frame exists.
- Epoch change, model removal and a position jump invalidate the
  registration and withdraw guidance; the records stay.
- Red percent and picture are captured when Red Percent runs, null
  otherwise.
- `thickness_approx_*` and `thickness_afm_*` are independent: setting one
  never touches the other; a linked trial's AFM attach fills
  `thickness_afm_nm`, `thickness_afm_sigma_nm`, `afm_measured_at` and
  `afm_by` and nothing approximate; `quality` outside 1–5 and a defect
  outside the vocabulary are refused with the operator's words; a flake
  with both thicknesses exports both.
- Export and import round-trip the record set; merge by uid.
- The typed-readings source produces identical records with
  `frame_source = manual:micrometer`.
- The figure renders to PNG for 0, 2, 4 corners and n flakes, never in
  the stop red.

Hardware checks (Ian, at the bench; none can be done headless):

1. µm per count on each locating axis against a stage micrometer slide
   (owner question 2).
2. Backlash: approach a corner from both directions ten times; record the
   spread.
3. Closure repeatability: mark A, drive around the chip, check A; ten
   repeats.
4. A real remount: register, flag three flakes, remove and remount the
   chip, register, guide to each flake; record the errors (the
   observations table).
5. The camera's rotation and mirror against the stage axes (the
   stage-shift calibration, or by eye).
6. On the manual rig: pitch, graduation, backlash, and the same remount
   test with typed readings.

## Sources

Besides the tree: the TMC2209 and stage facts in
`handoff/proposal-probe-zeroing.md` and its sources; the owner rulings in
`docs/rebuild/STATUS.md`; the model recipe in
`docs/rebuild/MODEL_CONTRACT.md`; the store conventions in
`src/model/transfer_map.py`.
