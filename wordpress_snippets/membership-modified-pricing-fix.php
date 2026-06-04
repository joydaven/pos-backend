/**
 * WooCommerce Modified Membership Pricing Hook for DS POS Orders
 * 
 * This code should be added to your theme's functions.php file or as a custom plugin.
 * It prevents WooCommerce/ATUM from recalculating prices for membership items from DS POS
 * when the price has been manually modified (discounted) during checkout.
 */

// Hook into order item creation to preserve modified membership pricing
add_action('woocommerce_checkout_create_order_line_item', 'preserve_ds_pos_modified_membership_pricing', 10, 4);

function preserve_ds_pos_modified_membership_pricing($item, $cart_item_key, $values, $order) {
    // Only process if this is a DS POS order
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Check if this is an item with modified pricing (any product)
    if (has_modified_pricing($item)) {
        $modified_price = get_modified_price($item);
        if ($modified_price !== false) {
            $item->set_subtotal($modified_price);
            $item->set_total($modified_price);
            error_log("DS POS: Set modified membership price to $" . $modified_price . " for: " . $item->get_name());
        }
    }
}

// Hook into order creation via REST API
add_action('woocommerce_rest_insert_shop_order_object', 'preserve_ds_pos_modified_membership_pricing_rest', 10, 3);

function preserve_ds_pos_modified_membership_pricing_rest($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS: Processing modified membership pricing for order " . $order->get_id());
    
    $modified_memberships_found = 0;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Log all meta data for debugging
        $all_meta = [];
        foreach ($item->get_meta_data() as $meta) {
            $all_meta[$meta->key] = $meta->value;
        }
        error_log("DS POS: Item {$item_id} ({$item->get_name()}) meta: " . json_encode($all_meta));
        
        // Handle modified pricing items - check for pricing modifications
        if (has_modified_pricing($item)) {
            $original_price = $item->get_meta('_ds_pos_original_price');
            $modified_price = $item->get_meta('_ds_pos_modified_price');
            $discount_amount = $item->get_meta('_ds_pos_discount_amount');
            
            // If we have modified price metadata, use it
            if ($modified_price !== '' && is_numeric($modified_price)) {
                $item->set_subtotal(floatval($modified_price));
                $item->set_total(floatval($modified_price));
                $item->save();
                $modified_memberships_found++;
                
                error_log("DS POS: Updated modified membership item {$item_id} ({$item->get_name()}) to $" . $modified_price . " (original: $" . $original_price . ", discount: $" . $discount_amount . ")");
            }
            // Fallback: calculate from original price and discount
            else if ($original_price !== '' && $discount_amount !== '' && 
                     is_numeric($original_price) && is_numeric($discount_amount)) {
                $final_price = floatval($original_price) - floatval($discount_amount);
                $final_price = max(0, $final_price); // Ensure non-negative
                
                $item->set_subtotal($final_price);
                $item->set_total($final_price);
                $item->save();
                $modified_memberships_found++;
                
                error_log("DS POS: Calculated modified membership item {$item_id} ({$item->get_name()}) to $" . $final_price . " (original: $" . $original_price . " - discount: $" . $discount_amount . ")");
            }
        }
    }
    
    error_log("DS POS: Modified membership processing complete - Found: {$modified_memberships_found} modified memberships");
    
    $order->save();
}

// AGGRESSIVE APPROACH: Hook into the calculation process itself
add_action('woocommerce_order_before_calculate_totals', 'lock_ds_pos_modified_membership_pricing', 5, 2);
add_action('woocommerce_order_after_calculate_totals', 'restore_ds_pos_modified_membership_pricing', 15, 2);

function lock_ds_pos_modified_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Store the correct modified pricing before WooCommerce messes with it
    foreach ($order->get_items() as $item_id => $item) {
        if (has_modified_pricing($item)) {
            $modified_price = get_modified_price($item);
            if ($modified_price !== false) {
                $order->add_meta_data("_ds_pos_modified_{$item_id}_subtotal", $modified_price, true);
                $order->add_meta_data("_ds_pos_modified_{$item_id}_total", $modified_price, true);
                error_log("DS POS: Locking modified membership item {$item_id} at $" . $modified_price);
            }
        }
    }
}

