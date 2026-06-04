from django.contrib import admin
from .models import Contact, Order, Product, OAuth2Token, TokenRequestLog, POSLocation, POSSetting, EmailLog, WooCreditedService, CreditServicePoints, WooCreditLog, WooServiceTypes
from .admin_site import crm_admin_site
from django.utils.html import format_html
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
import logging

# Register your models here.

class HasWooFilter(admin.SimpleListFilter):
    """Filter contacts by WooCommerce integration status"""
    title = 'WooCommerce'
    parameter_name = 'has_woo'
    
    def lookups(self, request, model_admin):
        return (
            ('yes', 'Has WooCommerce'),
            ('no', 'No WooCommerce'),
        )
    
    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(woo_customer_id__isnull=True)
        if self.value() == 'no':
            return queryset.filter(woo_customer_id__isnull=True)

class HasGHLFilter(admin.SimpleListFilter):
    """Filter contacts by GoHighLevel integration status"""
    title = 'GoHighLevel'
    parameter_name = 'has_ghl'
    
    def lookups(self, request, model_admin):
        return (
            ('yes', 'Has GoHighLevel'),
            ('no', 'No GoHighLevel'),
        )
    
    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(ghl_contact_id__isnull=True)
        if self.value() == 'no':
            return queryset.filter(ghl_contact_id__isnull=True)

class CombinedSourceFilter(admin.SimpleListFilter):
    """Filter contacts by combined data sources"""
    title = 'Combined Sources'
    parameter_name = 'combined_source'
    
    def lookups(self, request, model_admin):
        return (
            ('woo_only', 'WooCommerce Only'),
            ('ghl_only', 'GoHighLevel Only'),
            ('woo_and_ghl', 'WooCommerce AND GoHighLevel'),
            ('crm_only', 'CRM Only (No Integrations)'),
        )
    
    def queryset(self, request, queryset):
        if self.value() == 'woo_only':
            return queryset.filter(woo_customer_id__isnull=False, ghl_contact_id__isnull=True)
        if self.value() == 'ghl_only':
            return queryset.filter(ghl_contact_id__isnull=False, woo_customer_id__isnull=True)
        if self.value() == 'woo_and_ghl':
            return queryset.filter(woo_customer_id__isnull=False, ghl_contact_id__isnull=False)
        if self.value() == 'crm_only':
            return queryset.filter(woo_customer_id__isnull=True, ghl_contact_id__isnull=True)

class ContactAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Core Contact Information', {
            'fields': ('first_name', 'last_name', 'email', 'phone', 'normalized_phone',
                      'billing_address', 'billing_city', 'billing_state', 'billing_postcode')
        }),
        ('Integration Status', {
            'fields': ('primary_source', 'sources_display',
                      'woo_customer_id', 'woo_last_sync',
                      'ghl_contact_id', 'ghl_last_sync'),
            'classes': ('collapse',),
        }),
        ('GoHighLevel Data', {
            'fields': ('ghl_tags', 'ghl_custom_fields', 'ghl_data'),
            'classes': ('collapse',),
        }),
        ('WooCommerce Data', {
            'fields': ('woo_data',),
            'classes': ('collapse',),
        }),
        ('System Fields', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
    
    list_display = ('full_name', 'email', 'phone', 'billing_city', 'billing_state', 
                   'sources_display')
    search_fields = ('first_name', 'last_name', 'email', 'phone', 'normalized_phone',
                    'billing_city', 'billing_state', 'ghl_contact_id')
    list_filter = (CombinedSourceFilter, HasWooFilter, HasGHLFilter)  
    ordering = ('last_name', 'first_name')
    readonly_fields = ('id', 'created_at', 'updated_at', 'normalized_phone', 'sources_display')
    
    def full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()
    full_name.short_description = 'Name'
    
    def has_woo(self, obj):
        return obj.has_woo
    has_woo.boolean = True
    has_woo.short_description = 'WooCommerce'
    
    def has_ghl(self, obj):
        return obj.has_ghl
    has_ghl.boolean = True
    has_ghl.short_description = 'GoHighLevel'
    
    def sources_display(self, obj):
        """Display all sources for this contact with colored tags"""
        sources_html = []
        
        if obj.has_woo:
            tag_style = "background-color: purple; color: white; padding: 3px 7px; border-radius: 10px; margin-right: 5px; display: inline-block;"
            # Add star for primary source
            if obj.primary_source == 'woo':
                tag_style += "border: 2px solid gold;"
            sources_html.append(f'<span style="{tag_style}">WOO</span>')
        
        if obj.has_ghl:
            tag_style = "background-color: skyblue; color: white; padding: 3px 7px; border-radius: 10px; margin-right: 5px; display: inline-block;"
            # Add star for primary source
            if obj.primary_source == 'ghl':
                tag_style += "border: 2px solid gold;"
            sources_html.append(f'<span style="{tag_style}">GHL</span>')
        
        if not sources_html or obj.primary_source == 'crm':
            tag_style = "background-color: #999; color: white; padding: 3px 7px; border-radius: 10px; display: inline-block;"
            # Add star for primary source
            if obj.primary_source == 'crm':
                tag_style += "border: 2px solid gold;"
            sources_html.append(f'<span style="{tag_style}">CRM</span>')
        
        # Add multi-source indicator
        if obj.is_multi_source:
            return format_html('<span style="display: flex; align-items: center;"><span style="margin-right: 5px;">🔄</span>{}</span>', format_html(''.join(sources_html)))
        
        return format_html(''.join(sources_html))
    
    sources_display.short_description = 'Data Sources'
    sources_display.allow_tags = True
    
    def primary_source_display(self, obj):
        """Display primary source with a colored tag"""
        source_colors = {
            'woo': 'purple',
            'ghl': 'skyblue',
            'crm': '#999'
        }
        source_names = {
            'woo': 'WooCommerce',
            'ghl': 'GoHighLevel',
            'crm': 'CRM'
        }
        color = source_colors.get(obj.primary_source, '#999')
        name = source_names.get(obj.primary_source, 'Unknown')
        
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 7px; '
            'border-radius: 10px; display: inline-block;">{}</span>',
            color, name
        )
    
    primary_source_display.short_description = 'Primary Source'
    primary_source_display.allow_tags = True

