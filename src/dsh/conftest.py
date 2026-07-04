import pytest
from datetime import datetime, timezone
from dsh.drivers.md01.md01_driver import MD01Driver
from dsh.drivers.md01.md01_config import MD01Config
from models.dsh import DishModel, DishMode, PointingState, Feed, DriverType, Capability

""" Fixtures defined: md01_driver, dm, target, dsh_model.

Direct test usages:

md01_driver used by tests in md01_driver.py:407 (e.g. def test_can_reach(md01_driver):) and md01_driver.py:414 (test_do_flip).
dm and md01_driver used by a test in dm.py:762 (def test_get_desired_altaz(dm, md01_driver):).
Notes on discovery:

Pytest will automatically apply conftest.py fixtures to test modules in the same package/directory and its subpackages (i.e., tests under dsh will pick up this conftest.py).
Some fixtures may be used implicitly (tests may not import conftest directly; they simply declare fixture names in test function parameters).
"""

@pytest.fixture
def md01_driver():
    from dsh.drivers.md01.md01_driver import MD01Driver
    from dsh.drivers.md01.md01_config import MD01Config
    from models.dsh import DishModel, DishMode, PointingState, Feed, DriverType, Capability

    md01_cfg = MD01Config(
        host="192.168.0.2",
        port=65000,
        stow_alt=90.0,
        stow_az=0.0,
        offset_alt=0.0,
        offset_az=0.0,
        min_alt=0.0,
        max_alt=90.0,
        close_enough=0.1,
        rate_limit=0.0,
        last_update=datetime.now(timezone.utc)
    )
    
    dish002 = DishModel(
        dsh_id="dish002",
        short_desc="3m Jodrell Dish",
        diameter=3.0,
        fd_ratio=0.43,
        latitude=53.2421, longitude=-2.3067, height=80.0,
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.NONE,
        dig_id="dig002",
        capability=Capability.OPERATE_FULL,
        driver_type=DriverType.MD01,
        driver_config=md01_cfg,
        last_update=datetime.now(timezone.utc)
    )
    return MD01Driver(dsh_model=dish002)

@pytest.fixture
def dm():
    from dsh.dm import DM

    dm = DM()
    dm.start()

    return dm

@pytest.fixture
def target():
    from models.target import TargetModel, PointingType
    from astropy.coordinates import SkyCoord
    return TargetModel(
        id="Vega",
        pointing=PointingType.SIDEREAL_TRACK,
        sky_coord=SkyCoord.from_name("Vega")
    )

@pytest.fixture
def dsh_model():
    md01_cfg = MD01Config(
        host="192.168.0.2",
        port=65000,
        stow_alt=90.0,
        stow_az=0.0,
        offset_alt=0.0,
        offset_az=0.0,
        min_alt=0.0,
        max_alt=90.0,
        close_enough=0.1,
        rate_limit=0.0,
        last_update=datetime.now(timezone.utc)
    )
    
    dish002 = DishModel(
        dsh_id="dish002",
        short_desc="3m Jodrell Dish",
        diameter=3.0,
        fd_ratio=0.43,
        latitude=53.2421, longitude=-2.3067, height=80.0,
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.NONE,
        dig_id="dig002",
        capability=Capability.OPERATE_FULL,
        driver_type=DriverType.MD01,
        driver_config=md01_cfg,
        last_update=datetime.now(timezone.utc)
    )
    return dish002
