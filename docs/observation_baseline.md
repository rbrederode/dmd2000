# Baseline subtraction in observation velocity plots

The LSRK plot produced by `obs/opt.py` and `ObsDisplay.display_lsr()`
subtracts an independent baseline for every aggregated target/frequency scan.
OPT and the LAB profiler use the same automatic selection and fitting helpers:
`util.convert.select_baseline_windows()` and `sdp.spectral_baseline.subtract_baseline()`.

| Setting | OPT and LAB profiler |
|---|---|
| Default model | Linear |
| Default velocity bounds (`--vmin`, `--vmax`) | -100, +100 km/s |
| Default baseline selection | Outermost 20% of valid negative channels and outermost 20% of valid positive channels inside those bounds |
| Window fitting | Fit all selected channels with symmetric outlier clipping |
| Explicit `--baseline-window` | Overrides automatic selection; repeat for multiple windows; independent of velocity bounds |
| Automatic-window switch | Enabled by default; `--no-baseline-auto` restores lower-envelope fitting |

For uniformly spaced complete spectra, default windows are approximately
-100 to -80 and +80 to +100 km/s. With `--vmin -250 --vmax 250`, they are
approximately -250 to -200 and +200 to +250 km/s. Selection counts channels
on **each side of zero**, rather than selecting the lowest powers. Masking and
channel spacing can change the actual endpoints. These candidate windows
assume the ends are line-free; they do not identify emission automatically.

