import os

from models.target import TargetModel

import logging
logger = logging.getLogger(__name__)

def fmt_bool(value: str | bool) -> bool:
    """Parse common boolean spellings into a bool."""
    if isinstance(value, bool):
        return value

    value_normalized = str(value).strip().lower()
    if value_normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if value_normalized in {"false", "0", "no", "n", "off"}:
        return False

    raise ValueError(
        f"Expected a boolean value, got '{value}'. "
        "Use true/false, yes/no, on/off, or 1/0."
    )

def fmt_duration(seconds: float) -> str:
    """ Format a duration in seconds into a string of the form "HH:MM:SS". """
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"

def fmt_cell(value, width) -> str:
    """Format a single table cell to a fixed width. """
    return f"{str(value):<{width}}"

def fmt_float(value, scale=1.0, precision=2, suffix="") -> str:
    """Format a numeric value with optional scaling and suffix."""
    if value is None:
        return ""
    return f"{float(value) / scale:.{precision}f}{suffix}"

def fmt_number(value: float | None, precision: int) -> str:
    """Format a numeric value, preserving missing values as None."""
    if value is None:
        return "None"
    return f"{value:.{precision}f}"

def fmt_percent(value: float | None, precision: int = 0) -> str:
    """Format a percentage value, preserving missing values as None."""
    if value is None:
        return "None"
    return f"{value:.{precision}f}%"

def fmt_angle(value, precision=2) -> str:
    """Format an angle value for display, preserving missing values."""
    return "None" if value is None else f"{float(value):.{precision}f}"

def fmt_pointing_value(value) -> str:
    """Format a coordinate (e.g. alt/az) for stable, machine-readable pointing rows."""
    if value is None:
        return "None"
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return str(value)

def fmt_image_link(dir: str, filename: str, width: int = 8) -> str:
    """Build the image hyperlink cell for a scan row when metadata is available."""
    if not dir or not filename:
        return fmt_cell("", width)

    image_path = os.path.abspath(os.path.join(dir, filename))
    label = fmt_cell("open", width)
    return fmt_hyperlink(image_path, label)

def fmt_hyperlink(path: str, label: str) -> str:
    """Create an OSC 8 terminal hyperlink string. """
    return f"\033]8;;file://{path}\033\\{label}\033]8;;\033\\"

def fmt_target_coords(target: TargetModel) -> str:
    """Format target coordinates for table output. """
    if target is not None and target.sky_coord is not None:
        return f"RA {target.sky_coord.ra.to_string(unit='hour', precision=0)} Dec {target.sky_coord.dec.to_string(unit='deg', precision=2)}"
    if target is not None and target.altaz is not None:
        if isinstance(target.altaz, dict):
            alt = target.altaz.get("alt", "")
            az = target.altaz.get("az", "")
            return f"Alt {alt} Az {az}".strip()
        return f"Alt {target.altaz.alt.to_string(unit='deg', precision=0)} Az {target.altaz.az.to_string(unit='deg', precision=2)}"
    return ""

def fmt_title(title: str):
    """ Format a simple section title banner.
        Parameters:
            title:  Title text to print. 
    """
    return f"\n{'='*len(title)}\n{title}\n{'='*len(title)}\n"
