from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api import protocol as dmd_protocol
from models.dsh import DishList, DishManagerModel, DishModel, PointingState
from models.obs import ObsModel
from models.scan import ScanModel
from models.target import PointingType, TargetModel, TargetScanSet
from tm.tm import TelescopeManager


def test_scan_model_target_acq_dt_round_trip_and_default():
    target_acq_dt = datetime(2026, 8, 21, 12, 25, 55, tzinfo=timezone.utc)
    scan = ScanModel(
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=0,
        scan_iter=0,
        tgt_acq_dt=target_acq_dt,
        tgt_acq_altaz={"alt": 35.25, "az": 182.5},
    )

    persisted = scan.to_dict()
    assert "tgt_acq_dt" in persisted
    assert "target_acq_start" not in persisted
    assert "target_acq_dt" not in persisted
    restored = ScanModel.from_dict(persisted)
    assert restored.tgt_acq_dt == target_acq_dt
    assert restored.tgt_acq_altaz == {"alt": 35.25, "az": 182.5}

    # A completed model returned by SDP may not know the acquisition epoch;
    # its default None must not erase the value already held by TM.
    scan.update_from_model(
        ScanModel(obs_id="obs001", tgt_idx=0, freq_scan=0, scan_iter=0)
    )
    assert scan.tgt_acq_dt == target_acq_dt
    assert scan.tgt_acq_altaz == {"alt": 35.25, "az": 182.5}

    # Metadata without an acquisition timestamp uses the default.
    persisted.pop("tgt_acq_dt")
    assert ScanModel.from_dict(persisted).tgt_acq_dt is None


@pytest.mark.parametrize("pointing_state", [PointingState.TRACK, PointingState.SCAN])
def test_record_target_acquisition_start_on_all_target_scans(pointing_state):
    target_acq_dt = datetime(2026, 8, 21, 12, 25, 55, tzinfo=timezone.utc)
    scans = [
        ScanModel(obs_id="obs001", tgt_idx=0, freq_scan=0, scan_iter=0),
        ScanModel(obs_id="obs001", tgt_idx=0, freq_scan=0, scan_iter=1),
    ]
    obs = ObsModel(
        obs_id="obs001",
        target_scans=[
            TargetScanSet(obs_id="obs001", tgt_idx=0, scans=scans),
        ],
    )
    target = TargetModel(
        obs_id=obs.obs_id,
        tgt_idx=0,
        id="Sun",
        pointing=(
            PointingType.SIDEREAL_TRACK
            if pointing_state == PointingState.TRACK
            else PointingType.OFFSET_SCAN
        ),
    )
    dish = DishModel(
        dsh_id="dish001",
        pointing_state=pointing_state,
        target=target,
        tgt_id="obs001-0",
        tgt_acq_dt=target_acq_dt,
        pointing_altaz={"alt": 35.25, "az": 182.5},
    )

    assert TelescopeManager._set_tgt_acquisition(obs, dish)
    assert [scan.tgt_acq_dt for scan in scans] == [
        target_acq_dt,
        target_acq_dt,
    ]
    assert all(
        scan.tgt_acq_altaz == {"alt": 35.25, "az": 182.5} for scan in scans
    )

    # A duplicate transition advice must not replace the original epoch.
    dish.tgt_acq_dt = datetime(2026, 8, 21, 12, 26, tzinfo=timezone.utc)
    dish.pointing_altaz = {"alt": 36.0, "az": 183.0}
    assert not TelescopeManager._set_tgt_acquisition(obs, dish)
    assert all(scan.tgt_acq_dt == target_acq_dt for scan in scans)
    assert all(
        scan.tgt_acq_altaz == {"alt": 35.25, "az": 182.5} for scan in scans
    )


def test_ready_status_does_not_record_target_acquisition_start():
    scan = ScanModel(obs_id="obs001", tgt_idx=0, freq_scan=0, scan_iter=0)
    obs = ObsModel(
        obs_id="obs001",
        target_scans=[TargetScanSet(obs_id="obs001", tgt_idx=0, scans=[scan])],
    )
    dish = DishModel(
        dsh_id="dish001",
        pointing_state=PointingState.READY,
        target=TargetModel(obs_id="obs001", tgt_idx=0, id="Sun"),
    )

    assert not TelescopeManager._set_tgt_acquisition(obs, dish)
    assert scan.tgt_acq_dt is None
    assert scan.tgt_acq_altaz is None


