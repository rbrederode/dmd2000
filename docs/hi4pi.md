# HI4PI spectrum extraction and fitting

`src/util/hi4pi.py` extracts a Galactic H I spectrum from HI4PI CAR FITS
cubes. It can either:

1. extract and plot a spectrum at one Galactic position, optionally convolved
   to a larger Gaussian beam; or
2. sweep a grid of Galactic positions around a nominal coordinate, compare
   each HI4PI spectrum with an observation spectrum, and plot the best match.

The utility uses the HI4PI cubes published through the CDS archive. It can
download missing cubes into a local cache, or use FITS cubes already downloaded
to disk.

## Requirements

The utility requires:

```text
numpy
scipy
astropy
astroquery
matplotlib
```

From the repository root, run it with:

```bash
python src/util/hi4pi.py ...
```

## Beam convolution

HI4PI has a native beam of approximately `16.2` arcminutes (`0.27` degrees).
The `--beam` value is the requested final Gaussian FWHM in degrees. The
requested beam must be wider than the native HI4PI beam.

For example, to produce an approximately 8-degree beam profile:

```bash
python src/util/hi4pi.py \
  --lon 70 \
  --lat 0 \
  --beam 8.0 \
  --cache-dir /Users/r.brederode/github/dmd2000/src/hi4pi_cache
```

The convolution is a spatial Gaussian average around the requested Galactic
position. With the default `--cutoff-sigma 3.5`, an 8-degree beam samples out
to approximately 12 degrees from the centre. This can require several
neighbouring HI4PI cubes.

## Single-position extraction

Single-position extraction is the default when `--sweep` is not supplied.
`--lon` and `--lat` are Galactic longitude and latitude in degrees.

```bash
python src/util/hi4pi.py \
  --lon 70 \
  --lat 0 \
  --beam 8.0 \
  --vmin -150 \
  --vmax 200 \
  --cache-dir hi4pi_cache
```

The default output files are:

```text
hi4pi_spectrum.csv
hi4pi_spectrum.png
```

The CSV columns are:

```text
v_lsr_km_s,T_b_K
```

The plot shows HI4PI brightness temperature against LSR radio velocity and
limits the displayed x-axis using `--vmin` and `--vmax`.

### Using local cubes

Use `--cube` once for each local FITS cube. Supplying local cubes prevents the
utility from downloading them:

```bash
python src/util/hi4pi.py \
  --lon 70 \
  --lat 0 \
  --beam 8.0 \
  --cube /Users/r.brederode/github/dmd2000/src/hi4pi_cache/CAR_E03.fits \
  --cube /Users/r.brederode/github/dmd2000/src/hi4pi_cache/CAR_E02.fits
```

If `--cube` is omitted, the utility queries the HI4PI cube catalogue, selects
nearby CAR tiles, and downloads missing files into `--cache-dir`. The default
cache directory is `hi4pi_cache` relative to the current working directory.

## Galactic-coordinate sweep

Sweep mode compares an observation spectrum with a convolved HI4PI spectrum at
each candidate position. Use `--sweep` together with an observation CSV.

The observation CSV must contain these columns:

```text
target_id,freq_scan,velocity_km_s,calibrated_power
```

Rows with blank or non-numeric `calibrated_power` are ignored. At least ten
populated samples are required for the selected target and frequency scan.

Example for target 5 at nominal Galactic coordinates `(70, 0)`:

```bash
python src/util/hi4pi.py \
  --sweep \
  --file /Users/r.brederode/samples/rotation/ODT-2026-10-27T165909Z-dish003-Rotation_vlsr.csv \
  --target-id 5 \
  --freq-scan 0 \
  --lon 70 \
  --lat 0 \
  --lon-range "[-3,3,1]" \
  --lat-range "[-3,3,1]" \
  --beam 8.0 \
  --fit-vmin -150 \
  --fit-vmax 150 \
  --cache-dir /Users/r.brederode/github/dmd2000/src/hi4pi_cache
```

The range syntax is:

```text
[minimum offset,maximum offset,step]
```

The offsets are applied to the nominal Galactic longitude or latitude. The
example tests longitude `67` through `73` degrees and latitude `-3` through
`+3` degrees, giving 49 candidate positions.

The fitting process:

