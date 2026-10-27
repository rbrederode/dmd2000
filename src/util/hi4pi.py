#!/usr/bin/env python3
"""Download a CDS HI4PI Galactic cube and extract a beam-matched spectrum."""
import argparse
from pathlib import Path
from urllib.parse import quote
import numpy as np
import matplotlib.pyplot as plt
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from astropy.utils.data import download_file
from astroquery.vizier import Vizier

CATALOG = 'J/A+A/594/A116/cubes_gal'
# select_cube only selects CAR tiles; CDS groups files by projection.
BASE = 'https://cdsarc.cds.unistra.fr/ftp/J/A+A/594/A116/CUBES/GAL/CAR/'
NATIVE_FWHM = 16.2 / 60.0

def angular_distance(lon, lat, lon0, lat0):
    lon, lat, lon0, lat0 = map(np.deg2rad, (lon, lat, lon0, lat0))
    cosd = np.sin(lat) * np.sin(lat0) + np.cos(lat) * np.cos(lat0) * np.cos(lon - lon0)
    return np.rad2deg(np.arccos(np.clip(cosd, -1, 1)))

def select_cube(lon, lat, radius):
    # Get metadata only; no spectral data is downloaded in this query.
    viz = Vizier(columns=['GLON', 'GLAT', 'WCSproj', 'FileName'], row_limit=-1)
    tables = viz.get_catalogs(CATALOG)
    if not tables:
        raise RuntimeError('CDS returned no cube catalogue.')
    t = tables[0]
    choices = []
    for r in t:
        if str(r['WCSproj']).strip().upper() != 'CAR':
            continue
        cl, cb = float(r['GLON']), float(r['GLAT'])
        # Conservative selection: keep the complete convolution aperture
        # inside the nominal 20 x 20 degree CAR tile.
        dl = abs((lon - cl + 180) % 360 - 180)
        db = abs(lat - cb)
        if dl + radius < 9.8 and db + radius < 9.8:
            choices.append((dl**2 + db**2, str(r['FileName']).strip()))
    if not choices:
        raise RuntimeError('No single CAR cube contains the complete beam aperture. '
                           'Try a smaller beam, or use overlapping cubes/mosaicking.')
    return min(choices)[1]

def get_cube(name, directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / Path(name).name
    if not path.exists():
        url = BASE + quote(Path(name).name)
        print('Downloading:', url)
        cached = download_file(url, cache=True, timeout=180)
        import shutil
        shutil.copyfile(cached, path)
    return path

def extract(path, lon, lat, target_fwhm):
    if target_fwhm < NATIVE_FWHM:
        raise ValueError('Target beam cannot be narrower than native HI4PI beam.')
    sigma_kernel = np.sqrt(target_fwhm**2 - NATIVE_FWHM**2) / (2*np.sqrt(2*np.log(2)))
    radius = 3.5 * sigma_kernel
    with fits.open(path, memmap=True) as hdul:
        hdu = next(h for h in hdul if h.data is not None and h.data.ndim == 3)
        data = hdu.data
        wcs = WCS(hdu.header)
        if not wcs.has_celestial or not wcs.has_spectral:
            raise ValueError('FITS cube lacks celestial or spectral WCS.')
        cwcs = wcs.celestial
        x0, y0 = cwcs.world_to_pixel(SkyCoord(lon*u.deg, lat*u.deg, frame='galactic'))
        # Derive a conservative pixel bounding box from WCS pixel scale.
        from astropy.wcs.utils import proj_plane_pixel_scales
        scale = min(proj_plane_pixel_scales(cwcs))
        pixrad = int(np.ceil(radius / scale)) + 3
        ix, iy = int(round(float(x0))), int(round(float(y0)))
        x1, x2 = max(0, ix-pixrad), min(data.shape[-1], ix+pixrad+1)
        y1, y2 = max(0, iy-pixrad), min(data.shape[-2], iy+pixrad+1)
        if x1 >= x2 or y1 >= y2:
            raise ValueError('Target outside FITS cube.')
        yy, xx = np.mgrid[y1:y2, x1:x2]
        sky = cwcs.pixel_to_world(xx, yy).galactic
        distance = angular_distance(sky.l.deg, sky.b.deg, lon, lat)
        weights = np.exp(-0.5*(distance/sigma_kernel)**2)
        weights[distance > radius] = 0
        if not np.any(weights > 0):
            raise ValueError('No pixels in convolution aperture.')
        # FITS spectral WCS returns physical units; use the native spectral axis.
        spec = wcs.sub(['spectral'])
        pixels = np.arange(data.shape[0], dtype=float)
        spectral_world = spec.pixel_to_world(pixels)
        if not hasattr(spectral_world, 'to'):
            raise ValueError('Unrecognised spectral axis.')
        try:
            velocity = spectral_world.to(u.km/u.s).value
        except u.UnitConversionError as exc:
            raise ValueError('Cube spectral axis is not velocity. Inspect header CTYPE3/CUNIT3.') from exc
        ctype = str(hdu.header.get('CTYPE3', '')).upper()
        if not any(k in ctype for k in ('VRAD', 'VELO')):
            raise ValueError(f'Unexpected spectral frame {ctype}; verify LSR radio convention.')
        unit = str(hdu.header.get('BUNIT', '')).strip()
        if unit and unit.upper() not in ('K', 'KELVIN'):
            print('Warning: BUNIT is', unit, '(expected K).')
        # Process a channel at a time: avoids allocating a large spectral subcube.
        spectrum = np.empty(len(velocity), dtype=float)
        coverage = np.empty(len(velocity), dtype=float)
        for k in range(len(velocity)):
            plane = np.asarray(data[k, y1:y2, x1:x2], dtype=float)
            good = np.isfinite(plane)
            valid_weight = np.sum(weights[good])
            coverage[k] = valid_weight / weights.sum()
            spectrum[k] = np.sum(plane[good] * weights[good]) / valid_weight if valid_weight > 0 else np.nan
    if np.nanmin(coverage) < 0.98:
        print(f'Warning: incomplete data coverage (minimum {np.nanmin(coverage):.1%}).')
    return velocity, spectrum

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--lon', type=float, required=True, help='Galactic longitude (degrees)')
    ap.add_argument('--lat', type=float, required=True, help='Galactic latitude (degrees)')
    ap.add_argument('--beam', type=float, default=4.8, help='Final effective FWHM (degrees)')
    ap.add_argument('--cube', type=Path, help='Use an existing Galactic-coordinate FITS cube')
    ap.add_argument('--cache-dir', type=Path, default=Path('hi4pi_cache'))
    ap.add_argument('--output', type=Path, default=Path('hi4pi_spectrum.csv'))
    ap.add_argument('--vmin', type=float, default=-150)
    ap.add_argument('--vmax', type=float, default=200)
    args = ap.parse_args()
    if args.beam <= NATIVE_FWHM:
        raise SystemExit(f'Beam must exceed native HI4PI {NATIVE_FWHM:.3f} degrees.')
    sigma = np.sqrt(args.beam**2 - NATIVE_FWHM**2) / 2.354820045
    radius = 3.5*sigma
    path = args.cube or get_cube(select_cube(args.lon, args.lat, radius), args.cache_dir)
    print('Using cube:', path)
    velocity, temp = extract(path, args.lon, args.lat, args.beam)
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
