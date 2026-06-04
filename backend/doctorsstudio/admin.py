from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig


class DoctorsStudioAdminSite(AdminSite):
    """
    Custom admin site that restricts login to @doctorsstudio.com emails.
    Superusers are always allowed as a safety fallback.
    """
    site_header = 'Doctors Studio Admin'
    site_title = 'Doctors Studio Portal'
    index_title = 'Welcome to Doctors Studio Management Portal'

    def has_permission(self, request):
        """
        Only allow staff users whose email ends with @doctorsstudio.com.
        Superusers bypass the email check.
        """
        if not super().has_permission(request):
            return False
        user = request.user
        if user.is_superuser:
            return True
        return user.email.endswith('@doctorsstudio.com')


class DoctorsStudioAdminConfig(AdminConfig):
    """Replace the default admin site with our custom one."""
    default_site = 'doctorsstudio.admin.DoctorsStudioAdminSite'
