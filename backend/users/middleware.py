import logging
import re

logger = logging.getLogger(__name__)

# Paths that should never be logged (noisy, irrelevant, or machine-initiated)
SKIP_PATHS = [
    '/api/health/',
    '/api/schema/',
    '/api/docs/',
    '/api/redoc/',
    '/o/token/',
    '/o/custom/token/',
    '/o/revoke-token/',
    '/o/introspect/',
    '/api/auth/refresh/',
    '/api/auth/check/',
    '/drs-admin/',
    '/static/',
    '/media/',
]

# URL prefix → category mapping (order matters: first match wins)
CATEGORY_MAP = [
    ('/api/pos-orders/', 'pos_order'),
    ('/api/pos-customers/', 'customer'),
    ('/api/contacts/', 'customer'),
    ('/api/orders/', 'order'),
    ('/api/products/', 'product'),
    ('/api/product-inventory/', 'inventory'),
    ('/api/inventory-locations/', 'inventory'),
    ('/api/payments/', 'payment'),
    ('/api/payment-plans/', 'payment_plan'),
    ('/api/credit-bank/', 'credit_bank'),
    ('/api/shipping/', 'shipping'),
    ('/api/users/', 'user_management'),
    ('/api/auth/', 'auth'),
    ('/api/refund', 'refund'),
    ('/api/saved-carts/', 'pos_order'),
    ('/api/webhook-logs/', 'other'),
    ('/api/pos-locations/', 'settings'),
    ('/api/pos-settings', 'settings'),
    ('/api/sync', 'sync'),
    ('/woocommerce/', 'sync'),
    ('/api/subscriptions/', 'subscription'),
    ('/api/memberships/', 'membership'),
    ('/api/customer', 'customer'),
    ('/api/credit-service-points/', 'pos_order'),
    ('/api/create-woocommerce-order/', 'pos_order'),
    ('/api/create-customer', 'customer'),
    ('/api/woocommerce-order', 'pos_order'),
    ('/api/update-woocommerce-order', 'pos_order'),
]

# HTTP method → human-readable verb
METHOD_VERBS = {
    'POST': 'Created',
    'PUT': 'Updated',
    'PATCH': 'Updated',
    'DELETE': 'Deleted',
}

# Category → human-readable label for action strings
CATEGORY_LABELS = {
    'pos_order': 'POS Order',
    'customer': 'Customer',
    'order': 'Order',
    'product': 'Product',
    'inventory': 'Inventory',
    'payment': 'Payment',
    'payment_plan': 'Payment Plan',
    'credit_bank': 'Credit Bank',
    'shipping': 'Shipment',
    'user_management': 'User',
    'auth': 'Auth',
    'refund': 'Refund',
    'settings': 'Settings',
    'sync': 'Sync',
    'subscription': 'Subscription',
    'membership': 'Membership',
    'other': 'Resource',
}


def _get_category(path):
    for prefix, category in CATEGORY_MAP:
        if path.startswith(prefix):
            return category
    return 'other'


def _build_action_string(method, path, category):
    """Build a human-readable action string from method + path + category."""
    verb = METHOD_VERBS.get(method, method)
    label = CATEGORY_LABELS.get(category, 'Resource')

    # Try to extract a meaningful sub-action from the URL
    # e.g. /api/pos-orders/123/trash/ → "Trashed POS Order"
    action_overrides = {
        'trash': 'Trashed',
        'restore': 'Restored',
        'send-receipt': 'Sent receipt for',
        'send-refund-receipt': 'Sent refund receipt for',
        'change_password': 'Changed password for',
        'cancel': 'Cancelled',
        'restructure': 'Restructured',
        'pay-early': 'Paid early',
        'mark-paid': 'Marked paid',
        'charge': 'Charged',
        'add': 'Added credits to',
        'redeem': 'Redeemed credits from',
        'adjust': 'Adjusted credits for',
        'sync': 'Synced',
        'update-status': 'Updated status of',
    }

    # Check the last non-empty path segment for action overrides
    segments = [s for s in path.rstrip('/').split('/') if s]
    if segments:
        last_segment = segments[-1]
        # Skip UUIDs and numeric IDs
        if not re.match(r'^[0-9a-f-]{8,}$', last_segment, re.IGNORECASE) and not last_segment.isdigit():
            if last_segment in action_overrides:
                return f"{action_overrides[last_segment]} {label}"

    return f"{verb} {label}"


def _should_skip(path):
    return any(path.startswith(skip) for skip in SKIP_PATHS)


class AuditMiddleware:
    """
    Middleware that automatically logs all write API requests (POST/PUT/PATCH/DELETE)
    to the UserActivity model for authenticated users.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        try:
            if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
                return response

            if _should_skip(request.path):
                return response

            # Only log for authenticated users
            user = getattr(request, 'user', None)
            if not user or not getattr(user, 'is_authenticated', False):
                return response

            # Only log successful responses (2xx)
            if response.status_code < 200 or response.status_code >= 300:
                return response

            # Skip if a manual log was already written for this request
            if getattr(request, '_activity_logged', False):
                return response

            category = _get_category(request.path)
            action = _build_action_string(request.method, request.path, category)

            ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')

            from users.models import UserActivity
            UserActivity.objects.create(
                user=user,
                action=action,
                ip_address=ip,
                category=category,
                method=request.method,
                endpoint=request.path[:500],
                status_code=response.status_code,
                source='middleware',
            )
        except Exception:
            logger.exception("AuditMiddleware: failed to log activity")

        return response
