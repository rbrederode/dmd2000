"""Local UI authentication and profile-independent SQLite user accounts."""

from datetime import timedelta
import fcntl
import os
from pathlib import Path
import re
import secrets

from flask import current_app, jsonify, redirect, request, session, url_for
from flask_security import Security, SQLAlchemyUserDatastore, current_user, hash_password
from flask_security.models import fsqla_v3 as fsqla
from flask_security.signals import user_authenticated
from flask_security.utils import uia_username_mapper
from flask_security.username_util import UsernameUtil
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from util.version import get_version_info

DEFAULT_STATE_DIR = Path.home() / '.local' / 'share' / 'dmd2000' / 'web'
ROLES = ('viewer', 'operator', 'admin')
USERNAME_ERROR = 'Username must have 3–64 letters, digits, dots, underscores or hyphens'
db = SQLAlchemy()
fsqla.FsModels.set_db_info(db)


class Role(db.Model, fsqla.FsRoleMixin):
    pass


class User(db.Model, fsqla.FsUserMixin):
    # Username-only accounts do not require an email address.
    email = db.Column(db.String(255), unique=True, nullable=True)


def normalize_username(username):
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,63}', username):
        raise ValueError(USERNAME_ERROR)
    return username.lower()


class UIUsernameUtil(UsernameUtil):
    """Use the account editor's exact username rules during authentication too."""

    def normalize(self, username):
        try:
            return normalize_username(username)
        except ValueError:
            return None

    def validate(self, username):
        normalized = self.normalize(username)
        return (None, normalized) if normalized is not None else (USERNAME_ERROR, None)


def _secret(path, environment):
    value = os.getenv(environment)
    if value:
        return value
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    value = secrets.token_urlsafe(48)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(value)
    return value


def public_user(user):
    return {'id': user.id, 'username': user.username, 'active': user.active,
            'roles': sorted(role.name for role in user.roles)}


def save_user(datastore, data, user=None):
    # Serialize account changes across HTTP workers and the recovery CLI, so
    # simultaneous edits cannot disable both remaining administrators.
    path = current_app.extensions['ui_auth_state_dir'] / '.users.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        db.session.expire_all()
        try:
            return _save_user(datastore, data, user)
        except IntegrityError as error:
            db.session.rollback()
            raise ValueError('Username already exists') from error


def _save_user(datastore, data, user=None):
    if not isinstance(data, dict) or not data or set(data) - {'username', 'password', 'role', 'active'}:
        raise ValueError('Supply username, password, role and/or active')
    creating = user is None
    username = normalize_username(data.get('username', user.username if user else ''))
    if user and username != user.username:
        raise ValueError('Usernames cannot be changed')
    if creating and datastore.find_user(username=username):
        raise ValueError('Username already exists')
    role = data.get('role', user.roles[0].name if user and user.roles else 'viewer')
    if role not in ROLES:
        raise ValueError('Choose viewer, operator or admin')
    active = data.get('active', user.active if user else True)
    if type(active) is not bool:
        raise ValueError('active must be true or false')
    password = data.get('password')
    if (creating or password is not None) and (not isinstance(password, str) or len(password) < 12):
        raise ValueError('Passwords must contain at least 12 characters')
    if user and user.active and user.has_role('admin') and (not active or role != 'admin'):
        admins = User.query.filter_by(active=True).filter(User.roles.any(Role.name == 'admin')).count()
        if admins <= 1:
            raise ValueError('Keep at least one active administrator')
    if creating:
        user = datastore.create_user(username=username, password=hash_password(password),
                                     active=active, roles=[role])
    else:
        user.active = active
        user.roles = [datastore.find_role(role)]
        if password is not None:
            user.password = hash_password(password)
            datastore.set_uniquifier(user)
    db.session.commit()
    return user


