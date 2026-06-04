<?php
/**
 * Custom WordPress REST API endpoint for updating YITH Points & Rewards
 * Add this to your WordPress theme's functions.php or create a custom plugin
 */

// Register custom REST API endpoint
add_action('rest_api_init', function () {
    register_rest_route('custom/v1', '/update-points', array(
        'methods' => 'POST',
        'callback' => 'update_customer_points',
        'permission_callback' => 'check_points_update_permission',
    ));
});

// Permission callback - check if user has WooCommerce manage permissions
function check_points_update_permission($request) {
    // Check if user is authenticated and has proper capabilities
    if (!current_user_can('manage_woocommerce')) {
        return new WP_Error('rest_forbidden', 'You do not have permission to update points.', array('status' => 403));
    }
    return true;
}

// Main function to update customer points
function update_customer_points($request) {
    $user_id = $request->get_param('user_id');
    $points = $request->get_param('points');
    
    if (!$user_id || !is_numeric($points)) {
        return new WP_Error('invalid_params', 'Invalid user_id or points value.', array('status' => 400));
    }
    
    // Log the attempt
    error_log("Custom endpoint: Attempting to update points for user {$user_id} to {$points}");
    
    // Try multiple meta keys that YITH might use
    $meta_keys = [
        '_ywpar_user_total_points',
        'wc_points_balance',
        '_yith_ywpar_customer_total_points', 
        'ywpar_user_total_points'
    ];
    
    $updated_keys = [];
    
    foreach ($meta_keys as $meta_key) {
        $result = update_user_meta($user_id, $meta_key, $points);
        if ($result !== false) {
            $updated_keys[] = $meta_key;
            error_log("Custom endpoint: Successfully updated {$meta_key} for user {$user_id}");
        }
    }
    
    // Also try to trigger YITH plugin hooks if they exist
    if (function_exists('YITH_WC_Points_Rewards')) {
        try {
            // Try to get YITH instance and update points
            $yith_instance = YITH_WC_Points_Rewards();
            if (method_exists($yith_instance, 'update_user_points')) {
                $yith_instance->update_user_points($user_id, $points);
                error_log("Custom endpoint: Updated via YITH instance method");
            }
        } catch (Exception $e) {
            error_log("Custom endpoint: YITH instance update failed: " . $e->getMessage());
        }
    }
    
    // Return success response
    return array(
        'success' => true,
        'user_id' => $user_id,
        'points' => $points,
        'updated_keys' => $updated_keys,
        'message' => 'Points updated successfully'
    );
}

// Alternative: Add this action hook to update points when called
add_action('wp_ajax_update_customer_points', 'handle_ajax_points_update');
add_action('wp_ajax_nopriv_update_customer_points', 'handle_ajax_points_update');

function handle_ajax_points_update() {
    // Verify nonce for security (optional for API calls with proper auth)
    $user_id = intval($_POST['user_id']);
    $points = floatval($_POST['points']);
    
    if ($user_id && is_numeric($points)) {
        // Update all possible meta keys
        update_user_meta($user_id, '_ywpar_user_total_points', $points);
        update_user_meta($user_id, 'wc_points_balance', $points);
        update_user_meta($user_id, '_yith_ywpar_customer_total_points', $points);
        update_user_meta($user_id, 'ywpar_user_total_points', $points);
        
        wp_send_json_success(array(
            'message' => 'Points updated successfully',
            'user_id' => $user_id,
            'points' => $points
        ));
    } else {
        wp_send_json_error('Invalid parameters');
    }
}
?>
