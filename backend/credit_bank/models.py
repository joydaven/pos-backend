import uuid
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User


class CreditBankAccount(models.Model):
    """
    Credit Bank account for a customer — stores a dollar balance 
    that can be used as payment at checkout. Entirely POS-side,
    no WooCommerce plugin dependency.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField(
        'crm.Contact',
        on_delete=models.CASCADE,
        related_name='credit_bank_account'
    )
    balance = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Current credit bank balance in dollars"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'credit_bank_account'
        verbose_name = 'Credit Bank Account'
        verbose_name_plural = 'Credit Bank Accounts'
        indexes = [
            models.Index(fields=['customer']),
        ]

    def __str__(self):
        return f"{self.customer.first_name} {self.customer.last_name} — ${self.balance}"

    def add_credits(self, amount, order_id=None, description="Credits added", user=None):
        """Add credits and create a transaction record."""
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError("Amount must be positive")

        self.balance += amount
        self.save()

        return CreditBankTransaction.objects.create(
            account=self,
            customer=self.customer,
            transaction_type='credit',
            amount=amount,
            balance_after=self.balance,
            order_id=order_id,
            description=description,
            created_by=user,
        )

    def redeem_credits(self, amount, order_id=None, description="Credits redeemed", user=None):
        """Redeem credits and create a transaction record."""
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError("Amount must be positive")
        if self.balance < amount:
            raise ValueError(
                f"Insufficient credits. Available: ${self.balance}, Requested: ${amount}"
            )

        self.balance -= amount
        self.save()

        return CreditBankTransaction.objects.create(
            account=self,
            customer=self.customer,
            transaction_type='debit',
            amount=-amount,
            balance_after=self.balance,
            order_id=order_id,
            description=description,
            created_by=user,
        )


class CreditBankTransaction(models.Model):
    """Full audit trail for every credit bank balance change."""
    TRANSACTION_TYPES = [
        ('credit', 'Credit Added'),
        ('debit', 'Credit Redeemed'),
        ('adjust', 'Manual Adjustment'),
        ('refund', 'Refund'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account = models.ForeignKey(
        CreditBankAccount,
        on_delete=models.CASCADE,
        related_name='transactions',
    )
    customer = models.ForeignKey(
        'crm.Contact',
        on_delete=models.CASCADE,
        related_name='credit_bank_transactions',
    )
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Positive for credits added, negative for debits",
    )
    balance_after = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Account balance after this transaction",
    )
    order_id = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = 'credit_bank_transaction'
        verbose_name = 'Credit Bank Transaction'
        verbose_name_plural = 'Credit Bank Transactions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer', '-created_at']),
            models.Index(fields=['account', '-created_at']),
            models.Index(fields=['transaction_type']),
            models.Index(fields=['order_id']),
        ]

    def __str__(self):
        return (
            f"{self.customer.first_name} {self.customer.last_name} — "
            f"{self.transaction_type} — ${self.amount}"
        )
