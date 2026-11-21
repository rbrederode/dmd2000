from types import SimpleNamespace
from unittest import mock
from pathlib import Path
import json

import pytest

from sdk.models.pipeline import PipelineConfig
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

    saved = json.loads(profile.read_text())
    assert set(config.steps_map) == set(saved['steps_map'])
    for name, steps in config.steps_map.items():
        baselines = [step for step in steps if step.get_step_key() == "baseline"]
        assert len(baselines) == 1
        baseline = baselines[0]
        saved_baselines = [step['params'] for step in saved['steps_map'][name]
                           if (step['step'].get('value') if isinstance(step['step'], dict)
                               else step['step']) == 'BASELINE']
        # Profiles can intentionally disable baseline correction. Loading must
        # preserve the saved parameters rather than force correction on.
        assert len(saved_baselines) == 1
        assert baseline.params == saved_baselines[0]
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

