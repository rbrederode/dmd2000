# Observation Processing Tool (OPT)

`obs.opt` reprocesses recorded observations, combines accepted scan iterations
per target/frequency scan, and displays and exports calibrated spectra. Its
longitude–velocity (L–V) view maps Galactic longitude against LSRK velocity,
with colour and contours representing baseline-subtracted calibrated power.

## Browser workspace

For scan selection and completed-aggregate browsing without terminal pauses,
use the integrated frontend prototype:

```bash
PYTHONPATH=src python -m ui.opt_service --data-root ~/samples
```

Open **http://127.0.0.1:8765/#results** to use **Observation Processing**.
Choose a directory, then select an observation from the scrollable metadata
catalogue and open it. The directory, Find, filter and Open controls share one
pane. Choose the configuration profile under **Analysis settings** at the bottom;
it selects the processing pipeline and changing it requires reprocessing.
Review input checkboxes, process the whole batch, then select completed
aggregates in the left pane. Signal, velocity, total-power timeline and L–V
views appear on the right. Baseline/contour changes can update plots from cached
spectra; changing input scans requires reprocessing. Results are saved in a
separate session directory and can be downloaded as a ZIP.

See the [frontend workspace guide](../src/ui/frontend_prototype/README.md) for
configuration, controls and current limitations. The terminal workflow below
remains available and retains its existing interactive prompts.

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

## SNR Distribution

OPT produces a SNR Distribution CSV and map when completed SKY aggregates
have `SIDEREAL_TRACK` targets with valid Galactic coordinates. RA/Dec targets
are converted to Galactic coordinates. LOAD, drift, offset, five-point and
non-sidereal targets are excluded.

The frontend always shows the **SNR Distribution** tab. Observations without suitable sidereal-track SKY targets show an explanation instead of a map. It defaults to
**Velocity SNR**, with 15° × 15° Diamond cells, Log (zero-safe) colours and an automatic Upper SNR limit. Choose Frequency SNR
or change the cell dimensions and click **Update map** to redraw from the exported
measurements. This launches a server worker but does not replay scans, fit
baselines or recalculate SNR. Cell dimensions must divide 360 degrees (longitude)
and 180 degrees (latitude), respectively. Cell height is a map-cell parameter,
not beam smoothing. Unobserved cells remain blank; repeated hits in the same cell
are coloured by their unweighted arithmetic mean SNR in linear units.

### Measurement dots, spectra and cell shape

Black dots mark the original CSV measurement coordinates, before cell averaging.
Click a dot to highlight it and show its saved individual Frequency or Velocity
plot beside the map, following the map's **applied** SNR source. The preview uses
the same saved image selection as that aggregate's individual plot tab (including
saved LAB/terminal variants when selected). Selecting a dot never launches a
processing job or regenerates a spectrum. Missing saved images are explained.
Use **Preview** or the aggregate list to select a scan; repeated clicks at
identical coordinates cycle the measurements there. On narrower screens the
spectrum appears below the map. **Open image** beside Download CSV and **Open spectrum** below Preview open the full-size saved images. The map receives more space than the preview. The question-mark button in the upper-right corner opens settings help for the current plot tab.
Older map products need **Update map** once to add the clickable point metadata.

**Cell shape** selects Rectangle or Diamond (default). Width and height are the
full longitude/latitude spans, in degrees. Diamonds tile in diagonal coordinates:
round `u = l/width + b/height` and `v = l/width - b/height` to select a cell,
whose centre is `(width*(u+v)/2, height*(u-v)/2)`. Thus its half-diagonals
are width/2 and height/2. Measurements are averaged inside the selected shape;
switching shape can change which measurements share a cell. This is binning,
not interpolation or colour blending into unmeasured cells. Shapes are clipped
at the poles and split at the longitude seam. CSV rows and SNR measurements
remain unchanged. CLI `obs.dist_map` also accepts `--cell-shape rectangle|diamond`
and draws the same original measurement dots, without any observation identity
or UI dependency.

