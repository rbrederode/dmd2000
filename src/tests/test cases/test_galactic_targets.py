import json

import astropy.units as u
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time
from astropy.utils import iers
import pytest

from dsh.drivers.driver import DishDriver
from models.obs import ObsModel
from models.dsh import DishModel
from models.target import FivePointScan, OffsetScan, PointingType, TargetModel
from util.format import fmt_target_coords


def galactic_target(longitude=0.0, latitude=0.0):
    return TargetModel.from_dict({
        "_type": "TargetModel", "tgt_idx": 1,
        "pointing": {
            "_type": "enum.IntEnum", "instance": "PointingType", "value": "SIDEREAL_TRACK",
        },
        "sky_coord": {
            "_type": "SkyCoord", "frame": "galactic", "l": longitude, "b": latitude,
        },
    })


@pytest.mark.parametrize("longitude, latitude", [(0.0, 0.0), (123.5, -14.25)])
def test_galactic_target_survives_observation_json_and_disk_roundtrip(tmp_path, longitude, latitude):
    target = galactic_target(longitude, latitude)
    observation = ObsModel(obs_id="galactic-test", targets=[target])

    encoded = json.dumps(observation.to_dict())
    sky_coord = json.loads(encoded)["targets"][0]["sky_coord"]
    assert sky_coord == {
        "_type": "SkyCoord", "frame": "galactic", "l": longitude, "b": latitude,
    }
    restored = ObsModel.from_dict(json.loads(encoded))
    (tmp_path / "obs.json").write_text(encoded, encoding="utf-8")
    loaded = ObsModel.load_from_disk(input_dir=str(tmp_path), filename="obs.json")

    for result in (restored, loaded):
        coord = result.targets[0].sky_coord
        assert coord.frame.name == "galactic"
        assert coord.l.deg == pytest.approx(longitude)
        assert coord.b.deg == pytest.approx(latitude)


@pytest.mark.parametrize("pointing", [
    PointingType.SIDEREAL_TRACK, PointingType.OFFSET_SCAN, PointingType.FIVE_POINT_SCAN,
])
@pytest.mark.parametrize("equatorial_frame", ["icrs", "fk5"])
@pytest.mark.parametrize("longitude, latitude", [(0.0, 0.0), (123.5, -14.25)])
def test_galactic_pointing_matches_equivalent_equatorial_target(
    monkeypatch, pointing, equatorial_frame, longitude, latitude,
):
    observing_time = Time("2026-06-15T23:00:00")
    monkeypatch.setattr("dsh.drivers.driver.Time", lambda _: observing_time)
    driver = DishDriver.__new__(DishDriver)
    driver.dsh_model = DishModel(dsh_id="dish-test")
    driver.location = EarthLocation.from_geodetic(-2.305533 * u.deg, 53.23409 * u.deg, 77 * u.m)
    commanded_positions = []
    driver.set_desired_altaz = commanded_positions.append
    if pointing == PointingType.OFFSET_SCAN:
        scan = OffsetScan(offset=0.4, rate=0.1, angle=35.0)
    elif pointing == PointingType.FIVE_POINT_SCAN:
        scan = FivePointScan(offset=0.4, direction="E")
    else:
        scan = None
    galactic = galactic_target(longitude, latitude)
    galactic.pointing = pointing
    galactic.scan = scan
    equatorial = TargetModel(
        pointing=pointing, sky_coord=galactic.sky_coord.transform_to(equatorial_frame), scan=scan,
    )

    with iers.conf.set_temp("auto_download", False):
        galactic_position = driver.get_desired_altaz(galactic)
        equatorial_position = driver.get_desired_altaz(equatorial)

    assert len(commanded_positions) == 2
    assert galactic_position.separation(equatorial_position).to_value(u.arcsec) < 1e-5


def test_galactic_target_formatting_supports_unnamed_and_named_targets():
    target = galactic_target(123.5, -14.25)
    assert "l=123.5" in str(target)
    assert "b=-14.25" in str(target)
    assert fmt_target_coords(target) == "Galactic l 123d30m00.00s b -14d15m00.00s"
    target.id = "Galactic target"
    assert str(target) == "Id=Galactic target"
    assert "Galactic l" in fmt_target_coords(target)


def test_equatorial_target_formatting_is_preserved():
    target = TargetModel(sky_coord=SkyCoord(ra=180 * u.deg, dec=45 * u.deg, frame="icrs"))
    assert "ra=" in str(target)
    assert fmt_target_coords(target) == "RA 12h00m00s Dec 45d00m00.00s"
