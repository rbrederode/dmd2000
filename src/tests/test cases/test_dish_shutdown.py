import threading
from unittest import mock

import pytest

from dsh.drivers.driver import DishDriver
from models.dsh import DishMode, DishModel, PointingState


def make_driver(mode):
    driver = DishDriver.__new__(DishDriver)
    driver._rlock = threading.RLock()
    driver.dsh_model = DishModel(
        dsh_id="dish001",
        mode=mode,
        pointing_state=PointingState.READY,
    )
    driver._set_shutdown_mode = mock.MagicMock()

    def stow():
        driver.dsh_model.mode = DishMode.STOW

    driver.set_stow_mode = mock.MagicMock(side_effect=stow)
    return driver


@pytest.mark.parametrize(
    "initial_mode",
    [DishMode.STANDBY_FP, DishMode.CONFIG, DishMode.OPERATE],
)
def test_shutdown_stows_first_from_powered_operational_modes(initial_mode):
    driver = make_driver(initial_mode)

    driver.set_shutdown_mode()

    driver.set_stow_mode.assert_called_once_with()
    driver._set_shutdown_mode.assert_called_once_with()
    assert driver.dsh_model.mode == DishMode.SHUTDOWN


@pytest.mark.parametrize(
    "initial_mode",
    [
        DishMode.STARTUP,
        DishMode.STOW,
        DishMode.MAINTENANCE,
        DishMode.STANDBY_LP,
        DishMode.UNKNOWN,
        DishMode.SHUTDOWN,
    ],
)
def test_shutdown_does_not_attempt_stow_from_non_operational_modes(initial_mode):
    driver = make_driver(initial_mode)

    driver.set_shutdown_mode()

    driver.set_stow_mode.assert_not_called()
    driver._set_shutdown_mode.assert_called_once_with()
    assert driver.dsh_model.mode == DishMode.SHUTDOWN
