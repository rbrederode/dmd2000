import numpy as np
import logging
from typing import Any, List, Dict
from numpy.lib.stride_tricks import sliding_window_view

from models.pipeline import StepConfig, StepType
from models.scan import ScanType
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
        modifying it in-place.
        """
        if not isinstance(signal, np.ndarray):
            raise ValueError("RFIFlag: signal must be a numpy array.")

        if not isinstance(context, dict):
            raise ValueError("RFIFlag: context must be a dictionary.")

        # If this is a load scan, we should not apply rfi flagging, because we end up dividing the sky signal by the load scan
        # and this effectively cancels out rfi that is present in both, before we apply rfi flagging in the resultant signal. 
        if self.scan.get_scan_type() == ScanType.LOAD:
            return signal
        
        if self.scan_qa is None:
            self.scan_qa = self.scan.get_qa()
            self.scan_qa = self.scan.init_qa() if self.scan_qa is None else self.scan_qa  # Ensure the scan QA is initialised

        pipeline = context.get("pipeline", "unknown")  # Get the pipeline name from the context 

        n = context.get("threshold", 5) # Threshold multiplier for MAD, 6-7 is recommended for pulsar search
        window_size = context.get("window_size", 21)  # Must be odd
        if window_size % 2 == 0:
            raise ValueError("window_size must be odd.")

        if window_size < 1:
            raise ValueError("window_size must be >= 1.")

        pad = window_size // 2
        padded_signal = np.pad(signal, pad_width=pad, mode="edge")
        windows = sliding_window_view(padded_signal, window_shape=window_size)

        local_median = np.median(windows, axis=1)
        local_mad = np.median(np.abs(windows - local_median[:, np.newaxis]), axis=1)
        threshold = n * np.maximum(local_mad, 1e-12)

        flagged_mask = np.abs(signal - local_median) > threshold
        num_flagged = int(np.count_nonzero(flagged_mask))

        if num_flagged > 0:
            signal[flagged_mask] = local_median[flagged_mask]

        # --- Update scan QA attributes. Filterbank rows are short-timescale
        # products and do not map cleanly onto the scan-level spr/cal/mpr QA.
        if pipeline in {"spr", "cal", "mpr"}:
            sec = context.get("sec", max(self.scan.get_loaded_seconds(), 1))
            idx = sec - 1
            qa = self.scan_qa.getQA(pipeline, idx)

            if qa is not None:
                qa.rfi_fraction = num_flagged / len(signal) if len(signal) > 0 else 0.0

        logger.debug(f"RFIFlag (sliding window): Flagged {num_flagged} channels as RFI outliers using window_size={window_size}, threshold={n}*MAD")
        return signal

    @classmethod
    def describe(cls) -> str:
        return "Detect and suppress likely RFI outliers using a sliding-window median absolute deviation filter."


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
    context = {"pipeline": "cal", "channels": 1024, "rfi": 10}

    processed_signal = rfi_step.process(context, signal)
    print("Processed signal:", processed_signal)

    print("Scan QA after RFI flagging:")
    print(scan.scan_qa)

if __name__ == "__main__":
    main()
