"""Shared presentation helpers for measured and predicted longitude–velocity maps."""


def reference_lv_colormap():
    """White-to-blue palette shared by OPT and the LAB predicted L–V view."""
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list(
        "lab_white_blue", ["white", "#e3f5fc", "#8ad5ef", "#239dce", "#0067a5", "#00335c", "#00121f"])


def interpolate_lv_contour_column(velocity, power, grid, smoothing_fwhm=0.):
    """Interpolate for contour drawing only, preserving missing-data gaps.

    The colour image keeps its native channels. Do not extrapolate, interpolate
    across masked channels, or bridge velocity jumps above 1.5 channel spacings.
    Optional Gaussian smoothing FWHM is in velocity units and operates within
    each valid run, without changing the supplied power array.
    """
    import numpy as np
    if not np.isfinite(smoothing_fwhm) or smoothing_fwhm < 0:
        raise ValueError("Contour smoothing FWHM must be finite and non-negative")
    velocity = np.asarray(velocity)
    power = np.ma.masked_invalid(power)
    values = power.filled(np.nan)
    valid = np.flatnonzero(np.isfinite(velocity) & ~np.ma.getmaskarray(power))
    result = np.full(len(grid), np.nan)
    if not len(valid):
        return result
    spacings = np.diff(velocity)
    spacings = spacings[np.isfinite(spacings) & (spacings > 0)]
    max_gap = 1.5 * np.median(spacings) if len(spacings) else 0
    breaks = (np.diff(valid) > 1) | (np.diff(velocity[valid]) > max_gap)
    for run in np.split(valid, np.flatnonzero(breaks) + 1):
        if len(run) == 1:
            result[np.isclose(grid, velocity[run[0]], rtol=0, atol=1e-9)] = values[run[0]]
        else:
            samples = values[run].copy()
            if smoothing_fwhm > 0:
                sigma = smoothing_fwhm / np.sqrt(8 * np.log(2)) / np.median(np.diff(velocity[run]))
                radius = min(int(np.ceil(3 * sigma)), 3 * len(run))
                offsets = np.arange(-radius, radius + 1)
                weights = np.exp(-.5 * (offsets / sigma)**2)
                weights /= weights.sum()
                samples = np.convolve(np.pad(samples, radius, mode="edge"), weights, mode="valid")
            inside = (grid >= velocity[run[0]]) & (grid <= velocity[run[-1]])
            result[inside] = np.interp(grid[inside], velocity[run], samples)
    return result
