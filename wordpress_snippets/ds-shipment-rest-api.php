<?php
/**
 * Plugin Name: DS Shipment REST API
 * Description: Custom REST API endpoint for managing WooCommerce Shipment Tracking
 *              with products_list support. Used by the DS POS backend.
 * Version: 1.0
 * Author: DS POS Team
 *
 * Registers: ds-shipment/v1/orders/{order_id}/trackings
 *
 * Endpoints:
 *   GET    /wp-json/ds-shipment/v1/orders/{order_id}/trackings              — List all trackings
 *   POST   /wp-json/ds-shipment/v1/orders/{order_id}/trackings              — Create tracking
 *   DELETE /wp-json/ds-shipment/v1/orders/{order_id}/trackings/{tracking_id} — Delete tracking
 *
 * Installation:
 *   Upload to wp-content/mu-plugins/ on the WordPress server.
 *
 * Auth: HTTP Basic Auth with WooCommerce consumer key/secret (same as wc/v3).
 */

if (!defined('ABSPATH')) {
    exit;
}

/**
 * Authenticate WooCommerce API keys (ck_/cs_) for the ds-shipment namespace.
 * WordPress's built-in Basic Auth handler rejects ck_ keys as "invalid_username".
 * This filter runs BEFORE that handler and authenticates the user via WC API keys,
 * so WordPress sees a valid user and doesn't reject the request.
 */
add_filter('determine_current_user', function ($user_id) {
    // Only act on REST API requests to our namespace
    if (!empty($user_id)) {
        return $user_id;
    }

    // Check if this is a ds-shipment REST request
    $request_uri = isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '';
    if (strpos($request_uri, 'ds-shipment/v1') === false) {
        return $user_id;
    }

    // Extract Basic Auth credentials
    $consumer_key    = '';
    $consumer_secret = '';

    if (!empty($_SERVER['PHP_AUTH_USER']) && !empty($_SERVER['PHP_AUTH_PW'])) {
        $consumer_key    = $_SERVER['PHP_AUTH_USER'];
        $consumer_secret = $_SERVER['PHP_AUTH_PW'];
    } else {
        // Try Authorization header
        $auth_header = '';
        if (!empty($_SERVER['HTTP_AUTHORIZATION'])) {
            $auth_header = $_SERVER['HTTP_AUTHORIZATION'];
        } elseif (!empty($_SERVER['REDIRECT_HTTP_AUTHORIZATION'])) {
            $auth_header = $_SERVER['REDIRECT_HTTP_AUTHORIZATION'];
        }
        if ($auth_header && stripos($auth_header, 'basic ') === 0) {
            $decoded = base64_decode(substr($auth_header, 6));
            if ($decoded && strpos($decoded, ':') !== false) {
                list($consumer_key, $consumer_secret) = explode(':', $decoded, 2);
            }
        }
    }

    // Only handle WooCommerce API keys (ck_ prefix)
    if (empty($consumer_key) || strpos($consumer_key, 'ck_') !== 0) {
        return $user_id;
    }

    if (!function_exists('wc_api_hash')) {
        return $user_id;
    }

    global $wpdb;
    $key = $wpdb->get_row(
        $wpdb->prepare(
            "SELECT key_id, user_id, permissions, consumer_secret
             FROM {$wpdb->prefix}woocommerce_api_keys
             WHERE consumer_key = %s",
            wc_api_hash($consumer_key)
        )
    );

    if ($key && hash_equals($key->consumer_secret, $consumer_secret)) {
        error_log('DS Shipment REST API: Authenticated user #' . $key->user_id . ' via WC API key #' . $key->key_id);
        // Store permissions for later use in permission_callback
        $GLOBALS['ds_shipment_api_permissions'] = $key->permissions;
        return $key->user_id;
    }

    return $user_id;
}, 1);

add_action('rest_api_init', function () {
    // GET  — list all trackings for an order
    register_rest_route('ds-shipment/v1', '/orders/(?P<order_id>\d+)/trackings', array(
        'methods'             => 'GET',
        'callback'            => 'ds_shipment_get_trackings',
        'permission_callback' => 'ds_shipment_check_permission',
        'args'                => array(
            'order_id' => array(
                'required'          => true,
                'validate_callback' => function ($param) {
                    return is_numeric($param);
                },
            ),
        ),
    ));

    // POST — create a new tracking
    register_rest_route('ds-shipment/v1', '/orders/(?P<order_id>\d+)/trackings', array(
        'methods'             => 'POST',
        'callback'            => 'ds_shipment_create_tracking',
        'permission_callback' => 'ds_shipment_check_permission',
        'args'                => array(
            'order_id' => array(
                'required'          => true,
                'validate_callback' => function ($param) {
                    return is_numeric($param);
                },
            ),
        ),
    ));

    // DELETE — remove a tracking by tracking_id
    register_rest_route('ds-shipment/v1', '/orders/(?P<order_id>\d+)/trackings/(?P<tracking_id>[a-f0-9]+)', array(
        'methods'             => 'DELETE',
        'callback'            => 'ds_shipment_delete_tracking',
        'permission_callback' => 'ds_shipment_check_permission',
        'args'                => array(
            'order_id' => array(
                'required'          => true,
                'validate_callback' => function ($param) {
                    return is_numeric($param);
                },
            ),
            'tracking_id' => array(
                'required'          => true,
                'validate_callback' => function ($param) {
                    return preg_match('/^[a-f0-9]+$/', $param);
                },
            ),
        ),
    ));

    error_log('DS Shipment REST API: Routes registered');
});


