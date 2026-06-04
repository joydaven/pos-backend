import io
import json
import logging
import os
import re
from datetime import datetime, timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.core.mail import EmailMessage
from django.conf import settings
from .woocommerce import WooCommerceAPI
from .models import POSOrder, POSOrderRefund, POSOrderRefundItem
from django.db.models import Sum
from xhtml2pdf import pisa

logger = logging.getLogger(__name__)

# Version string for deployment verification — check server logs for this
REFUND_RECEIPT_VERSION = "2026-02-23-v4-pos-db-first"
logger.info(f"✅ Refund receipt module loaded: {REFUND_RECEIPT_VERSION}")

BUSINESS_NAME = 'Doctors Studio'
BUSINESS_ADDRESS_1 = '2595 NW Boca Raton Blvd'
BUSINESS_ADDRESS_2 = 'Suite 200'
BUSINESS_CITY_STATE = 'Boca Raton, FL 33431'
BUSINESS_PHONE = '(561) 444-7751'
BUSINESS_WEBSITE = 'www.doctorsstudio.com'
LOGO_URL = 'https://store.doctorsstudio.com/wp-content/uploads/2024/06/Doctors-Studio-Logo.png'
PDF_LOGO_PATH = os.path.join(os.path.dirname(__file__), 'static', 'crm', 'Logo White.jpg')

def _html_to_pdf(html_content):
    """Convert HTML string to PDF bytes. Returns bytes or None on failure."""
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


def _get_item_variation_name(item):
    """Extract variation name for a WooCommerce line item.
    1. Check meta_data for _ds_variation_name (set by POS when creating WooCommerce order)
    2. Fall back to WooCommerce variation attributes (keys starting with 'pa_')
    Returns variation name string or empty string.
    """
    meta_data = item.get('meta_data', [])
    if not isinstance(meta_data, list):
        return ''

    # Check for explicit variation name from POS
    for meta in meta_data:
        if meta.get('key') == '_ds_variation_name' and meta.get('value'):
            return str(meta['value'])

    # Fall back to WooCommerce variation attributes (pa_ prefixed keys)
    if not item.get('variation_id'):
        return ''

    variation_parts = []
    for meta in meta_data:
        key = str(meta.get('key', ''))
        if key.startswith('pa_'):
            display_value = meta.get('display_value') or meta.get('value', '')
            if display_value:
                variation_parts.append(str(display_value))
    return ', '.join(variation_parts)


def _get_item_brand(item, product_cache=None):
    """Extract brand name for a WooCommerce line item.
    1. Check line item meta_data for _ds_pos_brand (set by POS orders)
    2. Fall back to DB product lookup by product_id, extracting from woo_data.attributes
    Returns brand string or empty string.
    """
    if product_cache is None:
        product_cache = {}

    # 1. Check meta_data for _ds_pos_brand (POS orders persist this)
    meta_data = item.get('meta_data', [])
    if isinstance(meta_data, list):
        for meta in meta_data:
            if meta.get('key') == '_ds_pos_brand' and meta.get('value'):
                return str(meta['value'])

    # 2. Fall back to DB product lookup
    product_id = item.get('product_id')
    if not product_id:
        return ''

    pid = int(product_id)
    if pid in product_cache:
        return product_cache[pid]

    brand = ''
    try:
        from .models import ProductSimple, ProductVariation, ProductBundle
        for Model in (ProductSimple, ProductVariation, ProductBundle):
            try:
                product = Model.objects.filter(woo_id=pid).first()
                if product and product.woo_data:
                    woo_data = product.woo_data if isinstance(product.woo_data, dict) else json.loads(product.woo_data)
                    for attr in woo_data.get('attributes', []):
                        if (attr.get('name') == 'Brand' or attr.get('slug') == 'pa_brand') and attr.get('options'):
                            brand = attr['options'][0].strip()
                            break
                if brand:
                    break
            except Exception:
                continue
    except Exception as e:
        logger.debug(f"Could not look up brand for product {pid}: {e}")

    product_cache[pid] = brand
    return brand


def _build_pdf_receipt_html(order_data, is_refund=False):
    """Build a modern HTML receipt optimised for xhtml2pdf rendering.
    Matches the teal-branded email template design."""
    order_number = order_data.get('number', order_data.get('id', 'N/A'))
    order_date = _format_date(order_data.get('date_created', ''))
    billing = order_data.get('billing', {})
    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or 'Valued Customer'
    customer_email = billing.get('email', '')

    line_items = order_data.get('line_items', [])
    total = float(order_data.get('total', '0.00'))
    discount_total = float(order_data.get('discount_total', '0.00'))
    total_tax = float(order_data.get('total_tax', '0.00'))
    payment = _get_payment_details(order_data)
    amount_paid, change = _get_amount_paid(order_data, total)
    has_subscription = any(_is_subscription_item(i) for i in line_items)

    # Bundle pricing detection: if all items are $0 but order total > 0,
    # show the full total on the first line item
    all_items_zero = total > 0 and all(
        abs(float(i.get('subtotal', '0'))) < 0.01 and abs(float(i.get('total', '0'))) < 0.01
        for i in line_items
    ) if line_items else False
    logger.info(f'PDF receipt #{order_number}: total={total}, all_items_zero={all_items_zero}, items={len(line_items)}')

    # Pre-scan items to detect if any have discounts (to conditionally show Disc. column)
    any_item_has_discount = discount_total > 0.005
    if not any_item_has_discount:
        for _item in line_items:
            _op, _cp = _get_item_prices(_item)
            if abs(_op - _cp) > 0.01:
                any_item_has_discount = True
                break

    # Build items rows — tabular format (Disc. column hidden when no discounts)
    computed_subtotal = 0.0
    brand_cache = {}
    disc_c = '#059669'
    hdr_color = '#6b7280'
    pdf_border = '1px solid #e5e7eb'
    text_dark = '#1f2937'
    fs_h = '9px'
    fs_b = '10px'
    fs_sm = '8px'
    num_cols = 5 if any_item_has_discount else 4

    th_qty = f'<th align="left" style="padding:6px 4px 6px 0;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:bold;color:{hdr_color};width:30px;">Qty</th>'
    th_desc = f'<th align="left" style="padding:6px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:bold;color:{hdr_color};">Description</th>'
    th_price = f'<th align="right" style="padding:6px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:bold;color:{hdr_color};width:56px;">Price</th>'
    th_disc = f'<th align="right" style="padding:6px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:bold;color:{hdr_color};width:56px;">Disc.</th>' if any_item_has_discount else ''
    th_line = f'<th align="right" style="padding:6px 0 6px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:bold;color:{hdr_color};width:64px;">Total</th>'
    items_rows = f'<tr>{th_qty}{th_desc}{th_price}{th_disc}{th_line}</tr>'

    for idx, item in enumerate(line_items):
        name = item.get('name', 'Item')
        name = re.sub(r'[\u2500-\u257F]', '', name).strip()
        if name.startswith('- '):
            pass
        elif not name:
            name = 'Item'
        variation_label = _get_item_variation_name(item)
        if variation_label and variation_label not in name:
            name = f'{name} - {variation_label}'
        name = _with_series_suffix(name, _get_item_series_count(item))
        qty = item.get('quantity', 1)
        sku = item.get('sku', '')
        brand = _get_item_brand(item, brand_cache)
        original_price, current_price = _get_item_prices(item)

        if all_items_zero and idx == 0:
            original_price = total / qty if qty else total
            current_price = original_price

        line_total = original_price * qty
        computed_subtotal += line_total
        has_discount = abs(original_price - current_price) > 0.01
        discount_amount = (original_price - current_price) * qty
        line_net_pdf = current_price * qty
        is_sub = _is_subscription_item(item)
        freq_text = ''
        if is_sub:
            sp, si = _get_subscription_frequency(item)
            if sp is None:
                sp, si = _get_order_subscription_frequency(order_data)
            freq_text = _format_subscription_frequency(sp, si)

        sub_label = f'<br/><span style="color:#7c3aed;font-size:9px;font-weight:bold;">SUBSCRIPTION{" — " + freq_text if freq_text else ""}</span>' if is_sub else ''
        brand_line = f'<br/><span style="font-size:{fs_sm};color:{hdr_color};font-weight:bold;">{brand}</span>' if brand else ''
        sku_line = f'<br/><span style="font-size:{fs_sm};color:{hdr_color};">SKU: {sku}</span>' if sku else ''

        is_bundle_child = all_items_zero and idx > 0
        if is_bundle_child:
            price_cell = ''
            disc_cell = ''
            total_cell = ''
        else:
            price_cell = f'${original_price:.2f}'
            disc_cell = f'<span style="color:{disc_c};">-${discount_amount:.2f}</span>' if has_discount else ''
            total_cell = f'${line_net_pdf:.2f}'

        disc_td = f'<td align="right" style="padding:6px 4px;border-bottom:{pdf_border};font-size:{fs_b};color:{disc_c};vertical-align:top;">{disc_cell}</td>' if any_item_has_discount else ''
        items_rows += f'''
        <tr>
            <td align="left" style="padding:6px 4px 6px 0;border-bottom:{pdf_border};font-size:{fs_b};color:{text_dark};vertical-align:top;">{qty}</td>
            <td align="left" style="padding:6px 4px;border-bottom:{pdf_border};font-size:{fs_b};color:{text_dark};vertical-align:top;"><span style="font-weight:bold;">{name}</span>{brand_line}{sku_line}{sub_label}</td>
            <td align="right" style="padding:6px 4px;border-bottom:{pdf_border};font-size:{fs_b};color:{text_dark};vertical-align:top;">{price_cell}</td>
            {disc_td}
            <td align="right" style="padding:6px 0 6px 4px;border-bottom:{pdf_border};font-size:{fs_b};font-weight:bold;color:{text_dark};vertical-align:top;">{total_cell}</td>
        </tr>'''

    # Totals — colspan adapts to whether Disc. column is shown
    lbl_span = num_cols - 1
    subtotal_display = '' if all_items_zero else f'${computed_subtotal:.2f}'
    disc_color_pdf_rcpt = '#059669' if discount_total > 0 else '#6b7280'
    disc_display_pdf = f'-${discount_total:.2f}' if discount_total > 0 else ''
    if any_item_has_discount:
        totals_rows = f'''
        <tr>
            <td colspan="2" style="padding:6px 0;font-size:10px;color:#6b7280;">Subtotal</td>
            <td align="right" style="padding:6px 4px;font-size:10px;color:#1f2937;">{subtotal_display}</td>
            <td align="right" style="padding:6px 4px;font-size:10px;color:{disc_color_pdf_rcpt};">{disc_display_pdf}</td>
            <td align="right" style="padding:6px 0;font-size:10px;color:#1f2937;font-weight:bold;">${total:.2f}</td>
        </tr>'''
    else:
        totals_rows = f'''
        <tr>
            <td colspan="2" style="padding:6px 0;font-size:10px;color:#6b7280;">Subtotal</td>
            <td align="right" style="padding:6px 4px;font-size:10px;color:#1f2937;">{subtotal_display}</td>
            <td align="right" style="padding:6px 0;font-size:10px;color:#1f2937;font-weight:bold;">${total:.2f}</td>
        </tr>'''
    if total_tax > 0:
        totals_rows += f'''
        <tr>
            <td colspan="{lbl_span}" style="padding:3px 0;font-size:10px;color:#6b7280;">Tax</td>
            <td align="right" style="padding:3px 0;font-size:10px;color:#1f2937;">${total_tax:.2f}</td>
        </tr>'''
    totals_rows += f'''
        <tr>
            <td colspan="{lbl_span}" style="padding:8px 0 4px;font-size:12px;font-weight:bold;color:#1f2937;border-top:2px solid #1f2937;">Total</td>
            <td align="right" style="padding:8px 0 4px;font-size:12px;font-weight:bold;color:#1f2937;border-top:2px solid #1f2937;">${total:.2f}</td>
        </tr>'''
    if abs(amount_paid - total) > 0.01 or change > 0.01:
        totals_rows += f'''
        <tr>
            <td colspan="{lbl_span}" style="padding:3px 0;font-size:10px;color:#6b7280;">Amount Paid</td>
            <td align="right" style="padding:3px 0;font-size:10px;">${amount_paid:.2f}</td>
        </tr>'''
        if change > 0.01:
            totals_rows += f'''
        <tr>
            <td colspan="{lbl_span}" style="padding:3px 0;font-size:10px;color:#6b7280;">Change</td>
            <td align="right" style="padding:3px 0;font-size:10px;">${change:.2f}</td>
        </tr>'''
        balance = max(0, total - amount_paid)
        if balance > 0.01:
            totals_rows += f'''
        <tr>
            <td colspan="4" style="padding:3px 0;font-size:10px;color:#dc2626;font-weight:bold;">Balance Due</td>
            <td align="right" style="padding:3px 0;font-size:10px;color:#dc2626;font-weight:bold;">${balance:.2f}</td>
        </tr>'''

    # Merge items + totals into one table so columns align
    items_rows = f'{items_rows}{totals_rows}'

    # Payment rows (handle split payments)
    payment_rows = ''
    if payment['is_split'] and payment['split_payments']:
        for sp in payment['split_payments']:
            method = sp.get('method', {})
            sp_amount = sp.get('amount', 0)
            sp_type = method.get('type', '')
            if sp_type in ('credit', 'debit'):
                label = 'Credit Card'
                detail = ''
                if method.get('brand') and method.get('last4'):
                    detail = f"{method['brand']} ****{method['last4']}"
                payment_rows += f'''
            <tr>
                <td style="padding:4px 0;font-size:11px;color:#1f2937;">{label}</td>
                <td style="text-align:right;padding:4px 0;font-size:11px;font-weight:bold;">${sp_amount:.2f}</td>
            </tr>'''
                if detail or customer_name:
                    parts = []
                    if customer_name:
                        parts.append(customer_name)
                    if detail:
                        parts.append(detail)
                    payment_rows += f'''
            <tr><td colspan="2" style="padding:0 0 4px 10px;font-size:9px;color:#6b7280;">{'<br/>'.join(parts)}</td></tr>'''
            else:
                label = _mask_payment_label(method.get('name', sp_type.title() if sp_type else 'Other'))
                payment_rows += f'''
            <tr>
                <td style="padding:4px 0;font-size:11px;color:#1f2937;">{label}</td>
                <td style="text-align:right;padding:4px 0;font-size:11px;font-weight:bold;">${sp_amount:.2f}</td>
            </tr>'''
    else:
        payment_text = payment['display']
        if payment['brand'] and payment['last4']:
            payment_text = f"{payment['brand']} ****{payment['last4']}"
        payment_rows = f'''
            <tr>
                <td style="padding:4px 0;font-size:11px;color:#1f2937;">{payment_text}</td>
                <td style="text-align:right;padding:4px 0;font-size:11px;font-weight:bold;">${amount_paid:.2f}</td>
            </tr>'''
        detail_parts = []
        if customer_name:
            detail_parts.append(customer_name)
        if payment['brand'] and payment['last4']:
            detail_parts.append(f"{payment['brand']} ****{payment['last4']}")
        if payment['auth_code']:
            detail_parts.append(f"Auth: {payment['auth_code']}")
        if detail_parts:
            payment_rows += f'''
            <tr><td colspan="2" style="padding:0 0 4px 10px;font-size:9px;color:#6b7280;">{'<br/>'.join(detail_parts)}</td></tr>'''

    # Transaction details section
    trans_section = ''
    trans_id = payment.get('transaction_id', '')
    auth_code = payment.get('auth_code', '')
    order_status = order_data.get('status', '')
    tx_status = 'Approved' if order_status in ('completed', 'processing', 'on-hold') else order_status.replace('-', ' ').title() if order_status else ''
    if trans_id or auth_code:
        trans_rows = ''
        if trans_id:
            trans_rows += f"<tr><td style='padding:3px 0;font-size:10px;color:#6b7280;'>Transaction ID</td><td style='padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;'>{trans_id}</td></tr>"
        if auth_code:
            trans_rows += f"<tr><td style='padding:3px 0;font-size:10px;color:#6b7280;'>Authorization Code</td><td style='padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;'>{auth_code}</td></tr>"
        if tx_status:
            trans_rows += f"<tr><td style='padding:3px 0;font-size:10px;color:#6b7280;'>Transaction Status</td><td style='padding:3px 0;text-align:right;font-size:10px;color:#059669;font-weight:bold;'>{tx_status}</td></tr>"
        trans_rows += f"<tr><td style='padding:3px 0;font-size:10px;color:#6b7280;'>Statement Descriptor</td><td style='padding:3px 0;text-align:right;font-size:10px;color:#1f2937;'>DOCTORS STUDIO {BUSINESS_PHONE}</td></tr>"
        trans_section = f'''
    <table width="100%" style="margin-top:14px;border:1px solid #e5e7eb;">
        <tr><td style="background:#e8edf2;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#183249;text-transform:uppercase;letter-spacing:1px;">Transaction Details</span>
        </td></tr>
        <tr><td style="padding:10px 12px;">
            <table width="100%">{trans_rows}</table>
        </td></tr>
    </table>'''

    # Shipping address section
    shipping = order_data.get('shipping', {})
    shipping_lines = _build_shipping_address_html(shipping)
    shipping_section = ''
    if shipping_lines:
        shipping_section = f'''
    <table width="100%" style="margin-top:14px;border:1px solid #e5e7eb;">
        <tr><td style="background:#e8edf2;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#183249;text-transform:uppercase;letter-spacing:1px;">Shipping Address</span>
        </td></tr>
        <tr><td style="padding:10px 12px;font-size:10px;color:#6b7280;line-height:1.6;">{shipping_lines}</td></tr>
    </table>'''

    # Subscription summary with terms
    sub_section = ''
    if has_subscription:
        next_billing = _get_next_billing_date(order_data)
        sub_items = []
        sub_item_billing = []  # (item_name, amount, freq) per item
        for item in line_items:
            if _is_subscription_item(item):
                sp, si = _get_subscription_frequency(item)
                if sp is None:
                    sp, si = _get_order_subscription_frequency(order_data)
                freq = _format_subscription_frequency(sp, si)
                if freq:
                    item_name = item.get('name', 'Item')
                    sub_items.append(f"<tr><td style='padding:3px 0;font-size:10px;color:#1f2937;'>&#8226; <strong>{item_name}</strong>: {freq}</td></tr>")
                    item_amount = float(item.get('total', '0')) or float(item.get('subtotal', '0'))
                    sub_item_billing.append((item_name, item_amount, freq))
        sub_list = ''.join(sub_items) if sub_items else "<tr><td style='padding:3px 0;font-size:10px;color:#6b7280;'>Billing cycle details are managed in your account.</td></tr>"
        # Next billing date row
        next_billing_row = f"<tr><td style='padding:3px 0;font-size:10px;color:#1f2937;'>&#8226; <strong>Next Billing Date</strong>: {next_billing}</td></tr>" if next_billing else ''
        # Subscription terms — one "You will be billed" line per item
        terms_text = ''
        for item_name, item_amount, freq_text in sub_item_billing:
            if item_amount > 0:
                terms_text += f"<p style='font-size:9px;color:#1f2937;margin:6px 0 2px;'>You will be billed <strong>${item_amount:.2f}</strong> {freq_text.lower()}.</p>"
        terms_text += f"<p style='font-size:9px;color:#6b7280;margin:2px 0 0;'>You may cancel anytime by contacting support@doctorsstudio.com or calling {BUSINESS_PHONE}.</p>"
        sub_section = f'''
    <table width="100%" style="margin-top:14px;border:1px solid #7c3aed;">
        <tr><td style="background:#f5f3ff;padding:8px 12px;border-bottom:1px solid #7c3aed;">
            <span style="font-size:10px;font-weight:bold;color:#7c3aed;">Subscription Information</span>
        </td></tr>
        <tr><td style="padding:10px 12px;">
            <table width="100%">{sub_list}{next_billing_row}</table>
            {terms_text}
        </td></tr>
    </table>'''

    # Refund section (for refund receipts)
    refund_section = ''
    if is_refund:
        refunds = order_data.get('refunds', [])
        if refunds:
            refund_rows = ''
            total_refunded = 0.0
            for r in refunds:
                r_amount = abs(float(r.get('total', '0')))
                total_refunded += r_amount
                r_reason = r.get('reason', '') or 'No reason provided'
                refund_rows += f'''
                <tr>
                    <td style="padding:4px 0;border-bottom:1px solid #e5e7eb;font-size:10px;">#{r.get("id", "N/A")}</td>
                    <td style="padding:4px 0;border-bottom:1px solid #e5e7eb;text-align:right;color:#dc2626;font-size:10px;">${r_amount:.2f}</td>
                    <td style="padding:4px 0;border-bottom:1px solid #e5e7eb;font-size:9px;">{r_reason}</td>
                </tr>'''
            refund_section = f'''
    <table width="100%" style="margin-top:14px;border:1px solid #dc2626;">
        <tr><td colspan="3" style="background:#fef2f2;padding:8px 12px;border-bottom:1px solid #dc2626;">
            <span style="font-size:10px;font-weight:bold;color:#dc2626;text-transform:uppercase;letter-spacing:1px;">Refund Details</span>
        </td></tr>
        <tr><td colspan="3" style="padding:8px 12px;">
            <table width="100%">
                <tr style="background:#fef2f2;">
                    <th style="text-align:left;padding:4px;font-size:10px;">Refund ID</th>
                    <th style="text-align:right;padding:4px;font-size:10px;">Amount</th>
                    <th style="text-align:left;padding:4px;font-size:10px;">Reason</th>
                </tr>
                {refund_rows}
                <tr>
                    <td style="font-weight:bold;padding:6px 0;font-size:10px;">Total Refunded</td>
                    <td style="text-align:right;font-weight:bold;padding:6px 0;color:#dc2626;font-size:10px;">${total_refunded:.2f}</td>
                    <td></td>
                </tr>
            </table>
        </td></tr>
    </table>'''

    # Credit Bank section
    credit_bank_section = ''
    cb_info = _get_credit_bank_info(order_data)
    if cb_info and cb_info['redeemed'] > 0:
        credit_bank_section = f'''
    <table width="100%" style="margin-top:14px;border:1px solid #0891b2;">
        <tr><td style="background:#ecfeff;padding:8px 12px;border-bottom:1px solid #0891b2;">
            <span style="font-size:10px;font-weight:bold;color:#0891b2;text-transform:uppercase;letter-spacing:1px;">Credit Bank</span>
        </td></tr>
        <tr><td style="padding:10px 12px;">
            <table width="100%">
                <tr>
                    <td style="padding:3px 0;font-size:10px;color:#1f2937;">Starting Balance</td>
                    <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">${cb_info["starting"]:.2f}</td>
                </tr>
                <tr>
                    <td style="padding:3px 0;font-size:10px;color:#1f2937;">Amount Redeemed</td>
                    <td style="padding:3px 0;text-align:right;font-size:10px;color:#0891b2;font-weight:bold;">-${cb_info["redeemed"]:.2f}</td>
                </tr>
                <tr>
                    <td style="padding:3px 0;font-size:10px;color:#1f2937;border-top:1px solid #e5e7eb;">Remaining Balance</td>
                    <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;border-top:1px solid #e5e7eb;">${cb_info["remaining"]:.2f}</td>
                </tr>
            </table>
        </td></tr>
    </table>'''

    if is_refund:
        title = 'Refund Receipt'
        header_bg = '#183249'
    elif has_subscription:
        title = 'Subscription Order Receipt'
        header_bg = '#183249'
    else:
        title = 'Order Receipt'
        header_bg = '#183249'

    html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/>
