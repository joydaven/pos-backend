<?php
/**
 * Plugin Name: DS Tracking Email Trigger
 * Description: Detects when WooCommerce Shipment Tracking data is saved on an order
 *              and triggers our branded tracking email via the Django POS backend.
 * Version: 2.0
 * Author: DS POS Team
 *
 * Installation:
 *   - Add to theme's functions.php, OR
 *   - Upload to wp-content/mu-plugins/, OR
 *   - Add via Code Snippets plugin
 *
 * How it works:
 *   The WC Shipment Tracking plugin does NOT fire a custom action hook when tracking
 *   is added via the admin UI. Instead, it saves tracking data as order meta
 *   (_wc_shipment_tracking_items) via AJAX.
 *
 *   This snippet intercepts the AJAX save handler (wc_shipment_tracking_save_form)
 *   by hooking in at a very early priority, reading the POST data, and firing our
 *   webhook to the Django backend.
 *
 *   When tracking is added via our POS (through the ds-shipment/v1 REST endpoint),
 *   a transient flag is set to prevent double-sending since the POS already sends
 *   the email directly.
 *
 * Environment:
 *   Define DS_POS_API_URL and DS_WEBHOOK_SECRET in wp-config.php or use defaults.
 *   define('DS_POS_API_URL', 'https://pos.doctorsstudio.com');
 *   define('DS_WEBHOOK_SECRET', 'ds-tracking-webhook-2026');
 */

if (!defined('ABSPATH')) {
    exit;
}

error_log('DS Tracking Email Trigger: Snippet loaded (v2.1)');

/**
 * Disable AST Pro's built-in shipped and partial-shipped email notifications.
 *
 * The POS backend now handles sending both email types directly:
 *   - Full shipped:    views_receipts.py → send_tracking_email()
 *   - Partial shipped: views_receipts.py → send_partial_shipped_email()
 *
 * Leaving AST Pro emails enabled would cause duplicate emails to the customer.
 */
add_filter('woocommerce_email_enabled_customer_partial_shipped_order', '__return_false');
add_filter('woocommerce_email_enabled_customer_shipped_order', '__return_false');

/**
 * Set a transient flag BEFORE tracking is created via our DS custom REST plugin
 * to prevent double-sending the email (POS already sends it).
 *
 * Uses rest_pre_dispatch which fires BEFORE the controller runs, so the transient
 * is set before any meta hooks fire. rest_pre_echo_response was too late.
 */
add_filter('rest_pre_dispatch', function ($result, $server, $request) {
    $route = $request->get_route();
    if ($request->get_method() === 'POST' && preg_match('#^/ds-shipment/v1/orders/(\d+)/trackings#', $route, $matches)) {
        $order_id = $matches[1];
        set_transient('ds_tracking_skip_' . $order_id, '1', 120);
        error_log("DS Tracking Email: Pre-set skip transient for order {$order_id} (rest_pre_dispatch)");
    }
    return $result;
}, 10, 3);

/**
 * Intercept the WC Shipment Tracking AJAX save handler.
 * The plugin registers: wp_ajax_wc_shipment_tracking_save_form
 * We hook in at priority 1 (before the plugin's handler at priority 10)
 * to capture the POST data and trigger our email webhook.
 */
add_action('wp_ajax_wc_shipment_tracking_save_form', 'ds_intercept_tracking_save', 1);

