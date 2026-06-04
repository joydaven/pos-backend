"""
Configuration settings for woocommerce
"""
import os
from django.conf import settings
from core.secrets import get_secret

# Woocommerce settings
WOO_API_URL = get_secret('WOO_API_URL', '')
WOO_CONSUMER_KEY = get_secret('WOO_CONSUMER_KEY', '')
WOO_CONSUMER_SECRET = get_secret('WOO_CONSUMER_SECRET', '')
WOOCOMMERCE_API_VERSION = os.environ.get('WOOCOMMERCE_API_VERSION', 'wc/v3')