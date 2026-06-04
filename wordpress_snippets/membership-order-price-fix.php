/**
 * WooCommerce Trial Membership Pricing Hook for DS POS Orders
 * 
 * This code should be added to your theme's functions.php file or as a custom plugin.
 * It prevents WooCommerce from recalculating prices for trial membership items from DS POS.
 */

// Hook into order item creation to preserve trial membership pricing
add_action('woocommerce_checkout_create_order_line_item', 'preserve_ds_pos_trial_membership_pricing', 10, 4);

function preserve_ds_pos_trial_membership_pricing($item, $cart_item_key, $values, $order) {
    // Only process if this is a DS POS order
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Check if this is a trial membership item
    if ($item->get_meta('_trial_membership') === 'true') {
        $item->set_subtotal(0);
        $item->set_total(0);
        error_log("DS POS: Set trial membership price to $0 for: " . $item->get_name());
    }
}

// Hook into order creation via REST API
add_action('woocommerce_rest_insert_shop_order_object', 'preserve_ds_pos_trial_membership_pricing_rest', 10, 3);

function preserve_ds_pos_trial_membership_pricing_rest($order, $request, $creating) {
    if (!$creating || !is_ds_pos_order($order)) {
        return;
    }
    
    error_log("DS POS: Processing trial membership pricing for order " . $order->get_id());
    
    $trial_memberships_found = 0;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Log all meta data for debugging
        $all_meta = [];
        foreach ($item->get_meta_data() as $meta) {
            $all_meta[$meta->key] = $meta->value;
        }
        error_log("DS POS: Item {$item_id} ({$item->get_name()}) meta: " . json_encode($all_meta));
        
        // Handle trial membership items - check for trial membership meta
        if ($item->get_meta('_trial_membership') === 'true') {
            $actual_price = $item->get_meta('_actual_membership_price');
            $trial_period = $item->get_meta('_trial_period_months');
            
            // Set to $0 for trial period
            $item->set_subtotal(0);
            $item->set_total(0);
            $item->save();
            $trial_memberships_found++;
            
            error_log("DS POS: Updated trial membership item {$item_id} ({$item->get_name()}) to $0 (actual price: $" . $actual_price . ", trial period: " . $trial_period . " months)");
        }
    }
    
    error_log("DS POS: Trial membership processing complete - Found: {$trial_memberships_found} trial memberships");
    
    $order->save();
}

// AGGRESSIVE APPROACH: Hook into the calculation process itself
add_action('woocommerce_order_before_calculate_totals', 'lock_ds_pos_trial_membership_pricing', 5, 2);
add_action('woocommerce_order_after_calculate_totals', 'restore_ds_pos_trial_membership_pricing', 15, 2);

function lock_ds_pos_trial_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    // Store the correct pricing before WooCommerce messes with it
    foreach ($order->get_items() as $item_id => $item) {
        if ($item->get_meta('_trial_membership') === 'true') {
            $order->add_meta_data("_ds_pos_trial_{$item_id}_subtotal", '0', true);
            $order->add_meta_data("_ds_pos_trial_{$item_id}_total", '0', true);
            error_log("DS POS: Locking trial membership item {$item_id} at $0");
        }
    }
}

function restore_ds_pos_trial_membership_pricing($and_taxes, $order) {
    if (!is_ds_pos_order($order)) {
        return;
    }
    
    $needs_save = false;
    
    foreach ($order->get_items() as $item_id => $item) {
        // Restore trial membership pricing
        $locked_subtotal = $order->get_meta("_ds_pos_trial_{$item_id}_subtotal");
        $locked_total = $order->get_meta("_ds_pos_trial_{$item_id}_total");
        
        if ($locked_subtotal !== '' && $locked_total !== '') {
            $item->set_subtotal(0);
            $item->set_total(0);
            $needs_save = true;
            error_log("DS POS: FORCE restored trial membership item {$item_id} to $0");
            
            // Clean up
            $order->delete_meta_data("_ds_pos_trial_{$item_id}_subtotal");
            $order->delete_meta_data("_ds_pos_trial_{$item_id}_total");
        }
    }
    
    if ($needs_save) {
        // Force save the items
        foreach ($order->get_items() as $item) {
            $item->save();
        }
        error_log("DS POS: FORCE saved all trial membership items with $0 pricing");
    }
}

// Helper function to check if this is a DS POS order (reuse from bundle hook)
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

// NUCLEAR OPTION: Hook into order display/rendering for trial memberships
add_filter('woocommerce_order_get_items', 'force_ds_pos_trial_membership_display', 10, 2);