function restore_ds_pos_modified_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    $needs_save = false;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Restore modified membership pricing
        $locked_subtotal = $order->get_meta("_ds_pos_modified_{$item_id}_subtotal");
        $locked_total = $order->get_meta("_ds_pos_modified_{$item_id}_total");
        
        if ($locked_subtotal !== '' && $locked_total !== '') {
            $item->set_subtotal(floatval($locked_subtotal));
            $item->set_total(floatval($locked_total));
            $needs_save = true;
            error_log("DS POS: FORCE restored modified membership item {$item_id} to $" . $locked_subtotal);
            
            // Clean up
            $order->delete_meta_data("_ds_pos_modified_{$item_id}_subtotal");
            $order->delete_meta_data("_ds_pos_modified_{$item_id}_total");
        }
    }
    
    if ($needs_save) {
        // Force save the items
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS: FORCE saved all modified membership items with modified pricing");
    }
}

// Helper function to check if this is a DS POS order (reuse from trial hook)
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

// Helper function to check if item has modified pricing (ANY product, not just memberships)
function has_modified_pricing($item) {
    if (!$item) {
        return false;
    }
    
    // Skip trial memberships (they have their own handler)
    if ($item->get_meta('_trial_membership') === 'true') {
        return false;
    }
    
    // Check if we have pricing modification metadata for ANY product
    $has_original_price = $item->get_meta('_ds_pos_original_price') !== '';
    $has_modified_price = $item->get_meta('_ds_pos_modified_price') !== '';
    $has_modified_pricing_flag = $item->get_meta('_ds_pos_modified_pricing') === 'true';
    
    // Debug logging
    error_log("DS POS: Checking modified pricing for item: " . $item->get_name());
    error_log("DS POS: - has_original_price: " . ($has_original_price ? 'yes' : 'no'));
    error_log("DS POS: - has_modified_price: " . ($has_modified_price ? 'yes' : 'no'));  
    error_log("DS POS: - has_modified_pricing_flag: " . ($has_modified_pricing_flag ? 'yes' : 'no'));
    
    if ($has_original_price) {
        error_log("DS POS: - original_price: " . $item->get_meta('_ds_pos_original_price'));
        error_log("DS POS: - modified_price: " . $item->get_meta('_ds_pos_modified_price'));
    }
    
    // Item has modified pricing if we have the flag OR both original and modified prices
    return $has_modified_pricing_flag || ($has_original_price && $has_modified_price);
}

// Helper function to get the modified price (any product)
function get_modified_price($item) {
    if (!$item) {
        return false;
    }
    
    // Try to get modified price directly
    $modified_price = $item->get_meta('_ds_pos_modified_price');
    if ($modified_price !== '' && is_numeric($modified_price)) {
        return floatval($modified_price);
    }
    
    // Calculate from original price and discount
    $original_price = $item->get_meta('_ds_pos_original_price');
    $discount_amount = $item->get_meta('_ds_pos_discount_amount');
    
    if ($original_price !== '' && $discount_amount !== '' && 
        is_numeric($original_price) && is_numeric($discount_amount)) {
        $final_price = floatval($original_price) - floatval($discount_amount);
        return max(0, $final_price); // Ensure non-negative
    }
    
    return false;
}

// NUCLEAR OPTION: Hook into order display/rendering for modified memberships
add_filter('woocommerce_order_get_items', 'force_ds_pos_modified_membership_display', 10, 2);

function force_ds_pos_modified_membership_display($items, $order) {
    if (!is_ds_pos_order($order)) {
        return $items;
    }
    
    // Force correct pricing on every display
    foreach ($items as $item_id => $item) {
        if (has_modified_pricing($item)) {
            $modified_price = get_modified_price($item);
            if ($modified_price !== false) {
                $item->set_subtotal($modified_price);
                $item->set_total($modified_price);
                error_log("DS POS: NUCLEAR - Force display modified pricing {$item_id} at $" . $modified_price);
            }
        }
    }
    
    return $items;
}

// Hook into line item pricing display for modified memberships
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_modified_membership_line_item_subtotal', 10, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_modified_membership_line_item_total', 10, 2);

function force_ds_pos_modified_membership_line_item_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $subtotal;
    }
    
    if (has_modified_pricing($item)) {
        $modified_price = get_modified_price($item);
        if ($modified_price !== false) {
            error_log("DS POS: NUCLEAR - Force modified pricing line item subtotal to $" . $modified_price);
            return $modified_price;
        }
    }
    
    return $subtotal;
}

