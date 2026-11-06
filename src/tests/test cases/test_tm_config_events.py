"""TM config events no longer forward commands to application endpoints."""

from unittest.mock import Mock

import pytest

from env.events import ConfigEvent
from tm import tm as tm_module
from tm.tm import TelescopeManager
from util import cmd_app


@pytest.mark.parametrize('config', [
    {'app': 'SDP', 'cmd': 'Resync'},
    {'app': 'TM', 'cmd': 'Trace', 'value': 'On'},
    {'app': 'DM', 'cmd': 'STOP', 'value': 'dish001'},
])
def test_cmd_config_events_are_ignored(monkeypatch, caplog, config):
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.abort_observations = Mock()
    run = Mock()
    thread = Mock()
    monkeypatch.setattr(cmd_app, 'main', run)
    monkeypatch.setattr(tm_module.threading, 'Thread', thread)
    action = manager.process_config(ConfigEvent(category='CMD', new_config=config))
    assert 'unknown config event category CMD' in caplog.text
    assert not action.msgs_to_remote
    assert not action.obs_transitions
    run.assert_not_called()
    thread.assert_not_called()
    manager.abort_observations.assert_not_called()
