# LAB survey reference profiles

From `src`, run:

```bash
python -m util.lab_profiler \
  --dir ~/samples/first_light \
  --obs ODT-2026-10-24T112512Z-dish003-Test \
  --scan ODT-2026-10-24T112512Z-dish003-Test-1-0-0
```

The scan ID must match a physical scan in the observation's `target_scans` metadata.
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

CSV and PNG outputs named `<scan-id>-lab-profile` are saved in `--dir`, or in
`--output-dir PATH`. Add `--no-show` to save without opening a plot window. A
noninteractive machine may also set `MPLBACKEND=Agg`. Direct execution via
`python util/lab_profiler.py` from `src` is supported.

Install the optional survey dependency in your active environment:

```bash
python -m pip install healpy
```

The importable functions `observation_pointing`, `survey_slices`, `extract_profile`
and `save_profile` can also be used independently. Missing scan errors list the
available IDs.

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
