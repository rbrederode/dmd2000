import numpy as np
import logging
import time
from queue import Queue
from typing import Any, List, Dict

from models.pipeline import StepConfig, StepType
from models.scan import ScanType, ScanState
from sdp.channel_mask import ChannelFlag, empty_channel_flags
from sdp.pipeline.pipeline_factory import ProcessingStep, ProcessingPipeline

logger = logging.getLogger(__name__)

class LoadCal(ProcessingStep):

    # Cache refresh intervals (seconds) to limit queue lookups during hot path processing
    LOAD_REFRESH_INTERVAL_SEC = 5.0

    def __init__(self, config: StepConfig = None):
        super().__init__(config)

        self.scan = config.params["scan"] if "scan" in config.params else None
        self.cal_q = config.params["cal_q"] if "cal_q" in config.params else None 
        self.cal_q = Queue() if self.cal_q is None else self.cal_q  # If no calibration queue is provided, create an empty queue

        logger.debug("LoadCal pipeline step initialisation with scan:\n%s", str(self.scan))

        if self.scan is None:
            raise ValueError(f"LoadCal: scan {self.scan} must be set before initialising LoadCal step.")

        self.load_scan = None
        self._last_cal_q_size = len(self.cal_q.queue) # Track the last seen calibration queue size to detect new load scans arriving
        
        # Perform initial matching to equivalent load scan
        self._set_load_scan(self._resolve_load_scan())

    def _resolve_load_scan(self):
        """Resolve the newest equivalent completed load scan from the calibration queue."""
        if self.scan is None or self.scan.get_scan_type() == ScanType.LOAD:
            return None

        load_scans = [
            s for s in list(self.cal_q.queue)
            if self.scan.equivalent(s)
            and s.get_scan_type() == ScanType.LOAD
            and s.get_status() == ScanState.COMPLETE
        ]

        # Prefer real load scans over synthetic ones, use the newest load scan (ideally real)
        real_load_scans = [s for s in load_scans if not s.scan_model.synthesised]
        if real_load_scans:
            return max(real_load_scans, key=lambda s: s.scan_model.created)

        if load_scans:
            return max(load_scans, key=lambda s: s.scan_model.created)

        return None

    def _set_load_scan(self, load_scan):
        """Cache a load scan and keep the scan metadata in step with it."""
        self.load_scan = load_scan

        if self.load_scan is not None:
            try:
                self.scan.set_load_scan(self.load_scan)
            except Exception as exc:
                logger.warning(f"LoadCal: could not associate load scan metadata: {exc}")

    def _should_refresh_load(self) -> bool:
        """Check if load cache should be refreshed based on queue changes
           Do not refresh if current load is real (non-default), only refresh if default or missing."""
        # If we have a real load, keep it for the duration of the scan
        if self.load_scan is not None and not self.load_scan.scan_model.synthesised:
            return False

        # While using the synthetic load, keep checking. A real load can
        # replace the synthetic queue entry without changing the queue length.
        if self.load_scan is None or self.load_scan.scan_model.synthesised:
            self._last_cal_q_size = len(self.cal_q.queue)
            return True
        
        cal_q_size = len(self.cal_q.queue)
        
        # Refresh if queue size changed (new load arrived)
        if cal_q_size != self._last_cal_q_size:
            self._last_cal_q_size = cal_q_size
            return True
        
        return False
    
    def process(self, context: Any, signal: Any) -> Any:
        """
        Divide signal by mean load scan power. Uses an equivalent load scan from the calibration queue if one exists.
        Caches resolved load and only refreshes periodically or when queue size changes to avoid hot-path O(n) scans.
        Args:
            context: dict containing static parameters for applying load file
            input_signal: 1D numpy array containing input spectrum (signal)
        Returns:
            1D numpy array containing processed output spectrum (signal)
        """

        if not isinstance(signal, np.ndarray):
            raise ValueError("LoadCal: input signal must be a numpy array.")

        if signal.ndim != 1:
            raise ValueError(f"LoadCal: signal must be one-dimensional, got shape {signal.shape}.")
        if not isinstance(context, dict):
            raise ValueError("LoadCal: context must be a dictionary.")

        channel_flags = context.get("channel_flags")
        if not isinstance(channel_flags, np.ndarray):
            raise ValueError("LoadCal: context must contain a NumPy 'channel_flags' array.")
        if channel_flags.shape != signal.shape:
            raise ValueError(
                "LoadCal: signal and channel_flags must have the same shape; "
                f"got {signal.shape} and {channel_flags.shape}."
            )
        if not np.issubdtype(channel_flags.dtype, np.unsignedinteger):
            raise ValueError(
                f"LoadCal: channel_flags must use an unsigned integer dtype, got {channel_flags.dtype}."
            )
        if not channel_flags.flags.writeable:
            raise ValueError("LoadCal: channel_flags must be writable.")

        nonfinite_bit = np.array(int(ChannelFlag.NONFINITE), dtype=channel_flags.dtype)
        channel_flags[~np.isfinite(signal)] |= nonfinite_bit

        # If this is a load scan, we should not apply the load calibration, otherwise we will divide the load scan by itself and end up 
        # with an array of ones, losing the actual load calibration values. Instead, we return the signal unchanged for load scans.
        if self.scan.get_scan_type() == ScanType.LOAD:
            return signal

        # Lazy refresh: only re-scan queue if size changed or refresh interval elapsed (avoids hot-path overhead)
        if self._should_refresh_load():
            latest_load = self._resolve_load_scan()
            if latest_load is not None and latest_load is not self.load_scan:
                self._set_load_scan(latest_load)
                logger.info(f"LoadCal pipeline step updated load calibration scan for processing:\n{self.load_scan}")

        # Check if the length of the input signal array matches the length of the load scan's spectrum
        if not self.load_scan or signal.shape != self.load_scan.mpr.shape:
            logger.warning(f"LoadCal: load_scan {'found but' if self.load_scan else 'not found'} and must be the same shape {str(self.load_scan.mpr.shape[0])+' ' if self.load_scan else ''}" + \
                f"as the scan {signal.shape[0]} on which to apply it.")

        if self.load_scan is None:
            return signal
        if signal.shape != self.load_scan.mpr.shape:
            raise ValueError(
                "LoadCal: signal and load spectrum must have the same shape; "
                f"got {signal.shape} and {self.load_scan.mpr.shape}."
            )

        load_flags = getattr(self.load_scan, "mpr_flags", None)
        if load_flags is not None:
            if not isinstance(load_flags, np.ndarray) or load_flags.shape != signal.shape:
                raise ValueError("LoadCal: load spectrum flags must match the signal shape.")
            channel_flags[:] |= load_flags.astype(channel_flags.dtype, copy=False)

        invalid_load = ~np.isfinite(self.load_scan.mpr) | (self.load_scan.mpr == 0)
        calibration_bit = np.array(int(ChannelFlag.CALIBRATION_INVALID), dtype=channel_flags.dtype)
        channel_flags[invalid_load] |= calibration_bit

        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            calibrated = signal / self.load_scan.mpr
        channel_flags[~np.isfinite(calibrated)] |= nonfinite_bit
        return calibrated

    @classmethod
    def describe(cls) -> str:
        return "Apply an equivalent load calibration spectrum to normalize the input signal."

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

    # Example StepConfig for gain calibration
    step_config = StepConfig(step=StepType.LOAD, params=params)
    load_step = LoadCal(step_config)

    # Example signal and context
    import numpy as np
    input_signal = np.random.rand(1024)

    print("Original signal: ", input_signal)
    context = {"pipeline": "cal", "channel_flags": empty_channel_flags(input_signal.shape)}

    output_signal = load_step.process(context, input_signal)
    print("Processed signal:", output_signal)

if __name__ == "__main__":
    main()
