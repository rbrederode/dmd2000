"""Application lifecycle and timer-backed TM requests for the HTTP service."""

from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from api import protocol, tm_web
from env.app import App
from env.events import CallbackEvent, ResponseEvent, TimerEvent
from ipc.action import Action
from ipc.message import APIMessage
from ipc.tcp_server import TCPServer
from models.comms import CommunicationStatus, InterfaceType
from models.health import HealthState
from models.launch import load_scan_store_dir
from models.web import WebServerModel
from util.timer import Timer
from web.cmd_handler import CommandHandler
from web.profiles import ProfileManager
from web.model_cache import TelescopeModelCache, POLL_TIMER, REQUEST_PREFIX, POLL_SECONDS


TIMER_PREFIX = 'web_config_request:'


@dataclass
class HTTPConfigRequest:
    request_id: str
    message: object
    deadline: float
    future: Future = field(default_factory=Future)
    lock: object = field(default_factory=threading.RLock)
    sent: bool = False

    @property
    def timer_name(self):
        return TIMER_PREFIX + self.request_id

    def complete(self, result):
        with self.lock:
            if not self.future.done():
                self.future.set_result(result)


class WebServer(App):
    def __init__(self, http_app=None):
        self.web_model = WebServerModel()
        self.http_app = http_app
        self.http_server = None
        self.http_thread = None
        self._http_map = {}
        self.ui_app = self.ui_server = self.ui_thread = None
        self._ui_map = {}
        self._requests_lock = threading.RLock()
        self._tm_connection = None
        self._accepting_requests = True
        self._stopped = False
        super().__init__(app_name=protocol.WEB, app_model=self.web_model.app)
        args = self.get_args()
        if args.http_threads < 1:
            raise ValueError('HTTP worker count must be positive')
        self.profile_dir = Path(args.profile_dir or Path(__file__).resolve().parents[1] / 'config' / args.profile).expanduser()
        self._load_configuration(initial=True)
        self.profile_manager = ProfileManager(self.profile_dir, args.scan_store_dir, args.log_dir)
        _ = self.request_timeout  # Validate the CLI/profile setting before startup.
        self.tm_api = tm_web.TM_WEB()
        self.tm_endpoint = TCPServer(description=protocol.TM, queue=self.get_queue(),
                                     host=args.tm_host, port=args.tm_port)
        self.register_interface(protocol.TM, self.tm_api, self.tm_endpoint, InterfaceType.APP_APP)
        if http_app is not None:
            http_app.extensions['web_application'] = self

    def add_args(self, parser):
        super().add_args(parser)
        parser.set_defaults(cmd_config=os.getenv('WEB_CMD_CONFIG', parser.get_default('cmd_config')))
        parser.add_argument('--tm_host', default=os.getenv('WEB_TM_HOST', '127.0.0.1'),
                            help='Local WEB address to listen on for TM connections')
        parser.add_argument('--tm_port', type=int, default=int(os.getenv('WEB_TM_PORT', '50004')),
                            help='Local WEB TCP port to listen on for TM connections')
        parser.add_argument('--http_host', default='127.0.0.1')
        parser.add_argument('--http_port', type=int, default=8000)
        parser.add_argument('--http_threads', type=int, default=10)
        parser.add_argument('--ui_port', type=int, default=8765, help='Local-only UI port (bound to 127.0.0.1)')
        parser.add_argument('--ui_threads', type=int, default=4)
        parser.add_argument('--no_ui', action='store_true', help='Run only the API listener')
        parser.add_argument('--ui_state_dir', default=os.getenv('DMD_UI_STATE_DIR'), help='Shared SQLite users, session keys and UI state directory')
        parser.add_argument('--ui_data_root', action='append', help='Permitted OPT observation root; repeat for multiple roots')
        parser.add_argument('--ui_cache_dir', help='OPT results directory; defaults to a temporary session directory')
        parser.add_argument('--request_timeout', type=float, default=os.getenv('WEB_REQUEST_TIMEOUT'),
                            help='TM response timeout in seconds; defaults to app.msg_timeout_ms from the profile')
        parser.add_argument('--profile_dir', default=os.getenv('WEB_PROFILE_DIR'))
        parser.add_argument('--scan_store_dir', help='Override the profile observation search directory')
        parser.add_argument('--command_timeout', type=float, default=float(os.getenv('WEB_COMMAND_TIMEOUT', '5')))

    def _load_configuration(self, initial=False):
        args = self.get_args()
        self.scan_store_dir = (args.scan_store_dir if args.scan_store_dir is not None
                               else load_scan_store_dir(profile_dir=self.profile_dir))
        path = self.profile_dir / 'WebServerModel.json'
        if path.exists():
            model = WebServerModel.load_from_disk(input_dir=str(self.profile_dir), filename=path.name)
            self.web_model.id = model.id
            self.app_model.msg_timeout_ms = model.app.msg_timeout_ms
            if initial:
                self.app_model.app_cmd_host = args.cmd_host or model.app.app_cmd_host or self.app_model.app_cmd_host
                self.app_model.app_cmd_port = args.cmd_port or model.app.app_cmd_port or self.app_model.app_cmd_port
        if args.entity_id != '<undefined>':
            self.web_model.id = args.entity_id
        self.cmd_handler = CommandHandler(
            profile_dir=self.profile_dir,
            endpoint_overrides=json.loads(os.getenv('WEB_COMMAND_ENDPOINTS', '{}')),
            command_config=args.cmd_config, timeout=args.command_timeout)

    @property
    def request_timeout(self):
        timeout = self.get_args().request_timeout
        if timeout is None:
            timeout = self.app_model.msg_timeout_ms / 1000.0
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Request timeout must be finite and positive')
        return timeout

    def process_init(self):
        self.tm_endpoint.start()
        return Action().set_timer_action(Action.Timer(POLL_TIMER, POLL_SECONDS * 1000))

    @property
    def tm_connected(self):
        connection = self._tm_connection
        return connection is not None and self.tm_endpoint.validate_client_socket(connection)[0]

    def _model_cache(self):
        with self._requests_lock:
            if not hasattr(self, '_telescope_cache'):
                self._telescope_cache = TelescopeModelCache()
            return self._telescope_cache

    def telescope_model_response(self):
        return self._model_cache().response(self.tm_connected)

    def process_resync(self):
        self._load_configuration()
        self.profile_manager.reload()
        return Action()

    def process_config(self, event):
        return Action()

    def process_status_event(self, event):
        self.get_app_processor_state()
        self.web_model.last_update = datetime.now(timezone.utc)
        action = Action()
        if self.tm_connected and self.web_model.tm_connected == CommunicationStatus.ESTABLISHED:
            action.set_msg_to_remote(self._construct_status_adv_to_tm())
        return action

    def _construct_status_adv_to_tm(self):
        message = APIMessage(api_version=self.tm_api.get_api_version())
        message.set_json_api_header(
            api_version=self.tm_api.get_api_version(), dt=datetime.now(timezone.utc),
            from_system=protocol.WEB, to_system=protocol.TM,
            api_call={'msg_type': protocol.MSG_TYPE_ADV, 'action_code': protocol.ACTION_CODE_SET,
                      'property': protocol.PROPERTY_STATUS, 'value': self.web_model.to_dict(),
                      'message': 'WEB status update'},
        )
        return message

    def get_health_state(self):
        return HealthState.OK if self.tm_connected else HealthState.DEGRADED

    def process_tm_connected(self, event):
        with self._requests_lock:
            if not self.tm_endpoint.validate_client_socket(event.remote_conn)[0]:
                return Action()
            previous = self._tm_connection
            self._tm_connection = event.remote_conn
            # There is one coordinator. A replacement must not leave a second
            # socket eligible for the server's default outbound connection.
            if previous is not None and previous is not event.remote_conn:
                self.tm_endpoint._process_disconnect(previous)
        self.web_model.tm_connected = CommunicationStatus.ESTABLISHED
        self.set_health_state(self.get_health_state())
        action = self.process_status_event(event)
        reset = self._model_cache().reconnected()
        action.timer_actions.extend(reset.timer_actions)
        poll = self._model_cache().poll(True, self.request_timeout)
        action.msgs_to_remote.extend(poll.msgs_to_remote)
        action.timer_actions.extend(poll.timer_actions)
        return action

    def _request_timers(self):
        if Timer.manager is None:
            return []
        return [timer for timer in Timer.manager.get_timers_by_keyword(TIMER_PREFIX)
                if timer.event_q is self.get_queue()]

    def process_tm_disconnected(self, event):
        # An older socket's disconnect must not fail requests on a new connection.
        with self._requests_lock:
            if event.remote_conn is not self._tm_connection:
                return Action()
            self._tm_connection = None
            self.web_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED
            for timer in self._request_timers():
                timer.user_ref.complete(ResponseEvent(
                    'error', 'TM disconnected; configuration outcome is unknown', code='connection_lost'))
                timer.cancel()
        return self._model_cache().disconnected()

    def submit_configuration(self, category, config):
        request_id = uuid4().hex
        try:
            message = tm_web.configuration_request(category, config, request_id)
        except Exception as error:
            return ResponseEvent('error', str(error), code='invalid_request'), request_id
        with self._requests_lock:
            if (not self._accepting_requests or not self.app_model.app_running
                    or not self.tm_connected):
                return ResponseEvent('error', 'TM is not connected; configuration was not sent',
                                     code='not_connected'), request_id
            request = HTTPConfigRequest(request_id, message, time.monotonic() + self.request_timeout)
            self.get_queue().put(CallbackEvent(lambda: self._send_configuration(request), user_ref=request,
                                              lock=self._requests_lock))
        try:
            # The request timer normally resolves the Future. This watchdog also
            # bounds HTTP waits if processors stop or the submission queue stalls.
            return request.future.result(timeout=self.request_timeout + 1), request_id
        except TimeoutError:
            with request.lock:
                request.complete(self._timeout_result(request))
            for timer in self._request_timers():
                if timer.name == request.timer_name:
                    timer.cancel()
            return request.future.result(), request_id

    @staticmethod
    def _timeout_result(request):
        return ResponseEvent(
            'error', ('TM response timed out; configuration outcome is unknown' if request.sent
                      else 'Configuration timed out before sending; it was not sent'),
            code='timeout')

    def _send_configuration(self, request):
        with self._requests_lock, request.lock:
            if request.future.done():
                return Action()
            remaining = request.deadline - time.monotonic()
            if remaining <= 0:
                request.complete(self._timeout_result(request))
                return Action()
            if not self._accepting_requests or not self.tm_connected:
                request.complete(ResponseEvent('error', 'TM is not connected; configuration was not sent',
                                               code='not_connected'))
                return Action()
            request.sent = True
            # AppProcessor applies timer actions before sending message actions.
            return (Action().set_timer_action(Action.Timer(
                name=request.timer_name, timer_action=max(1, int(remaining * 1000)), echo_data=request))
                .set_msg_to_remote(request.message))

    def process_tm_msg(self, event, api_msg, api_call, payload):
        if api_call['msg_type'] != 'rsp':
            return Action()
        if api_call.get('property') == tm_web.PROPERTY_MODEL:
            return self._model_cache().complete(api_msg, api_call)
        name = TIMER_PREFIX + tm_web.get_request_id(api_msg)
        with self._requests_lock:
            for timer in self._request_timers():
                if timer.name == name and timer.is_active():
                    request = timer.user_ref
                    with request.lock:
                        if time.monotonic() >= request.deadline:
                            request.complete(self._timeout_result(request))
                        else:
                            request.complete(ResponseEvent(
                                api_call['status'], api_call['message'],
                                payload=api_call.get('payload'), code=api_call.get('code')))
                        timer.cancel()
                    break
        return Action()

    def process_timer_event(self, event):
        if event.name == POLL_TIMER and not event.timer_cancelled:
            poll = self._model_cache().poll(self.tm_connected, self.request_timeout)
            return poll.set_timer_action(Action.Timer(POLL_TIMER, POLL_SECONDS * 1000))
        if event.name.startswith(REQUEST_PREFIX) and not event.timer_cancelled:
            self._model_cache().timeout(event.user_ref)
            return Action()
        if event.name.startswith(TIMER_PREFIX) and not event.timer_cancelled:
            event.user_ref.complete(self._timeout_result(event.user_ref))
        return Action()

    def start_http_server(self):
        # One application process owns the TCP connection and timer manager;
        # HTTP workers are threads within that process.
        from waitress import create_server
        args = self.get_args()
        self.http_server = create_server(self.http_app, map=self._http_map, host=args.http_host, port=args.http_port,
                                          threads=args.http_threads)
        self.http_server.print_listen('Web application listening on http://{}:{}')
        self.http_thread = threading.Thread(target=self.http_server.run, name='web-http', daemon=True)
        self.http_thread.start()
        if not getattr(args, 'no_ui', True):
            self.start_ui_server()

    def start_ui_server(self):
        from waitress import create_server
        from ui.opt_service import create_app
        from web.security import init_security
        args = self.get_args()
        if args.ui_threads < 1:
            raise ValueError('UI worker count must be positive')
        host = '127.0.0.1' if args.http_host in ('0.0.0.0', '') else args.http_host
        port = self.http_server.effective_port
        self.ui_app = create_app(args.ui_data_root, args.ui_cache_dir,
                                 web_url=f'http://{host}:{port}',
                                 profile_provider=lambda: self.profile_manager.current.to_dict())
        init_security(self.ui_app, args.ui_state_dir)
        self.ui_server = create_server(self.ui_app, map=self._ui_map,
            host='127.0.0.1', port=args.ui_port, threads=args.ui_threads)
        self.ui_server.print_listen('Private UI listening on http://{}:{}')
        self.ui_thread = threading.Thread(target=self.ui_server.run, name='web-UI', daemon=True)
        self.ui_thread.start()

    @staticmethod
    def _stop_listener(server, connection_map, thread):
        from waitress import wasyncore

        # Let workers finish while their owning event loop can still send replies.
        server.task_dispatcher.shutdown(timeout=2)
        if thread is not None and thread.is_alive():
            trigger = getattr(server, 'trigger', None)
            if trigger is None:
                # Waitress can wrap multiple listening sockets in one server.
                trigger = next((getattr(channel, 'trigger', None)
                                for channel in list(connection_map.values())
                                if hasattr(getattr(channel, 'trigger', None), 'pull_trigger')), None)
            if trigger is None:
                raise RuntimeError('Cannot wake WEB listener for shutdown')
            # Closing descriptors from this thread races with select() in the
            # listener. Trigger callbacks run on the listener's own event loop.
            trigger.pull_trigger(lambda: wasyncore.close_all(connection_map))
            thread.join(timeout=3)
            if thread.is_alive():
                raise RuntimeError(f'WEB listener {thread.name} did not stop')
        else:
            # Startup may fail before an event-loop thread has been started.
            server.close()
            wasyncore.close_all(connection_map)

    def stop(self):
        if getattr(self, '_stopped', False):
            return
        self._stopped = True
        if not hasattr(self, 'queue'):
            return
        with self._requests_lock:
            self._accepting_requests = False
            requests = [timer.user_ref for timer in self._request_timers()]
            # Queued submissions have not yet acquired their request timer.
            with self.queue.mutex:
                requests.extend(event.user_ref for event in self.queue.queue
                                if isinstance(event, (CallbackEvent, TimerEvent)) and isinstance(event.user_ref, HTTPConfigRequest))
            for request in requests:
                request.complete(ResponseEvent('error', 'Web application is shutting down; outcome may be unknown',
                                               code='shutting_down'))
        try:
            for server, connection_map, thread in (
                (getattr(self, 'ui_server', None), getattr(self, '_ui_map', {}), getattr(self, 'ui_thread', None)),
                (self.http_server, self._http_map, self.http_thread),
            ):
                if server is not None:
                    try:
                        self._stop_listener(server, connection_map, thread)
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception('Error stopping WEB listener')
            if getattr(self, 'ui_app', None) is not None:
                self.ui_app.extensions['opt_jobs'].close()
        finally:
            super().stop()
