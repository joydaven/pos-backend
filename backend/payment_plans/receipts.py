import io
import logging
import os
from datetime import datetime
from django.core.mail import EmailMessage
from django.conf import settings
from xhtml2pdf import pisa

logger = logging.getLogger(__name__)

BUSINESS_NAME = 'Doctors Studio'
BUSINESS_ADDRESS_1 = '2595 NW Boca Raton Blvd'
BUSINESS_ADDRESS_2 = 'Suite 200'
BUSINESS_CITY_STATE = 'Boca Raton, FL 33431'
BUSINESS_PHONE = '(561) 444-7751'
BUSINESS_WEBSITE = 'www.doctorsstudio.com'

BRAND_COLOR = '#183249'
BRAND_LIGHT = '#e8edf2'
LOGO_URL = 'https://store.doctorsstudio.com/wp-content/uploads/2024/06/Doctors-Studio-Logo.png'
BORDER_COLOR = '#e5e7eb'
TEXT_PRIMARY = '#1f2937'
TEXT_SECONDARY = '#6b7280'
TEXT_MUTED = '#9ca3af'


def _html_to_pdf(html_content):
    buffer = io.BytesIO()
    try:
        pisa_status = pisa.CreatePDF(io.StringIO(html_content), dest=buffer)
        if pisa_status.err:
            logger.warning('xhtml2pdf reported errors during PDF conversion')
            return None
        return buffer.getvalue()
    except Exception as e:
        logger.warning(f'Failed to generate PDF attachment: {e}')
        return None
    finally:
        buffer.close()


def _format_date(dt):
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except Exception:
            return dt
    if dt:
        return dt.strftime('%B %d, %Y')
    return ''


def _format_currency(amount):
    try:
        return f"${float(amount):,.2f}"
    except (ValueError, TypeError):
        return f"${amount}"


def _get_customer_name(plan):
    if plan.contact:
        first = plan.contact.first_name or ''
        last = plan.contact.last_name or ''
        name = f"{first} {last}".strip()
        if name:
            return name
    return 'Valued Customer'


def _get_customer_email(plan):
    if plan.contact and plan.contact.email:
        return plan.contact.email
    return ''


def _build_installment_schedule_html(plan, current_installment=None):
    """Build an HTML table showing the full installment schedule."""
    rows = ''
    for inst in plan.installments.all().order_by('installment_number'):
        is_current = current_installment and inst.id == current_installment.id

        if inst.status == 'paid':
            status_badge = f'<span style="color:#059669;font-weight:600;">&#10003; Paid</span>'
        elif is_current:
            status_badge = f'<span style="color:{BRAND_COLOR};font-weight:700;">&#9654; This Payment</span>'
        elif inst.status == 'cancelled':
            status_badge = f'<span style="color:#dc2626;">Cancelled</span>'
        elif inst.status == 'failed':
            status_badge = f'<span style="color:#dc2626;">Failed</span>'
        else:
            status_badge = f'<span style="color:{TEXT_SECONDARY};">Scheduled</span>'

        bg = f'background:{BRAND_LIGHT};' if is_current else ''
        rows += f'''
        <tr style="{bg}">
            <td style="padding:8px 12px;font-size:12px;color:{TEXT_PRIMARY};border-bottom:1px solid {BORDER_COLOR};">
                Payment {inst.installment_number}
            </td>
            <td style="padding:8px 12px;font-size:12px;color:{TEXT_PRIMARY};border-bottom:1px solid {BORDER_COLOR};">
                {_format_date(inst.due_date)}
            </td>
            <td style="padding:8px 12px;font-size:12px;color:{TEXT_PRIMARY};border-bottom:1px solid {BORDER_COLOR};text-align:right;font-weight:600;">
                {_format_currency(inst.amount)}
            </td>
            <td style="padding:8px 12px;font-size:12px;border-bottom:1px solid {BORDER_COLOR};text-align:center;">
                {status_badge}
            </td>
        </tr>'''

    return f'''
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
        <tr style="background:{BRAND_LIGHT};">
            <td style="padding:8px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};border-bottom:1px solid {BORDER_COLOR};">PAYMENT</td>
            <td style="padding:8px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};border-bottom:1px solid {BORDER_COLOR};">DUE DATE</td>
            <td style="padding:8px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};border-bottom:1px solid {BORDER_COLOR};text-align:right;">AMOUNT</td>
            <td style="padding:8px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};border-bottom:1px solid {BORDER_COLOR};text-align:center;">STATUS</td>
        </tr>
        {rows}
    </table>'''