function force_ds_pos_modified_membership_line_item_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $total;
    }
    
    if (has_modified_pricing($item)) {
        $modified_price = get_modified_price($item);
        if ($modified_price !== false) {
            error_log("DS POS: NUCLEAR - Force modified pricing line item total to $" . $modified_price);
            return $modified_price;
        }
    }
    
    return $total;
}

// Hook into product price calculation for modified membership items
add_filter('woocommerce_product_get_price', 'override_modified_membership_item_price', 10, 2);
add_filter('woocommerce_product_variation_get_price', 'override_modified_membership_item_price', 10, 2);

function override_modified_membership_item_price($price, $product) {
    // Only override during order creation/calculation
    if (!doing_action('woocommerce_rest_insert_shop_order_object') && 
        !doing_action('woocommerce_checkout_create_order_line_item')) {
        return $price;
    }
    
    // Check if we're in a DS POS modified membership context
    global $ds_pos_modified_membership_context;
    if (!$ds_pos_modified_membership_context) {
        return $price;
    }
    
    // Return modified price if this product is marked for price modification
    if (isset($ds_pos_modified_membership_context['product_' . $product->get_id()])) {
        $modified_price = $ds_pos_modified_membership_context['product_' . $product->get_id()];
        error_log("DS POS: Override product price for " . $product->get_id() . " to $" . $modified_price);
        return $modified_price;
    }
    
    return $price;
}

// Hook into order total calculation to ensure modified memberships are calculated correctly
add_filter('woocommerce_order_get_total', 'adjust_ds_pos_modified_membership_total', 10, 2);

function adjust_ds_pos_modified_membership_total($total, $order) {
    if (!is_ds_pos_order($order)) {
        return $total;
    }
    
    // Check if we have modified memberships and adjust total if needed
    $modified_membership_adjustment = 0;
    foreach ($order->get_items() as $item) {
        if (has_modified_pricing($item)) {
            $modified_price = get_modified_price($item);
            $current_item_total = $item->get_total();
            
            if ($modified_price !== false && $current_item_total != $modified_price) {
                $adjustment = $modified_price - $current_item_total;
                $modified_membership_adjustment += $adjustment;
                error_log("DS POS: Adjusting order total by $" . $adjustment . " for modified membership: " . $item->get_name() . " (current: $" . $current_item_total . " -> modified: $" . $modified_price . ")");
            }
        }
    }
    
    if ($modified_membership_adjustment != 0) {
        $adjusted_total = $total + $modified_membership_adjustment;
        error_log("DS POS: NUCLEAR - Adjusted order total from $" . $total . " to $" . $adjusted_total . " (adjustment: $" . $modified_membership_adjustment . ")");
        return max(0, $adjusted_total); // Ensure total never goes negative
    }
    
    return $total;
}

// Add admin notice for successful installation
add_action('admin_notices', 'ds_pos_modified_membership_pricing_notice');

function ds_pos_modified_membership_pricing_notice() {
    if (current_user_can('manage_options')) {
        echo '<div class="notice notice-success"><p><strong>DS POS Modified Product Pricing:</strong> Custom modified pricing hooks are active and will preserve discounted/modified pricing for ALL products (not just memberships).</p></div>';
    }
}

// Advanced: Hook into WooCommerce Subscriptions to ensure modified memberships create proper subscriptions
add_filter('woocommerce_subscriptions_product_sign_up_fee', 'force_modified_membership_signup_fee', 10, 2);

function force_modified_membership_signup_fee($sign_up_fee, $product) {
    // Check if we're processing a DS POS modified membership
    global $ds_pos_modified_membership_context;
    if ($ds_pos_modified_membership_context && 
        isset($ds_pos_modified_membership_context['product_' . $product->get_id()])) {
        $modified_price = $ds_pos_modified_membership_context['product_' . $product->get_id()];
        error_log("DS POS: Setting modified membership signup fee to $" . $modified_price . " for product " . $product->get_id());
        return $modified_price;
    }
    
    return $sign_up_fee;
}

error_log("DS POS Modified Membership Pricing hooks loaded successfully");
