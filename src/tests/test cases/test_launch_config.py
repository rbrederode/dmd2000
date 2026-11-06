import pytest

from models.launch import LaunchConfigModel, LaunchModel
from util.launch import resolve_entity_ids


def test_launch_config_loads_jodrell_profile():
    config = LaunchConfigModel.load_from_disk(input_dir="src/config/jodrell", filename="LaunchConfig.json")

    assert config.profile == "jodrell"
    assert {launch.entity_id for launch in config.launches} == {"dig001", "dm001", "sdp001", "tm001"}

    dig = config.get_launch_by_entity_id("dig001")

    assert isinstance(dig, LaunchModel)
    assert dig.app_name == "dig"
    assert dig.env["GPIOZERO_PIN_FACTORY"] == "mock"
    assert dig.program == "python"
    assert dig.args[:5] == ["-m", "dig.dig", "--profile", "jodrell", "--entity_id"]


@pytest.mark.parametrize("profile", ["local", "alston", "gqrx", "jodrell", "patio", "rtlstream", "solar"])
def test_app_launches_use_modules_so_src_packages_are_importable(profile):
    config = LaunchConfigModel.load_from_disk(
        input_dir=f"src/config/{profile}", filename="LaunchConfig.json",
    )
    modules = {"dm": "dsh.dm", "dig": "dig.dig", "tm": "tm.tm", "sdp": "sdp.sdp", "ws": "ws.ws"}
    for launch in config.launches:
        if launch.app_name in modules:
            assert launch.args[:2] == ["-m", modules[launch.app_name]]


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


def test_launch_storage_round_trip_and_older_profiles():
    config = LaunchConfigModel(profile='test', scan_store_dir='~/samples/test', launches=[LaunchModel()])
    restored = LaunchConfigModel.from_dict(config.to_dict())
    assert restored.scan_store_dir == '~/samples/test'
    assert restored.launches[0].entity_id == 'app001'
    assert LaunchConfigModel.from_dict({'profile': 'old', 'launches': []}).scan_store_dir is None


@pytest.mark.parametrize('profile', ['local', 'alston', 'gqrx', 'jodrell', 'patio', 'rtlstream', 'solar', 'rotation'])
def test_profiles_share_storage_instead_of_sdp_launch_argument(profile):
    from models.launch import load_scan_store_dir
    config = LaunchConfigModel.load_from_disk(input_dir=f'src/config/{profile}', filename='LaunchConfig.json')
    assert load_scan_store_dir(profile) == config.scan_store_dir
    assert config.scan_store_dir.startswith('~/samples')
    for launch in config.launches:
        if launch.app_name == 'sdp':
            assert '--scan_store_dir' not in launch.args


@pytest.mark.parametrize('override', [None, '/custom/storage'])
def test_sdp_reads_profile_default_with_cli_override(monkeypatch, override):
    import argparse
    import sys
    from sdp.sdp import SDP
    argv = ['sdp', '--profile', 'local']
    if override is not None:
        argv += ['--scan_store_dir', override]
    monkeypatch.setattr(sys, 'argv', argv)
    sdp = SDP.__new__(SDP)
    sdp.stop = lambda: None
    parser = argparse.ArgumentParser()
    sdp.add_args(parser)
    assert parser.parse_args().scan_store_dir == (override or '~/samples/local')


@pytest.mark.parametrize('override', [None, '/custom/web-storage'])
def test_web_reads_profile_default_with_cli_override(monkeypatch, override):
    import argparse
    import sys
    from pathlib import Path
    from web.web import WebServer
    from models.web import WebServerModel
    argv = ['web', '--profile', 'local']
    if override is not None:
        argv += ['--scan_store_dir', override]
    monkeypatch.setattr(sys, 'argv', argv)
    web = WebServer.__new__(WebServer)
    web.stop = lambda: None
    web.arg_parser = argparse.ArgumentParser()
    web.add_args(web.arg_parser)
    web.profile_dir = Path('src/config/local')
    web.web_model = WebServerModel()
    web.app_model = web.web_model.app
    web._load_configuration()
    assert web.scan_store_dir == (override or '~/samples/local')
