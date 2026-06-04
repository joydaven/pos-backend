from django.db import models
from django.utils import timezone
import uuid

# Create your models here.

class PaymentTransaction(models.Model):
    """
    Model to store payment transaction information
    """
    PAYMENT_STATUS_CHOICES = (
        ('approved', 'Approved'),
        ('declined', 'Declined'),
        ('error', 'Error'),
        ('voided', 'Voided'),
        ('refunded', 'Refunded'),
    )
    
    PAYMENT_TYPE_CHOICES = (
        ('credit', 'Credit Card'),
        ('cash', 'Cash'),
        ('other', 'Other'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=255, unique=True, null=True, blank=True, db_index=True)
    transaction_id = models.CharField(max_length=100, unique=True)
    order_id = models.CharField(max_length=100, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='approved')
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPE_CHOICES, default='credit')
    auth_code = models.CharField(max_length=50, blank=True, null=True)
    response_code = models.CharField(max_length=50, blank=True, null=True)
    response_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Payment Transaction'
        verbose_name_plural = 'Payment Transactions'
    
    def __str__(self):
        return f"{self.transaction_id} - ${self.amount} ({self.status})"

class PaymentCardNote(models.Model):
    """
    Internal staff notes attached to a payment profile.
    Keyed by CIM payment_profile_id so notes persist for CIM-sourced cards
    that may not have a local PaymentCard record.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('crm.Contact', on_delete=models.CASCADE, related_name='payment_card_notes', null=True)
    payment_profile_id = models.CharField(max_length=100, db_index=True)
    note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Payment Card Note'
        verbose_name_plural = 'Payment Card Notes'
        unique_together = ['customer', 'payment_profile_id']

    def __str__(self):
        return f"Note for payment profile {self.payment_profile_id}"


class PaymentCard(models.Model):
    """
    Model for storing payment card information securely using tokens
    
    Instead of storing actual card details, we store tokens from Authorize.net's
    Customer Information Manager (CIM) system.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('crm.Contact', on_delete=models.CASCADE, related_name='payment_cards', null=True)
    order_id = models.CharField(max_length=50, blank=True, null=True)
    
    # Store only the last 4 digits of the card number
    last4 = models.CharField(max_length=4, blank=True, null=True)
    
    # Store the card brand (Visa, Mastercard, etc.)
    card_brand = models.CharField(max_length=50, blank=True, null=True)
    
    # Store expiration date (MM/YY format)
    exp_month = models.CharField(max_length=2, blank=True, null=True)
    exp_year = models.CharField(max_length=4, blank=True, null=True)
    
    # Store Authorize.net tokens instead of actual card details
    customer_profile_id = models.CharField(max_length=100, blank=True, null=True)
    payment_profile_id = models.CharField(max_length=100, blank=True, null=True)
    
    # Track when the card was added and last used
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    
    # Is this the customer's default payment method?
    is_default = models.BooleanField(default=False)
    
    # Billing address fields
    billing_street = models.CharField(max_length=255, blank=True, null=True)
    billing_city = models.CharField(max_length=100, blank=True, null=True)
    billing_state = models.CharField(max_length=50, blank=True, null=True)
    billing_zip = models.CharField(max_length=20, blank=True, null=True)
    billing_country = models.CharField(max_length=50, default='USA', blank=True, null=True)
    
    class Meta:
        verbose_name = 'Payment Card'
        verbose_name_plural = 'Payment Cards'
        
    def __str__(self):
        brand = self.card_brand or 'Card'
        last4 = self.last4 or 'xxxx'
        return f"{brand} ending in {last4}"
