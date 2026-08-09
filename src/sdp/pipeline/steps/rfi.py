import numpy as np
import logging
import warnings
from typing import Any, List, Dict
from numpy.lib.stride_tricks import sliding_window_view

from models.pipeline import StepConfig, StepType
from models.scan import ScanType
from sdp.channel_mask import ChannelFlag, empty_channel_flags, valid_channels
from sdp.pipeline.pipeline_factory import ProcessingStep

logger = logging.getLogger(__name__)

class RFIFlag(ProcessingStep):

    def __init__(self, config: StepConfig = None):
        super().__init__(config)

        logger.debug("RFIFlag pipeline step initialisation with config:\n%s", str(self.config))

        self.scan = config.params["scan"] if "scan" in config.params else None

        if self.scan is None:
            raise ValueError("RFIFlag step requires 'scan' in config.params. It is currently None.")

        self.scan_qa = self.scan.get_qa()
        
        logger.debug("RFIFlag pipeline step initialised with scan:\n%s", str(self.scan))

    def process(self, context: Any, signal: Any) -> Any:
        """
        Apply a vectorized sliding-window MAD RFI flagging pass to the signal array,
        recording detected channels without modifying the measured spectrum.
        """
        if not isinstance(signal, np.ndarray):
            raise ValueError("RFIFlag: signal must be a numpy array.")

        if signal.ndim != 1:
            raise ValueError(f"RFIFlag: signal must be one-dimensional, got shape {signal.shape}.")
        if signal.size == 0:
            raise ValueError("RFIFlag: signal must contain at least one channel.")

        if not isinstance(context, dict):
            raise ValueError("RFIFlag: context must be a dictionary.")

        channel_flags = context.get("channel_flags")
        if not isinstance(channel_flags, np.ndarray):
            raise ValueError("RFIFlag: context must contain a NumPy 'channel_flags' array.")
        if channel_flags.shape != signal.shape:
            raise ValueError(
                "RFIFlag: signal and channel_flags must have the same shape; "
                f"got {signal.shape} and {channel_flags.shape}."
            )
        if not np.issubdtype(channel_flags.dtype, np.unsignedinteger):
            raise ValueError(
                f"RFIFlag: channel_flags must use an unsigned integer dtype, got {channel_flags.dtype}."
            )
        if not channel_flags.flags.writeable:
            raise ValueError("RFIFlag: channel_flags must be writable.")

        # If this is a load scan, we should not apply rfi flagging, because we end up dividing the sky signal by the load scan
        # and this effectively cancels out rfi that is present in both, before we apply rfi flagging in the resultant signal. 
        if self.scan.get_scan_type() == ScanType.LOAD:
            return signal
        
        if self.scan_qa is None:
            self.scan_qa = self.scan.get_qa()
            self.scan_qa = self.scan.init_qa() if self.scan_qa is None else self.scan_qa  # Ensure the scan QA is initialised

        pipeline = context.get("pipeline", "unknown")  # Get the pipeline name from the context 

        n = context.get("threshold", self.config.params.get("threshold", 5))
        window_size = context.get("window_size", self.config.params.get("window_size", 21))
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not np.isfinite(n) or n <= 0:
            raise ValueError("threshold must be a finite number greater than zero.")
        if isinstance(window_size, bool) or not isinstance(window_size, (int, np.integer)):
            raise ValueError("window_size must be an integer.")
        if window_size % 2 == 0:
            raise ValueError("window_size must be odd.")

        if window_size < 1:
            raise ValueError("window_size must be >= 1.")

        # Reprocessing replaces only the RFI decision. Flags raised by bandpass,
        # calibration, or users remain intact and make those channels ineligible
        # both as candidates and as contributors to neighbouring statistics.
        rfi_bit = np.array(int(ChannelFlag.RFI_DETECTED), dtype=channel_flags.dtype)
        channel_flags[:] &= np.bitwise_not(rfi_bit)
        eligible = valid_channels(signal, channel_flags)

        pad = window_size // 2
        padded_signal = np.pad(signal, pad_width=pad, mode="constant", constant_values=np.nan)
        padded_eligible = np.pad(eligible, pad_width=pad, mode="constant", constant_values=False)
        windows = sliding_window_view(padded_signal, window_shape=window_size)
        eligible_windows = sliding_window_view(padded_eligible, window_shape=window_size)
        masked_windows = np.where(eligible_windows, windows, np.nan)

        # nanmedian warns for an all-NaN window; such windows are rejected by
        # enough_samples below, so the warning carries no useful information.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="All-NaN slice encountered", category=RuntimeWarning)
            local_median = np.nanmedian(masked_windows, axis=1)
            local_mad = np.nanmedian(
                np.abs(masked_windows - local_median[:, np.newaxis]),
                axis=1,
            )
        threshold = n * np.maximum(local_mad, 1e-12)

        valid_samples_per_window = np.count_nonzero(eligible_windows, axis=1)
        minimum_samples = min(window_size, 3)
        enough_samples = valid_samples_per_window >= minimum_samples
        flagged_mask = eligible & enough_samples & (np.abs(signal - local_median) > threshold)
        num_flagged = int(np.count_nonzero(flagged_mask))
        channel_flags[flagged_mask] |= rfi_bit

        # --- Update scan QA attributes. Filterbank rows are short-timescale
        # products and do not map cleanly onto the scan-level spr/cal/mpr QA.
        if pipeline in {"spr", "cal", "mpr"}:
            sec = context.get("sec", max(self.scan.get_loaded_seconds(), 1))
            idx = sec - 1
            qa = self.scan_qa.getQA(pipeline, idx)

            if qa is not None:
                eligible_count = int(np.count_nonzero(eligible))
                qa.rfi_fraction = num_flagged / eligible_count if eligible_count > 0 else 0.0

        logger.debug(f"RFIFlag (sliding window): Flagged {num_flagged} channels as RFI outliers using window_size={window_size}, threshold={n}*MAD")
        return signal

    @classmethod
    def describe(cls) -> str:
        return "Detect and flag likely RFI outliers using a sliding-window median absolute deviation filter without changing measured values."


def main():
 
    # Set log level to info for demonstration
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
    params['scan'] = scan        # The scan that the pipeline will process
    params['sky_q'] = sky_q      # Pipeline steps are provided access to the sky queue if needed
    params['cal_q'] = cal_q      # Pipeline steps are provided access to the calibration queue if needed
    params['threshold'] = 5      # Threshold for RFI flagging
    params['window_size'] = 21   # Window size for RFI flagging (must be odd)

    # Example StepConfig for RFI flagging
    step_config = StepConfig(step=StepType.RFI_FLAG, params=params)
    rfi_step = RFIFlag(step_config)

    # Example signal and context
    import numpy as np
    signal = np.random.rand(1024)
    print("Original signal: ", signal)
    context = {
        "pipeline": "cal",
        "channels": 1024,
        "rfi": 10,
        "channel_flags": empty_channel_flags(signal.shape),
    }

    processed_signal = rfi_step.process(context, signal)
    print("Processed signal:", processed_signal)

    print("Scan QA after RFI flagging:")
    print(scan.scan_qa)

if __name__ == "__main__":
    main()
