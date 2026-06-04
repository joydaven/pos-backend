# DS POS WooCommerce Pricing Fix Snippets

This directory contains WordPress/WooCommerce snippets to prevent ATUM and WooCommerce from recalculating product prices when orders are created from the DS POS system.

## Files

### 1. `membership-order-price-fix.php` 
**Purpose:** Preserves $0 pricing for trial membership products
**Usage:** Handles trial memberships (products marked with `isTrialMembership: true`)
**Detection:** Looks for `_trial_membership: 'true'` metadata on order line items
**Effect:** Forces price to $0.00 for trial memberships

### 2. `membership-modified-pricing-fix.php` (NEW)
**Purpose:** Preserves modified/discounted pricing for ALL products (not just memberships)
**Usage:** Handles ANY product where price has been manually changed during checkout
**Detection:** Looks for metadata:
- `_ds_pos_original_price` - Original product price
- `_ds_pos_modified_price` - Modified/discounted price  
- `_ds_pos_discount_amount` - Discount amount
- `_modified_membership_pricing: 'true'` - Flag for modified pricing

**Effect:** Preserves the modified price instead of letting ATUM recalculate to original price

## How It Works

### Backend (Django)
The DS POS backend automatically adds the required metadata when creating WooCommerce orders:

**For Trial Memberships:**
```php
$item->add_meta_data('_trial_membership', 'true');
$item->add_meta_data('_actual_membership_price', $original_price);
```

**For Modified Pricing:**
```php
$item->add_meta_data('_ds_pos_original_price', '499.00');
$item->add_meta_data('_ds_pos_modified_price', '299.00'); 
$item->add_meta_data('_ds_pos_discount_amount', '200.00');
$item->add_meta_data('_modified_membership_pricing', 'true');
```

### Frontend (WordPress)
The WordPress snippets use multiple aggressive hooks to prevent price recalculation:

1. **Order Creation Hooks:** `woocommerce_checkout_create_order_line_item`, `woocommerce_rest_insert_shop_order_object`
2. **Calculation Hooks:** `woocommerce_order_before_calculate_totals`, `woocommerce_order_after_calculate_totals` 
3. **Display Hooks:** `woocommerce_order_get_items`, `woocommerce_order_item_get_subtotal`, `woocommerce_order_item_get_total`
4. **Product Price Hooks:** `woocommerce_product_get_price`, `woocommerce_product_variation_get_price`
5. **Subscription Hooks:** `woocommerce_subscriptions_product_sign_up_fee`

## Installation

1. **Add to functions.php:** Copy the contents of both PHP files to your theme's `functions.php` file
2. **OR Create Plugin:** Create a custom plugin with both snippets
3. **Verify:** Check WordPress admin for success notices confirming the hooks are loaded

## Example Scenarios

### Trial Membership
- **POS:** User orders "Doctors Studio Monthly Membership" with trial option
- **Frontend Price:** $0.00 (trial period)  
- **WooCommerce:** Receives metadata `_trial_membership: 'true'`
- **Result:** WordPress snippet forces $0.00, prevents ATUM recalculation to $499.00

### Discounted Membership  
- **POS:** User orders "Doctors Studio Monthly Membership" at $499.00, applies $200 discount
- **Frontend Price:** $299.00 (discounted)
- **WooCommerce:** Receives metadata `_ds_pos_modified_price: '299.00'`
- **Result:** WordPress snippet preserves $299.00, prevents ATUM recalculation to $499.00

## Debugging

Both snippets include extensive error logging. Check WordPress error logs for:
```
DS POS: Processing trial membership pricing for order 123456
DS POS: Updated trial membership item 1 to $0 (actual price: $499.00)
DS POS: Processing modified membership pricing for order 123456  
DS POS: Updated modified membership item 2 to $299 (original: $499.00, discount: $200.00)
```

## Compatibility

- **WooCommerce:** 3.0+
- **WooCommerce Subscriptions:** 2.0+
- **ATUM Inventory Management:** All versions
- **WordPress:** 5.0+

## Notes

- Both snippets can coexist - they handle different scenarios
- Trial memberships take priority over modified pricing 
- Only affects orders created via DS POS (`created_via: 'DS POS'` or similar metadata)
- Does not affect regular WooCommerce checkout orders

---

### `ds-refund-receipt-pdf.php`
**Purpose:** Attaches a professional refund receipt PDF to WooCommerce's built-in refund notification emails.

**How it works:**
- Hooks into `woocommerce_email_attachments` filter targeting `customer_refunded_order` and `customer_partially_refunded_order` emails
- Generates a PDF using DOMPDF with the same design as the POS refund receipt (amber header, refund details, items table, refund destination, footer)
- Auto-detects DOMPDF from: WooCommerce PDF Invoices & Packing Slips plugin, Composer autoload, or standalone install
- Temp PDF files are cleaned up after email is sent

**Requires:** DOMPDF — install one of:
1. "WooCommerce PDF Invoices & Packing Slips" plugin (recommended, includes DOMPDF)
2. `composer require dompdf/dompdf` in the theme or project root
3. Manual DOMPDF install in `wp-content/dompdf/`

**Installation:**
1. Add to theme's `functions.php`, OR
2. Upload to `wp-content/mu-plugins/ds-refund-receipt-pdf.php`, OR
3. Add via Code Snippets plugin on Kinsta

**Debugging:** Check WordPress error logs for:
```
DS Refund Receipt PDF: Generated PDF for order #290665
DS Refund Receipt PDF: DOMPDF not available...
DS Refund Receipt PDF: Error generating PDF - ...
```

---

### `ds-tracking-email-trigger.php`
**Purpose:** Bridges WooCommerce admin-added shipment tracking to the Django POS backend for branded email delivery. Also disables AST Pro's built-in partial-shipped email to prevent duplicates.

**How it works:**
- Intercepts WC Shipment Tracking AJAX save handler and fires a webhook to Django POS
- Sets transient flags to prevent double-sending when tracking is created via POS
- Monitors order meta changes as a fallback for REST API-added tracking
- **Disables** AST Pro's `customer_shipped_order` and `customer_partial_shipped_order` emails — Django POS now sends both email types directly via `send_tracking_email()` and `send_partial_shipped_email()`

**Django POS email handling:**
- `status_shipped = "shipped"` → `send_tracking_email()` — subject: "Your Order Has Shipped!"
- `status_shipped = "partial"` → `send_partial_shipped_email()` — subject: "Your Order is Partially Shipped", shows shipped items + awaiting shipment items

**Installation:**
1. Add to theme's `functions.php`, OR
2. Upload to `wp-content/mu-plugins/ds-tracking-email-trigger.php`, OR
3. Add via Code Snippets plugin on Kinsta

**Environment:** Define in `wp-config.php`:
```php
define('DS_POS_API_URL', 'https://pos.doctorsstudio.com');
define('DS_WEBHOOK_SECRET', 'ds-tracking-webhook-2026');
```
