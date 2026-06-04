from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from .woocommerce import WooCommerceAPI
from .views_receipts import _enrich_order_subscription_frequency
from .models import Contact, POSOrder, POSOrderRefund, POSOrderRefundItem
from django.db.models import Sum
import logging

logger = logging.getLogger(__name__)

def normalize_email(value):
    if not isinstance(value, str):
        return ''
    normalized = value.strip().lower()
    return normalized if '@' in normalized else ''

class WooOrdersPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


@api_view(['GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def get_woocommerce_subscription(request, subscription_id):
    """
    Get a single subscription from WooCommerce Subscriptions REST API.
    Returns the subscription object with current payment_method / payment_method_title.
    """
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)

    try:
        wc = WooCommerceAPI()
        response = wc.wcapi.get(f"subscriptions/{subscription_id}")

        if not response.ok:
            logger.error(f"Failed to get subscription {subscription_id}. Status: {response.status_code}")
            return Response(
                {"error": f"Failed to get subscription. Status code: {response.status_code}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        subscription = response.json()

        # Add source metadata
        source = None
        meta_data = subscription.get('meta_data', [])
        for meta_item in meta_data:
            if meta_item.get('key') in ('_order_source', 'order_source'):
                source = meta_item.get('value')
                break
        subscription['source'] = source or 'woocommerce'

        return Response(subscription)

    except Exception as e:
        logger.error(f"Error getting subscription {subscription_id}: {str(e)}")
        return Response(
            {"error": f"Error getting subscription: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def get_woocommerce_orders(request):
    """
    Get orders from WooCommerce API
    
    Returns a paginated list of orders from WooCommerce
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # Get page and per_page parameters from request
        page = int(request.query_params.get('page', 1))
        per_page = int(request.query_params.get('per_page', 10))
        
        # Get status filter if provided
        order_status_filter = request.query_params.get('status', None)
        
        # Get search parameter if provided
        search = request.query_params.get('search', None)
        
        # Get user filter if provided
        created_by = request.query_params.get('created_by', None)
        
        # Get date range filters if provided
        date_from = request.query_params.get('date_from', None)
        date_to = request.query_params.get('date_to', None)
        
        # Get amount range filters if provided
        min_amount = request.query_params.get('min_amount', None)
        max_amount = request.query_params.get('max_amount', None)
        
        # Get payment method filter if provided
        payment_method_filter = request.query_params.get('payment_method', None)
        
        # Get source filter if provided
        source_filter = request.query_params.get('source', None)
        
        # Get customer filters if provided
        customer_email = normalize_email(request.query_params.get('customer_email', '')) or None
        # WooCommerce customer ID
        raw_customer_id = request.query_params.get('customer', None) or request.query_params.get('customer_id', None)
        customer_id = None
        if raw_customer_id is not None:
            raw_customer_id_str = str(raw_customer_id).strip()
            if raw_customer_id_str.startswith('woo_'):
                raw_customer_id_str = raw_customer_id_str[4:]
            try:
                parsed_customer_id = int(raw_customer_id_str)
                if parsed_customer_id > 0:
                    customer_id = str(parsed_customer_id)
                else:
                    logger.warning(f"Ignoring non-positive customer_id query param: {raw_customer_id}")
            except (TypeError, ValueError):
                logger.warning(f"Ignoring non-numeric customer_id query param: {raw_customer_id}")
        ghl_contact_id = (request.query_params.get('ghl_contact_id', '') or '').strip()
        lookup_requested = bool(customer_id or customer_email or ghl_contact_id)

        # Resolve by GHL ID when Woo ID/email is not available from caller.
        if ghl_contact_id and not customer_id:
            try:
                contact = (
                    Contact.objects
                    .filter(ghl_contact_id=ghl_contact_id)
                    .order_by('-updated_at', '-created_at')
                    .first()
                )
                if contact:
                    # If current contact is not Woo-linked, try merged-account contacts sharing email.
                    preferred_contact = contact
                    normalized_contact_email = normalize_email(getattr(contact, 'email', ''))
                    if (not preferred_contact.woo_customer_id) and normalized_contact_email:
                        merged = (
                            Contact.objects
                            .filter(email__iexact=normalized_contact_email, woo_customer_id__isnull=False)
                            .exclude(woo_customer_id__exact=0)
                            .order_by('-updated_at', '-created_at')
                            .first()
                        )
                        if merged:
                            preferred_contact = merged

                    if preferred_contact.woo_customer_id:
                        customer_id = str(preferred_contact.woo_customer_id)
                        logger.info(
                            f"Resolved Woo customer {customer_id} from ghl_contact_id={ghl_contact_id} "
                            f"via contact={preferred_contact.id}"
                        )

                    if not customer_email:
                        preferred_email = normalize_email(getattr(preferred_contact, 'email', ''))
                        if preferred_email:
                            customer_email = preferred_email
                            logger.info(
                                f"Resolved customer_email from ghl_contact_id={ghl_contact_id}: {customer_email}"
                            )
                else:
                    logger.warning(f"No Contact found for ghl_contact_id={ghl_contact_id}")
            except Exception as e:
                logger.warning(f"Failed resolving customer by ghl_contact_id={ghl_contact_id}: {e}")

        # Prevent accidental broad order fetch when caller intended a customer-specific lookup.
        if lookup_requested and not customer_id and not customer_email:
            logger.warning(
                f"Customer-scoped order lookup has no resolvable identity "
                f"(customer_id={customer_id}, customer_email={customer_email}, ghl_contact_id={ghl_contact_id})"
            )
            return Response({
                'orders': [],
                'pagination': {
                    'total': 0,
                    'total_pages': 0,
                    'current_page': page,
                    'per_page': per_page
                }
            })
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Log the WooCommerce API connection details (masked for security)
        logger.info(f"WooCommerce API URL: {wc.wcapi.url}")
        logger.info(f"Using WooCommerce API version: {wc.wcapi.version}")
        
        # Prepare parameters for WooCommerce API
        params = {
            'page': page,
            'per_page': per_page,
        }
        
        # Add status filter if provided
        if order_status_filter:
            params['status'] = order_status_filter
            
        # Determine if search term is a name (not purely numeric/order-number-like)
        # WooCommerce's search param only searches order numbers, NOT billing names.
        # For name searches, we look up matching customer IDs first, then fetch their orders.
        is_name_search = False
        search_customer_ids = []
        
        if search:
            search_stripped = search.strip()
            # If search is purely digits or starts with # it's an order number search
            is_order_number_search = search_stripped.replace('#', '').replace('-', '').isdigit()
            
            if is_order_number_search:
                params['search'] = search_stripped.replace('#', '')
            else:
                # Name/email search — look up WooCommerce customers first
                is_name_search = True
                logger.info(f"Search term '{search}' looks like a name — looking up customers first")
                try:
                    cust_response = wc.wcapi.get("customers", params={'search': search_stripped, 'per_page': 20})
                    if cust_response.ok:
                        customers_found = cust_response.json()
                        search_customer_ids = [c['id'] for c in customers_found if c.get('id')]
                        logger.info(f"Found {len(search_customer_ids)} WooCommerce customers matching '{search}': {search_customer_ids}")
                except Exception as e:
                    logger.warning(f"Customer lookup failed for search '{search}': {e}")
            
        # Add customer filter if provided (supported natively by WooCommerce API)
        # Also look up the customer's email so we can find guest orders too.
        # Keep small-page optimization by default, but allow callers to force
        # guest lookup for merged-account/customer-history correctness.
        customer_email_for_lookup = None
        include_guest_lookup = request.query_params.get('include_guest_lookup', '').lower() == 'true'
        skip_guest_lookup = (
            request.query_params.get('skip_guest_lookup', '').lower() == 'true'
            or (per_page <= 5 and not include_guest_lookup)
        )
        if customer_id:
            params['customer'] = customer_id
            if customer_email:
                customer_email_for_lookup = customer_email
                logger.info(f"Using provided customer_email for customer {customer_id}: {customer_email_for_lookup}")
            if not skip_guest_lookup:
                # Look up the customer's email from WooCommerce for guest order matching
                if not customer_email_for_lookup:
                    try:
                        cust_resp = wc.wcapi.get(f"customers/{customer_id}")
                        if cust_resp.ok:
                            cust_data = cust_resp.json()
                            customer_email_for_lookup = normalize_email(cust_data.get('email', ''))
                            logger.info(f"Customer {customer_id} email: {customer_email_for_lookup}")
                    except Exception as e:
                        logger.warning(f"Could not look up email for customer {customer_id}: {e}")
            else:
                logger.info(
                    f"Skipping guest order lookup for customer {customer_id} "
                    f"(per_page={per_page}, include_guest_lookup={include_guest_lookup})"
                )
            
        # Add date range filters if provided
        if date_from:
            params['after'] = f"{date_from}T00:00:00"
        if date_to:
            params['before'] = f"{date_to}T23:59:59"
            
        # Add created_by filter if provided
        # Note: WooCommerce API doesn't directly support filtering by created_by,
        # so we'll need to filter the results after fetching them
        
        logger.info(f"Fetching WooCommerce orders with params: {params}")
        logger.info(
            f"Additional filters - customer_id: {customer_id}, customer_email: {customer_email}, "
            f"ghl_contact_id: {ghl_contact_id}, created_by: {created_by}, min_amount: {min_amount}, max_amount: {max_amount}, "
            f"payment_method: {payment_method_filter}, source: {source_filter}"
        )
        
        # For name searches, we may need to make multiple API calls:
        # 1. Fetch orders for each matching customer ID
        # 2. Also fetch a broad page of orders and post-filter by billing name
        all_orders = []
        total_orders = 0
        total_pages = 0
        
        if is_name_search:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            seen_order_ids = set()

            # Run per-customer fetches and broad guest fetch in parallel
            def _fetch_for_customer(cust_id):
                try:
                    cust_params = {**params, 'customer': cust_id, 'per_page': per_page}
                    resp = wc.wcapi.get("orders", params=cust_params)
                    if resp.ok:
                        return resp.json(), dict(resp.headers)
                except Exception as e:
                    logger.warning(f"Error fetching orders for customer {cust_id}: {e}")
                return [], {}

            def _fetch_broad():
                try:
                    broad_params = {**params, 'per_page': 100}
                    resp = wc.wcapi.get("orders", params=broad_params)
                    if resp.ok:
                        return resp.json()
                except Exception as e:
                    logger.warning(f"Error in broad order search for '{search}': {e}")
                return []

            customer_results = []
            broad_orders = []
            with ThreadPoolExecutor(max_workers=6) as executor:
                cust_futures = {
                    executor.submit(_fetch_for_customer, cid): cid
                    for cid in search_customer_ids[:5]
                }
                broad_future = executor.submit(_fetch_broad)

                for future in as_completed(cust_futures):
                    customer_results.append(future.result())
                broad_orders = broad_future.result()

            # Collect customer-matched orders (dedup)
            for orders_batch, headers in customer_results:
                for o in orders_batch:
                    if o['id'] not in seen_order_ids:
                        seen_order_ids.add(o['id'])
                        all_orders.append(o)
                if not total_orders and headers:
                    total_orders = int(headers.get('X-WP-Total', 0))
                    total_pages = int(headers.get('X-WP-TotalPages', 0))

            # Post-filter broad results by billing name (guest checkout matches)
            search_lower = search.strip().lower()
            for o in broad_orders:
                if o['id'] in seen_order_ids:
                    continue
                billing = o.get('billing', {})
                first_name = (billing.get('first_name') or '').lower()
                last_name = (billing.get('last_name') or '').lower()
                full_name = f"{first_name} {last_name}"
                email = (billing.get('email') or '').lower()
                if (search_lower in first_name or search_lower in last_name or
                    search_lower in full_name or search_lower in email):
                    seen_order_ids.add(o['id'])
                    all_orders.append(o)

            total_orders = len(all_orders)
            total_pages = 1
            orders = all_orders
            logger.info(f"Name search for '{search}' found {len(all_orders)} orders")
        else:
            # Standard search (order number) or no search — single API call
            if customer_email and not customer_id:
                logger.info(f"Using customer_email fallback lookup for order history: {customer_email}")
                scan_params = {k: v for k, v in params.items() if k not in ('page', 'per_page')}
                scan_page = 1
                scan_per_page = 100
                requested_scan_cap = request.query_params.get('email_scan_max_pages')
                if requested_scan_cap:
                    max_scan_pages = int(requested_scan_cap)
                else:
                    # Customer modal history should never do a broad, unbounded scan.
                    # Keep default tight when guest lookup is enabled.
                    max_scan_pages = 2 if include_guest_lookup else 50
                scan_total_pages = 1
                matched_orders = []
                seen_ids = set()
                
                while scan_page <= scan_total_pages and scan_page <= max_scan_pages:
                    email_params = {**scan_params, 'page': scan_page, 'per_page': scan_per_page}
                    response = wc.wcapi.get("orders", params=email_params)
                    logger.info(
                        f"Customer email scan page {scan_page}/{scan_total_pages} "
                        f"(status={response.status_code})"
                    )
                    if not response.ok:
                        logger.error(
                            f"Failed email fallback scan for {customer_email}. "
                            f"Status code: {response.status_code}, Response: {response.text}"
                        )
                        return Response(
                            {"error": f"Failed to get orders from WooCommerce. Status code: {response.status_code}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR
                        )
                    
                    batch = response.json()
                    scan_total_pages = int(response.headers.get('X-WP-TotalPages', scan_total_pages))
                    for o in batch:
                        order_id = o.get('id')
                        if order_id in seen_ids:
                            continue
                        billing_email = normalize_email(o.get('billing', {}).get('email', ''))
                        if billing_email == customer_email:
                            seen_ids.add(order_id)
                            matched_orders.append(o)
                    scan_page += 1
                
                total_orders = len(matched_orders)
                total_pages = (total_orders + per_page - 1) // per_page if per_page > 0 else 0
                start_idx = (page - 1) * per_page
                end_idx = start_idx + per_page
                orders = matched_orders[start_idx:end_idx]
                logger.info(
                    f"Customer email fallback matched {total_orders} orders for {customer_email}; "
                    f"returning page {page} with {len(orders)} orders"
                )
            else:
                # When guest lookup is needed, run customer orders + guest orders
                # fetches in parallel to avoid consecutive HTTP calls.
                need_guest_lookup = bool(customer_id and customer_email_for_lookup)

                if need_guest_lookup:
                    from concurrent.futures import ThreadPoolExecutor

                    guest_params = {k: v for k, v in params.items() if k != 'customer'}
                    guest_params['per_page'] = 100

                    def _fetch_customer_orders():
                        return wc.wcapi.get("orders", params=params)

                    def _fetch_guest_orders():
                        return wc.wcapi.get("orders", params=guest_params)

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        customer_future = executor.submit(_fetch_customer_orders)
                        guest_future = executor.submit(_fetch_guest_orders)
                        response = customer_future.result()
                        guest_resp = guest_future.result()

                    logger.info(f"WooCommerce API response status: {response.status_code}")

                    if not response.ok:
                        logger.error(f"Failed to get orders from WooCommerce. Status code: {response.status_code}, Response: {response.text}")
                        return Response(
                            {"error": f"Failed to get orders from WooCommerce. Status code: {response.status_code}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR
                        )

                    total_orders = int(response.headers.get('X-WP-Total', 0))
                    total_pages = int(response.headers.get('X-WP-TotalPages', 0))
                    orders = response.json()

                    seen_ids = {o['id'] for o in orders}
                    try:
                        if guest_resp.ok:
                            guest_count = 0
                            for o in guest_resp.json():
                                if o['id'] in seen_ids:
                                    continue
                                billing_email = normalize_email(o.get('billing', {}).get('email', ''))
                                if billing_email == customer_email_for_lookup and o.get('customer_id', 0) == 0:
                                    orders.append(o)
                                    guest_count += 1
                            if guest_count > 0:
                                total_orders += guest_count
                                logger.info(f"Found {guest_count} guest orders for email {customer_email_for_lookup}")
                    except Exception as e:
                        logger.warning(f"Error processing guest orders by email: {e}")
                else:
                    try:
                        response = wc.wcapi.get("orders", params=params)
                        logger.info(f"WooCommerce API response status: {response.status_code}")
                    except Exception as e:
                        logger.error(f"Exception during WooCommerce API request: {str(e)}")
                        raise

                    if not response.ok:
                        logger.error(f"Failed to get orders from WooCommerce. Status code: {response.status_code}, Response: {response.text}")
                        return Response(
                            {"error": f"Failed to get orders from WooCommerce. Status code: {response.status_code}"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR
                        )

                    total_orders = int(response.headers.get('X-WP-Total', 0))
                    total_pages = int(response.headers.get('X-WP-TotalPages', 0))
                    orders = response.json()
        
        # Apply post-fetch filtering for parameters not supported by WooCommerce API
        filtered_orders = []
        
        for order in orders:
            # Apply search filter on customer billing info (WooCommerce API doesn't search billing by default)
            # Skip this for name searches since we already filtered by customer ID / billing name above
            if search and not is_name_search:
                search_lower = search.lower()
                billing = order.get('billing', {})
                
                # Search in customer first name, last name, and email
                first_name = (billing.get('first_name') or '').lower()
                last_name = (billing.get('last_name') or '').lower()
                email = (billing.get('email') or '').lower()
                
                # Also check order number and status (already handled by WooCommerce API, but include for completeness)
                order_number = str(order.get('id', '')).lower()
                order_status = (order.get('status') or '').lower()
                
                # If search term doesn't match any of these fields, skip this order
                if not (search_lower in first_name or 
                       search_lower in last_name or 
                       search_lower in email or
                       search_lower in order_number or
                       search_lower in order_status):
                    continue
            
            # Check created_by filter
            order_created_by = None
            if 'meta_data' in order and isinstance(order['meta_data'], list):
                for meta_item in order['meta_data']:
                    if meta_item.get('key') == '_created_by':
                        order_created_by = meta_item.get('value')
                        break
            
            # Add created_by to order data if found
            if order_created_by:
                order['created_by'] = order_created_by
            
            # Apply created_by filter
            if created_by and order_created_by != created_by:
                continue
            
            # Apply amount range filters
            order_total = float(order.get('total', 0))
            if min_amount and order_total < float(min_amount):
                continue
            if max_amount and order_total > float(max_amount):
                continue
            
            # Apply payment method filter
            if payment_method_filter:
                order_payment_method = order.get('payment_method_title', '').lower()
                if payment_method_filter.lower() not in order_payment_method:
                    continue
            
            # Apply source filter - extract source from meta_data before filtering
            if source_filter:
                order_source = None
                if 'meta_data' in order and isinstance(order['meta_data'], list):
                    for meta_item in order['meta_data']:
                        if meta_item.get('key') == '_order_source' or meta_item.get('key') == 'order_source':
                            order_source = meta_item.get('value')
                            break
                
                # Default to 'woocommerce' if no source found in meta_data
                if not order_source:
                    order_source = 'woocommerce'
                
                # Filter based on source
                if source_filter == 'pos' and order_source != 'DS POS':
                    continue
                elif source_filter == 'woocommerce' and order_source == 'DS POS':
                    continue
            
            # Order passes all filters
            filtered_orders.append(order)
        
        # Update orders list with filtered results
        original_count = len(orders)
        orders = filtered_orders
        logger.info(f"Filtered orders: {original_count} -> {len(orders)} orders after applying filters")
        
        # Process each order to add source, location properties, fix payment method display
        # NOTE: Detailed refund data is NOT fetched here (N+1 perf issue).
        # The order-level 'refunds' array from WooCommerce is already present.
        # Detailed per-line-item refund_status is fetched in get_woocommerce_order (single order view).
        for order in orders:
            # Check for order source in meta_data
            source = None
            if 'meta_data' in order and isinstance(order['meta_data'], list):
                for meta_item in order['meta_data']:
                    if meta_item.get('key') == '_order_source' or meta_item.get('key') == 'order_source':
                        source = meta_item.get('value')
                        break
            
            # Add source to order data if found
            if source:
                order['source'] = source
            else:
                order['source'] = 'woocommerce'  # Default source for WooCommerce orders
                
            # Check for created_by in meta_data if not already set
            if 'created_by' not in order and 'meta_data' in order and isinstance(order['meta_data'], list):
                for meta_item in order['meta_data']:
                    if meta_item.get('key') == '_created_by':
                        order['created_by'] = meta_item.get('value')
                        break
            
            # Extract transaction_id from meta_data if main field is empty
            pos_transaction_id = None
            if not order.get('transaction_id') and 'meta_data' in order and isinstance(order['meta_data'], list):
                for meta_item in order['meta_data']:
                    key = meta_item.get('key')
                    value = meta_item.get('value')
                    
                    # Check for transaction_id in meta_data
                    if key in ['_pos_transaction_id', '_transaction_id', '_payment_transaction_id'] and value:
                        order['transaction_id'] = value
                        if key == '_pos_transaction_id':
                            pos_transaction_id = value
                        logger.info(f"📋 Extracted transaction_id from meta_data key '{key}': {value} for order {order.get('id')}")
                        break
            
            # NOTE: bundle_products fetching is deferred to single-order view
            # (get_woocommerce_order) to avoid N+1 DB queries on the list.
            
            # Extract location properties from meta_data
            if 'meta_data' in order and isinstance(order['meta_data'], list):
                for meta_item in order['meta_data']:
                    key = meta_item.get('key')
                    value = meta_item.get('value')
                    
                    # Check for location-related meta fields
                    if key == '_assigned_location' or key == 'assigned_location':
                        order['assigned_location'] = value
                    elif key == '_pos_location_id' or key == 'pos_location_id':
                        order['pos_location_id'] = value
                    elif key == '_pos_location_name' or key == 'pos_location_name':
                        order['pos_location_name'] = value
                    # Check for notes-related meta fields
                    elif key == '_pos_notes' or key == 'pos_notes':
                        order['notes'] = value
                    elif key == '_pos_order_notes' or key == 'pos_order_notes':
                        # If we already have notes from _pos_notes, combine them
                        existing_notes = order.get('notes', '')
                        if existing_notes and value:
                            order['notes'] = f"{existing_notes}\n{value}"
                        elif value:
                            order['notes'] = value
                
            # Check if payment method needs enhancement from meta_data
            # Include authorize_net, authnet (website orders), and empty payment_method_title
            if (order.get('payment_method') in ['POS', 'credit_card', 'authorize_net', 'authorize_net_cim_credit_card', 'authnet'] or 
                order.get('payment_method_title') in ['POS Payment', 'Credit Card', 'Credit card', ''] or
                not order.get('payment_method_title')):
                
                # Look for detailed payment method information in meta_data
                payment_method_found = False
                card_brand = None
                card_last4 = None
                
                if 'meta_data' in order and isinstance(order['meta_data'], list):
                    # First, try to extract card brand from _pos_payment_method (POS orders)
                    for meta_item in order['meta_data']:
                        if meta_item.get('key') == '_pos_payment_method':
                            try:
                                import json
                                payment_data = json.loads(meta_item.get('value', '{}'))
                                
                                # Handle single credit card payment
                                if payment_data.get('type') == 'credit':
                                    card_brand = payment_data.get('brand')
                                    card_last4 = payment_data.get('last4')
                                    if card_brand and card_last4:
                                        order['payment_method_title'] = f'Credit Card ({card_brand} ****{card_last4})'
                                        payment_method_found = True
                                    elif card_brand:
                                        order['payment_method_title'] = f'Credit Card ({card_brand})'
                                        payment_method_found = True
                                
                                # Handle split payment - extract first credit card found
                                elif payment_data.get('type') == 'split' and payment_data.get('splitPayments'):
                                    for split_payment in payment_data.get('splitPayments', []):
                                        method = split_payment.get('method', {})
                                        if method.get('type') == 'credit':
                                            card_brand = method.get('brand')
                                            card_last4 = method.get('last4')
                                            if card_brand and card_last4:
                                                # Keep existing split payment title but enhance it
                                                current_title = order.get('payment_method_title', 'Split Payment')
                                                if 'Split Payment' in current_title:
                                                    # Title already shows split payment info, keep it
                                                    payment_method_found = True
                                                else:
                                                    order['payment_method_title'] = f'Split Payment ({card_brand} ****{card_last4})'
                                                    payment_method_found = True
                                                break
                                            elif card_brand:
                                                order['payment_method_title'] = f'Split Payment ({card_brand})'
                                                payment_method_found = True
                                                break
                                
                                # Handle other payment types
                                elif payment_data.get('type') == 'cash':
                                    order['payment_method_title'] = 'Cash'
                                    payment_method_found = True
                                elif payment_data.get('type') == 'check':
                                    order['payment_method_title'] = 'Check Payment'
                                    payment_method_found = True
                                    
                            except (json.JSONDecodeError, AttributeError) as e:
                                logger.warning(f"Could not parse _pos_payment_method meta data: {str(e)}")
                            break
                    
                    # Second, try Authorize.net meta keys (website orders via authnet plugin)
                    if not payment_method_found:
                        authnet_brand = None
                        authnet_last4 = None
                        for meta_item in order['meta_data']:
                            key = meta_item.get('key', '')
                            value = meta_item.get('value', '')
                            if key == '_authnet_cc_type' and value:
                                authnet_brand = value
                            elif key == '_authnet_cc_last4' and value:
                                authnet_last4 = value
                            # Also check WooCommerce Authorize.net CIM plugin keys
                            elif key == '_wc_authorize_net_cim_credit_card_card_type' and value:
                                authnet_brand = value
                            elif key == '_wc_authorize_net_cim_credit_card_last_four' and value:
                                authnet_last4 = value
                        if authnet_brand and authnet_last4:
                            order['payment_method_title'] = f'{authnet_brand} ending in {authnet_last4}'
                            payment_method_found = True
                        elif authnet_brand:
                            order['payment_method_title'] = f'Credit Card ({authnet_brand})'
                            payment_method_found = True
                        elif authnet_last4:
                            order['payment_method_title'] = f'Credit Card ending in {authnet_last4}'
                            payment_method_found = True
                    
                    # Fallback: Look for generic payment method in meta_data
                    if not payment_method_found:
                        for meta_item in order['meta_data']:
                            if meta_item.get('key') == '_payment_method' or meta_item.get('key') == 'payment_method':
                                # Set payment_method_title to the actual payment method
                                order['payment_method_title'] = meta_item.get('value', 'Credit Card')
                                payment_method_found = True
                                break
                
                # If no payment method found in meta_data, set a default
                if not payment_method_found:
                    if order.get('payment_method') == 'credit_card':
                        order['payment_method_title'] = 'Credit Card'
                    else:
                        order['payment_method_title'] = 'Credit Card'
        
        # NOTE: Subscription frequency enrichment is deferred to single-order view
        # (get_woocommerce_order) to avoid N+1 WooCommerce API calls on the list.

        # Return orders with pagination info
        return Response({
            'orders': orders,
            'pagination': {
                'total': total_orders,
                'total_pages': total_pages,
                'current_page': page,
                'per_page': per_page
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting orders from WooCommerce: {str(e)}")
        return Response(
            {"error": f"Error getting orders from WooCommerce: {str(e)}"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def get_woocommerce_order(request, order_id):
    """
    Get a single order from WooCommerce API
    
    Returns detailed information about a specific order
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get order from WooCommerce API
        response = wc.wcapi.get(f"orders/{order_id}")
        
        if not response.ok:
            logger.error(f"Failed to get order from WooCommerce. Status code: {response.status_code}, Response: {response.text}")
            return Response(
                {"error": f"Failed to get order from WooCommerce. Status code: {response.status_code}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            
        # Parse response data
        order = response.json()
        
        # Check for order source in meta_data
        source = None
        if 'meta_data' in order and isinstance(order['meta_data'], list):
            for meta_item in order['meta_data']:
                if meta_item.get('key') == '_order_source' or meta_item.get('key') == 'order_source':
                    source = meta_item.get('value')
                    break
        
        # Add source to order data if found
        if source:
            order['source'] = source
        else:
            order['source'] = 'woocommerce'  # Default source for WooCommerce orders
            
        # Check for created_by in meta_data
        created_by = None
        if 'meta_data' in order and isinstance(order['meta_data'], list):
            for meta_item in order['meta_data']:
                if meta_item.get('key') == '_created_by':
                    created_by = meta_item.get('value')
                    break
        
        # Add created_by to order data if found
        if created_by:
            order['created_by'] = created_by
        
        # Extract transaction_id from meta_data if main field is empty
        pos_transaction_id = None
        if not order.get('transaction_id') and 'meta_data' in order and isinstance(order['meta_data'], list):
            for meta_item in order['meta_data']:
                key = meta_item.get('key')
                value = meta_item.get('value')
                
                # Check for transaction_id in meta_data
                if key in ['_pos_transaction_id', '_transaction_id', '_payment_transaction_id'] and value:
                    order['transaction_id'] = value
                    if key == '_pos_transaction_id':
                        pos_transaction_id = value
                    logger.info(f"📋 Extracted transaction_id from meta_data key '{key}': {value}")
                    break
        
        # 🔥 GET BUNDLE_PRODUCTS FROM CORRESPONDING POS ORDER
        if pos_transaction_id:
            try:
                from .models import POSOrder
                from .serializers import POSOrderSerializer
                pos_order = POSOrder.objects.filter(transaction_id=pos_transaction_id).first()
                if pos_order:
                    # Use serializer to get reconstructed bundle_products (handles both stored and reconstructed)
                    serializer = POSOrderSerializer(pos_order)
                    serialized_data = serializer.data
                    bundle_products = serialized_data.get('bundle_products', [])
                    
                    if bundle_products:
                        order['bundle_products'] = bundle_products
                        logger.info(f"📦 Added bundle_products from POS order {pos_transaction_id} to WooCommerce order {order.get('id')}")
                    else:
                        logger.info(f"📋 No bundle_products found for POS order {pos_transaction_id}")
            except Exception as e:
                logger.error(f"❌ Error fetching bundle_products for POS transaction {pos_transaction_id}: {e}")
        
        # Extract location properties and notes from meta_data
        if 'meta_data' in order and isinstance(order['meta_data'], list):
            for meta_item in order['meta_data']:
                key = meta_item.get('key')
                value = meta_item.get('value')
                
                # Check for location-related meta fields
                if key == '_assigned_location' or key == 'assigned_location':
                    order['assigned_location'] = value
                elif key == '_pos_location_id' or key == 'pos_location_id':
                    order['pos_location_id'] = value
                elif key == '_pos_location_name' or key == 'pos_location_name':
                    order['pos_location_name'] = value
                # Check for notes-related meta fields
                elif key == '_pos_notes' or key == 'pos_notes':
                    order['notes'] = value
                elif key == '_pos_order_notes' or key == 'pos_order_notes':
                    # If we already have notes from _pos_notes, combine them
                    existing_notes = order.get('notes', '')
                    if existing_notes and value:
                        order['notes'] = f"{existing_notes}\n{value}"
                    elif value:
                        order['notes'] = value
        
        # Check if payment method needs enhancement from meta_data
        # Include authorize_net, authnet (website orders), and empty payment_method_title
        if (order.get('payment_method') in ['POS', 'credit_card', 'authorize_net', 'authorize_net_cim_credit_card', 'authnet'] or 
            order.get('payment_method_title') in ['POS Payment', 'Credit Card', 'Credit card', ''] or
            not order.get('payment_method_title')):
            
            # Look for detailed payment method information in meta_data
            payment_method_found = False
            card_brand = None
            card_last4 = None
            
            if 'meta_data' in order and isinstance(order['meta_data'], list):
                # First, try to extract card brand from _pos_payment_method (POS orders)
                for meta_item in order['meta_data']:
                    if meta_item.get('key') == '_pos_payment_method':
                        try:
                            import json
                            payment_data = json.loads(meta_item.get('value', '{}'))
                            
                            # Handle single credit card payment
                            if payment_data.get('type') == 'credit':
                                card_brand = payment_data.get('brand')
                                card_last4 = payment_data.get('last4')
                                if card_brand and card_last4:
                                    order['payment_method_title'] = f'Credit Card ({card_brand} ****{card_last4})'
                                    payment_method_found = True
                                elif card_brand:
                                    order['payment_method_title'] = f'Credit Card ({card_brand})'
                                    payment_method_found = True
                            
                            # Handle split payment - extract first credit card found
                            elif payment_data.get('type') == 'split' and payment_data.get('splitPayments'):
                                for split_payment in payment_data.get('splitPayments', []):
                                    method = split_payment.get('method', {})
                                    if method.get('type') == 'credit':
                                        card_brand = method.get('brand')
                                        card_last4 = method.get('last4')
                                        if card_brand and card_last4:
                                            # Keep existing split payment title but enhance it
                                            current_title = order.get('payment_method_title', 'Split Payment')
                                            if 'Split Payment' in current_title:
                                                # Title already shows split payment info, keep it
                                                payment_method_found = True
                                            else:
                                                order['payment_method_title'] = f'Split Payment ({card_brand} ****{card_last4})'
                                                payment_method_found = True
                                            break
                                        elif card_brand:
                                            order['payment_method_title'] = f'Split Payment ({card_brand})'
                                            payment_method_found = True
                                            break
                            
                            # Handle other payment types
                            elif payment_data.get('type') == 'cash':
                                order['payment_method_title'] = 'Cash'
                                payment_method_found = True
                            elif payment_data.get('type') == 'check':
                                order['payment_method_title'] = 'Check Payment'
                                payment_method_found = True
                                
                        except (json.JSONDecodeError, AttributeError) as e:
                            logger.warning(f"Could not parse _pos_payment_method meta data: {str(e)}")
                        break
                
                # Second, try Authorize.net meta keys (website orders via authnet plugin)
                if not payment_method_found:
                    authnet_brand = None
                    authnet_last4 = None
                    authnet_customer_id = None
                    authnet_card_id = None
                    for meta_item in order['meta_data']:
                        key = meta_item.get('key', '')
                        value = meta_item.get('value', '')
                        if key == '_authnet_cc_type' and value:
                            authnet_brand = value
                        elif key == '_authnet_cc_last4' and value:
                            authnet_last4 = value
                        # Also check WooCommerce Authorize.net CIM plugin keys
                        elif key == '_wc_authorize_net_cim_credit_card_card_type' and value:
                            authnet_brand = value
                        elif key == '_wc_authorize_net_cim_credit_card_last_four' and value:
                            authnet_last4 = value
                        # Collect CIM IDs for potential lookup
                        elif key == '_authnet_customer_id' and value:
                            authnet_customer_id = value
                        elif key in ('_authnet_card_id', '_authorize_net_cim_credit_card_payment_token') and value:
                            authnet_card_id = value
                        elif key == '_authorize_net_cim_credit_card_customer_id' and value:
                            authnet_customer_id = value
                    
                    if authnet_brand and authnet_last4:
                        order['payment_method_title'] = f'{authnet_brand} ending in {authnet_last4}'
                        payment_method_found = True
                    elif authnet_brand:
                        order['payment_method_title'] = f'Credit Card ({authnet_brand})'
                        payment_method_found = True
                    elif authnet_last4:
                        order['payment_method_title'] = f'Credit Card ending in {authnet_last4}'
                        payment_method_found = True
                    elif authnet_customer_id and authnet_card_id:
                        # CIM lookup — fetch card details from Authorize.net
                        try:
                            from payments.authorize_net import AuthorizeNetGateway
                            profiles = AuthorizeNetGateway.get_customer_payment_profiles(authnet_customer_id)
                            if profiles:
                                profile_map = {p['payment_profile_id']: p for p in profiles}
                                if str(authnet_card_id) in profile_map:
                                    card = profile_map[str(authnet_card_id)]
                                    cim_brand = card.get('card_brand', 'Credit Card')
                                    cim_last4 = card.get('last4', '')
                                    if cim_brand and cim_last4:
                                        order['payment_method_title'] = f'{cim_brand} ending in {cim_last4}'
                                        payment_method_found = True
                                    elif cim_last4:
                                        order['payment_method_title'] = f'Credit Card ending in {cim_last4}'
                                        payment_method_found = True
                                else:
                                    logger.warning(f"Payment profile {authnet_card_id} not found in CIM customer {authnet_customer_id}")
                        except Exception as e:
                            logger.warning(f"CIM lookup failed for order {order_id}: {e}")
                    
                    # Third, try getTransactionDetails from Authorize.Net using transaction_id
                    if not payment_method_found and order.get('transaction_id'):
                        try:
                            import os
                            from authorizenet import apicontractsv1 as anet_contracts
                            from authorizenet.apicontrollers import getTransactionDetailsController

                            from core.secrets import get_secret
                            api_login_id = get_secret('AUTHORIZE_NET_LOGIN_ID', '')
                            transaction_key = get_secret('AUTHORIZE_NET_TRANSACTION_KEY', '')
                            sandbox_mode = get_secret('AUTHORIZE_NET_SANDBOX', 'True').lower() == 'true'

                            if api_login_id and transaction_key:
                                merchant_auth = anet_contracts.merchantAuthenticationType()
                                merchant_auth.name = api_login_id
                                merchant_auth.transactionKey = transaction_key

                                txn_req = anet_contracts.getTransactionDetailsRequest()
                                txn_req.merchantAuthentication = merchant_auth
                                txn_req.transId = str(order['transaction_id'])

                                txn_ctrl = getTransactionDetailsController(txn_req)
                                endpoint = 'https://apitest.authorize.net/xml/v1/request.api' if sandbox_mode else 'https://api.authorize.net/xml/v1/request.api'
                                txn_ctrl.setenvironment(endpoint)
                                txn_ctrl.execute()
                                txn_resp = txn_ctrl.getresponse()

                                if txn_resp and txn_resp.messages.resultCode == "Ok" and hasattr(txn_resp, 'transaction'):
                                    txn = txn_resp.transaction
                                    if hasattr(txn, 'payment') and txn.payment is not None:
                                        cc = getattr(txn.payment, 'creditCard', None)
                                        if cc:
                                            txn_brand = str(cc.cardType) if hasattr(cc, 'cardType') and cc.cardType else ''
                                            txn_card_num = str(cc.cardNumber) if cc.cardNumber else ''
                                            txn_last4 = txn_card_num[-4:] if len(txn_card_num) >= 4 else ''
                                            if txn_brand and txn_last4:
                                                order['payment_method_title'] = f'{txn_brand} ending in {txn_last4}'
                                                payment_method_found = True
                                                logger.info(f"Enriched order {order_id} payment via getTransactionDetails: {txn_brand} ending in {txn_last4}")
                                            elif txn_last4:
                                                order['payment_method_title'] = f'Credit Card ending in {txn_last4}'
                                                payment_method_found = True
                        except Exception as e:
                            logger.warning(f"getTransactionDetails lookup failed for order {order_id}: {e}")
                
                # Fallback: Look for generic payment method in meta_data
                if not payment_method_found:
                    for meta_item in order['meta_data']:
                        if meta_item.get('key') == '_payment_method' or meta_item.get('key') == 'payment_method':
                            # Set payment_method_title to the actual payment method
                            order['payment_method_title'] = meta_item.get('value', 'Credit Card')
                            payment_method_found = True
                            break
            
            # If no payment method found in meta_data or title is still POS Payment, set a default
            if not payment_method_found or order.get('payment_method_title') == 'POS Payment':
                order['payment_method_title'] = 'Credit Card'
        
        # Enrich subscription frequency from WooCommerce API if needed
        try:
            _enrich_order_subscription_frequency(order)
        except Exception as e:
            logger.warning(f"Failed to enrich subscription frequency for order {order_id}: {e}")

        # Fetch detailed refund data for line items (only in single-order view)
        # PRIORITY: POS DB first (accurate per-item data), WooCommerce API fallback only if no POS records
        # WooCommerce distributes amount-only refunds proportionally across ALL items,
        # making its line_items data unreliable for partial refunds.
        if order.get('refunds') and len(order['refunds']) > 0:
            try:
                refunded_items_map = {}
                used_pos_db = False
                
                # === TRY POS DATABASE FIRST (most reliable source) ===
                woo_order_id_str = str(order.get('id', ''))
                pos_order = POSOrder.objects.filter(metadata__woo_order_id=woo_order_id_str).first()
                if not pos_order and woo_order_id_str.isdigit():
                    pos_order = POSOrder.objects.filter(metadata__woo_order_id=int(woo_order_id_str)).first()
                
                if pos_order:
                    # Get ALL refund items across ALL refunds for this order from POS DB
                    pos_refund_items = POSOrderRefundItem.objects.filter(
                        refund__order=pos_order
                    ).select_related('refund', 'order_item')
                    
                    if pos_refund_items.exists():
                        used_pos_db = True
                        for ri in pos_refund_items:
                            # Match by product name (normalized) — same key format used for line_items below
                            item_name = ri.name or ''
                            item_product_id = ri.product_id or ''
                            # Use product_id + name as key for matching with WooCommerce line_items
                            key = f"{item_product_id}_{item_name.replace(' ', '_')}" if item_product_id else item_name.lower().strip()
                            
                            refund_info = {
                                'refunded': True,
                                'refund_qty': ri.quantity,
                                'refund_total': float(ri.subtotal),
                                'refund_date': ri.refund.created_at.isoformat() if ri.refund.created_at else '',
                                'refund_reason': ri.refund_reason or ri.refund.refund_reason or '',
                                'refund_id': str(ri.refund.id)
                            }
                            
                            if key not in refunded_items_map:
                                refunded_items_map[key] = {
                                    'total_refunded_qty': 0,
                                    'total_refunded_amount': 0,
                                    'refund_history': []
                                }
                            refunded_items_map[key]['total_refunded_qty'] += refund_info['refund_qty']
                            refunded_items_map[key]['total_refunded_amount'] += refund_info['refund_total']
                            refunded_items_map[key]['refund_history'].append(refund_info)
                        
                        logger.info(f"📋 POS DB refund data for order {order['id']}: {len(refunded_items_map)} items from {pos_refund_items.count()} refund records")
                
                # === FALLBACK: WooCommerce API (only if no POS records) ===
                if not used_pos_db:
                    logger.info(f"📋 No POS DB records for order {order['id']}, falling back to WooCommerce API")
                    order_refunds_data = wc.get_order_refunds(order['id'])
                    
                    for refund_summary in order_refunds_data:
                        detailed_refund = wc.get_order_refund(order['id'], refund_summary.get('id'))
                        
                        if detailed_refund and detailed_refund.get('line_items'):
                            for refunded_item in detailed_refund['line_items']:
                                item_qty = abs(int(refunded_item.get('quantity', 0)))
                                item_total = abs(float(refunded_item.get('total', 0)))
                                # WooCommerce returns ALL line items; skip non-refunded ones
                                if item_qty == 0 and item_total == 0:
                                    continue
                                
                                item_product_id = refunded_item.get('product_id', '')
                                item_name = refunded_item.get('name', '')
                                key = f"{item_product_id}_{item_name.replace(' ', '_')}" if item_product_id else item_name.lower().strip()
                                
                                refund_info = {
                                    'refunded': True,
                                    'refund_qty': item_qty,
                                    'refund_total': item_total,
                                    'refund_date': detailed_refund.get('date_created', ''),
                                    'refund_reason': detailed_refund.get('reason', ''),
                                    'refund_id': detailed_refund.get('id')
                                }
                                
                                if key not in refunded_items_map:
                                    refunded_items_map[key] = {
                                        'total_refunded_qty': 0,
                                        'total_refunded_amount': 0,
                                        'refund_history': []
                                    }
                                refunded_items_map[key]['total_refunded_qty'] += refund_info['refund_qty']
                                refunded_items_map[key]['total_refunded_amount'] += refund_info['refund_total']
                                refunded_items_map[key]['refund_history'].append(refund_info)
                
                # === APPLY refund_status to line items ===
                if order.get('line_items') and refunded_items_map:
                    for line_item in order['line_items']:
                        item_product_id = line_item.get('product_id', '')
                        item_name = line_item.get('name', '')
                        # Try matching by product_id+name key first, then by name only
                        line_item_keys = [
                            f"{item_product_id}_{item_name.replace(' ', '_')}" if item_product_id else None,
                            item_name.lower().strip(),
                        ]
                        line_item_keys = [k for k in line_item_keys if k]
                        
                        refund_found = False
                        for key in line_item_keys:
                            if key in refunded_items_map:
                                refund_data = refunded_items_map[key]
                                line_item['refund_status'] = {
                                    'is_refunded': True,
                                    'refund_qty': refund_data['total_refunded_qty'],
                                    'refund_total': refund_data['total_refunded_amount'],
                                    'refund_history': refund_data['refund_history']
                                }
                                refund_found = True
                                break
                        if not refund_found:
                            line_item['refund_status'] = {
                                'is_refunded': False,
                                'refund_qty': 0,
                                'refund_total': 0,
                                'refund_history': []
                            }
                elif order.get('line_items'):
                    for line_item in order['line_items']:
                        line_item['refund_status'] = {
                            'is_refunded': False,
                            'refund_qty': 0,
                            'refund_total': 0,
                            'refund_history': []
                        }
                
                logger.info(f"Fetched refund data for order {order['id']}: {len(refunded_items_map)} refunded items mapped (source: {'POS DB' if used_pos_db else 'WooCommerce API'})")
            except Exception as e:
                logger.error(f"Error fetching refund data for order {order['id']}: {str(e)}")
                if order.get('line_items'):
                    for line_item in order['line_items']:
                        line_item['refund_status'] = {
                            'is_refunded': False,
                            'refund_qty': 0,
                            'refund_total': 0,
                            'refund_history': []
                        }
        else:
            if order.get('line_items'):
                for line_item in order['line_items']:
                    line_item['refund_status'] = {
                        'is_refunded': False,
                        'refund_qty': 0,
                        'refund_total': 0,
                        'refund_history': []
                    }

        # Return order
        return Response(order)
        
    except Exception as e:
        logger.error(f"Error getting order from WooCommerce: {str(e)}")
        return Response(
            {"error": f"Error getting order from WooCommerce: {str(e)}"}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def retry_failed_order(request, order_id):
    """
    Retry payment on a failed WooCommerce order using a saved card (Authorize.net CIM).
    
    Request body:
    {
        "customer_profile_id": "123456",
        "payment_profile_id": "789012"
    }
    """
    try:
        data = request.data
        customer_profile_id = data.get('customer_profile_id')
        payment_profile_id = data.get('payment_profile_id')
        
        if not customer_profile_id or not payment_profile_id:
            return Response({'success': False, 'error': 'Missing customer_profile_id or payment_profile_id'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        # 1. Fetch the WooCommerce order to get the total and verify it's failed
        wc = WooCommerceAPI()
        order_resp = wc.wcapi.get(f"orders/{order_id}")
        if not order_resp.ok:
            return Response({'success': False, 'error': f'Order {order_id} not found'},
                            status=status.HTTP_404_NOT_FOUND)
        
        order = order_resp.json()
        order_status = order.get('status', '')
        
        if order_status != 'failed':
            return Response({'success': False, 'error': f'Order is not in failed status (current: {order_status})'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        amount = float(order.get('total', 0))
        if amount <= 0:
            return Response({'success': False, 'error': 'Order total is zero or negative'},
                            status=status.HTTP_400_BAD_REQUEST)
        
        # Build order items for Authorize.net line items
        line_items = order.get('line_items', [])
        order_items = []
        for item in line_items[:30]:  # Authorize.net max 30 line items
            order_items.append({
                'name': (item.get('name', 'Item'))[:31],
                'quantity': item.get('quantity', 1),
                'unitPrice': float(item.get('price', 0)),
            })
        
        logger.info(f"Retrying payment for failed order #{order_id} — amount: ${amount}, CIM profile: {customer_profile_id}/{payment_profile_id}")
        
        # 2. Charge the saved card via Authorize.net CIM
        from payments.authorize_net import AuthorizeNetGateway
        payment_response = AuthorizeNetGateway.charge_customer_profile(
            customer_profile_id=customer_profile_id,
            payment_profile_id=payment_profile_id,
            amount=amount,
            order_items=order_items,
            order_id=str(order_id)
        )
        
        if not payment_response.get('success'):
            logger.warning(f"Retry payment failed for order #{order_id}: {payment_response.get('message')}")
            return Response({
                'success': False,
                'error': payment_response.get('message', 'Payment failed'),
                'code': payment_response.get('code', 'UNKNOWN'),
            }, status=status.HTTP_400_BAD_REQUEST)
        
        transaction_id = payment_response.get('transactionId', '')
        logger.info(f"Retry payment succeeded for order #{order_id} — TX: {transaction_id}")
        
        # 3. Update WooCommerce order status to processing and add note
        try:
            update_data = {
                'status': 'processing',
                'transaction_id': transaction_id,
                'set_paid': True,
            }
            update_resp = wc.wcapi.put(f"orders/{order_id}", update_data)
            if update_resp.ok:
                logger.info(f"Updated order #{order_id} status to processing")
            else:
                logger.warning(f"Failed to update order #{order_id} status: {update_resp.status_code}")
            
            # Add order note
            note_text = f"Payment retried successfully via POS. Transaction ID: {transaction_id}. Auth Code: {payment_response.get('authCode', 'N/A')}."
            wc.wcapi.post(f"orders/{order_id}/notes", {'note': note_text})
        except Exception as e:
            logger.error(f"Error updating order status after retry: {str(e)}")
        
        # 4. Save transaction record
        try:
            from payments.models import PaymentTransaction
            PaymentTransaction.objects.create(
                transaction_id=transaction_id,
                order_id=str(order_id),
                amount=amount,
                status='approved',
                payment_type='credit',
                auth_code=payment_response.get('authCode'),
                response_code=payment_response.get('responseCode'),
                response_message=payment_response.get('message')
            )
        except Exception as e:
            logger.error(f"Error saving retry transaction record: {str(e)}")
        
        return Response({
            'success': True,
            'transaction_id': transaction_id,
            'auth_code': payment_response.get('authCode'),
            'message': 'Payment processed successfully. Order updated to processing.',
            'new_status': 'processing',
        })
        
    except Exception as e:
        logger.exception(f"Error retrying payment for order {order_id}: {str(e)}")
        return Response({'success': False, 'error': str(e)},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_failed_orders(request):
    """
    Get recent failed WooCommerce orders for notification purposes.
    Returns count and a summary list of failed orders (last 30 days by default).
    
    Query params:
      - days: Number of days to look back (default 30)
      - per_page: Max orders to return (default 20)
      - dismissed: Comma-separated order IDs to exclude from count
    """
    try:
        days = int(request.query_params.get('days', 30))
        per_page = min(int(request.query_params.get('per_page', 20)), 50)
        dismissed_ids = request.query_params.get('dismissed', '')
        dismissed_set = set(dismissed_ids.split(',')) if dismissed_ids else set()
        
        from datetime import datetime, timedelta
        after_date = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT00:00:00')
        
        wc = WooCommerceAPI()
        
        params = {
            'status': 'failed',
            'per_page': per_page,
            'page': 1,
            'after': after_date,
            'orderby': 'date',
            'order': 'desc',
        }
        
        logger.info(f"Fetching failed orders (last {days} days, per_page={per_page})")
        response = wc.wcapi.get("orders", params=params)
        
        if not response.ok:
            logger.error(f"Failed to fetch failed orders: {response.status_code}")
            return Response({'count': 0, 'orders': [], 'error': 'Failed to fetch from WooCommerce'})
        
        orders = response.json() or []
        total_count = int(response.headers.get('X-WP-Total', len(orders)))
        
        # Build summary list
        summary = []
        undismissed_count = 0
        for order in orders:
            order_id_str = str(order.get('id', ''))
            is_dismissed = order_id_str in dismissed_set
            
            if not is_dismissed:
                undismissed_count += 1
            
            billing = order.get('billing', {})
            customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or 'Guest'
            
            summary.append({
                'id': order.get('id'),
                'order_number': order.get('number', order.get('id')),
                'date_created': order.get('date_created'),
                'total': order.get('total'),
                'customer_name': customer_name,
                'customer_email': billing.get('email', ''),
                'payment_method': order.get('payment_method_title', order.get('payment_method', 'Unknown')),
                'dismissed': is_dismissed,
            })
        
        logger.info(f"Found {total_count} total failed orders, {undismissed_count} undismissed")
        
        return Response({
            'count': total_count,
            'undismissed_count': undismissed_count,
            'orders': summary,
        })
        
    except Exception as e:
        logger.error(f"Error fetching failed orders: {str(e)}")
        return Response(
            {'count': 0, 'orders': [], 'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
