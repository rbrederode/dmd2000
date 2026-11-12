import pytest

from models.app import AppModel
from models.dig import DigitiserModel
from models.telescope import TelescopeModel


def test_get_app_model_by_name_returns_fixed_application():
    telmodel = TelescopeModel()

    app = telmodel.get_app_model_by_name("DM")

    assert app is telmodel.dsh_mgr.app
    assert app.app_cmd_port == 60003


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
