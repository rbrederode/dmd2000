# LAB profiles and observation fitting

`src/util/lab_profiler.py` contains the LAB Gaussian extractor, single-profile
plotting, observation comparison and Galactic offset sweeps. It reads the local Galactic CAR cube (`lab.fit`).

Run from the repository root with Python, NumPy, Astropy and Matplotlib installed.
The default cube path is `~/github/lab-fit/lab.fit`; override with `--lab-fits`.
SciPy is also required for the `connected` and `erfc` terminal methods.

## Current defaults and the different velocity ranges

All velocity limits below are in km/s. These settings serve different purposes:

| Option | Default | Purpose |
|---|---|---|
| `--observation-baseline` | `linear` | Baseline model applied to the observation |
| `--baseline-auto` / `--no-baseline-auto` | Automatic selection enabled | Select outermost 20% of valid channels on each side of zero within the displayed range; assumes both ends are line-free |
| `--baseline-window VMIN VMAX` | Unspecified | Repeat to provide manual baseline windows, overriding automatic selection |
| `--vmin` / `--vmax` | -100 / +100 | Display bounds, automatic baseline/noise selection bounds, and terminal search limits |
| `--fit-vmin` / `--fit-vmax` | -150 / +150 | LAB shape-comparison scoring interval; also the baseline interval only when using lower-envelope fitting |
| `--noise-range VMIN VMAX` | Highest-velocity 20% of valid positive channels within the display bounds | Line-free noise statistics for thresholds and observation SNR; repeat for multiple windows |
| `--terminal-method` | `threshold` | Rightmost qualifying above-threshold run; select `connected` explicitly to follow the main wing with a common threshold, or `erfc` for a fitted midpoint |
| `--terminal-source` | `auto` | Measure both observation and LAB when available, otherwise LAB only |
| `--terminal-sigma` | 3 | Noise threshold multiplier for `threshold` and `connected` |
| `--terminal-min-channels` | 3 | Above-threshold support; `connected` also requires this many consecutive below-threshold samples |
| `--terminal-return-width` | 3 | Additional below-threshold confirmation span for `connected` only |
| `--beam` | 5 degrees | Gaussian LAB averaging FWHM |
| `--lon-range` / `--lat-range` | `[-3,3,1]` each | Coordinate offsets used with `--sweep`; longitude offsets also serve `--lv` |

For complete, uniformly spaced data with the default display bounds, automatic
baseline windows are approximately -100 to -80 and +80 to +100 km/s; the
automatic noise window is approximately +80 to +100 km/s. With
`--vmin -250 --vmax 250`, they become approximately -250 to -200 / +200 to +250, and
+200 to +250 respectively. Missing channels can change the actual endpoints.
These are channel-count selections, not fixed windows or an emission finder.
The -150/+150 scoring defaults do not change when the plot bounds change.

OPT accepts the same `--vmin` / `--vmax` options (default -100/+100) and
automatic outer-window baseline selection. Explicit windows override selection
in both tools. OPT also accepts its old `--baseline-vmin` / `--baseline-vmax`
names as aliases; those aliases are not LAB profiler options. See
[observation baseline controls](observation_baseline.md) for examples.

## One profile

```bash
python src/util/lab_profiler.py \
  --lab-fits ~/github/lab-fit/lab.fit \
  --lon 70 --lat 0 --beam 5
```

The plot shows native LAB LSR velocity channels in km/s and brightness in K.
`--beam` (alias `--fwhm`) is the Gaussian averaging FWHM in degrees. The method
uses a Gaussian in Galactic coordinate degrees, extending three sigma per axis,
with fractional pixel offsets and per-channel blank-pixel exclusion. It handles
the repeated longitude endpoint without counting it twice. This is a coordinate-plane average, not a spherical convolution;
there is no subtraction of the native LAB beam in quadrature. At high Galactic
latitude the longitude width does not represent a constant angular sky width.

Existing `--galactic L B` and `--obs OBS --scan SCAN --dir DIRECTORY` inputs
remain available. Metadata mode resolves the scan's pointing and observing time.
Direct Galactic coordinates are independent of observing time and location.

By default CSV, PNG and JSON context files are saved and the plot is displayed.
Use `--output path/profile.csv` to choose their stem, `--no-show` for saved
outputs without a window, or `--display-only` to show without saving. Existing
`--output-dir` remains supported. `--vmin` and `--vmax` control the plot limits
(default -100 and +100 km/s).

## Coordinate context and observation metadata

Direct Galactic input accepts either `--lon L --lat B` or `--galactic L B`.
Longitude wraps to 0–360° and latitude must be within ±90°. Optional site and
UTC time provide context without changing the survey spectrum:

```bash
python src/util/lab_profiler.py \
  --galactic 82.2361 -5.6143 \
  --location 53.234123 -2.305485 77 \
  --dt 2026-10-24T11:25:50Z \
  --output-dir ~/samples/lab-profiles --no-show
```

