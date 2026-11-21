"""Export plot measurements and render maps without measuring spectra again."""
import csv
import json
import math
from pathlib import Path
import shutil
import tempfile

from matplotlib import pyplot as plt
from obs.dist_map import plot_hi_snr_map_from_csv, _validate_steps

COLUMNS = ('scan_id', 'gain_db', 'integration_time_s', 'centre_freq_hz', 'sample_rate_hz',
           'galactic_longitude_deg', 'galactic_latitude_deg', 'velocity_snr', 'frequency_snr')


def eligible(row):
    return row.get('kind') == 'SKY' and row.get('pointing') == 'SIDEREAL_TRACK'


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError('Distribution settings must be an object')
    column = options.get('snr_column', 'velocity_snr')
    if column not in ('velocity_snr', 'frequency_snr'):
        raise ValueError('Choose velocity_snr or frequency_snr')
    width, height = float(options.get('l_step', 15)), float(options.get('b_step', 15))
    _validate_steps(width, height)
    stretch = options.get('stretch', 'log')
    if stretch not in ('linear', 'asinh', 'log'):
        raise ValueError('Choose a linear, asinh or log colour stretch')
    shape = options.get('cell_shape', 'diamond')
    if shape not in ('rectangle', 'diamond'):
        raise ValueError('Choose rectangle or diamond cells')
    maximum = options.get('snr_max')
    maximum = None if maximum in (None, '') else float(maximum)
    if maximum is not None and (not math.isfinite(maximum) or maximum <= 0):
        raise ValueError('SNR upper limit must be finite and greater than zero')
    return dict(snr_column=column, l_step=width, b_step=height, stretch=stretch, snr_max=maximum, cell_shape=shape)


def snr(row, basis):
    metric = row.get(basis + '_metrics') or {}
    number = metric.get('peak_snr') if metric.get('status') == 'measured' else None
    return number if number is not None and math.isfinite(number) else None


def export_distribution(result, out):
    """One CSV row per eligible aggregate, including unavailable SNRs as blanks."""
    out = Path(out)
    rows = [row for row in result['aggregates'] if eligible(row)
            and row.get('longitude') is not None and row.get('latitude') is not None]
    if not rows:
        result.pop('distribution', None)
        result.get('views', {}).pop('distribution', None)
        (out / 'distribution.csv').unlink(missing_ok=True)
        (out / 'distribution.png').unlink(missing_ok=True)
        return
    if any(any(row.get(key) is None for key in ('gain_db', 'integration_s', 'centre_freq_hz', 'sample_rate_hz')) for row in rows):
        result.pop('distribution', None)
        result.get('views', {}).pop('distribution', None)
        for name in ('distribution.csv', 'distribution.png'):
            (out / name).unlink(missing_ok=True)
        warning = 'Reprocess this observation once to cache acquisition details for the SNR Distribution export.'
        if warning not in result.setdefault('warnings', []):
            result['warnings'].append(warning)
        return
    options = validate_options(result.get('distribution', {}))
    result['distribution'] = dict(options, aggregates=len(rows), csv='distribution.csv')
    with (out / 'distribution.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(scan_id=row['scan_id'], gain_db=row.get('gain_db'),
                integration_time_s=row.get('integration_s'), centre_freq_hz=row.get('centre_freq_hz'),
                sample_rate_hz=row.get('sample_rate_hz'), galactic_longitude_deg=row['longitude'],
                galactic_latitude_deg=row['latitude'], velocity_snr=snr(row, 'velocity'), frequency_snr=snr(row, 'frequency')))
    render_map(result, out, options)


