import pytest
from astropy.coordinates import AltAz
import astropy.units as u

from dsh.drivers.driver import DishDriver
from models.dsh import Capability, DishMode, DishModel, PointingState


def make_ready_driver(pointing_altaz, mode=DishMode.STANDBY_FP):
    model = DishModel(
        dsh_id="dish-test",
        mode=mode,
        pointing_state=PointingState.READY,
        pointing_altaz=pointing_altaz,
    )
    driver = DishDriver(dsh_model=model)
    driver._get_resolution = lambda: 0.1
    return driver


def test_equivalent_wrapped_azimuth_is_stationary():
    driver = make_ready_driver({"alt": 87.2, "az": 359.9})
    previous_pointing_dt = driver.dsh_model.pointing_altaz_dt
    driver._get_current_altaz = lambda: (87.2, -0.1)

    driver.get_current_altaz()

    assert driver.dsh_model.pointing_altaz["az"] == pytest.approx(359.9)
    assert driver.dsh_model.pointing_altaz_dt is not None
    assert driver.dsh_model.pointing_altaz_dt != previous_pointing_dt
    assert driver.dsh_model.velocity_altaz == pytest.approx({"alt": 0.0, "az": 0.0})
    assert driver.dsh_model.last_err_msg is None


def test_azimuth_velocity_uses_shortest_distance_across_north():
    driver = make_ready_driver({"alt": 45.0, "az": 359.9})
    driver._get_current_altaz = lambda: (45.0, 0.1)

    driver.get_current_altaz()

    assert driver.dsh_model.velocity_altaz["az"] == pytest.approx(0.2)


def test_repeated_movement_errors_do_not_embed_previous_error():
    driver = make_ready_driver({"alt": 45.0, "az": 10.0})
    driver.dsh_model.set_last_err("sentinel previous error")
    readings = iter([(45.0, 11.0), (45.0, 12.0)])
    driver._get_current_altaz = lambda: next(readings)

    driver.get_current_altaz()
    first_error = driver.dsh_model.last_err_msg
    driver.get_current_altaz()
    second_error = driver.dsh_model.last_err_msg

    assert "sentinel previous error" not in first_error
    assert "last_err_msg" not in first_error
    assert first_error not in second_error
    assert "last_err_msg" not in second_error
    assert len(second_error) < len(first_error) + 100


def test_stationary_slew_accepts_truncated_achievable_target():
    driver = make_ready_driver({"alt": 46.7, "az": 227.8})
    driver.dsh_model.pointing_state = PointingState.SLEW
    driver.dsh_model.desired_altaz = {
        "alt": 46.70794128267457,
        "az": 227.94461791555793,
    }
    driver._get_current_altaz = lambda: (46.7, 227.8)

    driver.get_current_altaz()

    assert driver.dsh_model.pointing_state == PointingState.READY


def test_stationary_slew_still_rejects_position_beyond_one_step():
    driver = make_ready_driver({"alt": 46.7, "az": 227.7})
    driver.dsh_model.pointing_state = PointingState.SLEW
    driver.dsh_model.desired_altaz = {
        "alt": 46.70794128267457,
        "az": 227.94461791555793,
    }
    driver._get_current_altaz = lambda: (46.7, 227.7)

    driver.get_current_altaz()

    assert driver.dsh_model.pointing_state == PointingState.UNKNOWN


def test_slew_records_acquisition_when_dish_is_already_on_target():
    driver = make_ready_driver(
        {"alt": 80.0, "az": 10.0},
        mode=DishMode.OPERATE,
    )
    driver.dsh_model.capability = Capability.OPERATE_FULL
    target_altaz = AltAz(alt=80.0 * u.deg, az=10.0 * u.deg)

    driver.slew(target_altaz)

    assert driver.dsh_model.pointing_state == PointingState.READY
    assert driver.dsh_model.tgt_acq_dt is not None
    assert driver.dsh_model.last_update == driver.dsh_model.tgt_acq_dt
    assert driver.dsh_model.pointing_altaz == {"alt": 80.0, "az": 10.0}


