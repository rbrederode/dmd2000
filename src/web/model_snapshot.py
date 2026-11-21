"""JSON-safe telescope snapshots with credential fields removed at the source."""

from datetime import datetime, timezone


def redact_credentials(value):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if any(part in str(key).lower() for part in
                ('password', 'secret', 'token', 'authorization', 'cookie', 'signature', 'api_key', 'api-key', 'private_key'))
                else redact_credentials(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    return value


def telescope_snapshot(model):
    data = redact_credentials(model.to_dict())
    arguments = ((data.get('tel_mgr') or {}).get('app') or {}).get('arguments') or {}
    return {'telescope_model': data, 'snapshot_at': datetime.now(timezone.utc).isoformat(),
            'profile': arguments.get('profile')}