def render_map(result, out, options):
    out = Path(out)
    path = out / 'distribution.png'
    path.unlink(missing_ok=True)
    result['views'].pop('distribution', None)
    result['distribution'].update(options)
    result['distribution'].pop('points', None)
    label = 'Velocity' if options['snr_column'] == 'velocity_snr' else 'Frequency'
    try:
        # Adapt the full export to the reusable renderer's simple input contract.
        # The temporary input is not included in the user's export ZIP.
        with tempfile.TemporaryDirectory(prefix='.distribution-input-', dir=out) as temporary:
            simple = Path(temporary) / 'map.csv'
            plotted_rows = []
            with (out / 'distribution.csv').open(newline='') as source, simple.open('w', newline='') as destination:
                reader = csv.DictReader(source)
                writer = csv.writer(destination)
                writer.writerow(['long', 'lat', 'snr'])
                for row in reader:
                    writer.writerow([row['galactic_longitude_deg'], row['galactic_latitude_deg'], row[options['snr_column']]])
                    value = row[options['snr_column']]
                    if value and math.isfinite(float(value)):
                        plotted_rows.append(row)
            figure, axis = plot_hi_snr_map_from_csv(simple, l_step=options['l_step'], b_step=options['b_step'],
                stretch=options.get('stretch', 'log'), snr_max=options.get('snr_max'), cell_shape=options.get('cell_shape', 'diamond'),
                title=f"{result['observation']} — SNR Distribution\n{label} peak SNR · {options.get('cell_shape', 'diamond')} cells {options['l_step']:g}° × {options['b_step']:g}° · repeated hits averaged")
        try:
            figure.set_dpi(150)
            figure.canvas.draw()
            # Match the exact PNG transform, including the constrained layout.
            points = []
            aggregates = {row['scan_id']: row for row in result['aggregates']}
            for row in plotted_rows:
                aggregate = aggregates.get(row['scan_id'])
                if aggregate is None:
                    continue
                longitude, latitude = float(row['galactic_longitude_deg']), float(row['galactic_latitude_deg'])
                x = -((longitude + 180) % 360 - 180) * math.pi / 180
                px, py = axis.transData.transform((x, math.radians(latitude)))
                points.append(dict(aggregate_id=aggregate.get('id'), scan_id=row['scan_id'],
                                   longitude=longitude, latitude=latitude,
                                   x=float(px / figure.bbox.width), y=float(1 - py / figure.bbox.height)))
            result['distribution']['points'] = points
            figure.savefig(path, dpi=150)
            result['distribution']['colour_limits'] = [float(axis.collections[0].norm.vmin), float(axis.collections[0].norm.vmax)]
        finally:
            plt.close(figure)
        result['views']['distribution'] = path.name
        result.get('view_errors', {}).pop('distribution', None)
    except ValueError as error:
        result.setdefault('view_errors', {})['distribution'] = f'{label} SNR map unavailable: {error}. Try the other SNR source.'


def render_distribution_product(request, out, progress):
    source = Path(request['source_results'])
    result = json.loads((source / 'result.json').read_text())
    for path in source.iterdir():
        if path.is_file() and path.name not in ('manifest.json', 'result.json', 'results.zip', 'analysis-settings.json'):
            shutil.copy2(path, out / path.name)
    progress('Rendering SNR Distribution from exported measurements', .5)
    render_map(result, out, validate_options(request['distribution_product']))
    return result


def export_cli_distribution(observation, sky_scans, display, output_dir, baseline):
    """CLI OPT companion export, using prepared velocity metrics and shared frequency preparation."""
    import tempfile
    import numpy as np
    from sdk.models.target import PointingType
    from sdk.models.scan import ScanState
    from obs.obs_display import save_frequency_record
    from sdp.channel_mask import masked_values
    rows = []
    for scan in sky_scans:
        model = scan.scan_model
        target = observation.obs_model.get_target_by_index(model.tgt_idx)
        if model.status != ScanState.COMPLETE or target is None or target.pointing != PointingType.SIDEREAL_TRACK or target.sky_coord is None:
            continue
        galactic = target.sky_coord.galactic
        velocity = next((record for record in display.lsr_spectra
                         if (record['target_id'], record['freq_scan']) == (model.tgt_idx, model.freq_scan)), {})
        frequency = (model.center_freq + np.fft.fftshift(np.fft.fftfreq(model.spectral_resolution, d=1/model.sample_rate))) / 1e6
        record = dict(target_id=model.tgt_idx, freq_scan=model.freq_scan, frequency_mhz=frequency,
                      power=masked_values(scan.mpr, scan.mpr_flags), channel_flags=scan.mpr_flags,
                      velocity=velocity['velocity'][::-1] if velocity else None, qa_settings=velocity.get('qa_settings'))
        try:
            prepared = save_frequency_record(record, None, observation.obs_model.obs_id, baseline)
            frequency_metrics = prepared['spectral_metrics']
        except ValueError:
            frequency_metrics = None
        rows.append(dict(scan_id=model.scan_id, kind='SKY', pointing='SIDEREAL_TRACK', gain_db=model.gain,
            centre_freq_hz=model.center_freq, sample_rate_hz=model.sample_rate,
            integration_s=float(sum(entry.get('secs', 0) for key, entry in observation.int_data_arrays.items()
                                    if key[:2] == (model.tgt_idx, model.freq_scan))),
            longitude=float(galactic.l.deg), latitude=float(galactic.b.deg),
            velocity_metrics=velocity.get('spectral_metrics'), frequency_metrics=frequency_metrics))
    if not rows:
        return []
    output = Path(output_dir).expanduser()
    result = dict(observation=observation.obs_model.obs_id, aggregates=rows, views={})
    written = []
    with tempfile.TemporaryDirectory(prefix='.distribution-', dir=output) as temporary:
        export_distribution(result, temporary)
        for path in Path(temporary).iterdir():
            destination = output / (observation.obs_model.obs_id + '-' + path.name)
            path.replace(destination)
            written.append(destination)
    return written
