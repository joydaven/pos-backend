"""
Django settings for doctorsstudio project.
"""

import os
import atexit
from pathlib import Path
from dotenv import load_dotenv
from sshtunnel import SSHTunnelForwarder
import sentry_sdk

# Load environment variables from .env file (explicit path so it works under gunicorn/systemd)
# This populates os.environ which is the fallback when GCP Secret Manager is not enabled.
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

# Import centralized secret management (GCP Secret Manager with env var fallback)
from core.secrets import get_secret, get_server_secret

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = get_server_secret('SECRET_KEY')
if not SECRET_KEY:
    raise ValueError("SECRET_KEY is not set. Add it to .env or GCP Secret Manager.")

# SECURITY WARNING: don't run with debug turned on in production!
#DEBUG = os.environ.get('DEBUG', False)
# Convert string 'True'/'False' to Python boolean
DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'

ALLOWED_HOSTS = [h.strip() for h in os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]

# GHL Inbound Webhook Security
GHL_INBOUND_WEBHOOK_SECRET = get_secret('GHL_INBOUND_WEBHOOK_SECRET', '')

# Slack webhook for order processing alerts (failed orders after payment captured)
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')

# Application definition
INSTALLED_APPS = [
    'doctorsstudio.admin.DoctorsStudioAdminConfig',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',  # CORS headers for cross-origin requests
    'rest_framework',
    'drf_spectacular',  # API documentation
    'oauth2_provider',
    'crm',
    # 'woo',  # Commented out as it might not exist
    # 'api',  # Commented out as it might not exist
    'payments',
    'payment_plans',
    'credit_bank',
    'shipping',
    'users',
    'auth_api',
]

MIDDLEWARE = [
    # CORS middleware should be at the top
    'corsheaders.middleware.CorsMiddleware',
    # Custom middleware to disable CSRF for specific paths
    'auth_api.middleware.DisableCSRFMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'oauth2_provider.middleware.OAuth2TokenMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'users.middleware.AuditMiddleware',
]

SECURE_CROSS_ORIGIN_OPENER_POLICY = None  # Disable Cross-Origin-Opener-Policy for Google Auth

ROOT_URLCONF = 'doctorsstudio.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'doctorsstudio', 'templates'),
            os.path.join(BASE_DIR, 'templates'),  # Add general templates directory
            os.path.join(BASE_DIR, 'oauth2_provider', 'templates', 'oauth2_provider'),  # Add oauth2_provider templates directory
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'doctorsstudio.wsgi.application'

# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

# SSH Tunnel Configuration for secure database connection
SSH_TUNNEL_ENABLED = os.getenv('SSH_TUNNEL_ENABLED', 'False').lower() == 'true'

# Global variable to hold the tunnel reference
_ssh_tunnel = None

if SSH_TUNNEL_ENABLED:
    # SSH connection settings
    SSH_HOST = os.getenv('SSH_HOST')
    SSH_PORT = int(os.getenv('SSH_PORT', '22'))
    SSH_USER = os.getenv('SSH_USER')
    SSH_KEY_FILE = os.getenv('SSH_KEY_FILE')  # Path to your private key file (.pem)
    SSH_KEY_PASSWORD = get_server_secret('SSH_KEY_PASSWORD', None)  # Optional: passphrase for the key
    
    # Remote database settings (from SSH server's perspective)
    DB_HOST_REMOTE = os.getenv('DB_HOST', 'localhost')
    DB_PORT_REMOTE = int(os.getenv('DB_PORT', '5432'))
    
    # Create and start SSH tunnel
    _ssh_tunnel = SSHTunnelForwarder(
        (SSH_HOST, SSH_PORT),
        ssh_username=SSH_USER,
        ssh_pkey=SSH_KEY_FILE,
        ssh_private_key_password=SSH_KEY_PASSWORD,
        remote_bind_address=(DB_HOST_REMOTE, DB_PORT_REMOTE),
        local_bind_address=('127.0.0.1', 0)  # 0 = auto-assign available port
    )
    _ssh_tunnel.start()
    
    # Register cleanup function to close tunnel on exit
    def _close_ssh_tunnel():
        if _ssh_tunnel and _ssh_tunnel.is_active:
            _ssh_tunnel.stop()
    
    atexit.register(_close_ssh_tunnel)
    
    # Database connection through SSH tunnel
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', 'postgres'),
            'USER': os.getenv('DB_USER', 'postgres'),
            'PASSWORD': get_server_secret('DB_PASSWORD', 'postgres'),
            'HOST': '127.0.0.1',
            'PORT': _ssh_tunnel.local_bind_port,
            'CONN_MAX_AGE': 600,
            'CONN_HEALTH_CHECKS': True,
        }
    }
