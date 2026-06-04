from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .woocommerce import WooCommerceAPI
from .models import Product, ProductVariation
import logging
import re

logger = logging.getLogger(__name__)


@api_view(['GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def get_shipment_trackings(request, order_id):
    """
    Get all shipment trackings for a specific order.
    
    GET /api/woocommerce/orders/<order_id>/shipment-trackings/
    
    Returns:
        List of shipment tracking objects
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get shipment trackings from WooCommerce
        trackings = wc.get_shipment_trackings(order_id)
        
        return Response({
            'success': True,
            'data': trackings
        })
        
    except Exception as e:
        logger.error(f"Error getting shipment trackings for order {order_id}: {str(e)}")
        return Response(
            {
                'success': False,
                'error': f"Error getting shipment trackings: {str(e)}"
            }, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def get_shipment_tracking(request, order_id, tracking_id):
    """
    Get a specific shipment tracking by tracking ID.
    
    GET /api/woocommerce/orders/<order_id>/shipment-trackings/<tracking_id>/
    
    Returns:
        Shipment tracking object
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get shipment tracking from WooCommerce
        tracking = wc.get_shipment_tracking(order_id, tracking_id)
        
        if tracking:
            return Response({
                'success': True,
                'data': tracking
            })
        else:
            return Response(
                {
                    'success': False,
                    'error': 'Shipment tracking not found'
                },
                status=status.HTTP_404_NOT_FOUND
            )
        
    except Exception as e:
        logger.error(f"Error getting shipment tracking {tracking_id} for order {order_id}: {str(e)}")
        return Response(
            {
                'success': False,
                'error': f"Error getting shipment tracking: {str(e)}"
            }, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def create_shipment_tracking(request, order_id):
    """
    Create a new shipment tracking for an order.
    
    POST /api/woocommerce/orders/<order_id>/shipment-trackings/
    
    Request body:
    {
        "tracking_provider": "USPS",
        "tracking_number": "1234567890",
        "date_shipped": "2024-11-04",
        "products_list": [{"product_id": "123", "qty": 2}],
        "status_shipped": "shipped" or "partial"
    }
    
    Returns:
        Created shipment tracking object
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # Get tracking data from request
        tracking_data = request.data
        
        # Validate required fields
        if not tracking_data.get('tracking_number'):
            return Response(
                {
                    'success': False,
                    'error': 'Tracking number is required'
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate provider
        if not tracking_data.get('tracking_provider') and not tracking_data.get('custom_tracking_provider'):
            return Response(
                {
                    'success': False,
                    'error': 'Tracking provider or custom tracking provider is required'
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Pre-set dedup cache BEFORE the WooCommerce API call.
        # The WC API call triggers WordPress meta hooks that fire our wc_tracking_email_webhook.
        # By setting the cache first, the webhook will see it and skip (preventing duplicate emails).
        import hashlib
        from django.core.cache import cache
        tn = tracking_data.get('tracking_number', '')
        dedup_key = f"tracking_email_sent_{order_id}_{hashlib.md5(tn.encode()).hexdigest()}"
        cache.set(dedup_key, True, 300)
        logger.info(f"Pre-set dedup cache for order {order_id} tracking {tn}")
        
        # Create shipment tracking in WooCommerce
        tracking = wc.create_shipment_tracking(order_id, tracking_data)
        
        if tracking:
            # Send tracking email directly — we have the status_shipped context
            # (partial vs full) which the WordPress webhook does not have.
            tracking_email_sent = False
            try:
                email_tracking_data = {**tracking_data}
                if isinstance(tracking, dict):
                    if tracking.get('tracking_link'):
                        email_tracking_data['tracking_link'] = tracking['tracking_link']
                    if tracking.get('tracking_provider'):
                        email_tracking_data.setdefault('tracking_provider', tracking['tracking_provider'])
                
                status_shipped = tracking_data.get('status_shipped', 'shipped')
                if status_shipped == 'partial':
                    from .views_receipts import send_partial_shipped_email
                    tracking_email_sent = send_partial_shipped_email(order_id, email_tracking_data)
                    logger.info(f"Partial-shipped email {'sent' if tracking_email_sent else 'failed'} for order {order_id}")
                else:
                    from .views_receipts import send_tracking_email
                    tracking_email_sent = send_tracking_email(order_id, email_tracking_data)
                    logger.info(f"Tracking email {'sent' if tracking_email_sent else 'failed'} for order {order_id}")
            except Exception as email_err:
                logger.error(f"Error sending tracking email for order {order_id}: {email_err}")
            
            return Response({
                'success': True,
                'data': tracking,
                'message': 'Shipment tracking created successfully',
                'tracking_email_sent': tracking_email_sent
            }, status=status.HTTP_201_CREATED)
        else:
            return Response(
                {
                    'success': False,
                    'error': 'Failed to create shipment tracking'
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
    except Exception as e:
        logger.error(f"Error creating shipment tracking for order {order_id}: {str(e)}")
        return Response(
            {
                'success': False,
                'error': f"Error creating shipment tracking: {str(e)}"
            }, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['DELETE', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def delete_shipment_tracking(request, order_id, tracking_id):
    """
    Delete a shipment tracking from an order.
    
    DELETE /api/woocommerce/orders/<order_id>/shipment-trackings/<tracking_id>/
    
    Returns:
        Success message
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Delete shipment tracking from WooCommerce
        success = wc.delete_shipment_tracking(order_id, tracking_id)
        
        if success:
            return Response({
                'success': True,
                'message': 'Shipment tracking deleted successfully'
            })
        else:
            return Response(
                {
                    'success': False,
                    'error': 'Failed to delete shipment tracking'
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
    except Exception as e:
        logger.error(f"Error deleting shipment tracking {tracking_id} for order {order_id}: {str(e)}")
        return Response(
            {
                'success': False,
                'error': f"Error deleting shipment tracking: {str(e)}"
            }, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def get_shipment_tracking_providers(request, order_id):
    """
    Get all available shipment tracking providers.
    
    GET /api/woocommerce/orders/<order_id>/shipment-trackings/providers/
    
    Returns:
        Dictionary of provider names
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get shipment tracking providers from WooCommerce
        providers = wc.get_shipment_tracking_providers(order_id)
        
        return Response({
            'success': True,
            'data': providers
        })
        
    except Exception as e:
        logger.error(f"Error getting shipment tracking providers: {str(e)}")
        return Response(
            {
                'success': False,
                'error': f"Error getting shipment tracking providers: {str(e)}"
            }, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['PUT', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def update_order_shipping_address(request, order_id):
    """
    Update the shipping address on a WooCommerce order.
    Optionally also updates the customer's profile shipping address.
    
    PUT /api/woocommerce/orders/<order_id>/update-shipping/
    
    Request body:
    {
        "first_name": "John",
        "last_name": "Doe",
        "address_1": "123 Main St",
        "address_2": "",
        "city": "New York",
        "state": "NY",
        "postcode": "10001",
        "country": "US",
        "update_profile": false
    }
    
    Returns:
        Updated shipping address data
    """
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        shipping_data = dict(request.data)
        update_profile = shipping_data.get('update_profile', False)
        
        if not shipping_data.get('address_1'):
            return Response(
                {'success': False, 'error': 'Address line 1 is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        wc = WooCommerceAPI()
        
        # Build the shipping object for the WooCommerce order
        shipping_payload = {
            'first_name': shipping_data.get('first_name', ''),
            'last_name': shipping_data.get('last_name', ''),
            'address_1': shipping_data.get('address_1', ''),
            'address_2': shipping_data.get('address_2', ''),
            'city': shipping_data.get('city', ''),
            'state': shipping_data.get('state', ''),
            'postcode': shipping_data.get('postcode', ''),
            'country': shipping_data.get('country', 'US'),
        }
        
        # 1) Update the order's shipping address in WooCommerce
        logger.info(f"Updating shipping address for WooCommerce order {order_id}: {shipping_payload}")
        order_response = wc.wcapi.put(f"orders/{order_id}", {"shipping": shipping_payload})
        
        if not order_response.ok:
            error_text = order_response.text[:500] if hasattr(order_response, 'text') else 'Unknown error'
            logger.error(f"Failed to update order shipping: {order_response.status_code} - {error_text}")
            return Response(
                {'success': False, 'error': f'Failed to update order shipping in WooCommerce: {error_text}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        order_data = order_response.json()
        logger.info(f"Successfully updated shipping address for WooCommerce order {order_id}")
        
        # 2) Optionally update the customer profile in WooCommerce + local DB
        profile_updated = False
        if update_profile:
            customer_id = order_data.get('customer_id')
            if customer_id and customer_id != 0:
                logger.info(f"Also updating customer {customer_id} shipping address")
                customer_response = wc.wcapi.put(f"customers/{customer_id}", {"shipping": shipping_payload})
                
                if customer_response.ok:
                    profile_updated = True
                    logger.info(f"Successfully updated customer {customer_id} shipping address in WooCommerce")
                    
                    # Also update the local Contact record
                    try:
                        from .models import Contact
                        local_contact = Contact.objects.filter(woo_customer_id=customer_id).first()
                        if local_contact:
                            local_contact.shipping_address = shipping_payload.get('address_1', '')
                            local_contact.shipping_city = shipping_payload.get('city', '')
                            local_contact.shipping_state = shipping_payload.get('state', '')
                            local_contact.shipping_postcode = shipping_payload.get('postcode', '')
                            local_contact.shipping_country = shipping_payload.get('country', 'US')
                            local_contact.shipping_same_as_billing = False
                            local_contact.save()
                            logger.info(f"Updated local Contact {local_contact.id} shipping address")
                    except Exception as e:
                        logger.warning(f"Failed to update local contact record: {str(e)}")
                else:
                    logger.warning(f"Failed to update customer {customer_id} shipping in WooCommerce: {customer_response.status_code}")
            else:
                logger.info("No WooCommerce customer_id on order, skipping profile update")
        
        return Response({
            'success': True,
            'data': {
                'shipping': order_data.get('shipping', {}),
                'profile_updated': profile_updated
            },
            'message': 'Shipping address updated successfully'
        })
        
    except Exception as e:
        logger.error(f"Error updating shipping address for order {order_id}: {str(e)}")
        return Response(
            {'success': False, 'error': f"Error updating shipping address: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def get_product_name_by_woo_id(request, woo_product_id):
    """
    Get product name by WooCommerce product ID from database.
    Checks both Product table (for simple products) and ProductVariation table (for variations).
    
    GET /api/products/by-woo-id/<woo_product_id>/
    
    Returns:
        Product name with variation attributes if applicable
    """
    # Handle OPTIONS preflight request
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)
    
    try:
        # First, try to find as a simple product
        product = Product.objects.filter(woo_product_id=woo_product_id).first()
        
        if product:
            return Response({
                'success': True,
                'data': {
                    'id': str(product.id),
                    'woo_product_id': product.woo_product_id,
                    'name': product.name,
                    'type': 'product'
                }
            })
        
        # If not found as product, try to find as a variation
        from .models import ProductVariation
        variation = ProductVariation.objects.select_related('product').filter(
            woo_variation_id=woo_product_id
        ).first()
        
        if variation:
            # Build variation name from parent product + attributes
            parent_name = variation.product.name
            variation_name = parent_name
            
            # Try to extract variation attributes from woo_data
            if variation.woo_data and isinstance(variation.woo_data, dict):
                attributes = variation.woo_data.get('attributes', [])
                if attributes:
                    # Extract attribute values (e.g., "Vanilla", "Large", etc.)
                    attr_values = []
                    for attr in attributes:
                        if isinstance(attr, dict) and 'option' in attr:
                            attr_values.append(attr['option'])
                    
                    if attr_values:
                        variation_name = f"{parent_name} {' '.join(attr_values)}"
            
            return Response({
                'success': True,
                'data': {
                    'id': str(variation.id),
                    'parent_product_id': str(variation.product.id),
                    'woo_product_id': woo_product_id,
                    'woo_variation_id': variation.woo_variation_id,
                    'name': variation_name,
                    'type': 'variation',
                    'parent_product': parent_name
                }
            })
        
        # Not found in either table
        return Response(
            {
                'success': False,
                'error': f'Product or variation with ID {woo_product_id} not found'
            },
            status=status.HTTP_404_NOT_FOUND
        )
        
    except Exception as e:
        logger.error(f"Error getting product by woo_product_id {woo_product_id}: {str(e)}")
        return Response(
            {
                'success': False,
                'error': f"Error getting product: {str(e)}"
            }, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def _normalize_product_name(name):
    """Normalize names for tolerant matching."""
    return re.sub(r'[^a-z0-9]+', '', (name or '').strip().lower())


@api_view(['POST', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def resolve_product(request):
    """
    Resolve a product for Buy Again using deterministic priority.

    POST /api/products/resolve/
    Body: { "woo_product_id"?: number, "sku"?: string, "name"?: string }
    """
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)

    try:
        payload = request.data or {}
        woo_product_id = payload.get('woo_product_id')
        sku = (payload.get('sku') or '').strip()
        name = (payload.get('name') or '').strip()

        # Keep behavior aligned with POS catalog visibility.
        catalog_qs = Product.objects.exclude(product_type='variation').exclude(status='draft')

        # 1) Resolve by exact Woo product ID / Woo variation ID
        if woo_product_id:
            try:
                woo_product_id_int = int(str(woo_product_id).strip())
            except (TypeError, ValueError):
                woo_product_id_int = None

            if woo_product_id_int is not None:
                product = catalog_qs.filter(woo_product_id=woo_product_id_int).first()
                if product:
                    return Response({
                        'success': True,
                        'data': {
                            'id': str(product.id),
                            'woo_product_id': product.woo_product_id,
                            'name': product.name,
                            'type': 'product',
                            'matched_by': 'woo_product_id'
                        }
                    })

                variation = ProductVariation.objects.select_related('product').filter(
                    woo_variation_id=woo_product_id_int,
                    product__status__iexact='publish'
                ).exclude(product__product_type='variation').first()
                if variation:
                    return Response({
                        'success': True,
                        'data': {
                            'id': str(variation.id),
                            'parent_product_id': str(variation.product.id),
                            'woo_product_id': woo_product_id_int,
                            'woo_variation_id': variation.woo_variation_id,
                            'name': variation.product.name,
                            'type': 'variation',
                            'parent_product': variation.product.name,
                            'matched_by': 'woo_variation_id'
                        }
                    })

        # 2) Resolve by exact SKU (case-insensitive)
        if sku:
            product = catalog_qs.filter(simple_details__sku__iexact=sku).distinct().first()
            if not product:
                product = catalog_qs.filter(variations__sku__iexact=sku).distinct().first()

            if product:
                return Response({
                    'success': True,
                    'data': {
                        'id': str(product.id),
                        'woo_product_id': product.woo_product_id,
                        'name': product.name,
                        'type': 'product',
                        'matched_by': 'sku'
                    }
                })

        # 3) Resolve by name (exact first, then normalized-near match)
        if name:
            exact = catalog_qs.filter(name__iexact=name).order_by('name').first()
            if exact:
                return Response({
                    'success': True,
                    'data': {
                        'id': str(exact.id),
                        'woo_product_id': exact.woo_product_id,
                        'name': exact.name,
                        'type': 'product',
                        'matched_by': 'name_exact'
                    }
                })

            normalized_target = _normalize_product_name(name)
            candidates = list(catalog_qs.filter(name__icontains=name).order_by('name')[:25])
            normalized_matches = [p for p in candidates if _normalize_product_name(p.name) == normalized_target]

            if len(normalized_matches) == 1:
                matched = normalized_matches[0]
                return Response({
                    'success': True,
                    'data': {
                        'id': str(matched.id),
                        'woo_product_id': matched.woo_product_id,
                        'name': matched.name,
                        'type': 'product',
                        'matched_by': 'name_normalized'
                    }
                })

            if len(candidates) == 1:
                matched = candidates[0]
                return Response({
                    'success': True,
                    'data': {
                        'id': str(matched.id),
                        'woo_product_id': matched.woo_product_id,
                        'name': matched.name,
                        'type': 'product',
                        'matched_by': 'name_single_candidate'
                    }
                })

            if len(normalized_matches) > 1 or len(candidates) > 1:
                return Response(
                    {
                        'success': False,
                        'error': f'Ambiguous product name: "{name}"',
                        'match_count': max(len(normalized_matches), len(candidates))
                    },
                    status=status.HTTP_409_CONFLICT
                )

        return Response(
            {
                'success': False,
                'error': 'Could not resolve product from provided identifiers'
            },
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        logger.error(f"Error resolving product: {str(e)}")
        return Response(
            {
                'success': False,
                'error': f"Error resolving product: {str(e)}"
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def resend_tracking_email(request, order_id):
    """
    Resend a shipment tracking notification email for an existing tracking.
    Intentionally bypasses the 5-minute dedup cache.

    POST /api/woocommerce/orders/<order_id>/resend-tracking-email/
    Body (optional): { "tracking_id": "<specific_tracking_id>" }
    If tracking_id is omitted, resends the most recent tracking.
    """
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)

    try:
        wc = WooCommerceAPI()

        # Get all trackings for this order
        trackings = wc.get_shipment_trackings(order_id)
        if not trackings:
            return Response({
                'success': False,
                'error': 'No shipment trackings found for this order'
            }, status=status.HTTP_404_NOT_FOUND)

        # Pick the requested tracking or the most recent one
        requested_id = request.data.get('tracking_id') if request.data else None
        tracking = None
        if requested_id:
            tracking = next((t for t in trackings if t.get('tracking_id') == requested_id), None)
            if not tracking:
                return Response({
                    'success': False,
                    'error': f'Tracking {requested_id} not found on this order'
                }, status=status.HTTP_404_NOT_FOUND)
        else:
            # Use the last tracking (most recently added)
            tracking = trackings[-1]

        tracking_data = {
            'tracking_provider': tracking.get('tracking_provider', ''),
            'tracking_number': tracking.get('tracking_number', ''),
            'date_shipped': tracking.get('date_shipped', ''),
            'tracking_link': tracking.get('tracking_link', ''),
            'products_list': tracking.get('products_list', []),
        }

        # Clear any existing dedup cache so the email goes through
        import hashlib
        from django.core.cache import cache
        tn = tracking_data['tracking_number']
        dedup_key = f"tracking_email_sent_{order_id}_{hashlib.md5(tn.encode()).hexdigest()}"
        cache.delete(dedup_key)

        # Determine shipped vs partial-shipped based on order status
        woo_order = wc.get_order(order_id)
        order_status = woo_order.get('status', '') if woo_order else ''

        if order_status in ('partial-shipped', 'partially-shipped'):
            from .views_receipts import send_partial_shipped_email
            sent = send_partial_shipped_email(order_id, tracking_data)
        else:
            from .views_receipts import send_tracking_email as _send_tracking_email
            sent = _send_tracking_email(order_id, tracking_data)

        logger.info(f"📧 Resend tracking email for order {order_id}: {'sent' if sent else 'failed'}")

        return Response({
            'success': True,
            'email_sent': sent,
            'message': f"Tracking email {'resent successfully' if sent else 'failed to send'} for order {order_id}"
        }, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error resending tracking email for order {order_id}: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST', 'OPTIONS'])
@permission_classes([IsAuthenticated])
def wc_tracking_email_webhook(request):
    """
    Webhook endpoint called by WordPress when shipment tracking is added
    via WooCommerce admin (not through our POS).
    Triggers our branded tracking email to the customer.

    POST /api/webhooks/woocommerce/tracking-email/
    Headers: X-DS-Webhook-Secret: <shared_secret>
    Body: { order_id, tracking_provider, tracking_number, date_shipped,
            custom_tracking_link, products_list }
    """
    if request.method == 'OPTIONS':
        return Response(status=status.HTTP_200_OK)

    from core.secrets import get_secret
    expected_secret = get_secret('DS_WEBHOOK_SECRET', 'ds-tracking-webhook-2026')
    provided_secret = request.META.get('HTTP_X_DS_WEBHOOK_SECRET', '')
    if provided_secret != expected_secret:
        logger.warning(f"Tracking email webhook: invalid secret")
        return Response({'success': False, 'error': 'Unauthorized'}, status=status.HTTP_403_FORBIDDEN)

    try:
        data = request.data
        order_id = data.get('order_id')
        if not order_id:
            return Response({'success': False, 'error': 'order_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        tracking_number = data.get('tracking_number', '')

        # Server-side dedup: prevent duplicate emails for the same order+tracking within 5 minutes
        # Use cache.add() which is atomic — only succeeds if key doesn't exist yet.
        # This prevents race conditions where two webhook calls arrive simultaneously.
        import hashlib
        from django.core.cache import cache
        dedup_key = f"tracking_email_sent_{order_id}_{hashlib.md5(tracking_number.encode()).hexdigest()}"
        if not cache.add(dedup_key, True, 300):
            # Key already existed — another request is handling or has handled this email
            logger.info(f"Tracking email webhook: duplicate request for order {order_id} tracking {tracking_number}, skipping")
            return Response({
                'success': True,
                'email_sent': False,
                'message': f"Duplicate tracking email request for order {order_id}, skipped"
            }, status=status.HTTP_200_OK)

        tracking_data = {
            'tracking_provider': data.get('tracking_provider', ''),
            'tracking_number': tracking_number,
            'date_shipped': data.get('date_shipped', ''),
            'custom_tracking_link': data.get('custom_tracking_link', ''),
            'tracking_link': data.get('tracking_link', ''),
            'products_list': data.get('products_list', []),
        }

        from .views_receipts import send_tracking_email
        sent = send_tracking_email(order_id, tracking_data)

        # Reconcile Woo order status (admin-added tracking never hit create_shipment_tracking).
        formatted_products = []
        for p in data.get('products_list') or []:
            if not isinstance(p, dict):
                continue
            formatted_products.append({
                'product': str(p.get('product') or p.get('product_id') or ''),
                'item_id': str(p.get('item_id') or ''),
                'qty': str(p.get('qty', 1)),
            })
        try:
            wc_status = WooCommerceAPI()
            wc_status.recalculate_shipment_order_status(
                int(order_id),
                tracking_data={'status_shipped': data.get('status_shipped', 'shipped')},
                tracking_number=tracking_number or '',
                formatted_products=formatted_products if formatted_products else None,
            )
        except Exception as recalc_err:
            logger.warning(f"Tracking webhook: could not recalculate order status for {order_id}: {recalc_err}")

        return Response({
            'success': True,
            'email_sent': sent,
            'message': f"Tracking email {'sent' if sent else 'failed'} for order {order_id}"
        }, status=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Error in tracking email webhook: {e}")
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
