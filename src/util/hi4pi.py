#!/usr/bin/env python3
"""Extract an HI4PI spectrum convolved to a Gaussian beam from multiple CDS CAR cubes.

Requires: numpy, scipy, astropy, astroquery, matplotlib.
The sky is sampled on a common longitude/latitude grid. Each grid point is
assigned to exactly one source cube, avoiding double-counting tile overlaps.
"""
import argparse
import csv
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import quote
import shutil
from urllib.error import URLError
import numpy as np
import matplotlib.pyplot as plt
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy.utils.data import download_file
from astroquery.vizier import Vizier
from scipy.ndimage import map_coordinates

CATALOG = 'J/A+A/594/A116/cubes_gal'
BASE = 'https://cdsarc.cds.unistra.fr/ftp/J/A+A/594/A116/CUBES/GAL/CAR/'
BASE_FALLBACKS = (
    BASE,
    'https://cdsarc.u-strasbg.fr/ftp/J/A+A/594/A116/CUBES/GAL/CAR/',
)
NATIVE_FWHM = 16.2 / 60.0

def separation(lon, lat, lon0, lat0):
    a, b, c, d = np.deg2rad(np.broadcast_arrays(lon, lat, lon0, lat0))
    cosd = np.sin(b)*np.sin(d) + np.cos(b)*np.cos(d)*np.cos(a-c)
    return np.rad2deg(np.arccos(np.clip(cosd, -1, 1)))

def catalog_tiles(lon, lat, radius):
    viz = Vizier(columns=['GLON', 'GLAT', 'WCSproj', 'FileName'], row_limit=-1)
    tables = viz.get_catalogs(CATALOG)
    if not tables:
        raise RuntimeError('CDS returned no HI4PI cube catalogue.')
    tiles = []
    for row in tables[0]:
        if str(row['WCSproj']).strip().upper() != 'CAR':
            continue
        cl, cb = float(row['GLON']), float(row['GLAT'])
        dl = abs((lon - cl + 180) % 360 - 180)
        db = abs(lat - cb)
        # Broad candidate search; exact coverage is checked from the FITS WCS.
        if dl <= radius + 11 and db <= radius + 11:
            tiles.append((dl*dl + db*db, str(row['FileName']).strip()))
    if not tiles:
        raise RuntimeError('No nearby CAR tiles in CDS catalogue.')
    return [name for _, name in sorted(tiles)]

def get_cube(name, cache):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / Path(name).name
    if not path.exists():
        errors = []
        for base in BASE_FALLBACKS:
            url = base + quote(path.name)
            print('Downloading:', url, flush=True)
            try:
                downloaded = download_file(url, cache=True, timeout=240)
                shutil.copyfile(downloaded, path)
                break
            except (OSError, URLError, TimeoutError) as exc:
                errors.append(f'{url} ({exc})')
        else:
            raise RuntimeError(
                f'Failed to download HI4PI cube {path.name} from all mirrors: '
                + '; '.join(errors)
            )
    return path

def grid_points(lon, lat, sigma, radius, step):
    # Tangent-plane-like grid in longitude/latitude near the requested direction.
    # Correct longitude spacing at nonzero latitude; area weighting uses cos(latitude).
    coslat = max(np.cos(np.deg2rad(lat)), 0.05)
    dl = np.arange(-radius/coslat, radius/coslat + step, step)
    db = np.arange(-radius, radius + step, step)
    ll, bb = np.meshgrid(lon + dl, lat + db)
    valid_lat = (bb >= -90) & (bb <= 90)
    dist = separation(ll % 360, np.clip(bb, -90, 90), lon, lat)
    inside = valid_lat & (dist <= radius)
    yy, xx = np.where(inside)
    coords = SkyCoord((ll[inside] % 360)*u.deg, bb[inside]*u.deg, frame='galactic')
    weights = np.exp(-0.5*(dist[inside]/sigma)**2) * np.cos(np.deg2rad(bb[inside]))
    return coords, weights

