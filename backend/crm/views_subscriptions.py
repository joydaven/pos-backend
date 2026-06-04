import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as http_status
from .woocommerce import WooCommerceAPI
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)


def _header_int(headers, key, default=1):
    """
    Read numeric header values case-insensitively.
    requests headers may arrive with lowercase keys after dict conversion.
    """
    if not isinstance(headers, dict):
        return default

    value = headers.get(key)
    if value is None:
        value = headers.get(key.lower())
    if value is None:
        value = headers.get(key.upper())

    try:
        return int(value)
    except (TypeError, ValueError):
        return default

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_woocommerce_subscriptions(request, page=None, per_page=None):
    """
    Get subscriptions from WooCommerce with pagination and filtering
    Fetches ALL subscription statuses when filter is 'all' to ensure complete data
    """
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get parameters from either URL path or query parameters
        if page is None:
            page = int(request.query_params.get('page', 1))
        if per_page is None:
            per_page = int(request.query_params.get('per_page', 10))
            
        status_filter = request.query_params.get('status')
        payment_method = request.query_params.get('payment_method')
        search = request.query_params.get('search')
        include = request.query_params.get('include')
        parent = request.query_params.get('parent')
        
        logger.info(f"Fetching WooCommerce subscriptions with status filter: {status_filter}, page: {page}, per_page: {per_page}, search: {search}, include: {include}, parent: {parent}")
        
        # When status is 'all' or None, fetch using WooCommerce's default behavior
        # which returns subscriptions across all statuses more efficiently
        if not status_filter or status_filter == 'all':
            logger.info(f"Fetching subscriptions with 'any' status (page {page}, per_page {per_page})")
            
            # Use 'any' status to let WooCommerce return all statuses efficiently
            result = wc_api.get_subscriptions(
                page=page, 
                per_page=per_page,
                status='any',  # WooCommerce 'any' status returns all active statuses
                payment_method=payment_method,
                **({'search': search} if search else {}),
                **({'include': include} if include else {}),
                **({'parent': parent} if parent else {})
            )
            
            if result['status'] == 'warning':
                logger.warning(f"No subscriptions found: {result['message']}")
                return Response({
                    'subscriptions': [],
                    'total_pages': 1,
                    'current_page': page,
                    'total_items': 0,
                    'message': result['message']
                }, status=http_status.HTTP_200_OK)
                
            elif result['status'] == 'error':
                logger.error(f"Error fetching subscriptions: {result['message']}")
                return Response({
                    'error': result['message']
                }, status=http_status.HTTP_400_BAD_REQUEST)
            
            subscriptions_data = result['data']
            headers = result.get('headers', {})
            total_pages = _header_int(headers, 'X-WP-TotalPages', 1)
            total_items = _header_int(headers, 'X-WP-Total', len(subscriptions_data))
            
            logger.info(f"Fetched {len(subscriptions_data)} subscriptions (total: {total_items}, pages: {total_pages})")
            
        else:
            # Single status filter - fetch directly
            logger.info(f"Fetching subscriptions with specific status: {status_filter}")
            
            result = wc_api.get_subscriptions(
                page=page, 
                per_page=per_page, 
                status=status_filter, 
                payment_method=payment_method,
                **({'search': search} if search else {}),
                **({'include': include} if include else {}),
                **({'parent': parent} if parent else {})
            )
            
            logger.info(f"Subscription API result status: {result['status']}")
            
            # Handle different result statuses
            if result['status'] == 'warning':
                # No subscriptions found or endpoint not available
                logger.warning(f"No subscriptions found: {result['message']}")
                return Response({
                    'subscriptions': [],
                    'total_pages': 1,
                    'current_page': page,
                    'total_items': 0,
                    'message': result['message']
                }, status=http_status.HTTP_200_OK)
                
            elif result['status'] == 'error':
                # Error occurred
                logger.error(f"Error fetching subscriptions: {result['message']}")
                return Response({
                    'error': result['message']
                }, status=http_status.HTTP_400_BAD_REQUEST)
                
            # Success - process the data
            subscriptions_data = result['data']
            headers = result.get('headers', {})
            
            # Get pagination info from headers
            total_pages = _header_int(headers, 'X-WP-TotalPages', 1)
            total_items = _header_int(headers, 'X-WP-Total', len(subscriptions_data))
        
        logger.info(f"Successfully fetched {len(subscriptions_data)} subscriptions, page {page} of {total_pages}")
        
        # Process each subscription to add source and fix payment method display
        # First pass: extract source, created_by, and collect CIM lookup needs
        cim_lookups_needed = {}  # {customer_profile_id: [subscription_indices]}
        
        for idx, subscription in enumerate(subscriptions_data):
            # Check for subscription source in meta_data
            source = None
            if 'meta_data' in subscription and isinstance(subscription['meta_data'], list):
                for meta_item in subscription['meta_data']:
                    if meta_item.get('key') == '_order_source' or meta_item.get('key') == 'order_source':
                        source = meta_item.get('value')
                        break
            
            # Add source to subscription data if found
            if source:
                subscription['source'] = source
            else:
                subscription['source'] = 'woocommerce'  # Default source for WooCommerce subscriptions
                
            # Check for created_by in meta_data
            if 'meta_data' in subscription and isinstance(subscription['meta_data'], list):
                for meta_item in subscription['meta_data']:
                    if meta_item.get('key') == '_created_by':
                        subscription['created_by'] = meta_item.get('value')
                        break
                
            # Enrich payment method display
            payment_method_found = False
            meta_data = subscription.get('meta_data', []) if isinstance(subscription.get('meta_data'), list) else []
            
            # 1. Try _pos_payment_method (POS-created subscriptions)
            for meta_item in meta_data:
                if meta_item.get('key') == '_pos_payment_method' and meta_item.get('value'):
                    try:
                        import json
                        payment_data = json.loads(meta_item['value'])
                        if payment_data.get('name'):
                            subscription['payment_method_title'] = payment_data['name']
                            payment_method_found = True
                        elif payment_data.get('brand') and payment_data.get('last4'):
                            subscription['payment_method_title'] = f"{payment_data['brand']} ending in {payment_data['last4']}"
                            payment_method_found = True
                    except (json.JSONDecodeError, TypeError):
                        pass
                    break
            
            # 2. Try Authorize.net meta keys (_authnet_cc_type, _authnet_cc_last4)
            if not payment_method_found:
                authnet_brand = None
                authnet_last4 = None
                authnet_customer_id = None
                authnet_card_id = None
                for meta_item in meta_data:
                    key = meta_item.get('key', '')
                    value = meta_item.get('value', '')
                    if key == '_authnet_cc_type' and value:
                        authnet_brand = value
                    elif key == '_authnet_cc_last4' and value:
                        authnet_last4 = value
                    elif key == '_wc_authorize_net_cim_credit_card_card_type' and value:
                        authnet_brand = value
                    elif key == '_wc_authorize_net_cim_credit_card_last_four' and value:
                        authnet_last4 = value
                    elif key == '_authnet_customer_id' and value:
                        authnet_customer_id = value
                    elif key in ('_authnet_card_id', '_authorize_net_cim_credit_card_payment_token') and value:
                        authnet_card_id = value
                    elif key == '_authorize_net_cim_credit_card_customer_id' and value:
                        authnet_customer_id = value
                
                if authnet_brand and authnet_last4:
                    subscription['payment_method_title'] = f'{authnet_brand} ending in {authnet_last4}'
                    payment_method_found = True
                elif authnet_customer_id and authnet_card_id:
                    # Need CIM lookup — store for batch processing
                    subscription['_cim_customer_id'] = authnet_customer_id
                    subscription['_cim_card_id'] = authnet_card_id
                    if authnet_customer_id not in cim_lookups_needed:
                        cim_lookups_needed[authnet_customer_id] = []
                    cim_lookups_needed[authnet_customer_id].append(idx)
            
            # 3. Fallback for POS or empty titles
            if not payment_method_found and not subscription.get('_cim_customer_id'):
                if subscription.get('payment_method') == 'POS' or not subscription.get('payment_method_title') or subscription.get('payment_method_title') in ('POS Payment', 'Credit card'):
                    pm_found = False
                    for meta_item in meta_data:
                        if meta_item.get('key') in ('_payment_method', 'payment_method'):
                            subscription['payment_method_title'] = meta_item.get('value', 'Credit Card')
                            pm_found = True
                            break
                    
                    if not pm_found or not subscription.get('payment_method_title') or subscription.get('payment_method_title') == 'POS Payment':
                        if subscription.get('payment_method'):
                            method = subscription.get('payment_method')
                            if method.lower() == 'pos':
                                subscription['payment_method_title'] = 'Credit Card'
                            else:
                                subscription['payment_method_title'] = ' '.join(word.capitalize() for word in method.split('_'))
                        else:
                            subscription['payment_method_title'] = 'Credit Card'
        
        # Batch CIM lookups for subscriptions that need card details from Authorize.net
        if cim_lookups_needed:
            try:
                from payments.authorize_net import AuthorizeNetGateway
                for customer_profile_id, sub_indices in cim_lookups_needed.items():
                    try:
                        profiles = AuthorizeNetGateway.get_customer_payment_profiles(customer_profile_id)
                        if profiles:
                            # Build lookup by payment_profile_id
                            profile_map = {p['payment_profile_id']: p for p in profiles}
                            for sub_idx in sub_indices:
                                sub = subscriptions_data[sub_idx]
                                card_id = str(sub.get('_cim_card_id', ''))
                                if card_id in profile_map:
                                    card = profile_map[card_id]
                                    brand = card.get('card_brand', 'Credit Card')
                                    last4 = card.get('last4', '')
                                    if brand and last4:
                                        sub['payment_method_title'] = f'{brand} ending in {last4}'
                                    elif last4:
                                        sub['payment_method_title'] = f'Credit Card ending in {last4}'
                                else:
                                    logger.warning(f"Payment profile {card_id} not found in CIM customer {customer_profile_id}")
                    except Exception as e:
                        logger.warning(f"CIM lookup failed for customer profile {customer_profile_id}: {e}")
            except ImportError:
                logger.warning("Could not import AuthorizeNetGateway for CIM lookup")
        
        # Clean up temporary keys
        for subscription in subscriptions_data:
            subscription.pop('_cim_customer_id', None)
            subscription.pop('_cim_card_id', None)
        
        # Return subscription data
        return Response({
            'subscriptions': subscriptions_data,
            'total_pages': total_pages,
            'current_page': page,
            'total_items': total_items
        }, status=http_status.HTTP_200_OK)
        
    except RequestException as e:
        error_msg = f"Request error fetching subscriptions: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception as e:
        error_msg = f"Error fetching subscriptions: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_woocommerce_subscriptions(request, woo_customer_id):
    """
    Get all WooCommerce subscriptions for a specific customer via WooCommerce API using customer ID
    This includes payment method information with optional filtering for membership products
    
    Query Parameters:
    - membership_only: true/false - Filter for membership products only (default: false)
    """
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get membership_only parameter (default: false to show all subscriptions)
        membership_only = request.query_params.get('membership_only', 'false').lower() == 'true'
        
        logger.info(f"Fetching WooCommerce subscriptions for customer ID: {woo_customer_id}")
        logger.info(f"Membership filtering enabled: {membership_only}")
        
        # Use WooCommerce API customer parameter for precise subscription filtering
        # We'll fetch multiple pages to ensure we get all subscriptions
        all_subscriptions = []
        page = 1
        per_page = 100  # Higher per_page to reduce API calls
        
        # WooCommerce API requires separate calls for different statuses excluding trash
        # Exclude 'trash' status to hide permanently deleted subscriptions from customer view
        # Fetch all statuses in parallel (each handles its own pagination internally)
        statuses_to_fetch = ['active', 'on-hold', 'cancelled', 'pending-cancel', 'expired']
        status_counts = {}

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_status(status_name):
            """Fetch all pages for a single status."""
            status_subs = []
            pg = 1
            while True:
                result = wc_api.get_subscriptions(
                    page=pg,
                    per_page=per_page,
                    status=status_name,
                    customer=woo_customer_id
                )
                if result['status'] != 'success':
                    logger.warning(f"Failed to fetch subscriptions for status {status_name}: {result.get('message', 'Unknown error')}")
                    break
                subscriptions_data = result['data']
                if not subscriptions_data:
                    break
                status_subs.extend(subscriptions_data)
                logger.info(f"Added {len(subscriptions_data)} subscriptions with status {status_name}")
                headers = result.get('headers', {})
                total_pages = _header_int(headers, 'X-WP-TotalPages', 1)
                if pg >= total_pages:
                    break
                pg += 1
            return status_name, status_subs

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_fetch_status, s) for s in statuses_to_fetch]
            for future in as_completed(futures):
                status_name, subs = future.result()
                if subs:
                    all_subscriptions.extend(subs)
                    status_counts[status_name] = len(subs)

        logger.info(f"Fetched {len(all_subscriptions)} total subscriptions from WooCommerce for customer {woo_customer_id}")
        logger.info(f"Subscriptions by status: {status_counts}")
        
        # Since we filtered by customer ID in API call, all subscriptions are for the target customer
        customer_subscriptions = all_subscriptions
        
        logger.info(f"Found {len(customer_subscriptions)} subscriptions for customer ID {woo_customer_id}")
        
        # Log the subscription statuses we found
        statuses_found = {}
        for sub in customer_subscriptions:
            status = sub.get('status', 'unknown')
            statuses_found[status] = statuses_found.get(status, 0) + 1
        logger.info(f"Subscription statuses found: {statuses_found}")
        
        # Process subscriptions based on membership_only filter
        result_subscriptions = []
        membership_count = 0
        product_count = 0
        
        for subscription in customer_subscriptions:
            line_items = subscription.get('line_items', [])
            
            # Debug: Log subscription and line items
            logger.info(f"Processing subscription {subscription.get('id')} with {len(line_items)} line items")
            
            for item in line_items:
                product_name = item.get('name', '').lower()
                logger.info(f"Checking product: '{product_name}' (product_id: {item.get('product_id')}, variation_id: {item.get('variation_id')})")
                
                # Check if this is a membership-related product
                # Only check for 'membership' or 'member' keywords
                # Do NOT check for 'subscription' as it's too broad and includes non-membership products
                keywords = ['membership', 'member']
                is_membership = any(keyword in product_name for keyword in keywords)
                
                logger.info(f"Product '{product_name}' membership check: {is_membership} (keywords: {[kw for kw in keywords if kw in product_name]})")
                
                # Determine if we should include this subscription
                should_include = True
                subscription_type = "membership" if is_membership else "product"
                
                if membership_only and not is_membership:
                    should_include = False
                    logger.info(f"Skipping product subscription {subscription.get('id')} due to membership_only filter")
                
                if should_include:
                    # Map WooCommerce subscription statuses to frontend-friendly names
                    raw_status = subscription.get('status', '')
                    status_mapping = {
                        'wc-active': 'active',
                        'wc-on-hold': 'paused',
                        'wc-cancelled': 'cancelled', 
                        'wc-pending-cancel': 'pending_cancellation',
                        'wc-expired': 'expired',
                        'wc-pending': 'pending'
                    }
                    
                    # Use mapping or fallback to removing 'wc-' prefix
                    frontend_status = status_mapping.get(raw_status, raw_status.replace('wc-', '') if raw_status.startswith('wc-') else raw_status)
                    
                    # Format subscription data for frontend
                    formatted_subscription = {
                        'subscription_id': subscription.get('id'),
                        'product_id': item.get('product_id'),
                        'variation_id': item.get('variation_id'),
                        'product_name': item.get('name'),
                        'quantity': item.get('quantity', 1),
                        'line_total': float(item.get('total', 0)),
                        'subscription_status': frontend_status,
                        'raw_subscription_status': raw_status,
                        'start_date': subscription.get('start_date_gmt') or subscription.get('start_date'),
                        'next_payment_date': subscription.get('next_payment_date_gmt') or subscription.get('next_payment_date'),
                        'billing_period': subscription.get('billing_period'),
                        'billing_interval': subscription.get('billing_interval'),
                        'total_amount': float(subscription.get('total', 0)),
                        'subscription_type': subscription_type,  # Add subscription type for frontend
                        
                        # Payment method information (enhanced extraction)
                        'payment_method': subscription.get('payment_method_title') or subscription.get('payment_method', 'Unknown'),
                        'payment_method_last4': None,
                        'payment_method_brand': None,
                        'payment_method_full_name': None,
                        'payment_transaction_id': None
                    }
                    
                    # Extract detailed payment information from meta_data
                    meta_data = subscription.get('meta_data', [])
                    authnet_customer_id = None
                    authnet_card_id = None
                    
                    for meta_item in meta_data:
                        meta_key = meta_item.get('key', '')
                        meta_value = meta_item.get('value', '')
                        
                        # Extract POS payment method details (contains card info)
                        if meta_key == '_pos_payment_method' and meta_value:
                            try:
                                import json
                                payment_data = json.loads(meta_value)
                                
                                # Extract card details
                                formatted_subscription['payment_method_last4'] = payment_data.get('last4')
                                formatted_subscription['payment_method_brand'] = payment_data.get('brand')
                                formatted_subscription['payment_method_full_name'] = payment_data.get('name')  # e.g., "Mastercard ****2323"
                                
                                # Override payment_method with more descriptive name if available
                                if payment_data.get('name'):
                                    formatted_subscription['payment_method'] = payment_data.get('name')
                                elif payment_data.get('brand') and payment_data.get('last4'):
                                    formatted_subscription['payment_method'] = f"{payment_data.get('brand')} ending in {payment_data.get('last4')}"
                                    
                            except (json.JSONDecodeError, TypeError) as e:
                                logger.warning(f"Failed to parse _pos_payment_method meta data: {e}")
                        
                        # Extract Authorize.net card details from meta keys
                        elif meta_key == '_authnet_cc_type' and meta_value:
                            formatted_subscription['payment_method_brand'] = meta_value
                        elif meta_key == '_authnet_cc_last4' and meta_value:
                            formatted_subscription['payment_method_last4'] = meta_value
                        elif meta_key == '_wc_authorize_net_cim_credit_card_card_type' and meta_value:
                            formatted_subscription['payment_method_brand'] = meta_value
                        elif meta_key == '_wc_authorize_net_cim_credit_card_last_four' and meta_value:
                            formatted_subscription['payment_method_last4'] = meta_value
                        
                        # Collect CIM IDs for potential lookup
                        elif meta_key == '_authnet_customer_id' and meta_value:
                            authnet_customer_id = meta_value
                        elif meta_key in ('_authnet_card_id', '_authorize_net_cim_credit_card_payment_token') and meta_value:
                            authnet_card_id = meta_value
                        elif meta_key == '_authorize_net_cim_credit_card_customer_id' and meta_value:
                            authnet_customer_id = meta_value
                        
                        # Extract transaction ID
                        elif meta_key == '_payment_transaction_id':
                            formatted_subscription['payment_transaction_id'] = meta_value
                    
                    # If we got brand/last4 from authnet meta, update payment_method display
                    if formatted_subscription['payment_method_brand'] and formatted_subscription['payment_method_last4'] and not formatted_subscription.get('payment_method_full_name'):
                        formatted_subscription['payment_method'] = f"{formatted_subscription['payment_method_brand']} ending in {formatted_subscription['payment_method_last4']}"
                        formatted_subscription['payment_method_full_name'] = formatted_subscription['payment_method']
                    
                    # If no card details yet but have CIM IDs, store for batch lookup
                    if not formatted_subscription['payment_method_last4'] and authnet_customer_id and authnet_card_id:
                        formatted_subscription['_cim_customer_id'] = authnet_customer_id
                        formatted_subscription['_cim_card_id'] = authnet_card_id
                    
                    # Fallback: Try to extract last 4 digits from payment method title if no data available
                    if not formatted_subscription['payment_method_last4']:
                        payment_title = subscription.get('payment_method_title', '')
                        if 'ending in' in payment_title.lower():
                            import re
                            last4_match = re.search(r'ending in (\d{4})', payment_title.lower())
                            if last4_match:
                                formatted_subscription['payment_method_last4'] = last4_match.group(1)
                    
                    # Count subscription types
                    if is_membership:
                        membership_count += 1
                    else:
                        product_count += 1
                    
                    result_subscriptions.append(formatted_subscription)
                    break  # Only need one product per subscription
        
        logger.info(f"Filtered to {len(result_subscriptions)} total subscriptions")
        logger.info(f"Membership subscriptions: {membership_count}, Product subscriptions: {product_count}")
        
        # Batch CIM lookups for subscriptions that need card details from Authorize.net
        cim_lookups_needed = {}
        for idx, sub in enumerate(result_subscriptions):
            cim_cust = sub.get('_cim_customer_id')
            cim_card = sub.get('_cim_card_id')
            if cim_cust and cim_card:
                if cim_cust not in cim_lookups_needed:
                    cim_lookups_needed[cim_cust] = []
                cim_lookups_needed[cim_cust].append(idx)
        
        if cim_lookups_needed:
            try:
                from payments.authorize_net import AuthorizeNetGateway
                for customer_profile_id, sub_indices in cim_lookups_needed.items():
                    try:
                        profiles = AuthorizeNetGateway.get_customer_payment_profiles(customer_profile_id)
                        if profiles:
                            profile_map = {p['payment_profile_id']: p for p in profiles}
                            for sub_idx in sub_indices:
                                sub = result_subscriptions[sub_idx]
                                card_id = str(sub.get('_cim_card_id', ''))
                                if card_id in profile_map:
                                    card = profile_map[card_id]
                                    brand = card.get('card_brand', 'Credit Card')
                                    last4 = card.get('last4', '')
                                    sub['payment_method_brand'] = brand
                                    sub['payment_method_last4'] = last4
                                    if brand and last4:
                                        sub['payment_method'] = f'{brand} ending in {last4}'
                                        sub['payment_method_full_name'] = sub['payment_method']
                                    elif last4:
                                        sub['payment_method'] = f'Credit Card ending in {last4}'
                                        sub['payment_method_full_name'] = sub['payment_method']
                                else:
                                    logger.warning(f"Payment profile {card_id} not found in CIM customer {customer_profile_id}")
                    except Exception as e:
                        logger.warning(f"CIM lookup failed for customer profile {customer_profile_id}: {e}")
            except ImportError:
                logger.warning("Could not import AuthorizeNetGateway for CIM lookup")
        
        # Clean up temporary keys
        for sub in result_subscriptions:
            sub.pop('_cim_customer_id', None)
            sub.pop('_cim_card_id', None)
        
        # Log the subscription statuses we found
        result_statuses = {}
        for sub in result_subscriptions:
            status_name = sub.get('subscription_status', 'unknown')
            result_statuses[status_name] = result_statuses.get(status_name, 0) + 1
        logger.info(f"Result subscription statuses: {result_statuses}")
        
        return Response({
            'subscription_products': result_subscriptions,
            'total_count': len(result_subscriptions),
            'membership_count': membership_count,
            'product_count': product_count,
            'membership_only_filter': membership_only
        }, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Error fetching customer subscriptions from WooCommerce API: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_subscription_payment_method(request, subscription_id):
    """
    Update payment method for a WooCommerce subscription.
    
    Accepts:
      - payment_profile_id: Authorize.net CIM payment profile ID (the card to switch to)
      - customer_profile_id: Authorize.net CIM customer profile ID
    
    The endpoint validates the card exists in CIM, then updates the subscription's
    _authnet_card_id and _authnet_customer_id meta keys so future renewals charge
    the new card. Also updates payment_method_title for display.
    """
    try:
        payment_profile_id = request.data.get('payment_profile_id')
        customer_profile_id = request.data.get('customer_profile_id')
        
        if not payment_profile_id or not customer_profile_id:
            return Response({
                'error': 'payment_profile_id and customer_profile_id are required'
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"Updating payment method for subscription {subscription_id}: "
                     f"customer_profile={customer_profile_id}, payment_profile={payment_profile_id}")
        
        # Validate the card exists in Authorize.net CIM and get card details
        try:
            from payments.authorize_net import AuthorizeNetGateway
            profiles = AuthorizeNetGateway.get_customer_payment_profiles(customer_profile_id)
            if not profiles:
                return Response({
                    'error': f'No payment profiles found for customer profile {customer_profile_id}'
                }, status=http_status.HTTP_400_BAD_REQUEST)
            
            profile_map = {p['payment_profile_id']: p for p in profiles}
            if str(payment_profile_id) not in profile_map:
                return Response({
                    'error': f'Payment profile {payment_profile_id} not found in customer profile {customer_profile_id}'
                }, status=http_status.HTTP_400_BAD_REQUEST)
            
            card = profile_map[str(payment_profile_id)]
            card_brand = card.get('card_brand', 'Credit Card')
            card_last4 = card.get('last4', '')
            new_title = f'{card_brand} ending in {card_last4}' if card_brand and card_last4 else 'Credit Card'
            
        except ImportError:
            return Response({
                'error': 'Payment gateway module not available'
            }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            logger.error(f"CIM validation failed: {e}")
            return Response({
                'error': f'Failed to validate card with payment gateway: {str(e)}'
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        # Update subscription via WooCommerce API
        wc_api = WooCommerceAPI()
        
        update_data = {
            'payment_method': 'authnet',
            'payment_method_title': new_title,
            'meta_data': [
                {'key': '_authnet_card_id', 'value': str(payment_profile_id)},
                {'key': '_authnet_customer_id', 'value': str(customer_profile_id)},
                {'key': '_authnet_cc_type', 'value': card_brand},
                {'key': '_authnet_cc_last4', 'value': card_last4},
            ]
        }
        
        result = wc_api.wcapi.put(f"subscriptions/{subscription_id}", update_data)
        
        if not result.ok:
            error_msg = f"Failed to update subscription payment method. Status: {result.status_code}"
            if hasattr(result, 'text'):
                error_msg += f", Response: {result.text[:500]}"
            logger.error(error_msg)
            return Response({
                'error': error_msg
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        updated_subscription = result.json()
        
        logger.info(f"Successfully updated payment method for subscription {subscription_id} "
                     f"to {new_title} (profile {payment_profile_id})")
        
        return Response({
            'success': True,
            'message': f'Payment method updated to {new_title}',
            'subscription': {
                'id': updated_subscription.get('id'),
                'payment_method': updated_subscription.get('payment_method'),
                'payment_method_title': new_title,
                'card_brand': card_brand,
                'card_last4': card_last4
            }
        }, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Error updating subscription payment method: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_subscription_payment_profiles(request, subscription_id):
    """
    Fetch all Authorize.net CIM payment profiles for a subscription's customer.
    Looks up _authnet_customer_id from the subscription meta, then queries CIM.
    Returns cards in a format compatible with the frontend SavedCard interface.
    """
    try:
        wc_api = WooCommerceAPI()
        
        # Fetch subscription to get _authnet_customer_id
        result = wc_api.wcapi.get(f"subscriptions/{subscription_id}")
        if not result.ok:
            return Response({
                'error': f'Subscription {subscription_id} not found'
            }, status=http_status.HTTP_404_NOT_FOUND)
        
        subscription = result.json()
        meta_data = subscription.get('meta_data', [])
        
        authnet_customer_id = None
        authnet_card_id = None  # Current card on the subscription
        for meta_item in meta_data:
            key = meta_item.get('key', '')
            value = meta_item.get('value', '')
            if key == '_authnet_customer_id' and value:
                authnet_customer_id = value
            elif key == '_authorize_net_cim_credit_card_customer_id' and value:
                authnet_customer_id = value
            elif key in ('_authnet_card_id', '_authorize_net_cim_credit_card_payment_token') and value:
                authnet_card_id = value
        
        # Fallback: look up CIM profile via the subscription's customer email
        if not authnet_customer_id:
            customer_id_woo = subscription.get('customer_id')
            billing_email = subscription.get('billing', {}).get('email', '')
            
            # Try Contact model first
            if customer_id_woo or billing_email:
                try:
                    from crm.models import Contact
                    contact = None
                    if billing_email:
                        contact = Contact.objects.filter(email__iexact=billing_email).first()
                    if not contact and customer_id_woo:
                        contact = Contact.objects.filter(woo_customer_id=str(customer_id_woo)).first()
                    
                    if contact and contact.authorize_net_customer_profile_id:
                        profile_id = contact.authorize_net_customer_profile_id.strip()
                        if profile_id.isdigit():
                            authnet_customer_id = profile_id
                            logger.info(f"CIM profile {authnet_customer_id} found via Contact lookup for subscription {subscription_id}")
                except Exception as e:
                    logger.warning(f"Contact lookup failed for subscription {subscription_id}: {e}")
            
            # Try real-time CIM lookup by email
            if not authnet_customer_id and billing_email:
                try:
                    from payments.authorize_net import AuthorizeNetGateway as ANG
                    discovered_id = ANG.get_customer_profile_by_email(billing_email)
                    if discovered_id:
                        authnet_customer_id = discovered_id
                        logger.info(f"CIM profile {authnet_customer_id} discovered via email lookup for subscription {subscription_id}")
                except Exception as e:
                    logger.warning(f"CIM email lookup failed for subscription {subscription_id}: {e}")
        
        # Collect all CIM profile IDs to query (subscription's + Contact's)
        from payments.authorize_net import AuthorizeNetGateway
        cim_profile_ids = set()
        if authnet_customer_id:
            cim_profile_ids.add(authnet_customer_id)
        
        # Also check all Contact records for this customer to find additional CIM profiles
        customer_id_woo = subscription.get('customer_id')
        billing_email = subscription.get('billing', {}).get('email', '')
        try:
            from crm.models import Contact
            contacts = set()
            if billing_email:
                contacts.update(Contact.objects.filter(email__iexact=billing_email))
            if customer_id_woo:
                contacts.update(Contact.objects.filter(woo_customer_id=str(customer_id_woo)))
            for contact in contacts:
                if contact.authorize_net_customer_profile_id:
                    pid = contact.authorize_net_customer_profile_id.strip()
                    if pid.isdigit():
                        cim_profile_ids.add(pid)
        except Exception as e:
            logger.warning(f"Contact CIM lookup failed for subscription {subscription_id}: {e}")
        
        if not cim_profile_ids:
            return Response({
                'status': 'success',
                'cards': [],
                'message': 'No Authorize.net customer profile found for this subscription'
            }, status=http_status.HTTP_200_OK)
        
        # Fetch and merge payment profiles from all CIM profiles, dedup by payment_profile_id
        seen_profile_ids = set()
        cards = []
        for cim_id in cim_profile_ids:
            try:
                profiles = AuthorizeNetGateway.get_customer_payment_profiles(cim_id)
                if profiles:
                    for p in profiles:
                        ppid = str(p['payment_profile_id'])
                        if ppid not in seen_profile_ids:
                            seen_profile_ids.add(ppid)
                            cards.append({
                                'id': ppid,
                                'last4': p.get('last4', ''),
                                'cardBrand': p.get('card_brand', 'Credit Card'),
                                'customerProfileId': cim_id,
                                'paymentProfileId': ppid,
                                'isDefault': ppid == str(authnet_card_id) if authnet_card_id else False,
                            })
            except Exception as e:
                logger.warning(f"Error fetching profiles from CIM {cim_id}: {e}")
        
        logger.info(f"Found {len(cards)} CIM payment profiles for subscription {subscription_id} "
                     f"(from {len(cim_profile_ids)} CIM profile(s): {cim_profile_ids})")
        
        return Response({
            'status': 'success',
            'cards': cards,
            'customer_profile_id': authnet_customer_id or list(cim_profile_ids)[0],
            'current_card_id': authnet_card_id
        }, status=http_status.HTTP_200_OK)
        
    except ImportError:
        return Response({
            'error': 'Payment gateway module not available'
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        error_msg = f"Error fetching payment profiles for subscription {subscription_id}: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_subscription_dates(request, subscription_id):
    """
    Update subscription dates (start_date, next_payment_date, end_date) for a WooCommerce subscription
    
    Request body should contain:
    - start_date: ISO 8601 datetime string (optional)
    - next_payment_date: ISO 8601 datetime string (optional)
    - end_date: ISO 8601 datetime string or null for ongoing (optional)
    
    Notes:
    - All dates should be in ISO 8601 format: "2026-01-05T12:00:00"
    - next_payment_date must be after start_date
    - end_date must be after start_date and next_payment_date (or null for ongoing)
    - WooCommerce may need to recalculate payment schedule after date changes
    """
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get date fields from request
        start_date = request.data.get('start_date')
        next_payment_date = request.data.get('next_payment_date')
        end_date = request.data.get('end_date')
        
        # Validate that at least one date field is provided
        if not any([start_date, next_payment_date, end_date is not None]):
            return Response({
                'error': 'At least one date field (start_date, next_payment_date, or end_date) is required'
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"Updating dates for subscription {subscription_id}")
        logger.info(f"New dates: start_date={start_date}, next_payment_date={next_payment_date}, end_date={end_date}")
        
        # Prepare update data for WooCommerce
        update_data = {}
        
        if start_date:
            update_data['start_date'] = start_date
        
        if next_payment_date:
            update_data['next_payment_date'] = next_payment_date
        
        # Handle end_date specially - can be null for ongoing subscriptions
        if end_date is not None:
            if end_date == '' or end_date == 'null':
                update_data['end_date'] = None  # Ongoing subscription
            else:
                update_data['end_date'] = end_date
        
        logger.info(f"Sending update data to WooCommerce: {update_data}")
        
        # Update subscription via WooCommerce API
        result = wc_api.wcapi.put(f"subscriptions/{subscription_id}", update_data)
        
        if not result.ok:
            error_msg = f"Failed to update subscription dates. Status: {result.status_code}"
            if hasattr(result, 'text'):
                error_msg += f", Response: {result.text}"
            logger.error(error_msg)
            return Response({
                'error': error_msg,
                'details': result.text if hasattr(result, 'text') else None
            }, status=http_status.HTTP_400_BAD_REQUEST)
        
        # Parse response
        updated_subscription = result.json()
        
        logger.info(f"Successfully updated dates for subscription {subscription_id}")
        
        return Response({
            'success': True,
            'message': 'Subscription dates updated successfully',
            'subscription': {
                'id': updated_subscription.get('id'),
                'start_date': updated_subscription.get('start_date_gmt') or updated_subscription.get('start_date'),
                'next_payment_date': updated_subscription.get('next_payment_date_gmt') or updated_subscription.get('next_payment_date'),
                'end_date': updated_subscription.get('end_date_gmt') or updated_subscription.get('end_date'),
                'status': updated_subscription.get('status')
            }
        }, status=http_status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Error updating subscription dates: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_subscription_counts(request):
    """
    Lightweight endpoint that returns membership vs non-membership subscription counts.
    Iterates through all WooCommerce subscription pages server-side and classifies
    each subscription by checking if any line_item name contains 'membership' or 'member'.
    Returns: { total, membership_count, non_membership_count }
    """
    try:
        wc_api = WooCommerceAPI()
        membership_keywords = ['membership', 'member']

        membership_count = 0
        non_membership_count = 0
        page = 1
        per_page = 100  # Large page size to minimise round-trips

        while True:
            result = wc_api.get_subscriptions(
                page=page,
                per_page=per_page,
                status='any',
            )

            if result['status'] != 'success':
                # If first page fails, return error; otherwise return what we have
                if page == 1:
                    logger.error(f"Failed to fetch subscriptions for counts: {result.get('message')}")
                    return Response({
                        'error': result.get('message', 'Failed to fetch subscriptions')
                    }, status=http_status.HTTP_400_BAD_REQUEST)
                break

            subscriptions_data = result['data']
            if not subscriptions_data:
                break

            for subscription in subscriptions_data:
                line_items = subscription.get('line_items', [])
                is_membership = False
                if line_items and isinstance(line_items, list):
                    for item in line_items:
                        product_name = (item.get('name') or '').lower()
                        if any(kw in product_name for kw in membership_keywords):
                            is_membership = True
                            break

                if is_membership:
                    membership_count += 1
                else:
                    non_membership_count += 1

            # Check if there are more pages
            headers = result.get('headers', {})
            total_pages = _header_int(headers, 'X-WP-TotalPages', 1)
            if page >= total_pages:
                break
            page += 1

        total = membership_count + non_membership_count
        logger.info(f"Subscription counts: total={total}, membership={membership_count}, non_membership={non_membership_count}")

        return Response({
            'total': total,
            'membership_count': membership_count,
            'non_membership_count': non_membership_count,
        }, status=http_status.HTTP_200_OK)

    except RequestException as e:
        error_msg = f"Request error fetching subscription counts: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception as e:
        error_msg = f"Error fetching subscription counts: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_subscription_related_orders(request, subscription_id):
    """
    Get related orders (parent order + renewal orders) for a subscription.
    Accepts optional query params ?parent_id=X&customer_id=Y to skip the
    subscription lookup (the frontend already has this data from orderDetails).
    Uses ThreadPoolExecutor to run parent + renewal fetches in parallel.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        wc_api = WooCommerceAPI()

        # Use query params if provided (avoids an extra WooCommerce API call)
        parent_id = request.query_params.get('parent_id')
        customer_id = request.query_params.get('customer_id')

        if parent_id:
            parent_id = int(parent_id)
        if customer_id:
            customer_id = int(customer_id)

        # Only fetch subscription if we don't have parent_id/customer_id
        if not parent_id and not customer_id:
            sub_response = wc_api.wcapi.get(
                f"subscriptions/{subscription_id}",
                params={"_fields": "id,parent_id,customer_id"}
            )
            if not sub_response.ok:
                return Response({'error': 'Subscription not found'}, status=http_status.HTTP_404_NOT_FOUND)
            sub_data = sub_response.json()
            parent_id = sub_data.get('parent_id', 0) or 0
            customer_id = sub_data.get('customer_id', 0) or 0

        orders = []

        def _wc_order_display_status(status, date_paid, date_paid_gmt=None):
            """Paid renewals often stay wc-processing; align UI with successful payment."""
            st = (status or '').strip().lower()
            if st != 'processing':
                return status or 'unknown'
            dp = (str(date_paid or '').strip() or str(date_paid_gmt or '').strip())
            if dp and dp.lower() not in ('null', 'none') and not dp.startswith('0000-00-00'):
                return 'completed'
            return status or 'unknown'

        def fetch_parent():
            """Fetch parent order by ID (fast — single order lookup)."""
            if not parent_id or parent_id == 0:
                return None
            try:
                resp = wc_api.wcapi.get(
                    f"orders/{parent_id}",
                    params={"_fields": "id,status,date_created,total,date_paid,date_paid_gmt"}
                )
                if resp.ok:
                    p = resp.json()
                    return {
                        'id': p.get('id'),
                        'type': 'Parent Order',
                        'status': _wc_order_display_status(
                            p.get('status'),
                            p.get('date_paid'),
                            p.get('date_paid_gmt'),
                        ),
                        'date': p.get('date_created', ''),
                        'total': p.get('total', '0'),
                    }
            except Exception as e:
                logger.warning(f"Error fetching parent order {parent_id}: {e}")
            return None

        def fetch_renewals():
            """Fetch customer orders and filter for renewals of this subscription."""
            if not customer_id or customer_id == 0:
                return []
            try:
                resp = wc_api.wcapi.get("orders", params={
                    "customer": customer_id,
                    "per_page": 50,
                    "_fields": "id,status,date_created,date_paid,date_paid_gmt,total,meta_data",
                    "orderby": "date",
                    "order": "desc",
                })
                if not resp.ok:
                    return []
                renewal_orders = []
                for o in resp.json():
                    if o.get('id') == parent_id:
                        continue
                    meta = o.get('meta_data', [])
                    is_renewal = any(
                        m.get('key') == '_subscription_renewal'
                        and str(m.get('value')) == str(subscription_id)
                        for m in meta
                    )
                    if is_renewal:
                        renewal_orders.append({
                            'id': o.get('id'),
                            'type': 'Renewal Order',
                            'status': _wc_order_display_status(
                                o.get('status'),
                                o.get('date_paid'),
                                o.get('date_paid_gmt'),
                            ),
                            'date': o.get('date_created', ''),
                            'total': o.get('total', '0'),
                        })
                return renewal_orders
            except Exception as e:
                logger.warning(f"Error fetching renewal orders for subscription {subscription_id}: {e}")
                return []

        # Run parent + renewals fetch in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            parent_future = executor.submit(fetch_parent)
            renewals_future = executor.submit(fetch_renewals)

            parent_order = parent_future.result()
            renewal_orders = renewals_future.result()

        if parent_order:
            orders.append(parent_order)
        orders.extend(renewal_orders)

        return Response({'orders': orders})

    except Exception as e:
        logger.error(f"Error fetching related orders for subscription {subscription_id}: {e}")
        return Response({'error': str(e)}, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)
