"""Authenticate HTTP calls, dispatch TCP requests, and list stored observations."""

from flask import Flask, request, jsonify, abort, g, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import hmac
import hashlib
import os
import logging
import json
import time
from uuid import uuid4
from werkzeug.exceptions import HTTPException
from web.cmd_handler import CommandHandler, DEFAULT_PROFILE_DIR
from api.cmd_registry import DEFAULT_COMMAND_CONFIG
from env.events import ResponseEvent
from models.launch import load_scan_store_dir
from obs.catalogue import list_observations, read_observation, scan_spectrum_file
from web.web import WebServer
from web.dish_store import DishProfileStore, DishStoreError
from web.profiles import ProfileManager, ProfileError
from web.resource_store import DigitiserProfileStore, WeatherStationProfileStore
from web.profile_store import ProfileStoreError
from web.pipeline_store import PipelineProfileStore

# The application logging router owns file handlers under the configured log root.
logger = logging.getLogger(__name__)

app = Flask(__name__)


# Load secrets from environment variables
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')
if not WEBHOOK_SECRET:
    raise ValueError("WEBHOOK_SECRET environment variable must be set")

cmd_handler = CommandHandler(
    profile_dir=os.getenv('WEB_PROFILE_DIR', str(DEFAULT_PROFILE_DIR)),
    endpoint_overrides=json.loads(os.getenv('WEB_COMMAND_ENDPOINTS', '{}')),
    command_config=os.getenv('WEB_CMD_CONFIG', str(DEFAULT_COMMAND_CONFIG)),
    timeout=os.getenv('WEB_COMMAND_TIMEOUT', '5'),
)

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

