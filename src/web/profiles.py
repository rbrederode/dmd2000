"""Atomic selection of a profile for WEB's file-backed resource APIs."""

from dataclasses import dataclass
from pathlib import Path
import re
import threading

from models.launch import load_scan_store_dir
from util.log_config import load_log_dir, resolve_log_dir


class ProfileError(Exception):
    def __init__(self, message, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ProfileSelection:
    directory: Path
    scan_store_dir: str | None
    log_dir: str

    def to_dict(self):
        return {'profile': self.directory.name, 'scan_store_dir': self.scan_store_dir, 'log_dir': self.log_dir}


class ProfileManager:
    def __init__(self, directory, scan_store_dir=None, log_dir=None):
        directory = Path(directory).expanduser().resolve()
        self.root = directory.parent
        self._lock = threading.RLock()
        self._override = scan_store_dir
        self._log_override = log_dir
        self._selection = ProfileSelection(directory, scan_store_dir if scan_store_dir is not None
                                           else load_scan_store_dir(profile_dir=directory),
                                           str(resolve_log_dir(log_dir)) if log_dir is not None
                                           else load_log_dir(profile_dir=directory))

    @property
    def current(self):
        with self._lock:
            return self._selection

    def _directory(self, name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', name):
            raise ProfileError('Profile must be a directory name containing letters, digits, underscores or hyphens')
        directory = self.root / name
        if directory.is_symlink() or directory.resolve().parent != self.root:
            raise ProfileError('Profile must be a direct directory within the configuration root')
        if not directory.is_dir() or not any(directory.glob('*.json')):
            raise ProfileError(f'Profile {name} was not found', 'not_found', 404)
        return directory

    def list(self):
        with self._lock:
            profiles = []
            for directory in sorted(self.root.iterdir(), key=lambda item: item.name):
                if not directory.is_dir() or directory.is_symlink() or directory.name.startswith('.'):
                    continue
                # Configuration profiles may lack a LaunchConfig (e.g. default).
                if not any(directory.glob('*.json')):
                    continue
                error = None
                try:
                    store = load_scan_store_dir(profile_dir=directory)
                    log_dir = load_log_dir(profile_dir=directory)
                except Exception:
                    store = None
                    log_dir = None
                    error = 'Could not load LaunchConfig.json'
                entry = {'profile': directory.name,
                         'selected': directory == self._selection.directory,
                         'scan_store_dir': store, 'log_dir': log_dir, 'available': error is None}
                if error:
                    entry['message'] = error
                profiles.append(entry)
            return profiles, self._selection

    def select(self, name):
        with self._lock:
            directory = self._directory(name)
            try:
                store = load_scan_store_dir(profile_dir=directory)
                log_dir = load_log_dir(profile_dir=directory)
            except Exception as error:
                raise ProfileError('Could not load the selected profile LaunchConfig.json',
                                   'invalid_configuration', 400) from error
            # Commit only after reading and validating the complete new selection.
            self._selection = ProfileSelection(directory, store, log_dir)
            self._override = None
            self._log_override = None
            return self._selection

    def reload(self):
        with self._lock:
            directory = self._selection.directory
            store = self._override if self._override is not None else load_scan_store_dir(profile_dir=directory)
            log_dir = (str(resolve_log_dir(self._log_override)) if self._log_override is not None
                       else load_log_dir(profile_dir=directory))
            self._selection = ProfileSelection(directory, store, log_dir)
            return self._selection