/**
 * Permission check — the determine_current_user filter above handles WC API key auth.
 * This just verifies the authenticated user has the right capabilities.
 */
function ds_shipment_check_permission($request) {
    $user = wp_get_current_user();
    if ($user && $user->ID > 0 && user_can($user, 'manage_woocommerce')) {
        // For write operations, also check API key permissions if available
        $method = $request->get_method();
        if ($method !== 'GET' && !empty($GLOBALS['ds_shipment_api_permissions'])) {
            if (!in_array($GLOBALS['ds_shipment_api_permissions'], array('read_write', 'write'), true)) {
                return new WP_Error('rest_forbidden', 'Insufficient API key permissions.', array('status' => 403));
            }
        }
        return true;
    }

    return new WP_Error('rest_forbidden', 'Authentication required.', array('status' => 401));
}


/**
 * GET — List all shipment trackings for an order.
 * Returns array of tracking objects with products_list included.
 */
function ds_shipment_get_trackings($request) {
    $order_id = absint($request['order_id']);
    $order    = wc_get_order($order_id);

    if (!$order) {
        return new WP_Error('not_found', "Order {$order_id} not found.", array('status' => 404));
    }

    $tracking_items = $order->get_meta('_wc_shipment_tracking_items', true);
    if (!is_array($tracking_items)) {
        $tracking_items = array();
    }

    // Build line-item name map for enrichment
    $item_name_map = array();
    foreach ($order->get_items() as $line_item) {
        $item_name_map[strval($line_item->get_id())]         = $line_item->get_name();
        $item_name_map[strval($line_item->get_product_id())] = $line_item->get_name();
    }

    $result = array();
    foreach ($tracking_items as $tracking) {
        $result[] = ds_shipment_format_tracking($tracking, $item_name_map, $order_id);
    }

    return rest_ensure_response($result);
}


/**
 * POST — Create a new shipment tracking.
 *
 * Expected JSON body:
 * {
 *   "tracking_number": "...",
 *   "tracking_provider": "USPS",           // or empty
 *   "custom_tracking_provider": "MyShip",   // if no standard provider
 *   "custom_tracking_link": "https://...",  // optional
 *   "date_shipped": "2026-03-03",           // optional
 *   "products_list": [                      // optional
 *     {"product": "123", "item_id": "456", "qty": "2"}
 *   ],
 *   "status_shipped": "shipped"|"partial"   // optional
 * }
 */
function ds_shipment_create_tracking($request) {
    $order_id = absint($request['order_id']);
    $order    = wc_get_order($order_id);

    if (!$order) {
        return new WP_Error('not_found', "Order {$order_id} not found.", array('status' => 404));
    }

    $body = $request->get_json_params();

    $tracking_number = sanitize_text_field($body['tracking_number'] ?? '');
    if (empty($tracking_number)) {
        return new WP_Error('missing_field', 'tracking_number is required.', array('status' => 400));
    }

    $tracking_provider        = sanitize_text_field($body['tracking_provider'] ?? '');
    $custom_tracking_provider = sanitize_text_field($body['custom_tracking_provider'] ?? '');
    $custom_tracking_link     = esc_url_raw($body['custom_tracking_link'] ?? '');
    $date_shipped             = sanitize_text_field($body['date_shipped'] ?? '');

    // Generate a unique tracking ID
    $tracking_id = md5($tracking_provider . $tracking_number . microtime(true));

    // Build the tracking link from the provider URL template if available
    $tracking_link = '';
    if (!empty($tracking_provider) && function_exists('wc_shipment_tracking')) {
        // Try to get the tracking URL from the WC Shipment Tracking plugin
        $ast_instance = wc_shipment_tracking();
        if ($ast_instance && method_exists($ast_instance, 'get_tracking_url')) {
            $tracking_link = $ast_instance->get_tracking_url($order_id, $tracking_provider, $tracking_number);
        }
    }
    if (empty($tracking_link) && !empty($custom_tracking_link)) {
        $tracking_link = $custom_tracking_link;
    }

    // Build products_list as stdClass objects (WC Shipment Tracking format)
    $products_list = array();
    if (!empty($body['products_list']) && is_array($body['products_list'])) {
        foreach ($body['products_list'] as $product) {
            $p          = new stdClass();
            $p->product = sanitize_text_field($product['product'] ?? $product['product_id'] ?? '');
            $p->item_id = sanitize_text_field($product['item_id'] ?? '');
            $p->qty     = sanitize_text_field($product['qty'] ?? '1');
            $products_list[] = $p;
        }
    }

    // Build the tracking item array
    $new_tracking = array(
        'tracking_provider'        => $tracking_provider,
        'custom_tracking_provider' => $custom_tracking_provider,
        'custom_tracking_link'     => $custom_tracking_link,
        'tracking_number'          => $tracking_number,
        'date_shipped'             => $date_shipped,
        'tracking_id'              => $tracking_id,
        'tracking_link'            => $tracking_link,
        'products_list'            => $products_list,
    );

    // Get existing trackings and append
    $tracking_items = $order->get_meta('_wc_shipment_tracking_items', true);
    if (!is_array($tracking_items)) {
        $tracking_items = array();
    }
    $tracking_items[] = $new_tracking;

    // Save to order meta
    $order->update_meta_data('_wc_shipment_tracking_items', $tracking_items);
    $order->save();

    error_log("DS Shipment REST API: Created tracking {$tracking_id} for order {$order_id} — {$tracking_provider} {$tracking_number}");

    // Build enriched response
    $item_name_map = array();
    foreach ($order->get_items() as $line_item) {
        $item_name_map[strval($line_item->get_id())]         = $line_item->get_name();
        $item_name_map[strval($line_item->get_product_id())] = $line_item->get_name();
    }

    $response_data = ds_shipment_format_tracking($new_tracking, $item_name_map, $order_id);

    return rest_ensure_response(array(
        'success' => true,
        'data'    => $response_data,
    ));
}


