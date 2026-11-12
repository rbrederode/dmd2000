"""User-facing explanations of implemented pipeline steps and parameters."""

CONTEXT_HELP = (
    "Select when the step runs: spr is the summed power spectrum before calibration; "
    "cal is the calibrated spectrum; mpr is the mean of calibrated samples. "
    "Steps run in their configured order within each context."
)
ENABLED_HELP = (
    "Checked steps run during processing. Unchecking a step bypasses it while retaining its settings. "
    "Changes take effect when you process or reprocess the observation."
)

STEP_HELP = {
    "DC_SPIKE": {
        "overview": "Removes a narrow spike at the central FFT channel caused by a DC offset in the receiver. "
                    "It examines the three central channels and replaces high outliers with the mean of the remaining finite neighbours. "
                    "This correction changes those spectral values; it is not an RFI flagging operation.",
        "parameters": {},
        "notes": ["Normally used in spr. There are no adjustable spike-width or threshold parameters in this implementation."],
    },
    "LOAD": {
        "overview": "Divides the input spectrum by an equivalent completed LOAD reference spectrum to reduce the receiver's bandpass response. "
                    "LOAD scans themselves pass through unchanged so they remain useful calibration references.",
        "parameters": {},
        "notes": ["Normally used in cal, before channel flagging and baseline subtraction. The matching LOAD reference is selected automatically; there is no reference-file field here.",
                  "If no matching LOAD reference is available, the spectrum passes through unchanged. Zero or non-finite reference channels are flagged as invalid calibration.",
                  "This normalization alone does not calibrate the spectrum in kelvin."],
    },
    "BANDPASS_FILTER": {
        "overview": "Marks channels outside your selected passband ranges as excluded. This removes unreliable receiver band edges from later calculations while preserving the measured values in the spectrum.",
        "parameters": {
            "ranges_pct": "Each Min/Max pair defines an allowed interval as a percentage of the full channel array. "
                          "For example, 10 to 90 retains the middle 80% of channels. Multiple ranges are combined; channels outside all ranges are flagged. "
                          "Use 0 ≤ Min < Max ≤ 100. Add range creates another allowed interval; × removes one.",
        },
        "notes": ["Normally used in cal before RFI flagging, baseline fitting and QA. These percentages refer to the full channel array, unlike baseline windows, which refer to usable channels."],
    },
    "RFI_FLAG": {
        "overview": "Finds narrow spectral outliers that may be radio-frequency interference. Each usable channel is compared with the median of its local window. "
                    "Outliers are flagged for exclusion from subsequent calculations; their measured values are not replaced.",
        "parameters": {
            "threshold": "A positive multiplier of the local median absolute deviation (MAD). A channel is flagged when its absolute difference from the local median exceeds this multiplier times MAD. "
                         "The default is 5. Lower values flag more channels; higher values are less aggressive. This MAD is not rescaled into a Gaussian standard deviation.",
            "window_size": "The number of channels in the sliding comparison window, including the channel being tested. "
                           "Use a positive odd integer; the default is 21. Smaller windows follow local structure more closely; larger windows use a broader neighbourhood.",
        },
        "notes": ["Normally used in cal after bandpass selection. Strong or narrow astronomical features can also be outliers, so inspect the flags when adjusting these settings."],
    },
    "BASELINE": {
        "overview": "Fits a background to calibrated spectra and either subtracts it or flattens its slope while retaining a level, before the samples contribute to MPR. "
                    "LOAD references are preserved. The same numerical fitter is used by OPT's interactive views and the LAB profiler.",
        "parameters": {
            "context": "This step runs in cal only. MPR is then formed from the corrected calibrated samples, keeping the current and mean curves on the corrected scale.",
            "method": "Linear removes a fitted straight line; constant removes a fitted level; flatten (default) subtracts only the fitted line's rise "
                      "above its lowest value within the usable passband. Flatten retains that fitted background level and emission above it, "
                      "rather than using the lowest measured power. Flagged channels are excluded when choosing the retained level. "
                      "Flatten does not clip negative values or preserve the original integrated power. None leaves the spectrum unchanged. "
                      "The fit uses the selected windows with symmetric outlier clipping. At least ten fitting channels are required.",
            "ranges_pct": "Each Min/Max pair selects candidate line-free windows by percentile of usable channels after flags. "
                          "The default pairs 0–10 and 90–100 use the lowest and highest 10% of usable channels. "
                          "Use 0 ≤ Min < Max ≤ 100. Add range creates a further fitting window; × removes one. These are channel percentiles, not MHz or km/s limits.",
        },
        "notes": ["Place after calibration and channel flagging, before QA. The windows must avoid astronomical emission; outer channels are only a heuristic for finding a background.",
                  "Linear and constant subtraction remove continuum signal. Flatten retains a flat continuum increase, which can help with solar observations, "
                  "but can remove genuine spectral slopes. Disable this step when the original calibrated total power must be preserved.",
                  "Interactive plot adjustments start from the processed aggregate. Selecting no interactive subtraction restores that aggregate, including any baseline correction already applied by the pipeline."],
    },
    "QA": {
        "overview": "Measures baseline level, peak signal-to-noise ratio, signal power, linewidth and dynamic range, and stores the quality statistics on the scan. "
                    "QA does not subtract a baseline, smooth the returned spectrum, or change channel flags.",
        "parameters": {
            "window_frac": "The fraction of the total number of channels used to define an exclusion window around the detected peak. "
                           "Noise is estimated from usable channels outside that window. The default 0.2 means approximately 20% of the spectrum is excluded. "
                           "Use a number strictly between 0 and 1; too small a window can leave emission in the noise sample.",
            "smooth_window": "The channel count of a moving average used to locate the peak and measure its half-maximum width. "
                             "It does not smooth the output spectrum. Use a positive integer; 1 disables this smoothing. "
                             "The default 10 is adjusted to the next odd size (11), subject to the available spectrum length.",
            "noise_estimator": "median_mad (default) uses the median noise level and 1.4826 × MAD as a robust scatter estimate. "
                               "mean_rms uses the mean noise level and RMS scatter about that mean. "
                               "Peak SNR uses peak above that level divided by the estimated scatter; its dB display uses 10 log10 of the ratio.",
        },
        "notes": ["Normally used in mpr to assess the mean calibrated spectrum, after corrections. "
                  "QA's noise selection excludes the peak window; it is independent of the baseline step's fitting windows. "
                  "Comparisons require the same data, region selection and estimator."],
    },
}


