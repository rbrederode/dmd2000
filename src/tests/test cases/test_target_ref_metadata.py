import json
import pytest

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


@pytest.mark.parametrize('offset, angle', [(1.25, 270.), (None, None), (0, 0), (180, 360)])
def test_pointing_offset_metadata_survives_disk_round_trip(tmp_path, offset, angle):
    scan = ScanModel(pointing_refs=[{
        'datetime': datetime(2026,11,12,10,tzinfo=timezone.utc),
        'pointing_altaz': {'alt':35.25, 'az':182.5},
        'pointing_offset': offset, 'pointing_position_angle': angle,
    }])
    scan.save_to_disk(output_dir=str(tmp_path), filename='scan.json')
    stored = json.loads((tmp_path/'scan.json').read_text())['pointing_refs'][0]
    assert set(stored) == {'datetime', 'pointing_altaz', 'pointing_offset', 'pointing_position_angle'}
    assert stored['pointing_offset'] == offset
    assert stored['pointing_position_angle'] == angle
    restored = ScanModel.load_from_disk(input_dir=str(tmp_path), filename='scan.json')
    assert restored.pointing_refs == scan.pointing_refs
