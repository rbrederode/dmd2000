# Baseline subtraction in observation velocity plots

The LSRK plot produced by `obs/opt.py` and `ObsDisplay.display_lsr()` now
subtracts an independent baseline for every aggregated target/frequency scan.
The default is a linear fit, using the same calculation as `lab_profiler.py`.
The shared function is `util.convert.subtract_spectrum_baseline()`.

Only the baseline is subtracted. There is no peak normalization, multiplication,
LAB temperature matching, clipping of negative values or amplitude scaling.
Units remain arbitrary calibrated power units. The original MPR arrays,
frequency-domain plots and integrated-power products are unchanged.

## Command-line controls

From the repository root, the processing tool can be run as
`PYTHONPATH=src python -m obs.opt --obs OBS_ID --dir DIRECTORY`.
Add these options to your normal observation processing command:

```bash
--observation-baseline linear --baseline-vmin -150 --baseline-vmax 150
```

The defaults are `linear`, -150 km/s and +150 km/s. Available methods:

- `linear`: select the lowest 20% of valid powers within the fitting interval,
  fit a straight line, evaluate it across the spectrum and subtract it.
- `constant`: subtract the median of that lowest 20% of powers.
- `none`: retain the original calibrated power in the plot.

For more control, constrain the candidate fitting channels to line-free
windows within the fitting interval. Repeat the option for multiple windows:

```bash
--observation-baseline linear \
--baseline-vmin -150 --baseline-vmax 150 \
--baseline-window -90 -80 --baseline-window 100 150
```

These windows are examples; inspect the spectrum to choose regions without
emission. The lowest-20% selection still operates inside their union. A linear
fit removes a level and slope, not all baseline curvature. The background is
approximately zero, with noise allowed above and below zero.

Flagged and non-finite samples never contribute to the fit. Linear/constant
fits require at least ten usable fitting channels and at least three selected
lower-envelope samples. If a scan cannot be fitted, the tool logs its target
and frequency scan and skips it, rather than silently plotting uncorrected
power amongst corrected curves. Other valid scans are still plotted. If all
scans are skipped, no LSRK plot or velocity CSV is written for that run.

The plot identifies the method in its title and labels its amplitude axis as
baseline-subtracted. A horizontal line marks zero power.

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
from util.convert import subtract_spectrum_baseline

corrected, baseline = subtract_spectrum_baseline(
    velocity, power, method="linear", fit_mask=fit_mask,
)
```

The helper accepts matching one-dimensional arrays and preserves existing
masks while masking invalid samples. Plain arrays return plain arrays with
NaNs at invalid samples. It does not require a positive emission peak;
`lab_profiler.py` retains that separate requirement for comparison/plot scaling.
The helper also supports the legacy `read-ray` method for the LAB profiler.

`ObsDisplay.display_lsr()` accepts `observation_baseline`, `baseline_vmin`,
`baseline_vmax` and `baseline_windows` keyword arguments with the same semantics
as the command-line options.

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
valid lower edge and lowest valid upper edge across all columns, making their
top and bottom boundaries align. Spectra are not shifted, resampled or padded;
internal masked samples remain gaps. If the columns share no velocity coverage,
the L–V plot is skipped with a log message.

Coordinates come from each target's stored sky direction transformed to
Galactic coordinates. If only Alt/Az is available, the resolved direction from
the preceding LSRK plot is used. The longitude axis wraps across 360° without
placing nearby 359° and 1° targets at opposite ends. The panel title reports
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

The Python API is `display.display_lv(obs, output_path)`, called after
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
OPT retains its increasing longitude direction and shared truncated velocity
coverage.

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
