import pytest

from sdk.models.app import AppModel
from sdk.models.dig import DigitiserModel
from sdk.models.telescope import TelescopeModel
from sdk.models.telescope import TelescopeConfig
from sdk.util.xbase import XAPIValidationFailed


def test_get_app_model_by_name_returns_fixed_application():
    telmodel = TelescopeModel()

    app = telmodel.get_app_model_by_name("DM")

    assert app is telmodel.dsh_mgr.app
    assert app.app_cmd_port == 60003


def test_observatory_config_loads_attributes_and_reaches_web_snapshot(tmp_path):
    import json
    from sdk.models.base import BaseModel
    from web.model_snapshot import telescope_snapshot
    data = dict(_type='TelescopeConfig', name='Test Observatory', latitude=53.234123, longitude=-2.305485, height=77.)
    (tmp_path / 'telescope.json').write_text(json.dumps(data))
    config = TelescopeConfig.load_from_disk(input_dir=tmp_path, filename='telescope.json')
    assert config.longitude == data['longitude']
    assert TelescopeConfig.from_dict(config.to_dict()).to_dict() == config.to_dict()
    assert BaseModel._deserialise(config.to_dict()).to_dict() == config.to_dict()
    telescope = TelescopeModel()
    telescope.config = config
    payload = telescope_snapshot(telescope)
    assert payload['telescope_model']['config'] == config.to_dict()
    config.save_to_disk(output_dir=tmp_path, filename='saved.json')
    assert set(json.loads((tmp_path / 'saved.json').read_text())) == set(data)
    assert TelescopeConfig.from_dict.__func__ is BaseModel.from_dict.__func__
    assert TelescopeConfig.load_from_disk.__func__ is BaseModel.load_from_disk.__func__
    assert TelescopeConfig.load_from_disk(input_dir=tmp_path, filename='saved.json').to_dict() == config.to_dict()


@pytest.mark.parametrize('changes', [
    {'latitude': 91}, {'latitude': -91}, {'longitude': 181}, {'longitude': -181},
    {'latitude': True}, {'longitude': float('nan')}, {'height': float('inf')},
    {'longitude': '0'}, {'name': ''}, {'name': '   '}, {'sdp': {}}, {'_type': 'TelescopeModel'},
])
def test_observatory_config_rejects_invalid_attributes(changes):
    with pytest.raises(XAPIValidationFailed):
        TelescopeConfig(**changes)


def test_unconfigured_observatory_has_no_implicit_location():
    assert TelescopeModel().config.longitude is None


def test_get_app_model_by_name_supports_digitiser_id():
    telmodel = TelescopeModel()
    digitiser = DigitiserModel(
        dig_id="dig007",
        app=AppModel(app_name="dig", app_cmd_host="dig-host", app_cmd_port=60107),
    )
    telmodel.dig_store.dig_list = [digitiser]

    app = telmodel.get_app_model_by_name("DIG007")

    assert app is digitiser.app
    assert (app.app_cmd_host, app.app_cmd_port) == ("dig-host", 60107)


@pytest.mark.parametrize("app_name", ["DIG", "telmgr", "dshmgr", "wtr_stn"])
def test_get_app_model_by_name_rejects_names_outside_ui_contract(app_name):
    telmodel = TelescopeModel()
    telmodel.dig_store.dig_list = [
        DigitiserModel(dig_id="dig001"),
        DigitiserModel(dig_id="dig002"),
    ]

    with pytest.raises(ValueError, match="Unknown application name"):
        telmodel.get_app_model_by_name(app_name)


def test_application_timestamps_start_unset_and_survive_serialisation():
    from datetime import datetime, timezone
    telmodel = TelescopeModel()
    for model in [telmodel.tel_mgr, telmodel.dsh_mgr, telmodel.sdp,
                  telmodel.wtr_stn, telmodel.web_svr, DigitiserModel(dig_id="dig001")]:
        assert model.app.last_update is None
        restored = type(model).from_dict(model.to_dict())
        assert restored.app.last_update is None
    app = AppModel()
    assert AppModel.from_dict(app.to_dict()).last_update is None
    now = datetime.now(timezone.utc)
    app.last_update = now
    assert AppModel.from_dict(app.to_dict()).last_update == now
