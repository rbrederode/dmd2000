import numpy as np
import logging
from typing import Any, List, Dict

from models.pipeline import StepConfig, StepType
from sdp.channel_mask import empty_channel_flags, valid_channels
from sdp.pipeline.pipeline_factory import ProcessingStep, ProcessingPipeline

logger = logging.getLogger(__name__)

class QA(ProcessingStep):

    CALCULATED_FIELDS = (
        "baseline",
        "snr_db",
        "signal_db",
        "noise_db",
        "signal_start",
        "signal_end",
        "fwhm",
        "dynamic_range_db",
        "signal_pwr_db",
    )

    def __init__(self, config: StepConfig = None):
        super().__init__(config)

        self.scan = config.params["scan"] if "scan" in config.params else None

        if self.scan is None:
            raise ValueError("QA step requires 'scan' in config.params. It is currently None.")

        self.scan_qa = self.scan.get_qa()

        logger.debug("QA pipeline step initialised with scan:\n%s", str(self.scan))
    
    def process(self, context: Any, signal: Any) -> Any:
        """
        Calculate Quality Attributes for the signal array using parameters from context and update the scan QA attributes.
            - Baseline (robust): median of noise region
            - Signal (peak above baseline): max of signal region minus baseline
            - Signal Power (sum above baseline): sum of signal region minus baseline
            - Noise (robust RMS via MAD): 1.4826 * median absolute deviation of noise region
            - SNR (linear): signal / noise
            - SNR (dB): 10 * log10(signal / noise)
            - Dynamic range (dB): 10 * log10(peak signal / noise)
            - FWHM (full width at half maximum): width of signal region above half max

        Args:
            context: dict containing static parameters for applying load file
            input_signal: 1D numpy array containing input spectrum (signal)
        Returns:
            1D numpy array containing processed output spectrum (signal)
        """

        if not isinstance(signal, np.ndarray):
            raise ValueError("QA: input signal must be a numpy array.")

        if signal.ndim != 1:
            raise ValueError(f"QA: signal must be one-dimensional, got shape {signal.shape}.")

        if not isinstance(context, dict):
            raise ValueError("QA: context must be a dictionary.")

        channel_flags = context.get("channel_flags")
        if not isinstance(channel_flags, np.ndarray):
            raise ValueError("QA: context must contain a NumPy 'channel_flags' array.")
        if channel_flags.shape != signal.shape:
            raise ValueError(
                "QA: signal and channel_flags must have the same shape; "
                f"got {signal.shape} and {channel_flags.shape}."
            )
        if not np.issubdtype(channel_flags.dtype, np.unsignedinteger):
            raise ValueError(
                f"QA: channel_flags must use an unsigned integer dtype, got {channel_flags.dtype}."
            )

        if self.scan_qa is None:
            self.scan_qa = self.scan.get_qa()
            self.scan_qa = self.scan.init_qa() if self.scan_qa is None else self.scan_qa  # Ensure the scan QA is initialised

        pipeline = context.get("pipeline", "unknown")  # Get the pipeline name from the context 
        window_frac = context.get("window_frac", self.config.params.get("window_frac", 0.2))
        smooth_window = context.get("smooth_window", self.config.params.get("smooth_window", 10))
        if (
            isinstance(window_frac, bool)
            or not isinstance(window_frac, (int, float))
            or not np.isfinite(window_frac)
            or not 0.0 < window_frac < 1.0
        ):
            raise ValueError("window_frac must be a finite number between zero and one.")
        if isinstance(smooth_window, bool) or not isinstance(smooth_window, (int, np.integer)) or smooth_window < 1:
            raise ValueError("smooth_window must be a positive integer.")

        channels = len(signal)
        valid = valid_channels(signal, channel_flags)

        sec = context.get("sec", max(self.scan.get_loaded_seconds(), 1))
        idx = sec - 1
        qa = self.scan_qa.getQA(pipeline=pipeline, idx=idx)
        if qa is None:
            logger.warning("QA could not find a QA record for pipeline '%s' at index %s.", pipeline, idx)
            return signal

        # Clear values from a previous processing pass. The RFI step owns
        # rfi_fraction, so it is deliberately not reset here.
        for field in self.CALCULATED_FIELDS:
            setattr(qa, field, None)

        if channels == 0 or np.count_nonzero(valid) < 3:
            logger.warning("QA requires at least three usable channels; metrics were left unset.")
            return signal

        smooth_window = min(int(smooth_window), channels if channels % 2 == 1 else channels - 1)
        if smooth_window % 2 == 0:
            smooth_window += 1

        if smooth_window > 1:
            kernel = np.ones(smooth_window, dtype=np.float64)
            smoothed_sum = np.convolve(np.where(valid, signal, 0.0), kernel, mode="same")
            smoothed_count = np.convolve(valid.astype(np.float64), kernel, mode="same")
            smoothed_signal = np.divide(
                smoothed_sum,
                smoothed_count,
                out=np.full(signal.shape, -np.inf, dtype=np.float64),
                where=smoothed_count > 0,
            )
        else:
            smoothed_signal = np.where(valid, signal, -np.inf)

        # A flagged channel cannot be selected merely because valid neighbours
        # make its smoothed value large.
        smoothed_signal[~valid] = -np.inf

        peak_bin = int(np.argmax(smoothed_signal))

        # Estimate baseline using all but a window around the peak
        window_width = max(3, int(window_frac * channels))
        half_width = window_width // 2
        exclude_start = max(0, peak_bin - half_width)
        exclude_end = min(channels, peak_bin + half_width + 1)
        noise_mask = valid.copy()
        noise_mask[exclude_start:exclude_end] = False
        noise_region = signal[noise_mask]
        if noise_region.size == 0:
            logger.warning("QA found no usable noise channels outside the signal window; metrics were left unset.")
            return signal

        # --- Baseline (robust)
        baseline = np.median(noise_region)

        # --- FWHM-based signal region detection around the peak bin ---
        peak = signal[peak_bin]
        smoothed_peak = smoothed_signal[peak_bin]
        half_max = baseline + 0.5 * (smoothed_peak - baseline)

        left_idx = int(peak_bin)
        while left_idx > 0 and valid[left_idx - 1] and smoothed_signal[left_idx - 1] >= half_max:
            left_idx -= 1

        right_idx = int(peak_bin)
        while right_idx < channels - 1 and valid[right_idx + 1] and smoothed_signal[right_idx + 1] >= half_max:
            right_idx += 1

        signal_start = left_idx
        signal_end = right_idx + 1  # exclusive

        signal_region_mask = valid[signal_start:signal_end]
        signal_region = signal[signal_start:signal_end][signal_region_mask]

        # --- Signal (peak above baseline)
        signal_lin = max(peak - baseline, 1e-12)  # avoid log(0)

        # --- Signal Power (sum above baseline)
        signal_power = np.sum(signal_region - baseline)

        # --- Noise (robust RMS via MAD)
        noise_std = 1.4826 * np.median(np.abs(noise_region - baseline))
        noise_lin = max(noise_std, 1e-12)   # avoid log(0)

        # --- SNR (linear)
        snr = signal_lin / noise_lin

        # --- Convert to dB
        signal_db = 10 * np.log10(signal_lin)
        noise_db = 10 * np.log10(noise_lin)
        snr_db = 10 * np.log10(snr)
        signal_pwr_db = 10 * np.log10(max(signal_power, 1e-12))

        # --- Dynamic range (peak signal - noise floor in dB)
        # Clamp to a finite, non-negative value so startup/empty spectra do not
        # produce -inf and fail QA schema validation.
        dynamic_range_ratio = max(peak, 1e-12) / noise_lin if noise_lin > 0 else np.inf
        dynamic_range_db = max(0.0, 10 * np.log10(dynamic_range_ratio)) if np.isfinite(dynamic_range_ratio) else np.inf

        # --- FWHM (full width at half maximum) in bins ---
        fwhm = float(right_idx - left_idx + 1) if signal_region.size > 0 else 0.0

        qa.baseline = float(baseline)
        qa.snr_db = snr_db
        qa.signal_db = signal_db
        qa.noise_db = noise_db
        qa.signal_start = signal_start
        qa.signal_end = signal_end
        qa.dynamic_range_db = dynamic_range_db
        qa.fwhm = fwhm
        qa.signal_pwr_db = signal_pwr_db

        # No modifications to the signal !
        return signal

    @classmethod
    def describe(cls) -> str:
        return "Measure high-level quality metrics such as baseline, SNR, linewidth, and dynamic range and store them on the scan."

