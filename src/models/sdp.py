# -*- coding: utf-8 -*-

import enum
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus
from models.dig import DigitiserList
from models.health import HealthState
from models.pipeline import StepConfig, StepType, PipelineConfig
from models.scan import ScanModel, ScanState
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class ScienceDataProcessorModel(BaseModel):
    """A class representing the science data processor model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ScienceDataProcessorModel"),
        "sdp_id": And(str, lambda v: isinstance(v, str)),
        "app": And(AppModel, lambda v: isinstance(v, AppModel)),
        "dig_store": And(DigitiserList, lambda v: isinstance(v, DigitiserList)),
        "scans_created": And(int, lambda v: v >= 0),
        "scans_wip": And(int, lambda v: v >= 0),
        "scans_completed": And(int, lambda v: v >= 0),
        "scans_aborted": And(int, lambda v: v >= 0),
        "pipeline_config": And(PipelineConfig, lambda v: isinstance(v, PipelineConfig)),
        "processing_scans": And(list, lambda v: all(isinstance(item, ScanModel) for item in v)),
        "tm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "ScienceDataProcessorModel",
            "sdp_id": "<undefined>",
            "app": AppModel(
                app_name="sdp",
                app_running=False,
                app_cmd_port=60004,
                num_processors=0,
                queue_size=0,
                interfaces=[],
                processors=[],
                health=HealthState.UNKNOWN,
                last_update=datetime.now(timezone.utc),
            ),
            "dig_store": DigitiserList(list_id="sdplist001"),
            "scans_created": 0,
            "scans_wip": 0,
            "scans_completed": 0,
            "scans_aborted": 0,
            "processing_scans": [],
            "pipeline_config": PipelineConfig(),
            "tm_connected": CommunicationStatus.NOT_ESTABLISHED,
            "dig_connected": CommunicationStatus.NOT_ESTABLISHED,
            "last_update": datetime.now(timezone.utc)
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def add_scan(self, scan: ScanModel):
        """Add a scan to the processing queue.
            A single scan is maintained in the processing scans list per digitiser id.
            If a scan with the same digitiser id already exists, it will be replaced.

        Args:
            scan (ScanModel): The scan model to add.
        """

        if not isinstance(scan, ScanModel):
            raise XAPIValidationFailed("Science Data Processor Model: scan must be an instance of ScanModel")

        # Replace existing scan with the same digitiser id
        for i, existing_scan in enumerate(self.processing_scans):
            if existing_scan.dig_id == scan.dig_id:
                self.processing_scans[i] = scan
                return

        self.processing_scans.append(scan)


if __name__ == "__main__":

    scan001 = ScanModel(
        scan_id="scan001",
        dig_id="dig001",
        duration=120,
        sample_rate=2000000,
        spectral_resolution=2048,
        center_freq=1420000000,
        gain=30,
        status=ScanState.WIP,
    )

    scan002 = ScanModel(
        scan_id="scan002",
        dig_id="dig002",
        duration=120,
        sample_rate=2000000,
        spectral_resolution=2048,
        center_freq=1420000000,
        gain=30,
        status=ScanState.WIP,
    )

    scan003 = ScanModel(
        scan_id="scan003",
        dig_id="dig001",
        duration=60,
        sample_rate=2000000,
        spectral_resolution=2048,
        center_freq=1420000000,
        gain=30,
        status=ScanState.COMPLETE,
    )
    
    sdp001 = ScienceDataProcessorModel(
        sdp_id="sdp001",
        app=AppModel(
            app_name="sdp",
            app_running=True,
            num_processors=2,
            queue_size=0,
            interfaces=["tm", "dig"],
            processors=[],
            health=HealthState.UNKNOWN,
            last_update=datetime.now(timezone.utc)
        ),
        spectral_resolution=0,
        scan_duration=0,
        processing_scans=[scan001],
        tm_connected=CommunicationStatus.NOT_ESTABLISHED,
        dig_connected=CommunicationStatus.NOT_ESTABLISHED,
        pipeline_factory=PipelineConfig(),
        last_update=datetime.now(timezone.utc)
    )

    sdp001.add_scan(scan002)
    sdp001.add_scan(scan003)

    sdp002 = ScienceDataProcessorModel(id="sdp002")

    import pprint
    print("="*40)
    print("sdp001 Model Initialized")
    print("="*40)
    pprint.pprint(sdp001.to_dict())

    print("="*40)
    print("sdp002 Model with Defaults Initialized")
    print("="*40)
    pprint.pprint(sdp002.to_dict())

    print("="*40)
    print('Testing from_dict')
    print('='*40)

    dict_str = "{'_type': 'ScienceDataProcessorModel', 'id': 'sdp001', 'app': {'_type': 'AppModel', 'app_name': 'sdp', 'app_running': True, 'health': {'_type': 'enum.IntEnum', 'instance': 'HealthState', 'value': 'OK'}, 'num_processors': 4, 'queue_size': 0, 'interfaces': ['tm', 'dig'], 'processors': [{'_type': 'ProcessorModel', 'name': 'Thread-4', 'current_event': None, 'processing_time_ms': None, 'last_update': {'_type': 'datetime', 'value': '2025-11-09T18:04:29.743187+00:00'}}, {'_type': 'ProcessorModel', 'name': 'Thread-5', 'current_event': 'StatusUpdateEvent (Enqueued Timestamp=2025-11-09 18:04:29.742739 , Dequeued Timestamp=2025-11-09 18:04:29.742827 , Updated Timestamp=[None], Current Status=BEING PROCESSED, Total Processing Count=48, Total Processing Time (ms)=0.0166170597076416, Average Processing Time (ms)=0.0003461887439092)', 'processing_time_ms': 0.0005192756652832031, 'last_update': {'_type': 'datetime', 'value': '2025-11-09T18:04:29.743280+00:00'}}, {'_type': 'ProcessorModel', 'name': 'Thread-6', 'current_event': None, 'processing_time_ms': None, 'last_update': {'_type': 'datetime', 'value': '2025-11-09T18:04:29.743365+00:00'}}, {'_type': 'ProcessorModel', 'name': 'Thread-7', 'current_event': None, 'processing_time_ms': None, 'last_update': {'_type': 'datetime', 'value': '2025-11-09T18:04:29.743429+00:00'}}], 'msg_timeout_ms': 10000, 'arguments': {'verbose': False, 'num_processors': 4, 'tm_host': '192.168.0.3', 'tm_port': 50001, 'dig_host': '192.168.0.3', 'dig_port': 60000, 'output_dir': '/Users/r.brederode/samples'}, 'last_update': {'_type': 'datetime', 'value': '2025-11-09T18:04:29.743979+00:00'}}, 'channels': 1024, 'scan_duration': 60, 'scans_created': 1, 'scans_completed': 0, 'scans_aborted': 0, 'processing_scans': [{'_type': 'ScanModel', 'scan_id': '1', 'dig_id': 'dig001', 'created': {'_type': 'datetime', 'value': '2025-11-09T17:58:03.933889+00:00'}, 'read_start': {'_type': 'datetime', 'value': '2025-11-09T17:58:01.478359+00:00'}, 'read_end': {'_type': 'datetime', 'value': '2025-11-09T17:58:02.505563+00:00'}, 'gap': None, 'start_idx': 2, 'duration': 60, 'sample_rate': 2048000, 'channels': 1024, 'center_freq': 1422400000, 'gain': 23, 'status': {'_type': 'enum.IntEnum', 'instance': 'ScanState', 'value': 'WIP'}, 'load_failures': 0, 'last_update': {'_type': 'datetime', 'value': '2025-11-09T17:58:03.932428+00:00'}}], 'tm_connected': {'_type': 'enum.IntEnum', 'instance': 'CommunicationStatus', 'value': 'ESTABLISHED'}, 'dig_connected': {'_type': 'enum.IntEnum', 'instance': 'CommunicationStatus', 'value': 'ESTABLISHED'}, 'last_update': {'_type': 'datetime', 'value': '2025-11-09T17:40:59.486875+00:00'}}"
    sdp_from_dict = ScienceDataProcessorModel.from_dict(eval(dict_str))
    pprint.pprint(sdp_from_dict.to_dict())

    print("="*40)
    print('Testing SDP pipeline')
    print('='*40)

    step1 = StepConfig(step=StepType.REMOVE_DC_SPIKE, params={})
    step2 = StepConfig(step=StepType.APPLY_LOAD, params={})
    step3 = StepConfig(step=StepType.RFI_FLAGGING, params={"threshold": 5.0})
    
    pipeline_config = PipelineConfig(steps_map={
        "default": [step1, step2],
        "dig001": [step1, step3],
    })

    sdp001.pipeline_config = pipeline_config
    pprint.pprint(sdp001.to_dict())

