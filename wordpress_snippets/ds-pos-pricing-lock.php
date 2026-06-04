<?php
/**
 * DS POS Pricing Lock - Prevent WooCommerce from modifying DS POS order prices
 * 
 * This is the DEFINITIVE solution to prevent WooCommerce from applying
 * automatic membership discounts or any other price modifications to DS POS orders.
 */

// PRIORITY 1: Lock DS POS prices immediately when order is created
add_action('woocommerce_rest_insert_shop_order_object', 'lock_ds_pos_order_pricing', 1, 3);

function lock_ds_pos_order_pricing($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS PRICING LOCK: Locking prices for order " . $order->get_id());
    
    // Store original DS POS prices before WooCommerce can modify them
    foreach ($order->get_items() as $item_id => $item) {
        $original_subtotal = $item->get_subtotal();
        $original_total = $item->get_total();
        
        // Store the DS POS prices as meta data
        $item->add_meta_data('_ds_pos_locked_subtotal', $original_subtotal, true);
        $item->add_meta_data('_ds_pos_locked_total', $original_total, true);
        $item->add_meta_data('_ds_pos_pricing_locked', 'true', true);
        $item->save();
        
        error_log("DS POS PRICING LOCK: Stored original prices for '{$item->get_name()}': subtotal=$" . $original_subtotal . ", total=$" . $original_total);
    }
    
    // Mark order as having locked pricing
    $order->add_meta_data('_ds_pos_pricing_locked', 'true', true);
    $order->save();
}

// PRIORITY 2: Prevent membership discounts during order processing
add_filter('woocommerce_order_item_product', 'prevent_ds_pos_membership_discount', 1, 2);

function prevent_ds_pos_membership_discount($product, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $product;
    }
    
    // Create a proxy product that returns DS POS prices
    if ($item->get_meta('_ds_pos_pricing_locked') === 'true') {
        $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
        if ($locked_subtotal) {
            $ds_pos_price = floatval($locked_subtotal) / $item->get_quantity();
            
            // Override product price methods to return DS POS price
            add_filter('woocommerce_product_get_price', function($price, $prod) use ($product, $ds_pos_price) {
                if ($prod->get_id() === $product->get_id()) {
                    return $ds_pos_price;
                }
                return $price;
            }, 999, 2);
            
            add_filter('woocommerce_product_variation_get_price', function($price, $prod) use ($product, $ds_pos_price) {
                if ($prod->get_id() === $product->get_id()) {
                    return $ds_pos_price;
                }
                return $price;
            }, 999, 2);
            
            error_log("DS POS PRICING LOCK: Overriding product price for '{$product->get_name()}' to $" . $ds_pos_price);
        }
    }
    
    return $product;
}

// PRIORITY 3: Force DS POS prices during any calculation
add_action('woocommerce_order_before_calculate_totals', 'force_ds_pos_pricing', 1, 2);

function force_ds_pos_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order) || $order->get_meta('_ds_pos_pricing_locked') !== 'true') {
        return;
    }
    
    error_log("DS POS PRICING LOCK: Forcing DS POS prices during calculation for order " . $order->get_id());
    
    foreach ($order->get_items() as $item_id => $item) {
        $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
        $locked_total = $item->get_meta('_ds_pos_locked_total');
        
        if ($locked_subtotal && $locked_total) {
            $item->set_subtotal(floatval($locked_subtotal));
            $item->set_total(floatval($locked_total));
            error_log("DS POS PRICING LOCK: Restored locked prices for '{$item->get_name()}': $" . $locked_subtotal);
        }
    }
}

// PRIORITY 4: Final enforcement after calculation
add_action('woocommerce_order_after_calculate_totals', 'enforce_ds_pos_pricing', 999, 2);

function enforce_ds_pos_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order) || $order->get_meta('_ds_pos_pricing_locked') !== 'true') {
        return;
    }
    
    error_log("DS POS PRICING LOCK: Final enforcement of DS POS prices for order " . $order->get_id());
    
    $needs_save = false;
    foreach ($order->get_items() as $item_id => $item) {
        $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
        $locked_total = $item->get_meta('_ds_pos_locked_total');
        
        if ($locked_subtotal && $locked_total) {
            $current_subtotal = $item->get_subtotal();
            $current_total = $item->get_total();
            
            // Check if WooCommerce changed the prices
            if (abs($current_subtotal - floatval($locked_subtotal)) > 0.01 || 
                abs($current_total - floatval($locked_total)) > 0.01) {
                
                $item->set_subtotal(floatval($locked_subtotal));
                $item->set_total(floatval($locked_total));
                $needs_save = true;
                
                error_log("DS POS PRICING LOCK: CORRECTED pricing for '{$item->get_name()}': " . 
                         "WC tried to change $" . $current_subtotal . " to $" . $locked_subtotal);
            }
        }
    }
    
    if ($needs_save) {
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS PRICING LOCK: Saved corrected prices");
    }
}

// NUCLEAR OPTION: Override all price getters for DS POS orders
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_item_subtotal', 999, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_item_total', 999, 2);

function force_ds_pos_item_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $subtotal;
    }
    
    $locked_subtotal = $item->get_meta('_ds_pos_locked_subtotal');
    if ($locked_subtotal && $order->get_meta('_ds_pos_pricing_locked') === 'true') {
        return floatval($locked_subtotal);
    }
    
    return $subtotal;
}

function force_ds_pos_item_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $total;
    }
    
    $locked_total = $item->get_meta('_ds_pos_locked_total');
    if ($locked_total && $order->get_meta('_ds_pos_pricing_locked') === 'true') {
        return floatval($locked_total);
    }
    
    return $total;
}

// Helper function to check if this is a DS POS order
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

error_log("DS POS PRICING LOCK: Comprehensive pricing protection loaded successfully");
?>
