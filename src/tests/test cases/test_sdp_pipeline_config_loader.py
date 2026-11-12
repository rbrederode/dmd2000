from types import SimpleNamespace
from unittest import mock
from pathlib import Path

import pytest

from models.pipeline import PipelineConfig
from sdp.sdp import SDP


def _loader_owner():
    errors = []
    owner = SimpleNamespace(
        sdp_model=SimpleNamespace(pipeline_config=PipelineConfig()),
        set_last_err=lambda message: errors.append(message) or message,
    )
    return owner, errors


@pytest.mark.parametrize("profile", sorted(Path("src/config").glob("*/PipelineConfig.json")), ids=lambda p: p.parent.name)
def test_pipeline_loader_uses_steps_map_from_profile(profile):
    owner, errors = _loader_owner()

    config = SDP._load_pipeline_config(
        owner,
        input_dir=str(profile.parent),
        filename="PipelineConfig.json",
    )

    for steps in config.steps_map.values():
        baselines = [step for step in steps if step.get_step_key() == "baseline"]
        assert len(baselines) == 1
        baseline = baselines[0]
        assert baseline.params == {"context": "cal", "method": "flatten", "enabled": True,
                                   "ranges_pct": [[0, 10], [90, 100]]}
        index = steps.index(baseline)
        assert all(step.params.get("context") != "cal" for step in steps[index+1:])
        assert all(step.get_step_key() != "qa" for step in steps[:index])
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

