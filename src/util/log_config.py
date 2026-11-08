"""Resolve log storage before logging is initialized, without importing app models."""
import argparse
import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_log_dir(value=None, repo_root=None):
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    if value is None:
        value = 'logs'
    if not isinstance(value, str) or not value.strip():
        raise ValueError('log_dir must be a non-empty directory path or null')
    directory = Path(value).expanduser()
    return (directory if directory.is_absolute() else root / directory).resolve()


def load_log_dir(profile='default', profile_dir=None, repo_root=None):
    """Read a profile's path; relative paths always refer to the repository root."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    directory = Path(profile_dir).expanduser() if profile_dir is not None else root / 'src' / 'config' / profile
    path = directory / 'LaunchConfig.json'
    value = json.loads(path.read_text()).get('log_dir') if path.exists() else None
    return str(resolve_log_dir(value, root))


def runtime_log_dir(argv=None, environ=None):
    """CLI > process environment > profile > repository default, before imports log."""
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--profile_dir')
    parser.add_argument('--log_dir')
    args, _ = parser.parse_known_args(argv)
    override = args.log_dir if args.log_dir is not None else env.get('DMD_LOG_DIR')
    if override is not None:
        return str(resolve_log_dir(override))
    return load_log_dir(args.profile, args.profile_dir)
