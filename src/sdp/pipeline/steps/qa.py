import numpy as np
import logging
from typing import Any, List, Dict

from models.pipeline import StepConfig, StepType
from sdp.pipeline.pipeline_factory import ProcessingStep, ProcessingPipeline

logger = logging.getLogger(__name__)

class QA(ProcessingStep):

    def __init__(self, config: StepConfig = None):
        super().__init__(config)

        self.scan = config.params["scan"] if "scan" in config.params else None
        self.scan_qa = self.scan.get_qa() if self.scan else None

        logger.debug("QA pipeline step initialisation with scan qa:\n%s", str(self.scan_qa))

        if self.scan_qa is None:
            raise ValueError(f"QA: scan_qa {self.scan_qa} must be set before initialising QA step.")
    
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

        if not isinstance(context, dict):
            raise ValueError("QA: context must be a dictionary.")

        pipeline = context.get("pipeline", "unknown")  # Get the pipeline name from the context 
        window_frac = context.get("window_frac", 0.2)  # Fraction of channels to consider around the peak for signal region
        smooth_window = max(1, int(context.get("smooth_window", 10)))  # Odd window size for linewidth detection smoothing

        channels = len(signal)
        if smooth_window % 2 == 0:
            smooth_window += 1

        if smooth_window > 1:
            kernel = np.ones(smooth_window, dtype=np.float64) / smooth_window
            smoothed_signal = np.convolve(signal, kernel, mode="same")
        else:
            smoothed_signal = signal

        peak_bin = int(np.argmax(smoothed_signal))

        # Estimate baseline using all but a window around the peak
        window_width = max(3, int(window_frac * channels))
        half_width = window_width // 2
        exclude_start = max(0, peak_bin - half_width)
        exclude_end = min(channels, peak_bin + half_width + 1)
        if exclude_start == 0:
            noise_region = signal[exclude_end:]
        elif exclude_end == channels:
            noise_region = signal[:exclude_start]
        else:
            noise_region = np.concatenate((signal[:exclude_start], signal[exclude_end:]))

        # --- Baseline (robust)
        baseline = np.median(noise_region)

        # --- FWHM-based signal region detection around the peak bin ---
        peak = np.max(signal)
        smoothed_peak = smoothed_signal[peak_bin]
        half_max = baseline + 0.5 * (smoothed_peak - baseline)

        left_idx = int(peak_bin)
        while left_idx > 0 and smoothed_signal[left_idx - 1] >= half_max:
            left_idx -= 1

        right_idx = int(peak_bin)
        while right_idx < channels - 1 and smoothed_signal[right_idx + 1] >= half_max:
            right_idx += 1

        signal_start = left_idx
        signal_end = right_idx + 1  # exclusive

        signal_region = signal[signal_start:signal_end]

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
        dynamic_range_db = 10 * np.log10(peak / noise_lin) if noise_lin > 0 else np.inf

        # --- FWHM (full width at half maximum) in bins ---
        fwhm = float(right_idx - left_idx + 1) if signal_region.size > 0 else 0.0

        # --- Update scan QA attributes
        sec = context.get("sec", self.scan.get_loaded_seconds())
        qa = self.scan_qa.getQA(pipeline, sec)

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

def main():

    import logging
    logging.basicConfig(level=logging.INFO)

    from queue import Queue
 
    scan_q = Queue()  # Set the calibration queue in the pipeline factory to None
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
        channels=1024,
        center_freq=1420405752.0,
        gain=50.0,
        load=False,
        status=ScanState.WIP,
        load_failures=0,
        last_update=datetime.now(timezone.utc)
    )

    from obs.scan import Scan

    scan = Scan(scan_model=scan001)
    scan_q.put(scan)  # Put the scan in the scan queue for processing

    load001 = scan001.copy()
    load001.load = True
    load_scan = Scan(scan_model=load001)
    load_scan.mpr = load_scan.mpr / 0.5
    cal_q.put(load_scan)  # Put the load scan in the cal queue for processing

    params={}
    params['scan'] = scan     # The scan that the pipeline will process
    params['scan_q'] = scan_q    # Pipeline steps are provided access to the scan queue if needed
    params['cal_q'] = cal_q      # Pipeline steps are provided access to the calibration queue if needed

    # Example StepConfig for QA calculations
    step_config = StepConfig(step=StepType.QA, params=params)
    qa_step = QA(step_config)
    
    # Example signal and context
    import numpy as np
    input_signal = np.random.rand(1024)

    print("Original signal: ", input_signal)
    context = {"pipeline": "cal", "window_frac": 0.2}  # Example context with pipeline name and window fraction

    output_signal = qa_step.process(context, input_signal)
    print("Processed signal:", output_signal)

    print("Scan QA after QA calculation:")
    print(scan.scan_qa)

if __name__ == "__main__":
    main()