function force_ds_pos_trial_membership_display($items, $order) {
    if (!is_ds_pos_order($order)) {
        return $items;
    }
    
    // Force correct pricing on every display
    foreach ($items as $item_id => $item) {
        if ($item->get_meta('_trial_membership') === 'true') {
            $item->set_subtotal(0);
            $item->set_total(0);
            error_log("DS POS: NUCLEAR - Force display trial membership {$item_id} at $0");
        }
    }
    
    return $items;
}

// Hook into line item pricing display for trial memberships
add_filter('woocommerce_order_item_get_subtotal', 'force_ds_pos_trial_membership_line_item_subtotal', 10, 2);
add_filter('woocommerce_order_item_get_total', 'force_ds_pos_trial_membership_line_item_total', 10, 2);

function force_ds_pos_trial_membership_line_item_subtotal($subtotal, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $subtotal;
    }
    
    if ($item->get_meta('_trial_membership') === 'true') {
        error_log("DS POS: NUCLEAR - Force trial membership line item subtotal to $0");
        return 0;
    }
    
    return $subtotal;
}

function force_ds_pos_trial_membership_line_item_total($total, $item) {
    $order = $item->get_order();
    if (!$order || !is_ds_pos_order($order)) {
        return $total;
    }
    
    if ($item->get_meta('_trial_membership') === 'true') {
        error_log("DS POS: NUCLEAR - Force trial membership line item total to $0");
        return 0;
    }
    
    return $total;
}

// Hook into product price calculation for trial membership items
add_filter('woocommerce_product_get_price', 'override_trial_membership_item_price', 10, 2);
add_filter('woocommerce_product_variation_get_price', 'override_trial_membership_item_price', 10, 2);

function override_trial_membership_item_price($price, $product) {
    // Only override during order creation/calculation
    if (!doing_action('woocommerce_rest_insert_shop_order_object') && 
        !doing_action('woocommerce_checkout_create_order_line_item')) {
        return $price;
    }
    
    // Check if we're in a DS POS trial membership context
    global $ds_pos_trial_membership_context;
    if (!$ds_pos_trial_membership_context) {
        return $price;
    }
    
    // Return $0 price if this product is marked as trial membership
    if (isset($ds_pos_trial_membership_context['product_' . $product->get_id()])) {
        return 0;
    }
    
    return $price;
}

// Hook into order total calculation to ensure trial memberships don't affect totals
add_filter('woocommerce_order_get_total', 'adjust_ds_pos_trial_membership_total', 10, 2);

function adjust_ds_pos_trial_membership_total($total, $order) {
    if (!is_ds_pos_order($order)) {
        return $total;
    }
    
    // Check if we have trial memberships and adjust total if needed
    $trial_membership_adjustment = 0;
    foreach ($order->get_items() as $item) {
        if ($item->get_meta('_trial_membership') === 'true') {
            // Make sure trial memberships contribute $0 to total
            $current_item_total = $item->get_total();
            if ($current_item_total > 0) {
                $trial_membership_adjustment -= $current_item_total;
                error_log("DS POS: Adjusting order total by -$" . $current_item_total . " for trial membership: " . $item->get_name());
            }
        }
    }
    
    if ($trial_membership_adjustment != 0) {
        $adjusted_total = $total + $trial_membership_adjustment;
        error_log("DS POS: NUCLEAR - Adjusted order total from $" . $total . " to $" . $adjusted_total . " (adjustment: $" . $trial_membership_adjustment . ")");
        return max(0, $adjusted_total); // Ensure total never goes negative
    }
    
    return $total;
}

// Add admin notice for successful installation
add_action('admin_notices', 'ds_pos_trial_membership_pricing_notice');

function ds_pos_trial_membership_pricing_notice() {
    if (current_user_can('manage_options')) {
        echo '<div class="notice notice-success"><p><strong>DS POS Trial Membership Pricing:</strong> Custom trial membership pricing hooks are active and will preserve $0 pricing for trial memberships.</p></div>';
    }
}

// Advanced: Hook into WooCommerce Subscriptions to ensure trial memberships create proper subscriptions
add_filter('woocommerce_subscriptions_product_sign_up_fee', 'force_trial_membership_signup_fee', 10, 2);

function force_trial_membership_signup_fee($sign_up_fee, $product) {
    // Check if we're processing a DS POS trial membership
    global $ds_pos_trial_membership_context;
    if ($ds_pos_trial_membership_context && 
        isset($ds_pos_trial_membership_context['product_' . $product->get_id()])) {
        error_log("DS POS: Setting trial membership signup fee to $0 for product " . $product->get_id());
        return 0;
    }
    
    return $sign_up_fee;
}

error_log("DS POS Trial Membership Pricing hooks loaded successfully");
