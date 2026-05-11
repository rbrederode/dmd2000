import os
import subprocess
from pathlib import Path

def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _read_version_base() -> str:
    version_file = _repo_root() / "VERSION"
    try:
        value = version_file.read_text(encoding="ascii").strip()
        return value if value else ""
    except OSError:
        return ""


def _read_build_number():
    build_number = os.getenv("BUILD_NUMBER")
    if build_number is not None:
        try:
            return max(0, int(build_number))
        except ValueError:
            pass

    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=_repo_root(),
            check=True,
            capture_output=True,
            text=True,
        )
        return max(0, int(result.stdout.strip()))
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def get_version_info() -> str:
    version_base = _read_version_base()
    if not version_base:
        return ""

    build_number = _read_build_number()
    if build_number is None:
        return version_base

    return f"{version_base}-{build_number}"
