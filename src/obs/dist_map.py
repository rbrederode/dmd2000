"""Plot SNR Distribution from CSV measurements; no scan/QA files are read.

CSV input needs just long, lat and snr columns (linear SNR ratios).
This module has no knowledge of observations, velocity/frequency views or QA.
Repeated measurements in a cell contribute equally to its arithmetic mean.
"""
import argparse
import csv
from pathlib import Path

import numpy as np
from matplotlib import pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.colors import Normalize, AsinhNorm, FuncNorm
from matplotlib.patches import Polygon
from matplotlib.ticker import FuncFormatter


def wrap_lon_deg(longitude):
    return -((np.asarray(longitude) + 180) % 360 - 180)


def _validate_steps(l_step, b_step):
    for name, step, extent in [('l_step', l_step, 360), ('b_step', b_step, 180)]:
        if not np.isfinite(step) or not 0 < step <= extent or not np.isclose(extent / step, round(extent / step)):
            raise ValueError(f'{name} must be positive, finite and divide {extent} degrees exactly')
        if extent / step > 3600:
            raise ValueError(f'{name} is too small (maximum 3600 cells per axis)')


def read_snr_csv(path):
    """Read a simple longitude/latitude/SNR CSV.

    Blank/nonfinite SNRs are unavailable measurements and are skipped. Malformed
    coordinates or nonnumeric nonblank SNRs are reported with their row number.
    """
    with Path(path).expanduser().open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        headers = {name.strip().lower().replace(' ', '_'): name for name in reader.fieldnames or []}
        def column(names):
            return next((headers[name] for name in names if name in headers), None)
        longitude = column(('galactic_longitude_deg', 'longitude', 'long', 'lon', 'l'))
        latitude = column(('galactic_latitude_deg', 'latitude', 'lat', 'b'))
        snr = column(('snr',))
        if not longitude or not latitude or not snr:
            raise ValueError('CSV needs long, lat and snr columns')
        cells = []
        for index, row in enumerate(reader, 2):
            raw = row.get(snr)
            if raw is None or not raw.strip():
                continue
            try:
                l, b, ratio = float(row[longitude]), float(row[latitude]), float(raw)
            except (ValueError, TypeError) as error:
                raise ValueError(f'Invalid coordinate/SNR value in CSV row {index}') from error
            if not np.isfinite(ratio):
                continue
            if not np.all(np.isfinite([l, b])) or not -90 <= b <= 90:
                raise ValueError(f'Invalid Galactic coordinates in CSV row {index}')
            cells.append(dict(l=l % 360, b=b, snr=ratio))
        return cells


def mean_cells(cells, l_step=5., b_step=5., cell_shape="rectangle"):
    """Bin on cell centres at multiples of the steps, with b=0 centred.

    Longitude wraps at 0/360; the two polar rows are clipped to the poles.
    SNR is averaged in linear units, with one vote per supplied measurement.
    """
    _validate_steps(l_step, b_step)
    if cell_shape not in ('rectangle', 'diamond'):
        raise ValueError('Cell shape must be rectangle or diamond')
    buckets = {}
    for cell in cells:
        l, b, snr = float(cell['l']), float(cell['b']), float(cell['snr'])
        if not np.all(np.isfinite([l, b, snr])) or not -90 <= b <= 90:
            raise ValueError('Cells need finite longitude, latitude and SNR, with latitude -90..90')
        li = int(np.floor((l % 360) / l_step + .5)) % round(360 / l_step)
        bi = min(round(180 / b_step), int(np.floor((b + 90) / b_step + .5)))
        if cell_shape == 'diamond':
            # A tiled diamond lattice: round in the two diagonal coordinates.
            u = int(np.floor(l % 360 / l_step + b / b_step + .5))
            v = int(np.floor(l % 360 / l_step - b / b_step + .5))
            centre = (((u + v) * l_step / 2) % 360, (u - v) * b_step / 2)
        else:
            centre = (li * l_step, -90 + bi * b_step)
        buckets.setdefault(centre, []).append(snr)
    return [dict(l=l, b=b, snr=float(np.mean(values)), count=len(values))
            for (l, b), values in sorted(buckets.items())]


def _make_cell_polygons(l, b, l_step, b_step, cell_shape='rectangle'):
    centre = (l + 180) % 360 - 180
    w, h = l_step / 2, b_step / 2
    vertices = ([(centre-w, b), (centre, b+h), (centre+w, b), (centre, b-h)]
                if cell_shape == 'diamond' else
                [(centre-w, b-h), (centre-w, b+h), (centre+w, b+h), (centre+w, b-h)])

    def clip(points, coordinate, bound, keep_greater):
        output = []
        for previous, current in zip(points[-1:] + points[:-1], points):
            inside = lambda point: (point[coordinate] >= bound if keep_greater else point[coordinate] <= bound)
            if inside(previous) != inside(current):
                fraction = (bound - previous[coordinate]) / (current[coordinate] - previous[coordinate])
                output.append(tuple(a + fraction * (z-a) for a, z in zip(previous, current)))
            if inside(current):
                output.append(current)
        return output

    polygons = []
    for shift in (-360, 0, 360):
        points = [(x + shift, y) for x, y in vertices]
        for coordinate, bound, greater in [(0, -180, True), (0, 180, False), (1, -90, True), (1, 90, False)]:
            points = clip(points, coordinate, bound, greater) if points else []
        if len(points) >= 3:
            points = np.asarray(points)
            if np.ptp(points[:, 0]) > 0 and np.ptp(points[:, 1]) > 0:
                polygons.append(Polygon(np.column_stack([-np.radians(points[:, 0]), np.radians(points[:, 1])]), closed=True))
    return polygons