def test_target_acquisition_skips_load_scans():
    target_acq_dt = datetime(2026, 8, 21, 12, 25, 55, tzinfo=timezone.utc)
    sky_scan = ScanModel(
        obs_id="obs001", tgt_idx=0, freq_scan=0, scan_iter=0
    )
    load_scan = ScanModel(
        obs_id="obs001", tgt_idx=0, freq_scan=0, scan_iter=1, load=True
    )
    obs = ObsModel(
        obs_id="obs001",
        target_scans=[
            TargetScanSet(
                obs_id="obs001", tgt_idx=0, scans=[sky_scan, load_scan]
            ),
        ],
    )
    dish = DishModel(
        dsh_id="dish001",
        pointing_state=PointingState.SCAN,
        target=TargetModel(
            obs_id="obs001",
            tgt_idx=0,
            id="Sun",
            pointing=PointingType.OFFSET_SCAN,
        ),
        tgt_id="obs001-0",
        tgt_acq_dt=target_acq_dt,
        pointing_altaz={"alt": 35.25, "az": 182.5},
    )

    assert TelescopeManager._set_tgt_acquisition(obs, dish)
    assert sky_scan.tgt_acq_dt == target_acq_dt
    assert sky_scan.tgt_acq_altaz == {"alt": 35.25, "az": 182.5}
    assert load_scan.tgt_acq_dt is None
    assert load_scan.tgt_acq_altaz is None


def test_dish_status_advice_records_target_acquisition_start():
    target_acq_dt = datetime(2026, 8, 21, 12, 25, 55, tzinfo=timezone.utc)
    scan = ScanModel(obs_id="obs001", tgt_idx=0, freq_scan=0, scan_iter=0)
    obs = ObsModel(
        obs_id="obs001",
        target_scans=[TargetScanSet(obs_id="obs001", tgt_idx=0, scans=[scan])],
    )
    target = TargetModel(
        obs_id=obs.obs_id,
        tgt_idx=0,
        id="Sun",
        pointing=PointingType.OFFSET_SCAN,
    )
    old_dish = DishModel(dsh_id="dish001", pointing_state=PointingState.READY)
    acquired_dish = DishModel(
        dsh_id="dish001",
        pointing_state=PointingState.SCAN,
        target=target,
        tgt_id="obs001-0",
        tgt_acq_dt=target_acq_dt,
        pointing_altaz={"alt": 35.25, "az": 182.5},
    )

    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.set_last_err = lambda message: message
    manager.telmodel = SimpleNamespace(
        dsh_mgr=DishManagerModel(dish_store=DishList(dish_list=[old_dish])),
        oda=SimpleNamespace(
            obs_store=SimpleNamespace(
                get_obs_by_id=lambda obs_id: obs if obs_id == obs.obs_id else None,
                get_obs_by_dsh_id=lambda _dsh_id: None,
            )
        ),
    )
    acquired_manager = DishManagerModel(
        dish_store=DishList(dish_list=[acquired_dish]),
    )

    manager.process_dm_msg(
        event=None,
        api_msg={
            "entity": acquired_dish.dsh_id,
            "timestamp": "2026-08-21T12:25:55.100000+00:00",
        },
        api_call={
            "msg_type": dmd_protocol.MSG_TYPE_ADV,
            "action_code": dmd_protocol.ACTION_CODE_SET,
            "property": dmd_protocol.PROPERTY_STATUS,
            "status": dmd_protocol.STATUS_SUCCESS,
            "value": acquired_manager.to_dict(),
            "obs_data": {"obs_id": obs.obs_id, "target_id": "obs001-0"},
        },
        payload=bytearray(),
    )

    assert scan.tgt_acq_dt == target_acq_dt
    assert scan.tgt_acq_altaz == {"alt": 35.25, "az": 182.5}