1. loads the selected observation target and frequency scan;
2. extracts an HI4PI spectrum at each candidate Galactic position;
3. convolves each reference spectrum to the requested `--beam`;
4. interpolates the reference onto the observation velocity grid;
5. restricts the comparison to `--fit-vmin` through `--fit-vmax`;
6. independently normalizes the observation and reference by their maxima;
7. masks normalized values at or below `0.01`;
8. calculates RMS difference and correlation; and
9. selects the candidate with the lowest RMS.

The observation can optionally have a baseline removed before normalization.
This is useful when calibrated power contains a receiver or continuum floor,
as in a plot where the observation remains near `0.85` away from the H I line
while the HI4PI profile returns close to zero. Use:

```bash
--observation-baseline constant
```

for a constant lower-envelope subtraction, or:

```bash
--observation-baseline linear
```

for a sloping lower-envelope subtraction. The baseline is estimated from the
lowest 20% of observation samples in the fitting velocity range. The default
is `none`, which preserves the previous behavior. This is baseline removal
and relative normalization, not an absolute calibration of the observation to
HI4PI kelvin units.

The output plot contains the normalized observation and best-fitting HI4PI
profile. Its filename is derived from the observation CSV, target ID, and
frequency scan, for example:

```text
ODT-2026-10-27T165909Z-dish003-Rotation_vlsr_target5_scan0_hi4pi_fit.png
```

The terminal output reports the RMS and correlation for every candidate and
the best-fitting Galactic longitude and latitude.

### Refining a fit

Start with a coarse search, then run a finer search around the best candidate.
For example:

```bash
--lon-range "[-1,1,0.25]" \
--lat-range "[-1,1,0.25]"
```

Because an 8-degree beam is broad, nearby candidate positions can produce
highly correlated reference spectra. A fitted coordinate should therefore be
interpreted as an effective beam centre, not as a precise measurement of the
dish axis or feed position.

## Command-line parameters

| Parameter | Meaning |
|---|---|
| `--sweep` | Run observation fitting instead of single-position extraction. |
| `--lon` | Nominal Galactic longitude in degrees; required. |
| `--lat` | Nominal Galactic latitude in degrees; required. |
| `--beam` | Final Gaussian beam FWHM in degrees; default `4.8`. |
| `--cube` | Local FITS cube; repeat for multiple cubes. |
| `--cache-dir` | Directory for downloaded HI4PI cubes; default `hi4pi_cache`. |
| `--output` | Output path for single-position CSV/PNG output. In sweep mode its stem is used for the fitted plot name. |
| `--vmin` | Single-position plot minimum velocity; default `-150`. |
| `--vmax` | Single-position plot maximum velocity; default `200`. |
| `--cutoff-sigma` | Gaussian integration radius in sigma; default `3.5`. |
| `--grid-step` | Spatial integration grid spacing in degrees; default `0.1`. |
| `--file` | Observation CSV; required with `--sweep`. |
| `--target-id` | Target identifier to fit; required with `--sweep`. |
| `--freq-scan` | Frequency scan identifier; default `0`. |
| `--lon-range` | Galactic longitude offset range; default `[-3,3,1]`. |
| `--lat-range` | Galactic latitude offset range; default `[-3,3,1]`. |
| `--fit-vmin` | Comparison minimum velocity; default `-150`. |
| `--fit-vmax` | Comparison maximum velocity; default `150`. |
| `--observation-baseline` | Baseline handling in sweep mode: `none`, `constant`, or `linear`; default `none`. |

Show all options with:

```bash
python src/util/hi4pi.py --help
```

## Interpretation and limitations

The utility compares spectral shape after optional baseline removal and
independent normalization. It does not fit an absolute amplitude calibration
or convert calibrated power into HI4PI kelvin units. Baseline removal is
intended to remove receiver/continuum structure so that the observation and
survey profiles share a comparable zero level for shape comparison.

In sweep mode, the best position can reflect telescope pointing error, feed
offset, mechanical alignment, or limitations of the spectral match. The
algorithm cannot distinguish those causes from the spectrum alone.

The HI4PI reference is beam-convolved, but the observation spectrum must
represent a comparable effective beam for the result to have a direct physical
interpretation. The selected position is also limited by HI4PI angular
resolution and by the sweep step size.
