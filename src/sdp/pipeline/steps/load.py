import numpy as np
import logging
from typing import Any, List, Dict

from models.pipeline import StepConfig, StepType
from models.scan import ScanType
from sdp.pipeline.pipeline_factory import ProcessingStep, ProcessingPipeline

logger = logging.getLogger(__name__)

class LoadCal(ProcessingStep):

    def __init__(self, config: StepConfig = None):
        super().__init__(config)

        self.scan = config.params["scan"] if "scan" in config.params else None
        self.cal_q = config.params["cal_q"] if "cal_q" in config.params else None

        logger.debug("LoadCal pipeline step initialisation with scan:\n%s", str(self.scan))

        if self.scan is None or self.cal_q is None:
            raise ValueError(f"LoadCal: scan {self.scan} and cal_q {self.cal_q} must be set before initialising LoadCal step.")

        # Find the equivalent load scan to apply to the signal if it exists in the calibration queue
        load_scans = [s for s in list(self.cal_q.queue) if s.equivalent(self.scan) and s.get_scan_type() == ScanType.LOAD]
                        
        if len(load_scans) > 0:
            self.load_scan = max(load_scans, key=lambda s: s.scan_model.created)  # Remember the newest equivalent load scan to use
            logger.debug(f"LoadCal pipeline step found load scan to apply in Processing Pipeline\n{self.load_scan}")
        else:
            self.load_scan = None
            logger.warning(f"LoadCal pipeline step did not find load to apply to scan in Processing Pipeline\n{self.scan}")
    
    def process(self, context: Any, signal: Any) -> Any:
        """
        Divid signal by mean load scan power. Uses an equivalent load scan identified during initialisation. 
        Args:
            context: dict containing static parameters for applying load file
            input_signal: 1D numpy array containing input spectrum (signal)
        Returns:
            1D numpy array containing processed output spectrum (signal)
        """

        if not isinstance(signal, np.ndarray):
            raise ValueError("LoadCal: input signal must be a numpy array.")

        # If this is a load scan, we should not apply the load calibration, otherwise we will divide the load scan by itself and end up 
        # with an array of ones, losing the actual load calibration values. Instead, we return the signal unchanged for load scans.
        if self.scan.get_scan_type() == ScanType.LOAD:
            return signal

        # Check if the length of the input signal array matches the length of the load scan's spectrum
        if not self.load_scan or signal.shape[0] != self.load_scan.mpr.shape[0]:
            logger.warning(f"LoadCal: load_scan {'found but' if self.load_scan else 'not found'} and must be the same shape {str(self.load_scan.mpr.shape[0])+' ' if self.load_scan else ''}" + \
                f"as the scan {signal.shape[0]} on which to apply it.")

        return signal / self.load_scan.mpr if self.load_scan is not None else signal

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

    # Example StepConfig for gain calibration
    step_config = StepConfig(step=StepType.LOAD, params=params)
    load_step = LoadCal(step_config)

    # Example signal and context
    import numpy as np
    input_signal = np.random.rand(1024)

    print("Original signal: ", input_signal)
    context = {}

    output_signal = load_step.process(context, input_signal)
    print("Processed signal:", output_signal)

if __name__ == "__main__":
    main()