def extract(paths, lon, lat, beam, cutoff_sigma=3.5, step=0.1):
    if isinstance(paths, (str, Path)):
        paths = [paths]
    sigma = np.sqrt(beam**2 - NATIVE_FWHM**2) / (2*np.sqrt(2*np.log(2)))
    coords, weights = grid_points(lon, lat, sigma, cutoff_sigma*sigma, step)
    n = len(weights)
    owner = np.full(n, -1, dtype=int)
    pixel_x = np.empty(n, dtype=float)
    pixel_y = np.empty(n, dtype=float)
    best = np.full(n, np.inf)
    with ExitStack() as stack:
        sources = []
        for path in paths:
            hdul = stack.enter_context(fits.open(path, memmap=True))
            hdu = next((h for h in hdul if h.data is not None and h.data.ndim == 3), None)
            if hdu is None:
                raise ValueError(f'No 3D FITS data in {path}')
            wcs = WCS(hdu.header)
            if not wcs.has_celestial or not wcs.has_spectral:
                raise ValueError(f'Missing celestial/spectral WCS in {path}')
            ctype = str(hdu.header.get('CTYPE3', '')).upper()
            if not any(k in ctype for k in ('VRAD', 'VELO')):
                raise ValueError(f'Unexpected spectral axis {ctype} in {path}')
            if hdu.header.get('BUNIT', 'K').strip().upper() not in ('K', 'KELVIN'):
                print(f'Warning: unusual brightness unit in {path}: {hdu.header.get("BUNIT")}')
            velocity = wcs.sub(['spectral']).pixel_to_world(np.arange(hdu.data.shape[0])).to(u.km/u.s).value
            if sources and (len(velocity) != len(sources[0][3]) or
                            not np.allclose(velocity, sources[0][3], atol=1e-5, rtol=0)):
                raise ValueError('Cube velocity axes differ; spectral regridding is required.')
            cwcs = wcs.celestial
            px, py = cwcs.world_to_pixel(coords)
            ny, nx = hdu.data.shape[-2:]
            # Margin avoids interpolating from outside an image at its edge.
            good = np.isfinite(px) & np.isfinite(py) & (px >= 0) & (px <= nx-1) & (py >= 0) & (py <= ny-1)
            # Prefer tile whose centre is closest in pixel coordinates, to avoid
            # seam interpolation and repeated coverage of overlapping tiles.
            centre_dist = ((px - (nx-1)/2)/nx)**2 + ((py - (ny-1)/2)/ny)**2
            update = good & (centre_dist < best)
            owner[update] = len(sources)
            pixel_x[update], pixel_y[update] = px[update], py[update]
            best[update] = centre_dist[update]
            sources.append((path, hdu.data, cwcs, velocity))
        covered = owner >= 0
        coverage = weights[covered].sum() / weights.sum()
        print(f'Spatial beam coverage: {coverage:.2%} ({covered.sum()}/{n} grid points)')
        if coverage < 0.98:
            raise RuntimeError(f'Only {coverage:.1%} of beam weight is covered. '
                               'Check catalogue tiles, WCS boundaries, or use a smaller beam.')
        for i, (path, _, _, _) in enumerate(sources):
            print(f'  {path.name}: {(owner == i).sum()} assigned sample points')
        vel = sources[0][3]
        result = np.full(len(vel), np.nan)
        # Read one velocity plane at a time; bilinear spatial interpolation.
        for k in range(len(vel)):
            numerator = denominator = 0.0
            for i, (_, data, _, _) in enumerate(sources):
                sel = np.where(owner == i)[0]
                if not len(sel):
                    continue
                samples = map_coordinates(np.asarray(data[k], dtype=float),
                                          [pixel_y[sel], pixel_x[sel]],
                                          order=1, mode='nearest', prefilter=False)
                finite = np.isfinite(samples)
                numerator += np.dot(samples[finite], weights[sel][finite])
                denominator += weights[sel][finite].sum()
            if denominator > 0:
                result[k] = numerator / denominator
        return vel, result

def parse_range(value):
    parts = value.strip("[]").split(",")
    if len(parts) != 3:
        raise ValueError(f'Invalid range: {value}; expected [min,max,step]')
    minimum, maximum, step = map(float, parts)
    if step <= 0 or maximum < minimum:
        raise ValueError(f'Invalid range: {value}')
    return minimum, maximum, step

def range_values(value):
    minimum, maximum, step = parse_range(value)
    return np.round(np.arange(minimum, maximum + step / 2, step), 10)

