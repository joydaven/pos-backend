<?php
/**
 * Plugin Name: DS Refund Receipt PDF Attachment
 * Description: Attaches a professional refund receipt PDF to WooCommerce's built-in refund notification emails.
 * Version: 1.0
 * Author: DS POS Team
 *
 * Installation:
 *   - Add to theme's functions.php, OR
 *   - Upload to wp-content/mu-plugins/, OR
 *   - Add via Code Snippets plugin
 *
 * Requires: DOMPDF (auto-detected from WooCommerce PDF Invoices & Packing Slips plugin,
 *           Composer autoload, or bundled in wp-content/dompdf/).
 */

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Disable WooCommerce's built-in refund email notifications.
 *
 * The POS backend handles sending refund receipt emails (with PDF attachment)
 * via views_refunds.py → views_receipts.py, so WooCommerce's own refund
 * emails are redundant and would cause the customer to receive duplicates.
 *
 * This disables both 'customer_refunded_order' (full) and
 * 'customer_partially_refunded_order' (partial) email types.
 */
add_filter('woocommerce_email_enabled_customer_refunded_order', '__return_false');
add_filter('woocommerce_email_enabled_customer_partially_refunded_order', '__return_false');

/**
 * Attach refund receipt PDF to WooCommerce refund emails.
 *
 * NOTE: Currently inactive because WooCommerce refund emails are disabled above.
 * Kept as backup in case refund emails need to be re-enabled for WC-admin refunds.
 *
 * Hooks into both full and partial refund email types.
 */
// add_filter('woocommerce_email_attachments', 'ds_attach_refund_receipt_pdf', 10, 4);

function ds_attach_refund_receipt_pdf($attachments, $email_id, $order, $email_obj = null) {
    // Only target refund emails
    $refund_email_ids = array(
        'customer_refunded_order',
        'customer_partially_refunded_order',
    );

    if (!in_array($email_id, $refund_email_ids, true)) {
        return $attachments;
    }

    if (!$order || !is_a($order, 'WC_Order')) {
        return $attachments;
    }

    // Try to generate the PDF
    $pdf_path = ds_generate_refund_receipt_pdf($order);

    if ($pdf_path && file_exists($pdf_path)) {
        $attachments[] = $pdf_path;
        // Schedule cleanup after email is sent
        add_action('woocommerce_email_sent', function() use ($pdf_path) {
            if (file_exists($pdf_path)) {
                @unlink($pdf_path);
            }
        });
        // Fallback cleanup after 5 minutes in case the hook doesn't fire
        wp_schedule_single_event(time() + 300, 'ds_cleanup_temp_pdf', array($pdf_path));
    }

    return $attachments;
}

/**
 * Cleanup scheduled temp PDF files.
 */
add_action('ds_cleanup_temp_pdf', 'ds_cleanup_temp_pdf_file');
function ds_cleanup_temp_pdf_file($pdf_path) {
    if (file_exists($pdf_path)) {
        @unlink($pdf_path);
    }
}

/**
 * Generate the refund receipt PDF for a WooCommerce order.
 *
 * @param WC_Order $order
 * @return string|false Path to the generated PDF file, or false on failure.
 */
