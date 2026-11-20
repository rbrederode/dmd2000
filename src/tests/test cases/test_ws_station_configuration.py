"""WS selects one station definition, preserving live app state during configuration reloads."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from models.comms import CommunicationStatus
from models.ws import WeatherStation as StationConfig, WeatherStationList, WeatherStationModel, WeatherStationDriverType, WeatherDriverConfig
from ws.drivers.modbus import ModbusConfig
from ws.drivers.ads1115 import ADS1115Config
from ws.ws import WeatherStation
from util.xbase import XAPIValidationFailed


@pytest.fixture
def station(tmp_path):
    ws = WeatherStation.__new__(WeatherStation)
    ws.stop = lambda: None
    ws.ws_model = WeatherStationModel(id='ws002', driver_failures=7)
    ws.app_model = ws.ws_model.app
    ws.ws_model.tm_connected = CommunicationStatus.ESTABLISHED
    ws.ws_model.dm_connected = CommunicationStatus.ESTABLISHED
    ws.weather_driver = Mock()
    ws._poll_deadline = None
    ws._weather_available = True
    args = SimpleNamespace(profile='test', entity_id='ws002', sim=None)
    ws.get_args = lambda: args
    ws._get_profile_config_dir = lambda: str(tmp_path)
    entries = [StationConfig(ws_id='ws001', location='Other', sim_mode='windy'),
               StationConfig(ws_id='ws002', location='Selected', driver_type=WeatherStationDriverType.MODBUS,
                             driver_config=ModbusConfig(port='/dev/selected', driver_poll_period=250), sim_mode='calm')]
    WeatherStationList(weather_stations=entries).save_to_disk(output_dir=tmp_path, filename='WeatherStationList.json')
    # No part of station loading may consult a legacy app-model file.
    (tmp_path/'WeatherStationModel.json').write_text('not JSON')
    return ws, args, tmp_path


def test_resync_selects_exact_station_and_preserves_runtime_app_connections_and_failures(station, monkeypatch):
    ws, args, path = station
    original_app, original_model, old_driver = ws.ws_model.app, ws.ws_model, ws.weather_driver
    factory = Mock()
    monkeypatch.setattr('ws.ws.create_ws_driver', factory)
    action = ws.process_resync()
    assert ws.ws_model is original_model and ws.ws_model.app is original_app
    assert ws.ws_model.id == 'ws002' and ws.ws_model.driver_config.port == '/dev/selected'
    assert ws.ws_model.driver_config.driver_poll_period == 250
    assert ws.ws_model.sim_mode == 'calm' and ws.weather_driver is None
    assert ws.ws_model.driver_failures == 7
    assert ws.ws_model.dm_connected == ws.ws_model.tm_connected == CommunicationStatus.ESTABLISHED
    old_driver.close.assert_called_once(); factory.assert_not_called()
    assert action.timer_actions[0].timer_action in (249, 250)
    serialized = ws.ws_model.to_dict()
    assert 'driver_poll_period' not in serialized
    assert serialized['driver_config']['driver_poll_period'] == 250
    assert WeatherStationModel.from_dict(serialized).driver_failures == 7


@pytest.mark.parametrize('identifier', ['<undefined>', '', 'missing'])
def test_missing_or_unspecified_station_never_replaces_active_driver(station, identifier):
    ws, args, path = station
    args.entity_id = identifier
    before, old = ws.ws_model.to_dict(), ws.weather_driver
    with pytest.raises(XAPIValidationFailed):
        ws.process_resync()
    assert ws.ws_model.to_dict() == before and ws.weather_driver is old
    old.close.assert_not_called()


@pytest.mark.parametrize('invalid', ['duplicate', 'poll', 'driver', 'sim'])
def test_invalid_config_does_not_mutate_live_state_or_close_driver(station, invalid):
    ws, _, path = station
    file = path/'WeatherStationList.json'
    data = json.loads(file.read_text())
    selected = data['weather_stations'][1]
    if invalid == 'duplicate': data['weather_stations'].append(selected)
    if invalid == 'poll': selected['driver_config']['driver_poll_period'] = 0
    if invalid == 'driver': selected['driver_config'] = ADS1115Config().to_dict()
    if invalid == 'sim': selected['sim_mode'] = 'typhoon'
    file.write_text(json.dumps(data))
    before, old = ws.ws_model.to_dict(), ws.weather_driver
    with pytest.raises(XAPIValidationFailed): ws.process_resync()
    assert ws.ws_model.to_dict() == before and ws.weather_driver is old
    old.close.assert_not_called()


def test_launch_sim_override_opens_only_the_selected_physical_driver(station, monkeypatch):
    ws, args, _ = station
    args.sim = 'off'
    replacement = Mock(); replacement.get_poll_interval_ms.return_value = 250
    factory = Mock(return_value=replacement)
    monkeypatch.setattr('ws.ws.create_ws_driver', factory)
    old = ws.weather_driver
    ws.process_resync()
    old.close.assert_called_once()
    assert ws.weather_driver is replacement and ws.ws_model.sim_mode == 'off'
    assert factory.call_args.args[0].driver_config.port == '/dev/selected'
    args.sim = 'stormy'
    ws.process_resync()
    replacement.close.assert_called_once()
    assert ws.weather_driver is None and ws.ws_model.sim_mode == 'stormy'
    assert factory.call_count == 1


def test_simulation_without_a_physical_driver_retains_its_own_poll_interval(station, monkeypatch):
    ws, args, path = station
    WeatherStationList(weather_stations=[StationConfig(ws_id='ws002', location='Simulation', sim_mode='calm',
        driver_config=WeatherDriverConfig(driver_poll_period=500))]).save_to_disk(output_dir=path, filename='WeatherStationList.json')
    monkeypatch.setattr('ws.ws.create_ws_driver', Mock(side_effect=AssertionError('physical driver opened')))
    ws.process_resync()
    assert ws.ws_model.driver_config.driver_poll_period == 500
    args.sim='off'
    with pytest.raises(XAPIValidationFailed, match='physical driver'): ws.process_resync()
    assert ws.ws_model.sim_mode == 'calm'


def test_migrated_profiles_have_valid_station_configs_and_explicit_ws_launch_ids():
    root = Path(__file__).resolve().parents[2]/'config'
    for path in root.glob('*/WeatherStationList.json'):
        model = WeatherStationList.load_from_disk(input_dir=path.parent, filename=path.name)
        for entry in model.weather_stations:
            assert entry.driver_config.driver_poll_period > 0
        assert not path.with_name('WeatherStationModel.json').exists()
    for path in root.glob('*/LaunchConfig.json'):
        for launch in json.loads(path.read_text()).get('launches', []):
            if launch['app_name'] == 'ws':
                assert launch['args'][launch['args'].index('--entity_id')+1] == launch['entity_id']


def test_ws_commands_use_default_endpoint_or_explicit_override_not_legacy_file(tmp_path):
    from web.cmd_handler import CommandHandler
    (tmp_path/'WeatherStationModel.json').write_text('invalid legacy file')
    assert CommandHandler(profile_dir=tmp_path)._resolve_endpoint('ws') == ('ws','127.0.0.1',60005)
    handler = CommandHandler(profile_dir=tmp_path, endpoint_overrides={'ws':{'host':'sensor-host','port':60105}})
    assert handler._resolve_endpoint('ws') == ('ws','sensor-host',60105)
