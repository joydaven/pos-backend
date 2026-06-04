import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as http_status
from .woocommerce import WooCommerceAPI
from requests.exceptions import RequestException
import datetime

logger = logging.getLogger(__name__)

# Create a more reliable debug log
def debug_log(message, membership_id=None):
    """
    Debug logging function for membership actions
    """
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_message = f"🔧 MEMBERSHIP DEBUG: [{timestamp}]"
    if membership_id:
        log_message += f" [Membership {membership_id}]"
    log_message += f" {message}"
    
    # Print to console
    print(log_message)
    
    # Write to file
    try:
        with open('/tmp/membership_actions_debug.log', 'a') as f:
            f.write(log_message + '\n')
    except Exception as e:
        print(f"Failed to write to debug log: {e}")

def find_subscription_by_parent_order(wc_api, parent_order_id):
    """
    Find subscription ID by parent order ID
    """
    try:
        debug_log(f"Searching for subscription with parent order: {parent_order_id}")
        
        # Get all subscriptions and filter by parent order
        response = wc_api.wcapi.get("subscriptions", params={'parent': parent_order_id})
        
        if response.ok:
            subscriptions = response.json()
            debug_log(f"Found {len(subscriptions)} subscriptions for parent order {parent_order_id}")
            
            if subscriptions:
                subscription_id = subscriptions[0].get('id')
                debug_log(f"Using subscription ID: {subscription_id}")
                return subscription_id
        else:
            debug_log(f"Failed to fetch subscriptions: {response.status_code} - {response.text}")
            
    except Exception as e:
        debug_log(f"Error finding subscription by parent order: {str(e)}")
    
    return None

