"""Standalone web server receiving external webhooks and forwarding them to TM."""

from flask import Flask, request, jsonify, abort
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import hmac
import hashlib
import os
import logging
import requests
from datetime import datetime
from werkzeug.exceptions import HTTPException

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('webhook.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Load secrets from environment variables
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET environment variable must be set")

# Initialize rate limiter (100 requests per hour per IP)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["100 per hour"],
    storage_uri="memory://"
)

def verify_hmac_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify HMAC signature from webhook request.
    
    Args:
        payload: Raw request body bytes
        signature: Signature from request header
        secret: Shared secret key
        
    Returns:
        True if signature is valid, False otherwise
    """
    if not signature:
        return False
    
    # Remove 'sha256=' prefix if present (GitHub/Jira style)
    if signature.startswith('sha256='):
        signature = signature[7:]
    
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_signature, signature)

def validate_request():
    """
    Validate incoming webhook request for security.
    
    Checks:
    - Content-Type is application/json
    - Required headers are present
    - HMAC signature is valid (if provided)
    - Payload size is reasonable
    
    Raises:
        400/401/413 HTTP errors if validation fails
    """
    # Check Content-Type
    if request.content_type and 'application/json' not in request.content_type:
        logger.warning(f"Invalid Content-Type from {request.remote_addr}: {request.content_type}")
        abort(400, description="Content-Type must be application/json")
    
    # Check payload size (max 1MB)
    if request.content_length and request.content_length > 1024 * 1024:
        logger.warning(f"Payload too large from {request.remote_addr}: {request.content_length} bytes")
        abort(413, description="Payload too large")
    
    # Verify HMAC signature if provided
    signature = request.headers.get('X-Hub-Signature-256') or request.headers.get('X-Webhook-Signature')
    token = request.headers.get('X-Webhook-Token')
    
    if signature:
        # Get raw data for HMAC verification
        payload = request.get_data()
        try:
            valid_signature = verify_hmac_signature(payload, signature, WEBHOOK_SECRET)
        except (ValueError, TypeError):
            valid_signature = False
        if not valid_signature:
            logger.error("Invalid HMAC signature from %s", request.remote_addr)
            abort(401, description="Invalid signature")
        logger.info("Valid HMAC signature verified from %s", request.remote_addr)
    elif token:
        # Check for simple token authentication as fallback
        if not hmac.compare_digest(token.encode('utf-8'), WEBHOOK_SECRET.encode('utf-8')):
            logger.error(f"Invalid token from {request.remote_addr}")
            abort(401, description="Invalid authentication")
    else:
        logger.error(f"No authentication provided from {request.remote_addr}")
        abort(401, description="Missing authentication")

@app.before_request
def log_request():
    """Log all incoming requests for security monitoring."""
    logger.info(f"Request from {request.remote_addr}: {request.method} {request.path}")
    safe_headers = {
        name: '[REDACTED]' if any(part in name.lower() for part in
                                  ('token', 'authorization', 'cookie', 'signature', 'secret', 'api-key')) else value
        for name, value in request.headers.items()
    }
    logger.info("Headers: %s", safe_headers)
    logger.info(f"Content-Type: {request.content_type}")
    logger.info(f"Content-Length: {request.content_length}")

@app.route('/webhook', methods=['POST'])
@limiter.limit("100 per hour")
def webhook_receiver():
    """
    Secure webhook endpoint with authentication and rate limiting.
    
    Expected headers:
    - Content-Type: application/json
    - X-Hub-Signature-256: sha256=<hmac_signature> (recommended)
    OR
    - X-Webhook-Token: <secret_token> (fallback)
    
    Returns:
        JSON response with status
    """
    try:
        # Validate request security
        validate_request()
        
        # Get and validate JSON data
        try:
            data = request.get_json(force=True)
        except Exception as json_error:
            logger.warning(f"JSON parsing error from {request.remote_addr}: {json_error}")
            abort(400, description="Invalid JSON payload")
        
        if not isinstance(data, dict) or not data:
            logger.warning(f"Empty payload from {request.remote_addr}")
            abort(400, description="Send a non-empty JSON object")
        
        # Log successful webhook receipt
        logger.info(f"Valid webhook received from {request.remote_addr}")
        
        # Forward webhook data to TM application
        is_command = data.get('event') == 'alston-rt.ui.cmd'
        try:
            tm_response = requests.post(
                'http://localhost:5001/webhook',
                json=data,
                timeout=20 if is_command else 5,
                allow_redirects=False
            )
            if is_command:
                # Preserve TM's command result, including target rejection and
                # timeout details, through the authenticated external endpoint.
                try:
                    result = tm_response.json()
                except ValueError:
                    return jsonify(status='error', message='Invalid command response from TM'), 502
                if (not isinstance(result, dict) or 'status' not in result
                        or tm_response.status_code not in (200, 400, 500, 502, 504)):
                    return jsonify(status='error', message='Invalid command response from TM'), 502
                return jsonify(result), tm_response.status_code
            
            if 200 <= tm_response.status_code < 300:
                logger.info("Successfully forwarded webhook to TM application")
            else:
                logger.warning("TM application returned status %s", tm_response.status_code)
                return jsonify(status='error', message='Telescope Manager rejected the webhook',
                               upstream_status=tm_response.status_code), 502
                
        except requests.exceptions.Timeout:
            logger.error("Timeout connecting to TM application")
            return jsonify(status='error', message='Telescope Manager request timed out'), 504
        except requests.exceptions.ConnectionError:
            logger.error("Failed to connect to TM application at localhost:5001 - is it running?")
            return jsonify(status='error', message='Could not connect to Telescope Manager'), 502
        except requests.exceptions.RequestException:
            logger.error("Error forwarding to TM application")
            return jsonify(status='error', message='Could not forward webhook to Telescope Manager'), 502
        
        return jsonify({
            'status': 'success',
            'message': 'Webhook forwarded to Telescope Manager',
            'timestamp': datetime.utcnow().isoformat()
        }), 200
        
    except HTTPException as error:
        return jsonify(status='error', message=error.description), error.code
    except Exception:
        logger.error("Error processing webhook from %s", request.remote_addr)
        return jsonify({
            'status': 'error',
            'message': 'Internal server error'
        }), 500

@app.get('/api/observations')
def observations():
    """Authenticate catalogue requests and retrieve metadata from TM."""
    try:
        validate_request()
    except HTTPException as error:
        return jsonify(status='error', message=error.description), error.code
    try:
        query_options = ({'params': {'directory': request.args['directory']}}
                         if 'directory' in request.args else {})
        response = requests.get('http://localhost:5001/api/observations',
                                timeout=5, allow_redirects=False, **query_options)
    except requests.exceptions.Timeout:
        return jsonify(status='error', message='Telescope Manager request timed out'), 504
    except requests.exceptions.RequestException:
        return jsonify(status='error', message='Could not connect to Telescope Manager'), 502
    if response.status_code == 503:
        return jsonify(status='error', message='Observation search directory is unavailable or not known to TM'), 503
    if response.status_code == 400:
        return jsonify(status='error', message='Invalid observation search directory'), 400
    if response.status_code != 200:
        return jsonify(status='error', message='Could not retrieve observations from Telescope Manager',
                       upstream_status=response.status_code), 502
    try:
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('observations'), list):
            raise ValueError('Invalid catalogue response')
    except ValueError:
        return jsonify(status='error', message='Invalid observation catalogue from Telescope Manager'), 502
    return jsonify(payload)


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint (no authentication required)."""
    return jsonify({'status': 'healthy'}), 200

@app.errorhandler(429)
def ratelimit_handler(e):
    """Handle rate limit exceeded."""
    logger.warning(f"Rate limit exceeded for {request.remote_addr}")
    return jsonify({
        'status': 'error',
        'message': 'Rate limit exceeded'
    }), 429

if __name__ == '__main__':
    # Never run with debug=True in production
    # Use a production WSGI server like gunicorn instead
    logger.info("Starting webhook server...")
    logger.warning("Running in development mode - use gunicorn for production")
    app.run(host='127.0.0.1', port=8000, debug=False)
