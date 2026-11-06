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
from ipc.tcp_client import TCPClient
from models.comms import CommunicationStatus, InterfaceType
from models.health import HealthState
from models.launch import load_scan_store_dir
from models.web import WebServerModel
from util.timer import Timer
from web.cmd_handler import CommandHandler


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
        self._requests_lock = threading.RLock()
        self._accepting_requests = True
        self._stopped = False
        super().__init__(app_name=protocol.WEB, app_model=self.web_model.app)
        args = self.get_args()
        if args.http_threads < 1:
            raise ValueError('HTTP worker count must be positive')
        self.profile_dir = Path(args.profile_dir or Path(__file__).resolve().parents[1] / 'config' / args.profile).expanduser()
        self._load_configuration(initial=True)
        _ = self.request_timeout  # Validate the CLI/profile setting before startup.
        self.tm_api = tm_web.TM_WEB()
        self.tm_endpoint = TCPClient(description=protocol.TM, queue=self.get_queue(),
                                     host=args.tm_host, port=args.tm_port)
        self.register_interface(protocol.TM, self.tm_api, self.tm_endpoint, InterfaceType.APP_APP)
        if http_app is not None:
            http_app.extensions['web_application'] = self

    def add_args(self, parser):
        super().add_args(parser)
        parser.set_defaults(cmd_config=os.getenv('WEB_CMD_CONFIG', parser.get_default('cmd_config')))
        parser.add_argument('--tm_host', default=os.getenv('WEB_TM_HOST', '127.0.0.1'))
        parser.add_argument('--tm_port', type=int, default=int(os.getenv('WEB_TM_PORT', '50004')))
        parser.add_argument('--http_host', default='127.0.0.1')
        parser.add_argument('--http_port', type=int, default=8000)
        parser.add_argument('--http_threads', type=int, default=10)
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
        self.tm_endpoint.connect()
        return Action()

    def process_resync(self):
        self._load_configuration()
        return Action()

    def process_config(self, event):
        return Action()

    def process_status_event(self, event):
        self.get_app_processor_state()
        self.web_model.last_update = datetime.now(timezone.utc)
        action = Action()
        if self.tm_endpoint.connected and self.web_model.tm_connected == CommunicationStatus.ESTABLISHED:
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
        return HealthState.OK if self.tm_endpoint.connected else HealthState.DEGRADED

    def process_tm_connected(self, event):
        if not self.tm_endpoint.connected or event.remote_conn is not self.tm_endpoint.client_socket:
            return Action()
        self.web_model.tm_connected = CommunicationStatus.ESTABLISHED
        self.set_health_state(self.get_health_state())
        return self.process_status_event(event)

    def _request_timers(self):
        if Timer.manager is None:
            return []
        return [timer for timer in Timer.manager.get_timers_by_keyword(TIMER_PREFIX)
                if timer.event_q is self.get_queue()]

    def process_tm_disconnected(self, event):
        # An older socket's disconnect must not fail requests on a new connection.
        if self.tm_endpoint.connected and event.remote_conn is not self.tm_endpoint.client_socket:
            return Action()
        self.web_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        with self._requests_lock:
            for timer in self._request_timers():
                timer.user_ref.complete(ResponseEvent(
                    'error', 'TM disconnected; configuration outcome is unknown', code='connection_lost'))
                timer.cancel()
        return Action()

    def submit_configuration(self, category, config):
        request_id = uuid4().hex
        try:
            message = tm_web.configuration_request(category, config, request_id)
        except Exception as error:
            return ResponseEvent('error', str(error), code='invalid_request'), request_id
        with self._requests_lock:
            if (not self._accepting_requests or not self.app_model.app_running
                    or not self.tm_endpoint.connected):
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
            if not self._accepting_requests or not self.tm_endpoint.connected:
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
            if self.http_server is not None:
                self.http_server.close()
                try:
                    self.http_server.task_dispatcher.shutdown(timeout=2)
                finally:
                    from waitress import wasyncore
                    wasyncore.close_all(self._http_map)
            if self.http_thread is not None:
                self.http_thread.join(timeout=2)
        finally:
            super().stop()