`--location` takes geodetic latitude, east-positive longitude (degrees), and
height (metres). It defaults to Jodrell Bank (53.234123°, −2.305485°, 77 m).
`--dt` defaults to current UTC; an ISO datetime without a timezone is interpreted
as UTC, and explicit offsets are converted to UTC. Date-only input is rejected.
No additional site/time Doppler correction is applied to the LAB LSR channels.

To obtain the pointing from a recorded physical scan:

```bash
python src/util/lab_profiler.py \
  --dir ~/samples/rotation \
  --obs OBSERVATION_ID --scan FULL_PHYSICAL_SCAN_ID \
  --beam 5 --no-show
```

The directory must contain `<OBSERVATION_ID>-obs.json`. Both identifiers are
required, and the scan must match an entry in the observation's `target_scans`.
This mode uses the recorded site and observation time and cannot be combined
with direct coordinate, `--location`, or `--dt` overrides. Stored target sky
coordinates take precedence; fixed Alt/Az targets use acquisition pointing
when available and requested pointing as a fallback. Only metadata is read;
IQ data is not loaded or reprocessed.

Default output stems include the scan ID in metadata mode, or Galactic
coordinates and a UTC timestamp in direct mode. JSON records the resolved
coordinates, site/time and any observation/scan IDs. The shared coordinate
resolver is `obs.obs_pointing`.