def build_installment_receipt_html(plan, installment):
    """Build HTML email for an installment payment receipt."""
    customer_name = _get_customer_name(plan)
    order_number = plan.pos_order.order_number if plan.pos_order else 'N/A'
    schedule_html = _build_installment_schedule_html(plan, current_installment=installment)

    paid_amount = _format_currency(installment.paid_amount or installment.amount)
    remaining = _format_currency(plan.remaining_balance)
    total = _format_currency(plan.total_amount)
    total_paid = _format_currency(plan.total_paid)

    # Payment method display
    pm = installment.payment_method or plan.payment_method or {}
    if pm.get('brand') and pm.get('last4'):
        pm_display = f"{pm['brand']} ****{pm['last4']}"
    elif pm.get('type'):
        pm_display = pm['type'].title()
    else:
        pm_display = 'N/A'

    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header -->
    <tr><td style="background:{BRAND_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td>
                    <div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">Payment Plan — Installment Receipt</div>
                </td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">Plan {plan.plan_number}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.7);margin-top:2px;">Order #{order_number}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Greeting -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:15px;color:{TEXT_PRIMARY};">Hi {customer_name},</div>
        <div style="font-size:13px;color:{TEXT_SECONDARY};margin-top:6px;">
            Payment {installment.installment_number} of {plan.installments.count()} has been received. Thank you!
        </div>
    </td></tr>

    <!-- Payment Summary -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Payment Details</div>
            </td></tr>
            <tr><td style="padding:12px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Amount Paid</td>
                        <td style="padding:4px 0;text-align:right;font-size:15px;font-weight:700;color:{TEXT_PRIMARY};">{paid_amount}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Payment Method</td>
                        <td style="padding:4px 0;text-align:right;font-size:13px;color:{TEXT_PRIMARY};">{pm_display}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Date</td>
                        <td style="padding:4px 0;text-align:right;font-size:13px;color:{TEXT_PRIMARY};">{_format_date(installment.paid_at)}</td>
                    </tr>
                    <tr style="border-top:1px solid {BORDER_COLOR};">
                        <td style="padding:8px 0 4px;font-size:13px;color:{TEXT_SECONDARY};">Plan Total</td>
                        <td style="padding:8px 0 4px;text-align:right;font-size:13px;color:{TEXT_PRIMARY};">{total}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Total Paid to Date</td>
                        <td style="padding:4px 0;text-align:right;font-size:13px;color:#059669;font-weight:600;">{total_paid}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Remaining Balance</td>
                        <td style="padding:4px 0;text-align:right;font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">{remaining}</td>
                    </tr>
                </table>
            </td></tr>
        </table>
    </td></tr>

    <!-- Schedule -->
    <tr><td style="padding:0 32px 16px;">
        <div style="font-size:12px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:8px;">Payment Schedule</div>
        {schedule_html}
    </td></tr>

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your payment?</div>
                <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">Call us at {BUSINESS_PHONE}</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">or email us at support@doctorsstudio.com</div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 32px;border-top:1px solid {BORDER_COLOR};text-align:center;">
        <div style="font-size:10px;color:{TEXT_PRIMARY};font-weight:500;">Thank you for your business!</div>
    </td></tr>
    <tr><td style="background:#f9fafb;padding:12px 32px;text-align:center;">
        <div style="font-size:10px;color:{TEXT_MUTED};">
            {BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}
        </div>
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">{BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}</div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''


