/**
 * DS POS Fulfillment Location - WordPress Snippet
 * 
 * Stock deduction is now handled UPSTREAM by Django via ATUM's native mi_inventories
 * field on line items during order creation. This snippet only needs to:
 * 
 * 1. Preserve _fulfillment_location meta on order items (for display/audit)
 * 2. Act as a FALLBACK filter if mi_inventories wasn't sent by Django
 *    (e.g., if ATUM inventory lookup failed on the Django side)
 */

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

if (!function_exists('ds_pos_check_order_source')) {
    function ds_pos_check_order_source($order) {
        if (!$order) {
            return false;
        }
        $customer_note = $order->get_customer_note();
        return strpos($customer_note, 'POS Order:') !== false;
    }
}

if (!function_exists('ds_pos_get_atum_inventory_id')) {
    function ds_pos_get_atum_inventory_id($location_name, $product_id = null) {
        global $wpdb;
        
        $result = null;
        
        // Try to match by inventory name + product_id
        if ($product_id) {
            $result = $wpdb->get_var($wpdb->prepare(
                "SELECT id FROM {$wpdb->prefix}atum_inventories WHERE product_id = %d AND name = %s LIMIT 1",
                $product_id, $location_name
            ));
        }
        
        // Fallback: match by location taxonomy (join atum_inventories with term relationships)
        if (empty($result) && $product_id) {
            $location_slug = sanitize_title($location_name);
            $result = $wpdb->get_var($wpdb->prepare(
                "SELECT ai.id 
                 FROM {$wpdb->prefix}atum_inventories ai
                 INNER JOIN {$wpdb->prefix}atum_inventory_locations ail ON ai.id = ail.inventory_id
                 INNER JOIN {$wpdb->prefix}terms t ON ail.location_id = t.term_id
                 WHERE ai.product_id = %d AND (t.slug = %s OR t.name = %s)
                 LIMIT 1",
                $product_id, $location_slug, $location_name
            ));
        }
        
        // Last fallback: any inventory with this name
        if (empty($result)) {
            $result = $wpdb->get_var($wpdb->prepare(
                "SELECT id FROM {$wpdb->prefix}atum_inventories WHERE name = %s LIMIT 1",
                $location_name
            ));
        }
        
        return $result;
    }
}

// ============================================================================
// STEP 1: Preserve fulfillment location meta on order creation
// ============================================================================

add_action('woocommerce_rest_insert_shop_order_object', function ($order, $request, $creating) {
    if (!$creating || !ds_pos_check_order_source($order)) {
        return;
    }

    error_log("DS POS MI: Processing POS order #" . $order->get_id());

    foreach ($order->get_items() as $item_id => $item) {
        $fulfillment_location = $item->get_meta('_fulfillment_location');
        if (empty($fulfillment_location)) {
            continue;
        }

        // Store fulfillment location in order-level meta for audit trail
        $order->add_meta_data("_ds_pos_fulfillment_{$item_id}", $fulfillment_location, true);
        
        // Re-save to ensure it persists
        $item->update_meta_data('_fulfillment_location', $fulfillment_location);
        $item->save_meta_data();

        error_log("DS POS MI: Preserved fulfillment location '{$fulfillment_location}' for item '{$item->get_name()}' (item_id={$item_id})");
    }

    $order->save();
}, 10, 3);

// ============================================================================
// STEP 2: FALLBACK - Force ATUM to use POS-chosen inventory if mi_inventories
//         was not sent by Django (e.g., ATUM API lookup failed on Django side)
// ============================================================================

add_filter('atum/multi_inventory/order_item_inventories', function ($inventories, $item) {
    // Only intercept for order items that have a POS-assigned fulfillment location
    $fulfillment_location = $item->get_meta('_fulfillment_location');
    if (empty($fulfillment_location)) {
        return $inventories; // Not a POS item, let ATUM handle normally
    }

    // Check if mi_inventories was already set by Django (ATUM would have already
    // processed it). If the correct inventory is already first, no need to intervene.
    $product_id = $item->get_variation_id() ?: $item->get_product_id();
    $atum_inventory_id = ds_pos_get_atum_inventory_id($fulfillment_location, $product_id);

    if (empty($atum_inventory_id)) {
        error_log("DS POS MI FALLBACK: Could not resolve inventory for '{$fulfillment_location}' product {$product_id}, using ATUM default");
        return $inventories;
    }

    // Find the matching inventory in the list and force it
    foreach ($inventories as $inventory) {
        if ((int) $inventory->id === (int) $atum_inventory_id) {
            error_log("DS POS MI FALLBACK: Forcing inventory ID {$atum_inventory_id} ('{$inventory->name}') for '{$item->get_name()}'");
            return [$inventory];
        }
    }

    // Inventory not in sorted list - try loading directly
    if (class_exists('\AtumMultiInventory\Inc\Helpers')) {
        try {
            $inventory = \AtumMultiInventory\Inc\Helpers::get_inventory($atum_inventory_id, 0, false, true);
            if ($inventory && $inventory->id) {
                error_log("DS POS MI FALLBACK: Loaded inventory ID {$atum_inventory_id} directly for '{$item->get_name()}'");
                return [$inventory];
            }
        } catch (\Exception $e) {
            error_log("DS POS MI FALLBACK: Error loading inventory {$atum_inventory_id}: " . $e->getMessage());
        }
    }

    error_log("DS POS MI FALLBACK: Inventory ID {$atum_inventory_id} not found, using ATUM default for '{$item->get_name()}'");
    return $inventories;
}, 10, 2);

error_log("DS POS Fulfillment Location hooks loaded (mi_inventories upstream + fallback filter)");
