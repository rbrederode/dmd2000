from datetime import datetime, timezone

from models.dsh import DishModel
from models.scan import ScanModel


def test_scan_model_pointing_refs_round_trip():
    recorded = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    scan = ScanModel(
        pointing_refs=[{
            "datetime": recorded,
            "pointing_altaz": {"alt": 35.25, "az": 182.5},
        }],
    )

    restored = ScanModel.from_dict(scan.to_dict())

    assert restored.pointing_refs == scan.pointing_refs


def test_dish_model_pointing_altaz_datetime_round_trip():
    pointing_dt = datetime(2026, 8, 24, 12, 0, 30, tzinfo=timezone.utc)
    dish = DishModel(
        dsh_id="dish001",
        pointing_altaz={"alt": 35.25, "az": 182.5},
        pointing_altaz_dt=pointing_dt,
    )

    restored = DishModel.from_dict(dish.to_dict())

    assert restored.pointing_altaz == {"alt": 35.25, "az": 182.5}
    assert restored.pointing_altaz_dt == pointing_dt
