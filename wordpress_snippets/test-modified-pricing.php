<?php
/**
 * Test script to verify modified pricing metadata
 * Add this temporarily to functions.php to test metadata flow
 */

// Hook to log order creation for DS POS orders
add_action('woocommerce_rest_insert_shop_order_object', 'test_ds_pos_metadata_logging', 5, 3);

function test_ds_pos_metadata_logging($order, $request, $creating) {
    if (!$creating) {
        return;
    }
    
    // Check if this is a DS POS order
    $created_via = $order->get_created_via();
    $source = $order->get_meta('source');
    $order_source = $order->get_meta('_order_source');
    
    $is_ds_pos = (
        $created_via === 'DS POS' ||
        $source === 'DS POS' ||
        $order_source === 'DS POS'
    );
    
    if (!$is_ds_pos) {
        return;
    }
    
    error_log("=== DS POS ORDER METADATA TEST - Order ID: " . $order->get_id() . " ===");
    error_log("Created via: " . $created_via);
    error_log("Source: " . $source);
    error_log("Order source: " . $order_source);
    
    foreach ($order->get_items() as $item_id => $item) {
        error_log("--- Item {$item_id}: " . $item->get_name() . " ---");
        error_log("Item subtotal: $" . $item->get_subtotal());
        error_log("Item total: $" . $item->get_total());
        
        // Check all meta data
        $meta_data = [];
        foreach ($item->get_meta_data() as $meta) {
            $meta_data[$meta->key] = $meta->value;
        }
        
        error_log("All item metadata: " . json_encode($meta_data));
        
        // Check specific pricing metadata
        $original_price = $item->get_meta('_ds_pos_original_price');
        $modified_price = $item->get_meta('_ds_pos_modified_price'); 
        $discount_amount = $item->get_meta('_ds_pos_discount_amount');
        $modified_pricing_flag = $item->get_meta('_ds_pos_modified_pricing');
        
        error_log("DS POS pricing metadata:");
        error_log("  _ds_pos_original_price: " . ($original_price ?: 'NOT SET'));
        error_log("  _ds_pos_modified_price: " . ($modified_price ?: 'NOT SET'));
        error_log("  _ds_pos_discount_amount: " . ($discount_amount ?: 'NOT SET'));
        error_log("  _ds_pos_modified_pricing: " . ($modified_pricing_flag ?: 'NOT SET'));
    }
    
    error_log("=== END DS POS ORDER METADATA TEST ===");
}

?>
