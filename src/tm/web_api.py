"""
Web API handler module for Telescope Manager.

This module provides a lightweight Flask REST API that runs in a separate thread
to receive webhook payloads and inject them into the TM event queue.
"""

import logging
import json
import threading
from flask import Flask, request, jsonify
from werkzeug.exceptions import HTTPException
from datetime import datetime, timezone
from queue import Queue
from env.events import ResponseEvent
from obs.catalogue import list_observations

logger = logging.getLogger(__name__)


def _event_response_http_status(response: ResponseEvent) -> int:
    """Translate application results at the HTTP boundary only."""
    if response.status == 'success':
        return 200
    return {'invalid_request': 400, 'store_not_known': 503,
            'directory_unavailable': 503}.get(response.code, 500)


def _json_response(result: ResponseEvent):
    return jsonify(result.to_dict()), _event_response_http_status(result)


class WebAPIHandler:
    """
    REST API handler that runs Flask in a background thread.
    Receives webhook payloads and pushes them to the TM event queue.
    """
    
    def __init__(self, event_queue: Queue, host='127.0.0.1', port=5001,
                 telmodel=None, threads=4):
        """
        Initialize the webhook handler.
        
        Args:
            event_queue: The TM event queue to push webhook events to
            host: Flask host (default: localhost)
            port: Flask port (default: 5001)
            threads: Number of Waitress request workers (default: 4).
            telmodel: Live telescope model shared with TM, read by API handlers.
                Model changes must be requested through the event queue.
        """
        if threads < 1:
            raise ValueError('Web API threads must be at least 1')
        self.threads = threads
        self.event_queue = event_queue
        self.host = host
        self.port = port
        self.telmodel = telmodel
        self.app = Flask(__name__)
        self.app.register_error_handler(Exception, self._handle_error)
        self.server_thread = None
        self._setup_routes()

    def _handle_error(self, error):
        """Keep unhandled API failures in the common response format."""
        if isinstance(error, HTTPException):
            result = ResponseEvent('error', error.description, code='invalid_request')
            return jsonify(result.to_dict()), error.code
        logger.exception('Unhandled Web API error')
        return _json_response(ResponseEvent('error', 'Internal server error', code='internal_error'))
        
    def _setup_routes(self):
        """Configure Flask routes."""

        @self.app.get('/api/observations')
        def observations():
            """Recursively list metadata in an explicit directory or SDP's store."""
            if 'directory' in request.args:
                store = request.args['directory'].strip()
                if not store:
                    return _json_response(ResponseEvent('error', 'Directory must not be empty', code='invalid_request'))
            else:
                # Read the current SDP on every request: status updates may
                # replace this submodel while the telescope model stays shared.
                sdp = self.telmodel.sdp if self.telmodel is not None else None
                arguments = sdp.app.arguments if sdp is not None else None
                store = (arguments or {}).get('scan_store_dir')
            if not store:
                return _json_response(ResponseEvent('error', 'SDP sample store is not known yet', code='store_not_known'))
            return _json_response(list_observations(store, service="TM"))
        
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
                
                if event_type == 'alston-rt.ui.cmd':
                    return _json_response(ResponseEvent(
                        'error', 'TM no longer accepts CMD webhooks; use web_server or cmd_app',
                        code='invalid_request',
                    ))

                if event_type in ['alston-rt.ui.dig', 'alston-rt.ui.odt', 'alston-rt.ui.dsh', 'alston-rt.ui.oet']:
                    message_data = data.get('message', '')
                    
                    # Extract rightmost 3 characters and convert to uppercase
                    category = event_type[-3:].upper()
                    
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
                        )
                        
                        self.event_queue.put(config_event)
                        logger.info(f"Injected {category} config event into TM queue")
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
