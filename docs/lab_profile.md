# LAB survey reference profiles

## Direct Galactic coordinates

No observation or scan metadata is needed in this mode. From `src`:

```bash
python -m util.lab_profiler --galactic 82.2361 -5.6143
```

The location defaults to Jodrell Bank: geodetic latitude 53.234123°, longitude
-2.305485° (east positive), height 77 m, matching the alston profile. The datetime
defaults to the current UTC time, captured once for the request. Override these:

```bash
python -m util.lab_profiler \
  --galactic 82.2361 -5.6143 \
  --location 53.234123 -2.305485 77 \
  --dt 2026-10-24T11:25:50Z \
  --output-dir ~/samples/lab-profiles --no-show
```

`--galactic` takes longitude and latitude in degrees; finite longitudes wrap to
0–360° and latitude must be within ±90°. `--location` takes latitude, longitude
and height in metres. Latitude must be within ±90° and longitude within ±180°;
all three values must be finite. `--dt` takes an ISO datetime, interprets a missing
timezone as UTC, and converts explicit offsets to UTC. A date without a time is
rejected. This is `--dt`, not `--time`.

Location and datetime provide request context: they do **not** change the extracted
LAB spectrum for a given Galactic direction. The survey is already in Galactic
coordinates and the LSR frame. No site/time Doppler correction is applied.

Output names contain longitude, latitude, and the UTC timestamp. The current
directory is used by default (`--dir` also controls this default); `--output-dir`
overrides it.

## Observation and scan metadata

From `src`, run:

```bash
python -m util.lab_profiler \
  --dir ~/samples/first_light \
  --obs ODT-2026-10-24T112512Z-dish003-Test \
  --scan ODT-2026-10-24T112512Z-dish003-Test-1-0-0
```

The scan ID must match a physical scan in the observation's `target_scans` metadata.
Both `--obs` and `--scan` are required together. They cannot be combined with
`--galactic`, `--location`, or `--dt`; this mode uses the recorded site and time.
The utility uses the observation's latitude, longitude and height, the scan's
recording midpoint (or acquisition time if recording times are unavailable), and
the target's sky coordinates. For fixed Alt/Az targets it prefers the scan's
recorded acquisition coordinates over the requested coordinates. It reads metadata
only, without loading IQ samples or reprocessing the observation.

Use `--lab-dir PATH` to supply extracted FITS slices. Otherwise it looks in
`~/Downloads/LAB_Healpix`. If no slices are found, it downloads NASA's approximately
138 MB archive and extracts the velocity slices into temporary storage. Allow
roughly 2.5 GB of free temporary disk space. The downloaded and extracted files are
removed after extraction of the profile, including on errors. Local files are left
untouched. A directory containing only some velocity slices produces that partial
velocity range; missing bins are not downloaded individually.

CSV, PNG and JSON outputs named `<scan-id>-lab-profile` are saved in `--dir`, or in
`--output-dir PATH`. Add `--no-show` to save without opening a plot window. A
noninteractive machine may also set `MPLBACKEND=Agg`. Direct execution via
`python util/lab_profiler.py` from `src` is supported.

In either mode the JSON sidecar records input mode, Galactic coordinates, UTC
datetime, site latitude/longitude/height, and any observation/scan IDs. The plot
caption includes this context. Existing CSV columns are unchanged. Importable
`save_profile` creates the JSON only when its optional `context` is provided.

Install the optional survey dependency in your active environment:

```bash
python -m pip install healpy
```

The shared `get_obs_pointing` function lives in `obs.obs_pointing`. The importable
functions `survey_slices`, `extract_profile` and `save_profile` live in
`util.lab_profiler` and can also be used independently. Missing scan errors list the
available IDs.
`build_parser` and `resolve_profile_context` expose the CLI input resolution logic.

Each point is the nearest Galactic HEALPix pixel in a 10 km/s LAB velocity slice.
This is not an average over the telescope beam. The survey is already in the LSR
frame: no observing-site Doppler correction is applied to its velocity bins.
`TEMPERATURE` is already mean brightness in kelvin; unlike the original proof of
concept, the utility does not divide it by the bin width. The weight/count column
records contributing survey channels; bins with no contributions are masked.
Earth orientation calculations use Astropy's local IERS data; keep these updated
when precise pointing corrections matter.

Source and scientific acknowledgements: [NASA LAMBDA LAB product description](https://lambda.gsfc.nasa.gov/product/foreground/fg_LAB_HI_Survey_info.html),
[downloads](https://lambda.gsfc.nasa.gov/product/foreground/fg_LAB_HI_Survey_get.html).
Cite Kalberla et al. (2005), A&A 440, 775; citation of Land & Slosar (2007) is also
appreciated. Acknowledge NASA's HEASARC/LAMBDA service when publishing results.