function ds_intercept_tracking_save() {
    error_log('DS Tracking Email: AJAX save intercepted');

    $order_id = isset($_POST['order_id']) ? absint($_POST['order_id']) : 0;
    if (!$order_id) {
        error_log('DS Tracking Email: No order_id in AJAX POST');
        return; // Let the original handler continue
    }

    // Skip if this tracking was just created by our POS
    $skip_key = 'ds_tracking_skip_' . $order_id;
    if (get_transient($skip_key)) {
        // Do NOT delete_transient here — meta hooks can fire multiple times in the same
        // request (HPOS + legacy, or added + updated). Deleting on first check lets the
        // second hook slip through and send a duplicate email. The transient expires on its own (120s).
        error_log("DS Tracking Email: Skipping order {$order_id} — created via POS");
        return;
    }

    // Extract tracking data from the AJAX POST fields
    $tracking_provider = '';
    if (!empty($_POST['tracking_provider'])) {
        $tracking_provider = sanitize_text_field($_POST['tracking_provider']);
    }
    if (empty($tracking_provider) && !empty($_POST['custom_tracking_provider'])) {
        $tracking_provider = sanitize_text_field($_POST['custom_tracking_provider']);
    }

    $tracking_number = !empty($_POST['tracking_number']) ? sanitize_text_field($_POST['tracking_number']) : '';
    $date_shipped = !empty($_POST['date_shipped']) ? sanitize_text_field($_POST['date_shipped']) : '';
    $custom_tracking_link = !empty($_POST['custom_tracking_link']) ? esc_url_raw($_POST['custom_tracking_link']) : '';

    if (empty($tracking_number)) {
        error_log("DS Tracking Email: No tracking_number for order {$order_id}, skipping");
        return;
    }

    // Set flag so meta hooks in this same request know AJAX already handled it
    global $ds_tracking_ajax_handled;
    $ds_tracking_ajax_handled = $order_id;

    // Build products list from order line items
    $products_list = array();
    $order = wc_get_order($order_id);
    if ($order) {
        foreach ($order->get_items() as $item) {
            $products_list[] = $item->get_name() . ' × ' . $item->get_quantity();
        }
    }

    error_log("DS Tracking Email: Sending webhook for order {$order_id} — {$tracking_provider} {$tracking_number}");

    ds_send_tracking_webhook($order_id, $tracking_provider, $tracking_number, $date_shipped, $custom_tracking_link, $products_list);

    // IMPORTANT: Do NOT exit/die here — let the original AJAX handler continue
}

/**
 * Also hook into the WC Shipment Tracking REST API endpoint.
 * When tracking is added via REST API (e.g. from external tools), the plugin
 * fires a different code path. We monitor order meta changes as a fallback.
 */
add_action('added_order_meta', 'ds_check_tracking_meta_added', 10, 4);
add_action('updated_order_meta', 'ds_check_tracking_meta_updated', 10, 4);
// Legacy post meta hooks (for non-HPOS stores)
add_action('added_post_meta', 'ds_check_tracking_postmeta', 10, 4);
add_action('updated_post_meta', 'ds_check_tracking_postmeta', 10, 4);

function ds_check_tracking_meta_added($meta_id, $order_id, $meta_key, $meta_value) {
    ds_handle_tracking_meta_change($order_id, $meta_key, $meta_value);
}

function ds_check_tracking_meta_updated($meta_id, $order_id, $meta_key, $meta_value) {
    ds_handle_tracking_meta_change($order_id, $meta_key, $meta_value);
}

function ds_check_tracking_postmeta($meta_id, $post_id, $meta_key, $meta_value) {
    if ($meta_key !== '_wc_shipment_tracking_items') {
        return;
    }
    // Verify this is an order post type
    if (get_post_type($post_id) !== 'shop_order') {
        return;
    }
    ds_handle_tracking_meta_change($post_id, $meta_key, $meta_value);
}

