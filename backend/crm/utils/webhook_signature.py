"""
WooCommerce webhook HMAC signature validation and deduplication utility.

WooCommerce sends an HMAC-SHA256 signature in the X-WC-Webhook-Signature header.
The signature is a base64-encoded HMAC of the request body using the webhook secret.

Deduplication uses the X-WC-Webhook-Delivery-ID header (unique per delivery attempt)
stored in Redis/Django cache to reject duplicate deliveries within a 5-minute window.
"""

import base64
import hashlib
import hmac
import logging
import os

from django.core.cache import cache
from django.http import JsonResponse

from core.secrets import get_secret

logger = logging.getLogger(__name__)

# Read the webhook secret once at module load
_WEBHOOK_SECRET = get_secret('WOOCOMMERCE_WEBHOOK_SECRET', '')

# Deduplication TTL in seconds (5 minutes covers WooCommerce retry window)
_DEDUP_TTL = 300


def verify_woocommerce_signature(request):
    """
    Verify the HMAC-SHA256 signature on a WooCommerce webhook request.

    Args:
        request: Django HttpRequest

    Returns:
        (True, None) if valid or if the request is an empty ping/test.
        (False, JsonResponse) if invalid — caller should return the response.
    """
    # Empty body = WooCommerce ping/test — always allow
    if not request.body:
        return True, None

    secret = _WEBHOOK_SECRET
    if not secret:
        logger.warning("WOOCOMMERCE_WEBHOOK_SECRET not configured — skipping signature check")
        return True, None

    signature_header = request.META.get('HTTP_X_WC_WEBHOOK_SIGNATURE', '')
    if not signature_header:
        logger.warning("Webhook request missing X-WC-Webhook-Signature header")
        return False, JsonResponse(
            {'error': 'Missing webhook signature'},
            status=401,
        )

    # Compute expected signature
    expected = base64.b64encode(
        hmac.new(
            secret.encode('utf-8'),
            request.body,
            hashlib.sha256,
        ).digest()
    ).decode('utf-8')

    if not hmac.compare_digest(expected, signature_header):
        logger.warning("Webhook signature mismatch — rejecting request")
        return False, JsonResponse(
            {'error': 'Invalid webhook signature'},
            status=401,
        )

    return True, None


def check_webhook_duplicate(request):
    """
    Check if this webhook delivery has already been processed using the
    X-WC-Webhook-Delivery-ID header.  Uses Django cache (Redis-backed).

    Args:
        request: Django HttpRequest

    Returns:
        (False, None) if this is a new delivery (not a duplicate).
        (True, JsonResponse) if this is a duplicate — caller should return the response.
    """
    delivery_id = request.META.get('HTTP_X_WC_WEBHOOK_DELIVERY_ID', '')
    if not delivery_id:
        # No delivery ID header — can't deduplicate, allow through
        return False, None

    cache_key = f'wh_dedup:{delivery_id}'
    try:
        if cache.get(cache_key):
            logger.info(f"Duplicate webhook delivery rejected: {delivery_id}")
            return True, JsonResponse(
                {'status': 'duplicate', 'message': 'Webhook already processed'},
                status=200,  # 200 so WooCommerce doesn't retry
            )
        # Mark as processed
        cache.set(cache_key, 1, _DEDUP_TTL)
    except Exception:
        # If cache is unavailable, allow through rather than blocking
        logger.warning("Cache unavailable for webhook deduplication — allowing through")

    return False, None
