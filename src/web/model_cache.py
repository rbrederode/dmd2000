"""Timer-driven telescope snapshots shared by every HTTP/browser caller."""

from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from uuid import uuid4

from api import tm_web
from env.events import ResponseEvent
from ipc.action import Action

POLL_TIMER = 'web_telescope_model_poll'
REQUEST_PREFIX = 'web_telescope_model_request:'
POLL_SECONDS = 30


class TelescopeModelCache:
    def __init__(self):
        self.lock = threading.RLock()
        self.pending = None
        self.deadline = None
        self.snapshot = None
        self.received_at = None
        self.received_monotonic = None
        self.error = 'Waiting for the first telescope model snapshot'
        self.waiters = []

    def poll(self, connected, timeout, force=False, waiter=None):
        with self.lock:
            self.waiters = [item for item in self.waiters if not item.done()]
            if waiter is not None:
                if not connected:
                    waiter.set_result(ResponseEvent('error', 'TM is disconnected', code='not_connected'))
                    return Action()
                self.waiters.append(waiter)
            action = Action()
            if connected and force and self.pending is not None:
                action.set_timer_action(Action.Timer(REQUEST_PREFIX + self.pending, Action.Timer.TIMER_STOP))
                self.pending = None
            if not connected or self.pending is not None:
                return action
            self.pending = uuid4().hex
            self.deadline = time.monotonic() + timeout
            return (action.set_timer_action(Action.Timer(
                REQUEST_PREFIX + self.pending, max(1, int(timeout * 1000)), echo_data=self.pending))
                .set_msg_to_remote(tm_web.model_request(self.pending)))

    def complete(self, message, call):
        with self.lock:
            request_id = tm_web.get_request_id(message)
            if self.pending is None or request_id != self.pending:
                return Action()
            timer = REQUEST_PREFIX + self.pending
            self.pending = None
            if time.monotonic() >= self.deadline:
                self.error = 'TM telescope model request timed out'
            elif call.get('status') != 'success':
                self.error = call.get('message') or 'TM telescope model request failed'
            else:
                payload = call.get('payload')
                if not isinstance(payload, dict) or not isinstance(payload.get('telescope_model'), dict):
                    self.error = 'TM returned an invalid telescope model snapshot'
                else:
                    self.snapshot = deepcopy(payload)
                    self.received_at = datetime.now(timezone.utc).isoformat()
                    self.received_monotonic = time.monotonic()
                    self.error = None
            self.deadline = None
            self._finish_waiters(self.response(True) if self.error is None else
                ResponseEvent('error', self.error, code='snapshot_failed'))
            return Action().set_timer_action(Action.Timer(timer, Action.Timer.TIMER_STOP))

    def _finish_waiters(self, result):
        for waiter in self.waiters:
            if not waiter.done():
                waiter.set_result(result)
        self.waiters.clear()

    def timeout(self, request_id):
        with self.lock:
            if request_id == self.pending:
                self.pending = None
                self.deadline = None
                self.error = 'TM telescope model request timed out'
                self._finish_waiters(ResponseEvent('error', self.error, code='timeout'))

    def disconnected(self):
        with self.lock:
            action = Action()
            if self.pending is not None:
                action.set_timer_action(Action.Timer(REQUEST_PREFIX + self.pending, Action.Timer.TIMER_STOP))
            self.pending = self.deadline = None
            self.error = 'TM is disconnected'
            self._finish_waiters(ResponseEvent('error', self.error, code='not_connected'))
            return action

    def reconnected(self):
        with self.lock:
            action = self.disconnected()
            self.error = 'Waiting for a fresh TM snapshot'
            return action

    def response(self, connected):
        with self.lock:
            if self.snapshot is None:
                return ResponseEvent('error', self.error, code='model_unavailable',
                    payload={'tm_connected': connected, 'stale': True, 'poll_interval_seconds': POLL_SECONDS})
            age = max(0.0, time.monotonic() - self.received_monotonic)
            stale = not connected or self.error is not None or age > POLL_SECONDS + 5
            payload = dict(deepcopy(self.snapshot), received_at=self.received_at,
                           age_seconds=round(age, 3), stale=stale, tm_connected=connected,
                           poll_interval_seconds=POLL_SECONDS, last_error=self.error)
            return ResponseEvent('success', 'Cached telescope model snapshot' if not stale
                                 else 'Last telescope model snapshot is stale', payload=payload)