def main():

    import logging
    logging.basicConfig(level=logging.INFO)

    from queue import Queue
 
    sky_q = Queue()   # Set the sky queue in the pipeline factory to None
    cal_q = Queue()   # Set the calibration queue in the pipeline factory to None

    from models.scan import ScanModel, ScanState
    from datetime import datetime, timezone

    scan001 = ScanModel(
        dig_id="dig001",
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=1,
        scan_iter=5,
        created=datetime.now(timezone.utc),
        read_start=datetime.now(timezone.utc),
        read_end=datetime.now(timezone.utc),
        start_idx=100,
        duration=60,
        sample_rate=1024.0,
        spectral_resolution=1024,
        center_freq=1420405752.0,
        gain=50.0,
        load=False,
        status=ScanState.WIP,
        load_failures=0,
        last_update=datetime.now(timezone.utc)
    )

    from obs.scan import Scan

    scan = Scan(scan_model=scan001)
    sky_q.put(scan)  # Put the scan in the sky queue for processing

    load001 = scan001.copy()
    load001.load = True
    load_scan = Scan(scan_model=load001)
    load_scan.mpr = load_scan.mpr / 0.5
    cal_q.put(load_scan)  # Put the load scan in the cal queue for processing

    params={}
    params['scan'] = scan      # The scan that the pipeline will process
    params['sky_q'] = sky_q    # Pipeline steps are provided access to the sky queue if needed
    params['cal_q'] = cal_q      # Pipeline steps are provided access to the calibration queue if needed

    # Example StepConfig for QA calculations
    step_config = StepConfig(step=StepType.QA, params=params)
    qa_step = QA(step_config)
    
    # Example signal and context
    import numpy as np
    input_signal = np.random.rand(1024)

    print("Original signal: ", input_signal)
    context = {
        "pipeline": "cal",
        "window_frac": 0.2,
        "channel_flags": empty_channel_flags(input_signal.shape),
    }

    output_signal = qa_step.process(context, input_signal)
    print("Processed signal:", output_signal)

    print("Scan QA after QA calculation:")
    print(scan.scan_qa)

if __name__ == "__main__":
    main()
