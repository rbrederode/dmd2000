from env.events import ConfigEvent
from models.telescope import TelescopeModel
from tm import tm as tm_module
from tm.tm import TelescopeManager


def test_process_config_forwards_ui_command_to_resolved_endpoint(monkeypatch):
    forwarded = []

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.telmodel = TelescopeModel()
    monkeypatch.setattr(tm_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(tm_module.cmd_app, "main", lambda argv: forwarded.append(argv) or 0)

    manager.process_config(
        ConfigEvent(
            category="CMD",
            old_config=None,
            new_config={"_type": "Command", "app": "DM", "cmd": "Trace", "value": "On"},
        )
    )

    assert forwarded == [[
        "--host", "127.0.0.1",
        "--port", "60003",
        "--system", "dm",
        "set", "trace", "ON",
    ]]


def test_process_config_ignores_resync_value(monkeypatch):
    forwarded = []

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.telmodel = TelescopeModel()
    monkeypatch.setattr(tm_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(tm_module.cmd_app, "main", lambda argv: forwarded.append(argv) or 0)

    manager.process_config(
        ConfigEvent(
            category="CMD",
            old_config=None,
            new_config={"_type": "Command", "app": "TM", "cmd": "Resync", "value": "On"},
        )
    )

    assert forwarded == [[
        "--host", "127.0.0.1",
        "--port", "60002",
        "--system", "tm",
        "resync",
    ]]


def test_process_config_forwards_stop_with_case_preserved(monkeypatch):
    forwarded = []
    aborted = []

    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target = target
            self.args = args

        def start(self):
            self.target(*self.args)

    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.telmodel = TelescopeModel()
    manager.abort_observations = (
        lambda dsh_id, action: aborted.append(dsh_id) or action
    )
    monkeypatch.setattr(tm_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(tm_module.cmd_app, "main", lambda argv: forwarded.append(argv) or 0)

    manager.process_config(
        ConfigEvent(
            category="CMD",
            old_config=None,
            new_config={
                "_type": "Command",
                "app": "DM",
                "cmd": "STOP",
                "value": "dish001",
            },
        )
    )

    assert forwarded == [[
        "--host", "127.0.0.1",
        "--port", "60003",
        "--system", "dm",
        "stop", "dish001",
    ]]
    assert aborted == ["dish001"]
