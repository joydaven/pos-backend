"""
Custom throttle classes for rate limiting sensitive endpoints.
"""

from rest_framework.throttling import UserRateThrottle, AnonRateThrottle


class PaymentRateThrottle(UserRateThrottle):
    """Rate limit for payment processing endpoints (20/minute)."""
    scope = 'payment'


class AuthRateThrottle(AnonRateThrottle):
    """Rate limit for authentication endpoints (10/minute)."""
    scope = 'auth'
