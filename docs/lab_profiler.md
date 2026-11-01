# LAB profiles and observation fitting

`src/util/lab_profiler.py` contains the LAB Gaussian extractor, single-profile
plotting, observation comparison and Galactic offset sweeps. It replaces
`lab_gau_extractor_simple_mod.py` and `read_ray.py`. It reads the local Galactic
CAR cube (`lab.fit`), rather than the old 10 km/s HEALPix slices. There is no
automatic survey download and `healpy` is no longer needed.

Run from the repository root with Python, NumPy, Astropy and Matplotlib installed.
The default cube path is `~/github/lab-fit/lab.fit`; override with `--lab-fits`.

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
the repeated longitude endpoint without counting it twice. As in the original
extractor, this is a coordinate-plane average, not a spherical convolution;
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

The calculation is shared with observation velocity plots through
`util.convert.subtract_spectrum_baseline()`. See
[observation baseline controls](observation_baseline.md) for `obs/opt.py` usage.

The baseline is selected using `--observation-baseline`:

- `linear` (default): match HI4PI's lower-envelope method. Select the lowest
  20% of raw observation powers within the fitting interval and finite survey
  overlap, fit a straight line to those samples, and subtract it.
- `constant`: subtract the median of that same lowest 20% of samples, as in HI4PI.
- `none`: preserve the raw observation power without baseline subtraction.
- `read-ray`: reproduce the earlier line from the first valid observation
  channel to the interpolated observed power at +50 km/s. Only this mode
  requires coverage of +50 km/s and a first channel below it.

For example, explicitly select the HI4PI linear method with:

```bash
--observation-baseline linear
```

The lower-envelope modes require at least ten shared fitting channels and at
least three lower-envelope samples. The fit uses the finite survey overlap and
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
as an additional diagnostic. The lower-envelope baseline is calculated before
normalization; the legacy `read-ray` anchors are independent of the fitting
interval.

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
nominal `--lon` and `--lat`. Both default to `[-3,3,1]`. The maximum is included
when reachable by a whole number of steps. Longitude wraps into 0–360 degrees;
latitudes outside -90–90 degrees are skipped. Offset signs are preserved across
the longitude boundary. Steps and limits must be finite and steps positive.

The plot title, terminal output and JSON report the fitted Galactic coordinates,
signed longitude/latitude offsets, beam, selected target/frequency scan, RMS
and correlation. The cube is opened once for the sweep. Each candidate uses
its finite overlap for baseline estimation, matching HI4PI. The winning
candidate's baseline is retained for the plot and exported data.

The output stem identifies the input CSV, target and frequency scan unless
`--output` is supplied. Outputs are:

- `.csv`: best LAB profile (pair-averaged for comparisons; native for survey-only).
- `.png`: profile or best-match comparison, including signed offsets.
- `.json`: coordinates, offsets, input file, beam, baseline and fitting metadata.
- `_comparison.csv`: observation-grid baseline, corrected power, scaled power
  and interpolated LAB brightness; unavailable LAB channels are NaN.
- `_sweep.csv`: every valid-coordinate candidate's coordinates, signed offsets,
  RMS and correlation. Candidates without a usable fit have non-finite scores.

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

## Positive terminal velocity at a noise threshold

The plot now marks the rightmost positive-velocity falling edge above a
3-sigma noise threshold. By default it uses the baseline-corrected observation
when an observation is loaded, otherwise the LAB profile. During sweeps this
measurement is made on the winning comparison, without changing fit ranking.
Use `--terminal-source lab` or `--terminal-source observation` to choose
explicitly, or `--no-terminal-velocity` to disable the measurement.

Noise is estimated independently of the observation/survey fit RMS shown in
the title. In line-free channels, compute the mean level `mu` and the RMS
fluctuation about it, `sigma = sqrt(mean((T - mu)^2))`. The detection threshold
is `mu + 3*sigma`. This accounts for any residual constant noise floor after
baseline subtraction. For observation plots these quantities are in the same
LAB-scaled kelvin units as the red curve; multiplying the spectrum by a positive
scale factor does not change the measured velocity.

By default the noise window is the highest-velocity 20% of finite positive
channels within the displayed `--vmin`/`--vmax` interval. This assumes that end
of the spectrum is emission-free. For a controlled measurement, explicitly
select line-free windows using repeated `--noise-range VMIN VMAX` arguments.
They can lie outside the displayed interval and are limited to available data.
At least ten finite noise samples are required. All samples in these windows
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

A purple vertical line and dot mark the crossing, and the legend reports its
LSR velocity. A dotted grey line shows the noise threshold. The result, noise
mean/RMS, threshold, source, noise windows and consecutive-channel requirement
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