<style>
    @page {{ size: letter; margin: 1cm; }}
    body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #1f2937; margin: 0; padding: 0; }}
    table {{ border-collapse: collapse; }}
    .keep-together {{ -pdf-keep-with-next: true; }}
</style>
</head>
<body>
    <!-- Logo -->
    <table width="100%" style="margin-bottom:0;background:#ffffff;">
        <tr><td style="padding:10px 20px;text-align:center;background:#ffffff;">
            <img src="file:///{PDF_LOGO_PATH.replace(chr(92), '/')}" width="200" style="background:#ffffff;" />
        </td></tr>
    </table>

    <!-- Header bar -->
    <table width="100%" style="background:{header_bg};margin-bottom:10px;">
        <tr>
            <td style="padding:12px 20px;">
                <span style="font-size:10px;color:rgba(255,255,255,0.85);">{title}</span>
            </td>
            <td style="padding:12px 20px;text-align:right;">
                <span style="font-size:10px;color:rgba(255,255,255,0.85);">Order #{order_number}</span><br/>
                <span style="font-size:9px;color:rgba(255,255,255,0.7);">{order_date}</span>
            </td>
        </tr>
    </table>

    <!-- Greeting -->
    <p style="font-size:11px;color:#1f2937;margin:0 0 2px 0;">Hi <strong>{customer_name}</strong>,</p>
    <p style="font-size:10px;color:#6b7280;margin:0 0 8px 0;">Thank you for your purchase! Here is your receipt.</p>

    <!-- Items table -->
    <table width="100%" style="border:1px solid #e5e7eb;">
        <tr class="keep-together"><td colspan="2" style="background:#e8edf2;padding:6px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#183249;text-transform:uppercase;letter-spacing:1px;">Order Items</span>
        </td></tr>
        <tr><td colspan="2" style="padding:4px 12px 10px;">
            <table width="100%">
                {items_rows}
            </table>
        </td></tr>
    </table>

    <!-- Payment section -->
    <table width="100%" style="margin-top:14px;border:1px solid #e5e7eb;">
        <tr><td style="background:#e8edf2;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#183249;text-transform:uppercase;letter-spacing:1px;">Payment</span>
        </td></tr>
        <tr><td style="padding:8px 12px;">
            <table width="100%">
                {payment_rows}
            </table>
        </td></tr>
    </table>

    {trans_section}
    {shipping_section}
    {sub_section}
    {refund_section}
    {credit_bank_section}

    <!-- Help -->
    <table width="100%" style="background:#f9fafb;margin-top:14px;">
        <tr><td style="padding:10px 12px;text-align:center;">
            <span style="font-size:10px;color:#6b7280;">Questions about your order?</span><br/>
            <span style="font-size:11px;color:#1f2937;font-weight:bold;">Call us at {BUSINESS_PHONE}</span><br/>
            <span style="font-size:10px;color:#6b7280;">or email us at support@doctorsstudio.com</span>
        </td></tr>
    </table>

    <!-- Footer -->
    <table width="100%" style="margin-top:18px;border-top:1px solid #e5e7eb;">
        <tr><td style="padding:10px 0;text-align:center;">
            <span style="font-size:9px;font-weight:bold;color:#1f2937;">Refund Policy</span><br/>
            <span style="font-size:9px;color:#6b7280;">All sales are final. No refunds or exchanges.</span><br/><br/>
            <span style="font-size:9px;color:#1f2937;font-weight:bold;">Thank you for your business!</span>
        </td></tr>
        <tr><td style="background:#f9fafb;padding:8px 0;text-align:center;">
            <span style="font-size:8px;color:#9ca3af;">{BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}</span><br/>
            <span style="font-size:8px;color:#9ca3af;">{BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}</span>
        </td></tr>
    </table>
