"""Plot annotations using the same profile-noise diagnostic as the LAB profiler."""
import numpy as np

from sdp.channel_mask import masked_values
from sdp.spectral_metrics import observation_noise_metrics


METRICS_GID = "spectrum-noise-metrics"


def annotate_spectral_metrics(axis, coordinates, power, low, high, *, label,
                              unit="km/s", flags=None, velocity=None,
                              amplitude_unit="a.u.", qa_settings=None):
    """Measure displayed samples with pipeline QA's median/MAD policy.

    Frequency uses its matching LSRK coordinates where available. Without that
    metadata, reversed frequency gives the same native channel ordering.
    Report the actual noise windows outside the smoothed-peak exclusion region.
    """
    x = np.asarray(coordinates, dtype=float)
    values = np.ma.masked_invalid(power)
    if flags is not None:
        values = np.ma.array(values, mask=np.ma.getmaskarray(values) |
                             np.ma.getmaskarray(masked_values(values.filled(np.nan), flags)))
    visible = np.isfinite(x) & (x >= low) & (x <= high)
    values = np.ma.array(values, mask=np.ma.getmaskarray(values) | ~visible)
    noise_x = np.asarray(velocity, dtype=float) if velocity is not None else (
        (low + high)/2 - x if unit == "MHz" else x)
    selected = noise_x[visible & np.isfinite(noise_x)]
    policy = dict(window_frac=.2, smooth_window=10, noise_estimator="median_mad")
    policy.update(qa_settings or {})
    if len(selected) < 2:
        metrics = dict(status="insufficient_noise_channels", noise_ranges_kms=[],
                       noise_sample_count=0, noise_rms_k=None, noise_level_k=None,
                       peak_snr=None, peak_snr_db=None, noise_estimator=policy["noise_estimator"])
    else:
        metrics = observation_noise_metrics(noise_x, values, float(selected.min()), float(selected.max()),
                                            **policy)
    order = np.argsort(noise_x)
    windows = []
    for left, right in metrics["noise_ranges_kms"]:
        a, b = np.interp([left, right], noise_x[order], x[order])
        windows.append([float(min(a, b)), float(max(a, b))])
    metrics.update(noise_ranges=windows, coordinate_unit=unit, amplitude_unit=amplitude_unit)
    robust = metrics["noise_estimator"] == "median_mad"
    if metrics["status"] == "measured":
        ratio = f"{metrics['peak_snr']:.2f}" if metrics["peak_snr"] is not None else "out of range"
        summary = f"Peak SNR: {ratio} ({metrics['peak_snr_db']:.2f} dB, 10 log)"
    else:
        status = "zero robust noise" if robust and metrics["status"] == "zero_noise_rms" else metrics["status"].replace('_', ' ')
        summary = f"Peak SNR: unavailable\n{status}"
    if metrics["noise_rms_k"] is not None:
        summary += f"\n{'Robust Noise' if robust else 'Noise RMS'}: {metrics['noise_rms_k']:.4g} {amplitude_unit}"
    if metrics["noise_level_k"] is not None:
        summary += f"\n{'Median baseline' if robust else 'Mean floor'}: {metrics['noise_level_k']:.4g} {amplitude_unit}"
        axis.axhline(metrics["noise_level_k"], color="tab:blue", linestyle="--", linewidth=1,
                     label="Median noise baseline" if robust else "Mean noise baseline")
    window_text = "; ".join(f"{a:.4f} to {b:.4f}" if unit == "MHz" else f"{a:.1f} to {b:.1f}"
                            for a, b in windows)
    if window_text:
        summary += f"\nNoise window: {window_text} {unit}"
    summary += f"\nNoise samples: {metrics['noise_sample_count']} ({'median/MAD' if robust else 'mean/RMS'})"
    block = f"{label}\n{summary}"
    text = next((text for text in axis.texts if text.get_gid() == METRICS_GID), None)
    if text is None:
        text = axis.text(1.015, .65, block, transform=axis.transAxes, va="top", ha="left", fontsize=8,
                         bbox=dict(boxstyle="round,pad=.4", facecolor="white", edgecolor=".7", alpha=.95),
                         gid=METRICS_GID)
        text.set_in_layout(False)
    else:
        text.set_text(text.get_text() + "\n\n" + block)
    return metrics


def place_spectral_metrics(figure, axis, legend):
    """Place diagnostics below the external legend and include them in exports."""
    texts = [text for text in axis.texts if text.get_gid() == METRICS_GID]
    if texts and legend is not None:
        figure.canvas.draw()
        bottom = axis.transAxes.inverted().transform(
            (legend.get_window_extent().x0, legend.get_window_extent().y0))[1]
        for text in texts:
            text.set_position((1.015, bottom - .04))
    return texts