AUTO_UPPER_PERCENTILE = 99.
MIN_PERCENTILE_CELLS = 30


def snr_colour_norm(cells, stretch='linear', snr_max=None):
    """Zero-anchored contrast from populated cell means, never empty sky/polygons."""
    if stretch not in ('linear', 'asinh', 'log'):
        raise ValueError('Colour stretch must be linear, asinh or log')
    values = np.asarray([cell['snr'] for cell in cells], dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        raise ValueError('No finite SNR values for colour scaling')
    if snr_max is not None:
        upper = float(snr_max)
        if not np.isfinite(upper) or upper <= 0:
            raise ValueError('SNR upper limit must be finite and greater than zero')
        policy = 'fixed upper limit'
    else:
        percentile = len(values) >= MIN_PERCENTILE_CELLS
        upper = float(np.percentile(values, AUTO_UPPER_PERCENTILE) if percentile else np.max(values))
        upper = max(upper, 1.) if upper <= 0 else upper
        policy = 'auto P99' if percentile else 'auto maximum'
    if stretch == 'asinh':
        norm = AsinhNorm(linear_width=1., vmin=0., vmax=upper, clip=True)
    elif stretch == 'log':
        # log(1 + SNR) preserves a meaningful zero, unlike LogNorm.
        norm = FuncNorm((np.log1p, np.expm1), vmin=0., vmax=upper, clip=True)
    else:
        norm = Normalize(vmin=0., vmax=upper, clip=True)
    return norm, policy


def plot_hi_snr_map(cells, l_step=5., b_step=5., cmap='viridis', title=None, snr_limits=None,
                    *, snr_max=None, stretch='linear', cell_shape='rectangle'):
    measurements = list(cells)
    cells = mean_cells(measurements, l_step, b_step, cell_shape)
    if not cells:
        raise ValueError('No usable SNR measurements for this map')
    patches, values = [], []
    for cell in cells:
        polygons = _make_cell_polygons(cell['l'], cell['b'], l_step, b_step, cell_shape)
        patches.extend(polygons)
        values.extend([cell['snr']] * len(polygons))
    if snr_limits is not None:
        if len(snr_limits) != 2 or snr_limits[0] != 0 or snr_max is not None:
            raise ValueError('Colour limits start at zero; specify only snr_max, or snr_limits=(0, maximum)')
        snr_max = snr_limits[1]
    norm, policy = snr_colour_norm(cells, stretch, snr_max)
    figure = plt.figure(figsize=(12, 6), layout='constrained')
    axis = figure.add_subplot(111, projection='mollweide')
    collection = PatchCollection(patches, array=np.asarray(values), cmap=cmap, norm=norm,
                                 edgecolor='white', linewidth=.3)
    axis.add_collection(collection)
    axis.scatter(wrap_lon_deg([point['l'] for point in measurements]) * np.pi / 180,
                 np.radians([point['b'] for point in measurements]), s=9, color='black', zorder=4)
    axis.xaxis.set_major_formatter(FuncFormatter(lambda radians, position: f'{-np.degrees(radians):g}°'))
    axis.set_facecolor('white')
    axis.grid(True, alpha=.35)
    axis.set(xlabel='Galactic longitude', ylabel='Galactic latitude')
    axis.set_title(title or 'SNR Distribution', pad=30)
    clipped_high, clipped_low = any(v > norm.vmax for v in values), any(v < 0 for v in values)
    extend = 'both' if clipped_high and clipped_low else 'max' if clipped_high else 'min' if clipped_low else 'neither'
    colour_name = 'log(1 + SNR)' if stretch == 'log' else stretch
    figure.colorbar(collection, ax=axis, orientation='horizontal', pad=.08, extend=extend).set_label(
        f'Mean peak SNR (linear ratio) · {colour_name} colours · {policy}')
    return figure, axis


def plot_hi_snr_map_from_csv(path, *, l_step=5., b_step=5., title=None, snr_max=None, stretch='linear', cell_shape='rectangle'):
    return plot_hi_snr_map(read_snr_csv(path), l_step, b_step, title=title, snr_max=snr_max, stretch=stretch, cell_shape=cell_shape)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', required=True, type=Path, help='CSV containing Galactic coordinates and SNR')
    parser.add_argument('--l-step', type=float, default=5., help='Cell width in degrees (default 5)')
    parser.add_argument('--b-step', type=float, default=5., help='Cell height in degrees (default 5)')
    parser.add_argument('--cell-shape', choices=('rectangle', 'diamond'), default='rectangle')
    parser.add_argument('--snr-max', type=float, help='Fixed positive colour maximum; default max for <30 cells, P99 otherwise')
    parser.add_argument('--stretch', choices=('linear', 'asinh', 'log'), default='linear',
                        help='Colour stretch only; log uses log(1+SNR) to include zero')
    parser.add_argument('--output', type=Path, help='Save PNG instead of opening an interactive plot')
    args = parser.parse_args(argv)
    try:
        figure, _ = plot_hi_snr_map_from_csv(args.csv, l_step=args.l_step, b_step=args.b_step,
                                            snr_max=args.snr_max, stretch=args.stretch, cell_shape=args.cell_shape)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.output:
        destination = args.output.expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(destination, dpi=150)
        plt.close(figure)
    else:
        plt.show()


if __name__ == '__main__':
    main()
