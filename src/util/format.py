import os

from models.target import TargetModel

import logging
logger = logging.getLogger(__name__)

def fmt_cell(value, width) -> str:
    """Format a single table cell to a fixed width. """
    return f"{str(value):<{width}}"

def fmt_float(value, scale=1.0, precision=2, suffix="") -> str:
    """Format a numeric value with optional scaling and suffix."""
    if value is None:
        return ""
    return f"{float(value) / scale:.{precision}f}{suffix}"

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
