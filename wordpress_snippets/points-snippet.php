// Register custom REST API endpoint
add_action('rest_api_init', function () {
    register_rest_route('custom/v1', '/update-points', array(
        'methods' => 'POST',
        'callback' => 'pos_update_customer_points',
        'permission_callback' => 'pos_check_points_update_permission',
    ));
});
 
// Permission callback - check if request has proper authentication
function pos_check_points_update_permission($request) {
    // For WooCommerce API authentication, check if consumer key/secret are valid
    $auth_header = $request->get_header('authorization');
 
    if ($auth_header) {
        // Basic auth with consumer key/secret should be handled by WooCommerce
        return true;
    }
 
    // Fallback: check if user has WooCommerce manage permissions
    if (current_user_can('manage_woocommerce')) {
        return true;
    }
 
    return new WP_Error('rest_forbidden', 'You do not have permission to update points.', array('status' => 403));
}
 
// Main function to update customer points
function pos_update_customer_points($request) {
    $user_id = $request->get_param('user_id');
    $points = $request->get_param('points');
 
    if (!$user_id || !is_numeric($points)) {
        return new WP_Error('invalid_params', 'Invalid user_id or points value.', array('status' => 400));
    }
 
    // Log the attempt
    error_log("POS Points Update: Attempting to update points for user {$user_id} to {$points}");
 
    // Get current points data
    $current_balance = get_user_meta($user_id, '_ywpar_user_total_points', true);
    $current_earned = get_user_meta($user_id, '_ywpar_user_total_earned_points', true);
 
    // Convert to numbers, default to 0 if empty
    $current_balance = floatval($current_balance);
    $current_earned = floatval($current_earned);
 
    // Calculate earned points - only update if it's currently 0 or less than the new balance
    if ($current_earned == 0 || $current_earned < $points) {
        // Set earned points to be at least equal to the new balance
        // This ensures points_collected >= points_to_redeem
        $current_earned = max($current_earned, $points);
        error_log("POS Points Update: Updated total earned points to: {$current_earned}");
    } else {
        // Keep existing earned points if they're already higher
        error_log("POS Points Update: Keeping existing earned points: {$current_earned}");
    }
 
    // Update current balance (available points)
    $balance_keys = [
        '_ywpar_user_total_points',
        'wc_points_balance',
        '_yith_ywpar_customer_total_points', 
        'ywpar_user_total_points'
    ];
 
    // Update earned points (lifetime total)
    $earned_keys = [
        '_ywpar_user_total_earned_points',
        '_ywpar_points_earned',
        'ywpar_points_earned',
        '_ywpar_total_earned',
        '_ywpar_points_collected'
    ];
 
    $updated_keys = [];
 
    // Update current balance
    foreach ($balance_keys as $meta_key) {
        $result = update_user_meta($user_id, $meta_key, $points);
        if ($result !== false) {
            $updated_keys[] = $meta_key;
            error_log("POS Points Update: Successfully updated balance {$meta_key} = {$points} for user {$user_id}");
        }
    }
 
    // Update earned points (set to higher value to show lifetime earned)
    foreach ($earned_keys as $meta_key) {
        $result = update_user_meta($user_id, $meta_key, $current_earned);
        if ($result !== false) {
            $updated_keys[] = $meta_key;
            error_log("POS Points Update: Successfully updated earned {$meta_key} = {$current_earned} for user {$user_id}");
        }
    }
 
    // Try to use YITH plugin functions if available
    if (class_exists('YITH_WC_Points_Rewards_Customer')) {
        try {
            $customer = new YITH_WC_Points_Rewards_Customer($user_id);
            if (method_exists($customer, 'set_total_points')) {
                $customer->set_total_points($points);
                error_log("POS Points Update: Updated via YITH customer class");
            }
        } catch (Exception $e) {
            error_log("POS Points Update: YITH customer class update failed: " . $e->getMessage());
        }
    }
 
    // Try YITH main class methods
    if (function_exists('YITH_WC_Points_Rewards')) {
        try {
            $yith_instance = YITH_WC_Points_Rewards();
            if (method_exists($yith_instance, 'update_user_points')) {
                $yith_instance->update_user_points($user_id, $points);
                error_log("POS Points Update: Updated via YITH instance method");
            }
        } catch (Exception $e) {
            error_log("POS Points Update: YITH instance update failed: " . $e->getMessage());
        }
    }
 
    // Trigger any YITH hooks that might exist
    do_action('ywpar_update_user_points', $user_id, $points);
 
    // Return success response
    return array(
        'success' => true,
        'user_id' => $user_id,
        'points' => $points,
        'current_earned' => $current_earned,
        'updated_keys' => $updated_keys,
        'message' => 'Points updated successfully'
    );
}