else:
    # Direct database connection (no SSH tunnel)
    if DEBUG:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.getenv('DB_NAME', 'postgres'),
                'USER': os.getenv('DB_USER', 'postgres'),
                'PASSWORD': get_server_secret('DB_PASSWORD', 'postgres'),
                'HOST': os.getenv('DB_HOST', 'db'),
                'PORT': os.getenv('DB_PORT', '5432'),
                'CONN_MAX_AGE': 600,
                'CONN_HEALTH_CHECKS': True,
            }
        }
        #DATABASES = {
        #    'default': {
        #        'ENGINE': 'django.db.backends.sqlite3',
        #        'NAME': BASE_DIR / 'db.sqlite3',
        #    }
        #}
    else:
        # Production PostgreSQL database
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': os.getenv('DB_NAME', 'postgres'),
                'USER': os.getenv('DB_USER', 'postgres'),
                'PASSWORD': get_server_secret('DB_PASSWORD', 'postgres'),
                'HOST': os.getenv('DB_HOST', 'db'),
                'PORT': os.getenv('DB_PORT', '5432'),
                'CONN_MAX_AGE': 600,
                'CONN_HEALTH_CHECKS': True,
            }
        }

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'America/New_York'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Media files
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Additional locations of static files
_crm_static = os.path.join(BASE_DIR, 'crm', 'static')
STATICFILES_DIRS = [_crm_static] if os.path.exists(_crm_static) else []

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Cache configuration - use Redis in production, fallback to local memory for dev
REDIS_URL = os.environ.get('REDIS_URL')
if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'SOCKET_CONNECT_TIMEOUT': 5,
                'SOCKET_TIMEOUT': 5,
                'RETRY_ON_TIMEOUT': True,
            },
            'KEY_PREFIX': 'staging_pos',
            'TIMEOUT': 300,
        },
        'throttle': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL.rsplit('/', 1)[0] + '/1',
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            },
            'KEY_PREFIX': 'throttle',
            'TIMEOUT': 60,
        },
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'default-cache',
            'TIMEOUT': 300,
        },
        'throttle': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'throttle-cache',
            'TIMEOUT': 60,
        },
    }

# CORS settings
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://34.148.82.199:85",
    "https://35.231.141.115:84",    
    "http://35.231.141.115:84",
    "https://35.231.141.115:83",
    "http://35.231.141.115:83",
    "https://pos.doctorsstudio.com",
    "http://pos.doctorsstudio.com",
    "https://staging-pos.doctorsstudio.com",
    "http://staging-pos.doctorsstudio.com",
    'http://127.0.0.1:8000',
]
CORS_ALLOW_CREDENTIALS = True
CORS_PREFLIGHT_MAX_AGE = 86400  # 24 hours

# CSRF settings - needed for cross-origin POST requests
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
    "http://127.0.0.1:8000",
    "https://pos.doctorsstudio.com",
    "http://pos.doctorsstudio.com",
]

CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
    'cache-control',
    'pragma',
    'expires',
]

