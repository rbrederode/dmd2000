import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api import protocol as dmd_protocol
from api.cmd_api import ACTION_CODE_COMMAND
from api.cmd_registry import CommandRegistry
from dsh.dm import DM
from dsh.drivers.driver import DishDriver
from env.app_processor import AppProcessor
from ipc.message import APIMessage
from models.app import AppModel
from models.dsh import Capability, DishMode


class StopDriver:
    def __init__(self):
        self.stop_count = 0

    def emergency_stop(self):
        self.stop_count += 1


def make_manager(driver=None):
    manager = DM.__new__(DM)
    manager.app_model = AppModel(app_name=dmd_protocol.DM)
    manager.stop = lambda: None
    manager.dish_drivers = {"dish001": driver} if driver is not None else {}
    manager.dish_locks = {}
    manager.command_registry = CommandRegistry.load(app_name=dmd_protocol.DM)
    manager.command_registry.register_handler("stop", manager.process_stop_command)
    return manager


def test_stop_command_stops_selected_dish():
    driver = StopDriver()
    manager = make_manager(driver)

    status, message = manager.process_stop_command({
        "action_code": ACTION_CODE_COMMAND,
        "command": "stop",
        "value": "dish001",
    })

    assert status == dmd_protocol.STATUS_SUCCESS
    assert "dish001" in message
    assert driver.stop_count == 1
    assert isinstance(manager.dish_locks["dish001"], threading.RLock().__class__)


def test_stop_command_rejects_unknown_dish():
    manager = make_manager()

    status, message = manager.process_stop_command({
        "action_code": ACTION_CODE_COMMAND,
        "command": "stop",
        "value": "dish999",
    })

    assert status == dmd_protocol.STATUS_ERROR
    assert "unknown dish" in message


def test_setting_standby_capability_stops_after_latching_capability():
    driver = DishDriver.__new__(DishDriver)
    driver.dsh_model = SimpleNamespace(
        capability=Capability.OPERATE_FULL,
        last_update=None,
    )
    calls = []
    driver.stop = lambda: calls.append(driver.dsh_model.capability)

    driver.set_dish_capability(Capability.STANDBY)

    assert driver.dsh_model.capability == Capability.STANDBY
    assert calls == [Capability.STANDBY]


def test_emergency_stop_is_latched_after_hardware_stop():
    driver = DishDriver.__new__(DishDriver)
    driver.dsh_model = SimpleNamespace(
        capability=Capability.OPERATE_FULL,
        mode=DishMode.OPERATE,
        last_update=None,
    )
    calls = []
    driver.stop = lambda: calls.append(("stop", driver.dsh_model.capability))
    driver.clear_target_tuple = lambda: calls.append("clear_target")
    driver.set_desired_altaz = lambda value: calls.append(("desired_altaz", value))

    driver.emergency_stop()

    assert calls == [
        ("stop", Capability.STANDBY),
        "clear_target",
        ("desired_altaz", None),
    ]
    assert driver.dsh_model.capability == Capability.STANDBY
    assert driver.dsh_model.mode == DishMode.UNKNOWN


def test_emergency_stop_remains_latched_when_hardware_stop_fails():
    driver = DishDriver.__new__(DishDriver)
    driver.dsh_model = SimpleNamespace(
        capability=Capability.OPERATE_FULL,
        mode=DishMode.OPERATE,
        last_update=None,
    )
    calls = []

    def fail_stop():
        raise RuntimeError("controller unavailable")

    driver.stop = fail_stop
    driver.clear_target_tuple = lambda: calls.append("clear_target")
    driver.set_desired_altaz = lambda value: calls.append(("desired_altaz", value))

    with pytest.raises(RuntimeError, match="controller unavailable"):
        driver.emergency_stop()

    assert driver.dsh_model.capability == Capability.STANDBY
    assert driver.dsh_model.mode == DishMode.UNKNOWN
    assert calls == ["clear_target", ("desired_altaz", None)]


def test_stop_driver_failure_returns_command_error_response():
    class FailingStopDriver:
        def emergency_stop(self):
            raise RuntimeError("mount did not acknowledge stop")

    manager = make_manager(FailingStopDriver())
    processor = AppProcessor(name="dm-stop-test", driver=manager)
    request = APIMessage()
    request.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=dmd_protocol.CMD,
        to_system=dmd_protocol.DM,
        api_call={
            "msg_type": dmd_protocol.MSG_TYPE_REQ,
            "action_code": ACTION_CODE_COMMAND,
            "command": "stop",
            "value": "dish001",
        },
    )

    response = processor._handle_cmd_req(request, request.get_api_call())

    assert response.get_api_call()["status"] == dmd_protocol.STATUS_ERROR
    assert "mount did not acknowledge stop" in response.get_api_call()["message"]
