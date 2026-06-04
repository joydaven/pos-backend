"""
Utility for creating rich UserActivity records from any Django app.

Usage:
    from users.activity_log import log_activity
    log_activity(request, 'Created POS Order', category='pos_order', details={...})
"""
import logging

logger = logging.getLogger(__name__)


def log_activity(request, action, category='other', details=None):
    """
    Create a UserActivity record and mark the request so the
    AuditMiddleware doesn't create a duplicate entry.
    """
    try:
        from users.models import UserActivity

        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return

        ip = (
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR')
        )

        UserActivity.objects.create(
            user=user,
            action=action,
            ip_address=ip,
            details=details,
            category=category,
            method=request.method,
            endpoint=request.path[:500],
            source='manual',
        )

        # Tell AuditMiddleware to skip this request
        request._activity_logged = True

    except Exception:
        logger.exception("Failed to log user activity")
