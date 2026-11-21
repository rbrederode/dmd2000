"""UI driver configuration is independent of live TM state and uses normal BaseModel loading."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sdk.models.base import BaseModel
from sdk.models.telescope import TelescopeModel
from sdk.models.ui import UIDriver, UIDriverList, UIDriverType
from tm.tm import TelescopeManager
from sdk.util.xbase import XAPIValidationFailed, XSoftwareFailure


def driver(description='Console', **changes):
    return UIDriver(type=UIDriverType.CUSTOM_UI, short_desc=description, config={'status_path':'unused.json'}, **changes)


@pytest.fixture
def tm(tmp_path, monkeypatch):
    model = TelescopeManager.__new__(TelescopeManager)
    model.stop = lambda: None
    model.telmodel = TelescopeModel()
    model.app_model = model.telmodel.tel_mgr.app
    model.queue = Queue()
    model.get_args = lambda: SimpleNamespace(profile='test')
    directory = tmp_path/'config'/'test';directory.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(type(model.telmodel.dig_store), 'load_from_disk', staticmethod(lambda **kwargs: model.telmodel.dig_store))
    return model, directory


def test_inherited_config_loading_and_runtime_serialisation(tmp_path):
    definition = driver(poll_period=12)
    definition.instance = Mock()
    before = definition.last_update
    config = UIDriverList(ui_drivers=[definition])
    config.save_to_disk(output_dir=tmp_path)
    raw = json.loads((tmp_path/'UIDriverList.json').read_text())
    assert set(raw) == {'_type','list_id','ui_drivers'}
    assert set(raw['ui_drivers'][0]) == {'_type','type','short_desc','config','poll_period'}
    loaded = UIDriverList.load_from_disk(input_dir=tmp_path,filename='UIDriverList.json')
    assert loaded.ui_drivers[0].instance is None
    assert loaded.ui_drivers[0].last_update >= before
    assert loaded.to_dict() == raw
    assert UIDriverList.from_dict(raw).to_dict() == raw
    assert BaseModel._deserialise(raw).to_dict() == raw
    assert UIDriverList.from_dict.__func__ is BaseModel.from_dict.__func__
    assert UIDriverList.load_from_disk.__func__ is BaseModel.load_from_disk.__func__
    # Runtime snapshots can still describe the latest publish time.
    assert 'last_update' in definition.to_dict() and 'instance' not in definition.to_dict()


@pytest.mark.parametrize('period', [None,0,-1,True,1.5])
def test_invalid_polling_period_is_rejected(period):
    with pytest.raises(XAPIValidationFailed):
        driver(poll_period=period)


def test_resync_replaces_only_integrations_and_preserves_tm_runtime_state(tm):
    model, directory = tm
    old = driver();instance=Mock();old.instance=instance
    model.telmodel.tel_mgr.ui_drivers=[old]
    app, allocations = model.telmodel.tel_mgr.app, model.telmodel.tel_mgr.allocations
    UIDriverList(ui_drivers=[driver('New console')]).save_to_disk(output_dir=directory)
    model.process_resync()
    instance.close.assert_called_once()
    assert old.instance is None
    assert model.telmodel.tel_mgr.app is app and model.telmodel.tel_mgr.allocations is allocations
    assert model.telmodel.tel_mgr.ui_drivers[0].short_desc == 'New console'
    assert model.telmodel.tel_mgr.ui_drivers[0].instance is None
    instance.reset_mock()
    (directory/'UIDriverList.json').write_text('{invalid JSON')
    current=model.telmodel.tel_mgr.ui_drivers
    current[0].instance=instance
    model.process_resync()
    assert model.telmodel.tel_mgr.ui_drivers is current
    instance.close.assert_not_called()
    (directory/'UIDriverList.json').unlink()
    model.process_resync()
    assert model.telmodel.tel_mgr.ui_drivers is current


def test_initialisation_failures_retry_after_resync_without_disabling_same_description(tm):
    model, directory = tm
    definitions=UIDriverList(ui_drivers=[driver('Duplicate'),driver('Duplicate')])
    definitions.save_to_disk(output_dir=directory)
    model.process_resync()
    first=Mock();second=Mock();replacement=Mock()
    model._create_ui_driver=Mock(side_effect=[XSoftwareFailure('temporary failure'),first,second,replacement])
    model._poll_ui_drivers()
    assert model._create_ui_driver.call_count==2
    assert model.telmodel.tel_mgr.ui_drivers[1].instance is first
    model._poll_ui_drivers()
    assert model._create_ui_driver.call_count==2  # Failed instance is not retried each second.
    model.process_resync()
    first.close.assert_called_once()
    assert not model._disabled_ui_drivers
    model._poll_ui_drivers()
    assert model._create_ui_driver.call_count==4
    assert model.telmodel.tel_mgr.ui_drivers[0].instance is second
    model.close_ui_drivers();model.close_ui_drivers()
    second.close.assert_called_once();replacement.close.assert_called_once()


def test_model_publication_cadence_and_observation_input_remain_in_memory(tm):
    model, directory = tm
    UIDriverList(ui_drivers=[driver(poll_period=10)]).save_to_disk(output_dir=directory)
    model.process_resync()
    path=directory/'UIDriverList.json';before=path.read_bytes()
    obs_config={'_type':'ObsList','obs_list':[]}
    instance=SimpleNamespace(publish=Mock(),read_config=Mock(return_value=obs_config),close=Mock())
    model._create_ui_driver=Mock(return_value=instance)
    now=datetime.now(timezone.utc)
    model._poll_ui_drivers(now)
    instance.publish.assert_not_called()
    model._poll_ui_drivers(now+timedelta(seconds=1))
    assert instance.publish.call_count==7
    assert {call.args[0]['_type'] for call in instance.publish.call_args_list} == {
        'DigitiserList','DishManagerModel','ScienceDataProcessorModel','ODAModel',
        'WeatherStationModel','TelescopeManagerModel','WebServerModel'}
    assert model.queue.qsize()==1
    assert model.queue.get().new_config==obs_config
    model._poll_ui_drivers(now+timedelta(seconds=2))
    assert instance.publish.call_count==7
    model._poll_ui_drivers(now+timedelta(seconds=11))
    assert instance.publish.call_count==14 and model.queue.empty()
    assert path.read_bytes()==before
    UIDriverList().save_to_disk(output_dir=directory)
    model.process_resync()
    instance.close.assert_called_once()
    model._poll_ui_drivers(now+timedelta(seconds=30))
    assert instance.publish.call_count==14


def test_google_sheets_cleanup_and_tm_command_defaults_need_no_old_config(tmp_path):
    from ui.drivers.gsheets.gsheets_driver import GoogleSheetsDriver
    from web.cmd_handler import CommandHandler
    instance=GoogleSheetsDriver.__new__(GoogleSheetsDriver)
    service=Mock();instance.service=service
    instance.close();instance.close();service.close.assert_called_once()
    (tmp_path/'TelescopeManagerModel.json').write_text('invalid obsolete file')
    assert CommandHandler(profile_dir=tmp_path)._resolve_endpoint('tm')==('tm','127.0.0.1',60002)
    assert CommandHandler(profile_dir=tmp_path,endpoint_overrides={'tm':{'host':'tm-host','port':60102}})._resolve_endpoint('tm')==('tm','tm-host',60102)


def test_migrated_profiles_are_config_only():
    root=Path(__file__).resolve().parents[2]/'config'
    assert not list(root.glob('*/TelescopeManagerModel.json'))
    files=list(root.glob('*/UIDriverList.json'));assert len(files)==11
    for path in files:
        raw=json.loads(path.read_text())
        assert UIDriverList.load_from_disk(input_dir=path.parent,filename=path.name).to_dict()==raw
        assert all('instance' not in item and 'last_update' not in item for item in raw['ui_drivers'])
