import logging
import time as _time
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as http_status
from .woocommerce import WooCommerceAPI
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_woocommerce_memberships(request, woo_customer_id):
    """
    Get all WooCommerce Memberships for a specific customer via WooCommerce Memberships API
    This fetches actual memberships (like "Doctors Studio Membership"), not subscriptions
    
    Query Parameters:
    - status: Optional status filter ('active', 'cancelled', etc.)
    """
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get status parameter
        status_filter = request.query_params.get('status')
        
        logger.info(f"Fetching WooCommerce memberships for customer ID: {woo_customer_id}")
        if status_filter:
            logger.info(f"Status filter: {status_filter}")
        
        # Use customer filter directly in the API call to avoid fetching ALL memberships
        params = {
            'per_page': 100,
            'customer': woo_customer_id,
            '_': str(int(_time.time() * 1000))  # Cache buster timestamp
        }
        if status_filter:
            params['status'] = status_filter
            
        logger.info(f"Fetching memberships with customer filter: {params}")

        # Run memberships + subscriptions fetches in parallel (independent data)
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_memberships():
            return wc_api.wcapi.get("memberships/members", params=params)

        def _fetch_all_subscriptions():
            subs = []
            sub_page = 1
            while True:
                sub_params = {
                    'customer': woo_customer_id,
                    'per_page': 100,
                    'page': sub_page,
                    '_': str(int(_time.time() * 1000))
                }
                sub_response = wc_api.wcapi.get("subscriptions", params=sub_params)
                if sub_response.ok:
                    sub_page_data = sub_response.json()
                    if not sub_page_data or len(sub_page_data) == 0:
                        break
                    subs.extend(sub_page_data)
                    if len(sub_page_data) < 100:
                        break
                    sub_page += 1
                    if sub_page > 10:
                        break
                else:
                    logger.error(f"Failed to fetch customer subscriptions: {sub_response.status_code}")
                    break
            return subs

        with ThreadPoolExecutor(max_workers=2) as executor:
            memberships_future = executor.submit(_fetch_memberships)
            subscriptions_future = executor.submit(_fetch_all_subscriptions)
            response = memberships_future.result()
            all_customer_subscriptions = subscriptions_future.result()

        if not response.ok:
            error_msg = f"Failed to fetch memberships. Status code: {response.status_code}, Response: {response.text}"
            logger.error(error_msg)
            return Response({
                'error': error_msg
            }, status=http_status.HTTP_400_BAD_REQUEST)

        all_memberships_data = response.json() or []
        logger.info(f"Retrieved {len(all_memberships_data)} memberships for customer {woo_customer_id}")

        # Filter to "Doctors Studio Membership" products only
        filtered_memberships_data = []
        for membership in all_memberships_data:
            plan_name = membership.get('plan_name', '').lower()
            if 'doctors studio membership' in plan_name:
                filtered_memberships_data.append(membership)
                logger.info(f"✅ Including membership {membership.get('id')} - plan '{membership.get('plan_name')}'")
            else:
                logger.info(f"❌ Excluding membership {membership.get('id')} - wrong plan '{plan_name}'")

        logger.info(f"Filtered {len(filtered_memberships_data)} of {len(all_memberships_data)} memberships (Doctors Studio only)")

        # ─── Process subscriptions (fetched in parallel above) ───
        logger.info(f"📋 Fetched {len(all_customer_subscriptions)} total subscriptions for customer {woo_customer_id}")

        membership_subscriptions = []
        membership_keywords = ['membership', 'member']
        for sub in all_customer_subscriptions:
            sub_items = sub.get('line_items', [])
            is_membership_sub = False
            for item in sub_items:
                item_name = (item.get('name') or '').lower()
                if any(kw in item_name for kw in membership_keywords):
                    is_membership_sub = True
                    break
            if is_membership_sub:
                membership_subscriptions.append(sub)
                logger.info(f"  ✅ Membership subscription #{sub.get('id')} status={sub.get('status')} start={sub.get('start_date_gmt')}")

        logger.info(f"📋 Found {len(membership_subscriptions)} membership-related subscriptions")
        
        # Sort membership subscriptions: active first, then by date (newest first)
        sub_status_priority = {'active': 0, 'on-hold': 1, 'pending': 2, 'cancelled': 3, 'expired': 4, 'trash': 5}
        membership_subscriptions.sort(key=lambda s: (
            sub_status_priority.get(s.get('status', ''), 9),
            -(int(_time.mktime(_time.strptime(s.get('start_date_gmt', '1970-01-01T00:00:00')[:19], '%Y-%m-%dT%H:%M:%S'))) if s.get('start_date_gmt') else 0)
        ))
        
        # Build subscription history for frontend
        subscription_history = []
        for sub in membership_subscriptions:
            sub_items_names = ', '.join([item.get('name', 'Unknown') for item in sub.get('line_items', [])])
            sub_total = sub.get('total', '0')
            sub_billing_period = sub.get('billing_period', '')
            sub_billing_interval = sub.get('billing_interval', '')
            
            history_entry = {
                'subscription_id': sub.get('id'),
                'status': sub.get('status'),
                'start_date': sub.get('start_date_gmt') or sub.get('start_date'),
                'end_date': sub.get('end_date_gmt') or sub.get('end_date'),
                'next_payment_date': sub.get('next_payment_date_gmt') or sub.get('next_payment_date'),
                'last_order_date': sub.get('last_order_date_created_gmt') or sub.get('date_created_gmt'),
                'total': sub_total,
                'billing_period': sub_billing_period,
                'billing_interval': sub_billing_interval,
                'items': sub_items_names,
                'created_via': sub.get('created_via', ''),
                'date_created': sub.get('date_created_gmt') or sub.get('date_created'),
                'payment_method_title': sub.get('payment_method_title', ''),
            }
            # Clear next_payment for cancelled/trash subscriptions
            if sub.get('status') in ('cancelled', 'trash', 'expired'):
                history_entry['next_payment_date'] = None
            subscription_history.append(history_entry)
        
        # Process each filtered membership for frontend consumption
        processed_memberships = []
        
        for membership in filtered_memberships_data:
            member_customer_id = membership.get('customer_id')
            logger.info(f"Processing membership {membership.get('id')} for customer {member_customer_id} (requested: {woo_customer_id})")
            
            # ─── Find associated subscription ───
            # Match from already-fetched customer subscriptions to avoid extra API calls
            subscription_id = None
            subscription_data = None
            order_id = membership.get('order_id')
            
            # Method 1: Match by parent order_id from already-fetched subscriptions
            # IMPORTANT: Also verify the subscription contains membership-related line items
            # to avoid linking unrelated subscriptions that share the same parent order
            if order_id:
                for sub in all_customer_subscriptions:
                    if sub.get('parent_id') == order_id:
                        # Verify this subscription has membership line items
                        sub_items = sub.get('line_items', [])
                        has_membership_item = any(
                            any(kw in (item.get('name') or '').lower() for kw in membership_keywords)
                            for item in sub_items
                        )
                        if has_membership_item:
                            subscription_data = sub
                            subscription_id = sub.get('id')
                            logger.info(f"Found subscription ID: {subscription_id} via parent order {order_id} (from cached list)")
                            break
                        else:
                            logger.info(f"⏭️ Skipping subscription #{sub.get('id')} — parent order matches but no membership line items")
            
            # Method 2: Fallback - use the latest active (or newest) membership subscription
            if not subscription_data and membership_subscriptions:
                # membership_subscriptions is already sorted: active first, then newest
                subscription_data = membership_subscriptions[0]
                subscription_id = subscription_data.get('id')
                logger.info(f"🔄 Using latest membership subscription #{subscription_id} (status={subscription_data.get('status')}) as fallback for membership {membership.get('id')}")
            
            # Extract subscription details if available
            subscription_start_date = None
            subscription_next_payment = None
            subscription_last_order_date = None
            subscription_end_date = None
            subscription_status = None
            
            if subscription_data:
                subscription_start_date = subscription_data.get('start_date_gmt') or subscription_data.get('start_date')
                subscription_next_payment = subscription_data.get('next_payment_date_gmt') or subscription_data.get('next_payment_date')
                subscription_end_date = subscription_data.get('end_date_gmt') or subscription_data.get('end_date')
                subscription_status = subscription_data.get('status')
                subscription_last_order_date = subscription_data.get('last_order_date_created_gmt') or subscription_data.get('date_created_gmt') or subscription_data.get('last_order_date_created') or subscription_data.get('date_created')
                
                if subscription_status in ('cancelled', 'trash', 'expired'):
                    subscription_next_payment = None
                
                logger.info(f"Subscription {subscription_id} status: {subscription_status}, start: {subscription_start_date}")
            
            # Get dates
            end_date = membership.get('end_date_gmt') or membership.get('end_date')
            cancelled_date = membership.get('cancelled_date_gmt') or membership.get('cancelled_date')
            membership_status = membership.get('status')
            
            if membership_status == 'cancelled' and not end_date and cancelled_date:
                end_date = cancelled_date
                logger.info(f"Membership {membership.get('id')} is cancelled but end_date was null, using cancelled_date as end_date: {end_date}")
            
            # Extract specific product name from subscription line items if available.
            # The subscription line item name (e.g., "Doctors Studio Membership + 6 Months")
            # is more specific than the generic plan_name ("Doctors Studio Membership").
            subscription_product_name = None
            if subscription_data:
                for li in subscription_data.get('line_items', []):
                    li_name = (li.get('name') or '').lower()
                    if 'membership' in li_name or 'member' in li_name:
                        subscription_product_name = li.get('name')
                        break
                # If no membership-specific line item found, use the first line item
                if not subscription_product_name and subscription_data.get('line_items'):
                    subscription_product_name = subscription_data['line_items'][0].get('name')
            
            processed_membership = {
                'membership_id': membership.get('id'),
                'plan_id': membership.get('plan_id'),
                'plan_name': subscription_product_name or membership.get('plan_name'),
                'customer_id': membership.get('customer_id'),
                'status': membership_status,
                'order_id': membership.get('order_id'),
                'product_id': membership.get('product_id'),
                'subscription_id': subscription_id,
                
                # Membership Dates
                'start_date': membership.get('start_date_gmt') or membership.get('start_date'),
                'end_date': end_date,
                'paused_date': membership.get('paused_date_gmt') or membership.get('paused_date'),
                'cancelled_date': cancelled_date,
                'date_created': membership.get('date_created_gmt') or membership.get('date_created'),
                
                # Subscription Dates (from associated subscription - now uses latest match)
                'subscription_start_date': subscription_start_date,
                'subscription_next_payment': subscription_next_payment,
                'subscription_last_order_date': subscription_last_order_date,
                'subscription_end_date': subscription_end_date,
                'subscription_status': subscription_status,
                
                # Subscription billing info (for displaying price/frequency)
                'billing_period': subscription_data.get('billing_period') if subscription_data else None,
                'billing_interval': int(subscription_data.get('billing_interval', 0) or 0) if subscription_data else None,
                'total_amount': float(subscription_data.get('total', 0) or 0) if subscription_data else 0,
                
                # Additional info
                'view_url': membership.get('view_url'),
                'profile_fields': membership.get('profile_fields', []),
                'meta_data': membership.get('meta_data', [])
            }
            
            processed_memberships.append(processed_membership)
        
        # Log membership statuses for debugging
        status_counts = {}
        for membership in processed_memberships:
            status = membership.get('status', 'unknown')
            status_counts[status] = status_counts.get(status, 0) + 1
        logger.info(f"Membership statuses: {status_counts}")
        
        # Lazy-update: persist is_active_member on Contact so the badge shows instantly next time
        has_active = status_counts.get('active', 0) > 0
        try:
            from .models import Contact
            updated = Contact.objects.filter(woo_customer_id=woo_customer_id).exclude(
                is_active_member=has_active
            ).update(is_active_member=has_active)
            if updated:
                logger.info(f"Updated is_active_member={has_active} for woo_customer_id={woo_customer_id}")
        except Exception as e:
            logger.warning(f"Could not update is_active_member for woo_customer_id={woo_customer_id}: {e}")
        
        return Response({
            'memberships': processed_memberships,
            'total_count': len(processed_memberships),
            'status_counts': status_counts,
            'subscription_history': subscription_history
        }, status=http_status.HTTP_200_OK)
        
    except RequestException as e:
        error_msg = f"Request error fetching memberships: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception as e:
        error_msg = f"Error fetching customer memberships from WooCommerce API: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def manage_membership(request, membership_id):
    """
    Manage a WooCommerce membership (pause, resume, cancel)
    """
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get the action from request
        action = request.data.get('action')
        
        if not action:
            return Response({
                'error': 'action is required'
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        # Debug to file
        import datetime
        debug_file = '/tmp/membership_debug.log'
        with open(debug_file, 'a') as f:
            f.write(f"{datetime.datetime.now()}: 🔄 Managing membership {membership_id} with action: {action}\n")
        
        print(f"🔄 DEBUG: Managing membership {membership_id} with action: {action}")
        
        # Map frontend actions to WooCommerce membership statuses (with wcm- prefix)
        action_mapping = {
            'pause': 'wcm-paused',
            'resume': 'wcm-active', 
            'cancel': 'wcm-cancelled',
            'delete': 'wcm-cancelled'  # Delete maps to cancelled for memberships
        }
        
        if action not in action_mapping:
            print(f"❌ DEBUG: Invalid action {action}. Valid actions: {list(action_mapping.keys())}")
            return Response({
                'error': f'Invalid action. Must be one of: {list(action_mapping.keys())}'
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        new_status = action_mapping[action]
        print(f"🎯 DEBUG: Mapping action '{action}' to status '{new_status}'")
        
        # Update membership via WooCommerce API
        update_data = {'status': new_status}
        
        print(f"📤 DEBUG: Sending update to WooCommerce: PUT memberships/members/{membership_id}")
        print(f"📤 DEBUG: Update data: {update_data}")
        
        # Try different endpoints for WooCommerce Memberships
        endpoints_to_try = [
            f"wc-memberships/v1/memberships/{membership_id}",  # WooCommerce Memberships v1 API
            f"memberships/{membership_id}",  # Alternative endpoint
            f"memberships/members/{membership_id}"  # Original endpoint
        ]
        
        response = None
        endpoint_used = None
        
        for endpoint in endpoints_to_try:
            with open(debug_file, 'a') as f:
                f.write(f"{datetime.datetime.now()}: 🔄 Trying endpoint: {endpoint}\n")
            
            try:
                response = wc_api.wcapi.put(endpoint, update_data)
                with open(debug_file, 'a') as f:
                    f.write(f"{datetime.datetime.now()}: 📋 Endpoint {endpoint} returned status: {response.status_code}\n")
                
                if response.ok:
                    endpoint_used = endpoint
                    break
                    
            except Exception as e:
                with open(debug_file, 'a') as f:
                    f.write(f"{datetime.datetime.now()}: ❌ Endpoint {endpoint} failed: {str(e)}\n")
        
        if not response:
            with open(debug_file, 'a') as f:
                f.write(f"{datetime.datetime.now()}: ❌ All endpoints failed\n")
            raise Exception("All membership API endpoints failed")
        
        # Debug WooCommerce response to file
        with open(debug_file, 'a') as f:
            f.write(f"{datetime.datetime.now()}: ✅ Used endpoint: {endpoint_used}\n")
            f.write(f"{datetime.datetime.now()}: 📥 WooCommerce response status: {response.status_code}\n")
            f.write(f"{datetime.datetime.now()}: 📥 WooCommerce response body: {response.text}\n")
        
        print(f"📥 DEBUG: WooCommerce response status: {response.status_code}")
        print(f"📥 DEBUG: WooCommerce response body: {response.text}")
        
        if not response.ok:
            error_msg = f"Failed to {action} membership. Status: {response.status_code}, Response: {response.text}"
            with open(debug_file, 'a') as f:
                f.write(f"{datetime.datetime.now()}: ❌ ERROR: {error_msg}\n")
            print(f"❌ DEBUG: {error_msg}")
            return Response({
                'error': error_msg
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        # Parse response
        updated_membership = response.json()
        actual_status = updated_membership.get('status')
        
        with open(debug_file, 'a') as f:
            f.write(f"{datetime.datetime.now()}: ✅ SUCCESS: Endpoint {endpoint_used} worked!\n")
            f.write(f"{datetime.datetime.now()}: ✅ Expected status: {new_status}\n")
            f.write(f"{datetime.datetime.now()}: ✅ Actual status from WooCommerce: {actual_status}\n")
            f.write(f"{datetime.datetime.now()}: ✅ Full membership data: {updated_membership}\n")
        
        print(f"✅ DEBUG: Successfully {action}d membership {membership_id}")
        print(f"✅ DEBUG: Expected status: {new_status}")
        print(f"✅ DEBUG: Actual status from WooCommerce: {actual_status}")
        print(f"✅ DEBUG: Full membership data: {updated_membership}")
        
        return Response({
            'success': True,
            'message': f'Membership {action}d successfully',
            'membership_id': membership_id,
            'new_status': updated_membership.get('status', new_status),
            'membership': {
                'id': updated_membership.get('id'),
                'status': updated_membership.get('status'),
                'plan_name': updated_membership.get('plan_name')
            }
        }, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Error managing membership: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