</body>
</html>'''
    return html


BRAND_COLOR = '#183249'
BRAND_LIGHT = '#e8edf2'
REFUND_COLOR = '#92400e'
REFUND_LIGHT = '#fffbeb'
BORDER_COLOR = '#e5e7eb'
TEXT_PRIMARY = '#1f2937'
TEXT_SECONDARY = '#6b7280'
TEXT_MUTED = '#9ca3af'


def _get_credit_bank_info(order_data):
    """Extract credit bank redemption info from order meta_data.
    Returns dict with 'redeemed' and 'remaining' or None."""
    for meta in order_data.get('meta_data', []):
        if meta.get('key') == '_credit_bank_redeemed':
            try:
                redeemed = float(meta['value'])
                remaining = 0.0
                for m2 in order_data.get('meta_data', []):
                    if m2.get('key') == '_credit_bank_remaining':
                        remaining = float(m2['value'])
                return {'redeemed': redeemed, 'remaining': remaining, 'starting': redeemed + remaining}
            except (ValueError, TypeError):
                pass
    return None


def _get_item_prices(item):
    """Extract original and discounted unit prices from a WooCommerce line item.

    Uses a priority chain to avoid phantom discounts caused by WC membership
    plugins transiently inflating the ``subtotal`` field:
      1. ``_ds_pos_original_price`` / ``_ds_pos_modified_price`` (trusted unit prices)
      2. ``_ds_pos_locked_subtotal`` / ``_ds_pos_locked_total`` ÷ qty
      3. ``total / qty`` for both (never trust ``subtotal`` alone for POS orders)
    """
    total = float(item.get('total', '0'))
    qty = item.get('quantity', 1)
    meta_data = item.get('meta_data', [])

    pos_original = None
    pos_modified = None
    locked_subtotal = None
    locked_total = None

    for meta in meta_data:
        key = meta.get('key')
        if key == '_ds_pos_original_price':
            pos_original = float(meta['value'])
        elif key == '_ds_pos_modified_price':
            pos_modified = float(meta['value'])
        elif key == '_ds_pos_locked_subtotal':
            locked_subtotal = float(meta['value'])
        elif key == '_ds_pos_locked_total':
            locked_total = float(meta['value'])

    if pos_original is not None:
        return pos_original, pos_modified if pos_modified is not None else pos_original

    if locked_subtotal is not None and locked_total is not None:
        return (locked_subtotal / qty if qty else locked_subtotal,
                locked_total / qty if qty else locked_total)

    safe_price = total / qty if qty else total
    return safe_price, safe_price


def _parse_series_count(value):
    """Best-effort parse of series count from mixed metadata values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        iv = int(value)
        return iv if iv > 0 else None

    text = str(value).strip()
    if not text:
        return None
    low = text.lower()
    if low in ('single', 'single series', 'single session'):
        return 1

    for pattern in (
        r'series\s*of\s*(\d+)',
        r'(\d+)\s*x?\s*series',
        r'(\d+)\s*sessions?',
        r'^(\d+)$',
    ):
        m = re.search(pattern, low, flags=re.IGNORECASE)
        if not m:
            continue
        try:
            parsed = int(m.group(1))
            if parsed > 0:
                return parsed
        except (ValueError, TypeError):
            continue
    return None


def _get_item_series_count(item):
    """Extract series count from line-item fields/meta_data when available."""
    for key in ('series_count', 'seriesCount', 'seriesSelection', 'selectSeries', 'select_series'):
        parsed = _parse_series_count(item.get(key))
        if parsed:
            return parsed

    preferred_meta_keys = (
        'ds_series_count',
        'series_count',
        'series_selection',
        'series',
        'pa_series',
        'attribute_pa_series',
        'selectseries',
    )
    meta_data = item.get('meta_data', [])
    if isinstance(meta_data, list):
        for meta in meta_data:
            raw_key = str(meta.get('key') or meta.get('display_key') or '').strip().lower()
            if not raw_key:
                continue
            raw_val = meta.get('display_value') if meta.get('display_value') not in (None, '') else meta.get('value')
            if any(raw_key == k or k in raw_key for k in preferred_meta_keys):
                parsed = _parse_series_count(raw_val)
                if parsed:
                    return parsed
        for meta in meta_data:
            raw_key = str(meta.get('key') or meta.get('display_key') or '').strip().lower()
            if 'series' not in raw_key:
                continue
            raw_val = meta.get('display_value') if meta.get('display_value') not in (None, '') else meta.get('value')
            parsed = _parse_series_count(raw_val)
            if parsed:
                return parsed
    return None


def _with_series_suffix(name, series_count):
    if not name or not series_count or series_count <= 1:
        return name
    if re.search(r'\(\s*\d+\s+series\s*\)', str(name), flags=re.IGNORECASE):
        return name
    return f'{name} ({series_count} Series)'


def _is_subscription_item(item):
    """Check if a WooCommerce line item is a subscription."""
    # Keys whose mere presence indicates a subscription (any value)
    presence_keys = {'_subscription_sign_up_fee', '_subscription_period', 'is_subscription',
                     '_ds_pos_is_subscription', 'subscription_billing_period',
                     'subscription_billing_frequency'}
    # Keys that must have a truthy value to indicate a subscription
    truthy_keys = {'subscription_selected'}
    for meta in item.get('meta_data', []):
        key = meta.get('key', '')
        if key in presence_keys:
            return True
        if key in truthy_keys:
            val = meta.get('value')
            if val and str(val).lower() not in ('false', '0', 'no', ''):
                return True
    return False


def _get_subscription_frequency(item):
    """Extract subscription billing period and interval from a WooCommerce line item.
    Returns (period, interval) tuple, e.g. ('month', 1) or (None, None) if not a subscription.
    Checks both WooCommerce-native keys and POS-specific keys for compatibility."""
    period = None
    interval = None
    # POS-specific keys (stored in flat metadata serialized into meta_data)
    pos_period = None
    pos_interval = None
    pos_billing_frequency = None
    for meta in item.get('meta_data', []):
        key = meta.get('key', '')
        value = meta.get('value')
        if key == '_subscription_period':
            period = value or 'month'
        elif key in ('_subscription_period_interval', '_subscription_interval'):
            try:
                interval = int(value or 1)
            except (ValueError, TypeError):
                interval = 1
        elif key == 'subscription_billing_period':
            pos_period = value
        elif key == 'subscription_billing_interval':
            try:
                pos_interval = int(value or 1)
            except (ValueError, TypeError):
                pos_interval = 1
        elif key == 'subscription_billing_frequency':
            pos_billing_frequency = value
    # Prefer WooCommerce-native keys
    if period is not None:
        return period, interval or 1
    # Fallback to POS-specific keys
    if pos_period is not None:
        return pos_period, pos_interval or 1
    # Fallback to POS billing frequency string
    if pos_billing_frequency:
        freq_map = {
            'weekly': ('week', 1),
            'biweekly': ('week', 2),
            'monthly': ('month', 1),
            'bimonthly': ('month', 2),
            'quarterly': ('month', 3),
            'yearly': ('year', 1),
        }
        if pos_billing_frequency in freq_map:
            return freq_map[pos_billing_frequency]
    return None, None


def _get_order_subscription_frequency(order_data):
    """Fallback: extract subscription billing period/interval from ORDER-level meta_data.
    Used when individual line items don't carry period/interval (common in renewal orders)."""
    period = None
    interval = None
    for meta in order_data.get('meta_data', []):
        key = meta.get('key', '')
        if key in ('_subscription_period', '_billing_period', '_order_subscription_period'):
            period = meta.get('value')
        elif key in ('_subscription_period_interval', '_billing_interval', '_order_subscription_interval'):
            try:
                interval = int(meta.get('value', 1))
            except (ValueError, TypeError):
                interval = 1
    if period:
        return period, interval or 1
    return None, None


def _enrich_item_with_subscription(item, sub_data):
    """Write subscription frequency into a single line item's meta_data."""
    period = sub_data.get('billing_period')
    interval = sub_data.get('billing_interval')
    if not period:
        return False
    item.setdefault('meta_data', []).extend([
        {'key': '_subscription_period', 'value': period},
        {'key': '_subscription_period_interval', 'value': str(interval or 1)},
    ])
    next_pay = sub_data.get('next_payment_date_gmt') or sub_data.get('next_payment_date', '')
    if next_pay:
        item['meta_data'].append({'key': '_subscription_next_payment_date', 'value': next_pay})
    return True


def _enrich_order_subscription_frequency(order_data):
    """Enrich order_data with subscription billing_period/billing_interval by fetching
    the related subscriptions from WooCommerce API. Writes frequency PER ITEM so that
    orders with multiple subscription items at different frequencies display correctly.
    Modifies order_data in place."""
    line_items = order_data.get('line_items', [])
    has_sub_items = any(_is_subscription_item(item) for item in line_items)

    # Also check order-level meta for subscription indicators
    if not has_sub_items:
        for meta in order_data.get('meta_data', []):
            key = meta.get('key', '')
            if key in ('_related_subscriptions', '_subscription_renewal',
                       '_subscription_ids', '_wcs_subscription_ids_cache'):
                if meta.get('value'):
                    has_sub_items = True
                    for item in line_items:
                        item.setdefault('meta_data', []).append(
                            {'key': '_ds_pos_is_subscription', 'value': 'yes'})
                    break
        if not has_sub_items:
            return

    # Check which subscription items still need frequency
    items_needing_freq = []
    for item in line_items:
        if _is_subscription_item(item):
            sp, _ = _get_subscription_frequency(item)
            if not sp:
                items_needing_freq.append(item)
    if not items_needing_freq:
        return

    order_id = order_data.get('id')
    try:
        wc_api = WooCommerceAPI()

        # Collect ALL subscription IDs from order meta
        all_sub_ids = []
        for meta in order_data.get('meta_data', []):
            key = meta.get('key', '')
            val = meta.get('value')
            if key == '_related_subscriptions' and val:
                if isinstance(val, list):
                    all_sub_ids.extend([str(v) for v in val if str(v).isdigit()])
                elif isinstance(val, (int, str)) and str(val).isdigit():
                    all_sub_ids.append(str(val))
            elif key == '_subscription_renewal' and val and str(val).isdigit():
                all_sub_ids.append(str(val))
            elif key in ('_subscription_ids', '_wcs_subscription_ids_cache') and val:
                try:
                    sub_ids = val
                    if isinstance(sub_ids, str):
                        sub_ids = json.loads(sub_ids)
                    if isinstance(sub_ids, list):
                        all_sub_ids.extend([str(v) for v in sub_ids if str(v).isdigit()])
                    elif isinstance(sub_ids, dict):
                        all_sub_ids.extend([str(v) for v in sub_ids.values() if str(v).isdigit()])
                except Exception:
                    pass
        all_sub_ids = list(dict.fromkeys(all_sub_ids))  # deduplicate, preserve order

        # Build product_id -> item lookup for matching
        pid_to_items = {}
        for item in items_needing_freq:
            pid = str(item.get('product_id', ''))
            if pid:
                pid_to_items.setdefault(pid, []).append(item)

        enriched_count = 0

        # Fetch each subscription and match its line items to order items by product_id
        for sub_id in all_sub_ids:
            try:
                response = wc_api.wcapi.get(f"subscriptions/{sub_id}")
                if not response.ok:
                    continue
                sub_data = response.json()
                sub_line_items = sub_data.get('line_items', [])

                if sub_line_items:
                    # Match by product_id
                    for sub_item in sub_line_items:
                        sub_pid = str(sub_item.get('product_id', ''))
                        if sub_pid in pid_to_items:
                            for order_item in pid_to_items.pop(sub_pid, []):
                                if _enrich_item_with_subscription(order_item, sub_data):
                                    enriched_count += 1
                                    logger.info(f"Enriched item '{order_item.get('name')}' with {sub_data.get('billing_period')}/{sub_data.get('billing_interval')} from subscription #{sub_id}")
                else:
                    # Subscription has no line items — single-product subscription
                    # Apply to first unmatched item
                    if pid_to_items:
                        first_pid = next(iter(pid_to_items))
                        for order_item in pid_to_items.pop(first_pid, []):
                            if _enrich_item_with_subscription(order_item, sub_data):
                                enriched_count += 1
                                logger.info(f"Enriched item '{order_item.get('name')}' with {sub_data.get('billing_period')}/{sub_data.get('billing_interval')} from subscription #{sub_id} (no line items match)")
            except Exception as sub_err:
                logger.warning(f"Failed to fetch subscription #{sub_id} for order #{order_id}: {sub_err}")

        # Fallback: if we still have unmatched items and found at least one subscription,
        # try matching by parent_id (for parent orders)
        if pid_to_items and not all_sub_ids:
            customer_id = order_data.get('customer_id')
            if customer_id:
                result = wc_api.get_subscriptions(per_page=20, customer=customer_id)
                if result.get('status') == 'success' and result.get('data'):
                    for sub in result['data']:
                        if str(sub.get('parent_id')) == str(order_id):
                            for sub_item in sub.get('line_items', []):
                                sub_pid = str(sub_item.get('product_id', ''))
                                if sub_pid in pid_to_items:
                                    for order_item in pid_to_items.pop(sub_pid, []):
                                        if _enrich_item_with_subscription(order_item, sub):
                                            enriched_count += 1
                                            logger.info(f"Enriched item '{order_item.get('name')}' with {sub.get('billing_period')}/{sub.get('billing_interval')} from subscription #{sub.get('id')} (parent_id match)")

        # If still unmatched items remain and we enriched at least one, apply first enriched
        # frequency as order-level fallback for remaining items
        if pid_to_items and enriched_count > 0:
            logger.info(f"Order #{order_id}: {len(pid_to_items)} items still unmatched after per-item enrichment, they will use item-level fallback")

        # If nothing was enriched at all, set order-level fallback from first subscription
        if enriched_count == 0 and all_sub_ids:
            try:
                response = wc_api.wcapi.get(f"subscriptions/{all_sub_ids[0]}")
                if response.ok:
                    sub_data = response.json()
                    period = sub_data.get('billing_period')
                    interval = sub_data.get('billing_interval')
                    if period:
                        enrichment = [
                            {'key': '_subscription_period', 'value': period},
                            {'key': '_subscription_period_interval', 'value': str(interval or 1)}
                        ]
                        next_pay = sub_data.get('next_payment_date_gmt') or sub_data.get('next_payment_date', '')
                        if next_pay:
                            enrichment.append({'key': '_subscription_next_payment_date', 'value': next_pay})
                        order_data.setdefault('meta_data', []).extend(enrichment)
                        logger.info(f"Order #{order_id}: order-level fallback frequency {period}/{interval} from subscription #{all_sub_ids[0]}")
            except Exception:
                pass

        if enriched_count == 0:
            logger.info(f"Could not find subscription frequency for order #{order_id}")
        else:
            logger.info(f"Enriched {enriched_count} subscription items for order #{order_id}")
    except Exception as e:
        logger.warning(f"Failed to enrich subscription frequency for order #{order_id}: {e}")


def _format_subscription_frequency(period, interval):
    """Format subscription frequency for display, e.g. 'Every 1 month', 'Every 2 months'."""
    if not period:
        return ''
    period_lower = period.lower()
    plural = period_lower if period_lower.endswith('s') else f'{period_lower}s'
    return f'Every {interval} {plural}'


def _get_next_billing_date(order_data):
    """Extract next billing date from enriched order metadata."""
    for meta in order_data.get('meta_data', []):
        if meta.get('key') == '_subscription_next_payment_date':
            raw = meta.get('value', '')
            if raw:
                try:
                    dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                    return dt.strftime('%B %d, %Y')
                except (ValueError, TypeError):
                    return raw
    return ''


def _get_payment_details(order_data):
    """Extract structured payment details from order data."""
    result = {
        'display': 'N/A',
        'type': None,
        'brand': None,
        'last4': None,
        'auth_code': None,
        'transaction_id': None,
        'is_split': False,
        'split_payments': [],
    }

    # Extract transaction ID from WooCommerce order-level field first
    woo_trans_id = order_data.get('transaction_id', '')
    if woo_trans_id and not woo_trans_id.startswith('ORD-'):
        result['transaction_id'] = woo_trans_id

    meta_data = order_data.get('meta_data', [])

    # Check _pos_transaction_id meta as fallback
    if not result['transaction_id']:
        for meta in meta_data:
            if meta.get('key') == '_pos_transaction_id':
                val = meta.get('value', '')
                if val and not val.startswith('ORD-'):
                    result['transaction_id'] = val
                    break

    for meta in meta_data:
        if meta.get('key') == '_pos_payment_method':
            try:
                pm = json.loads(meta['value']) if isinstance(meta['value'], str) else meta['value']
                result['type'] = pm.get('type')
                result['brand'] = pm.get('brand')
                result['last4'] = pm.get('last4')
                result['auth_code'] = pm.get('authCode')

                if pm.get('type') == 'split' and pm.get('splitPayments'):
                    result['is_split'] = True
                    result['display'] = 'Cash'
                    result['split_payments'] = pm['splitPayments']
                elif pm.get('type') in ('credit', 'debit'):
                    brand = pm.get('brand', '')
                    last4 = pm.get('last4', '')
                    if brand and last4:
                        result['display'] = f'Credit Card ({brand} ****{last4})'
                    else:
                        result['display'] = 'Credit Card'
                elif pm.get('type') == 'cash':
                    result['display'] = 'Cash'
                else:
                    result['display'] = pm.get('name', pm.get('type', 'N/A'))
                return result
            except Exception:
                pass

    # Fallback: check for Stripe card metadata
    for meta in meta_data:
        key = meta.get('key', '')
        if key == '_stripe_card_brand' and meta.get('value'):
            result['brand'] = meta['value']
        elif key == '_stripe_card_last4' and meta.get('value'):
            result['last4'] = meta['value']

    if result['brand'] and result['last4']:
        result['display'] = f"{result['brand'].title()} ending in {result['last4']}"
        return result

    title = order_data.get('payment_method_title', '')
    method = order_data.get('payment_method', '')
    if title:
        result['display'] = 'Cash' if 'split' in title.lower() else title
    elif method:
        display = method.replace('_', ' ').title()
        result['display'] = 'Cash' if 'split' in display.lower() else display
    return result


def _get_amount_paid(order_data, total):
    """Extract amount paid and calculate change from order meta."""
    amount_paid = total
    change = 0.0
    for meta in order_data.get('meta_data', []):
        if meta.get('key') == '_amount_paid' and meta.get('value'):
            try:
                amount_paid = float(meta['value'])
                if amount_paid > total:
                    change = amount_paid - total
            except (ValueError, TypeError):
                pass
    return amount_paid, change


def _get_customer_note(order_data):
    """Get customer note from order data, filtering out internal POS notes."""
    import re
    note = order_data.get('customer_note', '')
    if not note:
        return ''
    # Strip internal POS notes that should never be shown to customers
    # These start with [Name] prefix or contain POS Order tracking info
    if re.match(r'^\[.+?\]\s*', note):
        return ''
    if 'POS Order:' in note:
        return ''
    return note


def _get_expected_refund_date():
    """Calculate expected refund date (5 business days from now)."""
    date = datetime.now()
    business_days = 5
    while business_days > 0:
        date += timedelta(days=1)
        if date.weekday() < 5:
            business_days -= 1
    return date.strftime('%b %d, %Y')


def _format_date(date_str):
    """Format a WooCommerce date string to a readable format."""
    if not date_str:
        return 'N/A'
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime('%b %d, %Y at %I:%M %p')
    except (ValueError, AttributeError):
        if 'T' in date_str:
            return date_str.replace('T', ' ').split('.')[0]
        return date_str


def _build_billing_address_html(billing):
    """Build a formatted billing address block."""
    parts = []
    name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
    if name:
        parts.append(name)
    if billing.get('address_1'):
        parts.append(billing['address_1'])
    if billing.get('address_2'):
        parts.append(billing['address_2'])
    city_state = ''
    if billing.get('city'):
        city_state = billing['city']
    if billing.get('state'):
        city_state += f", {billing['state']}"
    if billing.get('postcode'):
        city_state += f" {billing['postcode']}"
    if city_state:
        parts.append(city_state)
    if billing.get('email'):
        parts.append(billing['email'])
    if billing.get('phone'):
        parts.append(billing['phone'])
    return '<br>'.join(parts) if parts else ''


def _build_shipping_address_html(shipping):
    """Build a formatted shipping address block."""
    parts = []
    name = f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()
    if name:
        parts.append(name)
    if shipping.get('address_1'):
        parts.append(shipping['address_1'])
    if shipping.get('address_2'):
        parts.append(shipping['address_2'])
    city_state = ''
    if shipping.get('city'):
        city_state = shipping['city']
    if shipping.get('state'):
        city_state += f", {shipping['state']}"
    if shipping.get('postcode'):
        city_state += f" {shipping['postcode']}"
    if city_state:
        parts.append(city_state)
    return '<br>'.join(parts) if parts else ''


def _mask_payment_label(label):
    """Mask internal payment labels for customer-facing display."""
    if not label:
        return 'Other'
    lower = label.lower()
    if 'outside' in lower and 'pos' in lower:
        return 'Other'
    if 'split' in lower:
        return 'Cash'
    return label


def _build_payment_section_html(payment, amount, customer_name=''):
    """Build the payment details HTML block."""
    rows = ''
    if payment['is_split'] and payment['split_payments']:
        for sp in payment['split_payments']:
            method = sp.get('method', {})
            sp_amount = sp.get('amount', 0)
            sp_type = method.get('type', '')
            if sp_type in ('credit', 'debit'):
                label = 'Credit Card'
                detail = ''
                if method.get('brand') and method.get('last4'):
                    detail = f"{method['brand']} ****{method['last4']}"
                elif method.get('last4'):
                    detail = f"****{method['last4']}"
                rows += f'''
                <tr>
                    <td style="padding:6px 0;color:{TEXT_PRIMARY};font-size:13px;">{label}</td>
                    <td style="padding:6px 0;text-align:right;font-size:13px;font-weight:600;">${sp_amount:.2f}</td>
                </tr>'''
                if detail or customer_name:
                    rows += f'''
                <tr><td colspan="2" style="padding:0 0 6px 12px;font-size:11px;color:{TEXT_SECONDARY};">'''
                    if customer_name:
                        rows += f'{customer_name}<br>'
                    if detail:
                        rows += detail
                    if method.get('authCode'):
                        rows += f'<br>Auth: {method["authCode"]}'
                    rows += '</td></tr>'
            else:
                label = _mask_payment_label(method.get('name', sp_type.title() if sp_type else 'Other'))
                rows += f'''
                <tr>
                    <td style="padding:6px 0;color:{TEXT_PRIMARY};font-size:13px;">{label}</td>
                    <td style="padding:6px 0;text-align:right;font-size:13px;font-weight:600;">${sp_amount:.2f}</td>
                </tr>'''
    else:
        rows += f'''
        <tr>
            <td style="padding:6px 0;color:{TEXT_PRIMARY};font-size:13px;">{payment["display"]}</td>
            <td style="padding:6px 0;text-align:right;font-size:13px;font-weight:600;">${amount:.2f}</td>
        </tr>'''
        detail_parts = []
        if customer_name:
            detail_parts.append(customer_name)
        if payment['brand'] and payment['last4']:
            detail_parts.append(f"{payment['brand']} ****{payment['last4']}")
        elif payment['last4']:
            detail_parts.append(f"****{payment['last4']}")
        if payment['auth_code']:
            detail_parts.append(f"Auth: {payment['auth_code']}")
        if detail_parts:
            rows += f'''
        <tr><td colspan="2" style="padding:0 0 6px 12px;font-size:11px;color:{TEXT_SECONDARY};">
            {'<br>'.join(detail_parts)}
        </td></tr>'''

    return f'<table width="100%" cellpadding="0" cellspacing="0">{rows}</table>'


def _build_order_receipt_html(order_data):
    """Build a professional HTML order receipt email."""
    order_number = order_data.get('number', order_data.get('id', 'N/A'))
    order_date_raw = order_data.get('date_created', '')
    order_date = _format_date(order_date_raw)

    billing = order_data.get('billing', {})
    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
    customer_email = billing.get('email', '')
    shipping = order_data.get('shipping', {})
    shipping_address_html = _build_shipping_address_html(shipping)

    total = float(order_data.get('total', '0.00'))
    discount_total = float(order_data.get('discount_total', '0.00'))
    total_tax = float(order_data.get('total_tax', '0.00'))

    payment = _get_payment_details(order_data)
    amount_paid, change = _get_amount_paid(order_data, total)
    customer_note = _get_customer_note(order_data)

    line_items = order_data.get('line_items', [])
    has_subscription = any(_is_subscription_item(item) for item in line_items)

    # Bundle pricing detection: if all items are $0 but order total > 0,
    # show the full total on the first line item
    all_items_zero = total > 0 and all(
        abs(float(i.get('subtotal', '0'))) < 0.01 and abs(float(i.get('total', '0'))) < 0.01
        for i in line_items
    ) if line_items else False
    logger.info(f'Email receipt #{order_number}: total={total}, all_items_zero={all_items_zero}, items={len(line_items)}')

    # Calculate subtotal from item prices and build tabular items table
    computed_subtotal = 0.0
    brand_cache = {}
    disc_c = '#059669'
    hdr_color = '#6b7280'
    border = f'1px solid {BORDER_COLOR}'

    # Build table header
    th_qty = f'<th align="left" style="padding:8px 4px 8px 0;border-bottom:2px solid {TEXT_PRIMARY};font-size:11px;font-weight:700;color:{hdr_color};width:36px;">Qty</th>'
    th_desc = f'<th align="left" style="padding:8px 4px;border-bottom:2px solid {TEXT_PRIMARY};font-size:11px;font-weight:700;color:{hdr_color};">Description</th>'
    th_price = f'<th align="right" style="padding:8px 4px;border-bottom:2px solid {TEXT_PRIMARY};font-size:11px;font-weight:700;color:{hdr_color};width:64px;">Price</th>'
    th_disc = f'<th align="right" style="padding:8px 4px;border-bottom:2px solid {TEXT_PRIMARY};font-size:11px;font-weight:700;color:{hdr_color};width:72px;">Discount</th>'
    th_line = f'<th align="right" style="padding:8px 0 8px 4px;border-bottom:2px solid {TEXT_PRIMARY};font-size:11px;font-weight:700;color:{hdr_color};width:76px;">Line total</th>'
    items_rows_html = f'<tr>{th_qty}{th_desc}{th_price}{th_disc}{th_line}</tr>'

    for idx, item in enumerate(line_items):
        name = item.get('name', 'Unknown Item')
        variation_label = _get_item_variation_name(item)
        if variation_label and variation_label not in name:
            name = f'{name} - {variation_label}'
        name = _with_series_suffix(name, _get_item_series_count(item))
        qty = item.get('quantity', 1)
        sku = item.get('sku', '')
        brand = _get_item_brand(item, brand_cache)
        original_price, current_price = _get_item_prices(item)

        # For bundle pricing: assign full total to first item, rest stay $0
        if all_items_zero and idx == 0:
            original_price = total / qty if qty else total
            current_price = original_price

        item_subtotal = original_price * qty
        computed_subtotal += item_subtotal
        has_discount = abs(original_price - current_price) > 0.01
        discount_amount = (original_price - current_price) * qty
        line_net = current_price * qty
        is_sub = _is_subscription_item(item)
        sub_period, sub_interval = _get_subscription_frequency(item) if is_sub else (None, None)
        if is_sub and sub_period is None:
            sub_period, sub_interval = _get_order_subscription_frequency(order_data)
        freq_text = _format_subscription_frequency(sub_period, sub_interval)

        sub_badge = ''
        if is_sub:
            freq_label = f' &mdash; {freq_text}' if freq_text else ''
            sub_badge = f'<div style="color:#7c3aed;font-size:10px;font-weight:600;margin-top:2px;">SUBSCRIPTION{freq_label}</div>'
        brand_line = f'<div style="font-size:10px;color:{hdr_color};margin-top:2px;"><span style="font-weight:600;">{brand}</span></div>' if brand else ''
        sku_line = f'<div style="font-size:10px;color:{hdr_color};margin-top:1px;">SKU: {sku}</div>' if sku else ''

        # For bundle child items, show blank prices
        is_bundle_child = all_items_zero and idx > 0
        if is_bundle_child:
            price_cell = ''
            disc_cell = ''
            total_cell = ''
        else:
            price_cell = f'${original_price:.2f}'
            disc_cell = f'<span style="color:{disc_c};">{_invoice_discount_column_text(item, discount_amount)}</span>' if has_discount else ''
            total_cell = f'${line_net:.2f}'

        items_rows_html += f'''
        <tr>
            <td align="left" style="padding:8px 4px 8px 0;border-bottom:{border};font-size:13px;color:{TEXT_PRIMARY};vertical-align:top;">{qty}</td>
            <td align="left" style="padding:8px 4px;border-bottom:{border};font-size:13px;color:{TEXT_PRIMARY};vertical-align:top;"><div style="font-weight:600;">{name}</div>{brand_line}{sku_line}{sub_badge}</td>
            <td align="right" style="padding:8px 4px;border-bottom:{border};font-size:13px;color:{TEXT_PRIMARY};vertical-align:top;">{price_cell}</td>
            <td align="right" style="padding:8px 4px;border-bottom:{border};font-size:13px;color:{disc_c};vertical-align:top;">{disc_cell}</td>
            <td align="right" style="padding:8px 0 8px 4px;border-bottom:{border};font-size:13px;font-weight:700;color:{TEXT_PRIMARY};vertical-align:top;">{total_cell}</td>
        </tr>'''

    # Totals — aligned to 5-column table: label spans cols 1-2, Subtotal under Price, Discount under Discount, Total under Line total
    subtotal_display = '' if all_items_zero else f'${computed_subtotal:.2f}'
    disc_color_order_rcpt = '#059669' if discount_total > 0 else TEXT_SECONDARY
    disc_display = f'-${discount_total:.2f}' if discount_total > 0 else ''
    totals_html = f'''
        <tr>
            <td colspan="2" style="padding:8px 0;font-size:13px;color:{TEXT_SECONDARY};">Subtotal</td>
            <td align="right" style="padding:8px 4px;font-size:13px;color:{TEXT_PRIMARY};">{subtotal_display}</td>
            <td align="right" style="padding:8px 4px;font-size:13px;color:{disc_color_order_rcpt};">{disc_display}</td>
            <td align="right" style="padding:8px 0;font-size:13px;color:{TEXT_PRIMARY};font-weight:700;">${total:.2f}</td>
        </tr>'''
    if total_tax > 0:
        totals_html += f'''
        <tr>
            <td colspan="4" style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Tax</td>
            <td align="right" style="padding:4px 0;font-size:13px;color:{TEXT_PRIMARY};">${total_tax:.2f}</td>
        </tr>'''
    totals_html += f'''
        <tr>
            <td colspan="4" style="padding:10px 0 4px;font-size:15px;font-weight:700;color:{TEXT_PRIMARY};border-top:2px solid {TEXT_PRIMARY};">Total</td>
            <td align="right" style="padding:10px 0 4px;font-size:15px;font-weight:700;color:{TEXT_PRIMARY};border-top:2px solid {TEXT_PRIMARY};">${total:.2f}</td>
        </tr>'''
    if abs(amount_paid - total) > 0.01 or change > 0.01:
        totals_html += f'''
        <tr>
            <td colspan="4" style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Amount Paid</td>
            <td align="right" style="padding:4px 0;font-size:13px;">${amount_paid:.2f}</td>
        </tr>'''
        if change > 0.01:
            totals_html += f'''
        <tr>
            <td colspan="4" style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Change</td>
            <td align="right" style="padding:4px 0;font-size:13px;">${change:.2f}</td>
        </tr>'''
        balance = max(0, total - amount_paid)
        if balance > 0.01:
            totals_html += f'''
        <tr>
            <td colspan="4" style="padding:4px 0;font-size:13px;color:#dc2626;font-weight:600;">Balance Due</td>
            <td align="right" style="padding:4px 0;font-size:13px;color:#dc2626;font-weight:600;">${balance:.2f}</td>
        </tr>'''

    items_html = f'<table width="100%" cellpadding="0" cellspacing="0">{items_rows_html}{totals_html}</table>'

    payment_section = _build_payment_section_html(payment, amount_paid, customer_name)

    # Transaction details section
    trans_section = ''
    trans_id = payment.get('transaction_id', '')
    auth_code_val = payment.get('auth_code', '')
    order_status = order_data.get('status', '')
    tx_status = 'Approved' if order_status in ('completed', 'processing', 'on-hold') else order_status.replace('-', ' ').title() if order_status else ''
    if trans_id or auth_code_val:
        trans_rows_html = ''
        if trans_id:
            trans_rows_html += f'<div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">Transaction ID: <strong style="color:{TEXT_PRIMARY};">{trans_id}</strong></div>'
        if auth_code_val:
            trans_rows_html += f'<div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">Authorization Code: <strong style="color:{TEXT_PRIMARY};">{auth_code_val}</strong></div>'
        if tx_status:
            trans_rows_html += f'<div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">Transaction Status: <strong style="color:#059669;">{tx_status}</strong></div>'
        trans_rows_html += f'<div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">Statement Descriptor: <strong style="color:{TEXT_PRIMARY};">DOCTORS STUDIO {BUSINESS_PHONE}</strong></div>'
        trans_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Transaction Details</div>
            </td></tr>
            <tr><td style="padding:12px 16px;">
                {trans_rows_html}
            </td></tr>
        </table>
    </td></tr>'''

    # Subscription info — collect frequencies from all subscription items
    sub_info = ''
    if has_subscription:
        next_billing = _get_next_billing_date(order_data)
        sub_frequencies = []
        sub_item_billing = []  # (item_name, amount, freq) per item
        for item in line_items:
            if _is_subscription_item(item):
                sp, si = _get_subscription_frequency(item)
                if sp is None:
                    sp, si = _get_order_subscription_frequency(order_data)
                freq = _format_subscription_frequency(sp, si)
                item_name = item.get('name', 'Item')
                if freq:
                    sub_frequencies.append(f'<div style="font-size:11px;color:{TEXT_PRIMARY};margin-top:4px;">&#8226; <strong>{item_name}</strong>: {freq}</div>')
                    item_amount = float(item.get('total', '0')) or float(item.get('subtotal', '0'))
                    sub_item_billing.append((item_name, item_amount, freq))
        freq_rows = '\n'.join(sub_frequencies)
        if not freq_rows:
            freq_rows = f'<div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">Billing cycle details are managed in your account.</div>'
        # Next billing date
        if next_billing:
            freq_rows += f'\n<div style="font-size:11px;color:{TEXT_PRIMARY};margin-top:4px;">&#8226; <strong>Next Billing Date</strong>: {next_billing}</div>'
        # Subscription terms — one "You will be billed" line per item
        terms_html = ''
        for item_name, item_amount, freq_text in sub_item_billing:
            if item_amount > 0:
                terms_html += f'<div style="font-size:10px;color:{TEXT_PRIMARY};margin-top:8px;line-height:1.5;">You will be billed <strong>${item_amount:.2f}</strong> {freq_text.lower()}.</div>'
        terms_html += f'<div style="font-size:10px;color:{TEXT_SECONDARY};margin-top:4px;line-height:1.5;">You may cancel anytime by contacting support@doctorsstudio.com or calling {BUSINESS_PHONE}.</div>'
        sub_info = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #7c3aed;border-radius:6px;overflow:hidden;">
            <tr><td style="background:#f5f3ff;padding:12px 16px;">
                <div style="font-size:12px;font-weight:700;color:#7c3aed;margin-bottom:4px;">Subscription Information</div>
                {freq_rows}
                {terms_html}
            </td></tr>
        </table>
    </td></tr>'''

    # Customer note
    note_html = ''
    if customer_note:
        note_html = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:#fffbeb;padding:12px 16px;">
                <div style="font-size:12px;font-weight:700;color:#92400e;margin-bottom:4px;">Order Note</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};">{customer_note}</div>
            </td></tr>
        </table>
    </td></tr>'''

    # Shipping address section
    shipping_section = ''
    if shipping_address_html:
        shipping_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="padding:12px 16px;">
                <div style="font-size:12px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:6px;">Shipping Address</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};line-height:1.6;">{shipping_address_html}</div>
            </td></tr>
        </table>
    </td></tr>'''

    # Credit Bank section (email)
    credit_bank_html = ''
    cb_info = _get_credit_bank_info(order_data)
    if cb_info and cb_info['redeemed'] > 0:
        credit_bank_html = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #0891b2;border-radius:6px;overflow:hidden;">
            <tr><td style="background:#ecfeff;padding:10px 16px;border-bottom:1px solid #0891b2;">
                <div style="font-size:12px;font-weight:700;color:#0891b2;text-transform:uppercase;letter-spacing:0.5px;">Credit Bank</div>
            </td></tr>
            <tr><td style="padding:12px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_PRIMARY};">Starting Balance</td>
                        <td style="padding:4px 0;text-align:right;font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">${cb_info["starting"]:.2f}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_PRIMARY};">Amount Redeemed</td>
                        <td style="padding:4px 0;text-align:right;font-size:13px;color:#0891b2;font-weight:600;">-${cb_info["redeemed"]:.2f}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;font-size:13px;color:{TEXT_PRIMARY};border-top:1px solid #e5e7eb;">Remaining Balance</td>
                        <td style="padding:4px 0;text-align:right;font-size:13px;color:{TEXT_PRIMARY};font-weight:600;border-top:1px solid #e5e7eb;">${cb_info["remaining"]:.2f}</td>
                    </tr>
                </table>
            </td></tr>
        </table>
    </td></tr>'''

    title = 'Subscription Order Receipt' if has_subscription else 'Order Receipt'

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header bar -->
    <tr><td style="background:{BRAND_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td>
                    <div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">{title}</div>
                </td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">Order #{order_number}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.7);margin-top:2px;">{order_date}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Greeting -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:15px;color:{TEXT_PRIMARY};">Hi {customer_name or 'Valued Customer'},</div>
        <div style="font-size:13px;color:{TEXT_SECONDARY};margin-top:6px;">Thank you for your purchase! Here is your receipt.</div>
    </td></tr>

    <!-- Items table -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Order Items</div>
            </td></tr>
            <tr><td style="padding:4px 16px 12px;">
                {items_html}
            </td></tr>
        </table>
    </td></tr>

    <!-- Payment section -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Payment</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                {payment_section}
            </td></tr>
        </table>
    </td></tr>

    {trans_section}
    {shipping_section}
    {sub_info}
    {note_html}
    {credit_bank_html}

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your order?</div>
                <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">Call us at {BUSINESS_PHONE}</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">or email us at support@doctorsstudio.com</div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 32px;border-top:1px solid {BORDER_COLOR};text-align:center;">
        <div style="font-size:11px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:4px;">Refund Policy</div>
        <div style="font-size:10px;color:{TEXT_SECONDARY};">All sales are final. No refunds or exchanges.</div>
        <div style="font-size:10px;color:{TEXT_PRIMARY};margin-top:12px;font-weight:500;">Thank you for your business!</div>
    </td></tr>
    <tr><td style="background:#f9fafb;padding:12px 32px;text-align:center;">
        <div style="font-size:10px;color:{TEXT_MUTED};">
            {BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}
        </div>
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">
            {BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}
        </div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''
    return html