function ds_generate_refund_receipt_pdf($order) {
    $dompdf = ds_get_dompdf_instance();
    if (!$dompdf) {
        error_log('DS Refund Receipt PDF: DOMPDF not available. Install WooCommerce PDF Invoices & Packing Slips plugin or add DOMPDF via Composer.');
        return false;
    }

    $html = ds_build_refund_receipt_html($order);

    try {
        $dompdf->loadHtml($html);
        $dompdf->setPaper('letter', 'portrait');
        $dompdf->render();

        $pdf_content = $dompdf->output();

        // Write to temp file
        $upload_dir = wp_upload_dir();
        $temp_dir = trailingslashit($upload_dir['basedir']) . 'ds-temp-pdfs/';
        if (!file_exists($temp_dir)) {
            wp_mkdir_p($temp_dir);
            // Add .htaccess to prevent direct access
            file_put_contents($temp_dir . '.htaccess', 'deny from all');
        }

        $filename = 'Refund_Receipt_Order_' . $order->get_order_number() . '_' . time() . '.pdf';
        $pdf_path = $temp_dir . $filename;

        file_put_contents($pdf_path, $pdf_content);

        error_log('DS Refund Receipt PDF: Generated PDF for order #' . $order->get_order_number());
        return $pdf_path;

    } catch (Exception $e) {
        error_log('DS Refund Receipt PDF: Error generating PDF - ' . $e->getMessage());
        return false;
    }
}

/**
 * Try to get a DOMPDF instance from available sources.
 *
 * @return Dompdf\Dompdf|false
 */
function ds_get_dompdf_instance() {
    // 1. Check if DOMPDF class already exists (loaded by another plugin)
    if (class_exists('Dompdf\\Dompdf')) {
        return new Dompdf\Dompdf(array(
            'isRemoteEnabled' => false,
            'isHtml5ParserEnabled' => true,
        ));
    }

    // 2. Try WooCommerce PDF Invoices & Packing Slips plugin path
    $wpo_paths = array(
        WP_PLUGIN_DIR . '/woocommerce-pdf-invoices-packing-slips/vendor/autoload.php',
        WP_PLUGIN_DIR . '/woocommerce-pdf-invoices-packing-slips/lib/dompdf/autoload.inc.php',
    );
    foreach ($wpo_paths as $path) {
        if (file_exists($path)) {
            require_once $path;
            if (class_exists('Dompdf\\Dompdf')) {
                return new Dompdf\Dompdf(array(
                    'isRemoteEnabled' => false,
                    'isHtml5ParserEnabled' => true,
                ));
            }
        }
    }

    // 3. Try Composer autoload in theme or project root
    $composer_paths = array(
        get_stylesheet_directory() . '/vendor/autoload.php',
        ABSPATH . 'vendor/autoload.php',
        WP_CONTENT_DIR . '/vendor/autoload.php',
    );
    foreach ($composer_paths as $path) {
        if (file_exists($path)) {
            require_once $path;
            if (class_exists('Dompdf\\Dompdf')) {
                return new Dompdf\Dompdf(array(
                    'isRemoteEnabled' => false,
                    'isHtml5ParserEnabled' => true,
                ));
            }
        }
    }

    // 4. Try standalone DOMPDF in wp-content
    $standalone = WP_CONTENT_DIR . '/dompdf/autoload.inc.php';
    if (file_exists($standalone)) {
        require_once $standalone;
        if (class_exists('Dompdf\\Dompdf')) {
            return new Dompdf\Dompdf(array(
                'isRemoteEnabled' => false,
                'isHtml5ParserEnabled' => true,
            ));
        }
    }

    return false;
}

/**
 * Build the refund receipt HTML for PDF generation.
 * Mirrors the design from the POS backend _build_pdf_refund_receipt_html().
 * Uses only inline styles for DOMPDF compatibility.
 *
 * @param WC_Order $order
 * @return string HTML content
 */
