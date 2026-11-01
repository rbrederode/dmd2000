# Observation Processing Tool (OPT)

`obs.opt` reprocesses recorded observations, combines accepted scan iterations
per target/frequency scan, and displays and exports calibrated spectra. Its
longitude–velocity (L–V) view maps Galactic longitude against LSRK velocity,
with colour and contours representing baseline-subtracted calibrated power.

## Run OPT

Install the repository dependencies and activate your Python environment.
From the repository root:

```bash
PYTHONPATH=src python -m obs.opt \
  --dir ~/samples/rotation \
  --obs ODT-2026-10-27T165909Z-dish003-Rotation \
  --profile default
```

Use the observation ID without its `-obs.json` suffix. The directory must
contain that observation metadata file and the associated recorded scan
products, normally `*-spr.csv` summed-power files. A velocity CSV alone is not
an OPT input. Choose the processing profile under `src/config/<profile>` that
matches your setup; `default` is the default profile.

Alternatively, from `src`:

```bash
python -m obs.opt --dir ~/samples/rotation --obs OBSERVATION_ID
```

Inspect all options without processing an observation:

```bash
PYTHONPATH=src python -m obs.opt --help
```

## Interactive sequence

1. Review the observation and calibration/SKY scan tables. At the exclusion
   prompt, enter comma-separated scan indexes such as `1-0-0,2-0-0` to toggle
   them out of processing. Entering an excluded index again restores it.
   Press Enter when finished.
2. OPT processes accepted scans and displays the aggregated frequency/power
   and integrated-total-power views, followed by the LSRK velocity-versus-power
   plot. Each target/frequency scan gets its own curve and baseline fit.
3. At `Press Enter to show the longitude–velocity (L–V) plot...`, press Enter
   **in the terminal** to open the new L–V figure. The velocity plot remains
   available.
4. Press Enter at `Press Enter to finish...` to finish the session. Saved PNGs
   and CSVs remain available after the session ends.

If no usable LSRK spectra are prepared, OPT skips the L–V prompt. Missing
pointing/timing metadata or insufficient baseline samples are logged; other
usable scans can still be plotted.

## Baseline subtraction without amplitude scaling

The default `--observation-baseline linear` independently fits every
aggregated target/frequency scan. It selects the lowest 20% of valid powers
inside the fitting interval, fits a straight line, and subtracts it across
that spectrum. There is no peak normalization or survey-temperature scaling.
Negative residuals are retained. Original calibrated spectra are preserved.

```bash
PYTHONPATH=src python -m obs.opt \
  --dir ~/samples/rotation \
  --obs ODT-2026-10-27T165909Z-dish003-Rotation \
  --observation-baseline linear \
  --baseline-vmin -150 --baseline-vmax 150
```

Use `constant` to subtract the median of the same lowest-power samples, or
`none` to plot the original calibrated powers. Flagged/non-finite channels are
excluded. Linear/constant modes require at least ten fitting samples and three
lower-envelope samples; a scan that cannot be fitted is logged and skipped.

Optionally restrict candidate fitting channels to a union of line-free windows:

```bash
--baseline-vmin -150 --baseline-vmax 150 \
--baseline-window -90 -80 --baseline-window 100 150
```

These are example windows; choose regions without emission for your observation.
Windows are intersected with the fitting interval. The lowest-20% selection
still applies within that intersection. The baseline limits control fitting,
not the displayed velocity-axis limits. A linear fit removes level and slope,
but may leave curved receiver backgrounds.

## L–V plot and contours

OPT reads Galactic longitude/latitude from each target's stored sky direction.
For an Alt/Az target it uses the observing-time/site direction already resolved
for the LSRK plot. The plot header reports the coordinate ranges.

The L–V figure uses the same white-to-blue palette and `asinh` colour mapping
as the LAB profiler's reference style. The x-axis is Galactic longitude and
the y-axis is LSRK velocity in km/s. All columns/panels share one colour scale
in **calibrated power [a.u.]**, not kelvin. Different frequency setups and
repeated longitudes get separate panels. Sparse longitude samples form broad
columns whose boundaries lie midway between neighbouring pointings; these
widths do not represent telescope beam resolution.

