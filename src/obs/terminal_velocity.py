"""Use the LAB profiler's terminal estimators for cached observation spectra."""
import numpy as np

from obs.obs_display import velocity_limits
from sdp.channel_mask import masked_values

TERMINAL_METHODS = {"threshold": "Threshold edge", "connected": "Connected edge", "erfc": "Erfc wing fit"}


def measure_terminal_velocity(record, method, vmin=None, vmax=None):
    """Apply an existing positive-edge estimator with the aggregate's QA policy."""
    from util.lab_profiler import (estimate_terminal_velocity, estimate_connected_terminal_velocity,
                                   fit_terminal_erfc)
    if method not in TERMINAL_METHODS:
        raise ValueError("Unsupported terminal velocity method")
    velocity = np.asarray(record['velocity'], dtype=float)
    power = np.ma.asarray(record['power'], dtype=float).filled(np.nan)
    if record.get('channel_flags') is not None:
        power = np.ma.asarray(masked_values(power, record['channel_flags'])).filled(np.nan)
    low, high = velocity_limits(velocity, vmin, vmax)
    policy = dict(noise_estimator='median_mad', window_frac=.2, smooth_window=10)
    policy.update(record.get('qa_settings') or {})
    if high <= max(0., low):
        measurement = dict(status='no_positive_velocity_range', velocity_lsr_kms=None)
    elif method == 'erfc':
        measurement = fit_terminal_erfc(velocity, power, search_range=[max(0., low), high])
    else:
        estimator = estimate_terminal_velocity if method == 'threshold' else estimate_connected_terminal_velocity
        measurement = estimator(velocity, power, low, high, **policy)
    return dict(measurement, method=method, velocity_range_kms=[low, high], source='observation')


def annotate_terminal_velocity(axis, measurement, *, amplitude_scale=1.):
    """Mark a measured edge, or explain why no terminal velocity was measured."""
    method = TERMINAL_METHODS[measurement['method']]
    status = measurement['status']
    value = measurement.get('velocity_lsr_kms')
    if value is not None and status in ('detected', 'fitted'):
        text = f"Terminal velocity ({method}): {value:+.2f} km/s LSRK"
        if status == 'fitted':
            from util.lab_profiler import erfc_edge
            text += f" ± {measurement['velocity_formal_error_kms']:.2f} (formal)"
            sample = np.linspace(*measurement['sampled_range_kms'], 300)
            axis.plot(sample, amplitude_scale * erfc_edge(sample, measurement['baseline'],
                      measurement['amplitude'], value, measurement['width_kms']),
                      color='tab:green', linewidth=1.5, label='Erfc wing fit')
        axis.axvline(value, color='tab:green', linestyle=':', label=f'Terminal velocity: {value:+.2f} km/s')
    else:
        text = f"Terminal velocity ({method}): {status.replace('_', ' ')}"
        bound = measurement.get('last_above_threshold_velocity_kms')
        if bound is not None and status in ('edge_limited', 'gap_limited'):
            text += f" (last supported channel {bound:+.2f} km/s)"
    axis.text(.02, .98, text, transform=axis.transAxes, va='top', fontsize=9,
              bbox=dict(facecolor='white', edgecolor='0.8', alpha=.9)).set_gid('terminal-velocity')
