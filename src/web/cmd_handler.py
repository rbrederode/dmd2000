"""Execute authenticated web commands directly through the command TCP API.

Endpoint discovery reads a local/shared configuration profile, never TM's live
model. Explicit server-side overrides handle different network mount/host layouts.
"""

import json
import logging
import math
from pathlib import Path

from api.cmd_registry import CommandRegistry, DEFAULT_COMMAND_CONFIG
from env.events import ResponseEvent
from models.dig import DigitiserList, DigitiserModel
from models.dsh import DishManagerModel
from models.sdp import ScienceDataProcessorModel
from models.tm import TelescopeManagerModel
from models.ws import WeatherStationModel
from models.web import WebServerModel
from util import cmd_app


logger = logging.getLogger(__name__)
DEFAULT_PROFILE_DIR = Path(__file__).resolve().parents[1] / 'config' / 'default'
FIXED_TARGETS = {
    'tm': TelescopeManagerModel,
    'dm': DishManagerModel,
    'sdp': ScienceDataProcessorModel,
    'ws': WeatherStationModel,
    'web': WebServerModel,
}


class CommandHandler:
    """One command client/queue per call; safe to share between HTTP workers."""

    def __init__(self, profile_dir=DEFAULT_PROFILE_DIR, endpoint_overrides=None,
                 command_config=DEFAULT_COMMAND_CONFIG, timeout=5.0):
        self.profile_dir = Path(profile_dir).expanduser()
        self.command_config = Path(command_config).expanduser()
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('Command timeout must be a finite positive number')
        if endpoint_overrides is not None and not isinstance(endpoint_overrides, dict):
            raise ValueError('Command endpoints must be a JSON object keyed by application name')
        self.endpoint_overrides = {}
        for name, endpoint in (endpoint_overrides or {}).items():
            if not isinstance(name, str) or not isinstance(endpoint, dict):
                raise ValueError('Each command endpoint must be an object keyed by application name')
            host, port = endpoint.get('host'), endpoint.get('port')
            if (not isinstance(host, str) or not host.strip()
                    or type(port) is not int or not 1 <= port <= 65535):
                raise ValueError('Command endpoints require a host and port in 1..65535')
            # Fixed app names are known. Other explicitly configured endpoints
            # identify a digitiser using the existing UI's dig_id convention.
            self.endpoint_overrides[name.strip().lower()] = (host.strip(), port)

    def _resolve_endpoint(self, app_name):
        if not app_name:
            raise ValueError('No application name was specified')
        system = app_name if app_name in FIXED_TARGETS else 'dig'
        if app_name in self.endpoint_overrides:
            return system, *self.endpoint_overrides[app_name]
        if not self.profile_dir.is_dir():
            raise FileNotFoundError('Command profile directory is unavailable')

        if app_name in FIXED_TARGETS:
            model_type = FIXED_TARGETS[app_name]
            default_app = model_type().app
            path = self.profile_dir / f'{model_type.__name__}.json'
            model = (model_type.load_from_disk(input_dir=str(self.profile_dir), filename=path.name)
                     if path.exists() else model_type())
            app = model.app
        else:
            dig_store = DigitiserList.load_from_disk(
                input_dir=str(self.profile_dir), filename='DigitiserList.json')
            matches = [dig for dig in dig_store.dig_list if dig.dig_id.strip().lower() == app_name]
            if len(matches) != 1:
                raise ValueError(f'Unknown or ambiguous application name {app_name!r}')
            app = matches[0].app
            default_app = DigitiserModel().app

        host = app.app_cmd_host or default_app.app_cmd_host
        port = app.app_cmd_port or default_app.app_cmd_port
        if (not isinstance(host, str) or not host.strip()
                or type(port) is not int or not 1 <= port <= 65535):
            raise ValueError(f'Target application {app_name!r} has no valid command endpoint')
        return system, host, port

    def execute(self, config) -> ResponseEvent:
        """Resolve and run a UI command without forwarding it through TM."""
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except ValueError:
                return ResponseEvent('error', 'Command message is not valid JSON', code='invalid_request')
        if not isinstance(config, dict):
            return ResponseEvent('error', 'Command message must be a JSON object', code='invalid_request')
        requested_app = str(config.get('app') or '').strip()
        app_name = requested_app.lower()
        try:
            system, host, port = self._resolve_endpoint(app_name)
            registry = CommandRegistry.load(app_name=system, path=self.command_config)
            command, property_name, value = cmd_app.normalise_command_config(config, registry=registry)
        except ValueError as exc:
            logger.warning('Web command could not be prepared for %s: %s', app_name, exc)
            return ResponseEvent('error', str(exc), code='invalid_request')
        except Exception:
            logger.exception('Web command configuration could not be loaded for %s', app_name)
            return ResponseEvent('error', 'Could not load command configuration', code='internal_error')

        argv = ['--host', host, '--port', str(port), '--system', system,
                '--timeout', str(self.timeout), '--cmd_config', str(self.command_config), command]
        if property_name is not None:
            argv.append(property_name)
        if value is not None:
            argv.append(str(value))
        responses = []
        try:
            logger.info('Web server sending %s command to %s at %s:%s', command, app_name, host, port)
            status = cmd_app.main(argv, response_callback=responses.append)
        except (Exception, SystemExit):
            logger.exception('Web command failed for %s', app_name)
            return ResponseEvent('error', 'Command failed before a response was received', code='command_failed')
        if not responses:
            return ResponseEvent('error', 'Command completed without a response', code='command_failed')
        response = responses[-1]
        return ResponseEvent(
            status='success' if status == 0 else 'error',
            message=response.get('message', 'Command response received'),
            payload={'app': requested_app, 'response': response},
            code=('timeout' if response.get('error') == 'timeout'
                  else 'command_failed' if status != 0 else None),
        )