The visible velocity interval is the intersection of the columns' valid ranges:
the highest lower edge and lowest upper edge. This truncates column ends to
line up, without shifting or resampling the native spectra. Internal masks
remain gaps. With no common interval, the L–V plot is skipped.

Thin magenta contours default to 5%, 10%, 20%, 40% and 80% of the shared positive
maximum, labelled as actual calibrated power. Override them with:

```bash
--lv-contours 0.005 0.01 0.02 0.04 0.08
```

The contour overlay alone uses 2 km/s Gaussian FWHM velocity smoothing and
interpolation between observed longitude centres. Smoothing/interpolation
respect masked channels and velocity gaps; contours do not bridge a known
missing longitude in a frequency panel. The coloured columns, line spectra
and CSV values remain unchanged. Sparse-pointing contours are visual
connections between measurements, not additional observations or identified
spiral arms.

Adjust or disable the overlay:

```bash
--lv-contour-smoothing 1
```

```bash
--lv-no-contours
```

Set `--lv-contour-smoothing 0` for unsmoothed contours. Only one label per level
is placed, and a single-longitude panel has no contour overlay.

## Output files

Outputs are written to `--dir`. Rerunning the same observation uses the same
filenames and replaces the generated files.

| File | Contents |
|---|---|
| `<obs>-opt.log` | Processing log. |
| `<obs>-sky-apr.csv` | Aggregated total calibrated power per integrated-scan position. |
| `<obs>-sky-apr.png` | Standalone integrated-total-power plot. |
| `<obs>-sky-apr-lsrk.png` | LSRK velocity-versus-power curves. |
| `<obs>_vlsr.csv` | Native velocity samples, original power, baseline and corrected power. |
| `<obs>-sky-apr-lv.png` | Longitude–velocity colour/contour diagram. |

The velocity CSV columns are:

```text
target_id,freq_scan,velocity_km_s,calibrated_power,baseline,baseline_subtracted_power
```

`calibrated_power` retains the original value. `baseline_subtracted_power` is
`calibrated_power - baseline`; it is the value used for the velocity curves
and native L–V colour columns. Invalid/flagged powers remain blank. Contour
smoothing and colour stretches do not change these exports. LAB and HI4PI
profilers continue reading the original `calibrated_power` column, so their
own baseline subtraction is not applied twice to corrected data.

## Command-line reference

| Option | Default | Purpose |
|---|---|---|
| `-o`, `--obs` | Required | Observation ID, without `-obs.json`. |
| `-d`, `--dir` | `~/samples` | Observation/scan input directory and generated-output directory. |
| `-p`, `--profile` | `default` | Processing configuration under `src/config`. |
| `--observation-baseline` | `linear` | `linear`, `constant`, or `none` for velocity spectra. |
| `--baseline-vmin` | `-150` | Minimum baseline-fitting LSRK velocity in km/s. |
| `--baseline-vmax` | `150` | Maximum baseline-fitting LSRK velocity in km/s. |
| `--baseline-window MIN MAX` | None | Optional line-free interval in km/s; repeat for multiple windows. |
| `--lv-contours POWER ...` | Automatic fractions | Positive, strictly increasing contour levels in calibrated power units. |
| `--lv-no-contours` | Off | Disable the L–V contour overlay. |
| `--lv-contour-smoothing` | `2` | Contour-only Gaussian FWHM in km/s; zero disables smoothing. |
| `--make-fil` | Off | Export recorded filterbanks as SIGPROC `.fil` files and exit. |
| `--fil-output-dir` | `--dir` | Destination for `--make-fil` output. |
| `-h`, `--help` | — | Show usage and exit. |

## Filterbank-only export

For observations with recorded `*-fb.dat` filterbank products:

```bash
PYTHONPATH=src python -m obs.opt \
  --make-fil --dir ~/samples/observation --obs OBSERVATION_ID \
  --fil-output-dir /tmp/dmd2000-filterbanks
```

This path exits after export and does not run the interactive baseline/L–V
workflow. Filterbank normalization is separate from the unscaled amplitude
handling described above. See [filterbank processing](filterbank_processing.md)
for export behaviour and data formats.

Further detail: [baseline and observed L–V implementation](observation_baseline.md),
[predicted LAB L–V diagrams](lab_profiler.md), and [HI4PI fitting](hi4pi.md).