function ds_handle_tracking_meta_change($order_id, $meta_key, $meta_value) {
    if ($meta_key !== '_wc_shipment_tracking_items') {
        return;
    }

    // Per-request guard: meta hooks can fire multiple times in one request
    // (HPOS + legacy, or added + updated). Only allow one webhook send per order per request.
    global $ds_tracking_meta_handled;
    if (!is_array($ds_tracking_meta_handled)) {
        $ds_tracking_meta_handled = array();
    }
    if (in_array((int) $order_id, $ds_tracking_meta_handled, true)) {
        error_log("DS Tracking Email: Meta hook skipping order {$order_id} — already handled in this request");
        return;
    }

    // Skip if the AJAX handler already handled this order in the same request
    global $ds_tracking_ajax_handled;
    if (!empty($ds_tracking_ajax_handled) && (int) $ds_tracking_ajax_handled === (int) $order_id) {
        error_log("DS Tracking Email: Meta hook skipping order {$order_id} — AJAX handler already sent");
        return;
    }

    // Skip if created by POS
    $skip_key = 'ds_tracking_skip_' . $order_id;
    if (get_transient($skip_key)) {
        // Do NOT delete_transient — meta hooks fire multiple times per request.
        error_log("DS Tracking Email: Meta hook skipping order {$order_id} — created via POS");
        return;
    }

    // Also skip if the AJAX handler already fired in this request (fallback)
    if (did_action('wp_ajax_wc_shipment_tracking_save_form') > 0) {
        error_log("DS Tracking Email: Meta hook skipping order {$order_id} — AJAX action already fired");
        return;
    }

    $tracking_items = maybe_unserialize($meta_value);
    if (!is_array($tracking_items) || empty($tracking_items)) {
        return;
    }

    // Get the latest tracking item (last in array)
    $latest = end($tracking_items);
    if (!is_array($latest)) {
        return;
    }

    $tracking_provider = !empty($latest['tracking_provider']) ? $latest['tracking_provider'] : '';
    if (empty($tracking_provider) && !empty($latest['custom_tracking_provider'])) {
        $tracking_provider = $latest['custom_tracking_provider'];
    }
    $tracking_number = !empty($latest['tracking_number']) ? $latest['tracking_number'] : '';
    $date_shipped = !empty($latest['date_shipped']) ? $latest['date_shipped'] : '';
    $custom_link = !empty($latest['custom_tracking_link']) ? $latest['custom_tracking_link'] : '';

    if (empty($tracking_number)) {
        return;
    }

    // Build products list
    $products_list = array();
    $order = wc_get_order($order_id);
    if ($order) {
        foreach ($order->get_items() as $item) {
            $products_list[] = $item->get_name() . ' × ' . $item->get_quantity();
        }
    }

    error_log("DS Tracking Email: Meta change detected for order {$order_id} — {$tracking_provider} {$tracking_number}");

    // Mark as handled BEFORE sending, so subsequent meta hooks in this request are blocked
    $ds_tracking_meta_handled[] = (int) $order_id;

    ds_send_tracking_webhook($order_id, $tracking_provider, $tracking_number, $date_shipped, $custom_link, $products_list);
}

/**
 * Send the tracking data to our Django backend webhook.
 */
function ds_send_tracking_webhook($order_id, $tracking_provider, $tracking_number, $date_shipped, $custom_tracking_link, $products_list) {
    // Prevent duplicate sends
    $sent_key = 'ds_tracking_sent_' . $order_id . '_' . md5($tracking_number);
    if (get_transient($sent_key)) {
        error_log("DS Tracking Email: Already sent for order {$order_id} tracking {$tracking_number}, skipping duplicate");
        return;
    }
    set_transient($sent_key, '1', 300); // 5 minute dedup window

    $api_url = defined('DS_POS_API_URL') ? DS_POS_API_URL : 'https://pos.doctorsstudio.com';
    $webhook_secret = defined('DS_WEBHOOK_SECRET') ? DS_WEBHOOK_SECRET : 'ds-tracking-webhook-2026';
    $endpoint = $api_url . '/api/webhooks/woocommerce/tracking-email/';

    $payload = array(
        'order_id' => $order_id,
        'tracking_provider' => $tracking_provider,
        'tracking_number' => $tracking_number,
        'date_shipped' => $date_shipped,
        'custom_tracking_link' => $custom_tracking_link,
        'products_list' => $products_list,
    );

    $args = array(
        'body' => wp_json_encode($payload),
        'headers' => array(
            'Content-Type' => 'application/json',
            'X-DS-Webhook-Secret' => $webhook_secret,
        ),
        'timeout' => 15,
        'blocking' => true,
        'sslverify' => false,
    );

    $response = wp_remote_post($endpoint, $args);

    if (is_wp_error($response)) {
        error_log('DS Tracking Email: Failed — ' . $response->get_error_message());
    } else {
        $code = wp_remote_retrieve_response_code($response);
        $body = wp_remote_retrieve_body($response);
        error_log("DS Tracking Email: Webhook response {$code} for order {$order_id} — {$body}");
    }
}