/**
 * DELETE — Remove a shipment tracking by tracking_id.
 */
function ds_shipment_delete_tracking($request) {
    $order_id    = absint($request['order_id']);
    $tracking_id = sanitize_text_field($request['tracking_id']);
    $order       = wc_get_order($order_id);

    if (!$order) {
        return new WP_Error('not_found', "Order {$order_id} not found.", array('status' => 404));
    }

    $tracking_items = $order->get_meta('_wc_shipment_tracking_items', true);
    if (!is_array($tracking_items)) {
        return new WP_Error('not_found', 'No trackings found for this order.', array('status' => 404));
    }

    $found = false;
    $updated_items = array();
    foreach ($tracking_items as $tracking) {
        if (isset($tracking['tracking_id']) && $tracking['tracking_id'] === $tracking_id) {
            $found = true;
            continue; // Skip this one (delete it)
        }
        $updated_items[] = $tracking;
    }

    if (!$found) {
        return new WP_Error('not_found', "Tracking {$tracking_id} not found.", array('status' => 404));
    }

    // Save updated list
    if (empty($updated_items)) {
        $order->delete_meta_data('_wc_shipment_tracking_items');
    } else {
        $order->update_meta_data('_wc_shipment_tracking_items', $updated_items);
    }
    $order->save();

    error_log("DS Shipment REST API: Deleted tracking {$tracking_id} from order {$order_id}");

    return rest_ensure_response(array(
        'success' => true,
        'message' => "Tracking {$tracking_id} deleted.",
    ));
}


/**
 * Format a tracking item for the REST response.
 * Enriches products_list with product names from order line items.
 */
function ds_shipment_format_tracking($tracking, $item_name_map, $order_id) {
    $products_list = array();

    if (!empty($tracking['products_list'])) {
        foreach ($tracking['products_list'] as $p) {
            // Handle both stdClass and array formats
            $product_id = '';
            $item_id    = '';
            $qty        = '1';

            if (is_object($p)) {
                $product_id = isset($p->product) ? strval($p->product) : '';
                $item_id    = isset($p->item_id) ? strval($p->item_id) : '';
                $qty        = isset($p->qty) ? strval($p->qty) : '1';
            } elseif (is_array($p)) {
                $product_id = isset($p['product']) ? strval($p['product']) : '';
                $item_id    = isset($p['item_id']) ? strval($p['item_id']) : '';
                $qty        = isset($p['qty']) ? strval($p['qty']) : '1';
            }

            // Enrich with product name
            $product_name = '';
            if (!empty($item_id) && isset($item_name_map[$item_id])) {
                $product_name = $item_name_map[$item_id];
            } elseif (!empty($product_id) && isset($item_name_map[$product_id])) {
                $product_name = $item_name_map[$product_id];
            }

            $products_list[] = array(
                'product'     => $product_id,
                'item_id'     => $item_id,
                'qty'         => $qty,
                'productName' => $product_name,
            );
        }
    }

    // Build tracking link
    $tracking_link = '';
    if (!empty($tracking['tracking_link'])) {
        $tracking_link = $tracking['tracking_link'];
    } elseif (!empty($tracking['custom_tracking_link'])) {
        $tracking_link = $tracking['custom_tracking_link'];
    }

    return array(
        'tracking_id'              => $tracking['tracking_id'] ?? '',
        'tracking_provider'        => $tracking['tracking_provider'] ?? '',
        'custom_tracking_provider' => $tracking['custom_tracking_provider'] ?? '',
        'custom_tracking_link'     => $tracking['custom_tracking_link'] ?? '',
        'tracking_number'          => $tracking['tracking_number'] ?? '',
        'tracking_link'            => $tracking_link,
        'date_shipped'             => $tracking['date_shipped'] ?? '',
        'products_list'            => $products_list,
    );
}