### Distribution colour scale

The colour scale starts at **SNR 0**, not the smallest observed SNR. **Upper SNR**
can be set to a fixed positive value for comparisons between observations. Leave
it blank for automatic scaling: use the maximum cell-average SNR for fewer than
30 populated cells, or the 99th percentile for 30 or more. Limits are calculated
*after* averaging repeat hits, using each populated cell once. Unobserved sky,
unavailable values and duplicate polygons at the map seam never affect them.
If every populated value is zero/nonpositive, the automatic upper limit is 1.

**Colours** selects Linear, Asinh, or Log (default). These affect only the colour
mapping; SNR values and the CSV stay unchanged. Asinh uses a transition width of
1 SNR unit. Log uses `log(1 + SNR)`, which includes SNR 0, rather than a logarithm
that would require a positive lower limit. The colour bar still reports actual
linear SNR ratios, and marks clipped values with an extension arrow. Colours below
zero/above the upper limit are clipped; measured zero stays distinct from blank,
unobserved sky. The actual colour limits and selected stretch are retained in
the result manifest. Click **Update map** after changing colour controls; this
redraws cached measurements without reprocessing scans or calculating SNR again.


The frontend's **Download CSV** and export ZIP include `distribution.csv`, with
one row per eligible aggregate and these columns:

```csv
scan_id,gain_db,integration_time_s,centre_freq_hz,sample_rate_hz,galactic_longitude_deg,galactic_latitude_deg,velocity_snr,frequency_snr
```

`scan_id` is the aggregate's scientific scan ID. Integration time is accumulated
physical-scan seconds, rather than the aggregate model's iteration count. SNRs
are linear peak-to-noise ratios, using the same channel flags, restricted plot
ranges and configured noise policy as the standard individual Frequency and
Velocity annotations. Their returned measurements are retained and reused for
export, rather than remeasured by the map code. An unavailable measurement is
blank, not zero; an aggregate remains in the CSV even if one SNR is unavailable.
The PNG is `distribution.png`; both files are included in the ZIP. Updating
spectral plots refreshes both SNR columns and the map. Existing processed jobs
need one reprocessing run to cache the additional acquisition metadata.

Command-line OPT also exports `<observation-id>-distribution.csv` and, when
velocity SNR is available, `<observation-id>-distribution.png`. It uses its prepared
velocity plot metrics and the same frequency preparation/measurement function as
the frontend. No separate noise or SNR estimator is introduced. The map describes
observed SNR; gain, integration time and other acquisition settings affect it.

### Standalone CSV map utility

`obs/dist_map.py` is independent of OPT, observations and QA metadata. It reads
just Galactic longitude, latitude and a linear SNR value:

```csv
long,lat,snr
30,0,12
31,0,18
45,5,9
```

```bash
python src/obs/dist_map.py \
  --csv measurements.csv \
  --l-step 5 \
  --b-step 5 \
  --snr-max 25 \
  --stretch asinh \
  --output hi-snr-map.png
```

Omit `--snr-max` for automatic zero-anchored limits. `--stretch` accepts `linear`,
`asinh` and `log`. Omit `--output` for an interactive display. Longitude wraps at 0/360 and map cells
crossing ±180 degrees are split at the projection seam. Cells are centred on
multiples of the longitude step, and latitude centres include the Galactic plane
for the default 5-degree grid. Longitudes increase towards the left. Coordinates
use degrees; latitude must lie in −90..90. Blank/nonfinite SNR entries are skipped,
and malformed numeric values are reported.

SNR source selection belongs to the frontend adapter: it creates a temporary
`long,lat,snr` CSV from the user's chosen column, passes that to `dist_map.py`, then
removes the temporary input. The reusable renderer has no Velocity/Frequency
selection option and never reads QA JSON files. The downloadable export always
retains both SNRs and the complete acquisition details above.
