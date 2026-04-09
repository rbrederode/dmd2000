import numpy as np
import logging
from typing import Any, List, Dict

from models.pipeline import StepConfig, StepType
from sdp.pipeline.pipeline_factory import ProcessingStep, ProcessingPipeline

logger = logging.getLogger(__name__)

class DCSpike(ProcessingStep):

    def __init__(self, config: StepConfig = None):
        super().__init__(config)

        self.scan = config.params["scan"] if "scan" in config.params else None

        logger.debug("DCSpike pipeline step initialisation with scan:\n%s", str(self.scan))

        if self.scan is None:
            raise ValueError("DCSpike: scan must be set before initializing DCSpike step.")

        self.channels = self.scan.scan_model.channels  # Get the number of channels from the scan to use in the DC spike removal process
    
    def process(self, context: Any, signal: Any) -> Any:
        """
        Remove DC spike from the signal array using the number of channels from initialisation.

        Ref: https://pysdr.org/content/sampling.html#dc-spike-and-offset-tuning

        Args:
            context: dict containing static parameters for removing DC spike
            input_signal: 1D numpy array containing input spectrum (signal)
        Returns:
            1D numpy array containing processed output spectrum (signal)
        """

        if not isinstance(signal, np.ndarray):
            raise ValueError("DCSpike: signal must be a numpy array.")

        # Review the bins either side the centre of channels
        # We expect the DC spike to occur in the central bin
        start = self.channels//2-1 # Zero indexed array
        end =  self.channels//2+2 # DC spike is in the middle

        # Calculate the mean and std deviation of the reviewed samples
        mean = np.mean(signal[start:end])
        std = np.std(signal[start:end])

        # Create a mask for values above one standard deviation from the mean
        mask = signal[start:end] > (mean + std)

        # Replace values above the threshold with the mean of the non-spike values
        signal[start:end][mask] = np.mean(signal[start:end][~mask]) 
        return signal

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
    sky_q.put(scan)  # Put the scan in the sky queue for processing

    params={}
    params['scan'] = scan      # The scan that the pipeline will process
    params['sky_q'] = sky_q    # Pipeline steps are provided access to the sky queue if needed
    params['cal_q'] = cal_q    # Pipeline steps are provided access to the calibration queue if needed

    # Example StepConfig 
    step_config = StepConfig(step=StepType.DC_SPIKE, params=params)
    dcspike_step = DCSpike(step_config)

    # Example signal and context
    import numpy as np
    signal = np.random.rand(1024)
    # Introduce a big spike in the middle bin
    signal[512] = 1e6
    print("Original signal: ", signal[510:515])
    context = {}

    processed_signal = dcspike_step.process(context, signal)
    print("Processed signal:", processed_signal[510:515])

if __name__ == "__main__":
    main()