def update_subscription_status(wc_api, subscription_id, status, action_name):
    """
    Update subscription status to match membership action
    """
    try:
        debug_log(f"Updating subscription {subscription_id} status to: {status}")
        
        # Map membership statuses to subscription statuses
        subscription_status_map = {
            'paused': 'on-hold',
            'active': 'active', 
            'cancelled': 'cancelled'
        }
        
        subscription_status = subscription_status_map.get(status, status)
        debug_log(f"Mapped to subscription status: {subscription_status}")
        
        update_data = {'status': subscription_status}
        
        # When cancelling, clear next_payment_date and set end_date
        if subscription_status == 'cancelled':
            from datetime import datetime
            current_datetime = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            update_data['next_payment_date'] = ''
            update_data['end_date'] = current_datetime
            debug_log(f"Cancelling subscription: clearing next_payment_date, setting end_date to {current_datetime}")
        
        response = wc_api.wcapi.put(f"subscriptions/{subscription_id}", update_data)
        
        if response.ok:
            debug_log(f"✅ Successfully updated subscription {subscription_id} to {subscription_status}")
            return True
        else:
            debug_log(f"❌ Failed to update subscription {subscription_id}: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        debug_log(f"Error updating subscription status: {str(e)}")
        return False

def update_membership_status(membership_id, new_status, action_name):
    """
    Common function to update membership status via WooCommerce API
    Also synchronizes the associated subscription status
    """
    debug_log(f"Starting {action_name} action", membership_id)
    
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        debug_log("WooCommerce API initialized", membership_id)
        
        # First, get the current membership to extract order_id
        debug_log("Fetching current membership details to get order_id", membership_id)
        membership_response = wc_api.wcapi.get(f"memberships/members/{membership_id}")
        
        subscription_id = None
        if membership_response.ok:
            membership_data = membership_response.json()
            order_id = membership_data.get('order_id')
            debug_log(f"Found parent order ID: {order_id}", membership_id)
            
            if order_id:
                # Find associated subscription
                subscription_id = find_subscription_by_parent_order(wc_api, order_id)
                if subscription_id:
                    debug_log(f"Found associated subscription: {subscription_id}", membership_id)
                else:
                    debug_log("No subscription found for this membership", membership_id)
        else:
            debug_log(f"Failed to fetch membership details: {membership_response.text}", membership_id)
        
        # Prepare update data
        update_data = {'status': new_status}
        
        # If cancelling, also set the end_date to now
        if new_status == 'cancelled':
            from datetime import datetime
            current_datetime = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            update_data['end_date'] = current_datetime
            debug_log(f"Setting end_date to {current_datetime} for cancelled membership", membership_id)
        
        debug_log(f"Update data prepared: {update_data}", membership_id)
        
        # Get customer ID from membership for additional endpoints
        customer_id = None
        if membership_response.ok:
            membership_data = membership_response.json()
            customer_id = membership_data.get('customer_id')
            debug_log(f"Found customer ID: {customer_id}", membership_id)
        
        # Try multiple WooCommerce Memberships API endpoints
        endpoints = [
            f"wc-memberships/v1/memberships/{membership_id}",
            f"memberships/{membership_id}",
            f"wp/v2/wc_user_membership/{membership_id}",
            f"memberships/members/{membership_id}"
        ]
        
        # Add customer-based endpoint if we have customer_id
        if customer_id:
            endpoints.append(f"customers/{customer_id}/memberships/{membership_id}")
        
        debug_log(f"Trying {len(endpoints)} endpoints", membership_id)
        
        updated_membership = None
        endpoint_used = None
        
        for i, endpoint in enumerate(endpoints, 1):
            debug_log(f"Attempt {i}/{len(endpoints)}: {endpoint}", membership_id)
            
            try:
                response = wc_api.wcapi.put(endpoint, update_data)
                debug_log(f"Endpoint {endpoint} returned status: {response.status_code}", membership_id)
                
                if response.ok:
                    updated_membership = response.json()
                    endpoint_used = endpoint
                    debug_log(f"SUCCESS: Endpoint {endpoint} worked!", membership_id)
                    break
                else:
                    # Check if WooCommerce returned valid data even with error status (bug workaround)
                    try:
                        response_data = response.json()
                        # If response contains membership data with the expected status, treat as success
                        if (response_data.get('id') == membership_id and 
                            response_data.get('status') == new_status):
                            debug_log(f"WORKAROUND: Endpoint {endpoint} returned {response.status_code} but data was updated successfully!", membership_id)
                            updated_membership = response_data
                            endpoint_used = endpoint
                            break
                    except:
                        pass  # Response isn't JSON or doesn't contain expected data
                    
                    debug_log(f"Endpoint {endpoint} failed with status {response.status_code}: {response.text}", membership_id)
                    
            except Exception as e:
                debug_log(f"Exception with endpoint {endpoint}: {str(e)}", membership_id)
                continue
        
        if not updated_membership:
            debug_log("ERROR: All WooCommerce API endpoints failed", membership_id)
            # Log the last response for debugging
            if 'response' in locals():
                debug_log(f"Last API response: Status {response.status_code}, Body: {response.text}", membership_id)
            
            # Try one more time with a different approach - using basic WP REST API
            try:
                debug_log("Attempting direct WP REST API approach", membership_id)
                wp_endpoint = f"wp/v2/users/{membership_id}/memberships"  # Alternative approach
                response = wc_api.wcapi.put(wp_endpoint, update_data)
                debug_log(f"WP REST API response: {response.status_code}", membership_id)
                
                if response.ok:
                    updated_membership = response.json()
                    endpoint_used = wp_endpoint
                    debug_log("SUCCESS: WP REST API approach worked!", membership_id)
                else:
                    debug_log(f"WP REST API also failed: {response.text}", membership_id)
            except Exception as wp_e:
                debug_log(f"WP REST API exception: {str(wp_e)}", membership_id)
            
            if not updated_membership:
                raise Exception(f"All WooCommerce API endpoints failed. Last error: {response.text if 'response' in locals() else 'Unknown error'}")
        
        # Get actual status from WooCommerce response
        actual_status = updated_membership.get('status', new_status)
        debug_log(f"SUCCESS: Membership updated via {endpoint_used}!", membership_id)
        debug_log(f"Expected status: {new_status}, Actual status: {actual_status}", membership_id)
        
        # Now update the associated subscription if found
        subscription_updated = False
        if subscription_id:
            subscription_updated = update_subscription_status(wc_api, subscription_id, new_status, action_name)
        
        debug_log(f"Full WooCommerce response: {updated_membership}", membership_id)
        
        return {
            'success': True,
            'message': f'Membership {action_name} successfully',
            'membership_id': membership_id,
            'subscription_id': subscription_id,
            'subscription_updated': subscription_updated,
            'new_status': actual_status or new_status,
            'endpoint_used': endpoint_used,
            'membership': {
                'id': updated_membership.get('id'),
                'status': updated_membership.get('status'),
                'plan_name': updated_membership.get('plan_name'),
                'customer_id': updated_membership.get('customer_id')
            }
        }
        
    except Exception as e:
        error_msg = f"Error during {action_name}: {str(e)}"
        debug_log(f"EXCEPTION: {error_msg}", membership_id)
        raise Exception(error_msg)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def pause_membership(request, membership_id):
    """
    Pause a WooCommerce membership
    """
    debug_log("=== PAUSE MEMBERSHIP REQUEST ===", membership_id)
    
    try:
        result = update_membership_status(membership_id, 'paused', 'paused')
        debug_log("Pause action completed successfully", membership_id)
        return Response(result, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Failed to pause membership: {str(e)}"
        debug_log(f"Pause action failed: {error_msg}", membership_id)
        return Response({
            'error': error_msg,
            'membership_id': membership_id
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resume_membership(request, membership_id):
    """
    Resume (activate) a WooCommerce membership
    """
    debug_log("=== RESUME MEMBERSHIP REQUEST ===", membership_id)
    
    try:
        result = update_membership_status(membership_id, 'active', 'resumed')
        debug_log("Resume action completed successfully", membership_id)
        return Response(result, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Failed to resume membership: {str(e)}"
        debug_log(f"Resume action failed: {error_msg}", membership_id)
        return Response({
            'error': error_msg,
            'membership_id': membership_id
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_membership(request, membership_id):
    """
    Cancel a WooCommerce membership
    """
    debug_log("=== CANCEL MEMBERSHIP REQUEST ===", membership_id)
    
    try:
        result = update_membership_status(membership_id, 'cancelled', 'cancelled')
        debug_log("Cancel action completed successfully", membership_id)
        
        # Auto-send cancellation email to customer
        try:
            from .models import POSSetting
            send_email = POSSetting.get_setting('membership_cancellation_email_enabled', True)
            if send_email:
                from .views_receipts import send_cancellation_email
                membership_data = result.get('membership', {})
                customer_id = membership_data.get('customer_id')
                plan_name = membership_data.get('plan_name', 'Membership')
                subscription_id = result.get('subscription_id')
                cancellation_date = datetime.datetime.now().strftime('%b %d, %Y')
                
                # Fetch subscription details from WooCommerce for rich email
                sub_total = ''
                billing_period = ''
                last_order_date = ''
                end_date = ''
                line_items = []
                billing = {}
                parent_order_id = None
                if subscription_id:
                    try:
                        wc_sub = WooCommerceAPI()
                        sub_resp = wc_sub.wcapi.get(f"subscriptions/{subscription_id}")
                        if sub_resp.ok:
                            sub_data = sub_resp.json()
                            sub_total_raw = sub_data.get('total', '')
                            bp = sub_data.get('billing_period', 'month')
                            bi = sub_data.get('billing_interval', 1)
                            billing_period = bp
                            if sub_total_raw:
                                try:
                                    interval_str = f" every {bi} " if bi and int(bi) > 1 else ' / '
                                    sub_total = f"${float(sub_total_raw):.2f}{interval_str}{bp}"
                                except (ValueError, TypeError):
                                    sub_total = str(sub_total_raw)
                            # Last order date
                            date_completed = sub_data.get('date_completed', '') or sub_data.get('date_paid', '')
                            if date_completed:
                                try:
                                    parsed = datetime.datetime.fromisoformat(date_completed.replace('Z', '+00:00'))
                                    last_order_date = parsed.strftime('%b %d, %Y')
                                except Exception:
                                    last_order_date = str(date_completed)[:10]
                            # End of prepaid term
                            schedule_end = sub_data.get('schedule_end', '') or sub_data.get('end_date', '')
                            if schedule_end:
                                try:
                                    parsed = datetime.datetime.fromisoformat(schedule_end.replace('Z', '+00:00'))
                                    end_date = parsed.strftime('%b %d, %Y')
                                except Exception:
                                    end_date = str(schedule_end)[:10]
                            line_items = sub_data.get('line_items', [])
                            billing = sub_data.get('billing', {})
                            parent_order_id = sub_data.get('parent_id', None)
                            discount_total = float(sub_data.get('discount_total', '0.00'))
                            if not plan_name or plan_name == 'Membership':
                                if line_items:
                                    plan_name = line_items[0].get('name', 'Membership')
                    except Exception as sub_fetch_err:
                        debug_log(f"Could not fetch subscription details for email: {sub_fetch_err}", membership_id)
                
                # Get customer email from WooCommerce
                if customer_id:
                    try:
                        wc_email = WooCommerceAPI()
                        cust_resp = wc_email.wcapi.get(f"customers/{customer_id}")
                        if cust_resp.ok:
                            cust_data = cust_resp.json()
                            customer_email = cust_data.get('email', '')
                            customer_name = f"{cust_data.get('first_name', '')} {cust_data.get('last_name', '')}".strip()
                            if customer_email:
                                email_sent = send_cancellation_email(
                                    customer_email, customer_name, plan_name, subscription_id, cancellation_date,
                                    status='cancelled', subscription_total=sub_total, billing_period=billing_period,
                                    last_order_date=last_order_date, end_date=end_date, line_items=line_items,
                                    billing=billing, parent_order_id=parent_order_id, discount_total=discount_total,
                                )
                                result['cancellation_email_sent'] = email_sent
                                debug_log(f"Cancellation email {'sent' if email_sent else 'failed'} to {customer_email}", membership_id)
                            else:
                                debug_log("No customer email found, skipping cancellation email", membership_id)
                    except Exception as email_fetch_err:
                        debug_log(f"Error fetching customer for email: {email_fetch_err}", membership_id)
            else:
                debug_log("Cancellation email disabled via settings", membership_id)
        except Exception as email_err:
            debug_log(f"Error sending cancellation email: {email_err}", membership_id)
        
        return Response(result, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Failed to cancel membership: {str(e)}"
        debug_log(f"Cancel action failed: {error_msg}", membership_id)
        return Response({
            'error': error_msg,
            'membership_id': membership_id
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def delete_membership(request, membership_id):
    """
    Delete a WooCommerce membership (sets to cancelled status)
    """
    debug_log("=== DELETE MEMBERSHIP REQUEST ===", membership_id)
    
    try:
        # For WooCommerce Memberships, "delete" means setting status to cancelled
        # This is the closest equivalent to deletion in the membership system
        result = update_membership_status(membership_id, 'cancelled', 'deleted')
        debug_log("Delete action completed successfully", membership_id)
        return Response(result, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Failed to delete membership: {str(e)}"
        debug_log(f"Delete action failed: {error_msg}", membership_id)
        return Response({
            'error': error_msg,
            'membership_id': membership_id
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_membership_status(request, membership_id):
    """
    Get current status of a WooCommerce membership
    """
    debug_log("=== GET MEMBERSHIP STATUS REQUEST ===", membership_id)
    
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        debug_log(f"Getting current status for membership", membership_id)
        
        # Try different endpoints to get membership data
        endpoints_to_try = [
            f"wc-memberships/v1/memberships/{membership_id}",
            f"memberships/{membership_id}",
            f"wp/v2/wc_user_membership/{membership_id}",
            f"memberships/members/{membership_id}"
        ]
        
        response = None
        endpoint_used = None
        
        for endpoint in endpoints_to_try:
            try:
                response = wc_api.wcapi.get(endpoint)
                debug_log(f"GET endpoint {endpoint} returned status: {response.status_code}", membership_id)
                
                if response.ok:
                    endpoint_used = endpoint
                    break
                    
            except Exception as e:
                debug_log(f"GET endpoint {endpoint} failed: {str(e)}", membership_id)
        
        if not response or not response.ok:
            raise Exception("Failed to get membership data from WooCommerce")
        
        membership_data = response.json()
        current_status = membership_data.get('status')
        
        debug_log(f"Current membership status: {current_status}", membership_id)
        
        return Response({
            'success': True,
            'membership_id': membership_id,
            'current_status': current_status,
            'endpoint_used': endpoint_used,
            'membership': {
                'id': membership_data.get('id'),
                'status': membership_data.get('status'),
                'plan_name': membership_data.get('plan_name'),
                'customer_id': membership_data.get('customer_id'),
                'start_date': membership_data.get('start_date'),
                'end_date': membership_data.get('end_date')
            }
        }, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Failed to get membership status: {str(e)}"
        debug_log(f"Get status failed: {error_msg}", membership_id)
        return Response({
            'error': error_msg,
            'membership_id': membership_id
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