def _get_refunded_items_from_pos_db(order_id, refund_id=None, latest_only=True):
    """Fetch refunded items from POS database (POSOrderRefundItem).
    
    Args:
        order_id: WooCommerce order ID
        refund_id: If provided, return items for this specific POS refund only
        latest_only: If True and no refund_id, return items from the LATEST refund only
                     (not all refunds). This prevents showing items from prior refunds.
    
    Returns a list of dicts or None if no POS data found.
    """
    try:
        woo_order_id_str = str(order_id)
        pos_order = POSOrder.objects.filter(metadata__woo_order_id=woo_order_id_str).first()
        if not pos_order and woo_order_id_str.isdigit():
            pos_order = POSOrder.objects.filter(metadata__woo_order_id=int(woo_order_id_str)).first()
        if not pos_order:
            return None

        # Filter to specific refund, latest refund, or all refunds
        if refund_id:
            refund_items = POSOrderRefundItem.objects.filter(
                refund_id=refund_id
            ).select_related('order_item')
            logger.info(f"📋 POS DB: filtering to specific refund_id={refund_id}")
        elif latest_only:
            latest_refund = POSOrderRefund.objects.filter(order=pos_order).order_by('-created_at').first()
            if not latest_refund:
                return None
            refund_items = POSOrderRefundItem.objects.filter(
                refund=latest_refund
            ).select_related('order_item')
            logger.info(f"📋 POS DB: filtering to latest refund={latest_refund.id} for order {order_id}")
        else:
            refund_items = POSOrderRefundItem.objects.filter(
                refund__order=pos_order
            ).select_related('order_item')

        if not refund_items.exists():
            return None

        result = []
        for ri in refund_items:
            result.append({
                'name': ri.name,
                'quantity': ri.quantity,
                'total': str(float(ri.subtotal)),
                'sku': ri.order_item.code if ri.order_item else '',
                'product_id': int(ri.product_id) if ri.product_id and str(ri.product_id).isdigit() else None,
                'meta_data': [],
            })
        logger.info(f"📋 POS DB: found {len(result)} refunded items for WooCommerce order {order_id}: {[r['name'] for r in result]}")
        return result
    except Exception as e:
        logger.warning(f"Could not fetch refunded items from POS database for order {order_id}: {e}")
        return None


def _get_refunded_items_from_order(order_data):
    """Fetch detailed refund line items — POS database is the authoritative source.
    
    Returns a list of dicts: [{name, quantity, total, sku, product_id, meta_data}]
    representing only the items that were actually refunded.
    
    IMPORTANT: WooCommerce API is NOT used as a fallback when a POS order exists,
    because WooCommerce distributes amount-only refunds proportionally across ALL
    order items, making its line_items data unreliable for partial refunds.
    Falls back to order line_items only for legacy non-POS orders.
    """
    order_id = order_data.get('id')
    refunds = order_data.get('refunds', [])
    line_items = order_data.get('line_items', [])

    # If caller already injected _refunded_items (e.g. from views_refunds.py), use those
    if order_data.get('_refunded_items'):
        logger.info(f"📋 _get_refunded_items_from_order: using injected _refunded_items")
        return order_data['_refunded_items']

    # Try POS database — most reliable and authoritative source
    if order_id:
        pos_items = _get_refunded_items_from_pos_db(order_id, latest_only=False)
        if pos_items:
            logger.info(f"📋 _get_refunded_items_from_order: POS DB returned {len(pos_items)} items")
            return pos_items
        
        # Check if a POS order exists at all — if it does, DON'T fall back to WooCommerce API
        # because WC data is unreliable for amount-only refunds
        woo_order_id_str = str(order_id)
        pos_order = POSOrder.objects.filter(metadata__woo_order_id=woo_order_id_str).first()
        if not pos_order and woo_order_id_str.isdigit():
            pos_order = POSOrder.objects.filter(metadata__woo_order_id=int(woo_order_id_str)).first()
        if pos_order:
            # POS order exists but no refund items — return empty or line_items
            logger.info(f"📋 _get_refunded_items_from_order: POS order exists but no refund items in DB, returning line_items as fallback")
            return line_items

    if not order_id or not refunds:
        return line_items

    # === LEGACY FALLBACK: WooCommerce API (only for non-POS orders) ===
    logger.info(f"📋 _get_refunded_items_from_order: No POS order found, using WooCommerce API fallback for order {order_id}")
    original_items_map = {}
    for li in line_items:
        pid = li.get('product_id')
        if pid:
            original_items_map[pid] = li
        lid = li.get('id')
        if lid:
            original_items_map[f"lid_{lid}"] = li

    refunded_map = {}
    try:
        wc = WooCommerceAPI()
        for refund_summary in refunds:
            refund_id = refund_summary.get('id')
            if not refund_id:
                continue
            resp = wc.wcapi.get(f"orders/{order_id}/refunds/{refund_id}")
            if not resp.ok:
                continue
            refund_data = resp.json()
            for ri in refund_data.get('line_items', []):
                qty = abs(int(ri.get('quantity', 0)))
                total = abs(float(ri.get('total', '0')))
                if qty == 0 and total == 0:
                    continue
                pid = ri.get('product_id')
                name = ri.get('name', 'Unknown Item')
                key = str(pid) if pid else name

                if key in refunded_map:
                    refunded_map[key]['quantity'] += qty
                    refunded_map[key]['total'] += total
                else:
                    orig = original_items_map.get(pid, {})
                    sku = ri.get('sku', '') or orig.get('sku', '')
                    refunded_map[key] = {
                        'name': name,
                        'quantity': qty,
                        'total': total,
                        'sku': sku,
                        'product_id': pid,
                        'meta_data': ri.get('meta_data', orig.get('meta_data', [])),
                    }
    except Exception as e:
        logger.warning(f"Could not fetch detailed refund items for order {order_id}: {e}")
        return line_items

    if not refunded_map:
        return line_items

    result = []
    for item in refunded_map.values():
        result.append({
            'name': item['name'],
            'quantity': item['quantity'],
            'total': str(item['total']),
            'sku': item['sku'],
            'product_id': item['product_id'],
            'meta_data': item['meta_data'],
        })
    return result