The LAB survey is described by Kalberla et al. (2005), A&A 440, 775; see the
[NASA LAMBDA LAB product description](https://lambda.gsfc.nasa.gov/product/foreground/fg_LAB_HI_Survey_info.html).

## Compare one observation

```bash
python src/util/lab_profiler.py \
  --lon 70 --lat 0 --beam 5 \
  --file src/util/ODT-2026-10-27T165909Z-dish003-Rotation_vlsr.csv \
  --target-id 5 --freq-scan 0 \
  --output lab_target5.csv
```

The Observation Velocity CSV must contain `target_id`, `freq_scan`,
`velocity_km_s` and `calibrated_power`. Both identifiers must be supplied;
only matching rows are used. Non-numeric or non-finite samples are discarded,
and channels are sorted by velocity. At least ten valid samples are required;
duplicate velocities are rejected.

The numerical helpers are shared with observation velocity plots through
`util.convert.select_baseline_windows()` and `sdp.spectral_baseline.subtract_baseline()`.
Both tools default to clipped outer-window fitting with the same velocity
bounds; identical inputs and settings therefore use the same baseline. See
[observation baseline controls](observation_baseline.md) for `obs/opt.py` usage.

To generate the velocity CSV first, run OPT from the repository root:

```bash
PYTHONPATH=src python -m obs.opt \
  --dir ~/samples/rotation \
  --obs ODT-2026-11-08T175906Z-dish003-Rotation --profile rotation
```

Complete its interactive prompts. It writes
`~/samples/rotation/ODT-2026-11-08T175906Z-dish003-Rotation_vlsr.csv`.
Each target/frequency pair aggregates all included repeated physical scans.
For comparison, `--obs` and a full `--scan` supply the pointing/time; `--file`,
`--target-id` and `--freq-scan` select the aggregated measured spectrum.
Selecting `--scan ...-15-0-2` does not restrict the CSV spectrum to iteration 2.
The profiler reads original `calibrated_power`, not OPT's already-corrected
`baseline_subtracted_power`, so baseline subtraction is not applied twice.

For a metadata-based sweep using automatic baselines and a connected edge:

```bash
python src/util/lab_profiler.py \
  --dir ~/samples/rotation \
  --obs ODT-2026-11-08T175906Z-dish003-Rotation \
  --scan ODT-2026-11-08T175906Z-dish003-Rotation-15-0-2 \
  --file ~/samples/rotation/ODT-2026-11-08T175906Z-dish003-Rotation_vlsr.csv \
  --target-id 15 --freq-scan 0 \
  --sweep --lon-range '[-10,10,1]' --lat-range '[-10,10,1]' \
  --vmin -250 --vmax 250 --noise-range 100 150 \
  --terminal-method connected
```

`linear`, automatic baseline windows, a 3-sigma threshold and a 3 km/s
connected return width are defaults; `connected` itself must be requested.
The example's noise range is explicit and should be checked for emission.

The baseline is selected using `--observation-baseline`:

- `linear` (default): subtract a straight line fitted to automatically selected
  outer windows, or to explicit `--baseline-window` regions.
- `constant`: subtract a constant fitted to the selected windows.
- `none`: preserve the raw observation power without baseline subtraction.

To restore the earlier HI4PI-style lower-envelope linear method, use:

```bash
--observation-baseline linear --no-baseline-auto
```

With `--no-baseline-auto` and no explicit windows, linear/constant modes use
the lowest 20% of raw powers; linear fits a straight line and constant uses
their median. These lower-envelope modes require at least ten shared fitting
channels and at least three lower-envelope samples. The fit uses the finite survey overlap and
`--fit-vmin` / `--fit-vmax`, just as HI4PI does. Changing the fitting interval
can therefore change the baseline. The fitted line or constant is evaluated
across the full observation grid, including channels outside the fitting range.
The plot and comparison CSV use the same correction as the fit score. The plot
and JSON identify the selected baseline method. A non-positive corrected peak
is rejected.

LAB channel handling is unchanged: discard the leading channel for odd-length
cubes, then average adjacent channel pairs (891 channels become 445 pairs).
Interpolate that spectrum onto the observation velocity grid for scoring,
excluding channels outside survey coverage. For the plot, scale the full
corrected observation's positive peak to the full averaged LAB profile's peak.
The observation is scaled to LAB rather than independently calibrated in kelvin.
The LAB curve is dashed black and the observation red, matching HI4PI.

The reference and observation are independently peak-normalized within
`--fit-vmin` / `--fit-vmax` (default -150 to +150 km/s) for scoring. Points where
either normalized value is at or below 0.01 are excluded. At least ten shared
usable channels are required. RMS selects the best fit; correlation is reported
as an additional diagnostic. Baseline subtraction precedes normalization.
Automatic/explicit baseline windows are independent of the scoring interval;
the lower-envelope baseline uses it.

### Select line-free baseline windows

By default, linear/constant observation baselines select candidate windows from
the outermost 20% of finite, unmasked negative-velocity channels and outermost
20% of positive-velocity channels within the displayed `--vmin`/`--vmax` range.
For uniformly sampled complete coverage from -250 to +250 km/s, this selects
approximately -250 to -200 and +200 to +250 km/s. The same outlier-clipped
linear/constant fit described below is then applied to both ends. Selection
occurs once before the coordinate sweep, independently of the LAB scoring
interval. The actual bounds are printed and saved as `baseline_windows_kms`,
with `baseline_window_selection: automatic_outer_20_percent_per_side`.

This is an outer-channel heuristic, not an automatic line finder: it assumes
both ends are emission-free. It requires a displayed range spanning zero and
at least five selected channels per side. Missing/non-finite channels affect
the selected bounds. If either side lacks coverage, the run fails with a
message rather than silently fitting one side. Explicit `--baseline-window`
arguments override automatic selection. Use `--no-baseline-auto` to restore
the previous lower-envelope fit, or `--observation-baseline none` to skip
subtraction. `--baseline-auto` can explicitly re-enable automatic selection.
Automatic selection is unused for survey-only runs and the `none`
method. Automatic baseline selection does not override `--noise-range`; without an explicit
noise range, the normal right-hand noise-window algorithm applies separately.

If a sloping residual remains, select line-free channels on both sides of the
emission using repeated `--baseline-window VMIN VMAX` arguments. For example:

```bash
--observation-baseline linear --baseline-window -200 -80 --baseline-window 140 200
```

These are candidate windows for target 4 of the 2026-11-08 rotation observation;
inspect both spectra before reusing them for another target. With explicit
windows, the linear/constant fit uses all finite selected channels, with
symmetric 3-sigma outlier clipping (IQR/1.349, with standard deviation as a
fallback for degenerate IQR; up to five iterations), rather than the lowest
20% of powers. Windows are independent of the scoring interval and displayed
limits, so the baseline can be anchored at both ends without changing the LAB
fit range. At least ten usable channels must remain. Emission and persistent
interference must be excluded by window selection; clipping is not a line finder.
The estimated baseline is subtracted across the full spectrum before scoring,
display scaling and terminal-velocity estimation. JSON records the windows and
method, and the comparison CSV retains the evaluated baseline. A text box at
the upper left of profile plots shows the baseline windows and the noise
windows used by the threshold estimator, with the legend at the upper right.
It distinguishes automatic/manual baseline windows and the common threshold's
noise source. Erfc or disabled terminal measurements are identified as not
using a noise threshold. These windows are separate from `--noise-range`, which
estimates terminal-threshold noise and observation SNR, and does not fit or
subtract a baseline.

## Sweep Galactic offsets

```bash
python src/util/lab_profiler.py \
  --lab-fits ~/github/lab-fit/lab.fit \
  --file src/util/ODT-2026-10-27T165909Z-dish003-Rotation_vlsr.csv \
  --target-id 5 --freq-scan 0 \
  --lon 70 --lat 0 --beam 5 \
  --sweep \
  --lon-range "[-3,3,1]" \
  --lat-range "[-3,3,1]" \
  --output lab_target5_sweep.csv
```

Ranges are `[minimum offset,maximum offset,step]` in degrees, relative to the
nominal pointing from direct Galactic coordinates or `--obs`/`--scan` metadata.
Both default to `[-3,3,1]`. The maximum is included
when reachable by a whole number of steps. Longitude wraps into 0–360 degrees;
latitudes outside -90–90 degrees are skipped. Offset signs are preserved across
the longitude boundary. Steps and limits must be finite and steps positive.

The plot title, terminal output and JSON report the fitted Galactic coordinates,
signed longitude/latitude offsets, beam, selected target/frequency scan, RMS
and correlation. The cube is opened once for the sweep. Automatic or explicit
baseline windows are independent of candidate coordinates and the score range.
Only the lower-envelope mode (`--no-baseline-auto` with no explicit windows)
uses each candidate's finite survey overlap and scoring interval for its
baseline fit. The winning candidate's baseline is retained for the plot and
exported data. Ranking uses normalized shape RMS, not terminal-velocity agreement.

The output stem identifies the input CSV, target and frequency scan unless
`--output` is supplied. Outputs are:

- `.csv`: best LAB profile (pair-averaged for comparisons; native for survey-only).
- `.png`: profile or best-match comparison, including signed offsets.
- `.json`: coordinates, offsets, input file, beam, baseline and fitting metadata.
- `_comparison.csv`: observation-grid baseline, corrected power, scaled power
  and interpolated LAB brightness; unavailable LAB channels are NaN.
- `_sweep.csv`: every valid-coordinate candidate's coordinates, signed offsets,
  RMS and correlation. Candidates without a usable fit have non-finite scores.

The observation ID appears above the smaller comparison-header details. The
detail lines show target/frequency/beam, `Best FIT LAB` coordinates and offsets,
baseline/RMS/correlation, then `Terminal Velocity (Δv_T)` when both sources are
evaluated (shown as unavailable if either edge fails). The sign is
**observation minus LAB**. The upper-left
box lists baseline and noise windows on single lines, plus observation SNR,
median baseline and Robust Noise; the legend is at the upper right. A missing ID can be
inferred for the header from the standard `<observation_id>_vlsr.csv` filename.

## Python API

```python
from util.lab_profiler import LABGaussianExtractor

with LABGaussianExtractor("~/github/lab-fit/lab.fit") as extractor:
    velocity_kms, brightness_k = extractor.extract(l=70, b=0, fwhm=5)
```

Use `PYTHONPATH=src` from the repository root for this import. The context
manager releases the memory-mapped cube; a single extractor can serve many
positions. There is no extra Earth-motion correction because the survey's
velocity axis already uses the LSR frame.

## Scaled-observation SNR and noise diagnostics

Comparison plots automatically show observation peak SNR (linear and dB),
median noise baseline and Robust Noise in plot units in the upper-left text box.
The inverse noise-to-peak dB ratio remains in JSON for compatibility but is
omitted from the plot because it duplicates peak SNR. The calculation uses the
baseline-adjusted, scaled
observation, not the LAB noise statistics. `--noise-range` selects its line-free
channels; otherwise pipeline QA's smoothed-peak exclusion rule applies.
These diagnostics also work with erfc or disabled terminal measurements.

Let `mu` be the median of the selected noise samples and `scatter` be
`1.4826 * median(abs(samples - mu))`. The peak is the original sample at the
channel selected by the same moving-average peak locator as pipeline QA, minus
`mu`. The exported diagnostics retain their legacy keys:

```text
peak_snr = peak / scatter
peak_snr_db = 10 * log10(peak / scatter)
noise_rms_db_relative_to_peak = 10 * log10(scatter / peak)
```

The dB convention is explicitly a power-like brightness ratio (10 log), not
20 log of a voltage ratio. This is a spectral peak-to-radiometric-scatter
diagnostic, not integrated receiver S/N or an absolute dBm/dBW calibration.
See the [ITU decibel definitions](https://www.itu.int/dms_pubrec/itu-r/rec/v/R-REC-V.574-3-199006-S%21%21PDF-E.pdf).
Positive display scaling cancels from both ratios. Smoothing locates the supported peak but does not alter amplitudes. The
median baseline remains in linear plot units because baseline subtraction can
make it zero or negative, for which a logarithmic floor is undefined. Missing
noise coverage, zero scatter or no positive peak produces an unavailable
result rather than infinite dB. JSON records values, units, reference and
noise windows under `observation_noise`.

## Shared noise and SNR calculations

The LAB profiler and pipeline QA step share the pure noise/SNR functions in
`sdp.spectral_metrics`. The profiler keeps its mean noise floor and RMS scatter
from the selected velocity windows; QA defaults to a median floor and MAD-based
robust scatter from its peak-exclusion region. Their default numbers therefore
need not match. Identical sample selections, peak definitions and estimator
policies produce matching results. Neither calculation modifies the spectrum.

Profiler JSON records the noise estimator. Noise/SNR diagnostics no longer
invoke a terminal-velocity detector merely to obtain noise statistics; the
terminal methods reuse the same profiler noise-window helper. Existing CLI
thresholds and statistics retain their definitions and defaults. See
[shared spectrum quality metrics](spectral_quality_metrics.md) for estimator
formulas, QA's numerical floor, Python options and reproducibility requirements.

## Positive terminal velocity at a noise threshold

The default `--terminal-method threshold` marks the rightmost positive-velocity
falling crossing above a 3-sigma noise threshold. It measures both the baseline-corrected
observation and the LAB profile when an observation is loaded, otherwise only
the LAB profile. Each spectrum has its own noise estimate, threshold line and
terminal-velocity marker. The heading reports `Terminal Velocity (Δv_T)` in
km/s when both edges are detected; otherwise it reports the difference as
unavailable. During sweeps this measurement is made on the winning comparison,
without changing fit ranking. The `connected` method below uses a common
observation-derived threshold instead of independent noise thresholds.
Use `--terminal-source lab` or `--terminal-source observation` to choose
explicitly, or `--no-terminal-velocity` to disable the measurement.

JSON exports include both results under `terminal_velocity.spectra`, with
`observation_minus_lab_kms` present only when both edges are detected. Existing
top-level threshold-result fields retain the observation result in automatic
comparison mode, or the explicitly selected source. Erfc comparisons also
report their terminal-velocity difference in the heading.

Noise is estimated independently of the observation/survey fit RMS shown in
the title. By default compute the median level `mu` and the robust scatter
`sigma = 1.4826 * median(abs(T - mu))`. The detection threshold is `mu + 3*sigma`.
This accounts for any residual constant noise floor after
baseline subtraction. For observation plots these quantities are in the same
scaled plot units as the red curve, not an independently calibrated observation
temperature. Multiplying a spectrum by a positive scale factor and recomputing
its own noise threshold does not change its crossing velocity.

By default the same selector as pipeline QA excludes 20% of the native channel
grid around a mask-aware smoothed peak. Defaults are `--noise-window-frac 0.2`,
`--noise-smooth-window 10` (adjusted to an odd width) and
`--noise-estimator median_mad`. Remaining usable displayed channels supply the
median and MAD. Broad emission outside that exclusion can still bias the result.
Mean/RMS remains available explicitly via `--noise-estimator mean_rms`; the old
edge-only selection is available via `--noise-selection positive_edge`.
For a controlled measurement, explicitly
select line-free windows using repeated `--noise-range VMIN VMAX` arguments.
They can lie outside the displayed interval and are limited to available data.
Explicit windows require at least ten finite noise samples. Automatic QA
selection requires three usable spectrum channels and a nonempty noise region.
All samples in these windows
are used; emission, baseline curvature or interference can bias the estimate.

For example, add these options to the observation comparison or sweep command:

```bash
--vmin -150 --vmax 150 --noise-range 100 150
```

Choose the noise window after inspecting the selected spectrum; `100 150` is
an example rather than an automatically verified line-free range.

The terminal search is limited to positive velocities within the displayed
interval. It requires at least three consecutive channels above threshold by
default, rejecting isolated spikes. Select `--terminal-min-channels 1` for the
literal rightmost individual above-threshold channel, or another positive
integer to change this rule. The algorithm does not bridge NaN channels or
velocity gaps larger than 1.5 times the median channel spacing. It uses the
rightmost qualifying run and linearly interpolates between its final channel
above threshold and the next channel at or below threshold. This interpolation
does not improve the instrument's spectral resolution. Use `--terminal-sigma`
to change the threshold multiplier from 3 if needed.

LAB crossings are blue and observation crossings purple, with a vertical line
and dot and the velocity in the legend. A matching-colour dashed horizontal
line shows each median noise baseline; a dotted line shows its noise-floor-plus-N-sigma
threshold. The result, noise
median/robust scatter (or explicitly selected mean/RMS), threshold, source, noise windows and consecutive-channel requirement
are also printed or recorded in the output JSON's `terminal_velocity` object.
The JSON stores the full noise calculation; the terminal prints a short report.
If no qualifying emission exists, the noise estimate is unavailable/zero, or
coverage/a gap ends before the falling crossing, the plot reports that status
rather than assigning a terminal velocity. Edge/gap cases retain the last
above-threshold channel in JSON as a lower-bound candidate.

This is an operational threshold estimate of the observed H I edge; it does
not correct for thermal/turbulent broadening, optical depth, or instrumental
spectral response. For survey-only plots the channels have already been beam
averaged, so the measured RMS is that profile's residual fluctuation level.

## Predicted longitude–velocity diagram

Use `--lv` to predict an L–V diagram directly from the downloaded LAB cube.
The x-axis is Galactic longitude, the y-axis is native LAB LSR velocity, and
The default reference style uses a white background, blue brightness shading,
and magenta contours at fixed temperatures in K. One common colour scale
applies to all longitudes. Use `--lv-style inferno` for the original appearance.
This mode does not require an Observation Velocity CSV. It does not subtract
baselines, normalize spectra, or simulate receiver gain/noise. The temperature
scale is a survey prediction, not the arbitrary-power scale of `obs_display`.

For a 3 m dish with a requested 5-degree Gaussian beam, predict the Galactic
plane from longitude 30 to 70 degrees in 1-degree steps:

```bash
python src/util/lab_profiler.py \
  --lv --lv-style reference \
  --lab-fits ~/github/lab-fit/lab.fit \
  --lon 50 --lat 0 \
  --lon-range "[-20,20,1]" \
  --beam 5 \
  --vmin -100 --vmax 150 \
  --output lab_predicted_lv_30_70.csv
```

`--lon-range` retains its existing offset meaning. The example generates 41
longitude columns, centred from 30 through 70 degrees. Latitude remains fixed
at `--lat`; `--lat-range` is a fitting-sweep option and is not used here. Use
`[-20,20,10]` to predict just the five existing pointings. Sampling more finely
than the beam does not improve the beam's intrinsic angular resolution.

For a wider all-longitude prediction, use `--lon 180 --lat 0` and
`--lon-range "[-180,179,1]"`. Longitude values wrap modulo 360 degrees while
remaining adjacent on the plot. A repeated 360-degree endpoint is omitted.
Ranges spanning more than one full circle are rejected. A single longitude
is supported and displayed with a one-degree column width. No spatial
interpolation is performed between the extracted spectra.

In this mode only, `--beam` is the **requested final Gaussian FWHM**. The
averaging-kernel FWHM is calculated as:

```text
kernel_fwhm = sqrt(final_beam_fwhm**2 - native_lab_fwhm**2)
```

The nominal native beam defaults to `--lab-native-beam 0.6` degrees, giving a
kernel of approximately 4.964 degrees for a final 5-degree beam. The final
beam must exceed the native beam. LAB's effective native resolution varies
across the survey, so 0.6 degrees is an approximation; this has a small effect
for a much broader 5-degree beam. Set `--lab-native-beam` to override it. The
extractor retains its circular Gaussian in Galactic coordinate degrees,
with a three-sigma rectangular aperture. This is appropriate as an approximate
prediction near the Galactic plane, rather than an exact spherical beam model.
The existing single-profile and fitting modes retain their original averaging
width semantics.

Galactic coordinates and a fixed LSR survey do not depend on the observer's
site or date. Existing `--location LAT LON HEIGHT` and `--dt` options are saved
as context; they do not apply another velocity correction or mask unobservable
sky. An existing `--obs`/`--scan` pointing can also supply the central Galactic
coordinate, with the requested longitude offsets applied about it.

The cube is opened once and reused for all columns. Blank survey pixels remain
masked. `--vmin` and `--vmax` select the displayed velocity interval and the
range used for the colour scale. No new velocity-channel averaging is applied.
Outputs share the `--output` stem:

- `.png`: the predicted L–V map.
- `.csv`: all native velocity samples, grouped by longitude, with columns
  `galactic_longitude_deg,galactic_latitude_deg,velocity_lsr_kms,brightness_K`.
  This export includes channels outside the displayed velocity interval.
- `.json`: longitude samples and offsets, fixed latitude, nominal native/final
  beam and smoothing width, cube path, units, and site/time context.

Use `--no-show` to save without opening a window, or `--display-only` to open
without writing files. `--lv` is separate from observation comparison and
best-match sweeps, so it cannot be combined with `--file` or `--sweep`.

For Python callers, `predict_lv(extractor, longitudes, latitude, beam,
native_beam=0.6)` returns the native LSR velocity vector and a two-dimensional
brightness array with shape `(velocity_channels, longitude_samples)`.


### Reference-style colours and contours

Predicted L–V plots now default to `--lv-style reference`, closer to a
publication-style white/blue map with thin magenta isobrightness contours.
The default contour levels are 5, 10, 20, 40 and 80 K; labels identify their
actual brightness temperatures. These contours follow the predicted H I
brightness field. They are not fitted spiral-arm tracks, and the code does not
assign arm names from a reference illustration.

The reference style uses an `asinh` colour stretch, a zero-K lower limit, and
the maximum visible temperature as the upper limit. This reveals faint
structure while avoiding a saturated multicolour background. Adjust with:

```bash
--lv-style reference \
--lv-stretch asinh --lv-asinh-width 10 \
--lv-brightness-min 0 --lv-brightness-max 100 \
--lv-contours 5 10 20 40 80
```

`--lv-asinh-width` is the transition scale in K. Smaller values make the colour
mapping more logarithmic. `--lv-stretch linear` and `sqrt` are also available.
The fixed colour limits are useful for comparing multiple maps. All these
options change presentation only: the predicted/extracted temperature values,
CSV exports, and beam convolution remain unchanged. Negative temperatures map
to the lowest colour when the displayed lower limit is zero; their numeric
values remain in the export.

Reference-style longitude decreases from left to right and is labelled in
signed degrees (-180 to +180), matching the supplied example. Use
`--lv-longitude-order increasing` to retain the previous direction. The
`inferno` style defaults to increasing longitude, unsigned labels, a linear
stretch, and no contours. Explicit `--lv-contours` enables contours with either
style; `--lv-no-contours` disables them. Levels must be positive, finite and
strictly increasing. Only levels inside the visible data range are drawn;
a one-longitude map does not have enough columns for contours.

For a full-longitude prediction comparable in layout to the reference:

```bash
python src/util/lab_profiler.py \
  --lv --lv-style reference \
  --lon 0 --lat 0 --lon-range "[-180,180,1]" \
  --beam 5 --vmin -160 --vmax 160 \
  --lv-brightness-max 100 \
  --lv-contours 5 10 20 40 80 \
  --output lab_reference_lv.csv
```

The duplicate circular endpoint is omitted from the extracted/exported samples.
JSON records the actual colour limits, stretch, contour levels, longitude order
and label convention for reproducibility. A 5-degree beam will still show
broader structures than a higher-resolution survey illustration; styling does
not restore spatial detail removed by beam convolution.


## Main-wing detection edge with a common threshold

Use `--terminal-method connected` to follow the dominant positive emission
wing to a sustained return below a noise threshold, rather than selecting the
rightmost qualifying excursion or an erfc transition midpoint. For example:

```bash
--terminal-method connected --terminal-sigma 3 --noise-range 100 150 --terminal-return-width 3
```

When observation data is supplied, the baseline-corrected, peak-scaled
observation's median noise baseline plus N times its robust scatter defines **one common
amplitude threshold for both curves**. The shared floor line is the observation's
reference level used in that comparison, not a claim that LAB and observation
have identical intrinsic noise baselines or scatter. The observation is not independently
temperature-calibrated: this is a common level in the plot's scaled units.
Without observation data, LAB supplies its own threshold. Explicit source
selection controls which edge is shown; an observation still supplies the
threshold when present, including `--terminal-source lab`. Invalid reference
noise produces an unavailable result rather than a fallback to LAB noise.

The estimator splits data at missing channels or gaps exceeding 1.5 times the
median channel spacing. In each segment a three-channel median followed by
Gaussian smoothing (dispersion max(1 km/s, one channel spacing)) supplies a
guide. The strongest guide peak in an above-threshold run containing at least
`--terminal-min-channels` samples anchors the search. The actual crossings use
the original unsmoothed samples. Starting at the anchor, it finds the first
consecutive below-threshold run containing at least `--terminal-min-channels`
samples (default 3) **and** spanning `--terminal-return-width` km/s (default 3).
Brief dips are bridged; once a return is confirmed, detached later islands do
not move the edge. Linear interpolation locates the crossing at the start of
the confirmed run. A gap or insufficient right-hand coverage before confirmation
gives a limited result, not a detected edge. Confirmation width is in velocity
units, though finite channel spacing still affects the result.

The plot labels these measurements as main-wing detection edges and draws the
shared mean noise floor as a dashed horizontal line and the noise-floor-plus-N-sigma
threshold as a dotted line, with separate legend entries. These shared lines
are drawn once. The independent `threshold` method shows each source's mean
floor and threshold separately. JSON records `method: connected`, `threshold_source`,
the anchor velocity, requested and confirmed return widths, channel counts,
noise statistics, status and the observation-minus-LAB difference when both
edges are detected. Keep baseline and noise windows line-free and inspect the
selected feature: this operational detection edge is not a physical tangent-point
velocity or a claim that all emission ends there. Distinct weaker components
and non-Gaussian noise need separate assessment. No formal positional/velocity
uncertainty is inferred from the confirmation width. The original `threshold`
method remains the default and `erfc` remains available.

## Complementary-error-function terminal edge

Add `--terminal-method erfc` to a profile comparison or sweep to select the
falling-wing window automatically within the positive part of the displayed
`--vmin`/`--vmax` range. Supply `--terminal-fit-range VMIN VMAX` to override
selection with a manual window. With the default `--terminal-source auto`, the final plot
fits both the observation and the best-matching LAB spectrum independently.
Use `--terminal-source observation` or `lab` to fit only one. Sweep coordinate
selection still uses the existing full-profile RMS score; the erfc fit measures
the edge of the winning profile and does not change coordinate selection.

Automatic selection is performed independently for observation and LAB. A
three-channel median filter suppresses isolated spikes, followed by Gaussian
smoothing with dispersion equal to the larger of 1 km/s and one channel
spacing. This guide is used only to locate the dominant peak and the strongest
negative gradient after it; the fit uses original, unsmoothed samples. Gaps
split the guide into separate segments and are never interpolated over. A
segment must have at least 12 channels and peak-to-tail contrast exceeding five
times the tail's IQR/1.349 scatter estimate. This is a selection heuristic, not
a formal detection significance.

The gradient width at one quarter maximum estimates the initial transition
dispersion (at least three channel spacings). The proposed window extends
3.5 dispersions below and five above the gradient seed, clipped to available
segment coverage. All existing fit-validity checks still apply; an automatic
selection may be rejected as unconstrained. JSON records the search range,
selected and actual sampled intervals, seed, smoothing and selection method.
The console and plot legend identify the selected window. Inspect these
intervals: the dominant falling transition can differ from a weaker component
at more extreme velocity, and a successful fit is not an independent physical
terminal-velocity validation. Baseline subtraction remains a separate step.

For example (replace the CSV path, pointing and fit window for your observation):

```bash
python src/util/lab_profiler.py \
  --lab-fits ~/github/lab-fit/lab.fit \
  --file observation_vlsr.csv --target-id 3 --freq-scan 0 \
  --lon 50 --lat 0 --beam 5 --sweep \
  --lon-range '[-3,3,1]' --lat-range '[-3,3,1]' \
  --vmin -100 --vmax 150 \
  --terminal-method erfc --terminal-fit-range 30 120 \
  --output lab_erfc.csv
```

The model is `B + A/2 * erfc((v - v_terminal)/(sqrt(2)*width))` with positive
amplitude and width. `v_terminal` is the midpoint of the fitted falling step;
`width` is its Gaussian dispersion in km/s. This is a model edge estimate,
separate from the original noise-dependent 3-sigma crossing, which remains the
default `--terminal-method threshold`.

Choose a contiguous positive-velocity window covering the final falling wing
and its line-free tail, excluding unrelated emission peaks. The window must lie
inside the displayed velocity limits. The program requires at least 12 finite
channels and rejects gaps or edges without enough coverage on both sides.
Inspect the overlay and try nearby windows: a converged fit alone does not
establish that a single erfc describes a blended profile.

The observation is baseline-subtracted using the selected baseline method and
fitted in its input power units, before display scaling. LAB is fitted in K.
Plots show both fitted curves, edge velocities and their observation-minus-LAB
difference. JSON stores each fit's status, parameters, residual RMS and formal
covariance errors. Failed fits are labelled rather than given a velocity.
Formal errors exclude baseline, beam, spectral-response and model uncertainties;
a 5-degree beam can mix materially different terminal edges across longitudes.
No instrumental-width or beam-smearing correction is applied to the fit.

This optional single-component model is a first comparison tool, rather than a
reproduction of the multi-component analysis in
[McClure-Griffiths & Dickey (2007)](https://arxiv.org/abs/0708.0870).


## Frontend Velocity comparison

In the observation-processing frontend, select a completed SKY aggregate,
choose **Velocity**, leave **All SKY aggregates** unchecked and enable
**LAB survey**. The comparison uses this profiler's Gaussian extraction,
paired survey channels and normalized shape scoring at the aggregate's resolved
Galactic coordinates. The configurable beam FWHM defaults to 5 degrees. This
view also offers an optional coordinate sweep, described below. It does not
measure terminal velocity.

The observation is corrected with the frontend's current baseline settings and
peak-scaled to LAB for the comparison plot, explicitly labelled in its legend.
Ordinary observation exports remain unscaled. Comparison PNG, LAB profile CSV
and metadata JSON are included in **Export results**. The server uses
`~/github/lab-fit/lab.fit`; override with the `DMD_LAB_FITS` environment variable.
See [frontend workflow](../src/ui/frontend_prototype/README.md#lab-survey-comparison).


### Frontend coordinate sweep

With **LAB survey** enabled, select **Sweep LAB coordinates**, enter minimum,
maximum and step for Galactic offsets Δl and Δb, then select **Find best fit**.
The default is -5/+5 degrees at 0.5-degree intervals on both axes. A -10/+10
search at the same step is also supported. Requests are limited to 2500
candidates; narrow ranges or increase steps for larger searches.

The server extracts every candidate using the selected beam FWHM and the
profiler's paired-channel matching, then ranks normalized spectral shapes by
minimum RMS within the frontend velocity minimum/maximum. The baseline-corrected
observation remains fixed throughout the sweep. The winning profile replaces
the nominal LAB curve; the plot and exported JSON include its coordinates,
offsets, RMS, correlation, search ranges and boundary flag. All candidate scores
are in `*-lab-sweep.csv`, included in **Export results**. Invalid latitude or
unscorable candidates are recorded and excluded from selection. A boundary
winner is flagged to indicate that expanding the search may improve the fit.
The result is a best sampled spectral match, not a measured pointing uncertainty.
Uncheck the sweep option to restore comparison at the nominal coordinates.
