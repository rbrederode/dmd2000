from types import SimpleNamespace
from unittest import mock

from models.pipeline import PipelineConfig
from sdp.sdp import SDP


def _loader_owner():
    errors = []
    owner = SimpleNamespace(
        sdp_model=SimpleNamespace(pipeline_config=PipelineConfig()),
        set_last_err=lambda message: errors.append(message) or message,
    )
    return owner, errors


def test_pipeline_loader_uses_steps_map_from_profile():
    owner, errors = _loader_owner()

    config = SDP._load_pipeline_config(
        owner,
        input_dir="src/config/solar",
        filename="PipelineConfig.json",
    )

    assert sum(len(steps) for steps in config.steps_map.values()) == 5
    assert errors == []


def test_pipeline_loader_reports_empty_steps_map():
    owner, errors = _loader_owner()
    empty_config = PipelineConfig(steps_map={})

    with mock.patch.object(PipelineConfig, "load_from_disk", return_value=empty_config):
        config = SDP._load_pipeline_config(
            owner,
            input_dir="unused",
            filename="PipelineConfig.json",
        )

    assert config is empty_config
    assert errors == [
        "Science Data Processor did not find any configured pipeline processing steps "
        "in directory unused file PipelineConfig.json"
    ]

