# Observation line of sight in the Milky Way

Direct Galactic coordinates, with Jodrell Bank/current UTC defaults:

```bash
python -m util.milkyway_plot --galactic 82.2361 -5.6143
```

Or specify site and datetime explicitly, using exactly the same flags as LAB:

```bash
python -m util.milkyway_plot \
  --galactic 82.2361 -5.6143 \
  --location 53.234123 -2.305485 77 \
  --dt 2026-10-24T11:25:50Z --no-show
```

`--location` takes geodetic latitude, east-positive longitude (degrees), and height
(metres); defaults are 53.234123, -2.305485, 77. `--dt` takes an ISO datetime,
defaults to current UTC, assumes UTC when no timezone is present, and converts
explicit offsets to UTC. Direct Galactic coordinates already specify direction;
site and datetime provide context without changing that direction.

The shared parser and resolver in `obs.obs_pointing` validate both tools identically.
Use finite coordinates, Galactic latitude ±90°, and site latitude ±90°/longitude
±180°. Galactic longitude wraps to 0–360°. Direct coordinates cannot be combined
with metadata IDs. `--location` and `--dt` are available only in direct mode.

From `src`:

```bash
python -m util.milkyway_plot \
  --dir ~/samples/first_light \
  --obs ODT-2026-10-24T112512Z-dish003-Test \
  --scan ODT-2026-10-24T112512Z-dish003-Test-1-0-0
```

The utility shares metadata lookup with `util.lab_profiler`: it uses the
observation's site, the physical scan's recording midpoint, and RA/Dec (or other
stored sky coordinates). For fixed pointings, recorded acquisition Alt/Az takes
precedence over requested Alt/Az. Missing scan errors list the available IDs.
No IQ files, HEALPix dependency, or network downloads are required.

The PNG is saved as `<scan-id>-milkyway.png` in metadata mode. Direct mode uses
Galactic coordinates and UTC datetime in the filename, but omits the generated
name from the plot title. A matching JSON sidecar records the input mode,
coordinates, site, datetime, and any observation/scan IDs.

Outputs are saved in `--dir` (current directory by default). Use `--output-dir PATH`
to save elsewhere and `--no-show` to skip the interactive window. Direct execution
with `python util/milkyway_plot.py` is also supported.

The arrow starts at the Sun and follows Galactic longitude projected onto the
Galactic plane: 0° points towards the centre, 90° left, 180° down, 270° right.
Galactic latitude is shown in the caption; the face-on illustration cannot depict
height above the plane. At the Galactic poles no in-plane arrow is drawn.
The arrow has no physical distance scale. The bundled picture is an artist's
impression with approximate positional registration, intended for orientation.

Image credit: NASA/JPL-Caltech/ESO/R. Hurt;
[ESO eso1339e](https://www.eso.org/public/images/eso1339e/).
