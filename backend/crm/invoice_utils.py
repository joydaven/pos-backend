"""
Invoice token generation and validation utilities.

Uses Django's TimestampSigner for tamper-proof, expiring tokens.
Token encodes the invoice UUID — the signer prevents forgery and the
max_age parameter enforces expiry.
"""
import logging
from datetime import timedelta
from django.core.signing import TimestampSigner, SignatureExpired, BadSignature
from django.utils import timezone

logger = logging.getLogger(__name__)

TOKEN_MAX_AGE_HOURS = 24
_signer = TimestampSigner(salt='invoice-payment-token')


def generate_invoice_token(invoice_id: str) -> tuple[str, 'datetime']:
    """
    Generate a signed, time-limited payment token for an invoice.

    Args:
        invoice_id: The invoice UUID (as string).

    Returns:
        (token_string, expiry_datetime) tuple.
    """
    token = _signer.sign(str(invoice_id))
    expires_at = timezone.now() + timedelta(hours=TOKEN_MAX_AGE_HOURS)
    return token, expires_at


def validate_invoice_token(token: str):
    """
    Validate a signed invoice payment token.

    Returns the Invoice instance if valid, or raises an appropriate exception.
    Checks: signature integrity, expiry, invoice existence, invoice status.

    Raises:
        InvalidTokenError — with a human-readable .reason attribute
    """
    from .models import Invoice

    # 1. Verify cryptographic signature + time expiry
    try:
        invoice_id = _signer.unsign(token, max_age=TOKEN_MAX_AGE_HOURS * 3600)
    except SignatureExpired:
        raise InvalidTokenError('expired', 'This payment link has expired. Please contact the office for a new invoice.')
    except BadSignature:
        raise InvalidTokenError('invalid', 'Invalid payment link.')

    # 2. Look up the invoice
    try:
        invoice = Invoice.objects.select_related('contact').get(pk=invoice_id)
    except Invoice.DoesNotExist:
        raise InvalidTokenError('not_found', 'Invoice not found.')

    # 3. Check invoice status
    if invoice.status == 'paid':
        raise InvalidTokenError('already_paid', 'This invoice has already been paid.', invoice=invoice)
    if invoice.status == 'cancelled':
        raise InvalidTokenError('cancelled', 'This invoice has been cancelled. Please contact the office.')
    if invoice.status == 'locked':
        raise InvalidTokenError('locked', 'This invoice has been locked due to too many failed payment attempts. Please contact the office.')
    if invoice.status == 'expired':
        raise InvalidTokenError('expired', 'This invoice has expired. Please contact the office for a new invoice.')

    # 4. Double-check the stored expiry (belt-and-suspenders with TimestampSigner)
    if invoice.token_expires_at and timezone.now() > invoice.token_expires_at:
        invoice.status = 'expired'
        invoice.save(update_fields=['status', 'updated_at'])
        raise InvalidTokenError('expired', 'This payment link has expired. Please contact the office for a new invoice.')

    return invoice


class InvalidTokenError(Exception):
    """Raised when an invoice payment token is invalid."""

    def __init__(self, code: str, reason: str, invoice=None):
        self.code = code
        self.reason = reason
        self.invoice = invoice
        super().__init__(reason)
