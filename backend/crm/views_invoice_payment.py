"""
Public-facing invoice payment API endpoints.

These endpoints are accessed by patients via a secure tokenized link in
their invoice email.  No Django authentication is required — the signed
payment token serves as the credential.

Endpoints:
    GET  /api/invoice/pay/<token>/  — invoice details + Accept.js config
    POST /api/invoice/pay/<token>/  — process payment via Accept.js nonce
"""
import logging
import os
from decimal import Decimal

from django.utils import timezone
from django.core.cache import caches
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import SimpleRateThrottle

from .invoice_utils import validate_invoice_token, InvalidTokenError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Throttle classes (keyed by token, not IP, to prevent token-sharing abuse)
# ---------------------------------------------------------------------------

class InvoicePaymentThrottle(SimpleRateThrottle):
    """5 payment attempts per token per hour."""
    cache = caches['throttle'] if 'throttle' in caches else caches['default']
    rate = '5/hour'
    scope = 'invoice_payment'

    def get_cache_key(self, request, view):
        token = view.kwargs.get('token', '')
        return self.cache_format % {'scope': self.scope, 'ident': token[:64]}


class InvoiceGetThrottle(SimpleRateThrottle):
    """30 GET requests per token per hour."""
    cache = caches['throttle'] if 'throttle' in caches else caches['default']
    rate = '30/hour'
    scope = 'invoice_get'

    def get_cache_key(self, request, view):
        token = view.kwargs.get('token', '')
        return self.cache_format % {'scope': self.scope, 'ident': token[:64]}


# ---------------------------------------------------------------------------
# Helper: get client IP
# ---------------------------------------------------------------------------

def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


# ---------------------------------------------------------------------------
# GET  /api/invoice/pay/<token>/
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([InvoiceGetThrottle])
def get_invoice_details(request, token):
    """
    Return invoice summary + masked saved cards for the payment page.
    No authentication required — the signed token validates access.
    """
    try:
        invoice = validate_invoice_token(token)
    except InvalidTokenError as e:
        return Response({
            'error': True,
            'code': e.code,
            'message': e.reason,
            'transaction_id': (
                e.invoice.transaction_id if e.invoice and e.invoice.status == 'paid' else None
            ),
        }, status=status.HTTP_400_BAD_REQUEST)

    # Provide Accept.js public client key for new card tokenization
    # NOTE: Saved cards are intentionally NOT returned on this public page
    # to avoid exposing card details and payment profile IDs via a shareable link.
    # Customers must enter their card details fresh each time.
    authorize_net_client_key = ''
    try:
        from payments.config import AUTHORIZE_NET_CLIENT_KEY, AUTHORIZE_NET_LOGIN_ID, AUTHORIZE_NET_SANDBOX
        authorize_net_client_key = AUTHORIZE_NET_CLIENT_KEY or ''
        authorize_net_login_id = AUTHORIZE_NET_LOGIN_ID or ''
        is_sandbox = AUTHORIZE_NET_SANDBOX
    except ImportError:
        authorize_net_login_id = ''
        is_sandbox = True

    return Response({
        'invoice': {
            'invoice_ref': invoice.invoice_ref,
            'items': invoice.items,
            'subtotal': str(invoice.subtotal),
            'total': str(invoice.total),
            'status': invoice.status,
            'customer_name': (
                f"{invoice.contact.first_name} {invoice.contact.last_name}".strip()
                if invoice.contact else 'Customer'
            ),
        },
        'saved_cards': [],
        'authorize_net': {
            'client_key': authorize_net_client_key,
            'api_login_id': authorize_net_login_id,
            'is_sandbox': is_sandbox,
        },
    })


# ---------------------------------------------------------------------------
# POST /api/invoice/pay/<token>/
# ---------------------------------------------------------------------------

MAX_FAILED_ATTEMPTS = 5

