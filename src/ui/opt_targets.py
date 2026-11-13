"""Shared target definitions for OPT input scans and processed aggregates."""
import astropy.units as u

from models.target import PointingType


def target_display_metadata(target):
    """Describe the configured target centre, without inferring measured pointing."""
    pointing = getattr(target, "pointing", None)
    mode = pointing.name if isinstance(pointing, PointingType) else None
    result = dict(pointing=mode, target_coordinates=None, longitude=None, latitude=None)
    if target is None:
        return result
    body = getattr(target, "id", None)
    if pointing == PointingType.NON_SIDEREAL_TRACK and body:
        result["target_coordinates"] = dict(kind="body", id=body)
        return result
    sky = getattr(target, "sky_coord", None)
    altaz = getattr(target, "altaz", None)
    if altaz is None and sky is not None and sky.frame.name == "altaz":
        altaz = sky
    if altaz is not None:
        alt = altaz.get("alt") if isinstance(altaz, dict) else altaz.alt
        az = altaz.get("az") if isinstance(altaz, dict) else altaz.az
        if alt is not None and az is not None:
            result["target_coordinates"] = dict(kind="altaz", alt_deg=float(u.Quantity(alt, u.deg).to_value(u.deg)),
                                                az_deg=float(u.Quantity(az, u.deg).to_value(u.deg)))
            return result
    if sky is not None:
        galactic = sky.galactic
        result.update(longitude=float(galactic.l.deg), latitude=float(galactic.b.deg))
        coordinates = dict(kind="galactic", longitude=result["longitude"], latitude=result["latitude"])
        if sky.frame.name != "galactic":
            equatorial = sky if hasattr(sky, "ra") and hasattr(sky, "dec") else sky.icrs
            coordinates.update(kind="equatorial", frame=equatorial.frame.name,
                               ra_deg=float(equatorial.ra.deg), dec_deg=float(equatorial.dec.deg))
        result["target_coordinates"] = coordinates
    elif body:
        result["target_coordinates"] = dict(kind="body", id=body)
    return result
