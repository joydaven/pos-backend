import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class PaymentPlanTemplate(models.Model):
    """
    Preset payment plan templates created by staff.
    Example: "50/25/25 — 3 Monthly Installments"
    """
    SERVICE_CHARGE_TYPE_CHOICES = (
        ('none', 'No Service Charge'),
        ('percentage', 'Percentage'),
        ('flat', 'Flat Fee'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, help_text="Display name, e.g. '50/25/25 over 3 months'")
    description = models.TextField(blank=True, default='')
    num_installments = models.PositiveIntegerField(help_text="Total number of installments including the first payment")
    installments_config = models.JSONField(
        help_text="Array of {percentage: number, offset_days: number} for each installment"
    )
    service_charge_type = models.CharField(
        max_length=20, choices=SERVICE_CHARGE_TYPE_CHOICES, default='none',
        help_text="Type of service charge: none, percentage, or flat fee"
    )
    service_charge_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Service charge value: percentage (e.g. 5.00 for 5%) or flat amount (e.g. 500.00)"
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_plan_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_plan_template'
        ordering = ['name']
        verbose_name = 'Payment Plan Template'
        verbose_name_plural = 'Payment Plan Templates'

    def __str__(self):
        return self.name


class PaymentPlan(models.Model):
    """
    An active payment plan tied to a POS order.
    The full order is posted to WooCommerce once; installments are tracked here.
    """
    STATUS_CHOICES = (
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('defaulted', 'Defaulted'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_number = models.CharField(max_length=100, unique=True, help_text="e.g. PP-10001")
    pos_order = models.OneToOneField(
        'crm.POSOrder', on_delete=models.CASCADE, related_name='payment_plan',
        help_text="The POS order this plan covers"
    )
    contact = models.ForeignKey(
        'crm.Contact', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='payment_plans'
    )
    template = models.ForeignKey(
        PaymentPlanTemplate, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='plans', help_text="Null if custom plan"
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Full plan total including service charge")
    original_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Original order amount before service charge"
    )
    service_charge_type = models.CharField(
        max_length=20, default='none',
        help_text="Type of service charge applied: none, percentage, flat"
    )
    service_charge_value = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Service charge value used (percentage or flat amount)"
    )
    service_charge_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0,
        help_text="Calculated service charge dollar amount"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    payment_method = models.JSONField(
        default=dict, blank=True,
        help_text="Default card for auto-billing: {customer_profile_id, payment_profile_id, last4, brand}"
    )
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_payment_plans'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_plan'
        ordering = ['-created_at']
        verbose_name = 'Payment Plan'
        verbose_name_plural = 'Payment Plans'

    def __str__(self):
        return f"{self.plan_number} — ${self.total_amount}"

    @property
    def total_paid(self):
        return self.installments.filter(status='paid').aggregate(
            total=models.Sum('paid_amount')
        )['total'] or 0

    @property
    def remaining_balance(self):
        return self.total_amount - self.total_paid

    @property
    def next_due_installment(self):
        return self.installments.filter(
            status__in=['pending', 'scheduled', 'overdue', 'failed']
        ).order_by('due_date').first()

    @classmethod
    def generate_plan_number(cls):
        """Generate the next sequential plan number."""
        last = cls.objects.order_by('-created_at').first()
        if last and last.plan_number.startswith('PP-'):
            try:
                num = int(last.plan_number.split('-')[1]) + 1
            except (ValueError, IndexError):
                num = 10001
        else:
            num = 10001
        return f"PP-{num}"


class PaymentPlanInstallment(models.Model):
    """
    Individual scheduled payment within a payment plan.
    """
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('scheduled', 'Scheduled'),
        ('processing', 'Processing'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('overdue', 'Overdue'),
        ('cancelled', 'Cancelled'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(PaymentPlan, on_delete=models.CASCADE, related_name='installments')
    installment_number = models.PositiveIntegerField(help_text="1-indexed installment number")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_method = models.JSONField(
        default=dict, blank=True,
        help_text="Payment method used/recorded for this installment after payment"
    )
    card_override = models.JSONField(
        default=dict, blank=True,
        help_text="Per-installment card override for auto-billing: {customer_profile_id, payment_profile_id, last4, brand}. If empty, uses plan default."
    )
    transaction_id = models.CharField(max_length=100, blank=True, default='')
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    failure_reason = models.TextField(blank=True, default='')
    retry_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payment_plan_installment'
        ordering = ['installment_number']
        verbose_name = 'Payment Plan Installment'
        verbose_name_plural = 'Payment Plan Installments'
        unique_together = ['plan', 'installment_number']

    def __str__(self):
        return f"Installment {self.installment_number} of {self.plan.plan_number} — ${self.amount}"

    def mark_paid(self, amount=None, transaction_id='', payment_method=None):
        """Mark this installment as paid."""
        self.status = 'paid'
        self.paid_at = timezone.now()
        self.paid_amount = amount or self.amount
        self.transaction_id = transaction_id
        if payment_method:
            self.payment_method = payment_method
        self.save()
        # Check if all installments are paid → complete the plan
        plan = self.plan
        if not plan.installments.exclude(status__in=['paid', 'cancelled']).exists():
            plan.status = 'completed'
            plan.save()