The tools can produce the same baseline when given identical raw data,
velocity bounds, windows and method. LAB's comparison plot then scales the
observation to the LAB peak, whereas OPT retains calibrated power units.
With automatic selection
disabled, OPT uses `--vmin`/`--vmax` for lower-envelope fitting; LAB uses its
separate `--fit-vmin`/`--fit-vmax` and survey overlap.
See [LAB profiler baseline controls](lab_profiler.md#select-line-free-baseline-windows).

Only the baseline is subtracted. There is no peak normalization, multiplication,
LAB temperature matching, clipping of negative values or amplitude scaling.
Units remain arbitrary calibrated power units. The original MPR arrays,
frequency-domain plots and integrated-power products are unchanged.

## Command-line controls

From the repository root, the processing tool can be run as
`PYTHONPATH=src python -m obs.opt --obs OBS_ID --dir DIRECTORY --profile PROFILE`.
Use the repository root as the working directory; launching the module from
`src/util` can make `util.py` shadow the `util` package.
Automatic baseline windows require no additional options. To expand the
LSRK plot and automatic baseline selection to +/-250 km/s, use:

```bash
PYTHONPATH=src python -m obs.opt \
  --dir ~/samples/rotation \
  --obs ODT-2026-11-08T175906Z-dish003-Rotation \
  --profile rotation --vmin -250 --vmax 250
```

The defaults are `--observation-baseline linear --baseline-auto --vmin -100
--vmax 100`. The bounds must be finite and ordered; automatic selection also
requires `vmin < 0 < vmax`. These limits control the LSRK line plot and fitting
selection, while the velocity CSV and prepared L–V spectra retain the full
native velocity grid. The L–V vertical axis uses the selected limits within common native coverage.

Available baseline methods:

- `linear`: fit a straight line across the selected baseline channels and
  subtract it across the full spectrum.
- `constant`: subtract the mean of the retained baseline channels.
- `none`: retain the original calibrated power, without selecting windows.

Both automatic and explicit windows fit all valid selected channels with
symmetric 3-sigma outlier clipping, using IQR/1.349 for scatter and a
standard-deviation fallback, for up to five iterations. No measured power
samples are clipped or changed by outlier rejection; it affects fitting only.

To select known line-free regions yourself, repeat `--baseline-window`:

```bash
--vmin -250 --vmax 250 --observation-baseline linear \
--baseline-window -250 -190 --baseline-window 170 250
```

Explicit windows take precedence over `--baseline-auto` and can extend outside
`--vmin`/`--vmax`. Inspect the spectrum to choose regions without emission.
A linear fit removes a level and slope, not all baseline curvature. Noise is
allowed above and below the approximately zero background.

For the previous lower-envelope method, use `--no-baseline-auto` without
explicit windows. It selects the lowest 20% of valid powers within
`--vmin`/`--vmax`; linear fits a line, constant subtracts their median. To
reproduce OPT's previous default fitting interval, also supply
`--vmin -150 --vmax 150`. The old OPT options `--baseline-vmin` and
`--baseline-vmax` remain accepted as aliases for `--vmin` and `--vmax`; they
now also set line-plot limits and automatic-window selection bounds.

Flagged and non-finite samples never contribute to selection or fitting.
Automatic windows require at least five selected channels on each side;
window fits require at least ten usable channels to remain after clipping.
Legacy lower-envelope fits require ten candidate channels and three selected
samples. If a scan cannot be fitted, the tool logs its target and frequency
scan and skips it. Other valid scans are still plotted. If all scans are
skipped, no LSRK plot or velocity CSV is written for that run.

The plot identifies the method in its title and labels its amplitude axis as
baseline-subtracted when a correction is applied. A vertical marker identifies
zero LSRK velocity. There is no horizontal zero-power reference line.

## Frontend analysis controls

The frontend uses the same automatic baseline windows during initial processing
and **Update plots**. **Velocity minimum/maximum** set the line-plot bounds, L–V vertical bounds and
automatic-window selection bounds; linear/constant/flatten modes require coverage on both
sides of zero. **No subtraction** ignores baseline selection but retains the
plot limits. Blank bounds default to the full recorded velocity range, including
excluded bandpass channels. Either bound can be supplied independently; clear
both to restore the full range. Automatic fitting windows are selected from
usable channels within those resolved bounds, not from the grey excluded edges.
The initial view uses **No subtraction**, retaining the configured pipeline output.
Velocity plots show grey bandpass exclusions and red RFI markers in the same
channel locations as Frequency, transformed into LSRK coordinates. Both initial
processing and cached updates preserve the channel flags and their ordering.
Older caches without flags remain usable but show a request to reprocess for
annotations. Obsolete browser range defaults are reset once; later explicit
limits are remembered.
**Update plots** also regenerates `velocity.csv` in **Export results** from
cached native spectra. It preserves original calibrated powers and Signal
images. Configuration-profile changes require full reprocessing. Contour levels
and smoothing affect the L–V contour overlay only.

### Single-aggregate Frequency baseline

The frontend Frequency tab enables **Baseline** and **Frequency minimum/maximum
[MHz]** when **All SKY aggregates** is unchecked and a SKY row is selected.
The MHz controls sit beneath the velocity controls, which are disabled in this
context. Blank MHz bounds select the available channel range. Limits control
both the frequency display and its own baseline fitting, independently of the
Velocity fitting bounds.

The frequency fit selects the outermost 20% of valid channels in each half of
the selected MHz interval, split at its midpoint, then uses the same symmetric
clipping and linear/constant fitting helper. No subtraction keeps raw powers.
Both Frequency and Velocity always start from the original cached powers, so
subtraction is not applied twice. Frequency uses the observed FFT frequencies
in MHz; its baseline can differ from Velocity when the fitting ranges differ.
The all-SKY Frequency overlay uses those same corrected curves and MHz limits,
with no additional fit or correction. **Update plots** regenerates individual
frequency spectra and their overlay from cached frequency channels; older jobs require one full
reprocessing run to create that cache. The velocity CSV retains Velocity's
baseline and is not replaced by the frequency-specific fit.

## Velocity CSV

The existing `<observation>_vlsr.csv` keeps its original four columns and adds
two more:

```text
target_id,freq_scan,velocity_km_s,calibrated_power,baseline,baseline_subtracted_power
```

`calibrated_power` retains the original unscaled power. `baseline` is the
fitted background, and `baseline_subtracted_power` is the plotted value:

```python
baseline_subtracted_power = calibrated_power - baseline
```

Masked/non-finite power samples are blank in all three power columns. With
`--observation-baseline none`, the baseline is zero at valid channels and
baseline-subtracted power equals calibrated power. Rows remain grouped by
target/frequency scan, sorted by velocity within each curve.

The LAB and HI4PI profilers continue reading `calibrated_power` and applying
their chosen baseline themselves. The extra columns therefore do not cause
baseline subtraction to be applied twice. Consumers wanting the corrected
spectrum directly should explicitly read `baseline_subtracted_power`.

## Python API

```python
from sdp.spectral_baseline import subtract_baseline

corrected, baseline = subtract_baseline(
    velocity, power, method="linear", fit_mask=fit_mask,
)
```

The helper accepts matching one-dimensional arrays and preserves existing
masks while masking invalid samples. Plain arrays return plain arrays with
NaNs at invalid samples. It does not require a positive emission peak;
`lab_profiler.py` retains that separate requirement for comparison/plot scaling.
The helper's default `fit_line_free=False` retains the lower-envelope
algorithm for direct callers. OPT and LAB's automatic or explicit windows
instead pass `fit_line_free=True` to enable the clipped line-free fit described
above. Calling the subtraction helper alone does not select windows:

```python
from util.convert import select_baseline_windows
from sdp.spectral_baseline import subtract_baseline

windows = select_baseline_windows(velocity, power, -250, 250)
fit_mask = ((velocity >= windows[0][0]) & (velocity <= windows[0][1])) | (
    (velocity >= windows[1][0]) & (velocity <= windows[1][1]))
corrected, baseline = subtract_baseline(
    velocity, power, method="linear", fit_mask=fit_mask, fit_line_free=True,
)
```

`ObsDisplay.display_lsr()` accepts `observation_baseline`, `vmin`, `vmax`,
`baseline_auto` and `baseline_windows` with the same semantics as the CLI.
Legacy Python keywords `baseline_vmin` and `baseline_vmax` remain supported
and override their corresponding `vmin`/`vmax` arguments when provided.
Prepared `lsr_spectra` entries include the selected `baseline_windows`.

## Longitude–velocity (L–V) view

After the velocity-versus-power plot appears, OPT prompts:

```text
Press Enter to show the longitude–velocity (L–V) plot...
```

Press Enter in the terminal to open the new plot. Its x-axis is Galactic
longitude in degrees, its y-axis is LSRK velocity in km/s, and its white-to-blue
colour scale encodes the same corrected power used in the velocity plot.
The palette and asinh colour stretch match the LAB predicted L–V reference
style, while retaining calibrated power units rather than kelvin.
It saves `<observation>-sky-apr-lv.png` beside the other aggregated SKY plots.
Press Enter again at the final prompt to finish.

Each observed longitude is a broad column. Boundaries lie halfway between
neighbouring longitudes; the outer columns extend half the neighbouring
spacing. One longitude alone gets a one-degree-wide column. Column widths
represent sparse sampling cells, not measured angular resolution. No spatial
interpolation or smoothing is applied to the coloured columns. Native velocity channels and masked
samples are retained. The visible velocity range is cropped to the highest
valid lower edge and lowest valid upper edge across all columns, further
cropped by the selected velocity minimum and maximum. All panels share these
limits, including with no baseline subtraction. The colour scale and automatic
contour levels use the visible channel cells, so offscreen peaks do not control
the contrast. Spectra are not shifted, resampled or padded;
internal masked samples remain gaps. If the columns share no velocity coverage,
the L–V plot is skipped with a log message.

Coordinates come from each target's stored sky direction transformed to
Galactic coordinates. If only Alt/Az is available, the resolved direction from
the preceding LSRK plot is used. The longitude axis increases towards the left and uses signed labels
(-180° to +180°, with 180° shown positive). It wraps across 360° without
placing nearby 359° (labelled -1°) and 1° targets at opposite ends. Zero appears
where included in the observed coverage; an exclusively positive-longitude
observation retains its measured range rather than adding an empty negative half. The panel title reports
the observed Galactic longitude and latitude ranges, identifying ranges that
cross 0°; this is a longitude projection of
the observed directions, not a latitude-integrated map.

Different frequency setups are shown in separate panels with a shared colour
bar. Repeated longitudes in the same frequency setup also get separate panels
so that observations are not silently averaged or overpainted. All panels use
one shared asinh colour mapping in calibrated power units. Negative residuals
remain in the native arrays/exports and map to the white, zero-end colour.
There is no normalization per column. The existing velocity CSV remains the
numeric export; this view adds a PNG.

The Python API is `display.display_lv(obs, output_path, vmin=..., vmax=...)`, called after
`display.display_lsr(...)`. It reuses the prepared spectra directly without
recomputing the baseline or changing the input data. Missing target directions
are logged and skipped; an empty data set produces no L–V figure or extra
Enter prompt. Loading a new scan set clears the prepared LSRK data.


### OPT L–V contours

The L–V view uses the same white/blue palette and thin magenta contour style
as `lab_profiler --lv --lv-style reference`. The shared palette is defined in
`util.lv_plot`. The asinh transition width is 10% of the common positive
maximum, analogous to a 10 K transition for a 100 K LAB display. The mapping
changes colours only; no column is normalized and no measured power is scaled.
OPT uses longitude increasing towards the left, signed longitude labels and
shared truncated velocity coverage.

Default contours are at 5%, 10%, 20%, 40% and 80% of the shared positive maximum.
These are converted to actual calibrated power levels, labelled in `a.u.`,
with the same levels used across all panels. They are not kelvin contours or
spiral-arm model tracks. Override them in the OPT command with, for example:

```bash
--lv-contours 0.005 0.01 0.02 0.04 0.08
```

Or disable them with `--lv-no-contours`. Levels must be positive, finite and
strictly increasing. A panel with only one longitude cannot support contours.
Only levels within a panel's available amplitude range are drawn.

For the contour overlay only, spectra are interpolated onto the first column's
velocity grid within the shared visible interval. Interpolation does not bridge
masked channels, large velocity gaps, or extrapolate outside valid spectra.
Contours interpolate between the observed longitude centres (without crossing
a known missing column in a frequency panel), so sparse
pointings give an approximate visual connection, not additional observations.
The coloured columns retain their native, unmodified channels and masks. The
underlying CSV and baseline-subtracted amplitudes are unchanged.

Python callers can supply `contour_levels=[...]` to `display_lv`, or an empty
list to disable contours.


To avoid drawing jagged contours around every fine-channel noise fluctuation,
the contour overlay alone is smoothed along velocity with a Gaussian FWHM of
2 km/s by default. This operates separately within contiguous valid channel
runs, preserving data gaps. Change it with `--lv-contour-smoothing 1` or disable
smoothing with `--lv-contour-smoothing 0`. Only one label per contour level is
placed on its longest path. Neither smoothing nor interpolation changes the
native coloured columns, the line plots, or any exported amplitudes.


## Optional processing-pipeline baseline

The `BASELINE` processing step uses the same coordinate-independent fitter as
OPT's interactive views and the LAB profiler. Place it in the `cal` context,
after calibration, bandpass selection and RFI flagging, before QA. Corrected
CAL samples contribute to MPR, so current and mean curves share a corrected
scale. SPR data is unchanged. LOAD reference scans bypass subtraction because
their power is needed for calibration of SKY scans.

Example entry in a profile's `PipelineConfig.json` step list:

```json
{
  "_type": "StepConfig",
  "step": "BASELINE",
  "params": {
    "context": "cal",
    "method": "flatten",
    "enabled": true,
    "ranges_pct": [[0, 10], [90, 100]]
  }
}
```

Methods are `flatten` (default), `linear`, `constant`, and `none`. Set `enabled: false`
in params, choose `none`, or remove the step to bypass subtraction. The
all configuration profiles enable this step with `flatten`, including solar.

`flatten` uses the same straight-line fit and clipping as `linear`, but subtracts
only `fitted_line - min(fitted_line[usable_channels])`. The minimum is evaluated
across the usable passband, excluding flagged and invalid channels, rather than
only within the fitting windows. This retains the lowest fitted background level
and any flat continuum increase; it does not anchor to a noisy measured minimum.
Negative corrected values are retained. Flattening changes integrated power and
can remove genuine spectral slopes, so it does not replace a measurement of the
original calibrated total power. LOAD references and saved SPR remain unchanged.
Choose `flatten` in the pipeline step's Method field or set `"method": "flatten"`
in its JSON parameters. The interactive Analysis settings baseline selector also
offers `Flatten (retain fitted level)`. It fits the processed aggregate using the
Frequency or Velocity view's automatic outer windows and selected limits. Each
Update plots starts from the cached pipeline output, allowing a further fit with
different windows without accumulating corrections from earlier plot updates.
The interactive selector starts at **No subtraction** on each page load, including
when browser preferences contain an older baseline selection. Choose another
method explicitly to apply an additional correction to the pipeline output.

By default the step selects the lowest and highest 10% of usable channels
(after flags), equivalent to 20% of each half of a complete spectrum. These
are candidate line-free regions, not automatic emission detection. Change
`ranges_pct` to select other percentiles of usable channels. The fit uses all
selected channels with symmetric clipping; at least ten fitting channels are
required. Insufficient coverage fails processing rather than inventing a fit.
Python callers can supply `baseline_x` and `baseline_fit_mask` in the context;
otherwise channel index supplies the coordinate scale. The numerical function
accepts arbitrary coordinates and a fitting mask without assuming physical units.

### OPT pipeline editing and interactive adjustments

The Processing pipeline panel sits below the observation list and above the
selected observation summary, process button, scans and plots. The **Pipeline Steps** list shows ordered steps, retaining digitiser labels
when there are digitiser-specific entries. Checkboxes enable/disable steps.
Inline fields select context and baseline method, edit bandpass/baseline
percentage ranges, RFI threshold and window size, and QA window fraction,
smoothing and noise estimator. Ranges support adding and removing pairs.
Fields and JSON stay synchronized; supported optional defaults are displayed
without adding parameters until edited. Invalid numbers or ranges block
processing until corrected. Expand the advanced JSON editor to reorder or remove steps,
or add digitiser-specific pipelines. All implemented built-in steps are listed;
unconfigured steps appear unchecked with disabled fields. Checking one adds it
with initial parameters in its appropriate stage (BASELINE before QA).
Unchecking a configured step retains its parameters but bypasses execution.
Unconfigured entries are not inserted into the JSON or processing request.
Reset to Config Profile restores the profile configuration.

Process/Reprocess applies edits to a new processing run. **Save Pipeline
Configuration** persists the edited pipeline to the selected profile's
`PipelineConfig.json`, after validation, and confirms the destination. Percentage
range pairs stay compact. Reset to Config Profile restores the most recently
saved configuration. Saving does not rerun processing or change existing results.
Completed results retain an exact `pipeline-config.json` snapshot and each
reanalysis retains it. Changing the pipeline requires Reprocess observation.
Selected corrected aggregates show “Pipeline baseline subtraction applied”.

Interactive Frequency/Velocity baselines are additional view adjustments to
the processed aggregate; choosing No subtraction restores that aggregate,
not the original SPR. Cached analysis does not rerun the pipeline or modify
aggregated data. Update plots also refreshes individual and all-SKY Total Power
plots at both resolutions and their `total-power-seconds.csv` / `total-power-scans.csv`
exports. An update from Frequency uses its aggregate baseline and MHz fitting
windows; an update from Velocity or L–V uses the Velocity baseline and window
bounds. Total Power titles identify the source. The fitted correction is applied
to each physical scan's recorded seconds across its usable passband, preserving
real brightness variation; it is not refitted independently per second. Scan
resolution shows the means of those corrected seconds. LOAD powers are unchanged.

The compact `native-total-power.npz` / `.json` cache stores original pipeline
totals and responses of the existing flag/RFI reconstruction to constant and
channel-index inputs. These allow exact affine baseline corrections without
retaining full per-second spectra or introducing a different reconstruction
policy. Each update starts from this immutable cache; No subtraction restores
pipeline totals. Older results require one Reprocess observation to create the
cache. Until then, a correction hides Total Power with a reprocessing explanation
rather than presenting stale powers. The legacy `*-sky-apr.csv` remains an
export of the pipeline output.

`flatten` retains a flat continuum increase while removing the fitted slope;
disable or remove pipeline BASELINE and select interactive No subtraction when
the original calibrated total power is required.
