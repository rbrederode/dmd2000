import astropy.units as u
from astropy.coordinates import SkyCoord, AltAz
import pytest

from models.target import TargetModel, PointingType
from ui.opt_targets import target_display_metadata


@pytest.mark.parametrize('mode', list(PointingType))
def test_equatorial_definition_keeps_ra_dec_and_galactic_coordinates(mode):
    sky = SkyCoord(ra=180*u.deg, dec=25*u.deg, frame='icrs')
    target = TargetModel(pointing=mode, sky_coord=sky)
    row = target_display_metadata(target)
    assert row['pointing'] == mode.name
    assert row['target_coordinates']['kind'] == 'equatorial'
    assert row['target_coordinates']['ra_deg'] == pytest.approx(180.)
    assert row['target_coordinates']['dec_deg'] == pytest.approx(25.)
    assert row['target_coordinates']['longitude'] == pytest.approx(sky.galactic.l.deg)
    assert row['target_coordinates']['latitude'] == pytest.approx(sky.galactic.b.deg)


def test_galactic_definition_does_not_invent_equatorial_coordinates():
    target = TargetModel(pointing=PointingType.SIDEREAL_TRACK, sky_coord=SkyCoord(l=30*u.deg,b=5*u.deg,frame='galactic'))
    row = target_display_metadata(target)
    assert row['pointing'] == 'SIDEREAL_TRACK'
    assert row['target_coordinates'] == dict(kind='galactic', longitude=30., latitude=5.)


@pytest.mark.parametrize('altaz', [{'alt':30., 'az':180.}, {'alt':30*u.deg, 'az':180*u.deg},
                                  AltAz(alt=30*u.deg, az=180*u.deg)])
def test_altaz_definition_replaces_equatorial_and_galactic_coordinates(altaz):
    target = TargetModel(altaz=altaz, sky_coord=SkyCoord(ra=180*u.deg,dec=25*u.deg))
    row = target_display_metadata(target)
    assert row['pointing'] == 'DRIFT_SCAN'
    assert row['target_coordinates'] == dict(kind='altaz', alt_deg=30., az_deg=180.)
    assert row['longitude'] is None and row['latitude'] is None


@pytest.mark.parametrize('mode', [PointingType.NON_SIDEREAL_TRACK, PointingType.OFFSET_SCAN, PointingType.FIVE_POINT_SCAN])
def test_body_id_without_ephemeris_or_pointing_conversion(mode):
    row = target_display_metadata(TargetModel(id='Sun',pointing=mode))
    assert row['pointing'] == mode.name
    assert row['target_coordinates'] == dict(kind='body', id='Sun')
    assert row['longitude'] is None and row['latitude'] is None


def test_missing_target_has_no_coordinates_or_mode():
    assert target_display_metadata(None) == dict(pointing=None, target_coordinates=None, longitude=None, latitude=None)


def test_altaz_skycoord_definition_stays_in_horizontal_coordinates():
    target = TargetModel(sky_coord=SkyCoord(alt=45*u.deg, az=90*u.deg, frame='altaz'))
    row = target_display_metadata(target)
    assert row['target_coordinates'] == dict(kind='altaz', alt_deg=45., az_deg=90.)
    assert row['longitude'] is None and row['latitude'] is None
