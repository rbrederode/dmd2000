"""Coordinate-independent baseline fitting shared by pipeline and analysis tools."""

def subtract_baseline(x, power, method="linear", fit_mask=None, *, fit_line_free=False, usable_mask=None):
    """Return (power - baseline, baseline) without scaling or mutating inputs.

    Linear/constant modes use the lowest 20% of finite, unmasked powers in
    ``fit_mask`` (all valid channels when omitted). Linear fits a straight
    line; constant uses their median. At least ten fit channels and three
    lower-envelope samples are required. The fit is evaluated on the full
    coordinate grid. Negative residuals and noise-only spectra are retained.
    With ``fit_line_free=True``, the mask must identify line-free channels:
    fit all selected channels, with symmetric 3-sigma clipping (up to five
    iterations), instead of selecting the lowest 20% of powers. Scatter uses
    IQR/1.349, falling back to standard deviation when the IQR is degenerate.
    ``none`` subtracts zero. Linear subtraction is the default.

    ``flatten`` fits the same line as ``linear`` but subtracts only its rise
    above its minimum over usable channels, retaining that fitted level.
    ``usable_mask`` excludes flagged channels from fitting and from this
    minimum; ``fit_mask`` selects fitting windows within the usable channels.
    The returned baseline is the correction actually subtracted. No clipping
    is applied to corrected powers.

    Inputs must be matching 1D arrays. Invalid values and existing masks are
    excluded from fitting and preserved in the returned masked arrays. Plain
    arrays return plain arrays, with invalid samples represented as NaN.
    """
    import numpy as np

    masked_input = np.ma.isMaskedArray(x) or np.ma.isMaskedArray(power)
    v = np.ma.asarray(x, dtype=float)
    p = np.ma.asarray(power, dtype=float)
    if v.ndim != 1 or p.shape != v.shape:
        raise ValueError("Coordinates and power must be matching one-dimensional arrays")
    x, y = v.filled(np.nan), p.filled(np.nan)
    invalid = np.ma.getmaskarray(v) | np.ma.getmaskarray(p) | ~np.isfinite(x) | ~np.isfinite(y)
    valid = ~invalid
    if usable_mask is not None:
        usable = np.asarray(usable_mask, dtype=bool)
        if usable.shape != x.shape:
            raise ValueError("Usable channel mask must match the spectrum shape")
        valid &= usable
    anchor_channels = valid.copy()
    if fit_mask is not None:
        selection = np.asarray(fit_mask, dtype=bool)
        if selection.shape != x.shape:
            raise ValueError("Baseline fit mask must match the spectrum shape")
        valid &= selection
    if method == "none":
        baseline = np.zeros_like(y)
    elif method in ("linear", "constant", "flatten"):
        if valid.sum() < 10:
            raise ValueError("Baseline fitting requires at least ten valid fitting channels")
        if fit_line_free:
            points = valid.copy()
            for iteration in range(6):
                if method in ("linear", "flatten"):
                    if np.unique(x[points]).size < 2:
                        raise ValueError("Linear baseline requires distinct fitting coordinates")
                    baseline = np.polyval(np.polyfit(x[points], y[points], 1), x)
                else:
                    baseline = np.full_like(y, np.mean(y[points]))
                residual = y - baseline
                centre = np.median(residual[points])
                q25, q75 = np.quantile(residual[points], [.25, .75])
                scatter = (q75 - q25) / 1.349
                tolerance = 1e-12 * max(1., np.max(np.abs(y[points])))
                if scatter <= tolerance:
                    scatter = np.std(residual[points])
                if iteration == 5 or scatter <= tolerance:
                    break
                clipped = points & (np.abs(residual - centre) <= 3 * scatter)
                if np.array_equal(clipped, points):
                    break
                if clipped.sum() < 10:
                    raise ValueError("Line-free baseline clipping leaves fewer than ten channels")
                points = clipped
        else:
            threshold = np.quantile(y[valid], .2)
            points = valid & (y <= threshold)
            if points.sum() < 3:
                raise ValueError("Baseline fitting requires at least three lower-envelope samples")
            if method in ("linear", "flatten"):
                if np.unique(x[points]).size < 2:
                    raise ValueError("Linear baseline requires distinct fitting coordinates")
                baseline = np.polyval(np.polyfit(x[points], y[points], 1), x)
            else:
                baseline = np.full_like(y, np.median(y[points]))
    else:
        raise ValueError(f"Unknown observation baseline: {method}")
    if method == "flatten":
        baseline = baseline - np.min(baseline[anchor_channels])
    baseline[invalid] = np.nan
    corrected = y - baseline
    if masked_input:
        return np.ma.array(corrected, mask=invalid), np.ma.array(baseline, mask=invalid)
    return corrected, baseline
