from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import UserProfile, UserRole, UserActivity

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'
    fk_name = 'user'

class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'get_role', 'get_clinic_location')
    list_filter = ('is_staff', 'is_superuser', 'is_active')
    search_fields = ('username', 'email', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    
    def get_inline_instances(self, request, obj=None):
        # Only show the profile inline when editing an existing user
        # This prevents the profile from being created twice
        if not obj:
            return []
        return super().get_inline_instances(request, obj)
    
    def get_role(self, obj):
        if not hasattr(obj, 'profile') or not obj.profile:
            return 'No Profile'
        try:
            return obj.profile.role.get_name_display() if obj.profile.role else 'No Role'
        except (UserProfile.DoesNotExist, AttributeError):
            return 'No Profile'
    get_role.short_description = 'Role'
    
    def get_clinic_location(self, obj):
        if not hasattr(obj, 'profile') or not obj.profile:
            return 'No Location'
        try:
            return obj.profile.clinic_location
        except (UserProfile.DoesNotExist, AttributeError):
            return 'No Location'
    get_clinic_location.short_description = 'Clinic Location'
    
    def save_model(self, request, obj, form, change):
        # Save the user first
        super().save_model(request, obj, form, change)
        # Ensure profile exists
        if not hasattr(obj, 'profile') or not obj.profile:
            UserProfile.objects.get_or_create(user=obj)

class UserRoleAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'can_access_pos', 'can_access_customers', 'can_access_orders',
        'can_process_refunds', 'can_access_subscriptions', 'can_view_reports',
        'can_manage_users', 'can_manage_products', 'can_manage_settings',
    )
    list_filter = (
        'can_manage_users', 'can_manage_products', 'can_manage_settings',
        'can_view_reports', 'can_process_refunds',
    )
    search_fields = ('name', 'description')
    fieldsets = (
        (None, {'fields': ('name', 'description')}),
        ('Sidebar / Navigation Access', {'fields': (
            'can_access_pos', 'can_access_customers', 'can_access_orders',
            'can_process_refunds', 'can_access_subscriptions',
            'can_access_membership_subscriptions', 'can_access_payment_plans',
            'can_view_reports', 'can_manage_settings',
        )}),
        ('In-View / Feature-Level Actions', {'fields': (
            'can_process_orders', 'can_manage_products',
            'can_manage_inventory', 'can_manage_users',
        )}),
    )

class UserActivityAdmin(admin.ModelAdmin):
    list_display = ('action_time', 'user', 'colored_category', 'action', 'colored_method', 'status_code', 'ip_address', 'source')
    list_filter = ('category', 'method', 'source', 'user')
    search_fields = ('user__username', 'action', 'endpoint', 'ip_address')
    readonly_fields = ('user', 'action', 'action_time', 'ip_address', 'details', 'category', 'method', 'endpoint', 'status_code', 'source')
    ordering = ('-action_time',)
    date_hierarchy = 'action_time'
    list_per_page = 50
    list_select_related = ('user',)

    fieldsets = (
        (None, {'fields': ('user', 'action', 'category', 'action_time')}),
        ('Request Details', {'fields': ('method', 'endpoint', 'status_code', 'ip_address', 'source')}),
        ('Additional Data', {'fields': ('details',), 'classes': ('collapse',)}),
    )

    CATEGORY_COLORS = {
        'pos_order': '#2563eb',
        'refund': '#dc2626',
        'payment': '#16a34a',
        'payment_plan': '#7c3aed',
        'customer': '#0891b2',
        'product': '#ea580c',
        'settings': '#6b7280',
        'credit_bank': '#ca8a04',
        'shipping': '#0d9488',
        'user_management': '#4f46e5',
        'auth': '#be185d',
        'sync': '#64748b',
        'subscription': '#9333ea',
        'membership': '#e11d48',
        'inventory': '#65a30d',
        'other': '#9ca3af',
    }

    METHOD_COLORS = {
        'POST': '#16a34a',
        'PUT': '#2563eb',
        'PATCH': '#ca8a04',
        'DELETE': '#dc2626',
    }

    def colored_category(self, obj):
        from django.utils.html import format_html
        color = self.CATEGORY_COLORS.get(obj.category, '#9ca3af')
        label = obj.get_category_display()
        return format_html(
            '<span style="background:{}; color:#fff; padding:2px 8px; border-radius:4px; font-size:11px;">{}</span>',
            color, label
        )
    colored_category.short_description = 'Category'
    colored_category.admin_order_field = 'category'

    def colored_method(self, obj):
        from django.utils.html import format_html
        if not obj.method:
            return '-'
        color = self.METHOD_COLORS.get(obj.method, '#6b7280')
        return format_html(
            '<span style="color:{}; font-weight:bold; font-size:11px;">{}</span>',
            color, obj.method
        )
    colored_method.short_description = 'Method'
    colored_method.admin_order_field = 'method'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(UserRole, UserRoleAdmin)
admin.site.register(UserActivity, UserActivityAdmin)