def _build_refund_receipt_html(order_data):
    """Build a professional HTML refund receipt email (calm, not alarming)."""
    order_number = order_data.get('number', order_data.get('id', 'N/A'))
    order_date_raw = order_data.get('date_created', '')
    order_date = _format_date(order_date_raw)

    billing = order_data.get('billing', {})
    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
    customer_email = billing.get('email', '')

    # Refund destination is always Cash or Points
    refund_method_display = order_data.get('_refund_method_display', 'Cash')
    expected_refund_date = _get_expected_refund_date()

    refunds = order_data.get('refunds', [])
    total_refund_amount = sum(abs(float(r.get('total', 0))) for r in refunds)
    refunded_items_count = len(refunds)
    line_items = order_data.get('line_items', [])
    original_total = float(order_data.get('total', '0.00'))
    discount_total = float(order_data.get('discount_total', '0.00'))

    order_status = order_data.get('status', '')
    if order_status == 'refunded' and total_refund_amount == 0:
        total_refund_amount = original_total

    # If WooCommerce returned $0 for refund totals, look up POS database for accurate amounts
    if total_refund_amount == 0:
        woo_order_id = str(order_data.get('id', ''))
        pos_order = POSOrder.objects.filter(metadata__woo_order_id=woo_order_id).first()
        if not pos_order and woo_order_id.isdigit():
            pos_order = POSOrder.objects.filter(metadata__woo_order_id=int(woo_order_id)).first()
        if pos_order:
            pos_total = POSOrderRefund.objects.filter(order=pos_order).aggregate(
                total=Sum('refund_amount'))['total'] or 0
            if float(pos_total) > 0:
                total_refund_amount = float(pos_total)
                refunded_items_count = POSOrderRefund.objects.filter(order=pos_order).count()
                logger.info(f"Using POS refund data for email: ${total_refund_amount:.2f} from {refunded_items_count} refund(s)")

    # Determine full vs partial refund
    is_full_refund = order_status == 'refunded' or (original_total > 0 and abs(total_refund_amount - original_total) < 0.01)
    refund_heading = 'Order Refunded' if is_full_refund else 'Partial Refund'
    refund_subject_type = 'refunded' if is_full_refund else 'partially refunded'

    # Get the actual refunded items (not all order items)
    # Priority: 1) _refunded_items (injected by process_refund), 2) POS DB latest refund, 3) WooCommerce API, 4) all line_items
    logger.info(f"📋 [{REFUND_RECEIPT_VERSION}] _build_refund_receipt_html: _refunded_items={bool(order_data.get('_refunded_items'))}, is_full_refund={is_full_refund}, order_status={order_status}, total_refund={total_refund_amount}, original_total={original_total}")
    
    if order_data.get('_refunded_items'):
        display_items = order_data['_refunded_items']
        logger.info(f"📋 PATH 1: Using _refunded_items (injected): {[i.get('name') for i in display_items]}")
    elif is_full_refund:
        display_items = line_items
        logger.info(f"📋 PATH 2: Using ALL line_items (full refund): {[i.get('name') for i in display_items]}")
    else:
        display_items = _get_refunded_items_from_order(order_data)
        logger.info(f"📋 PATH 3: Using _get_refunded_items_from_order: {[i.get('name') for i in display_items]}")
    
    # Safety net: if _refunded_item_names was injected, filter display_items to ONLY those names
    refunded_names = order_data.get('_refunded_item_names')
    if refunded_names and not is_full_refund and len(display_items) > len(refunded_names):
        filtered = [i for i in display_items if i.get('name', '').lower().strip() in refunded_names]
        if filtered:
            logger.info(f"📋 SAFETY FILTER: {len(display_items)} -> {len(filtered)} items (names: {refunded_names})")
            display_items = filtered

    # For full refunds, scale per-item prices if they don't sum to refund total
    items_subtotal = sum(abs(float(i.get('total', '0'))) for i in display_items)
    price_scale = (total_refund_amount / items_subtotal) if (is_full_refund and items_subtotal > 0 and abs(items_subtotal - total_refund_amount) > 0.01) else 1.0

    items_html = ''
    brand_cache = {}
    for idx, item in enumerate(display_items):
        name = item.get('name', 'Unknown Item')
        _var_label = _get_item_variation_name(item)
        if _var_label and _var_label not in name:
            name = f'{name} - {_var_label}'
        qty = item.get('quantity', 1)
        sku = item.get('sku', '')
        brand = _get_item_brand(item, brand_cache)
        brand_prefix = f'<span style="font-weight:600;">{brand}</span> ' if brand else ''
        sku_line = f'<div style="font-size:10px;color:{TEXT_MUTED};margin-top:1px;">SKU: {sku}</div>' if sku else ''
        # Add subscription badge if applicable
        is_sub = _is_subscription_item(item)
        sub_badge = ''
        if is_sub:
            sp, si = _get_subscription_frequency(item)
            if sp is None:
                sp, si = _get_order_subscription_frequency(order_data)
            freq_text = _format_subscription_frequency(sp, si)
            freq_label = f' &mdash; {freq_text}' if freq_text else ''
            sub_badge = f'<br><span style="color:#7c3aed;font-size:10px;font-weight:600;">SUBSCRIPTION{freq_label}</span>'
        # Only the first item shows the refund total amount
        if idx == 0:
            items_html += f'''
            <tr style="border-bottom:1px solid {BORDER_COLOR};">
                <td style="padding:10px 0;">
                    <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:500;">{name}{sub_badge}</div>
                    <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:2px;">{brand_prefix}Qty: {qty}</div>
                    {sku_line}
                </td>
                <td style="padding:10px 0;text-align:right;font-size:13px;color:{REFUND_COLOR};font-weight:600;vertical-align:top;">
                    -${total_refund_amount:.2f}
                </td>
            </tr>'''
        else:
            items_html += f'''
            <tr style="border-bottom:1px solid {BORDER_COLOR};">
                <td colspan="2" style="padding:10px 0;">
                    <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:500;">{name}{sub_badge}</div>
                    <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:2px;">{brand_prefix}Qty: {qty}</div>
                    {sku_line}
                </td>
            </tr>'''

    # Refund reasons
    refund_reasons_html = ''
    for refund in refunds:
        reason = refund.get('reason', '')
        if reason:
            refund_reasons_html += f'''
            <tr><td colspan="2" style="padding:6px 0;font-size:11px;color:{TEXT_SECONDARY};font-style:italic;">
                Reason: {reason}
            </td></tr>'''

    now_str = datetime.now().strftime('%b %d, %Y at %I:%M %p')

    disc_color_refund = '#059669' if discount_total > 0 else TEXT_SECONDARY
    refund_discount_row_html = f'''
                    <tr style="border-top:1px solid #e5e7eb;">
                        <td style="padding:8px 0 0;font-size:12px;color:{disc_color_refund};">Original Discount</td>
                        <td style="padding:8px 0 0;text-align:right;font-size:12px;color:{disc_color_refund};">-${discount_total:.2f}</td>
                    </tr>''' if discount_total > 0 else ''

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header bar -->
    <tr><td style="background:{BRAND_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td>
                    <div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">{refund_heading}</div>
                </td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">Order #{order_number}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.7);margin-top:2px;">{order_date}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Greeting -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:15px;color:{TEXT_PRIMARY};">Hi {customer_name or 'Valued Customer'},</div>
        <div style="font-size:13px;color:{TEXT_SECONDARY};margin-top:6px;line-height:1.5;">
            {'We have processed a full refund for your order' if is_full_refund else 'We have processed a partial refund for your order'} #{order_number}. This is <strong>not a new charge</strong> —
            the amount below will be returned to your original payment method.
        </div>
    </td></tr>

    <!-- Order reference info -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{REFUND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{REFUND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Refund Details</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Order Number</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">#{order_number}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Original Purchase</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">{order_date}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Refund Processed</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">{now_str}</td>
                    </tr>
                    {'<tr><td style="padding:4px 0;color:' + TEXT_SECONDARY + ';">Customer</td><td style="padding:4px 0;text-align:right;color:' + TEXT_PRIMARY + ';">' + customer_name + '</td></tr>' if customer_name else ''}
                    {'<tr><td style="padding:4px 0;color:' + TEXT_SECONDARY + ';">Email</td><td style="padding:4px 0;text-align:right;color:' + TEXT_PRIMARY + ';">' + customer_email + '</td></tr>' if customer_email else ''}
                </table>
            </td></tr>
        </table>
    </td></tr>

    <!-- Refunded items -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{REFUND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{REFUND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Refunded Items</div>
            </td></tr>
            <tr><td style="padding:4px 16px 8px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    {items_html}
                    {refund_reasons_html}
                </table>
            </td></tr>
            <tr><td style="padding:12px 16px;border-top:2px solid {REFUND_COLOR};background:{REFUND_LIGHT};">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                        <td style="font-size:15px;font-weight:700;color:{REFUND_COLOR};">Refund Total</td>
                        <td style="text-align:right;font-size:18px;font-weight:700;color:{REFUND_COLOR};">-${total_refund_amount:.2f}</td>
                    </tr>{refund_discount_row_html}
                </table>
            </td></tr>
        </table>
    </td></tr>

    <!-- Refund destination -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:#f0fdf4;padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:#166534;text-transform:uppercase;letter-spacing:0.5px;">Refund Destination</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Refunded to</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{refund_method_display}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Expected by</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{expected_refund_date}</td>
                    </tr>
                </table>
            </td></tr>
        </table>
    </td></tr>

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your refund?</div>
                <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">Call us at {BUSINESS_PHONE}</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">or email us at support@doctorsstudio.com</div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 32px;border-top:1px solid {BORDER_COLOR};text-align:center;">
        <div style="font-size:10px;color:{TEXT_SECONDARY};line-height:1.6;">
            Please allow 3–5 business days for the refund to appear on your statement.<br>
            Thank you for your business!
        </div>
    </td></tr>
    <tr><td style="background:#f9fafb;padding:12px 32px;text-align:center;">
        <div style="font-size:10px;color:{TEXT_MUTED};">
            {BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}
        </div>
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">
            {BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}
        </div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''
    return html, refunded_items_count, total_refund_amount


def _build_pdf_refund_receipt_html(order_data):
    """Build an xhtml2pdf-compatible HTML refund receipt that mirrors the email design.
    Uses only inline styles and table-based layout for PDF rendering compatibility."""
    order_number = order_data.get('number', order_data.get('id', 'N/A'))
    order_date = _format_date(order_data.get('date_created', ''))

    billing = order_data.get('billing', {})
    customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
    customer_email = billing.get('email', '')

    # Refund destination is always Cash or Points
    refund_method_display = order_data.get('_refund_method_display', 'Cash')
    expected_refund_date = _get_expected_refund_date()

    refunds = order_data.get('refunds', [])
    total_refund_amount = sum(abs(float(r.get('total', 0))) for r in refunds)
    refunded_items_count = len(refunds)
    line_items = order_data.get('line_items', [])
    original_total = float(order_data.get('total', '0.00'))
    discount_total = float(order_data.get('discount_total', '0.00'))

    order_status = order_data.get('status', '')
    if order_status == 'refunded' and total_refund_amount == 0:
        total_refund_amount = original_total

    # If WooCommerce returned $0 for refund totals, look up POS database for accurate amounts
    if total_refund_amount == 0:
        woo_order_id = str(order_data.get('id', ''))
        pos_order = POSOrder.objects.filter(metadata__woo_order_id=woo_order_id).first()
        if not pos_order and woo_order_id.isdigit():
            pos_order = POSOrder.objects.filter(metadata__woo_order_id=int(woo_order_id)).first()
        if pos_order:
            pos_total = POSOrderRefund.objects.filter(order=pos_order).aggregate(
                total=Sum('refund_amount'))['total'] or 0
            if float(pos_total) > 0:
                total_refund_amount = float(pos_total)
                refunded_items_count = POSOrderRefund.objects.filter(order=pos_order).count()

    # Determine full vs partial refund
    is_full_refund = order_status == 'refunded' or (original_total > 0 and abs(total_refund_amount - original_total) < 0.01)
    refund_heading = 'Order Refunded' if is_full_refund else 'Partial Refund'

    # Get the actual refunded items (not all order items)
    # Priority: 1) _refunded_items (injected by process_refund), 2) POS DB latest refund, 3) WooCommerce API, 4) all line_items
    logger.info(f"📋 [{REFUND_RECEIPT_VERSION}] _build_pdf: _refunded_items={bool(order_data.get('_refunded_items'))}, is_full_refund={is_full_refund}")
    
    if order_data.get('_refunded_items'):
        display_items = order_data['_refunded_items']
        logger.info(f"📋 PDF PATH 1: Using _refunded_items (injected): {[i.get('name') for i in display_items]}")
    elif is_full_refund:
        display_items = line_items
        logger.info(f"📋 PDF PATH 2: Using ALL line_items (full refund)")
    else:
        display_items = _get_refunded_items_from_order(order_data)
        logger.info(f"📋 PDF PATH 3: Using _get_refunded_items_from_order: {[i.get('name') for i in display_items]}")
    
    # Safety net: if _refunded_item_names was injected, filter display_items to ONLY those names
    refunded_names = order_data.get('_refunded_item_names')
    if refunded_names and not is_full_refund and len(display_items) > len(refunded_names):
        filtered = [i for i in display_items if i.get('name', '').lower().strip() in refunded_names]
        if filtered:
            logger.info(f"📋 PDF SAFETY FILTER: {len(display_items)} -> {len(filtered)} items")
            display_items = filtered

    # For full refunds, scale per-item prices if they don't sum to refund total
    items_subtotal = sum(abs(float(i.get('total', '0'))) for i in display_items)
    price_scale = (total_refund_amount / items_subtotal) if (is_full_refund and items_subtotal > 0 and abs(items_subtotal - total_refund_amount) > 0.01) else 1.0

    items_rows = ''
    brand_cache = {}
    for idx, item in enumerate(display_items):
        name = item.get('name', 'Unknown Item')
        # Strip Unicode box-drawing chars that xhtml2pdf can't render
        name = re.sub(r'[\u2500-\u257F]', '', name).strip() or 'Item'
        _var_label_pdf = _get_item_variation_name(item)
        if _var_label_pdf and _var_label_pdf not in name:
            name = f'{name} - {_var_label_pdf}'
        qty = item.get('quantity', 1)
        sku = item.get('sku', '')
        brand = _get_item_brand(item, brand_cache)
        brand_prefix = f'<span style="font-weight:bold;">{brand}</span> ' if brand else ''
        sku_line = f'<br/><span style="font-size:8px;color:#6b7280;">SKU: {sku}</span>' if sku else ''
        is_sub = _is_subscription_item(item)
        sub_badge = ''
        if is_sub:
            sp, si = _get_subscription_frequency(item)
            if sp is None:
                sp, si = _get_order_subscription_frequency(order_data)
            freq_text = _format_subscription_frequency(sp, si)
            freq_label = f' — {freq_text}' if freq_text else ''
            sub_badge = f'<br/><span style="color:#7c3aed;font-size:9px;font-weight:bold;">SUBSCRIPTION{freq_label}</span>'
        # Only the first item shows the refund total amount
        if idx == 0:
            items_rows += f'''
        <tr>
            <td style="padding:8px 8px 8px 0;border-bottom:1px solid #e5e7eb;">
                <span style="font-size:11px;font-weight:bold;color:#1f2937;">{name}</span>{sub_badge}
                <br/><span style="font-size:9px;color:#6b7280;">{brand_prefix}Qty: {qty}</span>{sku_line}
            </td>
            <td style="padding:8px 0;border-bottom:1px solid #e5e7eb;text-align:right;vertical-align:top;">
                <span style="font-size:11px;font-weight:bold;color:#92400e;">-${total_refund_amount:.2f}</span>
            </td>
        </tr>'''
        else:
            items_rows += f'''
        <tr>
            <td colspan="2" style="padding:8px 8px 8px 0;border-bottom:1px solid #e5e7eb;">
                <span style="font-size:11px;font-weight:bold;color:#1f2937;">{name}</span>{sub_badge}
                <br/><span style="font-size:9px;color:#6b7280;">{brand_prefix}Qty: {qty}</span>{sku_line}
            </td>
        </tr>'''

    # Refund reasons
    refund_reasons_rows = ''
    for refund in refunds:
        reason = refund.get('reason', '')
        if reason:
            refund_reasons_rows += f'''
        <tr>
            <td colspan="2" style="padding:4px 0;font-size:9px;color:#6b7280;font-style:italic;">
                Reason: {reason}
            </td>
        </tr>'''

    now_str = datetime.now().strftime('%b %d, %Y at %I:%M %p')

    # Customer info rows
    customer_row = ''
    if customer_name:
        customer_row = f'''
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Customer</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">{customer_name}</td>
                    </tr>'''
    email_row = ''
    if customer_email:
        email_row = f'''
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Email</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;">{customer_email}</td>
                    </tr>'''

    pdf_disc_color = '#059669' if discount_total > 0 else '#6b7280'
    pdf_refund_discount_row_html = f'''
                <tr>
                    <td style="font-size:10px;color:{pdf_disc_color};padding-top:4px;border-top:1px solid #e5e7eb;">Original Discount</td>
                    <td style="text-align:right;font-size:10px;color:{pdf_disc_color};padding-top:4px;border-top:1px solid #e5e7eb;">-${discount_total:.2f}</td>
                </tr>'''

    html = f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/>
<style>
    @page {{ size: letter; margin: 1cm; }}
    body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #1f2937; margin: 0; padding: 0; }}
    table {{ border-collapse: collapse; }}
</style>
</head>
<body>
    <!-- Logo -->
    <table width="100%" style="margin-bottom:0;background:#ffffff;">
        <tr><td style="padding:10px 20px;text-align:center;background:#ffffff;">
            <img src="file:///{PDF_LOGO_PATH.replace(chr(92), '/')}" width="200" style="background:#ffffff;" />
        </td></tr>
    </table>

    <!-- Header bar -->
    <table width="100%" style="background:#183249;margin-bottom:10px;">
        <tr>
            <td style="padding:12px 20px;">
                <span style="font-size:10px;color:rgba(255,255,255,0.85);">{refund_heading}</span>
            </td>
            <td style="padding:18px 20px;text-align:right;">
                <span style="font-size:10px;color:rgba(255,255,255,0.85);">Order #{order_number}</span><br/>
                <span style="font-size:9px;color:rgba(255,255,255,0.7);">{order_date}</span>
            </td>
        </tr>
    </table>

    <!-- Greeting -->
    <p style="font-size:12px;color:#1f2937;margin:0 0 4px 0;">Hi <strong>{customer_name or 'Valued Customer'}</strong>,</p>
    <p style="font-size:10px;color:#6b7280;margin:0 0 14px 0;line-height:1.5;">
        {'We have processed a full refund for your order' if is_full_refund else 'We have processed a partial refund for your order'} #{order_number}. This is <strong>not a new charge</strong> —
        the amount below will be returned to your original payment method.
    </p>

    <!-- Refund Details -->
    <table width="100%" style="border:1px solid #e5e7eb;margin-bottom:14px;">
        <tr><td colspan="2" style="background:#fffbeb;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#92400e;text-transform:uppercase;letter-spacing:1px;">Refund Details</span>
        </td></tr>
        <tr><td colspan="2" style="padding:10px 12px;">
            <table width="100%">
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Order Number</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">#{order_number}</td>
                    </tr>
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Original Purchase</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;">{order_date}</td>
                    </tr>
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Refund Processed</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;">{now_str}</td>
                    </tr>
                    {customer_row}
                    {email_row}
            </table>
        </td></tr>
    </table>

    <!-- Refunded Items -->
    <table width="100%" style="border:1px solid #e5e7eb;margin-bottom:14px;">
        <tr><td colspan="2" style="background:#fffbeb;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#92400e;text-transform:uppercase;letter-spacing:1px;">Refunded Items</span>
        </td></tr>
        <tr><td colspan="2" style="padding:4px 12px 6px;">
            <table width="100%">
                {items_rows}
                {refund_reasons_rows}
            </table>
        </td></tr>
        <tr><td colspan="2" style="padding:10px 12px;border-top:2px solid #92400e;background:#fffbeb;">
            <table width="100%">
                <tr>
                    <td style="font-size:13px;font-weight:bold;color:#92400e;">Refund Total</td>
                    <td style="text-align:right;font-size:15px;font-weight:bold;color:#92400e;">-${total_refund_amount:.2f}</td>
                </tr>
                {pdf_refund_discount_row_html}
            </table>
        </td></tr>
    </table>

    <!-- Refund Destination -->
    <table width="100%" style="border:1px solid #e5e7eb;margin-bottom:14px;">
        <tr><td style="background:#f0fdf4;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#166534;text-transform:uppercase;letter-spacing:1px;">Refund Destination</span>
        </td></tr>
        <tr><td style="padding:10px 12px;">
            <table width="100%">
                <tr>
                    <td style="padding:3px 0;font-size:10px;color:#6b7280;">Refunded to</td>
                    <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">{refund_method_display}</td>
                </tr>
                <tr>
                    <td style="padding:3px 0;font-size:10px;color:#6b7280;">Expected by</td>
                    <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">{expected_refund_date}</td>
                </tr>
            </table>
        </td></tr>
    </table>

    <!-- Help -->
    <table width="100%" style="background:#f9fafb;margin-bottom:14px;">
        <tr><td style="padding:10px 12px;text-align:center;">
            <span style="font-size:10px;color:#6b7280;">Questions about your refund?</span><br/>
            <span style="font-size:11px;color:#1f2937;font-weight:bold;">Call us at {BUSINESS_PHONE}</span><br/>
            <span style="font-size:10px;color:#6b7280;">or email us at support@doctorsstudio.com</span>
        </td></tr>
    </table>

    <!-- Footer -->
    <table width="100%" style="border-top:1px solid #e5e7eb;">
        <tr><td style="padding:10px 0;text-align:center;">
            <span style="font-size:9px;color:#6b7280;">Please allow 3-5 business days for the refund to appear on your statement.</span><br/>
            <span style="font-size:9px;color:#1f2937;font-weight:bold;">Thank you for your business!</span>
        </td></tr>
        <tr><td style="background:#f9fafb;padding:8px 0;text-align:center;">
            <span style="font-size:8px;color:#9ca3af;">{BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}</span><br/>
            <span style="font-size:8px;color:#9ca3af;">{BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}</span>
        </td></tr>
    </table>
</body>
</html>'''
    return html


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_receipt(request, order_id):
    """Send an order receipt email to the customer."""
    email = request.data.get('email')
    if not email:
        return Response({'error': 'Email address is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Dedup: block duplicate receipt emails (5 min window)
    from django.core.cache import cache
    dedup_key = f"receipt_sent:{order_id}"
    if cache.get(dedup_key):
        logger.info(f"📧 Dedup: receipt already sent for order {order_id}, skipping")
        return Response({'status': 'ok', 'dedup': True}, status=status.HTTP_200_OK)
    cache.set(dedup_key, True, 300)

    try:
        wc = WooCommerceAPI()
        response = wc.wcapi.get(f'orders/{order_id}')

        if response.status_code != 200:
            logger.error(f'Failed to fetch WooCommerce order {order_id}: {response.status_code}')
            return Response(
                {'error': f'Could not fetch order {order_id} from WooCommerce'},
                status=status.HTTP_404_NOT_FOUND
            )

        order_data = response.json()
        order_number = order_data.get('number', order_id)
        _enrich_order_subscription_frequency(order_data)
        html_body = _build_order_receipt_html(order_data)

        from .email_sender import send_pos_email

        subject = f'Your Doctors Studio Receipt — Order #{order_number}'
        text_content = f'Your Doctors Studio Receipt — Order #{order_number}'

        pdf_html = _build_pdf_receipt_html(order_data, is_refund=False)
        pdf_bytes = _html_to_pdf(pdf_html)
        attachments = []
        if pdf_bytes:
            attachments.append((f'Receipt_Order_{order_number}.pdf', pdf_bytes, 'application/pdf'))
        else:
            logger.warning(f'PDF generation failed for order #{order_number}, sending email without attachment')

        sent = send_pos_email(
            subject=subject,
            text_content=text_content,
            html_content=html_body,
            to_email=email,
            email_type='order_receipt',
            attachments=attachments or None,
            related_order=str(order_number),
        )
        if not sent:
            return Response({'error': 'Failed to send receipt email'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'status': 'success', 'message': f'Receipt sent to {email}'})

    except Exception as e:
        logger.error(f'Error sending receipt for order {order_id}: {e}')
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_refund_receipt(request, order_id):
    """Send a refund receipt email to the customer."""
    email = request.data.get('email')
    if not email:
        return Response({'error': 'Email address is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        wc = WooCommerceAPI()
        response = wc.wcapi.get(f'orders/{order_id}')

        if response.status_code != 200:
            logger.error(f'Failed to fetch WooCommerce order {order_id}: {response.status_code}')
            return Response(
                {'error': f'Could not fetch order {order_id} from WooCommerce'},
                status=status.HTTP_404_NOT_FOUND
            )

        order_data = response.json()
        order_number = order_data.get('number', order_id)
        _enrich_order_subscription_frequency(order_data)

        # Check POS refund records for actual refund method (points vs gateway)
        # AND inject _refunded_items from POS database so receipt only shows actually refunded items
        woo_order_id = str(order_data.get('id', ''))
        pos_order = POSOrder.objects.filter(metadata__woo_order_id=woo_order_id).first()
        if not pos_order and woo_order_id.isdigit():
            pos_order = POSOrder.objects.filter(metadata__woo_order_id=int(woo_order_id)).first()
        if pos_order:
            latest_refund = POSOrderRefund.objects.filter(order=pos_order).order_by('-created_at').first()
            if latest_refund and latest_refund.metadata and latest_refund.metadata.get('refund_as_points'):
                order_data['_refund_method_display'] = 'Points'
            elif pos_order.total is not None and float(pos_order.total) == 0 and pos_order.items.exists() and all(
                (item.metadata if isinstance(item.metadata, dict) else {}).get('isCreditedService', False) for item in pos_order.items.all()
            ):
                order_data['_refund_method_display'] = 'Credited Services'
            else:
                # Detect payment method from POS order metadata or WooCommerce meta
                from .views_refunds import _get_refund_method_display
                pm_info = None
                # Try POS order metadata first
                if pos_order.metadata and pos_order.metadata.get('pos_payment_method'):
                    raw = pos_order.metadata['pos_payment_method']
                    if isinstance(raw, str):
                        try:
                            pm_info = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            pass
                    elif isinstance(raw, dict):
                        pm_info = raw
                # Fallback: try WooCommerce order meta_data
                if not pm_info:
                    for meta in order_data.get('meta_data', []):
                        if meta.get('key') == '_pos_payment_method':
                            raw = meta.get('value', '')
                            if isinstance(raw, str):
                                try:
                                    pm_info = json.loads(raw)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                            elif isinstance(raw, dict):
                                pm_info = raw
                            break
                order_data['_refund_method_display'] = _get_refund_method_display(pm_info) if pm_info else 'Cash'

            # Inject actual refunded items from POS database — LATEST refund only
            # This ensures the receipt only shows items from the most recent refund,
            # not items from ALL prior refunds combined
            if latest_refund:
                latest_refund_items = POSOrderRefundItem.objects.filter(
                    refund=latest_refund
                ).select_related('order_item')
                if latest_refund_items.exists():
                    order_data['_refunded_items'] = [
                        {
                            'name': ri.name,
                            'quantity': ri.quantity,
                            'total': str(float(ri.subtotal)),
                            'sku': ri.order_item.code if ri.order_item else '',
                            'product_id': int(ri.product_id) if ri.product_id and str(ri.product_id).isdigit() else None,
                            'meta_data': [],
                        }
                        for ri in latest_refund_items
                    ]
                    order_data['_refunded_item_names'] = [ri.name.lower().strip() for ri in latest_refund_items]
                    logger.info(f"📋 send_refund_receipt: Injected {latest_refund_items.count()} items from LATEST refund for receipt: {order_data['_refunded_item_names']}")

        html_body, refunded_items_count, total_refund_amount = _build_refund_receipt_html(order_data)

        from .email_sender import send_pos_email

        subject = f'Your Doctors Studio Refund Receipt — Order #{order_number}'
        text_content = f'Refund receipt for Order #{order_number}'

        pdf_html = _build_pdf_refund_receipt_html(order_data)
        pdf_bytes = _html_to_pdf(pdf_html)
        attachments = []
        if pdf_bytes:
            attachments.append((f'Refund_Receipt_Order_{order_number}.pdf', pdf_bytes, 'application/pdf'))
        else:
            logger.warning(f'PDF generation failed for refund receipt order #{order_number}, sending email without attachment')

        sent = send_pos_email(
            subject=subject,
            text_content=text_content,
            html_content=html_body,
            to_email=email,
            email_type='refund_receipt',
            attachments=attachments or None,
            related_order=str(order_number),
        )
        if not sent:
            return Response({'error': 'Failed to send refund receipt email'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'status': 'success',
            'message': f'Refund receipt sent to {email}',
            'refunded_items_count': refunded_items_count,
            'total_refund_amount': total_refund_amount,
        })

    except Exception as e:
        logger.error(f'Error sending refund receipt for order {order_id}: {e}')
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==================== REUSABLE EMAIL SENDER ====================

def _send_email_via_mailgun_or_smtp(subject, text_content, html_content, to_email, from_email=None, attachments=None):
    """
    Backward-compatible wrapper — delegates to the unified send_pos_email().
    Returns True on success, False on failure. Never raises.
    """
    from .email_sender import send_pos_email
    return send_pos_email(
        subject=subject,
        text_content=text_content,
        html_content=html_content,
        to_email=to_email,
        email_type='other',
        from_email=from_email,
        attachments=attachments,
    )


# ==================== CANCELLATION EMAIL ====================

def _build_cancellation_email_html(customer_name, plan_name, subscription_id, cancellation_date, reason='',
                                    status='cancelled', subscription_total='', billing_period='',
                                    last_order_date='', end_date='', line_items=None, billing=None,
                                    parent_order_id=None, discount_total=0):
    """
    Build a professional HTML membership cancellation confirmation email.
    Mirrors the WooCommerce Cancelled Subscription email template with DS branding.

    Parameters:
        status: 'cancelled', 'pending-cancel', or 'expired'
        subscription_total: formatted price string e.g. "$99.00 / month"
        billing_period: e.g. "month", "year"
        last_order_date: formatted date string
        end_date: end of prepaid term date string
        line_items: list of dicts with 'name', 'quantity', 'total' (subscription products)
        billing: dict with 'first_name', 'last_name', 'address_1', 'city', 'state', 'postcode', 'email', 'phone'
        parent_order_id: WooCommerce parent order number
    """

    is_pending = status in ('pending-cancel', 'pending_cancel', 'wc-pending-cancel')

    # --- Intro paragraph (WC template style) ---
    if is_pending:
        header_title = 'Membership Pending Cancellation'
        intro_text = (
            f'A subscription belonging to <strong>{customer_name or "a customer"}</strong> '
            f'is now pending cancellation, and will end on <strong>{end_date or "the end of the prepaid term"}</strong>. '
            f'Their subscription\'s details are as follows:'
        )
        status_label = 'Pending Cancellation'
        status_color = '#d97706'
    elif status in ('expired', 'wc-expired'):
        header_title = 'Membership Expired'
        intro_text = (
            f'A subscription belonging to <strong>{customer_name or "a customer"}</strong> '
            f'has expired. Their subscription\'s details are as follows:'
        )
        status_label = 'Expired'
        status_color = '#6b7280'
    else:
        header_title = 'Membership Cancelled'
        intro_text = (
            f'A subscription belonging to <strong>{customer_name or "a customer"}</strong> '
            f'has been cancelled. Their subscription\'s details are as follows:'
        )
        status_label = 'Cancelled'
        status_color = '#dc2626'

    # --- Subscription summary table (4 columns like WC) ---
    sub_total_display = subscription_total or 'N/A'
    last_order_display = last_order_date or '—'
    end_date_display = end_date or '—'

    # --- Order items rows ---
    items_html = ''
    if line_items and isinstance(line_items, list):
        for item in line_items:
            item_name = item.get('name', 'Item') if isinstance(item, dict) else str(item)
            item_qty = item.get('quantity', 1) if isinstance(item, dict) else 1
            item_total = ''
            if isinstance(item, dict):
                try:
                    item_total = f"${float(item.get('total', 0)):.2f}"
                except (ValueError, TypeError):
                    item_total = item.get('total', '')
            items_html += f'''
                <tr style="border-bottom:1px solid {BORDER_COLOR};">
                    <td style="padding:8px 12px;font-size:12px;color:{TEXT_PRIMARY};">{item_name}</td>
                    <td style="padding:8px 12px;font-size:12px;color:{TEXT_PRIMARY};text-align:center;">{item_qty}</td>
                    <td style="padding:8px 12px;font-size:12px;color:{TEXT_PRIMARY};text-align:right;">{item_total}</td>
                </tr>'''

    order_items_section = ''
    if items_html:
        order_items_section = f'''
    <!-- Order Items -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Order Items</div>
            </td></tr>
            <tr><td style="padding:0;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    <tr style="background:#f9fafb;border-bottom:1px solid {BORDER_COLOR};">
                        <td style="padding:8px 12px;font-size:11px;font-weight:700;color:{TEXT_SECONDARY};text-transform:uppercase;">Product</td>
                        <td style="padding:8px 12px;font-size:11px;font-weight:700;color:{TEXT_SECONDARY};text-transform:uppercase;text-align:center;">Qty</td>
                        <td style="padding:8px 12px;font-size:11px;font-weight:700;color:{TEXT_SECONDARY};text-transform:uppercase;text-align:right;">Price</td>
                    </tr>
                    {items_html}
                </table>
            </td></tr>
        </table>
    </td></tr>'''

    # --- Customer details section ---
    customer_details_section = ''
    if billing and isinstance(billing, dict):
        b_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
        b_addr1 = billing.get('address_1', '')
        b_addr2 = billing.get('address_2', '')
        b_city = billing.get('city', '')
        b_state = billing.get('state', '')
        b_postcode = billing.get('postcode', '')
        b_email = billing.get('email', '')
        b_phone = billing.get('phone', '')

        addr_parts = [b_addr1]
        if b_addr2:
            addr_parts.append(b_addr2)
        city_line = ', '.join(filter(None, [b_city, b_state])) + (f' {b_postcode}' if b_postcode else '')
        if city_line.strip():
            addr_parts.append(city_line.strip())
        address_html = '<br>'.join(filter(None, addr_parts))

        detail_rows = ''
        if b_name:
            detail_rows += f'<div style="font-size:12px;color:{TEXT_PRIMARY};font-weight:600;">{b_name}</div>'
        if address_html:
            detail_rows += f'<div style="font-size:12px;color:{TEXT_SECONDARY};margin-top:4px;line-height:1.5;">{address_html}</div>'
        if b_email:
            detail_rows += f'<div style="font-size:12px;color:{TEXT_SECONDARY};margin-top:4px;">{b_email}</div>'
        if b_phone:
            detail_rows += f'<div style="font-size:12px;color:{TEXT_SECONDARY};margin-top:2px;">{b_phone}</div>'

        if detail_rows:
            customer_details_section = f'''
    <!-- Customer Details -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Customer Details</div>
            </td></tr>
            <tr><td style="padding:12px 16px;">
                {detail_rows}
            </td></tr>
        </table>
    </td></tr>'''

    # --- Parent order reference ---
    parent_order_html = ''
    if parent_order_id:
        parent_order_html = f'''
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Parent Order</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">#{parent_order_id}</td>
                    </tr>'''

    # --- Reason row ---
    reason_row = ''
    if reason:
        reason_row = f'''
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Reason</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">{reason}</td>
                    </tr>'''

    disc_color_cancellation = '#059669' if discount_total > 0 else TEXT_SECONDARY
    cancellation_discount_row_html = f'''
            <tr style="border-top:1px solid {BORDER_COLOR};">
                <td colspan="2" style="padding:8px 12px;font-size:11px;color:{disc_color_cancellation};">Original Discount Applied</td>
                <td colspan="2" style="padding:8px 12px;text-align:right;font-size:11px;color:{disc_color_cancellation};">-${discount_total:.2f}</td>
            </tr>'''

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header bar -->
    <tr><td style="background:{BRAND_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td>
                    <div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">{header_title}</div>
                </td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">Subscription #{subscription_id or 'N/A'}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Intro -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:13px;color:{TEXT_PRIMARY};line-height:1.6;">
            {intro_text}
        </div>
    </td></tr>

    <!-- Subscription Summary Table (WC style: 4 columns) -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr style="background:{BRAND_LIGHT};">
                <td style="padding:10px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid {BORDER_COLOR};">Subscription</td>
                <td style="padding:10px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid {BORDER_COLOR};">Price</td>
                <td style="padding:10px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid {BORDER_COLOR};">Last Order Date</td>
                <td style="padding:10px 12px;font-size:11px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;border-bottom:1px solid {BORDER_COLOR};">End of Prepaid Term</td>
            </tr>
            <tr>
                <td style="padding:10px 12px;font-size:12px;color:{BRAND_COLOR};font-weight:600;">#{subscription_id or 'N/A'}</td>
                <td style="padding:10px 12px;font-size:12px;color:{TEXT_PRIMARY};">{sub_total_display}</td>
                <td style="padding:10px 12px;font-size:12px;color:{TEXT_PRIMARY};">{last_order_display}</td>
                <td style="padding:10px 12px;font-size:12px;color:{TEXT_PRIMARY};">{end_date_display}</td>
            </tr>
            {cancellation_discount_row_html}
        </table>
    </td></tr>

    {order_items_section}

    <!-- Membership Details -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Membership Details</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Plan</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{plan_name or 'Membership'}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Status</td>
                        <td style="padding:4px 0;text-align:right;color:{status_color};font-weight:600;">{status_label}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Cancellation Date</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{cancellation_date}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Subscription ID</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">#{subscription_id or 'N/A'}</td>
                    </tr>{parent_order_html}{reason_row}
                </table>
            </td></tr>
        </table>
    </td></tr>

    {customer_details_section}

    <!-- What This Means -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:#f9fafb;padding:12px 16px;">
                <div style="font-size:12px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:6px;">What This Means</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};line-height:1.6;">
                    &bull; Your billing has been stopped — no further charges will occur.<br>
                    &bull; Any remaining prepaid benefits may still be available until the end of your current term.<br>
                    &bull; To reactivate your membership, please contact us or visit our office.
                </div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your membership?</div>
                <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">Call us at {BUSINESS_PHONE}</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">or email us at support@doctorsstudio.com</div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 32px;border-top:1px solid {BORDER_COLOR};text-align:center;">
        <div style="font-size:10px;color:{TEXT_PRIMARY};font-weight:500;">Thank you for being a valued member!</div>
    </td></tr>
    <tr><td style="background:#f9fafb;padding:12px 32px;text-align:center;">
        <div style="font-size:10px;color:{TEXT_MUTED};">
            {BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}
        </div>
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">
            {BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}
        </div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''
    return html


def send_cancellation_email(customer_email, customer_name, plan_name, subscription_id, cancellation_date, reason='',
                            status='cancelled', subscription_total='', billing_period='',
                            last_order_date='', end_date='', line_items=None, billing=None,
                            parent_order_id=None, discount_total=0):
    """
    Send a membership cancellation confirmation email.
    Returns True on success, False on failure. Never raises.
    """
    try:
        html = _build_cancellation_email_html(
            customer_name, plan_name, subscription_id, cancellation_date, reason,
            status=status, subscription_total=subscription_total, billing_period=billing_period,
            last_order_date=last_order_date, end_date=end_date, line_items=line_items,
            billing=billing, parent_order_id=parent_order_id, discount_total=discount_total
        )
        is_pending = status in ('pending-cancel', 'pending_cancel', 'wc-pending-cancel')
        if is_pending:
            subject = f'Membership Pending Cancellation — Subscription #{subscription_id or "N/A"}'
            text = f'Your membership is pending cancellation and will end on {end_date or cancellation_date}.'
        elif status in ('expired', 'wc-expired'):
            subject = f'Membership Expired — Subscription #{subscription_id or "N/A"}'
            text = f'Your membership has expired as of {cancellation_date}.'
        else:
            subject = f'Membership Cancellation Confirmation — Subscription #{subscription_id or "N/A"}'
            text = f'Your membership has been cancelled as of {cancellation_date}.'
        from .email_sender import send_pos_email
        return send_pos_email(
            subject=subject,
            text_content=text,
            html_content=html,
            to_email=customer_email,
            email_type='cancellation',
            related_order=str(subscription_id or ''),
        )
    except Exception as e:
        logger.error(f"📧 Error sending cancellation email to {customer_email}: {e}")
        return False


# ==================== TRACKING EMAIL ====================

def _build_tracking_email_html(customer_name, order_number, tracking_provider, tracking_number,
                                tracking_link, date_shipped, items_html, shipping_address_html, 
                                order_total=0, discount_total=0, subtotal=0):
    """Build a professional HTML shipment tracking notification email."""

    track_button = ''
    if tracking_link:
        track_button = f'''
                <div style="margin-top:12px;text-align:center;">
                    <a href="{tracking_link}" style="display:inline-block;padding:10px 24px;background:{BRAND_COLOR};color:#fff;text-decoration:none;border-radius:4px;font-size:13px;font-weight:600;">Track Your Package &rarr;</a>
                </div>'''

    items_section = ''
    if items_html:
        items_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Items Shipped</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                {items_html}
            </td></tr>
        </table>
    </td></tr>'''

    shipping_section = ''
    if shipping_address_html:
        shipping_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="padding:12px 16px;">
                <div style="font-size:12px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:6px;">Shipping Address</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};line-height:1.6;">{shipping_address_html}</div>
            </td></tr>
        </table>
    </td></tr>'''

    disc_color_tracking = '#059669' if discount_total > 0 else TEXT_SECONDARY
    tracking_discount_row_html = f'''
                    <tr>
                        <td style="padding:4px 0;color:{disc_color_tracking};">Discount</td>
                        <td style="padding:4px 0;text-align:right;color:{disc_color_tracking};">-${discount_total:.2f}</td>
                    </tr>'''
    tracking_order_totals_section = ''
    if order_total > 0:
        tracking_order_totals_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Order Summary</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Subtotal</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">${subtotal:.2f}</td>
                    </tr>
                    {tracking_discount_row_html}
                    <tr style="border-top:1px solid {BORDER_COLOR};padding-top:4px;">
                        <td style="padding:4px 0;font-weight:700;color:{TEXT_PRIMARY};">Total</td>
                        <td style="padding:4px 0;text-align:right;font-weight:700;color:{TEXT_PRIMARY};">${order_total:.2f}</td>
                    </tr>
                </table>
            </td></tr>
        </table>
    </td></tr>'''

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header bar -->
    <tr><td style="background:{BRAND_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td>
                    <div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">Your Order Has Shipped!</div>
                </td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">Order #{order_number}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Greeting -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:15px;color:{TEXT_PRIMARY};">Hi {customer_name or 'Valued Customer'},</div>
        <div style="font-size:13px;color:{TEXT_SECONDARY};margin-top:6px;">Great news! Your order has been shipped.</div>
    </td></tr>

    <!-- Tracking Information -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Tracking Information</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Carrier</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{tracking_provider}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Tracking Number</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{tracking_number}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Date Shipped</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">{date_shipped}</td>
                    </tr>
                </table>{track_button}
            </td></tr>
        </table>
    </td></tr>

    {items_section}
    {shipping_section}
    
    <!-- Order Totals -->
    {tracking_order_totals_section}

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your shipment?</div>
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
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">
            {BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}
        </div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''
    return html


# Common carrier tracking URL patterns
CARRIER_TRACKING_URLS = {
    'usps': 'https://tools.usps.com/go/TrackConfirmAction?tLabels={number}',
    'fedex': 'https://www.fedex.com/fedextrack/?trknbr={number}',
    'ups': 'https://www.ups.com/track?tracknum={number}',
    'dhl': 'https://www.dhl.com/en/express/tracking.html?AWB={number}',
}


def _resolve_tracking_link(tracking_provider, tracking_number, custom_link=''):
    """Resolve a tracking URL from provider name and number."""
    if custom_link:
        return custom_link
    provider_lower = (tracking_provider or '').lower()
    for key, url_template in CARRIER_TRACKING_URLS.items():
        if key in provider_lower:
            return url_template.format(number=tracking_number)
    return ''


def _build_bundle_name_map(line_items):
    """
    Build a mapping of line item IDs to bundle-aware display names.
    Bundle children get prefixed with their parent name:
      'Studio Detox Program: Probiotic 25+ Capsules'
    Bundle parents are returned as-is.
    """
    # First pass: identify bundle parents by their line item ID
    bundle_parent_names = {}  # line_item_id -> parent name
    for li in line_items:
        meta_data = li.get('meta_data', [])
        if not isinstance(meta_data, list):
            continue
        is_parent = any(
            (m.get('key') == '_ds_bundle_parent_item' and m.get('value') == 'true') or
            (m.get('key') == 'Bundle Type' and m.get('value') == 'Parent Item')
            for m in meta_data
        )
        if is_parent:
            bundle_parent_names[str(li.get('id', ''))] = li.get('name', 'Bundle')
    
    # Second pass: build name map — prefix children with parent name
    name_map = {}  # line_item_id or product_id -> display name
    for li in line_items:
        li_id = str(li.get('id', ''))
        pid = str(li.get('product_id', ''))
        name = li.get('name', '')
        meta_data = li.get('meta_data', []) if isinstance(li.get('meta_data'), list) else []
        
        # Check if this is a bundle child
        parent_name = None
        bundled_by = li.get('bundled_by', '')
        if bundled_by and str(bundled_by) in bundle_parent_names:
            parent_name = bundle_parent_names[str(bundled_by)]
        else:
            # Check meta_data for _bundled_by or _ds_bundle_parent_id
            for m in meta_data:
                if m.get('key') == '_bundled_by' and str(m.get('value', '')) in bundle_parent_names:
                    parent_name = bundle_parent_names[str(m['value'])]
                    break
                if m.get('key') == '_ds_bundle_parent_id':
                    # Try to find matching parent by bundle ID
                    bundle_id = str(m.get('value', ''))
                    for parent_li_id, pname in bundle_parent_names.items():
                        parent_li = next((l for l in line_items if str(l.get('id', '')) == parent_li_id), None)
                        if parent_li:
                            parent_meta = parent_li.get('meta_data', []) if isinstance(parent_li.get('meta_data'), list) else []
                            for pm in parent_meta:
                                if pm.get('key') in ('Bundle ID', '_ds_bundle_parent_id') and str(pm.get('value', '')) == bundle_id:
                                    parent_name = pname
                                    break
                        if parent_name:
                            break
                    if parent_name:
                        break
        
        # Also detect by name prefix "└─"
        if not parent_name and name.startswith('└─'):
            # Find first bundle parent in the list
            if bundle_parent_names:
                parent_name = next(iter(bundle_parent_names.values()))
            name = name.lstrip('└─').strip()
        
        if parent_name:
            display_name = f"{parent_name}: {name}"
        else:
            display_name = name
        
        if li_id:
            name_map[li_id] = display_name
        if pid:
            name_map[pid] = display_name
    
    return name_map


def send_tracking_email(order_id, tracking_data):
    """
    Send a shipment tracking notification email for a WooCommerce order.
    Fetches order data from WooCommerce to get customer info.
    Returns True on success, False on failure. Never raises.
    """
    try:
        wc = WooCommerceAPI()
        woo_order = wc.get_order(order_id)
        if not woo_order:
            logger.warning(f"📧 Cannot send tracking email: WooCommerce order {order_id} not found")
            return False

        billing = woo_order.get('billing', {})
        customer_email = billing.get('email', '')
        if not customer_email:
            logger.info(f"📧 No customer email for order {order_id}, skipping tracking email")
            return False

        customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
        order_number = woo_order.get('number', str(order_id))

        tracking_provider = tracking_data.get('tracking_provider', '') or tracking_data.get('custom_tracking_provider', '')
        tracking_number = tracking_data.get('tracking_number', '')
        date_shipped = tracking_data.get('date_shipped', '')
        custom_link = tracking_data.get('tracking_link', '') or tracking_data.get('custom_tracking_link', '')
        tracking_link = _resolve_tracking_link(tracking_provider, tracking_number, custom_link)

        # Build items HTML from products_list, resolving names from order line_items
        items_html = ''
        products_list = tracking_data.get('products_list', [])
        logger.info(f"📦 send_tracking_email: products_list has {len(products_list) if products_list else 0} items: {products_list}")

        # Fallback: if products_list is empty, use all order line items
        if not products_list:
            logger.warning(f"📦 send_tracking_email: products_list is EMPTY for order {order_id}, falling back to all line items")
            products_list = [
                f"{li.get('name', 'Item')} \u00d7 {li.get('quantity', 1)}"
                for li in woo_order.get('line_items', [])
            ]

        if products_list:
            # Build lookup maps: product_id -> name, item_id -> name
            # 🔥 BUNDLE EMAIL FIX: Use bundle-aware name map to prefix children with parent name
            bundle_name_map = _build_bundle_name_map(woo_order.get('line_items', []))
            pid_to_name = {}
            iid_to_name = {}
            for li in woo_order.get('line_items', []):
                pid_to_name[str(li.get('product_id', ''))] = li.get('name', '')
                iid_to_name[str(li.get('id', ''))] = li.get('name', '')

            item_rows = ''
            for prod in products_list:
                if isinstance(prod, str):
                    # String format from WordPress/Postman or fallback — use directly
                    item_rows += f'<div style="font-size:11px;color:{TEXT_PRIMARY};padding:3px 0;">&bull; {prod}</div>'
                else:
                    # Dict format from POS — resolve name from order line_items
                    prod_id = str(prod.get('product_id', '') or prod.get('product', ''))
                    item_id = str(prod.get('item_id', ''))
                    # 🔥 BUNDLE EMAIL FIX: Use bundle-aware name (includes parent prefix for children)
                    prod_name = (
                        bundle_name_map.get(item_id, '')
                        or bundle_name_map.get(prod_id, '')
                        or prod.get('productName', '')
                        or pid_to_name.get(prod_id, '')
                        or iid_to_name.get(item_id, '')
                        or iid_to_name.get(prod_id, '')
                        or f"Product #{prod_id or 'N/A'}"
                    )
                    qty = prod.get('qty', 1)
                    item_rows += f'<div style="font-size:11px;color:{TEXT_PRIMARY};padding:3px 0;">&bull; {prod_name} &times; {qty}</div>'
            items_html = item_rows

        # Build shipping address HTML
        shipping = woo_order.get('shipping', {})
        addr_parts = []
        ship_name = f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()
        if ship_name:
            addr_parts.append(ship_name)
        if shipping.get('address_1'):
            addr_parts.append(shipping['address_1'])
        if shipping.get('address_2'):
            addr_parts.append(shipping['address_2'])
        city_state = f"{shipping.get('city', '')}, {shipping.get('state', '')} {shipping.get('postcode', '')}".strip(', ')
        if city_state:
            addr_parts.append(city_state)
        shipping_address_html = '<br>'.join(addr_parts) if addr_parts else ''

        # Extract order totals
        order_total = float(woo_order.get('total', '0.00'))
        discount_total = float(woo_order.get('discount_total', '0.00'))
        subtotal = float(woo_order.get('subtotal', '0.00'))

        html = _build_tracking_email_html(
            customer_name, order_number, tracking_provider, tracking_number,
            tracking_link, date_shipped, items_html, shipping_address_html,
            order_total, discount_total, subtotal
        )

        subject = f'Your Order Has Shipped! — Order #{order_number}'
        text = f'Your order #{order_number} has been shipped via {tracking_provider}. Tracking: {tracking_number}'

        from .email_sender import send_pos_email
        return send_pos_email(
            subject=subject,
            text_content=text,
            html_content=html,
            to_email=customer_email,
            email_type='tracking',
            related_order=str(order_number),
        )

    except Exception as e:
        logger.error(f"📧 Error sending tracking email for order {order_id}: {e}")
        return False


# ==================== PARTIAL-SHIPPED EMAIL ====================

PARTIAL_SHIP_COLOR = '#b45309'
PARTIAL_SHIP_LIGHT = '#fffbeb'

def _build_partial_shipped_email_html(customer_name, order_number, tracking_provider, tracking_number,
                                       tracking_link, date_shipped, shipped_items_html,
                                       unshipped_items_html, shipping_address_html,
                                       order_total=0, discount_total=0, subtotal=0):
    """Build a professional HTML partial-shipment notification email."""

    track_button = ''
    if tracking_link:
        track_button = f'''
                <div style="margin-top:12px;text-align:center;">
                    <a href="{tracking_link}" style="display:inline-block;padding:10px 24px;background:{BRAND_COLOR};color:#fff;text-decoration:none;border-radius:4px;font-size:13px;font-weight:600;">Track Your Package &rarr;</a>
                </div>'''

    shipped_section = ''
    if shipped_items_html:
        shipped_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Items Shipped</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                {shipped_items_html}
            </td></tr>
        </table>
    </td></tr>'''

    unshipped_section = ''
    if unshipped_items_html:
        unshipped_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{PARTIAL_SHIP_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{PARTIAL_SHIP_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Awaiting Shipment</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                {unshipped_items_html}
                <div style="font-size:10px;color:{TEXT_MUTED};margin-top:8px;">These items will be shipped separately. You&rsquo;ll receive another email when they&rsquo;re on the way.</div>
            </td></tr>
        </table>
    </td></tr>'''

    shipping_section = ''
    if shipping_address_html:
        shipping_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="padding:12px 16px;">
                <div style="font-size:12px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:6px;">Shipping Address</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};line-height:1.6;">{shipping_address_html}</div>
            </td></tr>
        </table>
    </td></tr>'''

    disc_color_partial = '#059669' if discount_total > 0 else TEXT_SECONDARY
    partial_discount_row_html = f'''
                    <tr>
                        <td style="padding:4px 0;color:{disc_color_partial};">Discount</td>
                        <td style="padding:4px 0;text-align:right;color:{disc_color_partial};">-${discount_total:.2f}</td>
                    </tr>'''
    partial_order_totals_section = ''
    if order_total > 0:
        partial_order_totals_section = f'''
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Order Summary</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Subtotal</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">${subtotal:.2f}</td>
                    </tr>
                    {partial_discount_row_html}
                    <tr style="border-top:1px solid {BORDER_COLOR};padding-top:4px;">
                        <td style="padding:4px 0;font-weight:700;color:{TEXT_PRIMARY};">Total</td>
                        <td style="padding:4px 0;text-align:right;font-weight:700;color:{TEXT_PRIMARY};">${order_total:.2f}</td>
                    </tr>
                </table>
            </td></tr>
        </table>
    </td></tr>'''

    html = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header bar -->
    <tr><td style="background:{PARTIAL_SHIP_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td>
                    <div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">Your Order is Partially Shipped</div>
                </td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">Order #{order_number}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Greeting -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:15px;color:{TEXT_PRIMARY};">Hi {customer_name or 'Valued Customer'},</div>
        <div style="font-size:13px;color:{TEXT_SECONDARY};margin-top:6px;">Part of your order has been shipped! The remaining items will follow in a separate shipment.</div>
    </td></tr>

    <!-- Tracking Information -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Tracking Information</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;">
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Carrier</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{tracking_provider}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Tracking Number</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};font-weight:600;">{tracking_number}</td>
                    </tr>
                    <tr>
                        <td style="padding:4px 0;color:{TEXT_SECONDARY};">Date Shipped</td>
                        <td style="padding:4px 0;text-align:right;color:{TEXT_PRIMARY};">{date_shipped}</td>
                    </tr>
                </table>{track_button}
            </td></tr>
        </table>
    </td></tr>

    {shipped_section}
    {unshipped_section}
    {shipping_section}
    
    <!-- Order Totals -->
    {partial_order_totals_section}

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your shipment?</div>
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
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">
            {BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}
        </div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''
    return html


def _is_line_item_non_shippable(line_item):
    """
    Check if a WooCommerce order line item is non-shippable (clinic pickup).
    Mirrors the frontend isNonShippableItem() logic.
    
    Non-shippable if:
    - MISC items (shipping fees, other charges)
    - LAB items (lab tests, panels)
    - Items named "MISCELLANEOUS" (generic service/fee items)
    - Credited service redemptions
    - Clinic location (Boca/Jupiter) AND _needs_shipping is '0' or absent
    Shippable if:
    - Dropship location (always needs shipping)
    - Clinic location with _needs_shipping = '1'
    - No fulfillment location detected (default to shippable)
    """
    name = (line_item.get('name', '') or '').lower()
    sku = (line_item.get('sku', '') or '').lower()

    # MISC items (shipping fees, other charges)
    if name.startswith('misc:') or name.startswith('misc -') or sku.startswith('misc-') or sku.startswith('misc_'):
        return True
    # LAB items (lab tests, panels) — never need physical shipment
    if name.startswith('lab:') or name.startswith('lab -') or sku.startswith('lab'):
        return True
    # Items named "MISCELLANEOUS" (generic service/fee items)
    if name == 'miscellaneous':
        return True

    meta_data = line_item.get('meta_data', [])
    if not isinstance(meta_data, list):
        return False

    fulfillment = ''
    needs_shipping = None
    for meta in meta_data:
        key = meta.get('key', '')
        value = meta.get('value', '')
        if key == '_fulfillment_location':
            fulfillment = str(value).lower()
        elif key == '_atum_location_name':
            if not fulfillment:
                fulfillment = str(value).lower()
        elif key == '_needs_shipping':
            needs_shipping = str(value)
        elif key == 'ds_is_credited_service' and str(value) == '1':
            return True

    # No fulfillment location found — default to shippable
    if not fulfillment:
        return False

    # Dropship → always needs shipping
    if 'dropship' in fulfillment:
        return False

    # Clinic locations (Boca/Jupiter): check _needs_shipping flag
    is_clinic = 'boca' in fulfillment or 'jupiter' in fulfillment
    if is_clinic:
        return needs_shipping != '1'

    return False


def send_partial_shipped_email(order_id, tracking_data):
    """
    Send a partial-shipment notification email for a WooCommerce order.
    Shows shipped items + unshipped (remaining) items in separate sections.
    Returns True on success, False on failure. Never raises.
    """
    try:
        wc = WooCommerceAPI()
        woo_order = wc.get_order(order_id)
        if not woo_order:
            logger.warning(f"📧 Cannot send partial-shipped email: WooCommerce order {order_id} not found")
            return False

        billing = woo_order.get('billing', {})
        customer_email = billing.get('email', '')
        if not customer_email:
            logger.info(f"📧 No customer email for order {order_id}, skipping partial-shipped email")
            return False

        customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
        order_number = woo_order.get('number', str(order_id))

        tracking_provider = tracking_data.get('tracking_provider', '') or tracking_data.get('custom_tracking_provider', '')
        tracking_number = tracking_data.get('tracking_number', '')
        date_shipped = tracking_data.get('date_shipped', '')
        custom_link = tracking_data.get('tracking_link', '') or tracking_data.get('custom_tracking_link', '')
        tracking_link = _resolve_tracking_link(tracking_provider, tracking_number, custom_link)

        # Build lookup maps from order line items
        line_items = woo_order.get('line_items', [])
        # 🔥 BUNDLE EMAIL FIX: Use bundle-aware name map
        bundle_name_map = _build_bundle_name_map(line_items)
        pid_to_name = {}
        iid_to_name = {}
        pid_to_qty = {}
        iid_to_qty = {}
        for li in line_items:
            pid = str(li.get('product_id', ''))
            iid = str(li.get('id', ''))
            name = li.get('name', '')
            qty = li.get('quantity', 1)
            pid_to_name[pid] = name
            iid_to_name[iid] = name
            pid_to_qty[pid] = qty
            iid_to_qty[iid] = qty

        # Build shipped items HTML and track which items/quantities were shipped
        shipped_items_html = ''
        shipped_map = {}  # iid or pid -> shipped qty
        products_list = tracking_data.get('products_list', [])
        if products_list:
            item_rows = ''
            for prod in products_list:
                if isinstance(prod, str):
                    item_rows += f'<div style="font-size:11px;color:{TEXT_PRIMARY};padding:3px 0;">&bull; {prod}</div>'
                else:
                    prod_id = str(prod.get('product_id', '') or prod.get('product', ''))
                    item_id = str(prod.get('item_id', ''))
                    # 🔥 BUNDLE EMAIL FIX: Use bundle-aware name (includes parent prefix for children)
                    prod_name = (
                        bundle_name_map.get(item_id, '')
                        or bundle_name_map.get(prod_id, '')
                        or prod.get('productName', '')
                        or pid_to_name.get(prod_id, '')
                        or iid_to_name.get(item_id, '')
                        or iid_to_name.get(prod_id, '')
                        or f"Product #{prod_id or 'N/A'}"
                    )
                    qty = int(prod.get('qty', 1))
                    item_rows += f'<div style="font-size:11px;color:{TEXT_PRIMARY};padding:3px 0;">&bull; {prod_name} &times; {qty}</div>'
                    # Track shipped quantities by item_id (preferred) or product_id
                    key = item_id if item_id and item_id in iid_to_name else prod_id
                    shipped_map[key] = shipped_map.get(key, 0) + qty
            shipped_items_html = item_rows

        # Build total shipped map from ALL trackings (not just current one)
        # so "Awaiting Shipment" correctly accounts for previously shipped items
        total_shipped_map = {}  # iid or pid -> total shipped qty across all trackings
        try:
            all_trackings = wc.get_shipment_trackings(order_id)
            for t in (all_trackings or []):
                for p in (t.get('products_list', []) or []):
                    if isinstance(p, dict):
                        t_prod_id = str(p.get('product', '') or p.get('product_id', ''))
                        t_item_id = str(p.get('item_id', ''))
                        t_qty = int(str(p.get('qty', 0)))
                        key = t_item_id if t_item_id and t_item_id in iid_to_name else t_prod_id
                        if key:
                            total_shipped_map[key] = total_shipped_map.get(key, 0) + t_qty
            logger.info(f"📦 Partial email: total_shipped_map from all trackings = {total_shipped_map}")
        except Exception as ts_err:
            logger.warning(f"📦 Failed to fetch all trackings for unshipped calc, falling back to current tracking: {ts_err}")
            total_shipped_map = shipped_map

        # Build unshipped items HTML — order line items minus ALL shipped quantities
        # Skip non-shippable items (clinic pickup without shipping flag)
        # 🔥 BUNDLE EMAIL FIX: Use bundle-aware names for awaiting shipment items
        unshipped_rows = ''
        for li in line_items:
            pid = str(li.get('product_id', ''))
            iid = str(li.get('id', ''))
            name = bundle_name_map.get(iid, '') or bundle_name_map.get(pid, '') or li.get('name', '')
            total_qty = li.get('quantity', 1)

            # Filter out non-shippable items (clinic pickup) from "Awaiting Shipment"
            if _is_line_item_non_shippable(li):
                continue

            # Check how many were shipped across ALL trackings (by item_id first, then product_id)
            shipped_qty = total_shipped_map.get(iid, 0) or total_shipped_map.get(pid, 0)
            remaining = total_qty - shipped_qty
            if remaining > 0:
                unshipped_rows += f'<div style="font-size:11px;color:{TEXT_PRIMARY};padding:3px 0;">&bull; {name} &times; {remaining}</div>'
        unshipped_items_html = unshipped_rows

        # Build shipping address HTML
        shipping = woo_order.get('shipping', {})
        addr_parts = []
        ship_name = f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()
        if ship_name:
            addr_parts.append(ship_name)
        if shipping.get('address_1'):
            addr_parts.append(shipping['address_1'])
        if shipping.get('address_2'):
            addr_parts.append(shipping['address_2'])
        city_state = f"{shipping.get('city', '')}, {shipping.get('state', '')} {shipping.get('postcode', '')}".strip(', ')
        if city_state:
            addr_parts.append(city_state)
        shipping_address_html = '<br>'.join(addr_parts) if addr_parts else ''

        # Extract order totals
        order_total = float(woo_order.get('total', '0.00'))
        discount_total = float(woo_order.get('discount_total', '0.00'))
        subtotal = float(woo_order.get('subtotal', '0.00'))

        html = _build_partial_shipped_email_html(
            customer_name, order_number, tracking_provider, tracking_number,
            tracking_link, date_shipped, shipped_items_html,
            unshipped_items_html, shipping_address_html,
            order_total, discount_total, subtotal
        )

        subject = f'Your Order is Partially Shipped — Order #{order_number}'
        text = f'Part of your order #{order_number} has been shipped via {tracking_provider}. Tracking: {tracking_number}. Remaining items will ship separately.'

        from .email_sender import send_pos_email
        return send_pos_email(
            subject=subject,
            text_content=text,
            html_content=html,
            to_email=customer_email,
            email_type='partial_tracking',
            related_order=str(order_number),
        )

    except Exception as e:
        logger.error(f"📧 Error sending partial-shipped email for order {order_id}: {e}")
        return False


# ────────────────────────────────────────────────────────────────────
# Unpaid Invoice PDF + Email
# ────────────────────────────────────────────────────────────────────

def _invoice_item_line_fields(item):
    """Parse list extended, net extended, and line discount for one invoice item dict (JSON)."""
    qty = int(item.get('quantity', 1) or 1)
    price = float(item.get('price', 0) or 0)
    line_list = price * qty
    line_discount = float(item.get('line_discount', 0) or 0)
    lt_raw = item.get('line_total')
    if lt_raw is not None:
        line_net = float(lt_raw)
    else:
        line_net = max(0.0, line_list - line_discount) if line_discount > 0 else line_list
    if line_discount <= 0.001 and line_list > line_net + 0.001:
        line_discount = line_list - line_net
    return qty, price, line_list, line_net, line_discount


def _invoice_line_amount_stack_html(line_list, line_net, line_discount, *, pdf=False):
    """Right-column breakdown: Original, Discount, After discount (unpaid invoice lines)."""
    if line_discount <= 0.005:
        if pdf:
            return f'<span style="font-size:11px;font-weight:bold;color:#1f2937;">${line_net:.2f}</span>'
        return f'<span style="font-size:13px;color:{TEXT_PRIMARY};font-weight:500;">${line_net:.2f}</span>'
    lbl = '#6b7280'
    orig_val = '#9ca3af'
    disc_c = '#15803d'
    if pdf:
        return f'''<div style="text-align:right;font-size:9px;line-height:1.45;">
<div><span style="color:{lbl};">Original</span> <span style="text-decoration:line-through;color:{orig_val};">${line_list:.2f}</span></div>
<div style="margin-top:2px;"><span style="color:{lbl};">Discount</span> <span style="color:{disc_c};font-weight:bold;">-${line_discount:.2f}</span></div>
<div style="margin-top:3px;font-size:10px;font-weight:bold;"><span style="color:#1f2937;">After discount</span> <span style="color:{disc_c};">${line_net:.2f}</span></div>
</div>'''
    return f'''<div style="text-align:right;font-size:11px;line-height:1.5;">
<div><span style="color:{TEXT_SECONDARY};">Original</span> <span style="text-decoration:line-through;color:#9ca3af;">${line_list:.2f}</span></div>
<div style="margin-top:2px;"><span style="color:{TEXT_SECONDARY};">Discount</span> <span style="color:{disc_c};font-weight:600;">-${line_discount:.2f}</span></div>
<div style="margin-top:4px;font-size:13px;font-weight:700;"><span style="color:{TEXT_PRIMARY};">After discount</span> <span style="color:{disc_c};">${line_net:.2f}</span></div>
</div>'''


def _sum_invoice_line_discounts(items):
    total = 0.0
    for item in items:
        *_, ld = _invoice_item_line_fields(item)
        total += ld
    return total


def _invoice_display_amounts_per_line(items, order_discount):
    """
    (line_list, line_net, line_discount) per item for invoice PDF/email.
    When order_discount > 0, spreads it across lines proportional to line net
    so Original/Discount/After stacks show on each line. If spread, caller should
    omit the separate 'Order discount' row (merged_order True).
    """
    parsed = [_invoice_item_line_fields(it) for it in items]
    rows = []
    merged = False
    if order_discount > 0.005:
        sum_net = sum(max(p[3], 0.0) for p in parsed)
        if sum_net >= 0.01:
            merged = True
            for p in parsed:
                _qty, _price, line_list, line_net, _line_disc = p
                w = max(line_net, 1e-9)
                alloc = order_discount * (w / sum_net)
                net_after_order = line_net - alloc
                total_disc = line_list - net_after_order
                rows.append((line_list, net_after_order, total_disc))
            return rows, merged
    for p in parsed:
        rows.append((p[2], p[3], p[4]))
    return rows, merged


def _invoice_items_have_line_tax_column(items):
    for it in items:
        try:
            if it.get('tax_rate') is not None and float(it.get('tax_rate') or 0) > 0:
                return True
            if it.get('line_tax') is not None and float(it.get('line_tax') or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _invoice_line_tax_cell_html(item):
    try:
        lt = item.get('line_tax')
        if lt is not None and float(lt or 0) > 0:
            return f'${float(lt):.2f}'
        tr = item.get('tax_rate')
        if tr is not None:
            trf = float(tr)
            if trf > 0:
                if trf <= 1.0001:
                    trf *= 100.0
                if abs(trf - round(trf)) < 0.01:
                    return f'{int(round(trf))}%'
                return f'{trf:.2f}%'
    except (TypeError, ValueError):
        pass
    return '&#8212;'


def _invoice_discount_column_text(item, line_discount_dollars):
    """Discount cell for tabular invoice: em dash, -N%, or -$N."""
    if line_discount_dollars <= 0.005:
        return '&#8212;'
    dt = (item.get('discount_type') or '').lower()
    if 'percent' in dt and item.get('discount') is not None:
        try:
            dv = float(item.get('discount'))
            if abs(dv - round(dv)) < 0.001:
                return f'-{int(round(dv))}%'
            return f'-{dv:.2f}%'
        except (TypeError, ValueError):
            pass
    return f'-${line_discount_dollars:.2f}'


def _build_invoice_tabular_table_html(items, display_amounts, order_discount, merged_order_disc, *, pdf=False, extra_rows=''):
    """Qty | Description | Price | Discount [| Tax] | Line total + optional order discount row."""
    show_tax = _invoice_items_have_line_tax_column(items)
    num_cols = 6 if show_tax else 5
    fs_h = '9px' if pdf else '11px'
    fs_b = '10px' if pdf else '13px'
    fs_sm = '8px' if pdf else '10px'
    border = '1px solid #e5e7eb'
    hdr_color = '#6b7280'
    text_dark = '#1f2937'
    disc_c = '#059669'

    th_qty = f'<th align="left" style="padding:8px 4px 8px 0;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:700;color:{hdr_color};width:36px;">Qty</th>'
    th_desc = f'<th align="left" style="padding:8px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:700;color:{hdr_color};">Description</th>'
    th_price = f'<th align="right" style="padding:8px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:700;color:{hdr_color};width:64px;">Price</th>'
    th_disc = f'<th align="right" style="padding:8px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:700;color:{hdr_color};width:72px;">Discount</th>'
    th_tax = ''
    if show_tax:
        th_tax = f'<th align="right" style="padding:8px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:700;color:{hdr_color};width:52px;">Tax</th>'
    th_line = f'<th align="right" style="padding:8px 0 8px 4px;border-bottom:2px solid {text_dark};font-size:{fs_h};font-weight:700;color:{hdr_color};width:76px;">Line total</th>'
    rows_html = f'<tr>{th_qty}{th_desc}{th_price}{th_disc}{th_tax}{th_line}</tr>'

    for idx, item in enumerate(items):
        name = item.get('name', 'Item')
        name = re.sub(r'[\u2500-\u257F]', '', name).strip() or 'Item'
        _var_label_inv = _get_item_variation_name(item)
        if _var_label_inv and _var_label_inv not in name:
            name = f'{name} - {_var_label_inv}'
        name = _with_series_suffix(name, _get_item_series_count(item))
        qty, price, _, _, _ = _invoice_item_line_fields(item)
        _ll, line_net, line_discount = display_amounts[idx]
        sku = item.get('sku', '')
        brand = item.get('brand', '')
        brand_line = f'<div style="font-size:{fs_sm};color:{hdr_color};margin-top:2px;"><span style="font-weight:600;">{brand}</span></div>' if brand else ''
        sku_line = f'<div style="font-size:{fs_sm};color:{hdr_color};margin-top:1px;">SKU: {sku}</div>' if sku else ''
        disc_txt = _invoice_discount_column_text(item, line_discount)
        tax_td = ''
        if show_tax:
            tax_td = f'<td align="right" style="padding:8px 4px;border-bottom:{border};font-size:{fs_b};color:{text_dark};vertical-align:top;">{_invoice_line_tax_cell_html(item)}</td>'
        rows_html += f'''
        <tr>
            <td align="left" style="padding:8px 4px 8px 0;border-bottom:{border};font-size:{fs_b};color:{text_dark};vertical-align:top;">{qty}</td>
            <td align="left" style="padding:8px 4px;border-bottom:{border};font-size:{fs_b};color:{text_dark};vertical-align:top;"><div style="font-weight:600;">{name}</div>{brand_line}{sku_line}</td>
            <td align="right" style="padding:8px 4px;border-bottom:{border};font-size:{fs_b};color:{text_dark};vertical-align:top;">${price:.2f}</td>
            <td align="right" style="padding:8px 4px;border-bottom:{border};font-size:{fs_b};color:{disc_c};vertical-align:top;">{disc_txt}</td>
            {tax_td}
            <td align="right" style="padding:8px 0 8px 4px;border-bottom:{border};font-size:{fs_b};font-weight:700;color:{text_dark};vertical-align:top;">${line_net:.2f}</td>
        </tr>'''

    order_row = ''
    if order_discount > 0.005 and not merged_order_disc:
        csp = num_cols - 1
        order_row = f'''
        <tr>
            <td colspan="{csp}" style="padding:10px 4px 10px 0;border-bottom:{border};font-size:{fs_b};font-weight:700;color:{text_dark};">Order discount</td>
            <td align="right" style="padding:10px 0 10px 4px;border-bottom:{border};font-size:{fs_b};font-weight:700;color:#15803d;">-${order_discount:.2f}</td>
        </tr>'''

    return f'<table width="100%" cellpadding="0" cellspacing="0">{rows_html}{order_row}{extra_rows}</table>', num_cols


def _build_pdf_invoice_html(customer_name, items, subtotal, total, invoice_ref, order_discount=0, discount_total=None, total_tax=0):
    """Build xhtml2pdf-compatible HTML for an unpaid invoice PDF attachment."""
    now_str = datetime.now().strftime('%b %d, %Y at %I:%M %p')
    if discount_total is None:
        discount_total = max(0.0, float(subtotal) - float(total))
    total_tax = float(total_tax or 0)

    display_amounts, merged_order_disc = _invoice_display_amounts_per_line(items, order_discount)

    # Build totals rows aligned to the tabular table columns
    show_tax_col_pdf = _invoice_items_have_line_tax_column(items)
    num_cols_pdf = 6 if show_tax_col_pdf else 5
    disc_color_pdf_inv = '#059669' if discount_total > 0 else '#6b7280'
    disc_display_pdf_inv = f'-${discount_total:.2f}' if discount_total > 0 else ''
    tax_row_pdf = ''
    if total_tax > 0.005:
        tax_row_pdf = f'''
        <tr>
            <td colspan="{num_cols_pdf - 1}" style="padding:3px 0;font-size:10px;color:#6b7280;">Tax</td>
            <td align="right" style="padding:3px 0;font-size:10px;color:#1f2937;">${total_tax:.2f}</td>
        </tr>'''
    totals_rows = f'''
        <tr>
            <td colspan="2" style="padding:6px 0;font-size:10px;color:#6b7280;">Subtotal</td>
            <td align="right" style="padding:6px 4px;font-size:10px;color:#1f2937;">${subtotal:.2f}</td>
            <td align="right" style="padding:6px 4px;font-size:10px;color:{disc_color_pdf_inv};">{disc_display_pdf_inv}</td>
            {"<td></td>" if show_tax_col_pdf else ""}
            <td align="right" style="padding:6px 0;font-size:10px;color:#1f2937;font-weight:bold;">${total:.2f}</td>
        </tr>{tax_row_pdf}
        <tr>
            <td colspan="{num_cols_pdf - 1}" style="padding:8px 0 4px;font-size:12px;font-weight:bold;color:#1f2937;border-top:2px solid #1f2937;">Total</td>
            <td align="right" style="padding:8px 0 4px;font-size:12px;font-weight:bold;color:#1f2937;border-top:2px solid #1f2937;">${total:.2f}</td>
        </tr>
        <tr>
            <td colspan="{num_cols_pdf - 1}" style="padding:3px 0;font-size:10px;color:#dc2626;font-weight:bold;">Balance Due</td>
            <td align="right" style="padding:3px 0;font-size:10px;color:#dc2626;font-weight:bold;">${total:.2f}</td>
        </tr>'''

    items_table_html, _ = _build_invoice_tabular_table_html(
        items, display_amounts, order_discount, merged_order_disc, pdf=True, extra_rows=totals_rows
    )

    # Payment row — Pending
    payment_rows = f'''
            <tr>
                <td style="padding:4px 0;font-size:11px;color:#1f2937;">Pending</td>
                <td style="text-align:right;padding:4px 0;font-size:11px;font-weight:bold;">${total:.2f}</td>
            </tr>'''

    return f'''<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/>
<style>
    @page {{ size: letter; margin: 1cm; }}
    body {{ font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #1f2937; margin: 0; padding: 0; }}
    table {{ border-collapse: collapse; }}
    .keep-together {{ -pdf-keep-with-next: true; }}
</style>
</head>
<body>
    <!-- Logo -->
    <table width="100%" style="margin-bottom:0;background:#ffffff;">
        <tr><td style="padding:10px 20px;text-align:center;background:#ffffff;">
            <img src="file:///{PDF_LOGO_PATH.replace(chr(92), '/')}" width="200" style="background:#ffffff;" />
        </td></tr>
    </table>

    <!-- Header bar -->
    <table width="100%" style="background:#183249;margin-bottom:10px;">
        <tr>
            <td style="padding:12px 20px;">
                <span style="font-size:10px;color:rgba(255,255,255,0.85);">Invoice</span>
            </td>
            <td style="padding:12px 20px;text-align:right;">
                <span style="font-size:10px;color:rgba(255,255,255,0.85);">{invoice_ref}</span><br/>
                <span style="font-size:9px;color:rgba(255,255,255,0.7);">{now_str}</span>
            </td>
        </tr>
    </table>

    <!-- Greeting -->
    <p style="font-size:11px;color:#1f2937;margin:0 0 2px 0;">Hi <strong>{customer_name}</strong>,</p>
    <p style="font-size:10px;color:#6b7280;margin:0 0 8px 0;">Thank you for your order! Here is your invoice. Payment is required to complete your purchase.</p>

    <!-- Items table -->
    <table width="100%" style="border:1px solid #e5e7eb;">
        <tr class="keep-together"><td colspan="2" style="background:#e8edf2;padding:6px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#183249;text-transform:uppercase;letter-spacing:1px;">Order Items</span>
        </td></tr>
        <tr><td colspan="2" style="padding:4px 12px 10px;">
            {items_table_html}
        </td></tr>
    </table>

    <!-- Payment section -->
    <table width="100%" style="margin-top:14px;border:1px solid #e5e7eb;">
        <tr><td style="background:#e8edf2;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#183249;text-transform:uppercase;letter-spacing:1px;">Payment</span>
        </td></tr>
        <tr><td style="padding:8px 12px;">
            <table width="100%">
                {payment_rows}
            </table>
        </td></tr>
    </table>

    <!-- Help -->
    <table width="100%" style="background:#f9fafb;margin-top:14px;">
        <tr><td style="padding:10px 12px;text-align:center;">
            <span style="font-size:10px;color:#6b7280;">Questions about your invoice?</span><br/>
            <span style="font-size:11px;color:#1f2937;font-weight:bold;">Call us at {BUSINESS_PHONE}</span><br/>
            <span style="font-size:10px;color:#6b7280;">or email us at support@doctorsstudio.com</span>
        </td></tr>
    </table>

    <!-- Footer -->
    <table width="100%" style="margin-top:18px;border-top:1px solid #e5e7eb;">
        <tr><td style="padding:10px 0;text-align:center;">
            <span style="font-size:9px;font-weight:bold;color:#1f2937;">Payment Required</span><br/>
            <span style="font-size:9px;color:#6b7280;">Payment is due upon receipt of this invoice.</span><br/><br/>
            <span style="font-size:9px;color:#1f2937;font-weight:bold;">Thank you for your business!</span>
        </td></tr>
        <tr><td style="background:#f9fafb;padding:8px 0;text-align:center;">
            <span style="font-size:8px;color:#9ca3af;">{BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}</span><br/>
            <span style="font-size:8px;color:#9ca3af;">{BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}</span>
        </td></tr>
    </table>
</body>
</html>'''


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_unpaid_invoice(request):
    """Send an unpaid invoice email to a customer.
    Expects JSON body: { email, customer_name, customer_id?, items, subtotal, total, invoice_ref }
    Persists an Invoice record and includes a secure "Pay Now" link in the email.
    """
    from .models import Invoice, Contact
    from .invoice_utils import generate_invoice_token

    data = request.data
    email = data.get('email')
    customer_name = data.get('customer_name', 'Valued Customer')
    customer_id = data.get('customer_id')
    items = data.get('items', [])
    subtotal = float(data.get('subtotal', 0))
    total = float(data.get('total', 0))
    invoice_ref = data.get('invoice_ref', '')

    # Calculate discount total from items if not provided
    discount_total = float(data.get('discount_total', 0))
    if discount_total <= 0.001 and items:
        # Calculate discount as difference between item subtotal and total
        item_subtotal = sum(float(item.get('price', 0)) * int(item.get('quantity', 1)) for item in items)
        discount_total = max(0, item_subtotal - total)
    discount_total = max(0.0, float(discount_total))

    sum_line_discounts = _sum_invoice_line_discounts(items)
    order_discount = float(data.get('order_discount', 0) or 0)
    if order_discount <= 0.001:
        order_discount = max(0.0, discount_total - sum_line_discounts)

    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)
    if not items:
        return Response({'error': 'Items are required'}, status=status.HTTP_400_BAD_REQUEST)

    logger.info(f"📧 Sending unpaid invoice to {email} for {customer_name}, ref={invoice_ref}, total=${total:.2f}")

    try:
        # Resolve contact if customer_id provided
        contact = None
        if customer_id:
            try:
                contact = Contact.objects.get(pk=customer_id)
            except Contact.DoesNotExist:
                logger.warning(f"Contact {customer_id} not found for invoice {invoice_ref}")

        # Persist the invoice record
        invoice = Invoice(
            invoice_ref=invoice_ref,
            contact=contact,
            email=email,
            items=items,
            subtotal=subtotal,
            total=total,
            status='pending',
            created_by=request.user if request.user.is_authenticated else None,
        )
        token, expires_at = generate_invoice_token(str(invoice.pk))
        invoice.payment_token = token
        invoice.token_expires_at = expires_at
        invoice.save()
        logger.info(f"📧 Invoice {invoice_ref} persisted (id={invoice.pk}), token expires {expires_at}")

        # Build the Pay Now URL
        frontend_url = os.environ.get('FRONTEND_URL', 'https://pos.doctorsstudio.com')
        pay_now_url = f"{frontend_url}/pay/invoice/{token}"

        total_tax = float(data.get('total_tax', 0) or 0)

        html = _build_unpaid_invoice_email_html(
            customer_name,
            email,
            items,
            subtotal,
            total,
            invoice_ref,
            order_discount,
            pay_now_url=None,
            discount_total=discount_total,
            total_tax=total_tax,
        )

        # Generate PDF attachment
        pdf_html = _build_pdf_invoice_html(
            customer_name,
            items,
            subtotal,
            total,
            invoice_ref,
            order_discount,
            discount_total=discount_total,
            total_tax=total_tax,
        )
        pdf_bytes = _html_to_pdf(pdf_html)
        attachments = []
        if pdf_bytes:
            attachments.append((f'Invoice_{invoice_ref}.pdf', pdf_bytes, 'application/pdf'))
        else:
            logger.warning(f'PDF generation failed for invoice {invoice_ref}, sending email without attachment')

        from .email_sender import send_pos_email
        success = send_pos_email(
            subject=f'Invoice from {BUSINESS_NAME} — {invoice_ref}',
            text_content=f'Invoice {invoice_ref} for ${total:.2f}. Payment is required to complete your purchase.',
            html_content=html,
            to_email=email,
            email_type='invoice',
            attachments=attachments or None,
            related_order=invoice_ref,
        )

        if success:
            return Response({'status': 'sent', 'invoice_id': str(invoice.pk)})
        else:
            return Response({'error': 'Failed to send invoice email'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"📧 Error sending invoice email to {email}: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _build_unpaid_invoice_email_html(customer_name, customer_email, items, subtotal, total, invoice_ref, order_discount=0, pay_now_url=None, discount_total=None, total_tax=0):
    """Build HTML for an unpaid invoice email (tabular line items + summary)."""
    now_str = datetime.now().strftime('%b %d, %Y at %I:%M %p')
    if discount_total is None:
        discount_total = max(0.0, float(subtotal) - float(total))
    total_tax = float(total_tax or 0)

    display_amounts, merged_order_disc = _invoice_display_amounts_per_line(items, order_discount)

    # Build totals rows aligned to the tabular table columns
    disc_color_unpaid = '#059669' if discount_total > 0 else TEXT_SECONDARY
    disc_display_unpaid = f'-${discount_total:.2f}' if discount_total > 0 else ''
    show_tax_col = _invoice_items_have_line_tax_column(items)
    num_cols = 6 if show_tax_col else 5
    tax_row_email = ''
    if total_tax > 0.005:
        tax_row_email = f'''
        <tr>
            <td colspan="{num_cols - 1}" style="padding:4px 0;font-size:13px;color:{TEXT_SECONDARY};">Tax</td>
            <td align="right" style="padding:4px 0;font-size:13px;color:{TEXT_PRIMARY};">${total_tax:.2f}</td>
        </tr>'''
    totals_html = f'''
        <tr>
            <td colspan="2" style="padding:8px 0;font-size:13px;color:{TEXT_SECONDARY};">Subtotal</td>
            <td align="right" style="padding:8px 4px;font-size:13px;color:{TEXT_PRIMARY};">${subtotal:.2f}</td>
            <td align="right" style="padding:8px 4px;font-size:13px;color:{disc_color_unpaid};">{disc_display_unpaid}</td>
            {"<td></td>" if show_tax_col else ""}
            <td align="right" style="padding:8px 0;font-size:13px;color:{TEXT_PRIMARY};font-weight:700;">${total:.2f}</td>
        </tr>{tax_row_email}
        <tr>
            <td colspan="{num_cols - 1}" style="padding:10px 0 4px;font-size:15px;font-weight:700;color:{TEXT_PRIMARY};border-top:2px solid {TEXT_PRIMARY};">Total</td>
            <td align="right" style="padding:10px 0 4px;font-size:15px;font-weight:700;color:{TEXT_PRIMARY};border-top:2px solid {TEXT_PRIMARY};">${total:.2f}</td>
        </tr>
        <tr>
            <td colspan="{num_cols - 1}" style="padding:4px 0;font-size:13px;color:#dc2626;font-weight:600;">Balance Due</td>
            <td align="right" style="padding:4px 0;font-size:13px;color:#dc2626;font-weight:600;">${total:.2f}</td>
        </tr>'''

    items_table_html, _ = _build_invoice_tabular_table_html(
        items, display_amounts, order_discount, merged_order_disc, pdf=False, extra_rows=totals_html
    )

    # Payment section — Pending
    payment_html = f'''
        <tr>
            <td style="padding:6px 0;color:{TEXT_PRIMARY};font-size:13px;">Pending</td>
            <td style="padding:6px 0;text-align:right;font-size:13px;font-weight:600;">${total:.2f}</td>
        </tr>'''

    # Pay Now CTA button (only if URL provided)
    if pay_now_url:
        pay_now_block = f'''<tr><td style="padding:8px 32px 20px;text-align:center;">
        <table cellpadding="0" cellspacing="0" style="margin:0 auto;">
            <tr><td style="border-radius:8px;background:#16a34a;" align="center">
                <a href="{pay_now_url}" target="_blank" style="display:inline-block;padding:14px 48px;font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;font-family:Arial,Helvetica,sans-serif;">
                    Pay Now &mdash; ${total:.2f}
                </a>
            </td></tr>
        </table>
        <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:10px;">Secure payment &bull; Link expires in 24 hours</div>
    </td></tr>'''
    else:
        pay_now_block = ''

    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>Invoice — {invoice_ref}</title></head>
<body style="font-family:Arial,Helvetica,sans-serif;margin:0;padding:24px 0;font-size:12px;line-height:1.4;background-color:#f3f4f6;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">

    <!-- Logo -->
    <tr><td style="padding:24px 32px 16px;text-align:center;background:#ffffff;">
        <img src="{LOGO_URL}" alt="{BUSINESS_NAME}" style="max-width:220px;height:auto;" />
    </td></tr>

    <!-- Header bar -->
    <tr><td style="background:{BRAND_COLOR};padding:20px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
            <tr>
                <td><div style="font-size:14px;font-weight:600;color:rgba(255,255,255,0.9);">Invoice</div></td>
                <td style="text-align:right;">
                    <div style="font-size:12px;color:rgba(255,255,255,0.85);">{invoice_ref}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.7);margin-top:2px;">{now_str}</div>
                </td>
            </tr>
        </table>
    </td></tr>

    <!-- Greeting -->
    <tr><td style="padding:24px 32px 16px;">
        <div style="font-size:15px;color:{TEXT_PRIMARY};">Hi {customer_name},</div>
        <div style="font-size:13px;color:{TEXT_SECONDARY};margin-top:6px;">Thank you for your order! Here is your invoice. Payment is required to complete your purchase.</div>
    </td></tr>

    <!-- Items table -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Order Items</div>
            </td></tr>
            <tr><td style="padding:4px 16px 12px;">
                {items_table_html}
            </td></tr>
        </table>
    </td></tr>

    <!-- Payment section -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDER_COLOR};border-radius:6px;overflow:hidden;">
            <tr><td style="background:{BRAND_LIGHT};padding:10px 16px;border-bottom:1px solid {BORDER_COLOR};">
                <div style="font-size:12px;font-weight:700;color:{BRAND_COLOR};text-transform:uppercase;letter-spacing:0.5px;">Payment</div>
            </td></tr>
            <tr><td style="padding:10px 16px;">
                <table width="100%" cellpadding="0" cellspacing="0">
                    {payment_html}
                </table>
            </td></tr>
        </table>
    </td></tr>

    <!-- Pay Now CTA -->
    {pay_now_block}

    <!-- Help -->
    <tr><td style="padding:0 32px 16px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f9fafb;border-radius:6px;">
            <tr><td style="padding:14px 16px;text-align:center;">
                <div style="font-size:12px;color:{TEXT_SECONDARY};margin-bottom:4px;">Questions about your invoice?</div>
                <div style="font-size:13px;color:{TEXT_PRIMARY};font-weight:600;">Call us at {BUSINESS_PHONE}</div>
                <div style="font-size:11px;color:{TEXT_SECONDARY};margin-top:4px;">or email us at support@doctorsstudio.com</div>
            </td></tr>
        </table>
    </td></tr>

    <!-- Footer -->
    <tr><td style="padding:16px 32px;border-top:1px solid {BORDER_COLOR};text-align:center;">
        <div style="font-size:11px;font-weight:700;color:{TEXT_PRIMARY};margin-bottom:4px;">Payment Required</div>
        <div style="font-size:10px;color:{TEXT_SECONDARY};">Payment is due upon receipt of this invoice.</div>
        <div style="font-size:10px;color:{TEXT_PRIMARY};margin-top:12px;font-weight:500;">Thank you for your business!</div>
    </td></tr>
    <tr><td style="background:#f9fafb;padding:12px 32px;text-align:center;">
        <div style="font-size:10px;color:{TEXT_MUTED};">{BUSINESS_NAME} &bull; {BUSINESS_ADDRESS_1}, {BUSINESS_ADDRESS_2}, {BUSINESS_CITY_STATE}</div>
        <div style="font-size:10px;color:{TEXT_MUTED};margin-top:2px;">{BUSINESS_PHONE} &bull; {BUSINESS_WEBSITE}</div>
    </td></tr>

</table>
</td></tr></table>
</body></html>'''
