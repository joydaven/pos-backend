<?php
/**
 * Prevent Automatic Membership Discounts for DS POS Orders
 * 
 * This snippet prevents WooCommerce from automatically applying membership discounts
 * to DS POS orders. DS POS handles its own pricing and discounts.
 */

// Prevent automatic membership discounts for DS POS orders
add_action('woocommerce_rest_insert_shop_order_object', 'prevent_ds_pos_auto_membership_discount', 5, 3);

function prevent_ds_pos_auto_membership_discount($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS: Preventing automatic membership discounts for order " . $order->get_id());
    
    // Remove any automatic membership discount hooks for this order
    remove_all_actions('woocommerce_order_before_calculate_totals');
    remove_all_filters('woocommerce_product_get_price');
    remove_all_filters('woocommerce_product_variation_get_price');
    remove_all_filters('woocommerce_subscriptions_product_price');
    
    // Prevent membership plugin discounts
    if (class_exists('WC_Memberships')) {
        remove_filter('woocommerce_product_get_price', array('WC_Memberships_Member_Discounts', 'get_member_price'), 10);
        remove_filter('woocommerce_product_variation_get_price', array('WC_Memberships_Member_Discounts', 'get_member_price'), 10);
    }
    
    // Lock in the exact prices sent from DS POS
    foreach ($order->get_items() as $item_id => $item) {
        $sent_price = $item->get_subtotal();
        $sent_total = $item->get_total();
        
        // Force the exact price from DS POS
        $item->set_subtotal($sent_price);
        $item->set_total($sent_total);
        $item->save();
        
        error_log("DS POS: Locked item '{$item->get_name()}' at DS POS price: $" . $sent_price);
    }
    
    // Prevent any further price modifications
    $order->add_meta_data('_ds_pos_pricing_locked', 'true', true);
    $order->save();
}

// Hook into order calculation to maintain DS POS pricing
add_action('woocommerce_order_before_calculate_totals', 'maintain_ds_pos_pricing', 1, 2);

function maintain_ds_pos_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Check if pricing is already locked
    if ($order->get_meta('_ds_pos_pricing_locked') === 'true') {
        error_log("DS POS: Maintaining locked pricing for order " . $order->get_id());
        
        // Restore original DS POS prices
        foreach ($order->get_items() as $item_id => $item) {
            $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
            $locked_total = $item->get_meta('_ds_pos_locked_total');
            
            if (!$locked_subtotal) {
                // First time - store the DS POS prices
                $locked_subtotal = $item->get_subtotal();
                $locked_total = $item->get_total();
                $item->add_meta_data('_ds_pos_locked_subtotal', $locked_subtotal, true);
                $item->add_meta_data('_ds_pos_locked_total', $locked_total, true);
                $item->save();
            } else {
                // Restore locked prices
                $item->set_subtotal(floatval($locked_subtotal));
                $item->set_total(floatval($locked_total));
            }
        }
    }
}

// Prevent membership discounts during product price calculation
add_filter('woocommerce_product_get_price', 'prevent_membership_discount_for_ds_pos', 1, 2);
add_filter('woocommerce_product_variation_get_price', 'prevent_membership_discount_for_ds_pos', 1, 2);

function prevent_membership_discount_for_ds_pos($price, $product) {
    // Check if we're processing a DS POS order
    if (is_processing_ds_pos_order()) {
        // Return original price without membership discount
        error_log("DS POS: Preventing membership discount for product " . $product->get_id() . " - returning original price: $" . $price);
        return $product->get_regular_price() ?: $price;
    }
    
    return $price;
}

// Helper function to detect if we're processing a DS POS order
function is_processing_ds_pos_order() {
    // Check if we're in a REST API context for DS POS
    if (defined('REST_REQUEST') && REST_REQUEST) {
        $request_uri = $_SERVER['REQUEST_URI'] ?? '';
        if (strpos($request_uri, '/wp-json/wc/v3/orders') !== false) {
            // Check if the request contains DS POS indicators
            $input = file_get_contents('php://input');
            if ($input) {
                $data = json_decode($input, true);
                if ($data && isset($data['meta_data'])) {
                    foreach ($data['meta_data'] as $meta) {
                        if (in_array($meta['key'], ['_created_via', '_source_name', '_order_source', 'source']) && 
                            $meta['value'] === 'DS POS') {
                            return true;
                        }
                    }
                }
            }
        }
    }
    
    return false;
}

// Helper function to check if this is a DS POS order (reuse existing function)
if (!function_exists('is_ds_pos_order')) {
    function is_ds_pos_order($order) {
        if (!$order) {
            return false;
        }
        
        // Check multiple indicators that this is a DS POS order
        $created_via = $order->get_created_via();
        $source = $order->get_meta('source');
        $order_source = $order->get_meta('_order_source');
        $pos_order_id = $order->get_meta('_pos_order_id');
        
        return (
            $created_via === 'DS POS' ||
            $source === 'DS POS' ||
            $order_source === 'DS POS' ||
            !empty($pos_order_id)
        );
    }
}

error_log("DS POS: Auto-membership discount prevention loaded successfully");
?>
