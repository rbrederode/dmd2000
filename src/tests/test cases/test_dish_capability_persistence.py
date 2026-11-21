"""Capability inhibition survives controller failures and profile reloads."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dsh.drivers.driver import DishDriver
from dsh.dm import DM
from sdk.models.dsh import Capability, DishList, DishManagerModel, DishMode, DishModel, DriverType, PointingState
from sdk.models.ws import WeatherStationList
from sdk.util.xbase import XInvalidTransition, XSoftwareFailure
from web.dish_store import DishProfileStore


@pytest.fixture
def persisted_driver(tmp_path):
    model = DishModel(dsh_id='dish001', capability=Capability.OPERATE_FULL,
                      mode=DishMode.OPERATE, pointing_state=PointingState.READY,
                      driver_type=DriverType.DRIFT)
    DishList(dish_list=[model, DishModel(dsh_id='dish002')]).save_to_disk(str(tmp_path))
    path = tmp_path / 'DishList.json'
    raw = json.loads(path.read_text())
    raw['custom_metadata'] = {'preserve': True}
    path.write_text(json.dumps(raw))
    store = DishProfileStore(tmp_path)
    driver = DishDriver(model)
    driver.persist_capability = lambda value: store.set_capability(model.dsh_id, value)
    driver.stop = Mock()
    return driver, path, store


def reload_dish(path):
    return DishList.load_from_disk(str(path.parent), path.name).dish_list[0]


def test_capability_is_durable_before_hardware_stop(persisted_driver):
    driver, path, _ = persisted_driver
    before = json.loads(path.read_text())

    def stop():
        assert driver.get_capability() == Capability.STANDBY
        assert reload_dish(path).capability == Capability.STANDBY

    driver.stop.side_effect = stop
    driver.set_dish_capability(Capability.STANDBY)

    driver.stop.assert_called_once()
    after = json.loads(path.read_text())
    assert after['custom_metadata'] == before['custom_metadata']
    assert after['dish_list'][1] == before['dish_list'][1]
    assert reload_dish(path).mode == DishMode.OPERATE  # Runtime mode is not written.


def test_emergency_stop_persists_even_when_controller_fails(persisted_driver):
    driver, path, _ = persisted_driver
    driver.stop.side_effect = RuntimeError('controller unavailable')

    with pytest.raises(RuntimeError, match='controller unavailable'):
        driver.emergency_stop()

    assert reload_dish(path).capability == Capability.STANDBY
    assert driver.dsh_model.capability == Capability.STANDBY
    assert driver.dsh_model.mode == DishMode.UNKNOWN
    assert driver.dsh_model.target is None
    assert driver.dsh_model.desired_altaz is None


def test_maintenance_persists_standby_before_stowing(persisted_driver):
    driver, path, _ = persisted_driver
    calls = []

    def stow(**kwargs):
        assert reload_dish(path).capability == Capability.STANDBY
        calls.append('stow')

    driver._get_stow_altaz = lambda: (45.0, 60.0)
    driver._set_stow_mode = stow
    driver._set_maintenance_mode = lambda: calls.append('maintenance')
    driver.set_maintenance_mode()

    assert calls == ['stow', 'maintenance']
    assert driver.dsh_model.mode == DishMode.MAINTENANCE
    assert reload_dish(path).capability == Capability.STANDBY
    with pytest.raises(XInvalidTransition):
        driver.set_dish_mode(DishMode.OPERATE)
    with pytest.raises(XInvalidTransition):
        driver.set_stow_mode()


def test_persistence_failure_still_inhibits_and_stops(persisted_driver):
    driver, _, _ = persisted_driver
    driver.persist_capability = Mock(side_effect=OSError('disk full'))

    with pytest.raises(XSoftwareFailure, match='disk full'):
        driver.emergency_stop()

    assert driver.dsh_model.capability == Capability.STANDBY
    assert driver.dsh_model.mode == DishMode.UNKNOWN
    driver.stop.assert_called_once()


def test_cannot_enable_operation_if_persistence_fails(persisted_driver):
    driver, path, _ = persisted_driver
    driver.set_dish_capability(Capability.CONFIGURING)
    driver.stop.reset_mock()
    driver.persist_capability = Mock(side_effect=OSError('disk full'))

    with pytest.raises(XSoftwareFailure, match='disk full'):
        driver.set_dish_capability(Capability.OPERATE_FULL)

    assert driver.dsh_model.capability == Capability.CONFIGURING
    assert reload_dish(path).capability == Capability.CONFIGURING
    driver.stop.assert_not_called()


def test_invalid_transition_does_not_change_disk(persisted_driver):
    driver, path, _ = persisted_driver
    before = path.read_bytes()
    with pytest.raises(XInvalidTransition):
        driver.set_dish_capability(Capability.UNAVAILABLE)
    assert path.read_bytes() == before
    driver.stop.assert_not_called()


def test_persisting_capability_preserves_new_profile_edits(persisted_driver):
    driver, path, store = persisted_driver
    store.save({'short_desc': 'Edited while DM is running'}, dish_id='dish001')
    driver.set_dish_capability(Capability.STANDBY)
    assert reload_dish(path).short_desc == 'Edited while DM is running'


@pytest.mark.parametrize('capability', list(Capability))
def test_all_capabilities_are_persisted(persisted_driver, capability):
    driver, path, _ = persisted_driver
    driver.set_dish_capability(Capability.UNKNOWN)
    driver.set_dish_capability(capability)
    assert reload_dish(path).capability == capability


def test_repeated_emergency_stop_still_calls_hardware(persisted_driver):
    driver, path, _ = persisted_driver
    driver.emergency_stop()
    driver.emergency_stop()
    assert driver.stop.call_count == 2
    assert reload_dish(path).capability == Capability.STANDBY


def test_manager_restart_and_resync_restore_persisted_capability(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / 'config' / 'test'
    DishList(dish_list=[DishModel(dsh_id='dish001', capability=Capability.OPERATE_FULL)]).save_to_disk(str(profile))

    def manager():
        dm = DM.__new__(DM)
        dm.stop = lambda: None
        dm.dm_model = DishManagerModel()
        dm.dish_drivers = {}
        dm.dish_locks = {}
        dm.trace = None
        dm.get_args = lambda: SimpleNamespace(profile='test')
        dm._load_weather_station_config = lambda **kwargs: WeatherStationList()
        dm._instantiate_dish_driver = lambda dish: DishDriver(dish)
        dm.process_resync()
        return dm

    first = manager()
    driver = first.dish_drivers['dish001']
    driver.stop = Mock()
    first.process_stop_command({'value': 'dish001'})
    first.process_resync()
    assert first.dish_drivers['dish001'].get_capability() == Capability.STANDBY
    restarted = manager()
    assert restarted.dish_drivers['dish001'].get_capability() == Capability.STANDBY


def test_maintenance_stays_inhibited_if_stowing_fails(persisted_driver):
    driver, path, _ = persisted_driver
    driver._get_stow_altaz = lambda: (45.0, 60.0)
    driver._set_stow_mode = Mock(side_effect=RuntimeError('stow failed'))
    with pytest.raises(RuntimeError, match='stow failed'):
        driver.set_maintenance_mode()
    assert reload_dish(path).capability == Capability.STANDBY
    assert driver.dsh_model.capability == Capability.STANDBY


def test_maintenance_does_not_stow_when_persistence_fails(persisted_driver):
    driver, _, _ = persisted_driver
    driver.persist_capability = Mock(side_effect=OSError('disk full'))
    driver._stow = Mock()
    with pytest.raises(XSoftwareFailure, match='disk full'):
        driver.set_maintenance_mode()
    driver._stow.assert_not_called()
    driver.stop.assert_called_once()
