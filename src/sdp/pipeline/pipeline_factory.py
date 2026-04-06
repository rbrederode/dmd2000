import logging
from typing import Any, List, Dict
from queue import Queue
import time

from models.pipeline import PipelineConfig, StepConfig, StepType

logger = logging.getLogger(__name__)

class ProcessingStep:
    """
    Base class for all processing steps in a processing pipeline.
    Each step should implement the process method.
    """
    def __init__(self, config: StepConfig = None):

        if config is None or not isinstance(config, StepConfig):
            raise ValueError(f"ProcessingStep: step config {config} is None or not a StepConfig instance. Cannot initialise the step.")

        self.config = config

    def process(self, context: Any, signal: Any) -> Any:
        """
        Process the signal and return the processed signal.
        Override this method in subclasses.
        """
        raise NotImplementedError("process() must be implemented by subclasses.")

class ProcessingPipeline:
    """
    Manages a sequence of ProcessingStep objects and applies them to a signal.
    """
    
    def __init__(self, steps: List[ProcessingStep] = None):
        self.steps = steps or []

    def add_step(self, step: ProcessingStep):
        self.steps.append(step)

    def process(self, context: Any, signal: Any) -> Any:

        # Iterate through the processing steps and apply each one to the signal, passing the context as needed
        # Time each step and log a warning if it takes longer than a certain threshold (e.g. 100ms)
        for step in self.steps:
            
            step_pipeline = step.config.params.get("pipeline", "unknown")  # Get the pipeline name from the step config for logging
            context_pipeline = context.get("pipeline", "unknown") if isinstance(context, dict) else "unknown"
            
            if step_pipeline == context_pipeline:

                start_time = time.time()
                signal = step.process(context=context, signal=signal)
                elapsed = time.time() - start_time

                if elapsed >= 0.1:  # Log a warning if processing takes longer than 100ms
                    logger.warning(f"Processing step {step.__class__.__name__} in pipeline '{step_pipeline}' took {elapsed*1000:.2f} milliseconds!")
                else:
                    logger.debug(f"Processing step {step.__class__.__name__} in pipeline '{step_pipeline}' took {elapsed*1000:.2f} milliseconds.")

        return signal

class ProcessingPipelineFactory:
    """
    Factory to create ProcessingPipeline instances for a given digitiser, using PipelineConfig.
    """
    def __init__(self, pipeline_config: PipelineConfig):
        self.pipeline_config = pipeline_config

    def get_steps_for_dig(self, scan: "Scan" = None, scan_q: Queue = None, cal_q: Queue = None) -> List[ProcessingStep]:
        """ 
        Get the list of ProcessingStep instances for the given digitiser ID based on the pipeline configuration.
        If there are no specific steps for the dig_id, it will fall back to the 'default' steps.
        """
        step_configs = self.pipeline_config.get_steps(scan.scan_model.dig_id)

        for config in step_configs:
            config.params['scan']   = scan      # The scan that the pipeline will process
            config.params['scan_q'] = scan_q    # Pipeline steps are provided access to the scan queue if needed
            config.params['cal_q']  = cal_q     # Pipeline steps are provided access to the calibration queue if needed

        return [self.instantiate_step(config) for config in step_configs]

    def instantiate_step(self, config: StepConfig) -> ProcessingStep:
        """
        Instantiate a processing step based on its StepConfig.
        cfg is a StepConfig instance.
        """
        # Import step classes here to avoid circular imports
        from sdp.pipeline.steps.nop      import Nop
        from sdp.pipeline.steps.dc_spike import DCSpike
        from sdp.pipeline.steps.load     import LoadCal
        from sdp.pipeline.steps.gain     import GainCal
        from sdp.pipeline.steps.tsys     import TsysCal
        from sdp.pipeline.steps.rfi      import RFIFlag
        from sdp.pipeline.steps.qa       import QA
        # Add more step imports as needed

        step_map = {
            StepType.NOP.value:      Nop,
            StepType.DC_SPIKE.value: DCSpike,
            StepType.LOAD.value:     LoadCal,
            StepType.GAIN_CAL.value: GainCal,
            StepType.TSYS_CAL.value: TsysCal,
            StepType.RFI_FLAG.value: RFIFlag,
            StepType.QA.value:       QA,
            # Add more step types as needed
        }

        # Return an instance of the step class (passing config to the constructor)
        return step_map[config.step.value](config)

    def create_pipeline(self, scan: "Scan", scan_q: Queue, cal_q: Queue):
        steps = self.get_steps_for_dig(scan, scan_q, cal_q)
        return ProcessingPipeline(steps)
