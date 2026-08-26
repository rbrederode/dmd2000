from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from models.dsh import DishList, DishManagerModel, DishModel
from models.obs import ObsModel
from models.scan import ScanModel
from tm.tm import TelescopeManager


def _manager_with_dish(ref_dt, pointing_altaz):
    dish = DishModel(dsh_id="dish001", pointing_altaz=pointing_altaz)
    dsh_mgr = DishManagerModel(
        dish_store=DishList(dish_list=[dish]),
        last_update=ref_dt,
    )
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.telmodel = SimpleNamespace(dsh_mgr=dsh_mgr)
    return manager


def test_scan_model_target_ref_round_trip():
    ref_dt = datetime(2026, 8, 24, 12, 0, 30, tzinfo=timezone.utc)
    scan = ScanModel(
        tgt_ref_dt=ref_dt,
        tgt_ref_altaz={"alt": 35.25, "az": 182.5},
    )

    restored = ScanModel.from_dict(scan.to_dict())

    assert restored.tgt_ref_dt == ref_dt
    assert restored.tgt_ref_altaz == {"alt": 35.25, "az": 182.5}


@pytest.mark.parametrize("offset_seconds", [0, 30, 60])
def test_apply_target_ref_accepts_snapshot_within_scan(offset_seconds):
    read_start = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    ref_dt = read_start + timedelta(seconds=offset_seconds)
    scan = ScanModel(read_start=read_start, read_end=read_start + timedelta(seconds=60))
    obs = ObsModel(obs_id="obs001", dsh_id="dish001")
    manager = _manager_with_dish(ref_dt, {"alt": 35.25, "az": 182.5})

    assert manager._apply_target_ref_to_scan(obs, scan)
    assert scan.tgt_ref_dt == ref_dt
    assert scan.tgt_ref_altaz == {"alt": 35.25, "az": 182.5}


@pytest.mark.parametrize("offset_seconds", [-1, 61])
def test_apply_target_ref_rejects_snapshot_outside_scan(offset_seconds):
    read_start = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    ref_dt = read_start + timedelta(seconds=offset_seconds)
    scan = ScanModel(read_start=read_start, read_end=read_start + timedelta(seconds=60))
    obs = ObsModel(obs_id="obs001", dsh_id="dish001")
    manager = _manager_with_dish(ref_dt, {"alt": 35.25, "az": 182.5})

    assert not manager._apply_target_ref_to_scan(obs, scan)
    assert scan.tgt_ref_dt is None
    assert scan.tgt_ref_altaz is None


@pytest.mark.parametrize(
    "pointing_altaz",
    [None, {}, {"alt": 35.25}, {"alt": "35.25", "az": 182.5}],
)
def test_apply_target_ref_requires_numeric_altaz(pointing_altaz):
    read_start = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    scan = ScanModel(read_start=read_start, read_end=read_start + timedelta(seconds=60))
    obs = ObsModel(obs_id="obs001", dsh_id="dish001")
    manager = _manager_with_dish(
        read_start + timedelta(seconds=30),
        pointing_altaz,
    )

    assert not manager._apply_target_ref_to_scan(obs, scan)
    assert scan.tgt_ref_dt is None
    assert scan.tgt_ref_altaz is None


def test_apply_target_ref_skips_load_scan():
    read_start = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    scan = ScanModel(
        read_start=read_start,
        read_end=read_start + timedelta(seconds=60),
        load=True,
    )
    obs = ObsModel(obs_id="obs001", dsh_id="dish001")
    manager = _manager_with_dish(
        read_start + timedelta(seconds=30),
        {"alt": 35.25, "az": 182.5},
    )

    assert not manager._apply_target_ref_to_scan(obs, scan)
    assert scan.tgt_ref_dt is None
    assert scan.tgt_ref_altaz is None
