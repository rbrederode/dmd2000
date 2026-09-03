"""
Webhook handler module for Telescope Manager.

This module provides a lightweight Flask REST API that runs in a separate thread
to receive webhook payloads and inject them into the TM event queue.
"""

import logging
import threading
from flask import Flask, request, jsonify
from datetime import datetime, timezone
from queue import Queue

logger = logging.getLogger(__name__)

class WebhookHandler:
    """
    REST API handler that runs Flask in a background thread.
    Receives webhook payloads and pushes them to the TM event queue.
    """
    
    def __init__(self, event_queue: Queue, host='127.0.0.1', port=5001):
        """
        Initialize the webhook handler.
        
        Args:
            event_queue: The TM event queue to push webhook events to
            host: Flask host (default: localhost)
            port: Flask port (default: 5001)
        """
        self.event_queue = event_queue
        self.host = host
        self.port = port
        self.app = Flask(__name__)
        self.server_thread = None
        self._setup_routes()
        
    def _setup_routes(self):
        """Configure Flask routes."""
        
        @self.app.route('/webhook', methods=['POST'])
        def receive_webhook():
            """
            Receive webhook payload and inject into TM event queue.
            
            Expected JSON payload from webhook.py:
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
                    
                    # If message was parsed as JSON (dict), use it directly
                    if isinstance(message_data, dict):
                        logger.info(f"Webhook with parsed config: {message_data}")
                        
                        # Create ConfigEvent from the parsed message
                        from env.events import ConfigEvent
                        config_event = ConfigEvent(
                            category=category,
                            old_config=None,
                            new_config=message_data,
                            timestamp=datetime.fromisoformat(data.get('timestamp')) if data.get('timestamp') else datetime.now(timezone.utc)
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
            name="WebhookHandlerThread"
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
                    threads=4,  # Handle 4 concurrent requests
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
