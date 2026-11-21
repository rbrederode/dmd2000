"""Generate and cache individual Velocity options without updating other spectra."""
import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np

from ui.opt_reanalysis import save_velocity, velocity_record


def render_selected_product(request, out, progress):
    source = Path(request['source_results'])
    result = json.loads((source/'result.json').read_text())
    row = next(row for row in result['aggregates'] if row['id'] == request['aggregate_product']['aggregate_id'])
    selection = dict(lab_comparison=request.get('lab_comparison'), terminal_velocity=request.get('terminal_velocity'))
    settings = result['settings']
    signature = dict(selection=selection, settings=settings, pipeline=result.get('pipeline_config'))
    if selection['lab_comparison']:
        cube = Path(os.environ.get('DMD_LAB_FITS', '~/github/lab-fit/lab.fit')).expanduser()
        signature['lab_cube'] = [str(cube.resolve()), cube.stat().st_mtime_ns, cube.stat().st_size] if cube.is_file() else str(cube)
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    for path in source.iterdir():
        if path.is_file() and path.name not in ('manifest.json', 'result.json', 'results.zip', 'analysis-settings.json'):
            shutil.copy2(path, out/path.name)
    products = row.setdefault('velocity_products', {})
    product = products.get(key)
    if product and all((out/name).is_file() for name in product['views'].values()):
        progress('Using cached options for selected aggregate', .95)
    else:
        progress('Loading selected aggregate spectrum (1/1)', .1)
        labels = json.loads((source/'native-spectra.json').read_text())
        index, label = next((index, label) for index, label in enumerate(labels) if label['id'] == row['id'])
        with np.load(source/'native-spectra.npz', allow_pickle=False) as arrays:
            record = velocity_record(arrays, index, label, row, settings, result)
        product = dict(selection=selection, views={})
        if selection['terminal_velocity']:
            from obs.terminal_velocity import measure_terminal_velocity
            progress('Measuring terminal velocity for selected aggregate (1/1)', .35)
            measurement = measure_terminal_velocity(record, selection['terminal_velocity']['method'],
                settings['baseline_vmin'], settings['baseline_vmax'])
            record['terminal_velocity'] = product['terminal_velocity'] = measurement
            filename = f"{row['id']}-{key}-velocity-terminal.png"
            save_velocity([record], out/filename, result['observation'], settings['baseline'],
                          settings['baseline_vmin'], settings['baseline_vmax'], terminal=measurement)
            product['views']['velocity_terminal'] = filename
            (out/f"{row['id']}-{key}-terminal-velocity.json").write_text(json.dumps(measurement, indent=2, allow_nan=False)+'\n')
        if selection['lab_comparison']:
            from ui.opt_lab import render_lab_comparison
            row['views'].pop('lab', None)
            row['views'].pop('lab_terminal', None)
            progress('Comparing selected aggregate with LAB survey (1/1)', .5)
            render_lab_comparison([record], selection['lab_comparison'], settings, out, result,
                                  progress=lambda message, fraction: progress(message, .5+.4*(fraction-.94)/.035))
            product['lab_comparison'] = result['lab_comparison']
            for view in ('lab', 'lab_terminal'):
                if view in row['views']:
                    original = row['views'][view]
                    filename = f"{row['id']}-{key}-{view.replace('_','-')}.png"
                    (out/original).rename(out/filename)
                    product['views'][view] = filename
            for suffix in ('lab.json', 'lab.csv', 'lab-sweep.csv'):
                path = out/f"{row['id']}-{suffix}"
                if path.is_file():
                    path.rename(out/f"{row['id']}-{key}-{suffix}")
        products[key] = product
    for view in ('lab', 'lab_terminal', 'velocity_terminal'):
        row['views'].pop(view, None)
    row.pop('terminal_velocity', None)
    row.pop('lab_comparison', None)
    row['views'].update(product['views'])
    for field in ('terminal_velocity', 'lab_comparison'):
        if field in product:
            row[field] = product[field]
    result.pop('lab_comparison', None)
    if 'lab_comparison' in product:
        result['lab_comparison'] = product['lab_comparison']
    return result
