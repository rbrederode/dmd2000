"""Compact per-scan cache and background pointing/animation products for OPT."""
import json
from contextlib import nullcontext
from datetime import datetime, timezone
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from models.scan import ScanModel, ScanState
from models.target import TargetModel, PointingType
from sdp.channel_mask import empty_channel_flags

logger = logging.getLogger(__name__)
CACHE_FILES = ("native-pointing.json", "native-pointing.npz")


def suggested_animation_interval(frame_count):
    """Aim for a ten-second loop, within 100–500 ms, in GIF timing units."""
    return max(100, min(500, round(1000 / frame_count) * 10)) if frame_count else 100


def set_animation_interval(path, interval_ms):
    """Update GIF delay fields while preserving every frame and its colour palette."""
    data = bytearray(path.read_bytes())
    if len(data) < 13 or data[:6] not in (b'GIF87a', b'GIF89a'):
        raise ValueError("Invalid animation GIF; generate the animation again")
    position = 13 + (3 * (2 ** ((data[10] & 7) + 1)) if data[10] & 128 else 0)
    delay = round(interval_ms / 10).to_bytes(2, 'little')
    frames = delays = 0
    try:
        while data[position] != 0x3B:  # GIF trailer
            block = data[position]
            if block == 0x21:  # Extension, followed by length-prefixed sub-blocks.
                if data[position + 1] == 0xF9:
                    if data[position + 2] != 4:
                        raise ValueError("Invalid GIF frame control")
                    data[position + 4:position + 6] = delay
                    delays += 1
                position += 2
            elif block == 0x2C:  # Image descriptor, optional local palette, LZW size.
                packed = data[position + 9]
                position += 10 + (3 * (2 ** ((packed & 7) + 1)) if packed & 128 else 0)
                position += 1
                frames += 1
            else:
                raise ValueError("Invalid GIF block")
            while data[position]:
                position += 1 + data[position]
            position += 1
    except IndexError as exc:
        raise ValueError("Incomplete animation GIF; generate the animation again") from exc
    if not frames or frames != delays:
        raise ValueError("Animation is missing frame timings; generate the animation again")
    path.write_bytes(data)
    return frames


class PointingCache:
    """Copy only physical-scan MPRs, flags and metadata before scans are released."""

    def __init__(self):
        self.arrays = {}
        self.scans = []

    def capture(self, scan):
        model = scan.scan_model
        if model.status != ScanState.COMPLETE or scan.mpr is None:
            return
        power = np.ma.asarray(scan.mpr, dtype=float).filled(np.nan).squeeze()
        if power.ndim != 1 or power.size != model.spectral_resolution:
            logger.warning("Pointing cache omitted invalid MPR for %s", model.scan_id)
            return
        index = len(self.scans)
        self.arrays[f"{index}_power"] = power.copy()
        flags = getattr(scan, "mpr_flags", None)
        self.arrays[f"{index}_flags"] = (np.asarray(flags).copy() if flags is not None else empty_channel_flags(power.shape))
        metadata = model.to_dict()
        fields = ("_type", "obs_id", "tgt_idx", "freq_scan", "scan_iter", "scan_type", "dig_id",
                  "created", "read_start", "read_end", "sample_rate", "spectral_resolution", "center_freq", "pointing_refs",
                  "tgt_acq_dt", "tgt_acq_altaz")
        self.scans.append({key: metadata[key] for key in fields})

    def save(self, folder, observation):
        from ui.opt_service import atomic_json
        observer = dict(obs_id=observation.obs_id, latitude=float(observation.latitude),
                        longitude=float(observation.longitude), height=float(observation.height),
                        targets=[target.to_dict() for target in observation.targets])
        atomic_json(folder / CACHE_FILES[0], {"version": 1, "observation": observer, "scans": self.scans})
        np.savez_compressed(folder / CACHE_FILES[1], **self.arrays)
        return bool(self.scans)


def load_frames(folder, aggregate=None, *, sky_only=False, include_spectra=True):
    """Resolve cached spectra and first pointing references, without scan replay."""
    from obs.animation import (AnimationFrame, build_animation_frames, earth_location_for_observation,
                               scan_pointing_for_animation)
    data = json.loads((folder / CACHE_FILES[0]).read_text())
    metadata = data["observation"]
    targets = {target.tgt_idx: target for target in map(TargetModel.from_dict, metadata["targets"])}
    observer = SimpleNamespace(**{key: metadata[key] for key in ("obs_id", "latitude", "longitude", "height")},
                               get_target_by_index=targets.get)
    location = earth_location_for_observation(observer)
    frames, errors = [], []
    with (np.load(folder / CACHE_FILES[1], allow_pickle=False) if include_spectra else nullcontext()) as arrays:
        for index, item in enumerate(data["scans"]):
            model = ScanModel.from_dict(item)
            if sky_only and model.scan_type.name != "SKY":
                continue
            if aggregate is not None and (model.tgt_idx, model.freq_scan, model.dig_id, model.scan_type.name) != (
                    aggregate["target_id"], aggregate["freq_scan"], aggregate["digitiser"], aggregate["kind"]):
                continue
            try:
                if include_spectra:
                    scan = SimpleNamespace(scan_model=model, mpr=arrays[f"{index}_power"], mpr_flags=arrays[f"{index}_flags"])
                    frames.extend(build_animation_frames(observer, [scan], location))
                else:
                    observed_at, direction, pointing_source = scan_pointing_for_animation(observer, model, location)
                    galactic = direction.galactic
                    frames.append(AnimationFrame(scan_id=model.scan_id, time=observed_at,
                        ra_deg=float(direction.ra.deg), dec_deg=float(direction.dec.deg),
                        galactic_l_deg=float(galactic.l.deg), galactic_b_deg=float(galactic.b.deg),
                        frequency_mhz=np.empty(0), spectrum=np.empty(0), pointing_source=pointing_source))
            except (ValueError, TypeError, AttributeError) as exc:
                errors.append(f"{model.scan_id}: {exc}")
    if not frames:
        detail = errors[0] if errors else "No matching completed scans are cached."
        raise ValueError(f"No pointings could be drawn. {detail}")
    return sorted(frames, key=lambda frame: frame.time), location, errors