def _trace_redact(value):
    if isinstance(value, dict):
        return {name: '[REDACTED]' if any(part in name.lower() for part in
                ('token', 'authorization', 'cookie', 'signature', 'secret', 'api-key', 'password'))
                else _trace_redact(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_trace_redact(item) for item in value]
    return value


def _http_trace():
    runtime = app.extensions.get('web_application')
    if runtime is None or not getattr(getattr(runtime, 'app_model', None), 'app_tracing', False):
        return None
    trace = getattr(runtime, 'trace', None)
    return trace if trace is not None and trace.is_started else None


def _trace_body(data, content_type):
    # Bound trace size; omit non-JSON bodies that may contain credentials.
    if len(data) > 65536:
        return '[Body omitted: exceeds 64 KiB]'
    if not data:
        return None
    if 'json' in (content_type or ''):
        try:
            return _trace_redact(json.loads(data))
        except (ValueError, UnicodeError):
            pass
    return '[Non-JSON body omitted]'


def _trace_http_request():
    g.http_trace_id = uuid4().hex
    g.http_started = time.monotonic()
    trace = _http_trace()
    if trace is None:
        return
    try:
        data = request.get_data() if request.content_length is not None and request.content_length <= 65536 else b''
        trace.log_http('RX', request.method, request.path, g.http_trace_id, {
            'peer': request.remote_addr,
            'query': _trace_redact(request.args.to_dict(flat=False)),
            'headers': _trace_redact(dict(request.headers)),
            'body': _trace_body(data, request.content_type) if data else None,
        })
    except Exception:
        logger.warning('Could not trace HTTP request', exc_info=True)


@app.after_request
def trace_http_response(response):
    # Some Flask extensions (e.g. the rate limiter) reject before our hook runs.
    if not hasattr(g, 'http_trace_id'):
        _trace_http_request()
    trace = _http_trace()
    if trace is not None:
        try:
            trace.log_http('TX', request.method, request.path, g.http_trace_id, {
                'headers': _trace_redact(dict(response.headers)),
                'body': _trace_body(response.get_data(), response.content_type) if not response.is_streamed else '[Streamed body omitted]',
                'elapsed_ms': round((time.monotonic() - g.http_started) * 1000, 3),
            }, status=response.status_code)
        except Exception:
            logger.warning('Could not trace HTTP response', exc_info=True)
    return response


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
    _trace_http_request()

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
        
        # Commands use the target's existing TCP command API directly. Only
        # other webhook categories retain the embedded TM HTTP route for now.
        if data.get('event') == 'alston-rt.ui.cmd':
            runtime = app.extensions.get('web_application')
            handler = runtime.cmd_handler if runtime is not None else cmd_handler
            result = handler.execute(data.get('message'))
            # Completed command outcomes travel as ResponseEvent JSON, even
            # when the target fails or times out. Gateways may replace 5xx bodies.
            status = (200 if result.status == 'success' else
                      {'invalid_request': 400, 'timeout': 200, 'command_failed': 200}.get(result.code, 500))
            return jsonify(dict(result.to_dict(), request_id=uuid4().hex)), status

        categories = {'alston-rt.ui.dig': 'DIG', 'alston-rt.ui.dsh': 'DSH',
                      'alston-rt.ui.odt': 'ODT', 'alston-rt.ui.oet': 'OET'}
        category = categories.get(data.get('event'))
        if category is None:
            return jsonify(ResponseEvent('error', 'Unsupported webhook event', code='invalid_request').to_dict()), 400
        config = data.get('message')
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except ValueError:
                return jsonify(ResponseEvent('error', 'Configuration message is not valid JSON', code='invalid_request').to_dict()), 400
        if not isinstance(config, dict):
            return jsonify(ResponseEvent('error', 'Configuration message must be an object', code='invalid_request').to_dict()), 400
        runtime = app.extensions.get('web_application')
        if runtime is None:
            result, request_id = (ResponseEvent('error', 'Web application is not running', code='unavailable'), uuid4().hex)
        else:
            result, request_id = runtime.submit_configuration(category, config)
        # Application results retain their JSON body through HTTP gateways.
        return jsonify(dict(result.to_dict(), request_id=request_id)), 200

    except HTTPException as error:
        return jsonify(status='error', message=error.description), error.code
    except Exception:
        logger.error("Error processing webhook from %s", request.remote_addr)
        return jsonify({
            'status': 'error',
            'message': 'Internal server error'
        }), 500

def _profiles():
    runtime = app.extensions.get('web_application')
    if runtime is not None and getattr(runtime, 'profile_manager', None) is not None:
        return runtime.profile_manager
    # Support Flask-only use as well as the managed application entry point.
    manager = app.extensions.get('web_profiles')
    if manager is None:
        directory = runtime.profile_dir if runtime is not None else cmd_handler.profile_dir
        manager = ProfileManager(directory)
        app.extensions['web_profiles'] = manager
    return manager


@app.get('/api/profiles')
@app.route('/api/profile', methods=['GET', 'PUT'])
def profiles():
    """Discover or switch the profile used for file-backed WEB resources."""
    try:
        validate_request()
        manager = _profiles()
        if request.path == '/api/profiles':
            entries, selection = manager.list()
            payload = dict(selection.to_dict(), profiles=entries, count=len(entries))
        elif request.method == 'PUT':
            body = request.get_json()
            if not isinstance(body, dict) or set(body) != {'profile'}:
                raise ProfileError('Send a JSON object containing only profile')
            payload = manager.select(body['profile']).to_dict()
        else:
            payload = manager.current.to_dict()
        response = jsonify(ResponseEvent('success', 'WEB resource profile selected' if request.method == 'PUT'
                                        else 'WEB resource profiles retrieved', payload=payload).to_dict())
        response.headers['Cache-Control'] = 'no-store'
        return response
    except HTTPException as error:
        return jsonify(ResponseEvent('error', error.description, code='invalid_request').to_dict()), error.code
    except ProfileError as error:
        return jsonify(ResponseEvent('error', str(error), code=error.code).to_dict()), error.status
    except Exception:
        logger.exception('Could not access WEB profiles')
        return jsonify(ResponseEvent('error', 'Could not access configuration profiles', code='configuration_unavailable').to_dict()), 503


def _resource_directory():
    selection = _profiles().current
    expected = request.headers.get('X-Config-Profile')
    if expected is not None and expected != selection.directory.name:
        raise ProfileStoreError('The selected profile changed; refresh before saving', 'profile_changed', 409)
    return selection.directory


@app.route('/api/dishes', methods=['GET', 'POST'])
@app.route('/api/dishes/<dish_id>', methods=['GET', 'PATCH', 'DELETE'])
def dishes(dish_id=None):
    """Read or edit the active profile's DishList.json, independently of TM."""
    try:
        validate_request()
        store = DishProfileStore(_resource_directory())
        if request.method == 'GET':
            value, revision = store.read(dish_id)
            payload = ({'dishes': value, 'count': len(value)} if dish_id is None else {'dish': value})
            message, status = 'Dish configuration retrieved', 200
        elif request.method == 'DELETE':
            deleted, revision = store.delete(dish_id, request.if_match if 'If-Match' in request.headers else None)
            payload = {'deleted_id': deleted, 'phase': 'stored'}
            message, status = 'Dish configuration deleted', 200
        else:
            value, revision = store.save(
                request.get_json(), dish_id=dish_id,
                if_match=request.if_match if 'If-Match' in request.headers else None)
            payload = {'dish': value}
            payload['phase'] = 'stored'
            message = 'Dish configuration created' if request.method == 'POST' else 'Dish configuration updated'
            status = 201 if request.method == 'POST' else 200
        payload['profile'] = store.path.parent.name
        response = jsonify(ResponseEvent('success', message, payload=payload).to_dict())
        response.status_code = status
        response.set_etag(revision)
        response.headers['Cache-Control'] = 'no-store'
        if status == 201:
            response.headers['Location'] = '/api/dishes/' + value['dsh_id']
        return response
    except HTTPException as error:
        return jsonify(ResponseEvent('error', error.description, code='invalid_request').to_dict()), error.code
    except DishStoreError as error:
        return jsonify(ResponseEvent('error', str(error), code=error.code).to_dict()), error.status
    except OSError:
        logger.exception('Could not access profile dish configuration')
        return jsonify(ResponseEvent('error', 'Profile dish configuration is unavailable', code='configuration_unavailable').to_dict()), 503
    except Exception:
        logger.exception('Could not process dish configuration request')
        return jsonify(ResponseEvent('error', 'Could not process dish configuration request', code='internal_error').to_dict()), 500


@app.route('/api/digitisers', defaults={'resource': 'digitisers'}, methods=['GET', 'POST'])
@app.route('/api/digitisers/<identifier>', defaults={'resource': 'digitisers'}, methods=['GET', 'PATCH', 'DELETE'])
@app.route('/api/weather-stations', defaults={'resource': 'weather_stations'}, methods=['GET', 'POST'])
@app.route('/api/weather-stations/<identifier>', defaults={'resource': 'weather_stations'}, methods=['GET', 'PATCH', 'DELETE'])
def profile_resources(resource, identifier=None):
    """Read or edit configured digitisers and weather-station definitions."""
    try:
        validate_request()
        store_class, singular = {'digitisers': (DigitiserProfileStore, 'digitiser'),
                                 'weather_stations': (WeatherStationProfileStore, 'weather_station')}[resource]
        store = store_class(_resource_directory())
        if request.method == 'GET':
            value, revision = store.read(identifier)
            payload = ({resource: value, 'count': len(value)} if identifier is None else {singular: value})
            message, status = 'Configuration retrieved', 200
        elif request.method == 'DELETE':
            deleted, revision = store.delete(identifier, request.if_match if 'If-Match' in request.headers else None)
            payload = {'deleted_id': deleted, 'phase': 'stored'}
            message, status = 'Configuration deleted', 200
        else:
            value, revision = store.save(request.get_json(), identifier,
                request.if_match if 'If-Match' in request.headers else None)
            payload = {singular: value, 'phase': 'stored'}
            message = 'Configuration created' if request.method == 'POST' else 'Configuration updated'
            status = 201 if request.method == 'POST' else 200
        payload['profile'] = store.path.parent.name
        response = jsonify(ResponseEvent('success', message, payload=payload).to_dict())
        response.status_code = status
        response.set_etag(revision)
        response.headers['Cache-Control'] = 'no-store'
        if status == 201:
            response.headers['Location'] = request.path + '/' + value[store.id_field]
        return response
    except HTTPException as error:
        return jsonify(ResponseEvent('error', error.description, code='invalid_request').to_dict()), error.code
    except ProfileStoreError as error:
        return jsonify(ResponseEvent('error', str(error), code=error.code).to_dict()), error.status
    except OSError:
        logger.exception('Could not access profile resource configuration')
        return jsonify(ResponseEvent('error', 'Profile configuration is unavailable', code='configuration_unavailable').to_dict()), 503
    except Exception:
        logger.exception('Could not process profile configuration request')
        return jsonify(ResponseEvent('error', 'Could not process configuration request', code='internal_error').to_dict()), 500


@app.get('/api/pipeline')
@app.post('/api/pipeline/steps-maps')
@app.route('/api/pipeline/steps-maps/<name>', methods=['GET', 'DELETE'])
@app.route('/api/pipeline/steps-maps/<name>/steps', methods=['GET', 'POST'])
@app.route('/api/pipeline/steps-maps/<name>/steps/<int:index>', methods=['GET', 'PATCH', 'DELETE'])
def pipeline(name=None, index=None):
    """List, create and edit ordered steps in the selected profile's pipeline."""
    try:
        validate_request()
        store = PipelineProfileStore(_resource_directory())
        if request.method == 'GET':
            payload, revision = store.read(name, index)
            message, status = 'Pipeline configuration retrieved', 200
        elif request.method == 'DELETE':
            payload, revision = store.delete(name, index, request.if_match if 'If-Match' in request.headers else None)
            payload['phase'] = 'stored'
            message, status = 'Pipeline configuration deleted', 200
        else:
            payload, revision = store.save(request.get_json(), name, index,
                request.if_match if 'If-Match' in request.headers else None)
            payload['phase'] = 'stored'
            message = 'Pipeline configuration updated'
            status = 200 if request.method == 'PATCH' else 201
        payload['profile'] = store.path.parent.name
        response = jsonify(ResponseEvent('success', message, payload=payload).to_dict())
        response.status_code = status
        response.set_etag(revision)
        response.headers['Cache-Control'] = 'no-store'
        if status == 201:
            location = '/api/pipeline/steps-maps/' + payload['name']
            if name is not None:
                location += '/steps/' + str(payload['step']['index'])
            response.headers['Location'] = location
        return response
    except HTTPException as error:
        return jsonify(ResponseEvent('error', error.description, code='invalid_request').to_dict()), error.code
    except ProfileStoreError as error:
        return jsonify(ResponseEvent('error', str(error), code=error.code).to_dict()), error.status
    except OSError:
        logger.exception('Could not access pipeline configuration')
        return jsonify(ResponseEvent('error', 'Pipeline configuration is unavailable', code='configuration_unavailable').to_dict()), 503
    except Exception:
        logger.exception('Could not process pipeline request')
        return jsonify(ResponseEvent('error', 'Could not process pipeline request', code='internal_error').to_dict()), 500


@app.get('/api/telescope/model')
@limiter.limit('120 per minute', override_defaults=True)
def telescope_model():
    """Read the shared cache, or explicitly request and await a fresh TM snapshot."""
    try:
        validate_request()
    except HTTPException as error:
        return jsonify(ResponseEvent('error', error.description, code='invalid_request').to_dict()), error.code
    runtime = app.extensions.get('web_application')
    if runtime is None or not hasattr(runtime, 'telescope_model_response'):
        return jsonify(ResponseEvent('error', 'Telescope model cache is unavailable', code='model_unavailable').to_dict()), 503
    if set(request.args) - {'refresh'} or request.args.get('refresh', '0') not in ('0', '1'):
        return jsonify(ResponseEvent('error', 'Use refresh=1 to request a fresh snapshot', code='invalid_request').to_dict()), 400
    result = runtime.telescope_model_response(refresh=True) if request.args.get('refresh') == '1' else runtime.telescope_model_response()
    response = jsonify(result.to_dict())
    response.status_code = 200 if result.status == 'success' else 503
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/observations')
@app.get('/api/observations/<observation_id>')
@app.get('/api/observations/<observation_id>/scans/<int:target>/<int:frequency>/<int:iteration>/spr', defaults={'product': 'spr'})
@app.get('/api/observations/<observation_id>/scans/<int:target>/<int:frequency>/<int:iteration>/signal', defaults={'product': 'signal'})
def observations(observation_id=None, target=None, frequency=None, iteration=None, product='spr'):
    """Search local/shared storage without requiring a TM or SDP connection."""
    try:
        validate_request()
    except HTTPException as error:
        return jsonify(status='error', message=error.description), error.code

    try:
        expected_profile = request.headers.get('X-Config-Profile')
        if expected_profile and expected_profile != _profiles().current.directory.name:
            return jsonify(ResponseEvent('error', 'The selected profile changed; refresh the catalogue', code='profile_changed').to_dict()), 409
        if 'directory' in request.args:
            store = request.args['directory'].strip()
            if not store:
                result = ResponseEvent('error', 'Directory must not be empty', code='invalid_request')
                return jsonify(result.to_dict()), 400
        else:
            runtime = app.extensions.get('web_application')
            manager = (getattr(runtime, 'profile_manager', None) if runtime is not None else None) or app.extensions.get('web_profiles')
            store = (manager.current.scan_store_dir if manager is not None else
                     runtime.scan_store_dir if runtime is not None
                     else load_scan_store_dir(profile_dir=cmd_handler.profile_dir))
        if not store:
            result = ResponseEvent('error', 'Observation search directory is not configured', code='store_not_known')
            return jsonify(result.to_dict()), 503
        if target is not None:
            result = scan_spectrum_file(store, observation_id, target, frequency, iteration, request.args.get('folder'), product=product)
            if result.status == 'success':
                response = send_file(result.payload['path'], mimetype='text/csv' if product == 'spr' else 'image/png', as_attachment=True,
                                     download_name=result.payload['filename'], conditional=False)
                response.headers['Cache-Control'] = 'no-store'
                return response
        else:
            result = (read_observation(store, observation_id, request.args.get('folder', '.'))
                      if observation_id is not None else list_observations(store))
        if result.status == 'success' and observation_id is not None:
            from web.model_snapshot import redact_credentials
            result = ResponseEvent(result.status, result.message, payload=redact_credentials(result.payload))
        response = jsonify(result.to_dict())
        response.headers['Cache-Control'] = 'no-store'
        response.status_code = 200 if result.status == 'success' else {
            'invalid_request': 400, 'not_found': 404, 'scan_incomplete': 409}.get(result.code, 503)
        return response
    except Exception:
        logger.exception('Error listing observations')
        result = ResponseEvent('error', 'Could not load observation catalogue configuration', code='internal_error')
        return jsonify(result.to_dict()), 500


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

def main():
    runtime = WebServer(http_app=app)
    with runtime.running():
        runtime.start_http_server()
        runtime.wait_for_stop()


if __name__ == '__main__':
    main()
