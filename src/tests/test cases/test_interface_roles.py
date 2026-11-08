from unittest.mock import Mock

from env.app import App
from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from models.app import AppModel


def test_interface_roles_follow_registered_endpoints_and_roundtrip():
    app = App.__new__(App)
    app.stop = Mock()  # This fixture does not start the application lifecycle.
    app.app_model = AppModel()
    app.interfaces = {}
    api = Mock()
    api.get_api_version.return_value = "1.0"
    for name, endpoint in [("dm", Mock(spec=TCPClient)),
                           ("dig", Mock(spec=TCPServer)),
                           ("other", object())]:
        app.register_interface(name, api, endpoint)

    expected = {"dm": "CLIENT", "dig": "SERVER", "other": "UNKNOWN"}
    assert app.app_model.interface_roles == expected
    assert AppModel.from_dict(app.app_model.to_dict()).interface_roles == expected
    app.deregister_interface("dm")
    assert "dm" not in app.app_model.interface_roles
    app.stop_interfaces()
    assert app.app_model.interface_roles == {}


def test_older_app_snapshots_default_to_no_reported_roles():
    data = AppModel().to_dict()
    del data["interface_roles"]
    assert AppModel.from_dict(data).interface_roles == {}
