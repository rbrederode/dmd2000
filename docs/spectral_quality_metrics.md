# Shared spectrum noise and SNR calculations

Pipeline QA, LAB profiler diagnostics and individual-aggregate frontend plots
share the functions in `src/sdp/spectral_metrics.py`. The default estimator is
**median/MAD**, and their automatic channel selection uses the same
`select_qa_channels()` function. These functions never mutate powers or flags.

## Shared default policy

- Locate the supported spectral peak with a mask-aware moving average. Default
  `smooth_window=10` is adjusted to the same bounded odd width as pipeline QA.
  Smoothing selects the channel; the peak amplitude uses that channel's original
  power, rather than the smoothed value or an independently selected raw maximum.
- Exclude a window around that peak using `window_frac=0.2` of the original
  channel grid. Masked/flagged gaps and bandpass edges are retained in that grid.
  Noise samples are all remaining usable finite channels, not only one band edge.
- The noise baseline is the median of those samples. **Robust Noise** is
  `1.4826 * median(abs(samples - median))`, a Gaussian-equivalent scatter estimate.
  It is not a literal RMS measurement.
- Peak SNR is `(selected_peak - baseline) / robust_noise`; dB is
  `10 * log10(ratio)`, retaining the existing power-like spectrum convention.

`estimate_noise(samples)` now defaults to `median_mad`. The arithmetic mean and
population RMS implementation remains available with `estimator="mean_rms"`.
`peak_snr()` implements the ratio and logarithm for both estimators.

Pipeline QA itself retains its existing behaviour, including FWHM and other
statistics. Only its channel-selection code was extracted into the shared
selector. The pipeline can still explicitly choose `noise_estimator="mean_rms"`.
This produces a mean baseline and literal population RMS instead of median/MAD.

## Frontend plots and caches

Individual Frequency and Velocity plots label **Robust Noise**, **Median baseline**,
SNR, the two noise regions outside the peak exclusion where available, and the
sample count. A dashed line plots that same median baseline. All SKY aggregates
views omit these diagnostics and the baseline line, retaining normal curve
legends, bandpass shading and RFI markers.

Plots use the enabled MPR QA step's estimator, window fraction and smoothing
settings for that digitiser. These settings are recorded with the native cached
spectra and reused by **Update plots** and LAB comparisons. Older caches recover
settings from the recorded pipeline snapshot where available.

Frequency and Velocity canonicalize channel order to native increasing frequency
before selecting the peak. Reversing the plotted axis therefore preserves noise
samples and SNR when coverage, powers and settings match. Narrower display limits
restrict usable samples; additional baseline correction can legitimately change
the baseline, scatter and SNR. Metrics are measured from the displayed aggregate,
rather than copied from an earlier pipeline stage.
The restricted channel grid also sets smoothing and peak-exclusion widths:
using min/max gives the same diagnostics as supplying only those native channels
to the shared calculator. Masked/flagged gaps within the selected range remain
in that grid. Out-of-range powers do not influence the result.

LAB comparisons use the displayed peak-scaled observation. Noise amplitudes are
labelled **scaled plot units**, not calibrated kelvin. Positive scaling preserves
SNR for identical samples. LAB JSON exports include `observation_noise`, separately
from normalized `shape_rms`. Legacy metadata keys such as `noise_rms_k` are kept
for compatibility; `noise_estimator="median_mad"` identifies their robust scatter.

## Profiler options and compatibility

`estimate_profile_noise()` and `observation_noise_metrics()` remain importable
from `util.lab_profiler` and default to the same QA peak-exclusion and median/MAD
policy. Their Python arguments include `window_frac`, `smooth_window` and
`noise_estimator`. The CLI equivalents are `--noise-window-frac`,
`--noise-smooth-window` and `--noise-estimator`.

Explicit `--noise-range` windows still override automatic noise selection.
The former highest-positive-velocity edge selection remains available explicitly
with `--noise-selection positive_edge`. Combine this with
`--noise-estimator mean_rms` to recover the legacy mean/RMS edge policy.
Terminal-velocity detection consumes the same configured noise estimate; it is
separate from the SNR calculation.

## Degenerate spectra and reproducibility

The shared automatic selector needs at least three usable channels and a nonempty
noise region, matching QA. Explicit profiler noise windows and the legacy edge
policy retain their ten-noise-sample requirement. QA retains its explicit
`1e-12` startup floor for nonpositive signal or scatter. Plot/profiler SNR is
instead labelled unavailable for zero scatter or no positive peak; the measured
median and scatter remain reported. No infinite SNR is fabricated for a flat
spectrum.

Regression tests compare actual QA outputs, profiler diagnostics, Frequency and
Velocity annotations using the same samples and settings. They also cover masked
edges/RFI, a raw spike distinct from the smoothed peak, custom QA settings,
coordinate reversal and initial-versus-cached plot agreement. Explicit mean/RMS
and legacy edge policies have separate numerical regression coverage.
