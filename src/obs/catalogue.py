"""Discover observation metadata on storage accessible to the calling application."""

import json
import logging
import os
from pathlib import Path, PurePosixPath
import re

from env.events import ResponseEvent

logger = logging.getLogger(__name__)


def list_observations(store, service="WEB"):
    """Recursively return metadata and relative folders, skipping symbolic links."""
    try:
        root = Path(store).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise NotADirectoryError

        def walk_error(error):
            # Report an incomplete search rather than silently omit a subtree.
            raise error

        paths = []
        for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            parent = Path(folder)
            dirs[:] = [name for name in dirs if not (parent / name).is_symlink()]
            paths.extend(parent / name for name in files
                         if name.endswith('-obs.json')
                         and not (parent / name).is_symlink()
                         and (parent / name).is_file())
        paths.sort(key=lambda path: path.relative_to(root).as_posix())
    except (OSError, RuntimeError, ValueError):
        logger.warning(f'Observation search directory is unavailable to {service}')
        return ResponseEvent('error', f'Observation search directory is unavailable to {service}', code='directory_unavailable')

    entries = []
    for path in paths:
        entry = {'id': path.name.removesuffix('-obs.json'), 'filename': path.name,
                 'folder': path.parent.relative_to(root).as_posix()}
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('_type') != 'ObsModel':
                raise ValueError('Expected observation metadata')
            def value(field):
                raw = data.get(field)
                return raw.get('value') if isinstance(raw, dict) else raw
            entry.update(id=data.get('obs_id') or entry['id'],
                         title=data.get('title', ''), dish=data.get('dsh_id'),
                         state=value('obs_state'), started_at=value('start_dt'))
            groups = data.get('target_scans') if isinstance(data.get('target_scans'), list) else []
            scans = [scan for group in groups if isinstance(group, dict)
                     for scan in (group.get('scans') if isinstance(group.get('scans'), list) else []) if isinstance(scan, dict)]
            complete = sum((scan.get('status', {}).get('value') if isinstance(scan.get('status'), dict)
                            else scan.get('status')) in ('COMPLETE', 3) for scan in scans)
            configs = data.get('target_configs') if isinstance(data.get('target_configs'), list) else []
            finished = value('obs_state') in ('COMPLETE', 'COMPLETED') or (bool(scans) and complete == len(scans)) or (
                bool(configs) and isinstance(data.get('tgt_idx'), int) and data['tgt_idx'] >= len(configs))
            entry.update({key: value(key) for key in (
                'scheduling_block_start', 'scheduling_block_end', 'estimated_observation_duration',
                'total_integration_time', 'created', 'user_email', 'end_dt', 'last_update')})
            entry.update(scan_count=len(scans), completed_scans=complete, completed=finished,
                         revision=str(path.stat().st_mtime_ns))
        except (OSError, ValueError, UnicodeError):
            # A file may be incomplete while TM is updating it. Keep the
            # rest of the catalogue available and let the next request retry.
            entry['error'] = 'Observation metadata is unavailable or invalid'
            logger.warning('Could not read observation metadata: %s', path.name)
        entries.append(entry)
    return ResponseEvent(
        'success', f'Found {len(entries)} observations',
        payload={'observations': entries, 'count': len(entries)},
    )


def read_observation(store, observation_id, folder='.'):
    """Read a selected saved observation beneath the catalogue root."""
    if not isinstance(observation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:+-]{0,254}', observation_id):
        return ResponseEvent('error', 'Invalid observation filename', code='invalid_request')
    relative = PurePosixPath(folder)
    if relative.is_absolute() or '..' in relative.parts or '\\' in folder:
        return ResponseEvent('error', 'Invalid observation folder', code='invalid_request')
    try:
        root = Path(store).expanduser().resolve(strict=True)
        path = root
        for part in (*relative.parts, observation_id + '-obs.json'):
            path = path / part
            if path.is_symlink():
                return ResponseEvent('error', 'Linked observation paths are not supported', code='invalid_request')
        if not path.is_file():
            return ResponseEvent('error', 'Saved observation was not found', code='not_found')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('_type') != 'ObsModel':
            raise ValueError('Expected observation metadata')
    except FileNotFoundError:
        return ResponseEvent('error', 'Observation directory is unavailable', code='directory_unavailable')
    except (OSError, ValueError, UnicodeError):
        return ResponseEvent('error', 'Saved observation metadata is unavailable or invalid', code='invalid_metadata')
    return ResponseEvent('success', 'Saved observation retrieved', payload={'observation': data})


def scan_spectrum_file(store, observation_id, target, frequency, iteration, folder=None, *, product="spr"):
    """Locate a completed scan's SPR CSV or final signal PNG using saved metadata."""
    if product not in ("spr", "signal"):
        return ResponseEvent('error', 'Unknown scan product', code='invalid_request')
    label = 'spectrum CSV' if product == 'spr' else 'signal PNG'

    if folder is None:
        catalogue = list_observations(store)
        if catalogue.status != 'success':
            return catalogue
        matches = [entry for entry in catalogue.payload['observations'] if entry['id'] == observation_id]
        if not matches:
            return ResponseEvent('error', 'Saved observation was not found', code='not_found')
        entry = max(matches, key=lambda entry: int(entry.get('revision', '0')))
        folder, observation_id = entry['folder'], entry['filename'].removesuffix('-obs.json')
    result = read_observation(store, observation_id, folder)
    if result.status != 'success':
        return result
    observation = result.payload['observation']
    groups = observation.get('target_scans') or []
    scan = next((scan for group in groups if isinstance(group, dict)
                 for scan in (group.get('scans') or []) if isinstance(scan, dict)
                 and (scan.get('tgt_idx', group.get('tgt_idx')), scan.get('freq_scan'), scan.get('scan_iter'))
                 == (target, frequency, iteration)), None)
    if scan is None:
        return ResponseEvent('error', 'Saved scan was not found', code='not_found')
    status = scan.get('status')
    status = status.get('value') if isinstance(status, dict) else status
    if status not in ('COMPLETE', 3):
        return ResponseEvent('error', 'Only completed scans can be downloaded', code='scan_incomplete')
    prefix = scan.get('files_prefix')
    if not isinstance(prefix, str) or not prefix:
        return ResponseEvent('error', 'The scan has no saved spectrum filename', code='not_found')
    if Path(prefix).name != prefix or prefix in ('.', '..') or '\\' in prefix:
        return ResponseEvent('error', 'Invalid saved spectrum filename', code='invalid_request')
    try:
        root = Path(store).expanduser().resolve(strict=True)
        filename = prefix + ('-spr.csv' if product == 'spr' else '-sigfig.png')
        # Prefer the observation's current folder, so moved archives continue
        # to work even when metadata contains an old absolute recording path.
        directories = [root / folder]
        recorded = scan.get('files_directory')
        if isinstance(recorded, str) and recorded:
            recorded = Path(recorded).expanduser()
            directories.append(recorded if recorded.is_absolute() else root / recorded)
        for directory in directories:
            path = directory / filename
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if '..' in relative.parts:
                continue
            current = root
            linked = False
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    linked = True
                    break
            if not linked and path.is_file():
                return ResponseEvent('success', 'Scan spectrum ready', payload={'path': str(path), 'filename': filename})
    except (OSError, ValueError):
        return ResponseEvent('error', 'The scan spectrum is unavailable', code='file_unavailable')
    return ResponseEvent('error', f'The scan {label} was not found in this archive', code='not_found')