def load_observation(path, target_id, freq_scan):
    velocities, powers = [], []
    with path.open(newline='') as stream:
        for row in csv.DictReader(stream):
            if row.get('target_id') != target_id or row.get('freq_scan') != freq_scan:
                continue
            try:
                velocity = float(row['velocity_km_s'])
                power = float(row['calibrated_power'])
            except (KeyError, TypeError, ValueError):
                continue
            if np.isfinite(velocity) and np.isfinite(power):
                velocities.append(velocity)
                powers.append(power)
    if len(velocities) < 10:
        raise ValueError(
            f'Fewer than 10 populated samples found for target_id={target_id}, '
            f'freq_scan={freq_scan} in {path}'
        )
    order = np.argsort(velocities)
    return np.asarray(velocities)[order], np.asarray(powers)[order]

def compare_spectra(observation_velocity, observation_power,
                    reference_velocity, reference_power, vmin, vmax,
                    observation_baseline='none'):
    order = np.argsort(reference_velocity)
    reference_velocity = reference_velocity[order]
    reference_power = reference_power[order]
    valid = (
        np.isfinite(observation_velocity) & np.isfinite(observation_power)
        & (observation_velocity >= reference_velocity.min())
        & (observation_velocity <= reference_velocity.max())
        & (observation_velocity >= vmin) & (observation_velocity <= vmax)
    )
    if valid.sum() < 10:
        return np.inf, np.nan, None, None, None
    velocity = observation_velocity[valid]
    observed = observation_power[valid]
    reference = np.interp(velocity, reference_velocity, reference_power)
    finite = np.isfinite(observed) & np.isfinite(reference)
    velocity, observed, reference = velocity[finite], observed[finite], reference[finite]
    if observation_baseline != 'none':
        baseline_quantile = np.quantile(observed, 0.2)
        baseline_points = observed <= baseline_quantile
        if baseline_points.sum() < 3:
            return np.inf, np.nan, None, None, None
        if observation_baseline == 'linear':
            coefficients = np.polyfit(
                velocity[baseline_points],
                observed[baseline_points],
                1,
            )
            observed = observed - np.polyval(coefficients, velocity)
        else:
            observed = observed - np.median(observed[baseline_points])
    observed_max, reference_max = np.max(observed), np.max(reference)
    if observed_max <= 0 or reference_max <= 0:
        return np.inf, np.nan, None, None, None
    observed_norm, reference_norm = observed / observed_max, reference / reference_max
    mask = (
        np.isfinite(observed_norm) & np.isfinite(reference_norm)
        & (observed_norm > 0.01) & (reference_norm > 0.01)
    )
    if mask.sum() < 10:
        return np.inf, np.nan, None, None, None
    rms = float(np.sqrt(np.mean((observed_norm[mask] - reference_norm[mask]) ** 2)))
    correlation = float(np.corrcoef(observed_norm[mask], reference_norm[mask])[0, 1])
    return rms, correlation, velocity, observed_norm, reference_norm

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sweep', action='store_true',
                    help='Sweep Galactic offsets and fit an observation CSV')
    ap.add_argument('--lon', type=float, required=True, help='Galactic longitude (degrees)')
    ap.add_argument('--lat', type=float, required=True, help='Galactic latitude (degrees)')
    ap.add_argument('--beam', type=float, default=4.8, help='Final Gaussian FWHM (degrees)')
    ap.add_argument('--cube', type=Path, action='append', help='Local FITS cube; repeat for multiple cubes')
    ap.add_argument('--cache-dir', type=Path, default=Path('hi4pi_cache'))
    ap.add_argument('--output', type=Path, default=Path('hi4pi_spectrum.csv'))
    ap.add_argument('--vmin', type=float, default=-150)
    ap.add_argument('--vmax', type=float, default=200)
    ap.add_argument('--cutoff-sigma', type=float, default=3.5, help='Gaussian integration radius in sigma')
    ap.add_argument('--grid-step', type=float, default=0.1, help='Spatial integration grid spacing in degrees')
    ap.add_argument('--file', type=Path, help='Observation velocity CSV (required with --sweep)')
    ap.add_argument('--target-id', help='Observation target ID (required with --sweep)')
    ap.add_argument('--freq-scan', default='0')
    ap.add_argument('--lon-range', default='[-3,3,1]', help='Sweep longitude offsets [min,max,step]')
    ap.add_argument('--lat-range', default='[-3,3,1]', help='Sweep latitude offsets [min,max,step]')
    ap.add_argument('--fit-vmin', type=float, default=-150.0)
    ap.add_argument('--fit-vmax', type=float, default=150.0)
    ap.add_argument(
        '--observation-baseline',
        choices=('none', 'constant', 'linear'),
        default='none',
        help='Remove a lower-envelope baseline before normalization in sweep mode',
    )
    args = ap.parse_args()
    if not NATIVE_FWHM < args.beam or not 0 < args.cutoff_sigma <= 5 or not 0 < args.grid_step <= 0.5:
        ap.error('Beam must exceed 0.27 deg, cutoff-sigma in (0,5], grid-step in (0,0.5].')
    if args.sweep:
        if args.file is None or args.target_id is None:
            ap.error('--file and --target-id are required with --sweep')
        observation_velocity, observation_power = load_observation(
            args.file, args.target_id, args.freq_scan
        )
        lon_offsets, lat_offsets = range_values(args.lon_range), range_values(args.lat_range)
        sigma = np.sqrt(args.beam**2 - NATIVE_FWHM**2) / 2.354820045
        aperture = args.cutoff_sigma * sigma
        sweep_radius = aperture + max(np.max(np.abs(lon_offsets)), np.max(np.abs(lat_offsets)))
        paths = args.cube or [
            get_cube(name, args.cache_dir)
            for name in catalog_tiles(args.lon, args.lat, sweep_radius)
        ]
        results = []
        for lon_offset in lon_offsets:
            for lat_offset in lat_offsets:
                lon = (args.lon + lon_offset) % 360
                lat = args.lat + lat_offset
                if not -90 <= lat <= 90:
                    continue
                reference_velocity, reference_power = extract(
                    paths, lon, lat, args.beam, args.cutoff_sigma, args.grid_step
                )
                rms, correlation, velocity, observed_norm, reference_norm = compare_spectra(
                    observation_velocity, observation_power, reference_velocity,
                    reference_power, args.fit_vmin, args.fit_vmax,
                    args.observation_baseline,
                )
                print(f'l={lon:8.3f} b={lat:7.3f} RMS={rms:.6f} corr={correlation:.6f}')
                results.append((rms, correlation, lon, lat, velocity, observed_norm, reference_norm))
        valid = [result for result in results if np.isfinite(result[0])]
        if not valid:
            raise RuntimeError('No valid HI4PI comparison was found')
        rms, correlation, lon, lat, velocity, observed_norm, reference_norm = min(
            valid, key=lambda result: result[0]
        )
        output = args.output.with_name(
            f'{args.output.stem}_target{args.target_id}_scan{args.freq_scan}_fit.png'
        ) if args.output else args.file.with_name(
            f'{args.file.stem}_target{args.target_id}_scan{args.freq_scan}_hi4pi_fit.png'
        )
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(velocity, observed_norm, 'r', label='Observation')
        ax.plot(velocity, reference_norm, '--k', label='HI4PI')
        ax.set(xlim=(args.fit_vmin, args.fit_vmax), ylim=(-0.05, 1.05),
               xlabel='Radial velocity [km/s]', ylabel='Normalized brightness / power',
               title=f'HI4PI best fit: l={lon:.3f}°, b={lat:.3f}°; '
                     f'beam={args.beam:g}°, RMS={rms:.4f}, corr={correlation:.4f}')
        ax.legend()
        ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(output, dpi=150)
        plt.close(fig)
        print(f'Best fit: l={lon:.4f}°, b={lat:.4f}°')
        print(f'Saved: {output}')
        return 0

    sigma = np.sqrt(args.beam**2 - NATIVE_FWHM**2)/2.354820045
    radius = sigma*args.cutoff_sigma
    paths = args.cube or [get_cube(name, args.cache_dir) for name in catalog_tiles(args.lon, args.lat, radius)]
    print('Using cubes:', ', '.join(str(p) for p in paths))
    velocity, temp = extract(paths, args.lon, args.lat, args.beam, args.cutoff_sigma, args.grid_step)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(args.output, np.column_stack((velocity, temp)), delimiter=',',
               header='v_lsr_km_s,T_b_K', comments='')
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(velocity, temp, lw=1.2)
    ax.axvline(0, color='gray', linestyle='--', lw=0.8)
    ax.set(xlabel='LSR radio velocity (km/s)', ylabel='HI brightness temperature (K)',
           title=f'HI4PI l={args.lon:g}°, b={args.lat:g}° | FWHM={args.beam:g}°',
           xlim=(args.vmin, args.vmax))
    ax.grid(alpha=0.3)
    fig.tight_layout()
    png = args.output.with_suffix('.png')
    fig.savefig(png, dpi=170)
    print('Saved:', args.output, 'and', png)

if __name__ == '__main__':
    main()