class OrderAdmin(admin.ModelAdmin):
    list_display = ('woo_order_id', 'contact', 'order_date', 'total_amount', 'status')
    search_fields = ('woo_order_id', 'contact__email', 'contact__first_name', 'contact__last_name')
    list_filter = ('status', 'order_date')

class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'woo_product_id', 'price', 'regular_price', 'stock_status', 'stock_quantity')
    search_fields = ('name', 'woo_product_id', 'description')
    list_filter = ('stock_status',)
    ordering = ('-created_at',)

class OAuth2TokenAdmin(admin.ModelAdmin):
    list_display = ('location_id', 'is_expired', 'created_at', 'updated_at')
    readonly_fields = ('id', 'created_at', 'updated_at', 'is_expired')
    search_fields = ('location_id',)
    list_filter = ('created_at', 'updated_at')
    
    def has_delete_permission(self, request, obj=None):
        return False

class TokenRequestLogAdmin(admin.ModelAdmin):
    list_display = ('request_type', 'status', 'token', 'created_at')
    list_filter = ('request_type', 'status', 'created_at')
    search_fields = ('token__location_id', 'error_message')
    readonly_fields = ('id', 'token', 'request_type', 'status', 'error_message', 
                      'request_data', 'response_data', 'created_at')
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return False

