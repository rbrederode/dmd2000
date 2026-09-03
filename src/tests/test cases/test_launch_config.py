import pytest

from models.launch import LaunchConfigModel, LaunchModel
from util.launch import resolve_entity_ids


def test_launch_config_loads_jodrell_profile():
    config = LaunchConfigModel.load_from_disk(input_dir="src/config/jodrell", filename="LaunchConfig.json")

    assert config.profile == "jodrell"
    assert len(config.launches) == 6

    dig = config.get_launch_by_entity_id("dig001")

    assert isinstance(dig, LaunchModel)
    assert dig.app_name == "dig"
    assert dig.env["GPIOZERO_PIN_FACTORY"] == "mock"
    assert dig.program == "python"
    assert dig.args[:4] == ["dig/dig.py", "--profile", "jodrell", "--entity_id"]


def test_launch_config_returns_none_for_unknown_entity():
    config = LaunchConfigModel.load_from_disk(input_dir="src/config/local", filename="LaunchConfig.json")

    assert config.get_launch_by_entity_id("missing001") is None


def test_resolve_entity_ids_supports_single_and_multiple_inputs():
    assert resolve_entity_ids(["dig001"], None) == ["dig001"]
    assert resolve_entity_ids(["dig001", "tm001"], None) == ["dig001", "tm001"]
    assert resolve_entity_ids(["sdp001"], ["dig001", "tm001"]) == ["dig001", "tm001", "sdp001"]


def test_resolve_entity_ids_requires_at_least_one_entity():
    with pytest.raises(ValueError):
        resolve_entity_ids([], None)


@pytest.mark.parametrize("profile", ["gqrx", "jodrell", "patio", "rtlstream"])
def test_tm_launch_config_does_not_load_observation_file(profile):
    config = LaunchConfigModel.load_from_disk(
        input_dir=f"src/config/{profile}",
        filename="LaunchConfig.json",
    )
    tm = config.get_launch_by_entity_id("tm001")

    assert tm is not None
    assert "-o" not in tm.args
    assert "--observation_file" not in tm.args
