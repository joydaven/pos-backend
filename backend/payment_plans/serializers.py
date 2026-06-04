from rest_framework import serializers
from .models import PaymentPlanTemplate, PaymentPlan, PaymentPlanInstallment


class PaymentPlanTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentPlanTemplate
        fields = [
            'id', 'name', 'description', 'num_installments',
            'installments_config', 'service_charge_type', 'service_charge_value',
            'is_active', 'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'created_by', 'created_at', 'updated_at')


class PaymentPlanInstallmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentPlanInstallment
        fields = [
            'id', 'plan', 'installment_number', 'amount', 'due_date',
            'status', 'payment_method', 'card_override', 'transaction_id',
            'paid_at', 'paid_amount', 'failure_reason', 'retry_count',
            'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ('id', 'plan', 'created_at', 'updated_at')


class PaymentPlanSerializer(serializers.ModelSerializer):
    installments = PaymentPlanInstallmentSerializer(many=True, read_only=True)
    total_paid = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    remaining_balance = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    customer_name = serializers.SerializerMethodField()
    order_number = serializers.SerializerMethodField()
    next_due_date = serializers.SerializerMethodField()
    next_due_amount = serializers.SerializerMethodField()

    class Meta:
        model = PaymentPlan
        fields = [
            'id', 'plan_number', 'pos_order', 'contact', 'template',
            'total_amount', 'original_amount', 'service_charge_type',
            'service_charge_value', 'service_charge_amount',
            'status', 'payment_method', 'notes',
            'created_by', 'created_at', 'updated_at',
            'installments', 'total_paid', 'remaining_balance',
            'customer_name', 'order_number', 'next_due_date', 'next_due_amount'
        ]
        read_only_fields = ('id', 'plan_number', 'created_by', 'created_at', 'updated_at')

    def get_customer_name(self, obj):
        if obj.contact:
            first = obj.contact.first_name or ''
            last = obj.contact.last_name or ''
            return f"{first} {last}".strip() or str(obj.contact)
        return ''

    def get_order_number(self, obj):
        if obj.pos_order:
            return obj.pos_order.order_number
        return ''

    def get_next_due_date(self, obj):
        inst = obj.next_due_installment
        return inst.due_date.isoformat() if inst else None

    def get_next_due_amount(self, obj):
        inst = obj.next_due_installment
        return str(inst.amount) if inst else None


class PaymentPlanCreateSerializer(serializers.Serializer):
    """Serializer for creating a payment plan with installments in one request."""
    pos_order_id = serializers.UUIDField()
    contact_id = serializers.UUIDField(required=False, allow_null=True)
    template_id = serializers.UUIDField(required=False, allow_null=True)
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    original_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    service_charge_type = serializers.CharField(required=False, default='none')
    service_charge_value = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0)
    service_charge_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0)
    payment_method = serializers.JSONField(required=False, default=dict)
    notes = serializers.CharField(required=False, default='', allow_blank=True)
    installments = serializers.ListField(
        child=serializers.DictField(),
        help_text="Array of {amount, due_date, payment_method?} for each installment"
    )
