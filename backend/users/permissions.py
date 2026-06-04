"""
Role-based permission classes for the POS system.

All classes respect the global `permissions_enabled` POSSetting toggle.
When permissions_enabled is False (default), all checks pass through — the
system behaves as if every user has full access.
"""

import logging
from rest_framework.permissions import BasePermission

logger = logging.getLogger(__name__)


def _permissions_enabled():
    """Check the global permissions_enabled POSSetting."""
    from crm.models import POSSetting
    return POSSetting.get_setting('permissions_enabled', default=False)


def _get_user_role(user):
    """Safely retrieve the UserRole from a request user, or None."""
    try:
        profile = getattr(user, 'profile', None)
        if profile:
            return profile.role
    except Exception:
        pass
    return None


class HasRolePermission(BasePermission):
    """
    Generic permission class that checks a specific boolean field on the
    user's UserRole.

    Usage in a view::

        class MyView(APIView):
            permission_classes = [IsAuthenticated, HasRolePermission]
            required_permission = 'can_process_refunds'

    Or use the factory::

        permission_classes = [IsAuthenticated, role_permission('can_process_refunds')]
    """

    def has_permission(self, request, view):
        if not _permissions_enabled():
            return True

        perm_field = getattr(view, 'required_permission', None)
        if not perm_field:
            return True

        role = _get_user_role(request.user)
        if role is None:
            # No role assigned → deny when permissions are enforced
            return False

        return getattr(role, perm_field, False)


def role_permission(field_name):
    """
    Factory that returns a permission class bound to a specific toggle field.

    Usage::

        permission_classes = [IsAuthenticated, role_permission('can_view_reports')]
    """

    class _Perm(BasePermission):
        def has_permission(self, request, view):
            if not _permissions_enabled():
                return True
            role = _get_user_role(request.user)
            if role is None:
                return False
            return getattr(role, field_name, False)

    _Perm.__name__ = f'RolePerm_{field_name}'
    _Perm.__qualname__ = _Perm.__name__
    return _Perm


class IsAdminRole(BasePermission):
    """Only allows users with the 'admin' role (or superusers)."""

    def has_permission(self, request, view):
        if not _permissions_enabled():
            return True
        if request.user.is_superuser:
            return True
        role = _get_user_role(request.user)
        return role is not None and role.name == 'admin'


class IsManagerOrAbove(BasePermission):
    """Allows users with 'admin' or 'manager' role (or superusers)."""

    def has_permission(self, request, view):
        if not _permissions_enabled():
            return True
        if request.user.is_superuser:
            return True
        role = _get_user_role(request.user)
        return role is not None and role.name in ('admin', 'manager')