# GoHighLevel OAuth2 Configuration
GOHIGHLEVEL_OAUTH = {
    'CLIENT_ID': get_secret('GHL_CLIENT_ID', ''),
    'CLIENT_SECRET': get_secret('GHL_CLIENT_SECRET', ''),
    'REDIRECT_URI': os.getenv('GHL_REDIRECT_URI', 'http://localhost:8000/api/oauth/callback'),
    'AUTHORIZATION_URL': 'https://marketplace.leadconnectorhq.com/oauth/chooselocation',
    'TOKEN_URL': 'https://services.leadconnectorhq.com/oauth/token',
    'API_BASE_URL': 'https://services.leadconnectorhq.com/v2',
    'SCOPE': 'contacts.readonly contacts.write',
}

# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'oauth2_provider.contrib.rest_framework.OAuth2Authentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 10,
    # API Schema generation with drf-spectacular
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    # Rate limiting — applied per-endpoint via throttle_classes, not globally
    # Global defaults removed to avoid throttling AllowAny endpoints (subscriptions, points, etc.)
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',
        'user': '200/minute',
        'payment': '20/minute',
        'auth': '10/minute',
    },
}

# drf-spectacular settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Doctor\'s Studio POS API',
    'DESCRIPTION': 'Complete API documentation for the Doctor\'s Studio Point of Sale System',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SCHEMA_PATH_PREFIX': '/api/',
    'SERVE_PERMISSIONS': ['rest_framework.permissions.IsAuthenticated'],
    'SERVERS': [
        {'url': 'https://pos.doctorsstudio.com', 'description': 'Production server'},
        {'url': 'http://localhost:8000', 'description': 'Development server'},
    ],
    'TAGS': [
        {'name': 'Products', 'description': 'Product management endpoints'},
        {'name': 'Customers', 'description': 'Customer management endpoints'},
        {'name': 'Orders', 'description': 'Order processing endpoints'},
        {'name': 'Subscriptions', 'description': 'Subscription management endpoints'},
        {'name': 'Payments', 'description': 'Payment processing endpoints'},
        {'name': 'Inventory', 'description': 'ATUM inventory management'},
        {'name': 'Authentication', 'description': 'OAuth2 authentication endpoints'},
        {'name': 'Webhooks', 'description': 'WooCommerce webhook handlers'},
        {'name': 'Sync', 'description': 'Data synchronization endpoints'},
    ],
    'CONTACT': {
        'name': 'Doctor\'s Studio',
        'email': 'support@doctorsstudio.com',
    },
    'LICENSE': {
        'name': 'Proprietary',
    },
}

# OAuth2 settings
OAUTH2_PROVIDER = {
    'SCOPES': {
        'read': 'Read scope',
        'write': 'Write scope',
        'products': 'Access to products API',
    },
    'PKCE_REQUIRED': True,
    'ALLOWED_GRANT_TYPES': ['authorization_code', 'password', 'refresh_token'],
    'CLIENT_ID_GENERATOR_CLASS': 'oauth2_provider.generators.ClientIdGenerator',
    'OAUTH2_VALIDATOR_CLASS': 'oauth2_provider.oauth2_validators.OAuth2Validator',
    'ACCESS_TOKEN_EXPIRE_SECONDS': 28800,  # 8 hours
    'REFRESH_TOKEN_EXPIRE_SECONDS': 2592000,  # 30 days
    'REFRESH_TOKEN_GRACE_PERIOD_SECONDS': 120,  # 2 minutes grace period for refresh token
}

# Session and cookie settings
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'False').lower() == 'true'  # True in production
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = os.getenv('SESSION_COOKIE_SAMESITE', 'Lax')  # Use 'Strict' in production
CSRF_COOKIE_SECURE = os.environ.get('CSRF_COOKIE_SECURE', 'False').lower() == 'true'  # True in production
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = os.getenv('CSRF_COOKIE_SAMESITE', 'Lax') # Use 'Strict' in production

