"""
Unified email sender for the POS system.

All outbound customer-facing emails should flow through send_pos_email().
It handles:
  - Toggle check (POSSetting) — skips if the email type is disabled
  - Mailgun (primary) → SMTP (fallback) delivery
  - EmailLog audit record on every attempt (sent / failed / skipped)
"""

import os
import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection

logger = logging.getLogger(__name__)

# Maps email_type → POSSetting key used for the on/off toggle.
# If a type is not listed here, it is always enabled.
EMAIL_TOGGLE_KEYS = {
    'order_receipt': 'email_order_receipt_enabled',
    'refund_receipt': 'email_refund_receipt_enabled',
    'auto_refund_receipt': 'email_auto_refund_receipt_enabled',
    'cancellation': 'email_cancellation_enabled',
    'tracking': 'email_tracking_enabled',
    'partial_tracking': 'email_partial_tracking_enabled',
    'installment_receipt': 'email_installment_receipt_enabled',
    'plan_summary': 'email_plan_summary_enabled',
    'membership_onboarding': 'membership_onboarding_email_enabled',
    'membership_cancellation': 'membership_cancellation_email_enabled',
    'woo_order_receipt': 'email_woo_order_receipt_enabled',
}


def _is_email_enabled(email_type):
    """Check whether a specific email type is enabled via POSSetting toggle."""
    from .models import POSSetting
    toggle_key = EMAIL_TOGGLE_KEYS.get(email_type)
    if not toggle_key:
        return True  # No toggle defined → always enabled
    return POSSetting.get_setting(toggle_key, True)


def _log_email(email_type, recipient, subject, status, delivery_backend='none',
               error_message='', related_order='', metadata=None):
    """Create an EmailLog record. Never raises."""
    try:
        from .models import EmailLog
        EmailLog.objects.create(
            email_type=email_type,
            recipient=recipient,
            subject=subject[:500] if subject else '',
            status=status,
            delivery_backend=delivery_backend,
            error_message=error_message[:2000] if error_message else '',
            related_order=str(related_order)[:100] if related_order else '',
            metadata=metadata or {},
        )
    except Exception as e:
        logger.error(f"Failed to write EmailLog: {e}")


def send_pos_email(
    subject,
    text_content,
    html_content,
    to_email,
    email_type='other',
    from_email=None,
    attachments=None,
    related_order='',
    metadata=None,
):
    """
    Send an email with Mailgun (primary) → SMTP (fallback), with toggle
    check and audit logging.

    Args:
        subject (str): Email subject line.
        text_content (str): Plain-text fallback body.
        html_content (str): HTML email body.
        to_email (str): Recipient email address.
        email_type (str): One of EmailLog.EMAIL_TYPE_CHOICES keys
                          (e.g. 'order_receipt', 'tracking', ...).
        from_email (str|None): Sender address (defaults to DEFAULT_FROM_EMAIL).
        attachments (list|None): List of (filename, content_bytes, mime_type) tuples.
        related_order (str): Order number / subscription ID for log reference.
        metadata (dict|None): Extra context stored in the log.

    Returns:
        bool: True if email was sent successfully, False otherwise.
    """
    if not from_email:
        from_email = settings.DEFAULT_FROM_EMAIL

    # ── Toggle check ──
    if not _is_email_enabled(email_type):
        logger.info(f"📧 ⏭️ Email skipped (toggle off): {email_type} → {to_email}")
        _log_email(
            email_type=email_type,
            recipient=to_email,
            subject=subject,
            status='skipped',
            related_order=related_order,
            metadata=metadata,
        )
        return False

    sent = False
    backend_used = 'none'

    # ── Try Mailgun first ──
    if getattr(settings, 'USE_MAILGUN', False):
        try:
            conn = get_connection(backend='anymail.backends.mailgun.EmailBackend')
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=from_email,
                to=[to_email],
                connection=conn,
            )
            msg.attach_alternative(html_content, 'text/html')
            if attachments:
                for fname, fbytes, fmime in attachments:
                    msg.attach(fname, fbytes, fmime)
            msg.send()
            sent = True
            backend_used = 'mailgun'
            logger.info(f"📧 ✅ Email sent via Mailgun to {to_email}: {subject}")
        except Exception as e:
            logger.error(f"📧 ❌ Mailgun failed: {e}")

    # ── Fallback to SMTP ──
    if not sent:
        try:
            conn = get_connection(backend='django.core.mail.backends.smtp.EmailBackend')
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=from_email,
                to=[to_email],
                connection=conn,
            )
            msg.attach_alternative(html_content, 'text/html')
            if attachments:
                for fname, fbytes, fmime in attachments:
                    msg.attach(fname, fbytes, fmime)
            msg.send()
            sent = True
            backend_used = 'smtp'
            logger.info(f"📧 ✅ Email sent via SMTP to {to_email}: {subject}")
        except Exception as e:
            logger.error(f"📧 ❌ SMTP also failed: {e}")
            _log_email(
                email_type=email_type,
                recipient=to_email,
                subject=subject,
                status='failed',
                delivery_backend='none',
                error_message=str(e),
                related_order=related_order,
                metadata=metadata,
            )
            return False

    # ── Log success ──
    _log_email(
        email_type=email_type,
        recipient=to_email,
        subject=subject,
        status='sent',
        delivery_backend=backend_used,
        related_order=related_order,
        metadata=metadata,
    )
    return True
