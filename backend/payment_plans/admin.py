from django.contrib import admin
from .models import PaymentPlanTemplate, PaymentPlan, PaymentPlanInstallment


class PaymentPlanInstallmentInline(admin.TabularInline):
    model = PaymentPlanInstallment
    extra = 0
    readonly_fields = ('id', 'created_at', 'updated_at')
    fields = ('installment_number', 'amount', 'due_date', 'status', 'paid_at', 'paid_amount', 'transaction_id')


@admin.register(PaymentPlanTemplate)
class PaymentPlanTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'num_installments', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(PaymentPlan)
class PaymentPlanAdmin(admin.ModelAdmin):
    list_display = ('plan_number', 'contact', 'total_amount', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('plan_number',)
    readonly_fields = ('id', 'created_at', 'updated_at')
    inlines = [PaymentPlanInstallmentInline]


@admin.register(PaymentPlanInstallment)
class PaymentPlanInstallmentAdmin(admin.ModelAdmin):
    list_display = ('plan', 'installment_number', 'amount', 'due_date', 'status', 'paid_at')
    list_filter = ('status',)
    search_fields = ('plan__plan_number',)
    readonly_fields = ('id', 'created_at', 'updated_at')