# Login/Logout URLs
LOGIN_REDIRECT_URL = '/drs-admin/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# ShipStation API Configuration
SHIPSTATION_API_KEY = get_secret('SHIPSTATION_API_KEY', '')
SHIPSTATION_API_SECRET = get_secret('SHIPSTATION_API_SECRET', '')
SHIPSTATION_WEBHOOK_SECRET = get_secret('SHIPSTATION_WEBHOOK_SECRET', '')
SHIPSTATION_TEST_MODE = os.getenv('SHIPSTATION_TEST_MODE', 'True').lower() == 'true'

# POS System Configuration
POS_ENABLE_SHIPPING = os.getenv('POS_ENABLE_SHIPPING', 'False').lower() == 'true'

# ShipStation Sender Information (business address — not secrets)
SHIPSTATION_FROM_NAME = 'Doctors Studio'
SHIPSTATION_FROM_COMPANY = 'Doctors Studio'
SHIPSTATION_FROM_STREET1 = '1001 S Federal Hwy'
SHIPSTATION_FROM_STREET2 = 'Suite 201'
SHIPSTATION_FROM_CITY = 'Boca Raton'
SHIPSTATION_FROM_STATE = 'FL'
SHIPSTATION_FROM_ZIP = '33432'
SHIPSTATION_FROM_COUNTRY = 'US'

# Google OAuth2 Configuration
GOOGLE_OAUTH2_CLIENT_ID = get_secret('GOOGLE_OAUTH2_CLIENT_ID', '')
GOOGLE_OAUTH2_CLIENT_SECRET = get_secret('GOOGLE_OAUTH2_CLIENT_SECRET', '')

# OAuth2 Client ID for token generation (server-specific — different Django OAuth2 app per server)
OAUTH2_CLIENT_ID = get_server_secret('OAUTH2_CLIENT_ID', '')

# Email Configuration with Mailgun Failover
# Check if we should use Mailgun (when MAILGUN_API_KEY is set)
USE_MAILGUN = bool(get_secret('MAILGUN_API_KEY'))

if USE_MAILGUN:
    # Mailgun Configuration (Primary)
    EMAIL_BACKEND = "anymail.backends.mailgun.EmailBackend"
    ANYMAIL = {
        "MAILGUN_API_KEY": get_secret('MAILGUN_API_KEY'),
        "MAILGUN_SENDER_DOMAIN": os.getenv('MAILGUN_DOMAIN', 'doctorsstudio.com'),
        "MAILGUN_API_URL": os.getenv('MAILGUN_API_URL', 'https://api.mailgun.net/v3'),
    }
    DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Support@doctorsstudio.com')
    
    # Add anymail to installed apps
    if 'anymail' not in INSTALLED_APPS:
        INSTALLED_APPS.append('anymail')
else:
    # SMTP Configuration (Fallback)
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.getenv('EMAIL_HOST')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
    EMAIL_HOST_PASSWORD = get_secret('EMAIL_HOST_PASSWORD')
    EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() == 'true'
    DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'webmaster@localhost')

SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {module}:{lineno} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'django.log'),
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'payment_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(BASE_DIR, 'logs', 'payments.log'),
            'maxBytes': 10 * 1024 * 1024,  # 10 MB
            'backupCount': 10,
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': True,
        },
        'django.request': {
            'handlers': ['console', 'file'],
            'level': 'ERROR',
            'propagate': False,
        },
        'crm': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'payments': {
            'handlers': ['console', 'payment_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'payment_plans': {
            'handlers': ['console', 'payment_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Security settings for production
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    X_FRAME_OPTIONS = 'SAMEORIGIN'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Sentry error tracking
SENTRY_DSN = (get_server_secret('SENTRY_DSN', '') or '').strip()
if SENTRY_DSN and SENTRY_DSN.startswith('http'):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        send_default_pii=False,
        environment=os.environ.get('SENTRY_ENVIRONMENT', 'staging'),
    )