def pointing_details(target, scans):
    """Describe the target and measured aggregate boundaries without estimating missing references."""
    import astropy.units as u

    def angle(value):
        return "Unavailable" if value is None else f"{float(u.Quantity(value, u.deg).to_value(u.deg)):.3f}°"

    def altaz(value):
        return "Unavailable" if value is None else f"Alt {angle(value.get('alt'))}, Az {angle(value.get('az'))}"

    def timestamp(value):
        if not isinstance(value, datetime):
            return "Unavailable"
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', ' UTC')

    def section(title, rows):
        return dict(title=title, rows=[dict(label=label, value=value) for label, value in rows])

    mode = getattr(target, 'pointing', None)
    rows = [('Target ID', getattr(target, 'id', None) or 'Unavailable'),
            ('Target Type', mode.name.replace('_', ' ') if mode is not None else 'Unavailable')]
    sky = getattr(target, 'sky_coord', None)
    if sky is not None and sky.frame.name != 'altaz':
        if mode == PointingType.SIDEREAL_TRACK or sky.frame.name != 'galactic':
            equatorial = sky if hasattr(sky, 'ra') else sky.icrs
            rows.append(('Target RA / Dec', f"RA {angle(equatorial.ra)}, Dec {angle(equatorial.dec)} ({equatorial.frame.name.upper()})"))
        if mode == PointingType.SIDEREAL_TRACK or sky.frame.name == 'galactic':
            rows.append(('Target l / b', f"l {angle(sky.galactic.l)}, b {angle(sky.galactic.b)}"))
    configured_altaz = getattr(target, 'altaz', None)
    if configured_altaz is None and sky is not None and sky.frame.name == 'altaz':
        configured_altaz = sky
    if configured_altaz is not None:
        if not isinstance(configured_altaz, dict):
            configured_altaz = dict(alt=configured_altaz.alt, az=configured_altaz.az)
        rows.append(('Target Alt / Az', altaz(configured_altaz)))
    scan = getattr(target, 'scan', None)
    if mode == PointingType.OFFSET_SCAN:
        rows.extend([('Target Offset', angle(getattr(scan, 'offset', None))),
                     ('Target Rate', f"{scan.rate:g}°/s" if getattr(scan, 'rate', None) is not None else 'Unavailable'),
                     ('Target Angle', angle(getattr(scan, 'angle', None)))])
    elif mode == PointingType.FIVE_POINT_SCAN:
        rows.extend([('Target Offset', angle(getattr(scan, 'offset', None))),
                     ('Target Direction', getattr(scan, 'direction', None) or 'Unavailable')])
    scans = sorted(scans, key=lambda model: (model.read_start or model.created or datetime.min.replace(tzinfo=timezone.utc), model.scan_iter))
    first, last = (scans[0], scans[-1]) if scans else (None, None)
    sections = [section('Target', rows), section('Target acquisition', [
        ('Target Acquisition Alt / Az', altaz(getattr(first, 'tgt_acq_altaz', None))),
        ('Target Acquisition Date time', timestamp(getattr(first, 'tgt_acq_dt', None)))])]
    for title, model, index in [('Scan start', first, 0), ('Scan end', last, -1)]:
        refs = getattr(model, 'pointing_refs', None) or []
        reference = refs[index] if refs else {}
        rows = [(f'{title} Pointing Alt / Az', altaz(reference.get('pointing_altaz')))]
        if mode in (PointingType.OFFSET_SCAN, PointingType.FIVE_POINT_SCAN):
            rows.extend([(f'{title} Offset', angle(reference.get('pointing_offset'))),
                         (f'{title} Position Angle', angle(reference.get('pointing_position_angle')))])
        rows.append((f'{title} Date time', timestamp(reference.get('datetime'))))
        sections.append(section(title, rows))
    return sections


