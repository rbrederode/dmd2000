from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from api import protocol as dmd_protocol
from api import tm_dm, tm_sdp
from models.dsh import DishModel
from models.scan import ScanModel, ScanState
from tm.tm import TelescopeManager


def test_scan_complete_requests_start_and_end_pointing_with_scan_id_echo(monkeypatch):
    read_start = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
    read_end = read_start + timedelta(seconds=60)
    completed_scan = ScanModel(
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=0,
        scan_iter=0,
        dig_id="dig001",
        read_start=read_start,
        read_end=read_end,
        duration=60,
        sample_rate=2_048_000.0,
        spectral_resolution=1024,
        center_freq=1_420_000_000.0,
        gain=20.0,
        status=ScanState.COMPLETE,
    )
    stored_scan = ScanModel(
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=0,
        scan_iter=0,
    )
    observation = SimpleNamespace(
        obs_id="obs001",
        dsh_id="dish001",
        get_target_scan_by_id=lambda scan_id: (
            stored_scan if scan_id == completed_scan.scan_id else None
        ),
    )

    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.app_model = SimpleNamespace(app_name="tm")
    manager.dm_api = tm_dm.TM_DM()
    manager.sdp_api = tm_sdp.TM_SDP()
    manager.telmodel = SimpleNamespace(
        sdp=SimpleNamespace(sdp_id="sdp001", last_update=None),
        tel_mgr=SimpleNamespace(app=SimpleNamespace(msg_timeout_ms=10_000)),
        oda=SimpleNamespace(
            obs_store=SimpleNamespace(
                get_obs_by_id=lambda obs_id: observation if obs_id == "obs001" else None
            )
        ),
        get_scan_store_dir=lambda: "/unused",
    )
    manager._apply_target_pec_to_scan = lambda obs, scan: False
    manager._apply_weather_summary_to_scan = lambda obs, scan: False
    manager.set_last_err = lambda message: message

    monkeypatch.setattr(ScanModel, "save_to_disk", lambda self, **kwargs: None)
    monkeypatch.setattr("tm.tm.util.gen_file_prefix", lambda **kwargs: "scan-meta")

    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_ADV,
        "action_code": dmd_protocol.ACTION_CODE_SET,
        "property": tm_sdp.PROPERTY_SCAN_COMPLETE,
        "status": dmd_protocol.STATUS_SUCCESS,
        "value": completed_scan.to_dict(),
    }
    api_msg = {
        "api_version": manager.sdp_api.get_api_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "from": "sdp",
        "to": "tm",
        "api_call": api_call,
    }

    action = manager.process_sdp_msg(None, api_msg, api_call, bytearray())

    pointing_requests = [
        message
        for message in action.msgs_to_remote
        if message.get_to_system() == "dm"
    ]
    assert len(pointing_requests) == 1

    request = pointing_requests[0]
    request_call = request.get_api_call()
    assert request.get_entity() == "dish001"
    assert request_call["action_code"] == dmd_protocol.ACTION_CODE_GET
    assert request_call["property"] == tm_dm.PROPERTY_POINTING
    assert request_call["value"] == [
        {"_type": "datetime", "value": read_start.isoformat()},
        {"_type": "datetime", "value": read_end.isoformat()},
    ]
    assert request.get_echo_data() == {"scan_id": completed_scan.scan_id}

    retry_timers = [
        timer
        for timer in action.timer_actions
        if timer.get_name().startswith("dish001_req_timer_retry:")
    ]
    assert len(retry_timers) == 1
    assert retry_timers[0].get_echo_data() is request

    # The outbound request also conforms to the TM-DM interface schema.
    manager.dm_api.validate(request.get_json_api_header())


def test_pointing_response_updates_and_saves_scan_metadata():
    read_start = datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc)
    read_end = read_start + timedelta(seconds=60)
    scan = ScanModel(
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=0,
        scan_iter=0,
        dig_id="dig001",
        read_start=read_start,
        read_end=read_end,
    )
    observation = SimpleNamespace(
        obs_id="obs001",
        get_target_scan_by_id=lambda scan_id: scan if scan_id == scan.scan_id else None,
    )
    older_scan = ScanModel(
        obs_id="older-observation",
        tgt_idx=0,
        freq_scan=0,
        scan_iter=0,
    )
    older_observation = SimpleNamespace(
        obs_id="older-observation",
        # Reproduce the former index-only lookup bug: this observation would
        # have captured obs001's response when iterated first.
        get_target_scan_by_id=lambda scan_id: (
            older_scan if scan_id.endswith("-0-0-0") else None
        ),
    )
    dish = DishModel(dsh_id="dish001")
    dish_manager = SimpleNamespace(
        get_dish_by_id=lambda dish_id: dish if dish_id == dish.dsh_id else None,
        last_update=None,
    )
    obs_store = SimpleNamespace(
        obs_list=[older_observation, observation],
        get_obs_by_id=lambda obs_id: observation if obs_id == observation.obs_id else None,
    )

    saved = []
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.telmodel = SimpleNamespace(
        dsh_mgr=dish_manager,
        oda=SimpleNamespace(obs_store=obs_store),
    )
    manager.set_last_err = lambda message: message
    manager._save_scan_metadata = lambda saved_scan: saved.append(saved_scan)

    start_recorded = read_start + timedelta(milliseconds=100)
    end_recorded = read_end - timedelta(milliseconds=200)
    pointing_data = [
        {
            "datetime": start_recorded.isoformat(),
            "requested_datetime": read_start.isoformat(),
            "offset_seconds": 0.1,
            "pointing_altaz": {"alt": 35.0, "az": 180.0},
        },
        {
            "datetime": end_recorded.isoformat(),
            "requested_datetime": read_end.isoformat(),
            "offset_seconds": -0.2,
            "pointing_altaz": {"alt": 36.0, "az": 181.0},
        },
    ]
    response_timestamp = datetime.now(timezone.utc).isoformat()
    api_msg = {
        "timestamp": response_timestamp,
        "entity": dish.dsh_id,
        "echo_data": {"scan_id": scan.scan_id},
    }
    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_RSP,
        "action_code": dmd_protocol.ACTION_CODE_GET,
        "property": tm_dm.PROPERTY_POINTING,
        "status": dmd_protocol.STATUS_SUCCESS,
        "value": pointing_data,
    }

    action = manager.process_dm_msg(None, api_msg, api_call, bytearray())

    assert saved == [scan]
    assert older_scan.pointing_refs == []
    assert scan.pointing_refs == [
        {
            "datetime": start_recorded,
            "pointing_altaz": {"alt": 35.0, "az": 180.0},
        },
        {
            "datetime": end_recorded,
            "pointing_altaz": {"alt": 36.0, "az": 181.0},
        },
    ]
    assert {
        timer.get_name() for timer in action.timer_actions
    } == {
        f"dish001_req_timer_retry:{response_timestamp}",
        f"dish001_req_timer_final:{response_timestamp}",
    }
