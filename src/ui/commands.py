"""Command choices and validation for the private application console."""
import os

from api.cmd_registry import CommandRegistry, DEFAULT_COMMAND_CONFIG
from util.cmd_app import normalise_command_config

APPLICATIONS = frozenset(('tm', 'dm', 'sdp', 'ws', 'web', 'dig'))


def registry(application):
    return CommandRegistry.load(application, os.getenv('WEB_CMD_CONFIG', str(DEFAULT_COMMAND_CONFIG)))


def choices(application):
    commands = [{'id': 'resync', 'cmd': 'resync', 'label': 'RESYNC',
                 'description': 'Reload application configuration'}]
    for command in ('trace', 'debug'):
        for value in (None, 'ON', 'OFF'):
            commands.append({'id': command + '-' + (value or 'get').lower(), 'cmd': command,
                             'label': command.upper() + ' ' + (value or '?'),
                             **({'value': value} if value else {})})
    configured = registry(application)
    for name in sorted(configured.command_names):
        spec = configured.get_spec(name)
        commands.append({'id': name, 'cmd': name, 'label': name.upper(),
                         'description': spec.description, 'takes_value': True,
                         'value_required': spec.value_required, 'value_type': spec.value_type,
                         'value_pattern': spec.value_pattern})
    return commands


def validate(target, command, value):
    application = target.lower() if target.lower() in APPLICATIONS else 'dig'
    if command == 'resync' and value is not None:
        raise ValueError('RESYNC does not accept a value')
    normalise_command_config({'app': target, 'cmd': command, 'value': value}, registry(application))