class POSLocationAdmin(admin.ModelAdmin):
    """Admin interface for POS Location management"""
    list_display = ('name', 'location', 'location_id', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'location', 'location_id')
    readonly_fields = ('id', 'created_at', 'updated_at')
    fieldsets = (
        ('Location Information', {
            'fields': ('name', 'location', 'location_id', 'is_active')
        }),
        ('System Information', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def get_readonly_fields(self, request, obj=None):
        # Make location_id readonly after creation to prevent accidental changes
        if obj:  # editing an existing object
            return self.readonly_fields + ('location_id',)
        return self.readonly_fields

class EmailLogAdmin(admin.ModelAdmin):
    """Read-only admin for viewing email delivery audit logs."""
    list_display = ('email_type_display', 'recipient', 'status_badge', 'delivery_backend', 'related_order', 'subject_short', 'created_at')
    list_filter = ('email_type', 'status', 'delivery_backend', 'created_at')
    search_fields = ('recipient', 'subject', 'related_order')
    readonly_fields = ('id', 'email_type', 'recipient', 'subject', 'status', 'delivery_backend',
                       'error_message', 'related_order', 'metadata', 'created_at')
    ordering = ('-created_at',)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True

    def email_type_display(self, obj):
        return obj.get_email_type_display()
    email_type_display.short_description = 'Email Type'
    email_type_display.admin_order_field = 'email_type'

    def subject_short(self, obj):
        return obj.subject[:80] + '...' if len(obj.subject) > 80 else obj.subject
    subject_short.short_description = 'Subject'

    def status_badge(self, obj):
        colors = {'sent': '#16a34a', 'failed': '#dc2626', 'skipped': '#9ca3af'}
        color = colors.get(obj.status, '#6b7280')
        label = obj.get_status_display()
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;">{}</span>',
            color, label
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'


class POSSettingAdmin(admin.ModelAdmin):
    """Admin for managing POS settings (email toggles, etc.)."""
    list_display = ('key', 'category_badge', 'value_display', 'setting_type', 'description_short', 'updated_at', 'updated_by')
    list_filter = ('category', 'setting_type')
    search_fields = ('key', 'description')
    readonly_fields = ('created_at', 'updated_at', 'updated_by')
    ordering = ('category', 'key')
    list_per_page = 25
    fieldsets = (
        ('Setting', {
            'fields': ('key', 'value', 'setting_type', 'category'),
        }),
        ('Description', {
            'fields': ('description',),
        }),
        ('Audit', {
            'fields': ('updated_by', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def value_display(self, obj):
        """Show colored badge for booleans, truncated preview for JSON, plain text otherwise."""
        if obj.setting_type == 'boolean':
            is_true = obj.value.lower() in ('true', '1', 'yes', 'on')
            if is_true:
                return format_html(
                    '<span style="background:#16a34a;color:#fff;padding:2px 10px;border-radius:8px;font-size:11px;">&#10003; true</span>'
                )
            return format_html(
                '<span style="background:#dc2626;color:#fff;padding:2px 10px;border-radius:8px;font-size:11px;">&#10007; false</span>'
            )
        if obj.setting_type == 'json':
            preview = obj.value[:60]
            if len(obj.value) > 60:
                preview += '…'
            return format_html(
                '<code style="background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:12px;">{}</code>',
                preview
            )
        return obj.value
    value_display.short_description = 'Value'
    value_display.admin_order_field = 'value'

    def category_badge(self, obj):
        """Colored category label."""
        colors = {
            'email': '#8b5cf6',
            'shipping': '#0891b2',
            'inventory': '#d97706',
            'display': '#2563eb',
            'general': '#6b7280',
            'categories': '#059669',
            'payment_plans': '#db2777',
        }
        color = colors.get(obj.category, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;white-space:nowrap;">{}</span>',
            color, obj.category
        )
    category_badge.short_description = 'Category'
    category_badge.admin_order_field = 'category'

    def description_short(self, obj):
        """Truncated description for the list view."""
        if not obj.description:
            return '—'
        if len(obj.description) > 80:
            return obj.description[:80] + '…'
        return obj.description
    description_short.short_description = 'Description'

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# ── Credited Services Admin ──────────────────────────────────────────────────

class WooCreditedServiceAdmin(admin.ModelAdmin):
    """Admin for legacy credited services (per-customer product credits)."""
    list_display = ('id', 'customer_name', 'contact_email', 'product_name', 'product_points', 'contact_woo_id', 'contact_ghl_id', 'updated_at')
    search_fields = ('contact_fname', 'contact_lname', 'contact_email', 'product_name', 'contact_woo_id', 'contact_ghl_id')
    list_filter = ('product_name', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    ordering = ('-updated_at',)
    list_per_page = 50

    fieldsets = (
        ('Contact', {
            'fields': ('contact_fname', 'contact_lname', 'contact_email', 'contact_phone', 'contact_woo_id', 'contact_ghl_id'),
        }),
        ('Product & Credits', {
            'fields': ('product_id', 'product_name', 'product_points'),
        }),
        ('System', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def customer_name(self, obj):
        return f"{obj.contact_fname or ''} {obj.contact_lname or ''}".strip() or '\u2014'
    customer_name.short_description = 'Customer'
    customer_name.admin_order_field = 'contact_fname'


class CreditServicePointsAdmin(admin.ModelAdmin):
    """Admin for new credit service points (UUID-based, FK to Contact & Product)."""
    list_display = ('customer_name', 'product_name_display', 'points', 'points_badge', 'contact_woo_id', 'contact_ghl_id', 'updated_at')
    search_fields = ('customer__first_name', 'customer__last_name', 'customer__email', 'product__name', 'contact_ghl_id')
    list_filter = ('points', 'created_at')
    readonly_fields = ('id', 'created_at', 'updated_at')
    ordering = ('-updated_at',)
    list_per_page = 50
    raw_id_fields = ('customer', 'product')

    fieldsets = (
        ('Customer & Product', {
            'fields': ('customer', 'product'),
        }),
        ('Credits', {
            'fields': ('points', 'contact_ghl_id', 'contact_woo_id'),
        }),
        ('System', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def customer_name(self, obj):
        return f"{obj.customer.first_name} {obj.customer.last_name}".strip()
    customer_name.short_description = 'Customer'
    customer_name.admin_order_field = 'customer__first_name'

    def product_name_display(self, obj):
        return obj.product.name
    product_name_display.short_description = 'Product'
    product_name_display.admin_order_field = 'product__name'

    def points_badge(self, obj):
        if obj.points > 0:
            color = '#16a34a'
        elif obj.points == 0:
            color = '#9ca3af'
        else:
            color = '#dc2626'
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 10px;border-radius:8px;font-size:12px;font-weight:600;">{}</span>',
            color, obj.points
        )
    points_badge.short_description = 'Points'
    points_badge.admin_order_field = 'points'


class WooCreditLogAdmin(admin.ModelAdmin):
    """Admin for credit transaction audit logs. Source field is editable; other fields are read-only."""
    list_display = ('id', 'woo_credited_service_id', 'action_badge', 'points_change', 'updated_points', 'order_number', 'source_badge', 'created_at')
    search_fields = ('order_number', 'reason', 'notes')
    list_filter = ('action', 'source', 'created_at')
    readonly_fields = ('id', 'woo_credited_service_id', 'action', 'order_number', 'points_change',
                       'updated_points', 'reason', 'notes', 'created_at', 'updated_at')
    ordering = ('-created_at',)
    list_per_page = 50

    fieldsets = (
        ('Transaction', {
            'fields': ('woo_credited_service_id', 'action', 'order_number', 'points_change', 'updated_points'),
        }),
        ('Details', {
            'fields': ('reason', 'notes', 'source'),
        }),
        ('System', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def action_badge(self, obj):
        colors = {
            'add': '#16a34a',
            'redeem': '#dc2626',
            'subtract': '#dc2626',
            'manual_add': '#2563eb',
            'manual_subtract': '#d97706',
        }
        color = colors.get(obj.action, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;">{}</span>',
            color, obj.action
        )
    action_badge.short_description = 'Action'
    action_badge.admin_order_field = 'action'

    def source_badge(self, obj):
        colors = {'POS': '#8b5cf6', 'MANUAL': '#d97706', 'WEB': '#0891b2'}
        color = colors.get(obj.source, '#6b7280')
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:8px;font-size:11px;">{}</span>',
            color, obj.source
        )
    source_badge.short_description = 'Source'
    source_badge.admin_order_field = 'source'


class WooServiceTypesAdmin(admin.ModelAdmin):
    """Admin for service type definitions (labs & services eligible for credits)."""
    list_display = ('id', 'name', 'series_display', 'default_index', 'created_at', 'updated_at')
    search_fields = ('name',)
    list_filter = ('created_at',)
    readonly_fields = ('id', 'created_at', 'updated_at')
    ordering = ('name',)
    list_per_page = 50

    def series_display(self, obj):
        if not obj.series:
            return '\u2014'
        return ', '.join(str(s) for s in obj.series)
    series_display.short_description = 'Allowed Series'


# Register models with both admin sites
_admin_logger = logging.getLogger(__name__)

_model_admin_pairs = [
    (Contact, ContactAdmin),
    (Order, OrderAdmin),
    (Product, ProductAdmin),
    (OAuth2Token, OAuth2TokenAdmin),
    (TokenRequestLog, TokenRequestLogAdmin),
    (POSLocation, POSLocationAdmin),
    (EmailLog, EmailLogAdmin),
    (POSSetting, POSSettingAdmin),
    (WooCreditedService, WooCreditedServiceAdmin),
    (CreditServicePoints, CreditServicePointsAdmin),
    (WooCreditLog, WooCreditLogAdmin),
    (WooServiceTypes, WooServiceTypesAdmin),
]

for _model, _admin_class in _model_admin_pairs:
    # Custom admin site
    try:
        crm_admin_site.register(_model, _admin_class)
    except admin.sites.AlreadyRegistered:
        pass  # harmless on reload
    except Exception as e:
        _admin_logger.warning(f"crm_admin_site: Failed to register {_model.__name__}: {e}")
        import sys; print(f"[CRM ADMIN] crm_admin_site: Failed to register {_model.__name__}: {e}", file=sys.stderr)

    # Default admin site (backward compatibility)
    try:
        admin.site.register(_model, _admin_class)
    except admin.sites.AlreadyRegistered:
        pass  # harmless on reload
    except Exception as e:
        _admin_logger.warning(f"admin.site: Failed to register {_model.__name__}: {e}")
        import sys; print(f"[CRM ADMIN] admin.site: Failed to register {_model.__name__}: {e}", file=sys.stderr)