def build_plan_summary_email(plan):
    """Build HTML email summarizing the full payment plan (sent on creation)."""
    customer_name = _get_customer_name(plan)
    order_number = plan.pos_order.order_number if plan.pos_order else 'N/A'
    schedule_html = _build_installment_schedule_html(plan)
    total = _format_currency(plan.total_amount)
    num = plan.installments.count()

    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header -->
    <tr><td style="background:{BRAND_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td>
                    <div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">Payment Plan Confirmation</div>
                </td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">Plan {plan.plan_number}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.7);margin-top:2px;">Order #{order_number}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Greeting -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:15px;color:{TEXT_PRIMARY};">Hi {customer_name},</div>
        <div style="font-size:13px;color:{TEXT_SECONDARY};margin-top:6px;">
            Your payment plan has been set up. Your order total of {total} will be split into {num} payments as shown below.
        </div>
    </td></tr>

    <!-- Schedule -->
    <tr><td style="padding:0 32px 16px;">
        <div style="font-size:12px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:8px;">Payment Schedule</div>
        {schedule_html}
    </td></tr>

    <!-- Note -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #fbbf24;border-radius:6px;overflow:hidden;">
            <tr><td style="background:#fffbeb;padding:12px 16px;">
                <div style="font-size:11px;color:#92400e;">
                    <strong>Important:</strong> Scheduled payments will be automatically charged to your card on file on each due date.
                    If you need to make changes to your payment schedule, please contact us.
                </div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your payment plan?</div>
                <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">Call us at {BUSINESS_PHONE}</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">or email us at support@doctorsstudio.com</div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 32px;border-top:1px solid {BORDER_COLOR};text-align:center;">
        <div style="font-size:10px;color:{TEXT_PRIMARY};font-weight:500;">Thank you for your business!</div>
    </td></tr>
    <tr><td style="background:#f9fafb;padding:12px 32px;text-align:center;">
        <div style="font-size:10px;color:{TEXT_MUTED};">
            {BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}
        </div>
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">{BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}</div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''


def send_installment_receipt(plan, installment, email):
    """Send an installment receipt email with PDF attachment."""
    from crm.email_sender import send_pos_email

    html_body = build_installment_receipt_html(plan, installment)
    order_number = plan.pos_order.order_number if plan.pos_order else 'N/A'
    subject = f'Payment Received — {plan.plan_number} (Installment {installment.installment_number})'
    text_content = f'Payment received for {plan.plan_number} installment {installment.installment_number}'

    pdf_bytes = _html_to_pdf(html_body)
    attachments = None
    if pdf_bytes:
        attachments = [(
            f'Installment_Receipt_{plan.plan_number}_{installment.installment_number}.pdf',
            pdf_bytes,
            'application/pdf'
        )]

    send_pos_email(
        subject=subject,
        text_content=text_content,
        html_content=html_body,
        to_email=email,
        email_type='installment_receipt',
        attachments=attachments,
        related_order=str(plan.plan_number),
    )
    logger.info(f"Sent installment receipt for {plan.plan_number} installment {installment.installment_number} to {email}")


def send_plan_summary_email(plan, email):
    """Send the payment plan summary email with PDF attachment."""
    from crm.email_sender import send_pos_email

    html_body = build_plan_summary_email(plan)
    subject = f'Payment Plan Confirmation — {plan.plan_number}'
    text_content = f'Payment plan confirmation for {plan.plan_number}'

    pdf_bytes = _html_to_pdf(html_body)
    attachments = None
    if pdf_bytes:
        attachments = [(
            f'Payment_Plan_{plan.plan_number}.pdf',
            pdf_bytes,
            'application/pdf'
        )]

    send_pos_email(
        subject=subject,
        text_content=text_content,
        html_content=html_body,
        to_email=email,
        email_type='plan_summary',
        attachments=attachments,
        related_order=str(plan.plan_number),
    )
    logger.info(f"Sent payment plan summary for {plan.plan_number} to {email}")
