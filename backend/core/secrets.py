"""
Google Cloud Secret Manager integration with environment variable fallback.

Usage:
    from core.secrets import get_secret, get_server_secret
    
    # Shared secrets (same across all servers): POS_{key}
    value = get_secret('WOO_CONSUMER_KEY')
    
    # Server-specific secrets (differ per server): POS_{environment}_{key}
    value = get_server_secret('DB_PASSWORD')

Two tiers of secrets:
    get_secret(key)        → looks up POS_{key}                    (shared across staging/production)
    get_server_secret(key) → looks up POS_{environment}_{key}      (per-server: staging or production)

Environment variables:
    USE_GCP_SECRETS  - Set to 'true' to enable Secret Manager (default: false)
    GCP_PROJECT_ID   - GCP project ID (default: 'doctors-studio-backend')
    APP_ENVIRONMENT  - 'staging' or 'production' (default: 'staging') — used by get_server_secret()
"""

import os
import logging

logger = logging.getLogger(__name__)

_client = None
_cache = {}
_initialized = False


def _get_client():
    """Lazy-initialize the Secret Manager client."""
    global _client
    if _client is None:
        try:
            from google.cloud import secretmanager
            _client = secretmanager.SecretManagerServiceClient()
        except ImportError:
            logger.error(
                "google-cloud-secret-manager is not installed. "
                "Install it with: pip install google-cloud-secret-manager"
            )
            raise
        except Exception as e:
            logger.error(f"Failed to initialize Secret Manager client: {e}")
            raise
    return _client


def _use_gcp_secrets():
    """Check if GCP Secret Manager is enabled."""
    return os.environ.get('USE_GCP_SECRETS', 'false').lower() == 'true'


def _get_project_id():
    """Get the GCP project ID."""
    return os.environ.get('GCP_PROJECT_ID', 'doctors-studio-backend')


def _get_environment():
    """Get the current environment (staging/production)."""
    return os.environ.get('APP_ENVIRONMENT', 'staging')


def get_secret(key, default=None):
    """
    Get a secret value.

    When USE_GCP_SECRETS=true:
        - Looks up {key} in Google Secret Manager
        - Caches the result in-memory for the lifetime of the process
        - Falls back to os.environ.get(key, default) on any error

    When USE_GCP_SECRETS is not set (default):
        - Reads directly from os.environ (populated by load_dotenv / .env file)

    Args:
        key: The secret key name (e.g., 'DB_PASSWORD')
        default: Default value if the secret is not found anywhere

    Returns:
        The secret value as a string, or the default
    """
    # Fast path: if GCP secrets not enabled, use env vars directly
    if not _use_gcp_secrets():
        return os.environ.get(key, default)

    # Check in-memory cache first
    if key in _cache:
        return _cache[key]

    # Try to fetch from Secret Manager
    try:
        client = _get_client()
        project_id = _get_project_id()

        # Prefix with POS_ in Secret Manager for clear project ownership
        secret_id = f"POS_{key}"
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

        response = client.access_secret_version(request={"name": name})
        value = response.payload.data.decode("UTF-8")

        # Cache the value
        _cache[key] = value

        global _initialized
        if not _initialized:
            logger.info(f"Secret Manager active (project={project_id})")
            _initialized = True

        return value

    except Exception as e:
        # Log once per key, then fall back to env var
        logger.warning(
            f"Secret Manager lookup failed for '{key}': {e}. "
            f"Falling back to environment variable."
        )
        # Fall back to environment variable
        value = os.environ.get(key, default)
        # Cache the fallback too so we don't retry on every call
        _cache[key] = value
        return value


def clear_cache():
    """Clear the in-memory secret cache. Useful for testing."""
    global _cache, _initialized
    _cache = {}
    _initialized = False


def get_server_secret(key, default=None):
    """
    Get a server-specific secret that differs between staging and production.

    Uses APP_ENVIRONMENT to determine the prefix:
        - staging:    POS_staging_{key}
        - production: POS_production_{key}

    Use this for secrets that have different values per server:
        - DB_PASSWORD, GHL_DB_PASSWORD (different databases)
        - SECRET_KEY (must be unique per server)
        - CLIENT_ID, CLIENT_SECRET, OAUTH2_CLIENT_ID (different OAuth2 apps)
        - SENTRY_DSN (separate Sentry projects)

    Falls back to os.environ.get(key, default) when GCP is disabled or on error.
    """
    if not _use_gcp_secrets():
        return os.environ.get(key, default)

    cache_key = f"_server_{key}"
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        client = _get_client()
        project_id = _get_project_id()
        environment = _get_environment()

        secret_id = f"POS_{environment}_{key}"
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

        response = client.access_secret_version(request={"name": name})
        value = response.payload.data.decode("UTF-8")

        _cache[cache_key] = value
        return value

    except Exception as e:
        logger.warning(
            f"Secret Manager lookup failed for server secret '{key}': {e}. "
            f"Falling back to environment variable."
        )
        value = os.environ.get(key, default)
        _cache[cache_key] = value
        return value


def get_woo_db_config():
    """
    Get WordPress/WooCommerce database connection configuration.

    Returns a dict with Cloud SQL (MariaDB) credentials for direct
    WooCommerce database access over private VPC. No SSH tunnel needed —
    both VMs share the same GCP VPC network.

    Non-secret config (hosts, ports, usernames) from env vars.
    Actual secrets (passwords) from Secret Manager.

    Returns:
        dict with keys: db_host, db_port, db_name, db_username, db_password
    """
    db_port_str = os.environ.get('WOO_DB_PORT', '3306')

    return {
        'db_host': os.environ.get('WOO_DB_HOST', '10.0.16.3'),
        'db_port': int(db_port_str) if db_port_str and str(db_port_str).isdigit() else 3306,
        'db_name': os.environ.get('WOO_DB_NAME'),
        'db_username': os.environ.get('WOO_DB_USER'),
        'db_password': get_server_secret('WOO_DB_PASSWORD'),
    }


def get_ghl_db_config():
    """
    Get GoHighLevel database connection configuration.

    Non-secret config (host, port, db name, user) from env vars.
    Password from Secret Manager.

    Returns:
        dict with keys: host, port, database, user, password
    """
    return {
        'host': os.environ.get('GHL_DB_HOST'),
        'port': os.environ.get('GHL_DB_PORT', '5432'),
        'database': os.environ.get('GHL_DB_NAME'),
        'user': os.environ.get('GHL_DB_USER'),
        'password': get_server_secret('GHL_DB_PASSWORD'),
    }