def init_security(app, state_dir=None):
    suite_version = get_version_info()
    directory = Path(state_dir or os.getenv('DMD_UI_STATE_DIR', DEFAULT_STATE_DIR)).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    app.config.update(
        SECRET_KEY=_secret(directory / 'session.key', 'DMD_UI_SECRET_KEY'),
        SECURITY_PASSWORD_SALT=_secret(directory / 'password.salt', 'DMD_UI_PASSWORD_SALT'),
        SQLALCHEMY_DATABASE_URI='sqlite:///' + str(directory / 'users.sqlite'),
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'timeout': 15, 'check_same_thread': False}},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SECURITY_PASSWORD_HASH='argon2', SECURITY_PASSWORD_LENGTH_MIN=12,
        SECURITY_USERNAME_ENABLE=True, SECURITY_USERNAME_REQUIRED=True,
        SECURITY_USERNAME_MIN_LENGTH=3, SECURITY_USERNAME_MAX_LENGTH=64,
        SECURITY_USER_IDENTITY_ATTRIBUTES=[{'username': {'mapper': uia_username_mapper, 'case_insensitive': True}}],
        SECURITY_REGISTERABLE=False, SECURITY_RECOVERABLE=False, SECURITY_CONFIRMABLE=False,
        SECURITY_CHANGEABLE=True, SECURITY_SEND_PASSWORD_CHANGE_EMAIL=False,
        SECURITY_EMAIL_REQUIRED=False, SECURITY_RETURN_GENERIC_RESPONSES=True,
        SECURITY_POST_LOGIN_VIEW='/', SECURITY_POST_LOGOUT_VIEW='/login',
        SECURITY_LOGOUT_METHODS=['POST'], SECURITY_FLASH_MESSAGES=False,
        SESSION_COOKIE_NAME='dmd2000_ui_session', SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=False,
        REMEMBER_COOKIE_NAME='dmd2000_ui_remember', REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE='Strict', REMEMBER_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), SESSION_REFRESH_EACH_REQUEST=False,
        WTF_CSRF_TIME_LIMIT=8 * 3600,
    )
    app.template_folder = str(Path(__file__).resolve().parent / 'templates')
    db.init_app(app)
    csrf = CSRFProtect(app)
    datastore = SQLAlchemyUserDatastore(db, User, Role)
    security = Security(app, datastore, username_util_cls=UIUsernameUtil)
    with app.app_context():
        db.create_all()
        for role in ROLES:
            datastore.find_or_create_role(name=role)
        db.session.commit()
    os.chmod(directory / 'users.sqlite', 0o600)
    app.extensions['ui_auth_state_dir'] = directory
    limiter = Limiter(key_func=get_remote_address, app=app, storage_uri='memory://',
                      default_limits=[], key_prefix='ui_auth')
    app.view_functions['security.login'] = limiter.limit('10 per minute')(app.view_functions['security.login'])

    @user_authenticated.connect_via(app)
    def authenticated(sender, user, **kwargs):
        session.permanent = True

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if request.path.startswith('/api/'):
            return jsonify(status='error', message='Your form session expired or is invalid. Refresh and try again.', code='csrf_failed'), 400
        return 'Your form session expired. Reload the login page and try again.', 400

    @app.before_request
    def require_user():
        if request.endpoint is None or request.endpoint.startswith('security.') or request.path == '/styles.css':
            return
        if not current_user.is_authenticated or not current_user.is_active:
            if request.path.startswith('/api/'):
                return jsonify(status='error', message='Sign in to continue', code='login_required'), 401
            return redirect(url_for('security.login', next=request.path))
        if request.path.startswith('/api/admin/') and not current_user.has_role('admin'):
            return jsonify(status='error', message='Administrator access is required', code='forbidden'), 403
        writes = request.method not in ('GET', 'HEAD', 'OPTIONS')
        read_only_post = request.path == '/api/opt/open'
        if writes and not read_only_post and not any(current_user.has_role(role) for role in ('operator', 'admin')):
            return jsonify(status='error', message='Operator access is required', code='forbidden'), 403

    # Check identity/roles before CSRF so expired API sessions return a clear 401.
    hooks = app.before_request_funcs[None]
    hooks.remove(require_user)
    hooks.insert(1 if hooks and hooks[0].__name__ == 'local_only' else 0, require_user)

    @app.get('/api/auth/me')
    def identity():
        user = public_user(current_user)
        return jsonify(**user, version=suite_version, can_edit=any(current_user.has_role(role) for role in ('operator', 'admin')),
                       can_manage_users=current_user.has_role('admin'), csrf_token=generate_csrf())

    @app.route('/api/admin/users', methods=['GET', 'POST'])
    @app.patch('/api/admin/users/<int:user_id>')
    def users(user_id=None):
        if request.method == 'GET':
            return jsonify(users=[public_user(user) for user in User.query.order_by(User.username)])
        user = db.session.get(User, user_id) if user_id is not None else None
        if user_id is not None and user is None:
            return jsonify(status='error', message='User was not found'), 404
        try:
            user = save_user(datastore, request.get_json(), user)
            return jsonify(user=public_user(user)), 201 if user_id is None else 200
        except ValueError as error:
            db.session.rollback()
            return jsonify(status='error', message=str(error)), 400

    @app.after_request
    def private_headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    return security