@pytest.mark.parametrize('angle', [0., 90., 180., 270., 359.])
def test_current_pointing_offset_recovers_spherical_scan_displacement(angle):
    from astropy.coordinates import SkyCoord
    from models.target import TargetModel
    centre = SkyCoord(alt=80*u.deg, az=359*u.deg, frame='altaz')
    measured = centre.directional_offset_by(angle*u.deg, 3*u.deg)
    driver = make_ready_driver(None)
    driver.dsh_model.target = TargetModel(altaz={'alt':80., 'az':359.})
    driver.dsh_model.desired_altaz = {'alt':float(measured.alt.deg), 'az':float(measured.az.deg)}
    driver._get_current_altaz = lambda: (float(measured.alt.deg), float(measured.az.deg))
    driver.get_current_altaz()
    assert driver.dsh_model.pointing_offset == pytest.approx(3.)
    assert driver.dsh_model.pointing_position_angle == pytest.approx(angle)
    assert driver.get_current_pec() == pytest.approx((0.,0.))
    # An unchanged hardware reading must still recompute against a changed target.
    driver.dsh_model.target.altaz = dict(driver.dsh_model.desired_altaz)
    driver.get_current_altaz()
    assert driver.dsh_model.pointing_offset == pytest.approx(0., abs=1e-10)
    assert driver.dsh_model.pointing_position_angle == 0.
    driver.dsh_model.target = None
    driver.get_current_altaz()
    assert driver.dsh_model.pointing_offset is None
    assert driver.dsh_model.pointing_position_angle is None


@pytest.mark.parametrize('pointing, scan_params', [('OFFSET_SCAN', {'offset':-4., 'angle':90., 'rate':0.}),
                                                ('FIVE_POINT_SCAN', {'offset':4., 'direction':'W'})])
def test_scan_offsets_refresh_only_after_measuring_current_pointing(pointing, scan_params):
    from astropy.coordinates import SkyCoord
    from models.target import TargetModel, PointingType, OffsetScan, FivePointScan
    driver = make_ready_driver({'alt':40., 'az':0.})
    centre = SkyCoord(alt=40*u.deg, az=0*u.deg, frame='altaz')
    scan = (OffsetScan if pointing == 'OFFSET_SCAN' else FivePointScan)(**scan_params)
    target = TargetModel(pointing=PointingType[pointing], scan=scan)
    # A fixed centre avoids ephemeris/network dependence in spherical geometry tests.
    driver._get_target_centre_altaz = lambda target, frame: SkyCoord(alt=40*u.deg, az=0*u.deg,
                                                               frame=frame.replicate_without_data())
    measured = centre.directional_offset_by(90*u.deg, 2*u.deg)
    driver.dsh_model.pointing_altaz = {'alt':float(measured.alt.deg), 'az':float(measured.az.deg)}
    driver.dsh_model.target = target
    driver.dsh_model.pointing_offset = 5.
    driver.dsh_model.pointing_position_angle = 270.
    desired = driver.get_desired_altaz(target)
    # Computing commands leaves the last measured displacement unchanged.
    assert driver.dsh_model.pointing_offset == 5.
    assert driver.dsh_model.pointing_position_angle == 270.
    assert centre.separation(SkyCoord(alt=desired.alt, az=desired.az, frame='altaz')).deg == pytest.approx(4.)
    driver._get_current_altaz = lambda: (float(measured.alt.deg), float(measured.az.deg))
    driver.get_current_altaz()
    assert driver.dsh_model.pointing_offset == pytest.approx(2.)
    assert driver.dsh_model.pointing_position_angle == pytest.approx(90.)
    assert driver.get_desired_altaz(None) is None
    assert driver.dsh_model.pointing_offset == pytest.approx(2.)
    assert driver.dsh_model.pointing_position_angle == pytest.approx(90.)
    driver.dsh_model.target = None
    driver.get_current_altaz()
    assert driver.dsh_model.pointing_offset is None
    assert driver.dsh_model.pointing_position_angle is None


@pytest.mark.parametrize('coordinates', [
    {'alt':40., 'az':359.},
    {'alt':40*u.deg, 'az':359*u.deg},
    AltAz(alt=40*u.deg, az=359*u.deg),
])
def test_desired_drift_position_reuses_target_centre_helper(coordinates):
    from unittest.mock import Mock
    from models.target import TargetModel
    driver = make_ready_driver(None)
    driver._get_target_centre_altaz = Mock(wraps=driver._get_target_centre_altaz)
    target = TargetModel(altaz=coordinates)
    desired = driver.get_desired_altaz(target)
    driver._get_target_centre_altaz.assert_called_once()
    assert driver._get_target_centre_altaz.call_args.args[0] is target
    assert desired.alt.deg == pytest.approx(40.)
    assert desired.az.deg == pytest.approx(359.)
    assert driver.dsh_model.pointing_offset is None
    assert driver.dsh_model.pointing_position_angle is None
