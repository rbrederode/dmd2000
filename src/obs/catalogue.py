"""Discover observation metadata on storage accessible to the calling application."""

import json
import logging
import os
from pathlib import Path

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
