"""Read current application logs/traces from a configured root for the private UI."""
from datetime import datetime, timezone
import errno
import os
from pathlib import Path
import re
import stat

from flask import Blueprint, jsonify, request, send_file

LOG_TAIL_BYTES = 512 * 1024
TRACE_MAX_BYTES = 32 * 1024 * 1024
APPLICATIONS = frozenset(('tm', 'dm', 'sdp', 'ws', 'web', 'dig'))


def register_diagnostics(app, profile_provider):
    api = Blueprint('diagnostics', __name__)

    @api.get('/api/diagnostics/<application>/<kind>')
    def read_file(application, kind):
        def error(message, code, status):
            return jsonify(status='error', message=message, code=code), status

        if application not in APPLICATIONS or kind not in ('log', 'trace'):
            return error('Choose an application log or trace', 'not_found', 404)
        entity = request.args.get('entity', '')
        if entity not in ('', '<undefined>') and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', entity):
            return error('Choose a valid application entity ID', 'invalid_request', 400)
        selection = profile_provider()
        profile, root = selection.get('profile'), selection.get('log_dir')
        if not isinstance(profile, str) or not isinstance(root, str) or not root.strip():
            return error('The selected profile has no log directory', 'not_configured', 503)
        if 'profile' in request.args and request.args['profile'] != profile:
            return error('The configuration profile changed. Refresh this file.', 'profile_changed', 409)
        folder = 'app' if kind == 'log' else 'trace'
        basename = entity if kind == 'trace' and entity not in ('', '<undefined>') else application
        filename = basename + ('.log' if kind == 'log' else '.html')
        # Open each component relative to a directory descriptor, refusing linked
        # subdirectories/files. The browser cannot provide arbitrary file paths.
        try:
            root_fd = os.open(Path(root).expanduser().resolve(), os.O_RDONLY | os.O_DIRECTORY)
            try:
                folder_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
                finally:
                    os.close(folder_fd)
            finally:
                os.close(root_fd)
            stream = os.fdopen(descriptor, 'rb')
        except FileNotFoundError:
            return error(f'No {kind} file is available for this application in profile {profile}. '
                         + ('Enable tracing and restart older application instances if necessary.' if kind == 'trace' else ''),
                         'not_found', 404)
        except OSError as exc:
            blocked = exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM)
            return error('The diagnostic file cannot be accessed', 'file_unavailable', 403 if blocked else 503)
        with stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return error('The diagnostic file is not a regular file', 'file_unavailable', 403)
            if request.args.get('download') == '1':
                # send_file owns this duplicated descriptor until the response closes.
                download = os.fdopen(os.dup(stream.fileno()), 'rb')
                response = send_file(download, mimetype='application/octet-stream', as_attachment=True,
                                     download_name=filename, conditional=False)
                response.call_on_close(download.close)
                response.headers['Cache-Control'] = 'no-store'
                return response
            if kind == 'trace' and info.st_size > TRACE_MAX_BYTES:
                return error('This trace is too large to display. Download it to view the full file.', 'file_too_large', 413)
            start = max(0, info.st_size - LOG_TAIL_BYTES) if kind == 'log' else 0
            stream.seek(start)
            data = stream.read(info.st_size - start)
            if start and b'\n' in data:
                data = data.split(b'\n', 1)[1]
        response = jsonify(status='success', message=f'Loaded application {kind}', payload={
            'profile': profile, 'application': application, 'entity': entity,
            'file': folder + '/' + filename, 'format': 'html' if kind == 'trace' else 'text',
            'modified_at': datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            'size_bytes': info.st_size, 'truncated': bool(start),
            'shared_log': application == 'dig' and kind == 'log',
            'content': data.decode('utf-8', errors='replace'),
        })
        response.headers['Cache-Control'] = 'no-store'
        return response

    app.register_blueprint(api)
