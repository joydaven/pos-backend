from django.contrib import admin
from .models import CreditBankAccount, CreditBankTransaction


@admin.register(CreditBankAccount)
class CreditBankAccountAdmin(admin.ModelAdmin):
    list_display = ('customer', 'balance', 'created_at', 'updated_at')
    search_fields = ('customer__first_name', 'customer__last_name', 'customer__email')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(CreditBankTransaction)
class CreditBankTransactionAdmin(admin.ModelAdmin):
    list_display = ('customer', 'transaction_type', 'amount', 'balance_after', 'order_id', 'created_at')
    list_filter = ('transaction_type',)
    search_fields = ('customer__first_name', 'customer__last_name', 'customer__email', 'order_id')
    readonly_fields = ('id', 'created_at')