def step_help(step):
    details = STEP_HELP[step]
    return {**details, "parameters": {"enabled": ENABLED_HELP, "context": CONTEXT_HELP, **details["parameters"]}}


PIPELINE_OVERVIEW = {
    "title": "How the pipeline works",
    "overview": "A context identifies the stage at which a pipeline step operates on a spectrum. The normal flow is spr → cal → mpr. "
                "At each stage, the processor executes only enabled steps assigned to that context, in their configured order. "
                "The first step receives that stage's input spectrum; its output spectrum and channel-quality flags become the input to the next step, "
                "and so on until the last matching step returns the stage's result. Disabled steps and steps assigned to other contexts are skipped. "
                "For example, in cal, LOAD normalization can feed bandpass selection, then RFI flagging, then baseline subtraction.",
    "stages": [
        {
            "title": "1. spr — Summed Power Spectrum",
            "description": "The spr context processes summed power per channel per second. During acquisition, FFT power samples "
                           "are summed within each frequency channel over one second, producing one summed-power spectrum for that second. "
                           "Enabled spr steps run on each of these spectra in order, for example to remove a DC spike. "
                           "The resulting per-channel, per-second SPR data is recorded in spectrum files in the scan store for processing and reprocessing. "
                           "Keep spr processing minimal because these saved spectra are the starting point for future reprocessing.",
        },
        {
            "title": "2. cal — Calibrated Spectrum",
            "description": "The cal context applies calibration and correction steps to the summed power per channel per second. "
                           "For each second, the processor takes a copy of its SPR spectrum and passes it through the enabled cal steps in order, "
                           "with each step receiving the previous step's result. The output remains one calibrated value per channel per second; "
                           "this stage does not average the seconds together. It runs during acquisition and again when saved spectra are processed, "
                           "preserving the saved SPR data as the starting point for reprocessing. "
                           "A typical sequence is LOAD normalization, bandpass selection, RFI flagging, then optional baseline subtraction. "
                           "Corrections change spectral values; flagging steps mark channels to exclude from subsequent calculations without replacing their measured values.",
        },
        {
            "title": "3. mpr — Mean Power Spectrum",
            "description": "The mpr context operates on reduced data: the mean of the calibrated per-second samples in each channel. "
                           "The processor first averages the usable per-second values across the loaded seconds, separately for each channel, "
                           "reducing the series of calibrated spectra to one mean spectrum with one value per channel. "
                           "For example, ten usable one-second samples in a channel are summed and divided by ten. "
                           "Flagged samples are excluded from that channel's mean and channel-quality flags are carried forward. "
                           "Enabled mpr steps then process this mean spectrum in order, passing their results from step to step. "
                           "QA normally runs here to measure noise, SNR and other quality statistics without changing the spectrum. "
                           "As samples arrive, MPR and its statistics are updated.",
        },
    ],
    "notes": [
        "During observation processing, OPT reads saved SPR files and forms target/frequency aggregates. Saved SPR data already starts after "
        "the original spr stage; this input path does not replay spr steps. It runs cal processing and rebuilds MPR instead. "
        "Changing a spr-only step here therefore does not redo that correction on saved SPR files.",
        "Selecting a context does not run a step immediately. The processor invokes that context when it reaches the corresponding stage. "
        "The table groups spr, cal and mpr to show this flow; the configuration order is retained within each group.",
        "Checking an unconfigured step adds it to this run. Unchecking a step skips it while retaining its settings. "
        "Process or Reprocess observation applies pipeline changes to the aggregates. Use Save Pipeline Configuration to keep changes in the selected Config Profile.",
        "You can always return to the saved SPR starting point by reprocessing the spectrum files. Each processing run reads those files afresh "
        "and rebuilds the calibrated and mean spectra; it does not apply the new pipeline on top of the previous run's corrected results. "
        "To recover the saved SPR spectrum without calibration or other corrections, uncheck the cal and mpr steps that change spectral values, "
        "then choose Process or Reprocess observation. The mean is then formed from the saved SPR samples. "
        "Reprocessing does not overwrite the source spectrum files, so you can change the settings and try again. "
        "This restores the saved SPR, including any spr-stage corrections already recorded during acquisition; undoing those requires the original raw IQ samples.",
        "Interactive plot adjustments start from the processed aggregates and do not rerun this pipeline. "
        "Update plots changes derived views; selecting no interactive baseline subtraction restores the processed aggregate, including any pipeline correction already applied.",
        "Choose a question mark beside an individual step for its purpose, parameters and usage notes. "
        "The question mark beside Pipeline Steps returns to this guide.",
    ],
}