@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([InvoicePaymentThrottle])
def process_invoice_payment(request, token):
    """
    Process a payment for an invoice via Accept.js nonce.

    Accepts:
      { "opaque_data": { "dataDescriptor": "...", "dataValue": "..." } }

    After a successful charge the card is saved to the customer's
    Authorize.Net CIM profile (if not already on file) so it appears
    in the POS saved-cards list.
    """
    try:
        invoice = validate_invoice_token(token)
    except InvalidTokenError as e:
        return Response({
            'error': True,
            'code': e.code,
            'message': e.reason,
        }, status=status.HTTP_400_BAD_REQUEST)

    # ── Determine payment method ──────────────────────────────────────
    opaque_data = request.data.get('opaque_data')
    client_ip = _get_client_ip(request)

    if not opaque_data:
        return Response({
            'error': True,
            'code': 'missing_payment',
            'message': 'Please enter your card details.',
        }, status=status.HTTP_400_BAD_REQUEST)

    # Amount is SERVER-AUTHORITATIVE — never trust the client amount
    amount = float(invoice.total)
    invoice_ref = invoice.invoice_ref

    logger.info(
        f"💳 Invoice payment attempt — ref={invoice_ref}, amount=${amount:.2f}, "
        f"method=accept_js, ip={client_ip}"
    )

    # ── Charge via Accept.js nonce ────────────────────────────────────
    from payments.authorize_net import AuthorizeNetGateway

    try:
        descriptor = opaque_data.get('dataDescriptor', '')
        value = opaque_data.get('dataValue', '')
        if not descriptor or not value:
            return Response({
                'error': True, 'code': 'invalid_nonce',
                'message': 'Invalid payment token. Please try again.',
            }, status=status.HTTP_400_BAD_REQUEST)

        payment_response = AuthorizeNetGateway.charge_accept_js_nonce(
            opaque_data_descriptor=descriptor,
            opaque_data_value=value,
            amount=amount,
            order_id=invoice_ref,
            customer_email=invoice.email,
            invoice_number=invoice_ref,
        )
        method_label = 'new_card_accept_js'

    except Exception as exc:
        logger.exception(f"Payment processing error for invoice {invoice_ref}: {exc}")
        return Response({
            'error': True, 'code': 'processing_error',
            'message': 'An error occurred while processing your payment. Please try again.',
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # ── Handle result ─────────────────────────────────────────────────
    if not payment_response.get('success'):
        # Record failed attempt
        invoice.failed_attempts += 1
        fields_to_update = ['failed_attempts', 'updated_at']

        if invoice.failed_attempts >= MAX_FAILED_ATTEMPTS:
            invoice.status = 'locked'
            fields_to_update.append('status')
            logger.warning(f"🔒 Invoice {invoice_ref} LOCKED after {invoice.failed_attempts} failed attempts")

        invoice.save(update_fields=fields_to_update)

        decline_msg = payment_response.get('message', 'Payment declined.')
        logger.info(
            f"💳 Invoice payment DECLINED — ref={invoice_ref}, attempt={invoice.failed_attempts}, "
            f"code={payment_response.get('code')}, msg={decline_msg}, ip={client_ip}"
        )

        resp_status = status.HTTP_423_LOCKED if invoice.status == 'locked' else status.HTTP_402_PAYMENT_REQUIRED
        return Response({
            'error': True,
            'code': 'declined' if invoice.status != 'locked' else 'locked',
            'message': decline_msg if invoice.status != 'locked' else (
                'This invoice has been locked due to too many failed attempts. Please contact the office.'
            ),
            'attempts_remaining': max(0, MAX_FAILED_ATTEMPTS - invoice.failed_attempts),
        }, status=resp_status)

    # ── Payment succeeded ─────────────────────────────────────────────
    transaction_id = payment_response.get('transactionId', '')

    invoice.status = 'paid'
    invoice.paid_at = timezone.now()
    invoice.transaction_id = transaction_id
    invoice.payment_method_used = method_label
    invoice.payer_ip = client_ip
    invoice.failed_attempts = 0  # reset on success
    invoice.save(update_fields=[
        'status', 'paid_at', 'transaction_id',
        'payment_method_used', 'payer_ip', 'failed_attempts', 'updated_at',
    ])

    logger.info(
        f"✅ Invoice {invoice_ref} PAID — trans_id={transaction_id}, "
        f"method={method_label}, amount=${amount:.2f}, ip={client_ip}"
    )

    # ── Save card to customer's CIM profile (best-effort) ─────────────
    _save_card_from_transaction(invoice, transaction_id, payment_response)

    # ── Create WooCommerce order ──────────────────────────────────────
    woo_order_id = None
    try:
        woo_order_id = _create_woo_order_for_invoice(invoice, transaction_id)
        if woo_order_id:
            invoice.woo_order_id = woo_order_id
            invoice.save(update_fields=['woo_order_id', 'updated_at'])
    except Exception as exc:
        logger.error(f"WooCommerce order creation failed for invoice {invoice_ref}: {exc}")
        # Payment succeeded — don't fail the response. WC order can be created manually.

    # ── Send confirmation email ───────────────────────────────────────
    try:
        _send_payment_confirmation_email(invoice, transaction_id)
    except Exception as exc:
        logger.error(f"Confirmation email failed for invoice {invoice_ref}: {exc}")

    return Response({
        'success': True,
        'message': 'Payment successful! Thank you.',
        'transaction_id': transaction_id,
        'woo_order_id': woo_order_id,
    })


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _save_card_from_transaction(invoice, transaction_id, payment_response):
    """
    Best-effort: create/update a CIM profile from the successful transaction
    so the card appears in the POS saved-cards list.

    Uses Authorize.Net's createCustomerProfileFromTransaction API which
    extracts card data from the transaction — raw card numbers never touch
    our server.

    If the customer already has a CIM profile and the card is already on
    file (E00039 duplicate), we skip silently.  A new PaymentCard DB record
    is only created when a genuinely new payment profile is added.
    """
    if not invoice.contact:
        logger.info(f"No contact linked to invoice {invoice.invoice_ref} — skipping card save")
        return

    contact = invoice.contact
    account_num = payment_response.get('accountNumber', '')  # e.g. "XXXX1234"
    account_type = payment_response.get('accountType', '')    # e.g. "Visa"
    last4 = account_num[-4:] if len(account_num) >= 4 else ''

    try:
        import requests as http_requests
        import xml.etree.ElementTree as ET
        from payments.authorize_net import AuthorizeNetGateway
        from payments.config import (
            AUTHORIZE_NET_LOGIN_ID, AUTHORIZE_NET_TRANSACTION_KEY,
            AUTHORIZE_NET_ENDPOINT,
        )
        from payments.models import PaymentCard

        ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
        ns_prefix = '{' + ns + '}'

        # If the contact already has a CIM customer profile, pass it so
        # Authorize.Net adds the payment profile to the existing one.
        existing_cim_id = (contact.authorize_net_customer_profile_id or '').strip()

        # Build the request — createCustomerProfileFromTransaction
        customer_block = ''
        if existing_cim_id and existing_cim_id.isdigit():
            customer_block = (
                f'<customerProfileId>{AuthorizeNetGateway._xml_escape(existing_cim_id)}'
                f'</customerProfileId>'
            )
        else:
            # Let Authorize.Net create a brand-new customer profile.
            # Provide merchantCustomerId so it can be linked back.
            cust_id = str(contact.id)[:20]
            customer_block = (
                f'<customer>'
                f'<merchantCustomerId>{AuthorizeNetGateway._xml_escape(cust_id)}</merchantCustomerId>'
                f'<email>{AuthorizeNetGateway._xml_escape(invoice.email or "")}</email>'
                f'</customer>'
            )

        create_xml = f'''<?xml version="1.0" encoding="utf-8"?>
        <createCustomerProfileFromTransactionRequest xmlns="{ns}">
            <merchantAuthentication>
                <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
            </merchantAuthentication>
            <transId>{AuthorizeNetGateway._xml_escape(transaction_id)}</transId>
            {customer_block}
        </createCustomerProfileFromTransactionRequest>'''

        resp = http_requests.post(
            AUTHORIZE_NET_ENDPOINT, data=create_xml,
            headers={'Content-Type': 'text/xml'}, timeout=15,
        )

        if resp.status_code != 200:
            logger.error(f"CIM profileFromTransaction HTTP {resp.status_code} for invoice {invoice.invoice_ref}")
            return

        root = ET.fromstring(resp.text)
        result_code = root.findtext(f'.//{ns_prefix}resultCode')

        customer_profile_id = root.findtext(f'.//{ns_prefix}customerProfileId') or ''
        payment_profile_id = ''

        # Extract payment profile ID from response
        pp_list = root.find(f'.//{ns_prefix}customerPaymentProfileIdList')
        if pp_list is not None:
            numeric = pp_list.findtext(f'{ns_prefix}numericString')
            if numeric:
                payment_profile_id = numeric

        if result_code == 'Ok':
            logger.info(
                f"CIM profile created from transaction {transaction_id} — "
                f"customer_profile={customer_profile_id}, payment_profile={payment_profile_id}"
            )
        else:
            # Check for E00039 (duplicate) — card already on file, which is fine
            error_code = ''
            error_text = ''
            for msg in root.findall(f'.//{ns_prefix}messages/{ns_prefix}message'):
                error_code = msg.findtext(f'{ns_prefix}code') or ''
                error_text = msg.findtext(f'{ns_prefix}text') or ''
                break

            if error_code == 'E00039':
                logger.info(
                    f"Card from transaction {transaction_id} already on file for "
                    f"contact {contact.id} — duplicate CIM profile (E00039), skipping"
                )
                # The customer profile already exists; make sure we have the ID stored
                if not existing_cim_id:
                    # Try to extract the existing profile ID from the error
                    import re
                    match = re.search(r'\b(\d{7,})\b', error_text)
                    if match:
                        customer_profile_id = match.group(1)
                        contact.authorize_net_customer_profile_id = customer_profile_id
                        contact.save(update_fields=['authorize_net_customer_profile_id'])
                        logger.info(f"Stored existing CIM profile {customer_profile_id} on contact {contact.id}")
                return
            elif error_code == 'E00040':
                logger.warning(f"CIM profile {existing_cim_id} stale (E00040) for contact {contact.id}")
                contact.authorize_net_customer_profile_id = None
                contact.save(update_fields=['authorize_net_customer_profile_id'])
                return
            else:
                logger.warning(
                    f"CIM profileFromTransaction failed for invoice {invoice.invoice_ref}: "
                    f"[{error_code}] {error_text}"
                )
                return

        # ── Persist to Contact and PaymentCard ────────────────────────
        if customer_profile_id and not existing_cim_id:
            contact.authorize_net_customer_profile_id = customer_profile_id
            contact.save(update_fields=['authorize_net_customer_profile_id'])
            logger.info(f"Stored CIM profile {customer_profile_id} on contact {contact.id}")

        if payment_profile_id:
            # Check if this payment profile already exists in our DB
            already_exists = PaymentCard.objects.filter(
                customer=contact,
                payment_profile_id=payment_profile_id,
            ).exists()

            if already_exists:
                logger.info(f"PaymentCard with profile {payment_profile_id} already exists — skipping DB insert")
                return

            # Also check by last4 + card brand to avoid saving a duplicate
            # card that got a new payment_profile_id
            if last4:
                dupe_by_card = PaymentCard.objects.filter(
                    customer=contact,
                    last4=last4,
                    card_brand__iexact=account_type or 'Credit Card',
                ).exists()
                if dupe_by_card:
                    logger.info(
                        f"Card {account_type} ****{last4} already on file for contact "
                        f"{contact.id} (different profile ID) — updating profile ID"
                    )
                    PaymentCard.objects.filter(
                        customer=contact,
                        last4=last4,
                        card_brand__iexact=account_type or 'Credit Card',
                    ).update(
                        payment_profile_id=payment_profile_id,
                        customer_profile_id=customer_profile_id or existing_cim_id,
                    )
                    return

            # Parse expiry from CIM (fetch the newly-created profile)
            exp_month = ''
            exp_year = ''
            try:
                profiles = AuthorizeNetGateway.get_customer_payment_profiles(
                    customer_profile_id or existing_cim_id
                )
                if profiles:
                    for p in profiles:
                        if p.get('payment_profile_id') == payment_profile_id:
                            exp_month = p.get('exp_month', '')
                            exp_year = p.get('exp_year', '')
                            break
            except Exception:
                pass

            PaymentCard.objects.create(
                customer=contact,
                order_id=invoice.invoice_ref[:50],
                last4=last4,
                card_brand=(account_type or 'Credit Card')[:50],
                exp_month=exp_month,
                exp_year=exp_year,
                customer_profile_id=(customer_profile_id or existing_cim_id)[:100],
                payment_profile_id=payment_profile_id[:100],
                is_default=not PaymentCard.objects.filter(customer=contact).exists(),
            )
            logger.info(
                f"Saved PaymentCard {account_type} ****{last4} for contact {contact.id} "
                f"(profile {payment_profile_id})"
            )

    except Exception as exc:
        logger.error(f"Best-effort card save failed for invoice {invoice.invoice_ref}: {exc}")


def _create_woo_order_for_invoice(invoice, transaction_id):
    """
    Create a WooCommerce order for a paid invoice.
    Returns the WC order ID or None.
    """
    from .woocommerce import WooCommerceAPI

    wc = WooCommerceAPI()

    # Build line items — use product name matching for WC product IDs
    line_items = []
    for item in (invoice.items or []):
        li = {
            'name': item.get('name', 'Item'),
            'quantity': item.get('quantity', 1),
            'total': str(round(float(item.get('price', 0)) * int(item.get('quantity', 1)), 2)),
        }
        line_items.append(li)

    # Customer info
    customer_id = 0
    billing = {}
    if invoice.contact:
        customer_id = invoice.contact.woo_customer_id or 0
        billing = {
            'first_name': invoice.contact.first_name or '',
            'last_name': invoice.contact.last_name or '',
            'email': invoice.email,
        }

    order_data = {
        'status': 'processing',
        'customer_id': customer_id,
        'billing': billing,
        'line_items': line_items,
        'payment_method': 'authorize_net_cim',
        'payment_method_title': 'Credit Card (Invoice Payment)',
        'set_paid': True,
        'transaction_id': transaction_id,
        'meta_data': [
            {'key': '_pos_invoice_ref', 'value': invoice.invoice_ref},
            {'key': '_pos_invoice_id', 'value': str(invoice.pk)},
            {'key': '_pos_payment_source', 'value': 'invoice_email_link'},
        ],
    }

    logger.info(f"Creating WooCommerce order for invoice {invoice.invoice_ref}")
    result = wc.create_order(order_data)

    if result and result.get('data'):
        wc_id = result['data'].get('id')
        logger.info(f"WooCommerce order {wc_id} created for invoice {invoice.invoice_ref}")
        return wc_id

    logger.error(f"WooCommerce order creation returned unexpected result: {result}")
    return None


def _send_payment_confirmation_email(invoice, transaction_id):
    """Send a simple payment confirmation email to the patient."""
    from .email_sender import send_pos_email

    BUSINESS_NAME = 'Doctors Studio'
    BUSINESS_PHONE = '(561) 444-7751'

    customer_name = 'Customer'
    if invoice.contact:
        customer_name = f"{invoice.contact.first_name} {invoice.contact.last_name}".strip() or 'Customer'

    subject = f"Payment Confirmed — {invoice.invoice_ref}"
    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,Helvetica,sans-serif;margin:0;padding:24px;background:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#fff;border-radius:8px;overflow:hidden;">
    <tr><td style="background:#16a34a;padding:24px 32px;text-align:center;">
        <div style="font-size:20px;font-weight:700;color:#fff;">Payment Confirmed</div>
    </td></tr>
    <tr><td style="padding:24px 32px;">
        <p style="font-size:15px;color:#1f2937;">Hi {customer_name},</p>
        <p style="font-size:14px;color:#4b5563;">Your payment of <strong>${float(invoice.total):.2f}</strong> for invoice <strong>{invoice.invoice_ref}</strong> has been received.</p>
        <table width="100%" style="margin:16px 0;border:1px solid #e5e7eb;border-radius:6px;overflow:hidden;">
            <tr><td style="padding:12px 16px;background:#f9fafb;font-size:13px;color:#6b7280;">Transaction ID</td>
                <td style="padding:12px 16px;background:#f9fafb;font-size:13px;color:#1f2937;font-weight:600;text-align:right;">{transaction_id}</td></tr>
            <tr><td style="padding:12px 16px;font-size:13px;color:#6b7280;">Amount</td>
                <td style="padding:12px 16px;font-size:13px;color:#1f2937;font-weight:600;text-align:right;">${float(invoice.total):.2f}</td></tr>
            <tr><td style="padding:12px 16px;background:#f9fafb;font-size:13px;color:#6b7280;">Invoice</td>
                <td style="padding:12px 16px;background:#f9fafb;font-size:13px;color:#1f2937;font-weight:600;text-align:right;">{invoice.invoice_ref}</td></tr>
        </table>
        <p style="font-size:13px;color:#6b7280;">If you have any questions, please call us at <strong>{BUSINESS_PHONE}</strong>.</p>
        <p style="font-size:13px;color:#1f2937;font-weight:500;">Thank you for your business!</p>
    </td></tr>
    <tr><td style="padding:16px 32px;background:#f9fafb;text-align:center;font-size:11px;color:#9ca3af;">
        {BUSINESS_NAME}
    </td></tr>
</table>
</body></html>'''

    send_pos_email(
        subject=subject,
        text_content=f"Payment of ${float(invoice.total):.2f} for invoice {invoice.invoice_ref} confirmed. Transaction ID: {transaction_id}",
        html_content=html,
        to_email=invoice.email,
        email_type='invoice',
        related_order=invoice.invoice_ref,
    )
    logger.info(f"Payment confirmation email sent for invoice {invoice.invoice_ref}")
