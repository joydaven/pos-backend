"""
Configuration settings for payment gateways
"""
import os
from django.conf import settings
from core.secrets import get_secret

# Authorize.net settings
AUTHORIZE_NET_LOGIN_ID = get_secret('AUTHORIZE_NET_LOGIN_ID', '')
AUTHORIZE_NET_TRANSACTION_KEY = get_secret('AUTHORIZE_NET_TRANSACTION_KEY', '')
AUTHORIZE_NET_CLIENT_KEY = get_secret('AUTHORIZE_NET_CLIENT_KEY', '')
AUTHORIZE_NET_SANDBOX = get_secret('AUTHORIZE_NET_SANDBOX', 'True').lower() == 'true'

# Get validation mode from env, default to 'liveMode' if not set or invalid
VALID_MODES = {"none", "testMode", "liveMode"}

env_mode = get_secret('AUTHORIZE_NET_CARD_VALIDATION_UPON_ADD', 'liveMode')
if env_mode not in VALID_MODES:
    env_mode = 'liveMode'

AUTHORIZE_NET_CARD_VALIDATION_UPON_ADD = env_mode

# Determine which endpoint to use based on sandbox setting
if AUTHORIZE_NET_SANDBOX:
    AUTHORIZE_NET_ENDPOINT = 'https://apitest.authorize.net/xml/v1/request.api'
else:
    AUTHORIZE_NET_ENDPOINT = 'https://api.authorize.net/xml/v1/request.api'
