"""
Web API handler module for Telescope Manager.

This module provides a lightweight Flask REST API that runs in a separate thread
to receive webhook payloads and inject them into the TM event queue.
"""

import logging
import json
import os
import threading
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from uuid import uuid4
from pathlib import Path
from flask import Flask, request, jsonify
from datetime import datetime, timezone
from queue import Queue
from env.events import ResponseEvent

logger = logging.getLogger(__name__)


def _event_response_http_status(response: ResponseEvent) -> int:
    """Translate application results at the HTTP boundary only."""
    if response.status == 'success':
        return 200
    return {'invalid_request': 400, 'timeout': 504,
            'command_failed': 502, 'dispatch_failed': 500}.get(response.code, 500)


class WebAPIHandler:
    """
    REST API handler that runs Flask in a background thread.
    Receives webhook payloads and pushes them to the TM event queue.
    """
    
    def __init__(self, event_queue: Queue, host='127.0.0.1', port=5001,
                 scan_store_provider=None, threads=4, command_wait_timeout=15.0):
        """
        Initialize the webhook handler.
        
        Args:
            event_queue: The TM event queue to push webhook events to
            host: Flask host (default: localhost)
            port: Flask port (default: 5001)
            threads: Number of Waitress request workers (default: 4).
            command_wait_timeout: Maximum seconds an HTTP worker waits for CMD.
            scan_store_provider: Callable returning SDP's current scan-store path,
                or None until it is known. The directory must be accessible to TM.
        """
        if threads < 1:
            raise ValueError('Web API threads must be at least 1')
        self.threads = threads
        if command_wait_timeout <= 0:
            raise ValueError('Command wait timeout must be positive')
        self.command_wait_timeout = command_wait_timeout
        self.event_queue = event_queue
        self.host = host
        self.port = port
        self.scan_store_provider = scan_store_provider
        self.app = Flask(__name__)
        self.server_thread = None
        self._setup_routes()
        
    def _setup_routes(self):
        """Configure Flask routes."""

        @self.app.get('/api/observations')
        def observations():
            """Recursively list metadata in an explicit directory or SDP's store."""
            if 'directory' in request.args:
                store = request.args['directory'].strip()
                if not store:
                    return jsonify(status='error', message='Directory must not be empty'), 400
            else:
                store = self.scan_store_provider() if self.scan_store_provider else None
            if not store:
                return jsonify(status='error', message='SDP sample store is not known yet'), 503
            try:
                root = Path(store).expanduser().resolve(strict=True)
                if not root.is_dir():
                    raise NotADirectoryError

                def walk_error(error):
                    # Report an incomplete search rather than silently omit a subtree.
                    raise error

                paths = []
                for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
                    parent = Path(folder)
                    dirs[:] = [name for name in dirs if not (parent / name).is_symlink()]
                    paths.extend(parent / name for name in files
                                 if name.endswith('-obs.json')
                                 and not (parent / name).is_symlink()
                                 and (parent / name).is_file())
                paths.sort(key=lambda path: path.relative_to(root).as_posix())
            except (OSError, RuntimeError, ValueError):
                logger.warning('Observation search directory is unavailable to TM')
                return jsonify(status='error', message='Observation search directory is unavailable to TM'), 503

            entries = []
            for path in paths:
                entry = {'id': path.name.removesuffix('-obs.json'), 'filename': path.name,
                         'folder': path.parent.relative_to(root).as_posix()}
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(data, dict) or data.get('_type') != 'ObsModel':
                        raise ValueError('Expected observation metadata')
                    def value(field):
                        raw = data.get(field)
                        return raw.get('value') if isinstance(raw, dict) else raw
                    entry.update(id=data.get('obs_id') or entry['id'],
                                 title=data.get('title', ''), dish=data.get('dsh_id'),
                                 state=value('obs_state'), started_at=value('start_dt'))
                except (OSError, ValueError, UnicodeError):
                    # A file may be incomplete while TM is updating it. Keep the
                    # rest of the catalogue available and let the next request retry.
                    entry['error'] = 'Observation metadata is unavailable or invalid'
                    logger.warning('Could not read observation metadata: %s', path.name)
                entries.append(entry)
            return jsonify(observations=entries, count=len(entries))
        
        @self.app.route('/webhook', methods=['POST'])
        def receive_webhook():
            """
            Receive webhook payload and inject into TM event queue.
            
            Expected JSON payload from web_server.py:
            {
                "event": "cell_edited" | "monitored_cell_edited" | "test",
                "timestamp": "ISO-8601 timestamp",
                "sheet_name": "Sheet name",
                "cell": {
                    "row": int,
                    "column": int,
                    "address": "A1 notation"
                },
                "values": {
                    "old": "previous value",
                    "new": "new value"
                },
                "user": "email@example.com"
            }
            
            Returns:
                JSON response with status
            """
            try:
                data = request.get_json()
                
                if not data:
                    logger.warning("Received empty webhook payload")
                    return jsonify({'status': 'error', 'message': 'Empty payload'}), 400
                if not isinstance(data, dict):
                    return jsonify(status='error', message='Payload must be a JSON object'), 400
                
                logger.info(f"Received webhook event: {data.get('event', 'unknown')}")
                logger.debug(f"Webhook data: {data}")
                
                # If 'message' field contains a JSON string, parse it
                if 'message' in data and isinstance(data['message'], str):
                    try:
                        import json
                        parsed_message = json.loads(data['message'])
                        data['message'] = parsed_message
                        logger.info(f"Parsed nested JSON in message field: {parsed_message}")
                    except json.JSONDecodeError:
                        # Not JSON, leave as-is
                        logger.debug(f"Message field is not JSON: {data['message']}")
                
                # Create a ConfigEvent or custom webhook event from the payload
                # This depends on what you want to do with the webhook data
                
                event_type = data.get('event', 'unknown')
                
                if event_type in ['alston-rt.ui.cmd', 'alston-rt.ui.dig', 'alston-rt.ui.odt', 'alston-rt.ui.dsh', 'alston-rt.ui.oet']:
                    message_data = data.get('message', '')
                    
                    # Extract rightmost 3 characters and convert to uppercase
                    category = event_type[-3:].upper()
                    if category == 'CMD' and not isinstance(message_data, dict):
                        return jsonify(status='error', message='Command message must be a JSON object'), 400
                    
                    # If message was parsed as JSON (dict), use it directly
                    if isinstance(message_data, dict):
                        logger.info(f"Webhook with parsed config: {message_data}")
                        
                        # Create ConfigEvent from the parsed message
                        from env.events import ConfigEvent
                        config_event = ConfigEvent(
                            category=category,
                            old_config=None,
                            new_config=message_data,
                            timestamp=datetime.fromisoformat(data.get('timestamp')) if data.get('timestamp') else datetime.now(timezone.utc),
                            rsp_future=Future() if category == 'CMD' else None,
                        )
                        
                        self.event_queue.put(config_event)
                        logger.info(f"Injected {category} config event into TM queue")
                        if config_event.rsp_future is not None:
                            request_id = uuid4().hex
                            try:
                                result = config_event.rsp_future.result(timeout=self.command_wait_timeout)
                            except FutureTimeoutError:
                                cancelled = config_event.rsp_future.cancel()
                                result = ResponseEvent(
                                    'error',
                                    ('Command timed out before dispatch and was cancelled'
                                     if cancelled else 'Command response timed out; outcome is unknown'),
                                    payload={'cancelled_before_dispatch': cancelled}, code='timeout',
                                )
                            return jsonify(dict(result.to_dict(), request_id=request_id)), _event_response_http_status(result)
                    else:
                        logger.info(f"Webhook received: {message_data}")
                
                else:
                    logger.warning(f"Unknown webhook event type: {event_type}")
                
                return jsonify({
                    'status': 'success',
                    'message': 'Webhook processed',
                    'timestamp': datetime.utcnow().isoformat()
                }), 200
                
            except Exception as e:
                logger.error(f"Error processing webhook: {e}", exc_info=True)
                return jsonify({'status': 'error', 'message': str(e)}), 500
        
        @self.app.route('/health', methods=['GET'])
        def health_check():
            """Health check endpoint."""
            return jsonify({'status': 'healthy', 'service': 'tm-webhook-handler'}), 200
    
    def _map_sheet_to_category(self, sheet_name: str) -> str:
        """
        Map Google Sheets sheet name to TM category.
        
        Args:
            sheet_name: Name of the Google Sheet
            
        Returns:
            Category string (DIG, ODT, SDP) or empty string if unknown
        """
        # Customize this mapping based on your Google Sheets structure
        sheet_mapping = {
            'Digitiser': 'DIG',
            'DIG': 'DIG',
            'Observations': 'ODT',
            'ODT': 'ODT',
            'SDP': 'SDP',
            'Science Data': 'SDP'
        }
        return sheet_mapping.get(sheet_name, '')
    
    def start(self, use_production_server=True):
        """
        Start the Flask server in a background thread.
        
        Args:
            use_production_server: If True, use waitress (production). If False, use Flask dev server.
        """
        if self.server_thread and self.server_thread.is_alive():
            logger.warning("Webhook handler already running")
            return
        
        self.use_production_server = use_production_server
        self.server_thread = threading.Thread(
            target=self._run_server,
            daemon=True,
            name="WebAPIHandlerThread"
        )
        self.server_thread.start()
        
        server_type = "waitress" if use_production_server else "Flask dev server"
        logger.info(f"Webhook handler started on {self.host}:{self.port} using {server_type}")
    
    def _run_server(self):
        """Run the Flask server (called in background thread)."""
        if self.use_production_server:
            # Use waitress for production - works perfectly in threads
            try:
                from waitress import serve
                
                logger.info(f"Starting waitress WSGI server on {self.host}:{self.port}")
                serve(
                    self.app,
                    host=self.host,
                    port=self.port,
                    threads=self.threads,
                    channel_timeout=30,
                    _quiet=False
                )
                
            except ImportError:
                logger.warning("waitress not installed, falling back to Flask dev server")
                logger.warning("Install waitress with: pip install waitress")
                self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
        else:
            # Use Flask development server
            self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
    
    def stop(self):
        """Stop the Flask server (graceful shutdown not easily supported by Flask dev server)."""
        # Note: Flask's built-in server doesn't support graceful shutdown easily
        # For production, use gunicorn with proper shutdown handling
        logger.info("Webhook handler stopping (note: Flask dev server doesn't support graceful shutdown)")