def save_pointing_chart(frames, location, path):
    from obs.animation import generate_star_chart
    from matplotlib import pyplot as plt
    first = frames[0]
    markers = [(frame.ra_deg, frame.dec_deg, str(index) if len(frames) <= 40 else None)
               for index, frame in enumerate(frames, 1)]
    # The background has one epoch; individual markers use their own reference times.
    chart = generate_star_chart(first.time, float(location.lon.deg), float(location.lat.deg),
                               first.ra_deg, first.dec_deg, filename=str(path.with_suffix('.sky.png')),
                               pointings=markers)
    figure, axis = plt.subplots(figsize=(9, 9))
    try:
        axis.imshow(chart)
        axis.axis('off')
        measured = sum(frame.pointing_source == 'first_pointing_reference' for frame in frames)
        axis.set_title(f"{len(frames)} physical scans · {measured} first pointing references\n"
                       f"Cyan crosses: scan pointings · Sky background: {first.time:%Y-%m-%d %H:%M:%S} UTC")
        figure.savefig(path, bbox_inches='tight', dpi=140)
    finally:
        plt.close(figure)
        path.with_suffix('.sky.png').unlink(missing_ok=True)


def render_pointing_product(request, out, progress):
    """Create selected-aggregate chart and optional observation-wide sky/MPR GIF."""
    from obs.animation import create_animation
    specification = request['pointing_product']
    if specification['animation']:
        if 'interval_ms' not in specification:
            raise ValueError("The web server did not pass the animation frame interval. "
                             "Restart the web server and refresh the browser, then generate the animation again.")
        interval_ms = specification['interval_ms']
        if type(interval_ms) is not int or not 100 <= interval_ms <= 500:
            raise ValueError("Frame interval must be a whole number between 100 and 500 ms")
    source = Path(request['source_results'])
    result = json.loads((source / 'result.json').read_text())
    for path in source.iterdir():
        if path.is_file() and path.name not in ('manifest.json', 'result.json', 'results.zip', 'analysis-settings.json'):
            shutil.copy2(path, out / path.name)
    row = next(item for item in result['aggregates'] if item['id'] == specification['aggregate_id'])
    errors = []
    charts = result['aggregates'] if specification['animation'] else [row]
    metadata = json.loads((source / CACHE_FILES[0]).read_text()) if (source / CACHE_FILES[0]).is_file() else None
    if metadata:
        count = sum(ScanModel.from_dict(item).scan_type.name == 'SKY' for item in metadata['scans'])
        result['animation_default_interval_ms'] = suggested_animation_interval(count)
    for index, chart_row in enumerate(charts):
        if metadata:
            target = next((TargetModel.from_dict(item) for item in metadata['observation']['targets'] if item['tgt_idx'] == chart_row['target_id']), None)
            scans = [ScanModel.from_dict(item) for item in metadata['scans']]
            scans = [model for model in scans if (model.tgt_idx, model.freq_scan, model.dig_id, model.scan_type.name) ==
                     (chart_row['target_id'], chart_row['freq_scan'], chart_row['digitiser'], chart_row['kind'])]
            chart_row['pointing_details'] = pointing_details(target, scans)
        chart_name = f"{chart_row['id']}-pointing.png"
        if (out / chart_name).is_file():
            chart_row['views']['pointing'] = chart_name
            chart_row.get('view_errors', {}).pop('pointing', None)
            continue
        progress(f"Preparing sky view {index+1}/{len(charts)}", .1+.25*index/len(charts))
        try:
            frames, location, chart_errors = load_frames(source, chart_row, include_spectra=False)
            save_pointing_chart(frames, location, out / chart_name)
        except ValueError as exc:
            chart_row.setdefault('view_errors', {})['pointing'] = str(exc)
            errors.append(str(exc))
            continue
        errors.extend(chart_errors)
        chart_row['views']['pointing'] = chart_name
        chart_row.get('view_errors', {}).pop('pointing', None)
        measured = sum(frame.pointing_source == 'first_pointing_reference' for frame in frames)
        chart_row['pointing_summary'] = dict(scans=len(frames), first_references=measured,
                                           target_midpoints=len(frames)-measured, omitted=len(chart_errors))
    if specification['animation']:
        result['pointing_charts_complete'] = True
        name = 'pointing-animation.gif'
        if not (out / name).is_file():
            frames, location, animation_errors = load_frames(source, sky_only=True)
            errors.extend(animation_errors)
            progress('Rendering sky and pipeline MPR animation', .4)
            create_animation(frames, location, out / name, interval_ms=interval_ms,
                             progress_callback=lambda index, count: progress(
                                 f"Saving animation GIF — {index}/{count} scan frames rendered"
                                 if index == count else f"Rendering animation — {index}/{count} scan frames rendered",
                                 .4+.5*index/count))
            result['animation_frames'] = len(frames)
            result['animation_default_interval_ms'] = suggested_animation_interval(len(frames))
        elif interval_ms != result.get('animation_interval_ms', 100):
            progress('Updating animation frame interval', .5)
            result['animation_frames'] = set_animation_interval(out / name, interval_ms)
        result['animation_interval_ms'] = interval_ms
        result['views']['animation'] = name
    for error in dict.fromkeys(errors):
        warning = f"Pointing omitted: {error}"
        if warning not in result['warnings']:
            result['warnings'].append(warning)
    return result
