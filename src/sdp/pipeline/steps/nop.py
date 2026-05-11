import numpy as np
import logging
from typing import Any, List, Dict

from models.pipeline import StepConfig, StepType
from sdp.pipeline.pipeline_factory import ProcessingStep

logger = logging.getLogger(__name__)

class Nop(ProcessingStep):

    def __init__(self, config: StepConfig = None):
        super().__init__(config)
    
    def process(self, context: Any, signal: Any) -> Any:
        """
        Apply no-operation to the signal array.

        Args:
            context: dict containing static parameters (none required for nop)
            input_signal: 1D numpy array containing input spectrum (signal)
        Returns:
            1D numpy array containing processed output spectrum (signal)
        """

        if not isinstance(signal, np.ndarray):
            raise ValueError("Nop: signal must be a numpy array.")

        if not isinstance(context, dict):
            raise ValueError("Nop: context must be a dictionary.")

        # ... apply load file logic using context parameters ...
        return signal

    @classmethod
    def describe(cls) -> str:
        return "Pass the signal through unchanged without applying any processing."

def main():

    # Example StepConfig for gain calibration
    step_config = StepConfig(step=StepType.NOP, params={})
    nop_step = Nop(step_config)

    # Example signal and context
    import numpy as np
    signal = np.random.rand(1024)
    print("Original signal: ", signal)
    context = {"channels": 1024, "nop": 10}

    processed_signal = nop_step.process(context, signal)
    print("Processed signal:", processed_signal)

if __name__ == "__main__":
    main()
