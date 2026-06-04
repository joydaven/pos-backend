from django.contrib import admin
from .models import PaymentTransaction, PaymentCard

# Register your models here.

@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    """
    Admin configuration for PaymentTransaction model
    """
    list_display = ('transaction_id', 'order_id', 'amount', 'status', 'payment_type', 'created_at')
    list_filter = ('status', 'payment_type', 'created_at')
    search_fields = ('transaction_id', 'order_id', 'auth_code')
    readonly_fields = ('id', 'transaction_id', 'created_at', 'updated_at')
    fieldsets = (
        ('Transaction Information', {
            'fields': ('id', 'transaction_id', 'order_id', 'amount', 'status', 'payment_type')
        }),
        ('Response Details', {
            'fields': ('auth_code', 'response_code', 'response_message')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

@admin.register(PaymentCard)
class PaymentCardAdmin(admin.ModelAdmin):
    """
    Admin configuration for PaymentCard model
    """
    list_display = ('order_id', 'last4', 'card_brand', 'created_at', 'customer_profile_id')
    search_fields = ('order_id', 'last4', 'cardholder_name', 'customer_profile_id')
    readonly_fields = ('id', 'created_at')
    fieldsets = (
        ('Card Information', {
            'fields': ('id', 'order_id', 'last4', 'card_brand', 'cardholder_name')
        }),
        ('Authorize.net CIM', {
            'fields': ('customer_profile_id', 'payment_profile_id')
        }),
        ('Timestamps', {
            'fields': ('created_at',)
        }),
    )
