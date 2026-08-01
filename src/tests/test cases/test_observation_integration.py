from types import SimpleNamespace

import numpy as np

from models.pipeline import PipelineConfig, StepConfig, StepType
from obs.obs import Observation
from sdp.channel_mask import ChannelFlag, empty_channel_flags
from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory


def test_update_integrated_data_arrays_uses_loaded_seconds():
    observation = Observation.__new__(Observation)
    observation.obs_model = SimpleNamespace(obs_id="test-observation")
    key = (2, 0, 1)
    observation.int_data_arrays = {
        key: {
            "int_spr": np.zeros(4),
            "int_mpr": np.zeros(4),
            "int_tpw": [],
            "secs": 0.0,
            "scans": 0,
        }
    }

    scan = SimpleNamespace(
        scan_model=SimpleNamespace(
            tgt_idx=key[0],
            freq_scan=key[1],
            scan_iter=key[2],
            scan_id="test-scan",
        ),
        spr=np.ones((2, 4)),
        cal=np.ones((2, 4)),
        mpr=np.ones(4),
        get_loaded_seconds=lambda: 2,
    )

    observation._update_integrated_data_arrays(scan)

    assert observation.int_data_arrays[key]["secs"] == 2
    assert observation.int_data_arrays[key]["scans"] == 1


def test_update_integrated_data_arrays_excludes_flagged_channels():
    observation = Observation.__new__(Observation)
    observation.obs_model = SimpleNamespace(obs_id="test-observation")
    key = (2, 0, 1)
    observation.int_data_arrays = {
        key: {
            "int_spr": np.zeros(3),
            "int_mpr": np.zeros(3),
            "int_tpw": [],
            "secs": 0.0,
            "scans": 0,
        }
    }
    cal_flags = empty_channel_flags((2, 3))
    cal_flags[:, 1] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    mpr_flags = empty_channel_flags(3)
    mpr_flags[1] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    scan = SimpleNamespace(
        scan_model=SimpleNamespace(
            tgt_idx=key[0],
            freq_scan=key[1],
            scan_iter=key[2],
            scan_id="test-scan",
        ),
        spr=np.ones((2, 3)),
        cal=np.array([[1.0, 100.0, 3.0], [4.0, 200.0, 6.0]]),
        cal_flags=cal_flags,
        mpr=np.array([2.5, 150.0, 4.5]),
        mpr_flags=mpr_flags,
        get_loaded_seconds=lambda: 2,
    )

    observation._update_integrated_data_arrays(scan)

    np.testing.assert_allclose(observation.int_data_arrays[key]["int_mpr"], [2.5, 0.0, 4.5])
    assert observation.int_data_arrays[key]["int_tpw"] == [4.0, 10.0]


def test_pipeline_description_supports_enum_and_string_step_names():
    observation = Observation.__new__(Observation)
    observation.obs_model = SimpleNamespace(
        obs_id="test-observation",
        target_scans=[
            SimpleNamespace(scans=[SimpleNamespace(dig_id="dig001")]),
        ]
    )
    observation.pipeline_factory = ProcessingPipelineFactory(
        PipelineConfig(
            steps_map={
                "default": [
                    StepConfig(step=StepType.DC_SPIKE, params={"context": "spr"}),
                    StepConfig(
                        step="BANDPASS_FILTER",
                        params={"context": "cal", "ranges_pct": [[20, 80]]},
                    ),
                ]
            }
        )
    )

    description = observation.describe_processing_pipeline_factory()

    assert "DC_SPIKE" in description
    assert "BANDPASS_FILTER" in description
