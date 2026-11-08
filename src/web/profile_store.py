"""Shared locking and atomic persistence for profile-backed configuration APIs."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
import threading

_WRITE_LOCK = threading.RLock()


class ProfileStoreError(Exception):
    def __init__(self, message, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ProfileStoreError('Numeric values must be finite')
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, list):
        for item in value:
            _finite(item)


class ProfileFileStore:
    def delete_entry(self, identifier, list_field, id_field, if_match=None):
        """Remove one definition while preserving every other file field."""
        with self._locked():
            data, revision = self._read()
            if if_match is not None and not if_match.contains(revision):
                raise ProfileStoreError('Configuration changed; reload before deleting', 'version_conflict', 412)
            items = data[list_field]
            index = next((index for index, item in enumerate(items) if item[id_field] == identifier), None)
            if index is None:
                raise ProfileStoreError(f'Definition {identifier} was not found', 'not_found', 404)
            del items[index]
            data['last_update'] = {'_type': 'datetime', 'value': datetime.now(timezone.utc).isoformat()}
            return identifier, self._write(data)

    @contextmanager
    def _locked(self):
        # A stable sidecar lock coordinates separate WEB processes as well as threads.
        with _WRITE_LOCK, self.path.with_name('.' + self.path.name + '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write(self, data):
        content = (json.dumps(data, indent=4, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix='.' + self.path.stem + '-', delete=False) as stream:
                temporary = Path(stream.name)
                mode = self.path.stat().st_mode & 0o777 if self.path.exists() else 0o600
                os.fchmod(stream.fileno(), mode)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return hashlib.sha256(content).hexdigest()