function ds_build_refund_receipt_html($order) {
    // Business constants
    $business_name    = 'Doctors Studio';
    $business_addr1   = '2595 NW Boca Raton Blvd';
    $business_addr2   = 'Suite 200';
    $business_city    = 'Boca Raton, FL 33431';
    $business_phone   = '(561) 444-7751';
    $business_website = 'www.doctorsstudio.com';

    // Order data
    $order_number   = $order->get_order_number();
    $order_date     = $order->get_date_created() ? $order->get_date_created()->date('M d, Y') : '';
    $customer_name  = trim($order->get_billing_first_name() . ' ' . $order->get_billing_last_name());
    $customer_email = $order->get_billing_email();
    $now_str        = current_time('M d, Y \a\t g:i A');

    if (empty($customer_name)) {
        $customer_name = 'Valued Customer';
    }

    // Payment method display
    $payment_display = ds_get_payment_display($order);

    // Expected refund date (5 business days)
    $expected_date = new DateTime(current_time('Y-m-d'));
    $bdays = 5;
    while ($bdays > 0) {
        $expected_date->modify('+1 day');
        if ($expected_date->format('N') < 6) { // Mon-Fri
            $bdays--;
        }
    }
    $expected_refund_date = $expected_date->format('M d, Y');

    // Get refund data
    $refunds = $order->get_refunds();
    $total_refund_amount = 0;
    $refund_reasons = array();
    $refunded_items_map = array(); // Track actually refunded items by product_id

    foreach ($refunds as $refund) {
        $total_refund_amount += abs(floatval($refund->get_amount()));
        $reason = $refund->get_reason();
        if (!empty($reason)) {
            $refund_reasons[] = $reason;
        }
        // Collect per-item refund data
        foreach ($refund->get_items() as $refund_item) {
            $refund_qty = abs(intval($refund_item->get_quantity()));
            $refund_total = abs(floatval($refund_item->get_total()));
            if ($refund_qty == 0 && $refund_total == 0) {
                continue;
            }
            $ref_product_id = $refund_item->get_product_id();
            $ref_name = $refund_item->get_name();
            $key = $ref_product_id ? strval($ref_product_id) : $ref_name;
            if (isset($refunded_items_map[$key])) {
                $refunded_items_map[$key]['qty'] += $refund_qty;
                $refunded_items_map[$key]['total'] += $refund_total;
            } else {
                $ref_product = $refund_item->get_product();
                $refunded_items_map[$key] = array(
                    'name' => $ref_name,
                    'qty' => $refund_qty,
                    'total' => $refund_total,
                    'sku' => $ref_product ? $ref_product->get_sku() : '',
                    'product' => $ref_product,
                );
            }
        }
    }

    // If no refund amount found but order is refunded, use order total
    if ($total_refund_amount == 0 && $order->get_status() === 'refunded') {
        $total_refund_amount = floatval($order->get_total());
    }

    // Determine full vs partial refund
    $order_total = floatval($order->get_total());
    $is_full_refund = ($order->get_status() === 'refunded') || ($order_total > 0 && abs($total_refund_amount - $order_total) < 0.01);
    $refund_heading = $is_full_refund ? 'Order Refunded' : 'Partial Refund';
    $refund_intro = $is_full_refund
        ? 'We have processed a full refund for your order #' . esc_html($order_number) . '.'
        : 'We have processed a partial refund for your order #' . esc_html($order_number) . '.';

    // Build line items HTML — use only refunded items for partial refunds
    $items_rows = '';
    if ($is_full_refund || empty($refunded_items_map)) {
        // Full refund: show all order items
        foreach ($order->get_items() as $item) {
            $product  = $item->get_product();
            $name     = $item->get_name();
            $qty      = $item->get_quantity();
            $sku      = $product ? $product->get_sku() : '';
            $total    = floatval($item->get_total());
            $sku_text = !empty($sku) ? ' | SKU: ' . esc_html($sku) : '';

            $sub_badge = '';
            if (class_exists('WC_Subscriptions_Product') && $product && WC_Subscriptions_Product::is_subscription($product)) {
                $period   = WC_Subscriptions_Product::get_period($product);
                $interval = WC_Subscriptions_Product::get_interval($product);
                $freq     = ds_format_subscription_frequency($period, $interval);
                $freq_label = !empty($freq) ? ' &mdash; ' . esc_html($freq) : '';
                $sub_badge = '<br><span style="color:#7c3aed;font-size:9px;font-weight:bold;">SUBSCRIPTION' . $freq_label . '</span>';
            }

            $items_rows .= '
            <tr>
                <td style="padding:8px 8px 8px 0;border-bottom:1px solid #e5e7eb;">
                    <span style="font-size:11px;font-weight:bold;color:#1f2937;">' . esc_html($name) . '</span>' . $sub_badge . '
                    <br><span style="font-size:9px;color:#6b7280;">Qty: ' . intval($qty) . $sku_text . '</span>
                </td>
                <td style="padding:8px 0;border-bottom:1px solid #e5e7eb;text-align:right;vertical-align:top;">
                    <span style="font-size:11px;font-weight:bold;color:#92400e;">-$' . number_format($total, 2) . '</span>
                </td>
            </tr>';
        }
    } else {
        // Partial refund: show only actually refunded items
        foreach ($refunded_items_map as $ri) {
            $name     = $ri['name'];
            $qty      = $ri['qty'];
            $sku      = $ri['sku'];
            $total    = $ri['total'];
            $product  = $ri['product'];
            $sku_text = !empty($sku) ? ' | SKU: ' . esc_html($sku) : '';

            $sub_badge = '';
            if (class_exists('WC_Subscriptions_Product') && $product && WC_Subscriptions_Product::is_subscription($product)) {
                $period   = WC_Subscriptions_Product::get_period($product);
                $interval = WC_Subscriptions_Product::get_interval($product);
                $freq     = ds_format_subscription_frequency($period, $interval);
                $freq_label = !empty($freq) ? ' &mdash; ' . esc_html($freq) : '';
                $sub_badge = '<br><span style="color:#7c3aed;font-size:9px;font-weight:bold;">SUBSCRIPTION' . $freq_label . '</span>';
            }

            $items_rows .= '
            <tr>
                <td style="padding:8px 8px 8px 0;border-bottom:1px solid #e5e7eb;">
                    <span style="font-size:11px;font-weight:bold;color:#1f2937;">' . esc_html($name) . '</span>' . $sub_badge . '
                    <br><span style="font-size:9px;color:#6b7280;">Qty: ' . intval($qty) . $sku_text . '</span>
                </td>
                <td style="padding:8px 0;border-bottom:1px solid #e5e7eb;text-align:right;vertical-align:top;">
                    <span style="font-size:11px;font-weight:bold;color:#92400e;">-$' . number_format($total, 2) . '</span>
                </td>
            </tr>';
        }
    }

    // Refund reasons rows
    $refund_reasons_rows = '';
    foreach ($refund_reasons as $reason) {
        $refund_reasons_rows .= '
        <tr>
            <td colspan="2" style="padding:4px 0;font-size:9px;color:#6b7280;font-style:italic;">
                Reason: ' . esc_html($reason) . '
            </td>
        </tr>';
    }

    // Customer info rows
    $customer_row = '';
    if (!empty($customer_name) && $customer_name !== 'Valued Customer') {
        $customer_row = '
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Customer</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">' . esc_html($customer_name) . '</td>
                    </tr>';
    }
    $email_row = '';
    if (!empty($customer_email)) {
        $email_row = '
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Email</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;">' . esc_html($customer_email) . '</td>
                    </tr>';
    }

    // Build the full HTML
    $html = '<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
    @page { size: letter; margin: 1cm; }
    body { font-family: Helvetica, Arial, sans-serif; font-size: 11px; color: #1f2937; margin: 0; padding: 0; }
    table { border-collapse: collapse; }
</style>
</head>
<body>
    <!-- Header bar -->
    <table width="100%" style="background:#92400e;margin-bottom:16px;">
        <tr>
            <td style="padding:18px 20px;">
                <span style="font-size:18px;font-weight:bold;color:#ffffff;">' . esc_html($business_name) . '</span><br>
                <span style="font-size:10px;color:#ffffffd9;">' . esc_html($refund_heading) . '</span>
            </td>
            <td style="padding:18px 20px;text-align:right;">
                <span style="font-size:10px;color:#ffffffd9;">Order #' . esc_html($order_number) . '</span><br>
                <span style="font-size:9px;color:#ffffffb3;">' . esc_html($order_date) . '</span>
            </td>
        </tr>
    </table>

    <!-- Greeting -->
    <p style="font-size:12px;color:#1f2937;margin:0 0 4px 0;">Hi <strong>' . esc_html($customer_name) . '</strong>,</p>
    <p style="font-size:10px;color:#6b7280;margin:0 0 14px 0;line-height:1.5;">
        ' . $refund_intro . ' This is <strong>not a new charge</strong> &mdash;
        the amount below will be returned to your original payment method.
    </p>

    <!-- Refund Details -->
    <table width="100%" style="border:1px solid #e5e7eb;margin-bottom:14px;">
        <tr><td colspan="2" style="background:#fffbeb;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#92400e;text-transform:uppercase;letter-spacing:1px;">REFUND DETAILS</span>
        </td></tr>
        <tr><td colspan="2" style="padding:10px 12px;">
            <table width="100%">
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Order Number</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">#' . esc_html($order_number) . '</td>
                    </tr>
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Original Purchase</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;">' . esc_html($order_date) . '</td>
                    </tr>
                    <tr>
                        <td style="padding:3px 0;font-size:10px;color:#6b7280;">Refund Processed</td>
                        <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;">' . esc_html($now_str) . '</td>
                    </tr>
                    ' . $customer_row . '
                    ' . $email_row . '
            </table>
        </td></tr>
    </table>

    <!-- Refunded Items -->
    <table width="100%" style="border:1px solid #e5e7eb;margin-bottom:14px;">
        <tr><td colspan="2" style="background:#fffbeb;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#92400e;text-transform:uppercase;letter-spacing:1px;">REFUNDED ITEMS</span>
        </td></tr>
        <tr><td colspan="2" style="padding:4px 12px 6px;">
            <table width="100%">
                ' . $items_rows . '
                ' . $refund_reasons_rows . '
            </table>
        </td></tr>
        <tr><td colspan="2" style="padding:10px 12px;border-top:2px solid #92400e;background:#fffbeb;">
            <table width="100%">
                <tr>
                    <td style="font-size:13px;font-weight:bold;color:#92400e;">Refund Total</td>
                    <td style="text-align:right;font-size:15px;font-weight:bold;color:#92400e;">-$' . number_format($total_refund_amount, 2) . '</td>
                </tr>
            </table>
        </td></tr>
    </table>

    <!-- Refund Destination -->
    <table width="100%" style="border:1px solid #e5e7eb;margin-bottom:14px;">
        <tr><td style="background:#f0fdf4;padding:8px 12px;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:10px;font-weight:bold;color:#166534;text-transform:uppercase;letter-spacing:1px;">REFUND DESTINATION</span>
        </td></tr>
        <tr><td style="padding:10px 12px;">
            <table width="100%">
                <tr>
                    <td style="padding:3px 0;font-size:10px;color:#6b7280;">Refunded to</td>
                    <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">' . esc_html($payment_display) . '</td>
                </tr>
                <tr>
                    <td style="padding:3px 0;font-size:10px;color:#6b7280;">Expected by</td>
                    <td style="padding:3px 0;text-align:right;font-size:10px;color:#1f2937;font-weight:bold;">' . esc_html($expected_refund_date) . '</td>
                </tr>
            </table>
        </td></tr>
    </table>

    <!-- Help -->
    <table width="100%" style="background:#f9fafb;margin-bottom:14px;">
        <tr><td style="padding:10px 12px;text-align:center;">
            <span style="font-size:10px;color:#6b7280;">Questions about your refund?</span><br>
            <span style="font-size:11px;color:#1f2937;font-weight:bold;">Call us at ' . esc_html($business_phone) . '</span>
        </td></tr>
    </table>

    <!-- Footer -->
    <table width="100%" style="border-top:1px solid #e5e7eb;">
        <tr><td style="padding:10px 0;text-align:center;">
            <span style="font-size:9px;color:#6b7280;">Please allow 3-5 business days for the refund to appear on your statement.</span><br>
            <span style="font-size:9px;color:#1f2937;font-weight:bold;">Thank you for your business!</span>
        </td></tr>
        <tr><td style="background:#f9fafb;padding:8px 0;text-align:center;">
            <span style="font-size:8px;color:#9ca3af;">' . esc_html($business_name) . ' &bull; ' . esc_html($business_addr1) . ', ' . esc_html($business_addr2) . ', ' . esc_html($business_city) . '</span><br>
            <span style="font-size:8px;color:#9ca3af;">' . esc_html($business_phone) . ' &bull; ' . esc_html($business_website) . '</span>
        </td></tr>
    </table>
</body>
</html>';

    return $html;
}

/**
 * Get a human-readable payment method display string.
 *
 * @param WC_Order $order
 * @return string
 */
function ds_get_payment_display($order) {
    // Check if the most recent refund was via points
    $refunds = $order->get_refunds();
    if (!empty($refunds)) {
        $latest_refund = $refunds[0]; // Most recent refund
        // Check refund meta for points indicator (set by POS)
        $refund_as_points = $latest_refund->get_meta('_refund_as_points');
        if ($refund_as_points) {
            return 'YITH Points (Store Credit)';
        }
        // Also check the refund reason for "points" keyword as fallback
        $reason = strtolower($latest_refund->get_reason());
        if (strpos($reason, 'points') !== false || strpos($reason, 'store credit') !== false) {
            return 'YITH Points (Store Credit)';
        }
    }

    $method_title = $order->get_payment_method_title();

    // Check for POS payment metadata
    $pos_payment = $order->get_meta('_pos_payment_method');
    if (!empty($pos_payment) && is_array($pos_payment)) {
        if (!empty($pos_payment['brand']) && !empty($pos_payment['last4'])) {
            return $pos_payment['brand'] . ' ending in ' . $pos_payment['last4'];
        }
        if (!empty($pos_payment['type']) && !empty($pos_payment['last4'])) {
            return $pos_payment['type'] . ' ending in ' . $pos_payment['last4'];
        }
        if (!empty($pos_payment['name'])) {
            return $pos_payment['name'];
        }
    }

    // Check for Stripe/card metadata
    $card_brand = $order->get_meta('_stripe_card_brand');
    $card_last4 = $order->get_meta('_stripe_card_last4');
    if (!empty($card_brand) && !empty($card_last4)) {
        return ucfirst($card_brand) . ' ending in ' . $card_last4;
    }

    // Mask "Split Payment (Cash)" style names
    if (!empty($method_title)) {
        $lower = strtolower($method_title);
        if (strpos($lower, 'split') !== false) {
            return 'Cash';
        }
        if (strpos($lower, 'outside') !== false && strpos($lower, 'pos') !== false) {
            return 'Other';
        }
        return $method_title;
    }

    return 'Original payment method';
}

/**
 * Format subscription frequency for display.
 *
 * @param string $period
 * @param string|int $interval
 * @return string
 */
function ds_format_subscription_frequency($period, $interval) {
    $interval = intval($interval);
    if ($interval <= 0) {
        $interval = 1;
    }

    $period_map = array(
        'day'   => array('Daily', 'Every %d days'),
        'week'  => array('Weekly', 'Every %d weeks'),
        'month' => array('Monthly', 'Every %d months'),
        'year'  => array('Yearly', 'Every %d years'),
    );

    if (isset($period_map[$period])) {
        if ($interval === 1) {
            return $period_map[$period][0];
        }
        return sprintf($period_map[$period][1], $interval);
    }

    return '';
}
