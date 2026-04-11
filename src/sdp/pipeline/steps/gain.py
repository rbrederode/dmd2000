import numpy as np
import logging
from typing import Any, List, Dict

from models.pipeline import StepConfig, StepType
from sdp.pipeline.pipeline_factory import ProcessingStep

logger = logging.getLogger(__name__)

class GainCal(ProcessingStep):

    def __init__(self, config: StepConfig = None):
        super().__init__(config)
    
    def process(self, context: Any, signal: Any) -> Any:
        """
        Apply gain calibration to the signal array using parameters from context.

        Args:
            context: dict containing static parameters for applying gain calibration
            input_signal: 1D numpy array containing input spectrum (signal)
        Returns:
            1D numpy array containing processed output spectrum (signal)
        """

        if not isinstance(signal, np.ndarray):
            raise ValueError("GainCal: signal must be a numpy array.")

        if not isinstance(context, dict):
            raise ValueError("GainCal: context must be a dictionary.")

        # ... apply load file logic using context parameters ...
        return signal

    @classmethod
    def describe(cls) -> str:
        return "Apply gain calibration to scale the spectrum using configured gain information."


def main():
 
    # Example StepConfig for gain calibration
    step_config = StepConfig(step=StepType.GAIN_CAL, params={"gain": 10})
    gain_step = GainCal(step_config)

    # Example signal and context
    import numpy as np
    signal = np.random.rand(1024)
    print("Original signal: ", signal)
    context = {"channels": 1024, "gain": 10}

    processed_signal = gain_step.process(context, signal)
    print("Processed signal:", processed_signal)

if __name__ == "__main__":
    main()
