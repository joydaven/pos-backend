from django.db.models import Q, Value, Case, When, IntegerField
from django.db.models.functions import Concat
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from rest_framework import viewsets, filters, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from .permissions import IsSuperUserOrReadOnly
from rest_framework.reverse import reverse
from .models import Contact, Order, Product, POSOrder, POSOrderItem, PaymentCard, ProductVariation, ProductBundle, ProductGrouped, ProductSimple, ProductSubscription, POSLocation, InventoryLocation
from .serializers import ContactSerializer, OrderSerializer, ProductSerializer, ProductWithSKUSerializer, POSOrderSerializer, POSOrderCreateSerializer, POSOrderItemSerializer, POSOrderItemCreateSerializer, PaymentCardSerializer, ProductVariationSerializer, ProductBundleSerializer, ProductGroupedSerializer, ProductSimpleSerializer, ProductSubscriptionSerializer, POSLocationSerializer
from .woocommerce import WooCommerceAPI
import logging
import json
import os
from oauth2_provider.contrib.rest_framework import TokenHasReadWriteScope, TokenHasScope
from .utils.encryption import encrypt_data
from .order_completion import validate_pos_order_can_complete, validate_woocommerce_order_can_complete
from datetime import datetime, timedelta
import json
import re
import time
import random

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_root(request, format=None):
    """
    API root view that provides links to all main endpoints
    """
    return Response({
        'contacts': reverse('contact-list', request=request, format=format),
        'orders': reverse('order-list', request=request, format=format),
        'products': reverse('product-list', request=request, format=format),
        'pos-orders': reverse('pos-order-list', request=request, format=format),
        'pos-customers': reverse('pos-customer-list', request=request, format=format),
        'all_customers': reverse('get_all_customers', request=request, format=format),
        'all_products': reverse('get_all_products', request=request, format=format),
        'sync': reverse('sync_woocommerce_data', request=request, format=format),
    })

sync_status = {
    'status': 'idle',
    'message': '',
    'progress': {'current': 0, 'total': 0, 'type': 'products'},
    'should_stop': False
}

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20  # Increased from 10 to 20 to match frontend expectations
    page_size_query_param = 'page_size'
    max_page_size = 100

class CustomPagination(StandardResultsSetPagination):
    pass

class ContactViewSet(viewsets.ModelViewSet):
    queryset = Contact.objects.all().order_by('last_name', 'first_name')
    serializer_class = ContactSerializer
    pagination_class = CustomPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['first_name', 'last_name', 'email', 'phone']
    ordering_fields = ['first_name', 'last_name', 'email']
    ordering = ['last_name', 'first_name']

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search', '')
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone__icontains=search)
            )
        return queryset

    def perform_update(self, serializer):
        """After saving contact edits, push changes to GHL and WooCommerce (POS → GHL/Woo sync)."""
        contact = serializer.save()
        try:
            from .ghl_api import push_contact_to_ghl
            result = push_contact_to_ghl(contact)
            if result.get('success'):
                logger.info(f"POS→GHL sync after edit: {contact.email} fields={result['updated_fields']}")
            elif result.get('error'):
                logger.warning(f"POS→GHL sync failed after edit: {contact.email} error={result['error']}")
        except Exception as e:
            logger.warning(f"POS→GHL sync error after edit for {contact.email}: {e}")
        try:
            from .woocommerce import push_contact_to_woo
            woo_result = push_contact_to_woo(contact)
            if woo_result.get('success'):
                logger.info(f"POS→Woo sync after edit: {contact.email} fields={woo_result['updated_fields']}")
            elif woo_result.get('error'):
                logger.warning(f"POS→Woo sync failed after edit: {contact.email} error={woo_result['error']}")
        except Exception as e:
            logger.warning(f"POS→Woo sync error after edit for {contact.email}: {e}")

    def destroy(self, request, *args, **kwargs):
        """Delete a customer from the local POS database only.
        
        Does NOT delete from GoHighLevel or WooCommerce — those platforms
        should be managed separately to avoid accidental cross-deletions.
        """
        contact = self.get_object()
        email = contact.email
        deletion_log = {
            'email': email,
            'local': {'success': False, 'error': None},
        }

        # Delete local Contact record only
        try:
            contact.delete()
            deletion_log['local']['success'] = True
            logger.info(f"Deleted local Contact record for {email}")
        except Exception as e:
            deletion_log['local']['error'] = str(e)
            logger.error(f"Error deleting local Contact record for {email}: {e}")
            return Response(
                {'detail': f'Failed to delete local record: {str(e)}', 'deletion_log': deletion_log},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'detail': f'Customer {email} deleted from POS successfully.',
            'deletion_log': deletion_log
        }, status=status.HTTP_200_OK)

class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all().order_by('-order_date')
    serializer_class = OrderSerializer
    pagination_class = CustomPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['woo_order_id', 'status', 'contact__first_name', 'contact__last_name', 'contact__email']
    ordering_fields = ['order_date', 'total_amount', 'status']
    ordering = ['-order_date']

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search', '')
        if search:
            queryset = queryset.filter(
                Q(woo_order_id__icontains=search) |
                Q(status__icontains=search) |
                Q(contact__first_name__icontains=search) |
                Q(contact__last_name__icontains=search) |
                Q(contact__email__icontains=search)
            )
        return queryset

class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.exclude(product_type='variation').exclude(status='draft').defer(
        'woo_data', 'short_description', 'purchase_note',
        'cross_sell_ids', 'upsell_ids', 'tags',
    ).prefetch_related(
        'simple_details',
        'variations',
        'bundles',
        'subscriptions',
        'inventory_locations__location',
        'inventory_locations__variation',
    ).order_by('name')  # Exclude variations from main product list
    serializer_class = ProductWithSKUSerializer  # Use the enhanced serializer with SKU support
    pagination_class = CustomPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description', 'categories']
    ordering_fields = ['name', 'price', 'stock_quantity']
    ordering = ['name']
    # More permissive permissions for development - accepts any authenticated user
    # In production, you might want to use more restrictive permissions
    permission_classes = [IsAuthenticated]
    # Removed TokenHasScope requirement to allow Google OAuth users to access products

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search', '')
        category = self.request.query_params.get('category', '')
        brand = self.request.query_params.get('brand', '')
        
        if search:
            import re
            # Cap search length to prevent expensive regex queries at the DB level
            search = search[:100]
            # Build a regex that matches the search term with optional
            # spaces, hyphens, or nothing between each character group.
            # e.g. "d3k2" -> matches "D3 K2", "D3-K2", "D3K2"
            # "5htp" -> matches "5-HTP", "5 HTP", "5HTP"
            # Split search into alphanumeric chunks
            chunks = re.findall(r'[a-zA-Z]+|\d+', search)[:10]
            
            if len(chunks) > 1:
                # Build regex: each chunk separated by optional [ \-]*
                fuzzy_pattern = r'[\s\-]*'.join(re.escape(c) for c in chunks)
                fuzzy_q = (
                    Q(name__iregex=fuzzy_pattern) |
                    Q(description__iregex=fuzzy_pattern) |
                    Q(categories__iregex=fuzzy_pattern)
                )
                # Also do standard icontains for the original search
                exact_q = (
                    Q(name__icontains=search) |
                    Q(description__icontains=search) |
                    Q(categories__icontains=search)
                )
                # Multi-word search: each word must appear somewhere
                words = search.split()
                word_q = queryset
                if len(words) > 1:
                    for word in words:
                        word_q = word_q.filter(
                            Q(name__icontains=word) |
                            Q(description__icontains=word) |
                            Q(categories__icontains=word)
                        )
                    queryset = (queryset.filter(fuzzy_q) | queryset.filter(exact_q) | word_q).distinct()
                else:
                    queryset = queryset.filter(fuzzy_q | exact_q).distinct()
            else:
                # Single chunk or single word — standard icontains
                queryset = queryset.filter(
                    Q(name__icontains=search) |
                    Q(description__icontains=search) |
                    Q(categories__icontains=search)
                )
        
        if category:
            queryset = queryset.filter(categories__icontains=category)
        
        if brand:
            # Filter by brand using the brand field from the serializer
            # We need to filter based on the woo_data JSON field in the appropriate tables
            from .models import ProductSimple, ProductVariation, ProductBundle
            
            # Get product IDs that have the specified brand in their woo_data
            brand_product_ids = []
            
            # Check ProductSimple - look for brand in options array
            simple_products = ProductSimple.objects.filter(
                Q(woo_data__attributes__contains=[{"name": "Brand", "options": [brand]}]) |
                Q(woo_data__attributes__contains=[{"slug": "pa_brand", "options": [brand]}])
            ).values_list('product_id', flat=True)
            brand_product_ids.extend(simple_products)
            
            # Check ProductVariation - look for brand in options array
            variation_products = ProductVariation.objects.filter(
                Q(woo_data__attributes__contains=[{"name": "Brand", "options": [brand]}]) |
                Q(woo_data__attributes__contains=[{"slug": "pa_brand", "options": [brand]}])
            ).values_list('product_id', flat=True)
            brand_product_ids.extend(variation_products)
            
            # Check ProductBundle - look for brand in options array
            bundle_products = ProductBundle.objects.filter(
                Q(woo_data__attributes__contains=[{"name": "Brand", "options": [brand]}]) |
                Q(woo_data__attributes__contains=[{"slug": "pa_brand", "options": [brand]}])
            ).values_list('product_id', flat=True)
            brand_product_ids.extend(bundle_products)
            
            # Filter the main queryset to only include products with the specified brand
            if brand_product_ids:
                queryset = queryset.filter(id__in=brand_product_ids)
            else:
                # If no products found with the brand, return empty queryset
                queryset = queryset.none()
        
        return queryset

class POSOrderViewSet(viewsets.ModelViewSet):
    """
    ViewSet for POS Orders with trash support
    By default shows only active (non-trashed) orders
    Use ?include_trashed=true to include trashed orders
    Use ?only_trashed=true to show only trashed orders
    """
    serializer_class = POSOrderSerializer
    pagination_class = CustomPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['order_number', 'status', 'contact__first_name', 'contact__last_name', 'contact__email']
    ordering_fields = ['created_at', 'total', 'status', 'trashed_at']
    ordering = ['-created_at']
    permission_classes = [IsAuthenticated]  # Allow unauthenticated access

    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return POSOrderCreateSerializer
        return POSOrderSerializer

    def get_queryset(self):
        # Handle trash filtering
        include_trashed = self.request.query_params.get('include_trashed', '').lower() == 'true'
        only_trashed = self.request.query_params.get('only_trashed', '').lower() == 'true'
        
        if only_trashed:
            queryset = POSOrder.objects.trashed().order_by('-created_at')
        elif include_trashed:
            queryset = POSOrder.objects.with_trash().order_by('-created_at')
        else:
            # Default: only active orders
            queryset = POSOrder.objects.active().order_by('-created_at')
        
        queryset = queryset.select_related('contact', 'card_details').prefetch_related('items')
        
        search = self.request.query_params.get('search', '')
        if search:
            # Filter orders that match the search term
            queryset = queryset.filter(
                Q(order_number__icontains=search) |
                Q(status__icontains=search) |
                Q(contact__first_name__icontains=search) |
                Q(contact__last_name__icontains=search) |
                Q(contact__email__icontains=search)
            )
            
            # Add search priority ordering: Customer info first, then order number
            queryset = queryset.annotate(
                search_priority=Case(
                    # Priority 1: Customer email exact match
                    When(contact__email__iexact=search, then=Value(1)),
                    # Priority 2: Customer email contains search term
                    When(contact__email__icontains=search, then=Value(2)),
                    # Priority 3: Customer first name exact match
                    When(contact__first_name__iexact=search, then=Value(3)),
                    # Priority 4: Customer last name exact match  
                    When(contact__last_name__iexact=search, then=Value(4)),
                    # Priority 5: Customer first name contains search term
                    When(contact__first_name__icontains=search, then=Value(5)),
                    # Priority 6: Customer last name contains search term
                    When(contact__last_name__icontains=search, then=Value(6)),
                    # Priority 7: Order number exact match
                    When(order_number__iexact=search, then=Value(7)),
                    # Priority 8: Order number contains search term
                    When(order_number__icontains=search, then=Value(8)),
                    # Priority 9: Status matches
                    When(status__icontains=search, then=Value(9)),
                    default=Value(10),
                    output_field=IntegerField()
                )
            ).order_by('search_priority', '-created_at')
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)
            
        # Filter by status
        status = self.request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)
            
        # Filter by contact
        contact = self.request.query_params.get('contact')
        if contact:
            queryset = queryset.filter(contact=contact)
            
        return queryset
    
    def list(self, request, *args, **kwargs):
        """Override list — bundle_products reconstruction is handled by POSOrderSerializer.to_representation()"""
        return super().list(request, *args, **kwargs)
    
    def _reconstruct_bundle_products_from_items(self, items):
        """Reconstruct bundle products from individual bundle items"""
        import json
        
        if not items:
            return []
        
        bundle_groups = {}
        non_bundle_items = []
        
        for item in items:
            try:
                # Parse metadata
                metadata = item.get('metadata', '{}')
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                
                # Check if this is a bundle item
                if metadata.get('is_bundle_item') and metadata.get('bundle_parent_id'):
                    bundle_parent_id = metadata['bundle_parent_id']
                    
                    if bundle_parent_id not in bundle_groups:
                        bundle_groups[bundle_parent_id] = {
                            'items': [],
                            'bundle_name': metadata.get('bundle_parent_name', 'Bundle Product'),
                            'bundle_price': metadata.get('original_bundle_price', 0)
                        }
                    
                    bundle_groups[bundle_parent_id]['items'].append(item)
                else:
                    non_bundle_items.append(item)
            except (json.JSONDecodeError, KeyError, TypeError):
                # If metadata parsing fails, treat as non-bundle item
                non_bundle_items.append(item)
        
        # Create bundle display items
        result = []
        
        # Add non-bundle items first
        result.extend(non_bundle_items)
        
        # Add reconstructed bundle items
        for bundle_parent_id, bundle_data in bundle_groups.items():
            bundle_item = {
                'id': f'bundle-{bundle_parent_id}',
                'name': f"{bundle_data['bundle_name']} (Bundle)",
                'quantity': 1,
                'price': str(bundle_data['bundle_price']),
                'subtotal': str(bundle_data['bundle_price']),
                'metadata': json.dumps({
                    'is_original_bundle': True,
                    'bundle_parent_id': bundle_parent_id,
                    'bundle_parent_name': bundle_data['bundle_name'],
                    'bundle_item_count': len(bundle_data['items']),
                    'original_bundle_price': bundle_data['bundle_price']
                })
            }
            result.append(bundle_item)
        
        return result if bundle_groups else []

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """
        Get statistics about orders
        """
        total_orders = POSOrder.objects.count()
        pending_orders = POSOrder.objects.filter(status='pending').count()
        processing_orders = POSOrder.objects.filter(status='processing').count()
        completed_orders = POSOrder.objects.filter(status='completed').count()
        cancelled_orders = POSOrder.objects.filter(status='cancelled').count()
        
        return Response({
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'processing_orders': processing_orders,
            'completed_orders': completed_orders,
            'cancelled_orders': cancelled_orders
        })

    @action(detail=True, methods=['post'])
    def save_card(self, request, pk=None):
        """
        Save encrypted card information for an order
        """
        order = self.get_object()
        
        # Check if card data is provided
        card_number = request.data.get('card_number')
        expiry_date = request.data.get('expiry_date')
        
        if not card_number or not expiry_date:
            return Response(
                {"error": "Card number and expiry date are required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get additional card data
        card_brand = request.data.get('card_brand', 'Credit Card')
        cardholder_name = request.data.get('cardholder_name', '')
        
        # Get the last 4 digits
        last4 = card_number[-4:]
        
        try:
            # Check if card already exists for this order
            from .utils.encryption import encrypt_data
            
            try:
                card = PaymentCard.objects.get(order=order)
                # Update existing card
                card.encrypted_card_number = encrypt_data(card_number)
                card.encrypted_expiry_date = encrypt_data(expiry_date)
                card.last4 = last4
                card.card_brand = card_brand
                card.cardholder_name = cardholder_name
                card.save()
                created = False
            except PaymentCard.DoesNotExist:
                # Create new card
                card = PaymentCard.objects.create(
                    order=order,
                    encrypted_card_number=encrypt_data(card_number),
                    encrypted_expiry_date=encrypt_data(expiry_date),
                    last4=last4,
                    card_brand=card_brand,
                    cardholder_name=cardholder_name
                )
                created = True
            
            return Response(
                {"message": "Card information saved successfully", "last4": last4},
                status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to save card information: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """
        Update the status of a POS order and optionally sync with WooCommerce
        """
        order = self.get_object()
        new_status = request.data.get('status')
        sync_woocommerce = request.data.get('sync_woocommerce', True)
        
        # Validate the new status
        valid_statuses = [choice[0] for choice in POSOrder.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {"error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if new_status == 'completed':
            completion_check = validate_pos_order_can_complete(order)
            if not completion_check.get('eligible'):
                return Response(
                    {
                        "error": completion_check.get("message"),
                        "code": completion_check.get("code"),
                        "completion_validation": completion_check,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
        
        # Update the order status
        old_status = order.status
        order.status = new_status
        
        # Set completed_at timestamp if marking as completed
        if new_status == 'completed' and old_status != 'completed':
            from django.utils import timezone
            order.completed_at = timezone.now()
        
        order.save()
        
        # Sync with WooCommerce if requested and order has WooCommerce ID
        woo_sync_result = None
        if sync_woocommerce:
            # Check if order has WooCommerce ID in metadata
            woo_order_id = None
            if hasattr(order, 'metadata') and order.metadata:
                woo_order_id = order.metadata.get('woo_order_id')
            
            if woo_order_id:
                try:
                    from .woocommerce import WooCommerceAPI
                    wc_api = WooCommerceAPI()
                    
                    # Map POS status to WooCommerce status
                    woo_status_map = {
                        'pending': 'pending',
                        'processing': 'processing',
                        'completed': 'completed',
                        'cancelled': 'cancelled',
                        'refunded': 'refunded'
                    }
                    
                    woo_status = woo_status_map.get(new_status, new_status)
                    woo_sync_result = wc_api.update_order_status(woo_order_id, woo_status)
                    
                    logger.info(f"Updated WooCommerce order {woo_order_id} status to {woo_status}")
                    
                except Exception as e:
                    logger.error(f"Failed to sync status with WooCommerce: {str(e)}")
                    woo_sync_result = {"status": "error", "message": str(e)}
        
        from users.activity_log import log_activity
        log_activity(request, f"Updated POS Order {order.order_number} status: {old_status} → {new_status}", category='pos_order', details={
            'order_number': order.order_number,
            'order_id': str(order.id),
            'old_status': old_status,
            'new_status': new_status,
        })
        
        # Return updated order data
        serializer = self.get_serializer(order)
        response_data = {
            "order": serializer.data,
            "old_status": old_status,
            "new_status": new_status,
            "woocommerce_sync": woo_sync_result
        }
        
        return Response(response_data, status=status.HTTP_200_OK)

    def create(self, request, *args, **kwargs):
        """Override create to ensure shipping_info is never null and award fractional points"""
        from decimal import Decimal
        from crm.models import CustomerPointsAccount, PointsTransaction
        from crm.woocommerce import WooCommerceAPI
        from django.utils import timezone
        from django.db import transaction as db_transaction
        from users.activity_log import log_activity
        
        # Check if shipping is enabled in settings
        from django.conf import settings
        shipping_enabled = getattr(settings, 'POS_ENABLE_SHIPPING', False)
        
        # If shipping is not enabled, ensure shipping_info is an empty dict, not null
        if not shipping_enabled and ('shipping_info' not in request.data or request.data['shipping_info'] is None):
            request.data['shipping_info'] = {}
        
        # Create the order atomically to prevent partial writes
        with db_transaction.atomic():
            response = super().create(request, *args, **kwargs)
        
        # Award fractional points after successful order creation
        try:
            if response.status_code == 201:  # Order created successfully
                order_data = response.data
                customer_id = order_data.get('contact')
                order_total = Decimal(str(order_data.get('total', 0)))
                order_number = order_data.get('order_number')
                
                log_activity(request, f"Created POS Order {order_number}", category='pos_order', details={
                    'order_number': order_number,
                    'total': str(order_total),
                    'customer_id': str(customer_id) if customer_id else None,
                    'item_count': len(order_data.get('items', [])),
                })
                
                # NOTE: Points earning is handled automatically by WooCommerce/YITH
                # YITH Points & Rewards plugin awards points based on order total (configured in WP admin)
                # POS does NOT manually award points to avoid double-awarding
                # Only points REDEMPTIONS are handled by POS fractional points system
                if customer_id and order_total > 0:
                    logger.info(f"Order {order_number} completed with total ${order_total}. Points will be awarded automatically by WooCommerce/YITH.")
                        
        except Exception as e:
            logger.error(f"Error in order completion process: {str(e)}")
            
        return response
    
    @action(detail=True, methods=['post'], url_path='trash')
    def trash_order(self, request, pk=None):
        """Move an order to trash"""
        try:
            order = self.get_object()
            
            if not order.can_be_trashed():
                return Response(
                    {'error': 'Order cannot be trashed', 'status': order.status}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the requesting user (if authenticated)
            user = request.user if request.user.is_authenticated else None
            order.trash(user=user)
            
            from users.activity_log import log_activity
            log_activity(request, f"Trashed POS Order {order.order_number}", category='pos_order', details={
                'order_number': order.order_number,
                'order_id': str(order.id),
                'total': str(order.total),
            })
            
            return Response({
                'message': 'Order moved to trash successfully',
                'order_id': str(order.id),
                'trashed_at': order.trashed_at.isoformat() if order.trashed_at else None
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to trash order: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='restore')
    def restore_order(self, request, pk=None):
        """Restore an order from trash"""
        try:
            order = self.get_object()
            
            if not order.is_trashed():
                return Response(
                    {'error': 'Order is not in trash', 'status': order.status}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get the new status from request data, default to 'processing'
            new_status = request.data.get('status', 'processing')
            if new_status not in ['pending', 'processing', 'completed', 'cancelled', 'refunded']:
                new_status = 'processing'
            
            order.restore(new_status=new_status)
            
            from users.activity_log import log_activity
            log_activity(request, f"Restored POS Order {order.order_number}", category='pos_order', details={
                'order_number': order.order_number,
                'order_id': str(order.id),
                'new_status': order.status,
            })
            
            return Response({
                'message': 'Order restored from trash successfully',
                'order_id': str(order.id),
                'new_status': order.status
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to restore order: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='trash-stats')
    def trash_stats(self, request):
        """Get statistics about trashed orders"""
        try:
            from django.db.models import Count, Sum
            from django.utils import timezone
            from datetime import timedelta
            
            # Get counts
            total_active = POSOrder.objects.active().count()
            total_trashed = POSOrder.objects.trashed().count()
            
            # Get trashed orders by status before trashing
            trashed_by_status = POSOrder.objects.trashed().values('status').annotate(
                count=Count('id')
            ).order_by('-count')
            
            # Get recent trash activity (last 30 days)
            thirty_days_ago = timezone.now() - timedelta(days=30)
            recent_trashed = POSOrder.objects.trashed().filter(
                trashed_at__gte=thirty_days_ago
            ).count()
            
            # Calculate total value of trashed orders
            trashed_value = POSOrder.objects.trashed().aggregate(
                total_value=Sum('total')
            )['total_value'] or 0
            
            return Response({
                'active_orders': total_active,
                'trashed_orders': total_trashed,
                'recently_trashed': recent_trashed,
                'trashed_value': float(trashed_value),
                'trashed_by_status': list(trashed_by_status)
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to get trash stats: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='(?P<order_number>[^/.]+)/send-receipt')
    def send_receipt(self, request, order_number=None):
        """Send receipt email for a POS order (also supports WooCommerce order ID lookup)"""
        try:
            from django.core.mail import EmailMultiAlternatives
            
            # ── Server-side dedup: block duplicate receipt emails (5 min window) ──
            from django.core.cache import cache
            dedup_key = f"receipt_sent:{order_number}"
            if cache.get(dedup_key):
                logger.info(f"📧 Dedup: receipt already sent for {order_number}, returning OK without re-sending")
                return Response({'status': 'ok', 'dedup': True}, status=status.HTTP_200_OK)
            cache.set(dedup_key, True, 300)  # 5 min window
            
            # Get the order by order number, or by WooCommerce order ID in metadata
            order = None
            try:
                order = POSOrder.objects.get(order_number=order_number)
            except POSOrder.DoesNotExist:
                # Try lookup by woo_order_id (for subscription Email Receipt which passes WC order ID)
                order = POSOrder.objects.filter(metadata__woo_order_id=order_number).first()
                if not order and order_number.isdigit():
                    order = POSOrder.objects.filter(metadata__woo_order_id=int(order_number)).first()
            
            if not order:
                # No POS order found — try sending directly via WooCommerce order data
                email = request.data.get('email')
                if not email:
                    return Response({'error': 'Email address is required'}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    from .views_receipts import _build_order_receipt_html, _build_pdf_receipt_html, _html_to_pdf, _enrich_order_subscription_frequency
                    from .email_sender import send_pos_email
                    from .woocommerce import WooCommerceAPI
                    wc = WooCommerceAPI()
                    woo_response = wc.wcapi.get(f'orders/{order_number}')
                    if woo_response.status_code == 200:
                        order_data = woo_response.json()
                        _enrich_order_subscription_frequency(order_data)
                        woo_number = order_data.get('number', order_number)
                        html_content = _build_order_receipt_html(order_data)
                        text_content = f'Your Doctors Studio Receipt — Order #{woo_number}'
                        pdf_html = _build_pdf_receipt_html(order_data, is_refund=False)
                        pdf_bytes = _html_to_pdf(pdf_html)
                        subject = f'Your Doctors Studio Receipt — Order #{woo_number}'
                        attachments = [(f'Receipt_Order_{woo_number}.pdf', pdf_bytes, 'application/pdf')] if pdf_bytes else None
                        send_pos_email(
                            subject=subject,
                            text_content=text_content,
                            html_content=html_content,
                            to_email=email,
                            email_type='order_receipt',
                            attachments=attachments,
                            related_order=str(woo_number),
                        )
                        logger.info(f"📧 ✅ Sent receipt via WC order #{woo_number} to {email}")
                        # Set dedup cache so webhook path won't double-send
                        try:
                            from django.core.cache import cache as _cache
                            _cache.set(f"receipt_email_sent_{order_number}", True, 600)
                        except Exception:
                            pass
                        return Response({'status': 'success', 'message': f'Receipt sent to {email}'})
                except Exception as wc_err:
                    logger.warning(f"📧 WC direct send failed for {order_number}: {wc_err}")
                return Response(
                    {'error': f'Order {order_number} not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get email from request
            email = request.data.get('email')
            logger.info(f"📧 Receipt Email Request - Order: {order_number}, Email: {email}")
            
            if not email:
                logger.error(f"📧 No email provided for order {order_number}")
                return Response(
                    {'error': 'Email address is required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Use the modern teal-branded receipt template via WooCommerce order data
            from .views_receipts import _build_order_receipt_html, _build_pdf_receipt_html, _html_to_pdf, _enrich_order_subscription_frequency
            from .woocommerce import WooCommerceAPI
            
            woo_order_id = order.metadata.get('woo_order_id') if order.metadata else None
            
            if not woo_order_id:
                logger.error(f"📧 No WooCommerce order ID found for POS order {order_number}")
                return Response(
                    {'error': f'No WooCommerce order linked to {order_number}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            wc = WooCommerceAPI()
            woo_response = wc.wcapi.get(f'orders/{woo_order_id}')
            
            if woo_response.status_code != 200:
                logger.error(f"📧 Failed to fetch WooCommerce order {woo_order_id}: {woo_response.status_code}")
                return Response(
                    {'error': f'Could not fetch WooCommerce order {woo_order_id}'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            order_data = woo_response.json()
            _enrich_order_subscription_frequency(order_data)
            woo_number = order_data.get('number', order_number)
            
            html_content = _build_order_receipt_html(order_data)
            text_content = f'Your Doctors Studio Receipt — Order #{woo_number}'
            
            pdf_html = _build_pdf_receipt_html(order_data, is_refund=False)
            pdf_bytes = _html_to_pdf(pdf_html)
            if not pdf_bytes:
                logger.warning(f"📧 PDF generation failed for POS order #{woo_number}, sending without attachment")
            
            subject = f'Your Doctors Studio Receipt — Order #{woo_number}'
            attachments = [(f'Receipt_Order_{woo_number}.pdf', pdf_bytes, 'application/pdf')] if pdf_bytes else None
            
            from .email_sender import send_pos_email
            sent = send_pos_email(
                subject=subject,
                text_content=text_content,
                html_content=html_content,
                to_email=email,
                email_type='order_receipt',
                attachments=attachments,
                related_order=str(woo_number),
            )
            
            if not sent:
                raise Exception("All email backends failed")
            
            # Set dedup cache so webhook path won't double-send for same WC order
            try:
                from django.core.cache import cache
                woo_id = order.metadata.get('woo_order_id') if order.metadata else None
                if woo_id:
                    cache.set(f"receipt_email_sent_{woo_id}", True, 600)
            except Exception:
                pass
            
            return Response({'status': 'ok'}, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Failed to send receipt email for order {order_number}: {str(e)}")
            return Response(
                {'error': f'Failed to send receipt email: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='(?P<order_number>[^/.]+)/send-refund-receipt')
    def send_refund_receipt(self, request, order_number=None):
        """Send refund receipt email for a POS order with refunded items"""
        try:
            from django.core.mail import EmailMultiAlternatives
            from django.template.loader import render_to_string
            from django.utils.html import strip_tags
            from datetime import timedelta
            
            # Get the order by order number
            try:
                order = POSOrder.objects.get(order_number=order_number)
            except POSOrder.DoesNotExist:
                return Response(
                    {'error': f'Order {order_number} not found'}, 
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get email from request
            email = request.data.get('email')
            logger.info(f"📧 Refund Receipt Email Request - Order: {order_number}, Email: {email}")
            
            if not email:
                logger.error(f"📧 No email provided for refund receipt order {order_number}")
                return Response(
                    {'error': 'Email address is required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get refunded items
            refunded_items = []
            total_refund_amount = 0
            
            for item in order.items.all():
                # Check for refund info in POSOrderRefundItem
                from .models import POSOrderRefundItem
                refund_records = POSOrderRefundItem.objects.filter(order_item=item)
                
                if refund_records.exists():
                    for refund in refund_records:
                        refunded_items.append({
                            'name': item.name,
                            'refund_qty': refund.refund_qty,
                            'refund_total': float(refund.refund_total),
                            'refund_reason': refund.refund_reason or '',
                            'refund_date': refund.refund_date
                        })
                        total_refund_amount += float(refund.refund_total)
            
            if not refunded_items:
                return Response(
                    {'error': 'No refunded items found in this order'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Refund destination: Points, Credited Services, or actual payment method
            from .models import POSOrderRefund
            latest_refund = POSOrderRefund.objects.filter(order=order).order_by('-created_at').first()
            if latest_refund and latest_refund.metadata and latest_refund.metadata.get('refund_as_points'):
                payment_method_display = 'Points'
            elif order.total is not None and float(order.total) == 0 and order.items.exists() and all(
                (item.metadata if isinstance(item.metadata, dict) else {}).get('isCreditedService', False) for item in order.items.all()
            ):
                payment_method_display = 'Credited Services'
            else:
                # Use actual payment method from order metadata
                from .views_refunds import _get_refund_method_display
                pm_info = None
                if order.metadata and order.metadata.get('_pos_payment_method'):
                    raw = order.metadata['_pos_payment_method']
                    if isinstance(raw, str):
                        try:
                            import json
                            pm_info = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            pass
                    elif isinstance(raw, dict):
                        pm_info = raw
                payment_method_display = _get_refund_method_display(pm_info) if pm_info else 'Cash'
            
            # Calculate expected refund date (5 business days)
            from django.utils import timezone
            expected_date = timezone.now()
            business_days = 5
            while business_days > 0:
                expected_date += timedelta(days=1)
                if expected_date.weekday() < 5:  # Monday = 0, Friday = 4
                    business_days -= 1
            
            # Convert timestamps
            local_timestamp = timezone.localtime(order.created_at)
            
            # Prepare context for email template
            context = {
                'order_number': order.order_number,
                'original_order_date': local_timestamp.strftime('%b %d, %Y'),
                'refund_date': timezone.now().strftime('%b %d, %Y, %I:%M %p'),
                'customer': order.contact,
                'refunded_items': refunded_items,
                'total_refund_amount': total_refund_amount,
                'payment_method_display': payment_method_display,
                'expected_refund_date': expected_date.strftime('%b %d, %Y'),
            }
            # Render email template
            html_content = render_to_string('emails/refund_receipt.html', context)
            text_content = strip_tags(html_content)
            
            # Generate PDF attachment using dedicated refund receipt PDF builder
            from .views_receipts import _html_to_pdf, _build_pdf_refund_receipt_html
            # Build order_data dict matching the WooCommerce shape for the PDF builder
            contact = order.contact
            pdf_order_data = {
                'number': order.order_number,
                'id': str(order.id),
                'date_created': order.created_at.isoformat() if order.created_at else '',
                'status': order.status or '',
                'total': str(order.total or '0.00'),
                'billing': {
                    'first_name': contact.first_name if contact else '',
                    'last_name': contact.last_name if contact else '',
                    'email': email,
                },
                'line_items': [
                    {
                        'name': ri['name'],
                        'quantity': ri.get('refund_qty', 1),
                        'sku': '',
                        'total': str(ri['refund_total']),
                        'meta_data': [],
                    }
                    for ri in refunded_items
                ],
                'refunds': [
                    {
                        'total': str(-ri['refund_total']),
                        'reason': ri.get('refund_reason', ''),
                    }
                    for ri in refunded_items
                ],
                'meta_data': [
                    {'key': '_pos_payment_method', 'value': order.payment_method or {}},
                ],
            }
            pdf_html = _build_pdf_refund_receipt_html(pdf_order_data)
            pdf_bytes = _html_to_pdf(pdf_html)
            if not pdf_bytes:
                logger.warning(f"\ud83d\udce7 PDF generation failed for POS refund receipt order #{order_number}, sending without attachment")
            
            # Create email — use WooCommerce order number for consistent subject
            woo_order_id = order.metadata.get('woo_order_id') if order.metadata else None
            display_number = order_number
            if woo_order_id:
                try:
                    from .woocommerce import WooCommerceAPI
                    wc = WooCommerceAPI()
                    woo_resp = wc.wcapi.get(f'orders/{woo_order_id}')
                    if woo_resp.status_code == 200:
                        display_number = woo_resp.json().get('number', order_number)
                except Exception:
                    pass
            subject = f'Your Doctors Studio Refund Receipt — Order #{display_number}'
            attachments = [(f'Refund_Receipt_Order_{display_number}.pdf', pdf_bytes, 'application/pdf')] if pdf_bytes else None
            
            from .email_sender import send_pos_email
            sent = send_pos_email(
                subject=subject,
                text_content=text_content,
                html_content=html_content,
                to_email=email,
                email_type='refund_receipt',
                attachments=attachments,
                related_order=str(display_number),
            )
            
            if not sent:
                raise Exception("All email backends failed")
            
            return Response({
                'status': 'ok',
                'refunded_items_count': len(refunded_items),
                'total_refund_amount': total_refund_amount
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Failed to send refund receipt email for order {order_number}: {str(e)}")
            return Response(
                {'error': f'Failed to send refund receipt email: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class POSCustomerViewSet(viewsets.ModelViewSet):
    """
    ViewSet for accessing customer data from the POS system
    Supports full CRUD operations for customer management
    """
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer
    permission_classes = [IsAuthenticated]  # Allow unauthenticated access for POS system
    pagination_class = StandardResultsSetPagination  # Use custom pagination for larger page sizes
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['first_name', 'last_name', 'email', 'phone']
    ordering_fields = ['first_name', 'last_name', 'email', 'created_at', 'updated_at']
    ordering = []  # Remove default ordering to allow custom search ordering
    
    def get_serializer_class(self):
        """
        Return the appropriate serializer class based on the action and query parameters.
        """
        # Check for points data request (only for retrieve action)
        if self.action == 'retrieve' and self.request.query_params.get('include_points', 'false').lower() == 'true':
            from .serializers import ContactWithPointsSerializer
            return ContactWithPointsSerializer
        
        # Check for latest order request (for both list and retrieve actions)
        if self.request.query_params.get('include_latest_order', 'false').lower() == 'true':
            from .serializers import ContactWithLatestOrderSerializer
            return ContactWithLatestOrderSerializer
        
        # Default behavior - use ContactWithLatestOrderSerializer by default for list view
        if self.action == 'list':
            from .serializers import ContactWithLatestOrderSerializer
            return ContactWithLatestOrderSerializer
        
        return super().get_serializer_class()

    def perform_update(self, serializer):
        """After saving contact edits, push changes to GHL and WooCommerce (POS → GHL/Woo sync)."""
        old_ghl_id = serializer.instance.ghl_contact_id if serializer.instance else None
        contact = serializer.save()
        
        # Cascade ghl_contact_id changes to CreditServicePoints records
        new_ghl_id = contact.ghl_contact_id
        if new_ghl_id and old_ghl_id != new_ghl_id:
            try:
                from .models import CreditServicePoints
                updated = CreditServicePoints.objects.filter(customer=contact).exclude(
                    contact_ghl_id=new_ghl_id
                ).update(contact_ghl_id=new_ghl_id)
                if updated:
                    logger.info(f"🔄 Cascaded GHL ID update to {updated} CreditServicePoints record(s) for {contact.email}: {old_ghl_id} → {new_ghl_id}")
            except Exception as e:
                logger.warning(f"Failed to cascade GHL ID to CreditServicePoints for {contact.email}: {e}")
        
        try:
            from .ghl_api import push_contact_to_ghl
            result = push_contact_to_ghl(contact)
            if result.get('success'):
                logger.info(f"POS→GHL sync (POSCustomer) after edit: {contact.email} fields={result['updated_fields']}")
            elif result.get('error'):
                logger.warning(f"POS→GHL sync (POSCustomer) failed after edit: {contact.email} error={result['error']}")
        except Exception as e:
            logger.warning(f"POS→GHL sync (POSCustomer) error after edit for {contact.email}: {e}")
        try:
            from .woocommerce import push_contact_to_woo
            woo_result = push_contact_to_woo(contact)
            if woo_result.get('success'):
                logger.info(f"POS→Woo sync (POSCustomer) after edit: {contact.email} fields={woo_result['updated_fields']}")
            elif woo_result.get('error'):
                logger.warning(f"POS→Woo sync (POSCustomer) failed after edit: {contact.email} error={woo_result['error']}")
        except Exception as e:
            logger.warning(f"POS→Woo sync (POSCustomer) error after edit for {contact.email}: {e}")

    def destroy(self, request, *args, **kwargs):
        """Delete a customer from POS, GHL, and WooCommerce."""
        contact = self.get_object()
        email = contact.email
        deletion_log = {
            'email': email,
            'ghl': {'attempted': False, 'success': False, 'error': None},
            'woocommerce': {'attempted': False, 'success': False, 'error': None},
            'local': {'success': False, 'error': None},
        }

        # 1. Delete from GoHighLevel
        if contact.ghl_contact_id:
            deletion_log['ghl']['attempted'] = True
            try:
                from .ghl_api import delete_ghl_contact
                result = delete_ghl_contact(contact.ghl_contact_id)
                deletion_log['ghl']['success'] = result.get('success', False)
                deletion_log['ghl']['error'] = result.get('error')
                if result.get('success'):
                    logger.info(f"Deleted GHL contact for {email} (GHL ID: {contact.ghl_contact_id})")
                else:
                    logger.warning(f"Failed to delete GHL contact for {email}: {result.get('error')}")
            except Exception as e:
                deletion_log['ghl']['error'] = str(e)
                logger.error(f"Error deleting GHL contact for {email}: {e}")

        # 2. Delete from WooCommerce
        if contact.woo_customer_id:
            deletion_log['woocommerce']['attempted'] = True
            try:
                wc_api = WooCommerceAPI()
                response = wc_api.wcapi.delete(
                    f"customers/{contact.woo_customer_id}",
                    params={"force": True, "reassign": 0}
                )
                if response.status_code in [200, 204]:
                    deletion_log['woocommerce']['success'] = True
                    logger.info(f"Deleted WooCommerce customer for {email} (Woo ID: {contact.woo_customer_id})")
                else:
                    deletion_log['woocommerce']['error'] = f"HTTP {response.status_code}: {response.text[:300]}"
                    logger.warning(f"Failed to delete WooCommerce customer for {email}: {deletion_log['woocommerce']['error']}")
            except Exception as e:
                deletion_log['woocommerce']['error'] = str(e)
                logger.error(f"Error deleting WooCommerce customer for {email}: {e}")

        # 3. Delete local Contact record (always proceed even if external deletes failed)
        try:
            contact.delete()
            deletion_log['local']['success'] = True
            logger.info(f"Deleted local Contact record for {email}")
        except Exception as e:
            deletion_log['local']['error'] = str(e)
            logger.error(f"Error deleting local Contact record for {email}: {e}")
            return Response(
                {'detail': f'Failed to delete local record: {str(e)}', 'deletion_log': deletion_log},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({
            'detail': f'Customer {email} deleted successfully.',
            'deletion_log': deletion_log
        }, status=status.HTTP_200_OK)

    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a customer and optionally include points data
        """
        instance = self.get_object()
        include_points = request.query_params.get('include_points', 'false').lower() == 'true'
        
        # If points data is requested and customer has a WooCommerce ID
        if include_points and instance.woo_customer_id:
            try:
                # Get points data from WooCommerce
                from .woocommerce import WooCommerceAPI
                wc_api = WooCommerceAPI()
                points_data = wc_api.get_customer_points(instance.woo_customer_id)
                
                # If points data is available, include it in the serializer context
                if points_data:
                    serializer = self.get_serializer(instance, context={'points_data': points_data})
                    return Response(serializer.data)
            except Exception as e:
                # Log the error but continue without points data
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error fetching points data for customer {instance.id}: {str(e)}")
        
        # Default behavior if points are not requested or not available
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
    
    def get_queryset(self):
        from django.db.models import Case, When, IntegerField
        from django.db.models import Prefetch, Q as DQ
        import logging
        
        logger = logging.getLogger(__name__)
        
        # Prefetch latest POS order and latest WooCommerce order per contact
        # to avoid N+1 queries in ContactWithLatestOrderSerializer
        from .models import POSOrder, Order as WooOrder
        queryset = Contact.objects.prefetch_related(
            Prefetch(
                'pos_orders',
                queryset=POSOrder.objects.exclude(status='trash').order_by('-created_at')[:1],
                to_attr='_prefetched_latest_pos_order',
            ),
            Prefetch(
                'orders',
                queryset=WooOrder.objects.order_by('-order_date')[:1],
                to_attr='_prefetched_latest_woo_order',
            ),
        )
        
        # Filter by search term
        search = self.request.query_params.get('search', None)
        if search:
            logger.info(f"POSCustomerViewSet search query: '{search}'")
            search_term = search.strip()
            
            # Detect if this is an email search (contains @)
            is_email_search = '@' in search_term
            
            # Enhanced search that handles full names and partial matches
            search_terms = search_term.split()
            
            # Create base filter conditions
            if len(search_terms) == 1:
                term = search_terms[0]
                
                # Create search filters
                name_filters = (
                    Q(first_name__icontains=term) |
                    Q(last_name__icontains=term)
                )
                
                email_filters = Q(email__icontains=term)
                phone_filters = Q(phone__icontains=term)
                
                # Apply filters
                queryset = queryset.filter(
                    name_filters | email_filters | phone_filters
                ).annotate(
                    full_name=Concat('first_name', Value(' '), 'last_name')
                ).filter(
                    Q(first_name__icontains=term) |
                    Q(last_name__icontains=term) |
                    Q(full_name__icontains=term) |
                    Q(email__icontains=term) |
                    Q(phone__icontains=term)
                )
                
            else:
                # Multiple terms - likely first and last name
                first_term = search_terms[0]
                last_term = search_terms[-1]
                
                # Create search filters
                name_filters = (
                    (Q(first_name__icontains=first_term) & Q(last_name__icontains=last_term)) |
                    (Q(first_name__icontains=last_term) & Q(last_name__icontains=first_term)) |
                    Q(first_name__icontains=search_term) |
                    Q(last_name__icontains=search_term)
                )
                
                email_filters = Q(email__icontains=search_term)
                phone_filters = Q(phone__icontains=search_term)
                
                queryset = queryset.filter(
                    name_filters | email_filters | phone_filters
                ).annotate(
                    full_name=Concat('first_name', Value(' '), 'last_name')
                ).filter(
                    Q(first_name__icontains=first_term) & Q(last_name__icontains=last_term) |
                    Q(first_name__icontains=last_term) & Q(last_name__icontains=first_term) |
                    Q(first_name__icontains=search_term) |
                    Q(last_name__icontains=search_term) |
                    Q(full_name__icontains=search_term) |
                    Q(email__icontains=search_term) |
                    Q(phone__icontains=search_term)
                )
            
            # Smart ordering: prioritize name matches unless it's clearly an email search
            if is_email_search:
                logger.info(f"Email search detected for: '{search_term}'")
                # For email searches (contains @), prioritize email matches
                queryset = queryset.annotate(
                    search_priority=Case(
                        When(email__icontains=search_term, then=1),  # Email matches first
                        When(Q(first_name__icontains=search_term) | Q(last_name__icontains=search_term), then=2),  # Name matches second
                        When(phone__icontains=search_term, then=3),  # Phone matches last
                        default=4,
                        output_field=IntegerField()
                    )
                ).order_by('search_priority', 'last_name', 'first_name')
            else:
                logger.info(f"Name search detected for: '{search_term}'")
                # For name searches (no @), prioritize name matches
                if len(search_terms) == 1:
                    term = search_terms[0]
                    logger.info(f"Single term search: '{term}'")
                    queryset = queryset.annotate(
                        search_priority=Case(
                            # Exact name matches first (only if name fields are not empty)
                            When(Q(first_name__iexact=term, first_name__isnull=False, first_name__gt='') | 
                                 Q(last_name__iexact=term, last_name__isnull=False, last_name__gt=''), then=1),
                            # Name starts with term (only if name fields are not empty)
                            When(Q(first_name__istartswith=term, first_name__isnull=False, first_name__gt='') | 
                                 Q(last_name__istartswith=term, last_name__isnull=False, last_name__gt=''), then=2),
                            # Name contains term (only if name fields are not empty)
                            When(Q(first_name__icontains=term, first_name__isnull=False, first_name__gt='') | 
                                 Q(last_name__icontains=term, last_name__isnull=False, last_name__gt=''), then=3),
                            # Email contains term
                            When(email__icontains=term, then=4),
                            # Phone contains term
                            When(phone__icontains=term, then=5),
                            default=6,
                            output_field=IntegerField()
                        )
                    ).order_by('search_priority', 'last_name', 'first_name')
                    
                    # Log the first few results to debug ordering
                    results = list(queryset[:10].values('id', 'first_name', 'last_name', 'email', 'search_priority'))
                    logger.info(f"DEBUG First 10 search results for '{term}':")
                    for i, result in enumerate(results):
                        name = f"{result['first_name'] or ''} {result['last_name'] or ''}".strip()
                        if not name:
                            name = "NO NAME"
                        logger.info(f"  {i+1}. ID:{result['id']} {name} ({result['email']}) - Priority: {result['search_priority']}")
                else:
                    # Multiple terms - prioritize full name matches
                    first_term = search_terms[0]
                    last_term = search_terms[-1]
                    queryset = queryset.annotate(
                        search_priority=Case(
                            # Exact first+last name match
                            When(Q(first_name__iexact=first_term) & Q(last_name__iexact=last_term), then=1),
                            When(Q(first_name__iexact=last_term) & Q(last_name__iexact=first_term), then=1),
                            # Partial first+last name match
                            When(Q(first_name__icontains=first_term) & Q(last_name__icontains=last_term), then=2),
                            When(Q(first_name__icontains=last_term) & Q(last_name__icontains=first_term), then=2),
                            # Any name field contains search term
                            When(Q(first_name__icontains=search_term) | Q(last_name__icontains=search_term), then=3),
                            # Email contains term
                            When(email__icontains=search_term, then=4),
                            # Phone contains term
                            When(phone__icontains=search_term, then=5),
                            default=6,
                            output_field=IntegerField()
                        )
                    ).order_by('search_priority', 'last_name', 'first_name')
        
        return queryset
    
    @action(detail=True, methods=['get'])
    def points(self, request, pk=None):
        """
        Get WooCommerce points for a specific customer
        
        Returns:
            Response with points data: {'points': points_value, 'points_value': monetary_value}
        """
        from .woocommerce import WooCommerceAPI
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            # Get the contact
            contact = self.get_object()
            
            # Check if contact has a WooCommerce customer ID
            if not contact.woo_customer_id:
                return Response(
                    {"error": "Customer does not have a WooCommerce ID", "points": 0, "points_value": 0},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Initialize WooCommerce API
            wc_api = WooCommerceAPI()
            
            # Get customer points
            points_data = wc_api.get_customer_points(contact.woo_customer_id)
            
            if points_data is None:
                return Response(
                    {"error": "Could not retrieve points data", "points": 0, "points_value": 0},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Return points data
            return Response(points_data)
            
        except Exception as e:
            logger.error(f"Error retrieving customer points: {str(e)}")
            return Response(
                {"error": f"Error retrieving points: {str(e)}", "points": 0, "points_value": 0},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def duplicates(self, request):
        from django.db.models import Count, Max
        from django.db.models.functions import Lower, Trim
        from collections import OrderedDict

        match_type = request.query_params.get('match_type', 'both')
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 30))
        search = request.query_params.get('search', '').strip()

        base_qs = Contact.objects.all()
        if search:
            terms = search.split()
            q = Q()
            for t in terms:
                q &= (
                    Q(first_name__icontains=t) | Q(last_name__icontains=t) |
                    Q(email__icontains=t) | Q(phone__icontains=t)
                )
            base_qs = base_qs.filter(q)

        groups_by_key = OrderedDict()

        if match_type in ('name', 'both'):
            name_keys = (
                base_qs
                .exclude(first_name='', last_name='')
                .annotate(fn=Lower(Trim('first_name')), ln=Lower(Trim('last_name')))
                .values('fn', 'ln')
                .annotate(cnt=Count('id'))
                .filter(cnt__gt=1)
                .order_by('-cnt')
            )
            for nk in name_keys:
                ids = set(
                    base_qs
                    .annotate(fn=Lower(Trim('first_name')), ln=Lower(Trim('last_name')))
                    .filter(fn=nk['fn'], ln=nk['ln'])
                    .values_list('id', flat=True)
                )
                key = ('name', nk['fn'], nk['ln'])
                groups_by_key[key] = {'match_reasons': ['name'], 'contact_ids': ids}

        if match_type in ('email', 'both'):
            email_rows = list(
                base_qs.exclude(email='')
                .values_list('id', 'email', 'first_name', 'last_name')
            )
            prefix_map = {}
            for cid, email, fn, ln in email_rows:
                local = email.split('@')[0].lower()
                prefix_map.setdefault(local, []).append({
                    'id': cid, 'fn': (fn or '').strip().lower(), 'ln': (ln or '').strip().lower(),
                })

            def _names_similar(a_fn, a_ln, b_fn, b_ln):
                if not a_fn or not b_fn:
                    return False
                if a_ln and b_ln and a_ln != b_ln:
                    return False
                shorter, longer = sorted([a_fn, b_fn], key=len)
                return longer.startswith(shorter) or shorter.startswith(longer)

            for local_part, entries in prefix_map.items():
                if len(entries) < 2:
                    continue
                sub_groups = []
                for entry in entries:
                    placed = False
                    for sg in sub_groups:
                        if any(_names_similar(entry['fn'], entry['ln'], m['fn'], m['ln']) for m in sg):
                            sg.append(entry)
                            placed = True
                            break
                    if not placed:
                        sub_groups.append([entry])

                for sg in sub_groups:
                    if len(sg) < 2:
                        continue
                    ids = {e['id'] for e in sg}
                    merged = False
                    for existing in groups_by_key.values():
                        if existing['contact_ids'] & ids:
                            existing['contact_ids'] |= ids
                            if 'email' not in existing['match_reasons']:
                                existing['match_reasons'].append('email')
                            merged = True
                            break
                    if not merged:
                        key = ('email', local_part, sg[0]['fn'])
                        groups_by_key[key] = {'match_reasons': ['email'], 'contact_ids': ids}

        all_groups = list(groups_by_key.values())
        total = len(all_groups)
        start = (page - 1) * page_size
        end = start + page_size
        page_groups = all_groups[start:end]

        all_ids = set()
        for g in page_groups:
            all_ids |= g['contact_ids']

        contacts_qs = (
            Contact.objects.filter(id__in=all_ids)
            .annotate(
                _pos_count=Count('pos_orders', filter=~Q(pos_orders__status='trash'), distinct=True),
                _woo_count=Count('orders', distinct=True),
                _latest_pos=Max('pos_orders__created_at', filter=~Q(pos_orders__status='trash')),
                _latest_woo=Max('orders__order_date'),
            )
        )
        contacts_map = {str(c.id): c for c in contacts_qs}

        def _serialize_contact(c):
            pos_n = c._pos_count or 0
            woo_n = c._woo_count or 0
            dates = [d for d in (c._latest_pos, c._latest_woo) if d]
            latest = max(dates).isoformat() if dates else None
            return {
                'id': str(c.id), 'first_name': c.first_name, 'last_name': c.last_name,
                'email': c.email, 'phone': c.phone,
                'woo_customer_id': c.woo_customer_id, 'ghl_contact_id': c.ghl_contact_id,
                'billing_address': c.billing_address, 'billing_city': c.billing_city,
                'billing_state': c.billing_state, 'billing_postcode': c.billing_postcode,
                'shipping_address': c.shipping_address, 'shipping_city': c.shipping_city,
                'shipping_state': c.shipping_state, 'shipping_postcode': c.shipping_postcode,
                'created_at': c.created_at.isoformat() if c.created_at else None,
                'updated_at': c.updated_at.isoformat() if c.updated_at else None,
                'pos_order_count': pos_n, 'woo_order_count': woo_n,
                'total_orders': pos_n + woo_n, 'latest_order_date': latest,
            }

        result_groups = []
        for g in page_groups:
            contacts = [_serialize_contact(contacts_map[str(cid)]) for cid in g['contact_ids'] if str(cid) in contacts_map]
            contacts.sort(key=lambda x: x['total_orders'], reverse=True)
            result_groups.append({'match_reasons': g['match_reasons'], 'contacts': contacts})

        return Response({'groups': result_groups, 'has_more': end < total, 'count': total})

    @action(detail=False, methods=['post'])
    def merge(self, request):
        keep_id = request.data.get('keep_id')
        delete_ids = request.data.get('delete_ids', [])

        if not keep_id or not delete_ids:
            return Response({'error': 'keep_id and delete_ids are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            keeper = Contact.objects.get(pk=keep_id)
        except Contact.DoesNotExist:
            return Response({'error': 'Contact to keep not found.'}, status=status.HTTP_404_NOT_FOUND)

        merge_log = []
        with transaction.atomic():
            for did in delete_ids:
                try:
                    donor = Contact.objects.get(pk=did)
                except Contact.DoesNotExist:
                    continue

                pos_transferred = donor.pos_orders.update(contact=keeper)
                woo_transferred = donor.orders.update(contact=keeper)

                if not keeper.woo_customer_id and donor.woo_customer_id:
                    keeper.woo_customer_id = donor.woo_customer_id
                if not keeper.ghl_contact_id and donor.ghl_contact_id:
                    keeper.ghl_contact_id = donor.ghl_contact_id
                if not keeper.phone and donor.phone:
                    keeper.phone = donor.phone

                merge_log.append({
                    'deleted_contact': f'{donor.first_name} {donor.last_name}'.strip(),
                    'deleted_email': donor.email,
                    'orders_transferred': {'pos': pos_transferred, 'woo': woo_transferred},
                })

                logger.info(f"Merge: deleted contact {donor.email} (id={did}), transferred {pos_transferred} POS + {woo_transferred} Woo orders to {keeper.email}")
                donor.delete()

            keeper.save()

        return Response({'merge_log': merge_log})

    @action(detail=True, methods=['get'], url_path='orders-for-merge')
    def orders_for_merge(self, request, pk=None):
        contact = self.get_object()
        orders = []

        for o in contact.pos_orders.exclude(status='trash').order_by('-created_at')[:20]:
            orders.append({
                'id': str(o.id), 'order_number': o.order_number,
                'total': float(o.total), 'status': o.status,
                'created_at': o.created_at.isoformat() if o.created_at else None,
                'source': 'pos',
            })

        for o in contact.orders.order_by('-order_date')[:20]:
            orders.append({
                'id': str(o.id), 'order_number': o.woo_order_id,
                'total': float(o.total_amount), 'status': o.status,
                'created_at': o.order_date.isoformat() if o.order_date else None,
                'source': 'woo',
            })

        orders.sort(key=lambda x: x['created_at'] or '', reverse=True)
        return Response({'orders': orders})

    @action(detail=False, methods=['get'])
    def zombies(self, request):
        from django.db.models import Count

        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 50))
        search = request.query_params.get('search', '').strip()

        qs = (
            Contact.objects
            .filter(woo_customer_id__isnull=False)
            .annotate(
                _pos_count=Count('pos_orders', filter=~Q(pos_orders__status='trash'), distinct=True),
                _woo_count=Count('orders', distinct=True),
            )
            .filter(_pos_count=0, _woo_count=0)
        )

        if search:
            terms = search.split()
            q = Q()
            for t in terms:
                q &= (
                    Q(first_name__icontains=t) | Q(last_name__icontains=t) |
                    Q(email__icontains=t) | Q(phone__icontains=t)
                )
            qs = qs.filter(q)

        total = qs.count()
        start = (page - 1) * page_size
        contacts = list(qs.order_by('last_name', 'first_name')[start:start + page_size])

        result = []
        for c in contacts:
            result.append({
                'id': str(c.id), 'first_name': c.first_name, 'last_name': c.last_name,
                'email': c.email, 'phone': c.phone,
                'woo_customer_id': c.woo_customer_id, 'ghl_contact_id': c.ghl_contact_id,
                'billing_address': c.billing_address, 'billing_city': c.billing_city,
                'billing_state': c.billing_state, 'billing_postcode': c.billing_postcode,
                'shipping_address': c.shipping_address, 'shipping_city': c.shipping_city,
                'shipping_state': c.shipping_state, 'shipping_postcode': c.shipping_postcode,
                'created_at': c.created_at.isoformat() if c.created_at else None,
                'updated_at': c.updated_at.isoformat() if c.updated_at else None,
                'pos_order_count': 0, 'woo_order_count': 0, 'total_orders': 0,
                'latest_order_date': None,
            })

        return Response({'contacts': result, 'has_more': (start + page_size) < total, 'count': total})

    @action(detail=False, methods=['post'], url_path='zombies/delete')
    def zombies_delete(self, request):
        from django.db.models import Count

        contact_ids = request.data.get('contact_ids', [])
        delete_all = request.data.get('delete_all', False)

        if delete_all:
            qs = (
                Contact.objects
                .filter(woo_customer_id__isnull=False)
                .annotate(
                    _pos_count=Count('pos_orders', filter=~Q(pos_orders__status='trash'), distinct=True),
                    _woo_count=Count('orders', distinct=True),
                )
                .filter(_pos_count=0, _woo_count=0)
            )
        elif contact_ids:
            qs = Contact.objects.filter(id__in=contact_ids)
        else:
            return Response({'error': 'Provide contact_ids or set delete_all.'}, status=status.HTTP_400_BAD_REQUEST)

        contacts = list(qs)
        deleted_count = 0

        for contact in contacts:
            email = contact.email
            try:
                if contact.ghl_contact_id:
                    try:
                        from .ghl_api import delete_ghl_contact
                        delete_ghl_contact(contact.ghl_contact_id)
                    except Exception as e:
                        logger.warning(f"Failed to delete GHL contact for {email}: {e}")

                if contact.woo_customer_id:
                    try:
                        wc_api = WooCommerceAPI()
                        wc_api.wcapi.delete(f"customers/{contact.woo_customer_id}", params={"force": True, "reassign": 0})
                    except Exception as e:
                        logger.warning(f"Failed to delete WooCommerce customer for {email}: {e}")

                contact.delete()
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting zombie contact {email}: {e}")

        return Response({'deleted': deleted_count})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_customer_with_woocommerce(request):
    """
    Create a customer in both the database and WooCommerce.
    
    This endpoint creates a customer in the local database and also creates
    a corresponding customer in WooCommerce, setting the woo_customer_id field.
    
    Expected payload:
    {
        "first_name": "John",
        "last_name": "Doe", 
        "email": "john@example.com",
        "phone": "+1234567890",
        "billing_address": "123 Main St",
        "billing_city": "City",
        "billing_state": "State",
        "billing_postcode": "12345",
        "billing_country": "US",
        "shipping_same_as_billing": true,
        "shipping_address": "123 Main St",
        "shipping_city": "City", 
        "shipping_state": "State",
        "shipping_postcode": "12345",
        "shipping_country": "US",
        "gender": "male",
        "date_of_birth": "1990-01-01",
        "email_notifications": true,
        "sms_notifications": true,
        "contact_via_phone": true,
        "contact_via_email": true,
        "contact_via_sms": true,
        "notes": "Customer notes"
    }
    
    Returns:
        Response with customer data including woo_customer_id
    """
    try:
        logger.info("🆕 Creating customer with WooCommerce integration...")
        logger.info(f"Request data: {json.dumps(request.data, indent=2)}")
        
        # Validate required fields
        required_fields = ['first_name', 'last_name', 'email']
        missing_fields = [field for field in required_fields if not request.data.get(field)]
        
        if missing_fields:
            return Response(
                {'error': f'Missing required fields: {", ".join(missing_fields)}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if customer already exists in database
        email = request.data.get('email')
        if Contact.objects.filter(email=email).exists():
            return Response(
                {'error': 'A customer with this email already exists in the database'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Step 1: Create customer in WooCommerce first
        logger.info("📦 Step 1: Creating customer in WooCommerce...")
        
        generated_password = None  # Track if we generate a password
        
        try:
            wc_api = WooCommerceAPI()
            
            # Prepare WooCommerce customer data
            woo_customer_data = {
                'email': email,
                'first_name': request.data.get('first_name', ''),
                'last_name': request.data.get('last_name', ''),
                'billing_address': request.data.get('billing_address', ''),
                'billing_city': request.data.get('billing_city', ''),
                'billing_state': request.data.get('billing_state', ''),
                'billing_postcode': request.data.get('billing_postcode', ''),
                'billing_country': request.data.get('billing_country', 'US'),
                'phone': request.data.get('phone', ''),
                'shipping_address': request.data.get('shipping_address', ''),
                'shipping_city': request.data.get('shipping_city', ''),
                'shipping_state': request.data.get('shipping_state', ''),
                'shipping_postcode': request.data.get('shipping_postcode', ''),
                'shipping_country': request.data.get('shipping_country', 'US'),
                'shipping_same_as_billing': request.data.get('shipping_same_as_billing', True)
            }
            
            # Create customer in WooCommerce
            woo_result = wc_api.create_customer(woo_customer_data)
            
            if woo_result['status'] != 'success':
                logger.error(f"WooCommerce customer creation failed: {woo_result['message']}")
                
                # Handle specific error cases
                if woo_result.get('error_code') == 'email_exists':
                    # Try to get the existing customer
                    existing_customer = wc_api.get_customer_by_email(email)
                    if existing_customer:
                        woo_customer_id = existing_customer['id']
                        logger.info(f"Using existing WooCommerce customer ID: {woo_customer_id}")
                    else:
                        # Customer exists but couldn't be retrieved - might be a guest order
                        logger.warning(f"Customer exists in WooCommerce but could not be retrieved for email: {email}")
                        logger.info("🔍 Checking for guest orders...")
                        
                        # Step 1a: Find guest orders
                        guest_orders = wc_api.find_guest_orders(email)
                        
                        if not guest_orders:
                            return Response(
                                {'error': 'Customer exists in WooCommerce but could not be retrieved and no guest orders found'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                        
                        logger.info(f"✅ Found {len(guest_orders)} guest orders")
                        
                        # Step 1b: Extract customer data from guest orders
                        extracted_data = wc_api.extract_customer_data_from_orders(guest_orders)
                        
                        if not extracted_data:
                            return Response(
                                {'error': 'Could not extract customer data from guest orders'},
                                status=status.HTTP_400_BAD_REQUEST
                            )
                        
                        logger.info("✅ Extracted customer data from guest orders")
                        
                        # Step 1c: Generate password and create WooCommerce customer account
                        generated_password = wc_api.generate_secure_password()
                        extracted_data['password'] = generated_password
                        
                        logger.info("🔐 Creating WooCommerce customer account from guest orders...")
                        
                        # Create customer with extracted data
                        conversion_result = wc_api.create_customer(extracted_data)
                        
                        if conversion_result['status'] != 'success':
                            logger.error(f"Failed to convert guest to customer: {conversion_result['message']}")
                            
                            # Check if error is due to existing account that we couldn't find earlier
                            if conversion_result.get('error_code') == 'email_exists':
                                logger.warning(f"Customer account exists but couldn't be found via API. Email: {email}")
                                logger.info("Attempting deep search for the customer...")
                                
                                # Try deep search - fetches all customers and filters locally
                                # This handles WooCommerce data inconsistencies where email search fails
                                retry_customer = wc_api.get_customer_by_email(email, deep_search=True)
                                if retry_customer:
                                    woo_customer_id = retry_customer['id']
                                    logger.info(f"✅ Deep search found customer with ID: {woo_customer_id}")
                                else:
                                    return Response(
                                        {
                                            'error': 'A WordPress user account exists with this email but cannot be accessed as a WooCommerce customer. '
                                                     'Please check WordPress admin to verify the user account, or try deleting and recreating the user.',
                                            'error_type': 'wordpress_user_conflict'
                                        },
                                        status=status.HTTP_400_BAD_REQUEST
                                    )
                            else:
                                return Response(
                                    {'error': f'Guest to customer conversion failed: {conversion_result["message"]}'},
                                    status=status.HTTP_400_BAD_REQUEST
                                )
                        
                        woo_customer_id = conversion_result['customer_id']
                        logger.info(f"✅ Guest converted to WooCommerce customer with ID: {woo_customer_id}")
                        
                        # Merge extracted data with request data for database creation
                        # Use request data where available, fall back to extracted data
                        if not request.data.get('first_name'):
                            request.data['first_name'] = extracted_data.get('first_name', '')
                        if not request.data.get('last_name'):
                            request.data['last_name'] = extracted_data.get('last_name', '')
                        if not request.data.get('phone'):
                            request.data['phone'] = extracted_data.get('billing', {}).get('phone', '')
                        if not request.data.get('billing_address'):
                            request.data['billing_address'] = extracted_data.get('billing', {}).get('address_1', '')
                        if not request.data.get('billing_city'):
                            request.data['billing_city'] = extracted_data.get('billing', {}).get('city', '')
                        if not request.data.get('billing_state'):
                            request.data['billing_state'] = extracted_data.get('billing', {}).get('state', '')
                        if not request.data.get('billing_postcode'):
                            request.data['billing_postcode'] = extracted_data.get('billing', {}).get('postcode', '')
                else:
                    return Response(
                        {'error': f'WooCommerce customer creation failed: {woo_result["message"]}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            else:
                woo_customer_id = woo_result['customer_id']
                logger.info(f"WooCommerce customer created successfully with ID: {woo_customer_id}")
            
        except Exception as e:
            logger.error(f"Error creating WooCommerce customer: {str(e)}")
            return Response(
                {'error': f'WooCommerce integration error: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Step 2: Create customer in database with WooCommerce ID
        logger.info("💾 Step 2: Creating customer in database...")
        
        try:
            # Prepare database customer data
            db_customer_data = {
                'first_name': request.data.get('first_name'),
                'last_name': request.data.get('last_name'),
                'email': email,
                'phone': request.data.get('phone', ''),
                'billing_address': request.data.get('billing_address', ''),
                'billing_city': request.data.get('billing_city', ''),
                'billing_state': request.data.get('billing_state', ''),
                'billing_postcode': request.data.get('billing_postcode', ''),
                'billing_country': request.data.get('billing_country', 'USA'),
                'shipping_address': request.data.get('shipping_address', ''),
                'shipping_city': request.data.get('shipping_city', ''),
                'shipping_state': request.data.get('shipping_state', ''),
                'shipping_postcode': request.data.get('shipping_postcode', ''),
                'shipping_country': request.data.get('shipping_country', 'USA'),
                'shipping_same_as_billing': request.data.get('shipping_same_as_billing', True),
                'gender': request.data.get('gender', ''),
                'date_of_birth': request.data.get('date_of_birth') or None,
                'email_notifications': request.data.get('email_notifications', True),
                'sms_notifications': request.data.get('sms_notifications', True),
                'contact_via_phone': request.data.get('contact_via_phone', True),
                'contact_via_email': request.data.get('contact_via_email', True),
                'contact_via_sms': request.data.get('contact_via_sms', True),
                'notes': request.data.get('notes', ''),
                'woo_customer_id': woo_customer_id,  # Set the WooCommerce customer ID
                'primary_source': 'crm',
                'is_active': True
            }
            
            # Handle shipping same as billing
            if db_customer_data['shipping_same_as_billing']:
                db_customer_data['shipping_address'] = db_customer_data['billing_address']
                db_customer_data['shipping_city'] = db_customer_data['billing_city']
                db_customer_data['shipping_state'] = db_customer_data['billing_state']
                db_customer_data['shipping_postcode'] = db_customer_data['billing_postcode']
                db_customer_data['shipping_country'] = db_customer_data['billing_country']
            
            # Create customer in database
            serializer = ContactSerializer(data=db_customer_data)
            
            if serializer.is_valid():
                customer = serializer.save()
                logger.info(f"Database customer created successfully with ID: {customer.id}")
                
                # Return the created customer data
                response_data = serializer.data
                response_data['woo_customer_id'] = woo_customer_id
                
                # Include password if it was generated (guest conversion)
                if generated_password:
                    response_data['generated_password'] = generated_password
                    response_data['is_guest_conversion'] = True
                    logger.info(f"✅ Guest conversion completed! Password generated for customer.")
                else:
                    response_data['is_guest_conversion'] = False
                
                logger.info("Customer creation completed successfully!")
                return Response(response_data, status=status.HTTP_201_CREATED)
            else:
                logger.error(f"Database customer creation failed: {serializer.errors}")
                
                # If database creation fails, we should ideally delete the WooCommerce customer
                # but for now, we'll just log the error and return the validation errors
                logger.warning(f"WARNING: WooCommerce customer {woo_customer_id} was created but database creation failed")
                
                return Response(
                    {'error': 'Database validation failed', 'details': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
        except Exception as e:
            logger.error(f"Error creating database customer: {str(e)}")
            logger.warning(f"WARNING: WooCommerce customer {woo_customer_id} was created but database creation failed")
            
            return Response(
                {'error': f'Database creation error: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
            
    except Exception as e:
        logger.error(f"Unexpected error in dual customer creation: {str(e)}")
        return Response(
            {'error': f'Unexpected error: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_customer_woo_link(request, customer_id):
    """
    Sync/relink a single local customer to WooCommerce by email.
    Uses email-only strategy and returns updated local customer payload.
    """
    try:
        try:
            customer = Contact.objects.get(id=customer_id)
        except Contact.DoesNotExist:
            return Response(
                {'error': f'Customer not found: {customer_id}'},
                status=status.HTTP_404_NOT_FOUND
            )

        email = _normalize_email_for_linking(customer.email)
        if not email:
            return Response(
                {'error': 'Customer has no valid email to synchronize'},
                status=status.HTTP_400_BAD_REQUEST
            )

        wc_api = WooCommerceAPI()
        woo_customer = wc_api.get_customer_by_email(email, deep_search=True)

        if not woo_customer or not woo_customer.get('id'):
            return Response(
                {'error': f'No WooCommerce customer found for email: {email}'},
                status=status.HTTP_404_NOT_FOUND
            )

        woo_customer_id = int(woo_customer['id'])
        customer.woo_customer_id = woo_customer_id
        customer.save(update_fields=['woo_customer_id'])

        # Merge-safe reassignment for duplicate/merged local contacts with same email.
        reassigned_customer = _reassign_woo_link_for_merged_email(
            woo_customer_id,
            email,
            reason='manual_sync_customer_woo_link'
        )
        if reassigned_customer:
            customer = reassigned_customer

        serialized = ContactSerializer(customer).data
        serialized['woo_customer_id'] = customer.woo_customer_id

        return Response({
            'success': True,
            'message': f'Synchronized WooCommerce link for {email}',
            'customer': serialized
        }, status=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error syncing Woo link for customer {customer_id}: {str(e)}")
        return Response(
            {'error': f'Failed to synchronize customer Woo link: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def _map_payment_method_for_woocommerce(payment_method_title, payment_method_json):
    """
    Map POS payment method to WooCommerce payment method identifier.
    
    Args:
        payment_method_title: The payment method title from POS (e.g., "Credit Card", "Cash")
        payment_method_json: The payment method JSON string from POS order
    
    Returns:
        str: WooCommerce payment method identifier
    """
    if not payment_method_title:
        return 'other'
    
    # Normalize the payment method title
    title_lower = payment_method_title.lower().strip()
    
    # Map common payment methods
    if title_lower in ['cash', 'cash on delivery', 'cod']:
        return 'cod'
    elif title_lower in ['credit card', 'credit', 'card', 'visa', 'mastercard', 'amex', 'discover']:
        return 'authorize_net'  # or 'stripe' depending on your payment processor
    elif title_lower in ['debit', 'debit card']:
        return 'authorize_net'
    elif title_lower in ['check', 'cheque']:
        return 'cheque'
    elif title_lower in ['bank transfer', 'wire transfer', 'ach']:
        return 'bacs'
    elif title_lower in ['paypal']:
        return 'paypal'
    
    # Try to determine from payment method JSON
    if payment_method_json:
        try:
            import json
            payment_data = json.loads(payment_method_json)
            payment_type = payment_data.get('type', '').lower()
            
            if payment_type in ['credit', 'debit']:
                return 'authorize_net'
            elif payment_type == 'cash':
                return 'cod'
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Default fallback
    return 'other'

def force_update_fulfillment_locations(woo_order_id, woo_order_data, pos_order):
    """
    Force update fulfillment locations for WooCommerce order line items.
    
    This function runs after order creation to ensure ATUM doesn't override
    the fulfillment locations selected in the POS system.
    
    Args:
        woo_order_id: WooCommerce order ID
        woo_order_data: WooCommerce order data from API response
        pos_order: POS order object
    """
    try:
        logger.info(f"🔥🔥🔥 SSH FUNCTION CALLED! force_update_fulfillment_locations for order {woo_order_id}")
        logger.info(f"🔥 WooCommerce order data type: {type(woo_order_data)}")
        logger.info(f"🔥 POS order type: {type(pos_order)}")
        logger.info(f"🔥 POS order ID: {pos_order.id if pos_order else 'None'}")
        
        import time
        import requests
        import os
        import pymysql
        from contextlib import contextmanager
        from django.conf import settings
        
        @contextmanager
        def get_woo_database_connection():
            """Direct connection to WooCommerce Cloud SQL database"""
            connection = None
            
            try:
                from core.secrets import get_woo_db_config
                woo_cfg = get_woo_db_config()
                
                logger.info(f"💾 DB Connection params: db_host={woo_cfg['db_host']}, db_port={woo_cfg['db_port']}, db_name={woo_cfg['db_name']}")
                
                if not all([woo_cfg['db_host'], woo_cfg['db_name'], woo_cfg['db_username'], woo_cfg['db_password']]):
                    raise ValueError("Missing required database connection credentials")
                
                connection = pymysql.connect(
                    host=woo_cfg['db_host'],
                    port=int(woo_cfg['db_port']),
                    user=woo_cfg['db_username'],
                    password=woo_cfg['db_password'],
                    database=woo_cfg['db_name'],
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10
                )
                logger.info(f"💾 Database connection established")
                
                yield connection
                
            except Exception as e:
                logger.error(f"Database connection error: {str(e)}")
                raise
            finally:
                if connection:
                    try:
                        connection.close()
                        logger.info(f"💾 Database connection closed")
                    except:
                        pass
        
        line_items = woo_order_data.get('line_items', [])
        logger.info(f"🔥 Found {len(line_items)} line items in WooCommerce order data")
        
        if not line_items:
            logger.warning(f"🎯 No line items found in WooCommerce order {woo_order_id}")
            return
        
        # Create mapping of POS items to their fulfillment locations
        pos_items_map = {}
        for pos_item in pos_order.items.all():
            if hasattr(pos_item, 'fulfillment_location') and pos_item.fulfillment_location:
                logger.info(f"🎯 Processing POS item: {pos_item.name} (ID: {pos_item.product_id}) -> {pos_item.fulfillment_location}")
                
                # Create multiple mapping keys for better matching
                keys = []
                
                # Add product_id as key (this might be UUID or WooCommerce ID)
                if pos_item.product_id:
                    keys.append(str(pos_item.product_id))
                
                # Add item name as key
                if pos_item.name:
                    keys.append(pos_item.name)
                
                # Try to get WooCommerce product ID from database lookup
                try:
                    from .models import Product, ProductSimple
                    import json
                    
                    woo_product_id = None
                    variation_id = None
                    
                    # CRITICAL FIX: Extract variation information from metadata for bundle items
                    try:
                        if pos_item.metadata:
                            metadata = json.loads(pos_item.metadata) if isinstance(pos_item.metadata, str) else pos_item.metadata
                            variation_selection = metadata.get('variation_selection')
                            is_bundle_item = metadata.get('is_bundle_item') or metadata.get('bundle_parent_id')
                            
                            if variation_selection:
                                variation_id_str = str(variation_selection)
                                logger.info(f"🎯 Found variation_selection in metadata: {variation_id_str}")
                                
                                # Check if this is a synthetic ID (format: "5859_pa_series_3")
                                if '_pa_' in variation_id_str:
                                    # Extract the WooCommerce parent product ID (first part before _pa_)
                                    parent_product_id = variation_id_str.split('_pa_')[0]
                                    logger.info(f"🎯 Extracted WooCommerce parent product ID from synthetic: {parent_product_id}")
                                    
                                    # For bundle items, we need to map both parent and potential variation
                                    if is_bundle_item:
                                        # Add parent product ID as a key
                                        keys.append(parent_product_id)
                                        logger.info(f"🔥 Bundle item: Added parent product ID {parent_product_id} as mapping key")
                                        
                                        # Also try to resolve the actual variation ID if possible
                                        # This helps with future SSH updates when the variation is properly created
                                        try:
                                            # Try to find the actual variation in WooCommerce
                                            variations = wc.get_product_variations(int(parent_product_id))
                                            if variations:
                                                # For now, use the first variation as fallback
                                                first_variation_id = variations[0].get('id')
                                                if first_variation_id:
                                                    keys.append(str(first_variation_id))
                                                    logger.info(f"🔥 Bundle item: Added first variation ID {first_variation_id} as mapping key")
                                        except Exception as e:
                                            logger.warning(f"🔥 Could not fetch variations for parent {parent_product_id}: {e}")
                                    else:
                                        # For regular items, use parent product ID
                                        variation_id = parent_product_id
                                elif variation_id_str.isdigit():
                                    # Direct WooCommerce variation ID
                                    variation_id = variation_id_str
                                    logger.info(f"🎯 Using direct WooCommerce variation ID: {variation_id}")
                    except Exception as e:
                        logger.warning(f"🎯 Error parsing metadata for variation: {e}")
                    
                    # If product_id is a digit, it's already a WooCommerce ID
                    if pos_item.product_id and pos_item.product_id.isdigit():
                        woo_product_id = pos_item.product_id
                        logger.info(f"🎯 Product ID is already WooCommerce ID: {woo_product_id}")
                    else:
                        # If product_id is UUID, look it up in database
                        if pos_item.product_id:
                            try:
                                # Try to find ProductSimple by UUID
                                product_simple = ProductSimple.objects.get(id=pos_item.product_id)
                                if product_simple.woo_product_id:
                                    woo_product_id = str(product_simple.woo_product_id)
                                    logger.info(f"🎯 Found WooCommerce product ID from ProductSimple: {woo_product_id}")
                            except ProductSimple.DoesNotExist:
                                logger.info(f"🎯 ProductSimple not found for UUID: {pos_item.product_id}")
                                
                                # Try to find by name as fallback
                                try:
                                    product_simple = ProductSimple.objects.filter(name__iexact=pos_item.name).first()
                                    if product_simple and product_simple.woo_product_id:
                                        woo_product_id = str(product_simple.woo_product_id)
                                        logger.info(f"🎯 Found WooCommerce product ID by name lookup: {woo_product_id}")
                                except Exception as e:
                                    logger.warning(f"🎯 Name lookup failed: {e}")
                    
                    # Add both product ID and variation ID as mapping keys
                    if woo_product_id:
                        keys.append(woo_product_id)
                    
                    # CRITICAL: Add variation ID as a separate mapping key for bundle items
                    if variation_id and variation_id != woo_product_id:
                        keys.append(variation_id)
                        logger.info(f"🎯 Added variation ID as mapping key: {variation_id}")
                        
                except Exception as e:
                    logger.warning(f"🎯 Could not resolve WooCommerce product ID: {e}")
                
                # Map all keys to the same fulfillment location
                for key in keys:
                    pos_items_map[key] = pos_item.fulfillment_location
                    logger.info(f"🎯 Mapped POS item {key} -> {pos_item.fulfillment_location}")
        
        if not pos_items_map:
            logger.info(f"🎯 No fulfillment locations found in POS order items")
            return
        
        logger.info(f"🎯 Total mapping keys created: {len(pos_items_map)}")
        logger.info(f"🎯 Mapping keys: {list(pos_items_map.keys())}")
        
        # Group keys by fulfillment location for clearer logging
        location_groups = {}
        for key, location in pos_items_map.items():
            if location not in location_groups:
                location_groups[location] = []
            location_groups[location].append(key)
        
        for location, keys in location_groups.items():
            logger.info(f"🎯 {location}: {keys}")
        
        # Update each line item's fulfillment location
        updates_made = 0
        logger.info(f"🔥 STARTING LINE ITEM PROCESSING: {len(line_items)} items to process")
        
        for line_item in line_items:
            line_item_id = line_item.get('id')
            product_id = str(line_item.get('product_id', ''))
            variation_id = str(line_item.get('variation_id', '')) if line_item.get('variation_id') else None
            item_name = line_item.get('name', '')
            
            logger.info(f"🔥🔥 PROCESSING LINE ITEM {line_item_id}:")
            logger.info(f"🔥   WooCommerce product_id: {product_id}")
            logger.info(f"🔥   WooCommerce variation_id: {variation_id}")
            logger.info(f"🔥   Item name: {item_name}")
            
            # Find matching fulfillment location
            fulfillment_location = None
            
            # 🔥 ENHANCED MATCHING: Try multiple matching strategies for bundle items
            # 1. Try exact product_id match
            if product_id in pos_items_map:
                fulfillment_location = pos_items_map[product_id]
                logger.info(f"🎯 Found match by product_id: {product_id} -> {fulfillment_location}")
            # 2. Try variation_id match (for variable products)
            elif variation_id and variation_id in pos_items_map:
                fulfillment_location = pos_items_map[variation_id]
                logger.info(f"🔥 Found match by variation_id: {variation_id} -> {fulfillment_location}")
            # 3. Try item_name match
            elif item_name in pos_items_map:
                fulfillment_location = pos_items_map[item_name]
                logger.info(f"🎯 Found match by item_name: {item_name} -> {fulfillment_location}")
            # 4. 🔥 BUNDLE ITEM FALLBACK: Try to find by partial name match (for bundle items)
            else:
                # For bundle items, try to match by the base product name (without variation attributes and tree prefixes)
                for key, location in pos_items_map.items():
                    # Check if the key is a product name that might match the line item
                    if isinstance(key, str) and not key.isdigit():
                        # Clean both names for comparison
                        clean_key = key.replace(" - Single", "").replace(" - 3 series", "").replace(" - 6 series", "").strip()
                        
                        # 🔥 CRITICAL: Remove bundle tree prefixes from WooCommerce item names
                        clean_item_name = item_name.replace("└─ ", "").replace("├─ ", "").replace("│  ", "").replace("   ", " ").strip()
                        clean_item_name = clean_item_name.replace(" - Single", "").replace(" - 3 series", "").replace(" - 6 series", "").strip()
                        
                        if clean_key.lower() == clean_item_name.lower():
                            fulfillment_location = location
                            logger.info(f"🔥 Found match by cleaned name: '{clean_key}' matches '{clean_item_name}' (original: '{item_name}') -> {fulfillment_location}")
                            break
                
                # 🔥 SPECIAL CASE: Handle bundle parent items (e.g., "OFFER: (PG) Assessment")
                if not fulfillment_location and ("OFFER:" in item_name or "Bundle" in item_name):
                    # For bundle parent items, use the first available fulfillment location from the bundle items
                    # This assumes all bundle items should have the same fulfillment location
                    available_locations = list(set(pos_items_map.values()))
                    if available_locations:
                        fulfillment_location = available_locations[0]  # Use the first location
                        logger.info(f"🔥 Bundle parent item '{item_name}' assigned location: {fulfillment_location}")
                    else:
                        logger.warning(f"🔥 No fulfillment locations available for bundle parent: {item_name}")
                
                if not fulfillment_location:
                    logger.warning(f"🎯 No match found for product_id={product_id}, variation_id={variation_id}, or item_name={item_name}")
                    logger.warning(f"🎯 Available keys: {list(pos_items_map.keys())}")
            
            if fulfillment_location and line_item_id:
                logger.info(f"🎯 Updating line item {line_item_id} ({item_name}) -> {fulfillment_location}")
                
                # Get ATUM location ID for this fulfillment location
                atum_location_id = None
                try:
                    from .models import POSSetting, InventoryLocation
                    import json
                    
                    # Get ATUM location mappings from settings
                    try:
                        mapping_setting = POSSetting.objects.get(key='ATUM_LOCATION_MAPPINGS')
                        atum_mappings = json.loads(mapping_setting.value) if mapping_setting.value else {}
                    except POSSetting.DoesNotExist:
                        atum_mappings = {}
                    
                    # Try to find ATUM location ID for this fulfillment location
                    if fulfillment_location in atum_mappings:
                        atum_location_id = atum_mappings[fulfillment_location]
                        logger.info(f"🎯 Found ATUM location ID from mapping: {fulfillment_location} -> {atum_location_id}")
                    else:
                        # Try to find by direct database query to wp_atum_inventories table using WooCommerce DB
                        try:
                            with get_woo_database_connection() as woo_db:
                                with woo_db.cursor() as cursor:
                                    # 🔥 CRITICAL FIX: wp_atum_inventories.id IS the ATUM location ID!
                                    # The table doesn't have an 'inventory_id' column
                                    cursor.execute("""
                                        SELECT id, name, product_id FROM wp_atum_inventories 
                                        WHERE name = %s AND product_id = %s 
                                        ORDER BY id ASC
                                    """, [fulfillment_location, product_id])
                                    all_results = cursor.fetchall()
                                    
                                    if all_results:
                                        if len(all_results) > 1:
                                            logger.warning(f"🚨 DUPLICATE ATUM INVENTORY RECORDS FOUND for {fulfillment_location} + product {product_id}:")
                                            for idx, rec in enumerate(all_results):
                                                logger.warning(f"   [{idx}] ATUM Location ID: {rec['id']}, Name: {rec['name']}, Product: {rec['product_id']}")
                                            logger.warning(f"   Using FIRST record's id: {all_results[0]['id']}")
                                        
                                        result = all_results[0]  # Use first/lowest ID for consistency
                                        atum_location_id = result['id']  # 🔥 CRITICAL: id column IS the ATUM location ID!
                                        logger.info(f"🎯 Found ATUM location ID from wp_atum_inventories via SSH: {fulfillment_location} (product {product_id}) -> id={atum_location_id}")
                                    else:
                                        # Fallback: try to find any record with this location name (ordered by ID)
                                        logger.warning(f"🔥 No exact match found for {fulfillment_location} + product {product_id}, trying name-only fallback")
                                        cursor.execute("""
                                            SELECT id, name, product_id FROM wp_atum_inventories 
                                            WHERE name = %s 
                                            ORDER BY id ASC
                                            LIMIT 1
                                        """, [fulfillment_location])
                                        result = cursor.fetchone()
                                        if result:
                                            atum_location_id = result['id']  # 🔥 CRITICAL: id column IS the ATUM location ID!
                                            logger.info(f"🎯 Found ATUM location ID (fallback) from wp_atum_inventories via SSH: {fulfillment_location} -> id={atum_location_id} (product: {result['product_id']})")
                        except Exception as e:
                            logger.warning(f"🎯 Could not lookup ATUM inventory ID via SSH: {e}")
                            
                except Exception as e:
                    logger.warning(f"🎯 Error getting ATUM location ID: {e}")
                
                # Prepare update data with both fulfillment location and ATUM location ID
                meta_data = [
                    {
                        "key": "_fulfillment_location",
                        "value": fulfillment_location
                    }
                ]
                
                # Add ATUM location ID if found
                if atum_location_id:
                    meta_data.append({
                        "key": "_atum_location",
                        "value": str(atum_location_id)
                    })
                    # CRITICAL: Also add _atum_inventory_id - this is what ATUM uses for UI display
                    meta_data.append({
                        "key": "_atum_inventory_id",
                        "value": str(atum_location_id)
                    })
                    logger.info(f"🎯 Will update _fulfillment_location='{fulfillment_location}', _atum_location='{atum_location_id}', and _atum_inventory_id='{atum_location_id}'")
                else:
                    logger.warning(f"🎯 Could not find ATUM location ID for '{fulfillment_location}' - updating fulfillment_location only")
                
                update_data = {
                    "meta_data": meta_data
                }
                
                try:
                    # BYPASS WooCommerce API - Update metadata directly in database (more reliable)
                    logger.info(f"🎯 Updating metadata directly in database for line item {line_item_id}")
                    
                    with get_woo_database_connection() as woo_db:
                        with woo_db.cursor() as cursor:
                            # Update/Insert metadata directly in wp_woocommerce_order_itemmeta table
                            for meta_item in meta_data:
                                key = meta_item['key']
                                value = meta_item['value']
                                
                                # First try to update existing meta
                                cursor.execute("""
                                    UPDATE wp_woocommerce_order_itemmeta 
                                    SET meta_value = %s 
                                    WHERE order_item_id = %s AND meta_key = %s
                                """, [value, line_item_id, key])
                                
                                # If no rows were updated, insert new meta
                                if cursor.rowcount == 0:
                                    cursor.execute("""
                                        INSERT INTO wp_woocommerce_order_itemmeta (order_item_id, meta_key, meta_value)
                                        VALUES (%s, %s, %s)
                                    """, [line_item_id, key, value])
                                
                                logger.info(f"🎯 ✅ Updated/Inserted metadata: {key} = {value} for line item {line_item_id}")
                            
                            # CRITICAL: Also update wp_atum_inventory_orders table
                            if atum_location_id:
                                # First get the correct name from wp_atum_inventories for extra_data
                                cursor.execute("""
                                    SELECT name FROM wp_atum_inventories 
                                    WHERE id = %s
                                """, [atum_location_id])
                                inventory_result = cursor.fetchone()
                                inventory_name = inventory_result['name'] if inventory_result else fulfillment_location
                                
                                # Get current extra_data to preserve other fields
                                cursor.execute("""
                                    SELECT extra_data FROM wp_atum_inventory_orders 
                                    WHERE order_item_id = %s
                                """, [line_item_id])
                                current_extra_data_result = cursor.fetchone()
                                
                                if current_extra_data_result and current_extra_data_result['extra_data']:
                                    # Parse existing serialized PHP data and update only the name
                                    import re
                                    current_extra_data = current_extra_data_result['extra_data']
                                    # Replace the name value in the serialized string
                                    # Pattern: s:4:"name";s:X:"OldName" -> s:4:"name";s:Y:"NewName"
                                    name_length = len(inventory_name)
                                    new_extra_data = re.sub(
                                        r's:4:"name";s:\d+:"[^"]*"',
                                        f's:4:"name";s:{name_length}:"{inventory_name}"',
                                        current_extra_data
                                    )
                                    logger.info(f"🎯 Updated serialized extra_data: name changed to {inventory_name}")
                                else:
                                    # Create new serialized data if none exists
                                    new_extra_data = f'a:1:{{s:4:"name";s:{len(inventory_name)}:"{inventory_name}";}}'
                                    logger.info(f"🎯 Created new serialized extra_data with name: {inventory_name}")
                                
                                # Update both inventory_id and extra_data
                                cursor.execute("""
                                    UPDATE wp_atum_inventory_orders 
                                    SET inventory_id = %s, extra_data = %s 
                                    WHERE order_item_id = %s
                                """, [atum_location_id, new_extra_data, line_item_id])
                                
                                rows_affected = cursor.rowcount
                                if rows_affected > 0:
                                    logger.info(f"🎯 ✅ Updated wp_atum_inventory_orders: order_item_id={line_item_id} -> inventory_id={atum_location_id}, extra_data name={inventory_name}")
                                else:
                                    logger.warning(f"🎯 No rows updated in wp_atum_inventory_orders for order_item_id={line_item_id}")
                            
                            # Commit all changes
                            woo_db.commit()
                            updates_made += 1
                            logger.info(f"🎯 ✅ Successfully updated all metadata and ATUM inventory for line item {line_item_id}")
                        
                except Exception as e:
                    logger.error(f"🎯 ❌ Exception updating line item {line_item_id}: {str(e)}")
            else:
                if not fulfillment_location:
                    logger.info(f"🎯 No fulfillment location found for line item {line_item_id} ({item_name})")
                if not line_item_id:
                    logger.warning(f"🎯 No line item ID found for {item_name}")
        
        logger.info(f"🔥🔥 SSH UPDATE SUMMARY:")
        logger.info(f"🔥   Total line items processed: {len(line_items)}")
        logger.info(f"🔥   Successfully updated: {updates_made}")
        logger.info(f"🔥   Failed to match: {len(line_items) - updates_made}")
        logger.info(f"🎯 FORCE UPDATE COMPLETE: Updated {updates_made} line items for order {woo_order_id}")
        
    except Exception as e:
        logger.error(f"🎯 ❌ Error in force_update_fulfillment_locations: {str(e)}")


def determine_order_status_from_fulfillment(pos_order):
    """
    Determine WooCommerce order status based on fulfillment locations and shipping flags.
    
    Rules:
    - If ANY item has needs_shipping=True → 'processing' (needs to be shipped)
    - If ANY item is Dropship → 'processing' (needs to be shipped)
    - If ALL items are from clinic inventory (Boca/Jupiter) with no shipping → 'completed'
    - Mixed orders follow Dropship rule (processing)
    
    Args:
        pos_order: POSOrder instance with items
    
    Returns:
        str: 'completed' or 'processing'
    """
    CLINIC_LOCATIONS = ['boca inventory', 'jupiter inventory']
    
    all_clinic_no_shipping = True
    has_items = False
    
    for item in pos_order.items.all():
        has_items = True
        
        # Check if this item needs shipping (toggle was ON in POS)
        needs_shipping = getattr(item, 'needs_shipping', False)
        if needs_shipping:
            logger.info(f"📦 Item '{item.name}' has needs_shipping=True → order stays 'processing'")
            all_clinic_no_shipping = False
            break
        
        location = getattr(item, 'fulfillment_location', None) or ''
        location_lower = location.lower().strip()
        
        # Check if this is a clinic location
        is_clinic = any(clinic in location_lower for clinic in CLINIC_LOCATIONS)
        
        if not is_clinic:
            logger.info(f"📦 Item '{item.name}' location '{location}' is not clinic → order stays 'processing'")
            all_clinic_no_shipping = False
            break
    
    # If all items are clinic inventory with no shipping needed, mark as completed
    if has_items and all_clinic_no_shipping:
        logger.info(f"✅ All items are clinic inventory with no shipping → order status 'completed'")
        return 'completed'
    
    # Default to processing (dropship, mixed, or needs shipping)
    return 'processing'


def _queue_failed_order(pos_order, failed_step, error_message, error_traceback='', request_payload=None):
    """
    Queue a failed post-payment order step for later retry and fire a Slack alert.
    ``pos_order`` may be None when the POS order itself could not be saved;
    in that case, order details are pulled from ``request_payload``.
    Returns the FailedOrderQueue entry or None.
    """
    from .models import FailedOrderQueue
    from .slack_notifications import notify_order_queued
    import threading

    try:
        payload = request_payload or {}

        if pos_order is not None:
            payment_info = pos_order.payment_method or {}
            order_number = pos_order.order_number
            payment_amount = pos_order.total
            payment_method_str = pos_order.payment_method_title or str(payment_info.get('type', ''))
            transaction_id = pos_order.transaction_id or ''
            contact = pos_order.contact
            customer_name = ''
            customer_email = ''
            if contact:
                customer_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
                customer_email = contact.email or ''
            pos_order_id_str = str(pos_order.id)
        else:
            order_number = payload.get('order_number', 'UNKNOWN')
            payment_amount = payload.get('payment_amount') or payload.get('total')
            payment_method_str = payload.get('payment_method', '')
            transaction_id = payload.get('transaction_id', '')
            customer_name = payload.get('customer_name', '')
            customer_email = payload.get('customer_email', '')
            pos_order_id_str = ''

        entry = FailedOrderQueue.objects.create(
            pos_order=pos_order,
            order_number=order_number,
            failed_step=failed_step,
            error_message=error_message,
            error_traceback=error_traceback,
            payment_taken=True,
            payment_amount=payment_amount,
            payment_method=payment_method_str,
            transaction_id=transaction_id,
            request_payload=payload,
            status='queued',
        )

        logger.error(
            f"[QUEUE] Order {order_number} queued — "
            f"step={failed_step}, error={error_message[:200]}"
        )

        def _send_slack():
            try:
                notify_order_queued(
                    order_number=order_number,
                    failed_step=failed_step,
                    error_message=error_message,
                    payment_amount=float(payment_amount) if payment_amount else None,
                    payment_method=payment_method_str,
                    transaction_id=transaction_id,
                    customer_name=customer_name,
                    customer_email=customer_email,
                    pos_order_id=pos_order_id_str,
                )
                entry.slack_notified = True
                entry.save(update_fields=['slack_notified'])
            except Exception as exc:
                logger.error(f"[QUEUE] Slack notification failed: {exc}")

        threading.Thread(target=_send_slack, daemon=True).start()

        return entry
    except Exception as exc:
        order_ref = pos_order.order_number if pos_order else (request_payload or {}).get('order_number', '??')
        logger.error(f"[QUEUE] Failed to queue order {order_ref}: {exc}")
        return None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_woocommerce_order(request):
    """
    Create a WooCommerce order from a POS order.
    
    This endpoint receives a POS order and creates a corresponding order in WooCommerce.
    If the order contains subscription products, it will also create the necessary subscriptions.
    
    Parameters:
        - order_id: ID of the POS order to convert to WooCommerce order
        - created_by: Email of the sales manager who created the order (optional)
    """
    try:
        import json  # Ensure json is available in this function scope
        
        # Get the POS order ID from the request
        pos_order_id = request.data.get('order_id')
        if not pos_order_id:
            return Response({'error': 'Order ID is required'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Get the created_by field (sales manager email) if provided
        created_by = request.data.get('created_by')
        
        # Get the payment_method from the request if provided
        payment_method = request.data.get('payment_method')
        
        # Get the subscription_items from the request if provided
        frontend_subscription_items = request.data.get('subscription_items', [])
        
        # Get the notes from the request if provided
        notes = request.data.get('notes', '')
        
        # Parse [SKIP_GHL_EMAIL] flag from notes
        skip_ghl_email = False
        if notes and '[SKIP_GHL_EMAIL]' in notes:
            skip_ghl_email = True
            notes = notes.replace('[SKIP_GHL_EMAIL]', '').strip()
            logger.info(f"🔕 Skip GHL Email flag detected - will add to order meta_data")
        
        # Parse [CREDIT_BANK_REDEEMED:amount:remaining] flag from notes
        credit_bank_redeemed = None
        credit_bank_remaining = None
        if notes:
            import re as _re
            cb_match = _re.search(r'\[CREDIT_BANK_REDEEMED:([\d.]+):([\d.]+)\]', notes)
            if cb_match:
                credit_bank_redeemed = cb_match.group(1)
                credit_bank_remaining = cb_match.group(2)
                notes = _re.sub(r'\[CREDIT_BANK_REDEEMED:[\d.]+:[\d.]+\]', '', notes).strip()
                logger.info(f"🏦 Credit Bank flag detected - redeemed: ${credit_bank_redeemed}, remaining: ${credit_bank_remaining}")
        
        # ✅ GET DISCOUNT DATA FROM REQUEST
        order_discount = request.data.get('order_discount')
        order_discount_type = request.data.get('order_discount_type')
        discounted_total = request.data.get('discounted_total')
        original_subtotal = request.data.get('original_subtotal')
        
        # 🔥 NEW: GET CREDITED ITEMS DATA FROM REQUEST
        credited_items = request.data.get('credited_items', [])
        
        logger.info(f"🎯 Creating WooCommerce order for POS order {pos_order_id}")
        logger.info(f"📝 Order details: created_by={created_by}, payment_method={payment_method}, notes='{notes}'")
        logger.info(f"💰 Discount data: order_discount={order_discount}, discount_type={order_discount_type}")
        logger.info(f"💰 Totals: original_subtotal={original_subtotal}, discounted_total={discounted_total}")
        logger.info(f"🔄 Frontend subscription items received: {frontend_subscription_items}")
        logger.info(f"🔄 Subscription items type: {type(frontend_subscription_items)}")
        logger.info(f"🔄 Subscription items length: {len(frontend_subscription_items) if frontend_subscription_items else 0}")
        logger.info(f"💳 Credited items received: {credited_items}")
        logger.info(f"💳 Credited items length: {len(credited_items) if credited_items else 0}")
        
        # Get the POS order from the database
        try:
            pos_order = POSOrder.objects.get(id=pos_order_id)
        except POSOrder.DoesNotExist:
            return Response({'error': f'POS Order with ID {pos_order_id} not found'}, status=status.HTTP_404_NOT_FOUND)
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get customer information
        customer_id = None
        customer_note = ""
        internal_note = ""
        
        # Determine who created this order for note attribution
        added_by = ''
        if hasattr(request, 'user') and request.user and request.user.is_authenticated:
            added_by = request.user.get_full_name() or request.user.username or ''
        if not added_by and created_by:
            added_by = created_by
        added_by = added_by or 'POS User'
        
        if pos_order.contact:
            # If we have a contact, try to find or create the customer in WooCommerce
            contact = pos_order.contact
            
            # Internal POS tracking note (added as private admin note after order creation)
            # Do NOT put this in customer_note — that field is visible on emails/receipts
            # Only post the user's actual note text — POS order number is already in metadata
            if notes and notes.strip():
                internal_note = f"[{added_by}] {notes.strip()}"
            
            # Check if the customer exists in WooCommerce
            # This is a simplified approach - in production, you might want to sync customers first
            customer_id = contact.woo_customer_id if hasattr(contact, 'woo_customer_id') and contact.woo_customer_id else None
    
        # Format the order items and track subscription items
        line_items = []
        subscription_items = []
        bundle_parents = {}  # Track bundle parent items to add later
        bundle_children = []  # Track bundle child items
        
        # 🚀 PERFORMANCE: Pre-fetch all product/variation/ATUM data in parallel
        # This converts ~15-25 sequential API calls (15-50s) into parallel batches (~3-8s)
        try:
            order_items_list = list(pos_order.items.all())
            wc.prefetch_products_for_order(order_items_list)
        except Exception as prefetch_error:
            logger.warning(f"[PREFETCH] Pre-fetch failed (will fall back to sequential): {prefetch_error}")
            order_items_list = list(pos_order.items.all())
        
        for item in order_items_list:
            # 🔥 BUNDLE ITEM DETECTION: Check if this is an expanded bundle item
            is_bundle_item = False
            bundle_parent_info = ""
            
            # Parse metadata if it's a string
            metadata = item.metadata
            if isinstance(metadata, str):
                try:
                    import json
                    metadata = json.loads(metadata)
                    logger.info(f"🔍 Parsed metadata from JSON string for {item.name}")
                except Exception as e:
                    logger.error(f"🔍 Failed to parse metadata JSON: {e}")
                    metadata = {}
            
            if metadata and isinstance(metadata, dict):
                # Check both bundle_parent_id and is_bundle_item flag
                if metadata.get('bundle_parent_id') or metadata.get('is_bundle_item'):
                    is_bundle_item = True
                    bundle_parent_name = metadata.get('bundle_parent_name', 'Unknown Bundle')
                    bundle_parent_info = f" (from bundle: {bundle_parent_name})"
                    logger.info(f"🔥 Processing bundle item: {item.name}{bundle_parent_info}")
                    logger.info(f"   Bundle metadata: {metadata}")
                    logger.info(f"   🎯 variation_selection in metadata: {metadata.get('variation_selection')}")
            
            # Check if we have a valid product ID
            product_id = None
            variation_id = None  # For variable products, this will hold the variation ID
            product_name_override = None  # For variations, we'll fetch the correct name
            
            if item.product_id:
                if item.product_id.isdigit():
                    product_id = int(item.product_id)
                else:
                    # product_id is a UUID — check metadata for the actual numeric WooCommerce product ID
                    # This prevents name-based fallback from picking the wrong product when
                    # multiple products share the same name (e.g., different brands of "Berberine Capsules (120 ct)")
                    meta_woo_id = None
                    if metadata and isinstance(metadata, dict):
                        meta_woo_id = metadata.get('woo_product_id') or metadata.get('wooProductId')
                    if meta_woo_id and str(meta_woo_id).isdigit():
                        product_id = int(meta_woo_id)
                        logger.info(f"🔑 Resolved WooCommerce product ID {product_id} from metadata for UUID product_id: {item.product_id}")
                        
                        # 🔥 BUNDLE VARIABLE FIX: Check if woo_product_id is actually a variation ID
                        # For bundle variable items, woo_product_id may hold the variation ID (e.g., "30 Capsules")
                        # instead of the parent product ID. WooCommerce requires product_id=parent, variation_id=variation.
                        bundle_variation_selection = metadata.get('variation_selection') if metadata else None
                        if bundle_variation_selection and str(bundle_variation_selection) == str(meta_woo_id):
                            try:
                                logger.info(f"🔑 woo_product_id {meta_woo_id} matches variation_selection — resolving parent product")
                                var_response = wc.get_product_variation(product_id)
                                if var_response and 'data' in var_response:
                                    parent_id = var_response['data'].get('parent_id')
                                    if parent_id:
                                        variation_id = product_id  # Current product_id is the variation
                                        product_id = parent_id     # Set to parent product ID
                                        
                                        # Also resolve the product name with variation attributes
                                        parent_response = wc.get_product(parent_id)
                                        if parent_response and 'data' in parent_response:
                                            parent_name = parent_response['data'].get('name', item.name)
                                            attributes = var_response['data'].get('attributes', [])
                                            attr_parts = [attr['option'] for attr in attributes if attr.get('option')]
                                            if attr_parts:
                                                product_name_override = f"{parent_name} - {' - '.join(attr_parts)}"
                                            else:
                                                product_name_override = parent_name
                                        
                                        logger.info(f"🔑 BUNDLE VARIABLE RESOLVED: parent_id={product_id}, variation_id={variation_id}, name={product_name_override}")
                                    else:
                                        logger.info(f"🔑 Variation {meta_woo_id} has no parent_id — treating as standalone product")
                                else:
                                    logger.info(f"🔑 Could not fetch variation {meta_woo_id} — keeping as product_id")
                            except Exception as e:
                                logger.warning(f"🔑 Error resolving variation parent for {meta_woo_id}: {e}")
                    
                    # 🎯 VARIATION FIX: Check if this is a variation ID (for bundle items OR regular variable products)
                    # If this is a bundle item with a variation selection, fetch the variation details
                    variation_selection = metadata.get('variation_selection') if metadata else None
                    
                    # 🎯 NEW: Also check for regular variable products with variation data
                    # Check if this item has variation data (from frontend cart item.variation or metadata)
                    if not variation_selection and metadata:
                        # Check metadata for variation_selection (from regular variable products)
                        metadata_variation = metadata.get('variation_selection')
                        if metadata_variation:
                            variation_selection = metadata_variation
                            logger.info(f"🎯 Found regular variable product variation from metadata: {variation_selection}")
                    
                    # Also check if this item has variation data directly (backup method)
                    variation_data = getattr(item, 'variation', None)
                    if not variation_selection and variation_data and hasattr(variation_data, 'get'):
                        # Extract variation ID from variation data
                        variation_id = variation_data.get('id')
                        variation_woo_id = variation_data.get('wooVariationId')
                        if variation_woo_id:
                            variation_selection = variation_woo_id
                            logger.info(f"🎯 Found regular variable product variation from item data: {variation_selection}")
                        elif variation_id:
                            variation_selection = variation_id
                            logger.info(f"🎯 Found regular variable product variation (synthetic) from item data: {variation_selection}")
                    
                    # Log variation selection status for debugging
                    logger.info(f"🎯 Variation selection check for {item.name}: {variation_selection}")
                    
                    if variation_selection:
                        variation_id_str = str(variation_selection)
                        logger.info(f"🎯 Processing variation selection: {variation_id_str}")
                        
                        # Check if this is a synthetic ID (format: "3024_pa_series_3" or "284769_pa_duration_1")
                        # If so, extract the parent product ID and attribute value
                        if '_pa_' in variation_id_str:
                            try:
                                # Extract parent product ID from synthetic ID
                                parts = variation_id_str.split('_pa_')
                                parent_product_id = int(parts[0])
                                attribute_part = parts[1]  # e.g., "series_3" or "duration_1"
                                
                                logger.info(f"🎯 Detected synthetic variation ID - Parent: {parent_product_id}, Attribute: {attribute_part}")
                                
                                # Handle different attribute types
                                if attribute_part.startswith('series_'):
                                    # Series attribute (e.g., "series_3")
                                    series_index = int(attribute_part.split('_')[1])
                                    series_value = metadata.get('series_selection', 1)
                                    attribute_name = 'Series'
                                    attribute_value = 'Single' if series_value == 1 else f'{series_value} series'
                                    logger.info(f"🎯 Series variation - Looking for {attribute_name}: {attribute_value}")
                                elif attribute_part.startswith('duration_') or attribute_part.startswith('period_'):
                                    # Duration/Period attribute (e.g., "duration_1")
                                    duration_index = int(attribute_part.split('_')[1])
                                    # Get the actual duration value from metadata or use common mappings
                                    duration_value = metadata.get('duration_selection') or metadata.get('period_selection')
                                    if not duration_value:
                                        # Common duration mappings
                                        duration_mappings = {1: '1 month', 2: '3 months', 3: '6 months', 4: '12 months'}
                                        duration_value = duration_mappings.get(duration_index, '1 month')
                                    attribute_name = 'Duration'
                                    attribute_value = str(duration_value)
                                    logger.info(f"🎯 Duration variation - Looking for {attribute_name}: {attribute_value}")
                                else:
                                    # Generic attribute handling
                                    attr_parts = attribute_part.split('_')
                                    attr_type = attr_parts[0]
                                    attr_index = int(attr_parts[1]) if len(attr_parts) > 1 else 1
                                    attribute_name = attr_type.title()
                                    attribute_value = metadata.get(f'{attr_type}_selection', f'{attr_type} {attr_index}')
                                    logger.info(f"🎯 Generic variation - Looking for {attribute_name}: {attribute_value}")
                                
                                logger.info(f"🎯 Looking up variation for product {parent_product_id} with {attribute_name}: {attribute_value}")
                                
                                # Fetch all variations for this product
                                variations = wc.get_product_variations(parent_product_id)
                                
                                if variations:
                                    # Find the variation that matches the attribute
                                    matching_variation = None
                                    for var in variations:
                                        attributes = var.get('attributes', [])
                                        for attr in attributes:
                                            # Check multiple possible attribute name formats
                                            attr_names = [
                                                attribute_name,
                                                f'pa_{attribute_name.lower()}',
                                                attribute_name.lower(),
                                                f'pa_{attr_type}' if 'attr_type' in locals() else None
                                            ]
                                            attr_names = [name for name in attr_names if name]  # Remove None values
                                            
                                            if attr.get('name') in attr_names and attr.get('option') == attribute_value:
                                                matching_variation = var
                                                break
                                        if matching_variation:
                                            break
                                    
                                    if matching_variation:
                                        # 🔥 CRITICAL FIX: Set parent product ID and variation ID separately
                                        product_id = parent_product_id  # Parent product ID
                                        variation_id = matching_variation['id']  # Actual variation ID
                                        
                                        # Get parent product name
                                        parent_response = wc.get_product(parent_product_id)
                                        if parent_response and 'data' in parent_response:
                                            parent_name = parent_response['data'].get('name', item.name)
                                            product_name_override = f"{parent_name} - {attribute_value}"
                                            logger.info(f"🎯 Variation resolved: ID={product_id}, Name={product_name_override}")
                                    else:
                                        logger.warning(f"🎯 No matching variation found for {attribute_name}: {attribute_value}, using parent product")
                                        product_id = parent_product_id
                            except Exception as e:
                                logger.error(f"🎯 Error parsing synthetic variation ID: {str(e)}")
                                # Continue with original product_id
                        else:
                            # This is a real WooCommerce variation ID
                            try:
                                variation_id_int = int(variation_id_str)
                                logger.info(f"🎯 Fetching variation details for ID: {variation_id_int}")
                                
                                # Get the variation directly using the variation ID
                                logger.info(f"🔥 VARIATION DEBUG: Attempting to fetch variation {variation_id_int}")
                                variation_response = wc.get_product_variation(variation_id_int)
                                logger.info(f"🔥 VARIATION DEBUG: Response received: {variation_response}")
                                
                                if variation_response and 'data' in variation_response:
                                    variation_data = variation_response['data']
                                    # Get the parent product name
                                    parent_id = variation_data.get('parent_id')
                                    if parent_id:
                                        parent_response = wc.get_product(parent_id)
                                        if parent_response and 'data' in parent_response:
                                            parent_name = parent_response['data'].get('name', item.name)
                                            
                                            # Build variation name with attributes
                                            attributes = variation_data.get('attributes', [])
                                            attr_parts = []
                                            for attr in attributes:
                                                if attr.get('option'):
                                                    attr_parts.append(attr['option'])
                                            
                                            if attr_parts:
                                                product_name_override = f"{parent_name} - {' - '.join(attr_parts)}"
                                                logger.info(f"🎯 Variation name resolved: {product_name_override}")
                                            else:
                                                product_name_override = parent_name
                                                logger.info(f"🎯 Using parent name: {product_name_override}")
                                            
                                            # 🔥 CRITICAL FIX: Set parent product ID and variation ID separately
                                            product_id = parent_id  # Parent product ID
                                            variation_id = variation_id_int  # Actual variation ID
                                            logger.info(f"🔥 VARIATION SUCCESS: parent_id={product_id}, variation_id={variation_id}")
                                else:
                                    logger.error(f"🔥 VARIATION FAILED: No data in response for variation {variation_id_int}")
                                    logger.error(f"🔥 VARIATION FAILED: Full response: {variation_response}")
                            except Exception as e:
                                logger.error(f"🔥 VARIATION ERROR: Error fetching variation details: {str(e)}")
                                # Continue with original name if variation fetch fails
                    
                    # 🎯 NEW: Handle variable products without variation selection
                    # Only check for variations if this looks like it should be a variable product
                    # (has "Studio Detox Protocol" in name or other known variable products)
                    elif (is_bundle_item and metadata and not variation_selection and 
                          ("Studio Detox Protocol" in item.name or "Protocol" in item.name)):
                        logger.info(f"🎯 Potential variable product without variation selection: {item.name}")
                        
                        # Check if this product has variations in WooCommerce
                        try:
                            logger.info(f"🎯 Checking if product {product_id} has variations...")
                            variations = wc.get_product_variations(product_id)
                            
                            if variations and len(variations) > 0:
                                logger.info(f"🎯 Found {len(variations)} variations for product {product_id}")
                                
                                # Debug: Log all available variations
                                for i, var in enumerate(variations):
                                    attrs = var.get('attributes', [])
                                    attr_info = []
                                    for attr in attrs:
                                        attr_info.append(f"{attr.get('name', 'Unknown')}={attr.get('option', 'None')}")
                                    logger.info(f"🎯 Variation {i}: ID={var['id']}, Attributes=[{', '.join(attr_info)}]")
                                
                                # Resolve default variation from Woo defaults first, then menu_order.
                                # Avoid hardcoded "Single service" preference because it can override
                                # bundle-specific variation filters/defaults configured in WooCommerce.
                                default_variation = None

                                try:
                                    parent_response = wc.get_product(product_id)
                                    parent_data = parent_response.get('data', {}) if isinstance(parent_response, dict) else {}
                                    default_attributes = parent_data.get('default_attributes') or []

                                    if default_attributes:
                                        def normalize_attr(value):
                                            return str(value or '').strip().lower().replace('-', ' ').replace('_', ' ')

                                        for var in variations:
                                            var_attrs = var.get('attributes', []) or []
                                            matches_all = True

                                            for default_attr in default_attributes:
                                                default_name = normalize_attr(default_attr.get('name'))
                                                default_option = normalize_attr(default_attr.get('option'))
                                                if not default_name or not default_option:
                                                    continue

                                                matched_attr = False
                                                for var_attr in var_attrs:
                                                    var_name = normalize_attr(var_attr.get('name'))
                                                    var_option = normalize_attr(var_attr.get('option'))
                                                    if not var_name or not var_option:
                                                        continue
                                                    if var_name == default_name and var_option == default_option:
                                                        matched_attr = True
                                                        break

                                                if not matched_attr:
                                                    matches_all = False
                                                    break

                                            if matches_all:
                                                default_variation = var
                                                logger.info(f"🎯 Matched Woo default_attributes variation: {var['id']}")
                                                break
                                except Exception as e:
                                    logger.warning(f"🎯 Failed to resolve variation from Woo default_attributes: {e}")

                                if not default_variation:
                                    def variation_menu_order(v):
                                        try:
                                            return int(v.get('menu_order') or 0)
                                        except Exception:
                                            return 0

                                    sorted_variations = sorted(variations, key=variation_menu_order)
                                    default_variation = sorted_variations[0]
                                    logger.info(
                                        f"🎯 No explicit default variation found; using lowest menu_order: {default_variation['id']}"
                                    )
                                
                                # 🔥 CRITICAL FIX: Store parent product ID before overwriting
                                parent_product_id = product_id  # Store original parent product ID
                                variation_id = default_variation['id']  # Store variation ID separately
                                
                                # Get parent product name
                                parent_response = wc.get_product(parent_product_id)
                                if parent_response and 'data' in parent_response:
                                    parent_name = parent_response['data'].get('name', item.name)
                                    
                                    # Build variation name with attributes
                                    attributes = default_variation.get('attributes', [])
                                    attr_parts = []
                                    for attr in attributes:
                                        if attr.get('option'):
                                            attr_parts.append(attr['option'])
                                    
                                    if attr_parts:
                                        product_name_override = f"{parent_name} - {' - '.join(attr_parts)}"
                                        logger.info(f"🎯 Using default variation: ID={variation_id}, Name={product_name_override}")
                                    else:
                                        product_name_override = parent_name
                                        logger.info(f"🎯 Using default variation with parent name: {product_name_override}")
                                    
                                    # 🔥 CRITICAL FIX: Keep parent product ID, variation ID already set
                                    product_id = parent_product_id  # Keep parent product ID
                                    logger.info(f"🎯 Default variation resolved: parent_id={product_id}, variation_id={variation_id}")
                                else:
                                    logger.warning(f"🎯 Could not fetch parent product {product_id}")
                            else:
                                logger.info(f"🎯 No variations found for product {product_id}, using parent product")
                        except Exception as e:
                            logger.error(f"🎯 Error checking variations for product {product_id}: {str(e)}")
                            # Continue with original product_id
                
                    # 🔥 CRITICAL FIX: Handle bundle items with UUID product_id but variation_selection in metadata
                    elif is_bundle_item and metadata and metadata.get('variation_selection'):
                        variation_selection = metadata.get('variation_selection')
                        logger.info(f"🔥 BUNDLE VARIATION: Processing bundle item with UUID product_id but variation_selection: {variation_selection}")
                        
                        # This is a bundle item with a real WooCommerce variation ID
                        if variation_selection and str(variation_selection).isdigit():
                            try:
                                variation_id_int = int(variation_selection)
                                logger.info(f"🔥 BUNDLE VARIATION: Fetching variation details for ID: {variation_id_int}")
                                
                                # Get the variation directly using the variation ID
                                variation_response = wc.get_product_variation(variation_id_int)
                                logger.info(f"🔥 BUNDLE VARIATION: Response received: {variation_response}")
                                
                                if variation_response and 'data' in variation_response:
                                    variation_data = variation_response['data']
                                    # Get the parent product name
                                    parent_id = variation_data.get('parent_id')
                                    if parent_id:
                                        parent_response = wc.get_product(parent_id)
                                        if parent_response and 'data' in parent_response:
                                            parent_name = parent_response['data'].get('name', item.name)
                                            
                                            # Build variation name with attributes
                                            attributes = variation_data.get('attributes', [])
                                            attr_parts = []
                                            for attr in attributes:
                                                if attr.get('option'):
                                                    attr_parts.append(attr['option'])
                                            
                                            if attr_parts:
                                                product_name_override = f"{parent_name} - {' - '.join(attr_parts)}"
                                                logger.info(f"🔥 BUNDLE VARIATION: Name resolved: {product_name_override}")
                                            else:
                                                product_name_override = parent_name
                                                logger.info(f"🔥 BUNDLE VARIATION: Using parent name: {product_name_override}")
                                            
                                            # 🔥 CRITICAL FIX: Set parent product ID and variation ID separately
                                            product_id = parent_id  # Parent product ID
                                            variation_id = variation_id_int  # Actual variation ID
                                            logger.info(f"🔥 BUNDLE VARIATION SUCCESS: parent_id={product_id}, variation_id={variation_id}")
                                else:
                                    logger.error(f"🔥 BUNDLE VARIATION FAILED: No data in response for variation {variation_id_int}")
                                    logger.error(f"🔥 BUNDLE VARIATION FAILED: Full response: {variation_response}")
                            except Exception as e:
                                logger.error(f"🔥 BUNDLE VARIATION ERROR: Error fetching variation details: {str(e)}")
                    
                    else:
                        # 🔥 SKU FIX: Try metadata SKU first (actual product SKU like "PROD1010"),
                        # then fall back to item.product_id (which is a UUID — unlikely to match any SKU)
                        meta_sku = metadata.get('sku', '') if metadata and isinstance(metadata, dict) else ''
                        if meta_sku:
                            logger.info(f"🔍 SKU LOOKUP: Searching WooCommerce by metadata SKU: '{meta_sku}' for item '{item.name}'")
                            product = wc.get_product_by_sku(meta_sku)
                            if product and 'id' in product:
                                product_id = product['id']
                                logger.info(f"✅ SKU MATCH: Found product ID {product_id} for SKU '{meta_sku}'")
                            else:
                                logger.warning(f"⚠️ SKU LOOKUP FAILED: No WooCommerce product found with SKU '{meta_sku}'")
                        
                        if not product_id:
                            # Fallback: Try item.product_id as SKU (legacy behavior, usually a UUID so rarely matches)
                            logger.info(f"Searching for product with SKU: {item.product_id}")
                            product = wc.get_product_by_sku(item.product_id)
                            if product and 'id' in product:
                                product_id = product['id']
                                logger.info(f"Found product with ID: {product_id} for SKU: {item.product_id}")
        
            # If we still don't have a valid product ID, try to find by name before creating a new product
            if not product_id:
                logger.warning(f"⚠️ No product ID resolved for '{item.name}' (product_id={item.product_id}). "
                              f"Falling back to name search. metadata={metadata}")
                # Search for products with the same name
                search_result = wc.search_products(item.name)
                if search_result and 'data' in search_result and search_result['data']:
                    # Check if any of the search results match the name exactly
                    exact_matches = [prod for prod in search_result['data']
                                     if prod['name'].lower() == item.name.lower()]
                    if len(exact_matches) > 1:
                        # 🔥 SKU DISAMBIGUATION: When multiple products share the same name,
                        # use SKU from metadata to pick the correct one
                        meta_sku = metadata.get('sku', '') if metadata and isinstance(metadata, dict) else ''
                        if meta_sku:
                            sku_match = [p for p in exact_matches if p.get('sku', '') == meta_sku]
                            if sku_match:
                                product_id = sku_match[0]['id']
                                logger.info(
                                    f"✅ SKU DISAMBIGUATION: Resolved ambiguous name '{item.name}' using SKU '{meta_sku}' -> product ID {product_id} "
                                    f"(out of {len(exact_matches)} matches: {[(p['id'], p.get('sku', '')) for p in exact_matches]})"
                                )
                            else:
                                logger.warning(
                                    f"⚠️ AMBIGUOUS NAME MATCH: {len(exact_matches)} WooCommerce products named '{item.name}': "
                                    f"{[(p['id'], p.get('sku', '')) for p in exact_matches]}. "
                                    f"SKU '{meta_sku}' didn't match any. Using first match."
                                )
                        else:
                            logger.warning(
                                f"⚠️ AMBIGUOUS NAME MATCH: {len(exact_matches)} WooCommerce products named '{item.name}': "
                                f"{[(p['id'], p.get('sku', '')) for p in exact_matches]}. "
                                f"No SKU in metadata to disambiguate. Using first match."
                            )
                    if exact_matches and not product_id:
                        product_id = exact_matches[0]['id']
                        logger.info(f"Found existing product with ID: {product_id} for name: {item.name}")
            
            # 🔥 CRITICAL FIX: Don't create new products for bundle items with variations
            # Only create a new product if we absolutely cannot find an existing one AND it's not a bundle item
            if not product_id:
                # Check if this is a bundle item with variation data
                is_bundle_with_variation = (is_bundle_item and metadata and 
                                          metadata.get('variation_selection'))
                
                if is_bundle_with_variation:
                    # For bundle items with variations, try to find the actual variation ID
                    variation_selection = metadata.get('variation_selection')
                    if variation_selection and '_pa_' in str(variation_selection):
                        try:
                            # Extract parent product ID and attribute from synthetic variation ID
                            variation_parts = str(variation_selection).split('_pa_')
                            parent_product_id = int(variation_parts[0])
                            attribute_info = variation_parts[1] if len(variation_parts) > 1 else ''
                            
                            logger.info(f"🔥 Bundle variation: parent_id={parent_product_id}, attribute_info={attribute_info}")
                            
                            # Try to find the actual variation ID by matching attributes
                            variations_response = wc.get_product_variations(parent_product_id)
                            if variations_response and 'data' in variations_response:
                                variations = variations_response['data']
                                logger.info(f"🔥 Found {len(variations)} variations for parent {parent_product_id}")
                                
                                # Try to match the variation by attribute
                                matched_variation = None
                                for variation in variations:
                                    attributes = variation.get('attributes', [])
                                    for attr in attributes:
                                        attr_option = str(attr.get('option', '')).lower()
                                        # Match common attribute values
                                        if (attribute_info.lower() in attr_option or 
                                            'single' in attr_option or 
                                            '3' in attribute_info and '3' in attr_option):
                                            matched_variation = variation
                                            logger.info(f"🔥 Matched variation {variation['id']} by attribute: {attr_option}")
                                            break
                                    if matched_variation:
                                        break
                                
                                if matched_variation:
                                    # 🔥 CRITICAL FIX: Keep parent product ID and set variation ID separately
                                    product_id = parent_product_id  # Parent product ID
                                    variation_id = matched_variation['id']  # Actual variation ID
                                    logger.info(f"🔥 Bundle variation resolved: parent_id={product_id}, variation_id={variation_id} for {item.name}")
                                else:
                                    # Fallback to first variation if no exact match
                                    if variations:
                                        product_id = parent_product_id  # Parent product ID
                                        variation_id = variations[0]['id']  # First variation ID
                                        logger.info(f"🔥 Bundle variation fallback: parent_id={product_id}, variation_id={variation_id} for {item.name}")
                                    else:
                                        # Last resort: use parent product ID only (simple product)
                                        product_id = parent_product_id
                                        variation_id = None
                                        logger.warning(f"🔥 Bundle variation fallback: Using parent product ID {product_id} as simple product for {item.name}")
                            else:
                                # Fallback to parent product ID if variations can't be fetched
                                product_id = parent_product_id
                                variation_id = None
                                logger.warning(f"🔥 Could not fetch variations for {parent_product_id}, using parent ID as simple product")
                                
                        except (ValueError, IndexError) as e:
                            logger.warning(f"🔥 Could not extract parent ID from {variation_selection}: {e}")
                
                # Only create new product if it's not a bundle item with variation
                if not product_id and not is_bundle_with_variation:
                    logger.info(f"Creating a simple product for item: {item.name}")
                    product_data = {
                        'name': item.name,
                        'type': 'simple',
                        'regular_price': str(float(item.price)),
                        'description': f"Product created from POS order {pos_order.order_number}",
                        'short_description': item.name,
                        'categories': [{'id': 1}],  # Default category
                        'status': 'publish'
                    }
                    result = wc.create_product(product_data)
                    if result.get('status') == 'success' and 'data' in result and 'id' in result['data']:
                        product_id = result['data']['id']
                        logger.info(f"Created new product with ID: {product_id}")
                    else:
                        logger.error(f"Failed to create product: {result.get('message', 'Unknown error')}")
                        product_id = 0  # Fallback
                elif not product_id:
                    logger.warning(f"🔥 Could not resolve product ID for bundle item with variation: {item.name}")
                    product_id = 0  # Fallback
        
            # Check if this is a subscription product using frontend subscription_items parameter
            is_subscription = False
            subscription_detection_source = 'none'  # 🔥 Track HOW subscription was detected
            subscription_period = 'month'  # FIXED: Default to monthly for memberships
            subscription_interval = 1      # FIXED: Default to 1 month interval
            
            # 🔥 DIAGNOSTIC: Log all detection inputs for this item
            logger.info(f"🔍 SUBSCRIPTION DETECTION for '{item.name}' (product_id={item.product_id}):")
            logger.info(f"   frontend_subscription_items: {frontend_subscription_items}")
            logger.info(f"   item.product_id type: {type(item.product_id).__name__}, value: '{item.product_id}'")
            logger.info(f"   metadata keys: {list(metadata.keys()) if metadata and isinstance(metadata, dict) else 'N/A'}")
            if metadata and isinstance(metadata, dict):
                logger.info(f"   metadata.subscription_selected: {metadata.get('subscription_selected')}")
                logger.info(f"   metadata.subscription (truthy): {bool(metadata.get('subscription'))}")
                logger.info(f"   metadata.subscription_billing_period: {metadata.get('subscription_billing_period')}")
            
            # First priority: Check if this product is in the frontend_subscription_items list
            if frontend_subscription_items and item.product_id in frontend_subscription_items:
                is_subscription = True
                subscription_detection_source = 'frontend_subscription_items (exact match)'
                logger.info(f"✅ Product '{item.name}' (ID: {item.product_id}) marked as subscription by frontend")
            
            # 🔥 FIX: Also try matching by str() conversion in case of type mismatch
            if not is_subscription and frontend_subscription_items:
                for fsi in frontend_subscription_items:
                    if str(fsi) == str(item.product_id):
                        is_subscription = True
                        subscription_detection_source = 'frontend_subscription_items (str match)'
                        logger.info(f"✅ Product '{item.name}' matched by str conversion: '{fsi}' == '{item.product_id}'")
                        break
        
            # Default subscription settings for membership products
            subscription_period = 'month'  # FIXED: Default to monthly for memberships
            subscription_interval = 1      # FIXED: Default to 1 month interval
            
            # Try to get subscription details from database if available
            db_product = None
            try:
                from .models import Product, ProductSubscription
                
                # Try to find the product in our database by product_id or name
                if hasattr(item, 'sku') and item.sku:
                    db_product = Product.objects.filter(sku=item.sku).first()
                if not db_product:
                    # Try by UUID product_id or numeric WooCommerce product_id
                    pid = str(item.product_id).strip()
                    if pid.isdigit():
                        db_product = Product.objects.filter(woo_product_id=int(pid)).first()
                    else:
                        try:
                            import uuid as uuid_mod
                            uuid_mod.UUID(pid)
                            db_product = Product.objects.filter(id=pid).first()
                        except ValueError:
                            pass
                if not db_product:
                    # Try to find by name (case insensitive)
                    db_product = Product.objects.filter(name__iexact=item.name).first()
                
                # If product found in database, get subscription details
                if db_product:
                    logger.info(f"Found product in database: {db_product.name} (ID: {db_product.id})")
                    
                    # 🎨 Check if this is a Divi product
                    is_divi_product = False
                    from .models import ProductSimple
                    product_simple = ProductSimple.objects.filter(product=db_product).first()
                    if product_simple and product_simple.woo_data:
                        subscription_metadata = product_simple.woo_data.get('subscription_metadata', {})
                        divi_metadata = product_simple.woo_data.get('divi_metadata', {})
                        is_divi_product = subscription_metadata.get('is_divi_managed', False) or bool(divi_metadata)
                        
                        # 🔥 FIX: Check for WCSATT subscription schemes in woo_data
                        # This detects Subscribe & Save products even if frontend didn't flag them
                        if not is_subscription and not is_divi_product:
                            wcsatt_schemes = product_simple.woo_data.get('subscription_options', [])
                            meta_data = product_simple.woo_data.get('meta_data', [])
                            # Check meta_data for _wcsatt_schemes
                            for md in (meta_data if isinstance(meta_data, list) else []):
                                if md.get('key') == '_wcsatt_schemes' and md.get('value'):
                                    wcsatt_schemes = md['value']
                                    break
                            
                            if wcsatt_schemes and isinstance(wcsatt_schemes, list) and len(wcsatt_schemes) > 0:
                                # This product has WCSATT subscription schemes
                                # Only mark as subscription if metadata indicates user selected Subscribe & Save
                                if metadata and isinstance(metadata, dict):
                                    sub_discount_pct = metadata.get('subscription_discount_percentage', 0)
                                    if sub_discount_pct and float(sub_discount_pct) > 0:
                                        is_subscription = True
                                        subscription_detection_source = 'WCSATT schemes + subscription_discount_percentage in metadata'
                                        # Try to get period/interval from the WCSATT scheme
                                        scheme = wcsatt_schemes[0]
                                        if isinstance(scheme, dict):
                                            subscription_period = scheme.get('subscription_period', 'month')
                                            subscription_interval = int(scheme.get('subscription_period_interval', 1))
                                        logger.info(f"✅ WCSATT DETECTION: '{item.name}' has subscription schemes AND discount applied -> marking as subscription (period={subscription_period}, interval={subscription_interval})")
                    
                    if is_divi_product:
                        # 🎨 Divi product - use default subscription values
                        # Divi handles subscription creation at checkout
                        logger.info(f"🎨 Divi product detected: {db_product.name} - using default subscription values (monthly)")
                        subscription_period = 'month'
                        subscription_interval = 1
                    else:
                        # Check if there's subscription data for this product
                        subscription = ProductSubscription.objects.filter(product=db_product).first()
                        if subscription:
                            subscription_period = subscription.period
                            subscription_interval = subscription.interval
                            logger.info(f"Found subscription data in database: period={subscription_period}, interval={subscription_interval}")
                        else:
                            logger.info(f"No subscription data found in database for {db_product.name}, using defaults")
                else:
                    logger.info(f"Product not found in database, using default subscription settings")
            except Exception as e:
                logger.error(f"Error checking product subscription status in database: {str(e)}")
            
            # 🔥 BUNDLE → SUBSCRIPTION FALLBACK: If this is a bundle child item and the resolved
            # product in the database is a subscription/membership product, mark it as a subscription.
            # This is a safety net in case the frontend metadata flags are missing.
            # The primary detection path is via frontend metadata (is_bundle_subscription_item),
            # which is set during bundle expansion in OrderContext.tsx.
            if not is_subscription and is_bundle_item and db_product:
                if db_product.product_type in ('subscription', 'variable_subscription', 'variable-subscription'):
                    is_subscription = True
                    subscription_detection_source = f'bundle_child_db_product_type ({db_product.product_type})'
                    logger.info(f"✅ BUNDLE → SUBSCRIPTION FALLBACK: '{item.name}' is a bundle child with DB product_type='{db_product.product_type}' -> marking as subscription")
                    # Try to get subscription period/interval from ProductSubscription table
                    try:
                        bundle_sub = ProductSubscription.objects.filter(product=db_product).first()
                        if bundle_sub:
                            subscription_period = bundle_sub.period
                            subscription_interval = bundle_sub.interval
                            logger.info(f"   Using DB subscription data: period={subscription_period}, interval={subscription_interval}")
                    except Exception as e:
                        logger.warning(f"   Could not fetch ProductSubscription for bundle child: {e}")
            
            # Fallback: Check metadata for subscription information
            if not is_subscription and metadata and isinstance(metadata, dict):
                # 🔄 NEW: Check for subscription data from frontend metadata
                if metadata.get('subscription_selected') or metadata.get('subscription'):
                    is_subscription = True
                    subscription_detection_source = f"metadata (subscription_selected={metadata.get('subscription_selected')}, subscription={bool(metadata.get('subscription'))})"
                    
                    # Use subscription period/interval from metadata if available
                    if metadata.get('subscription_billing_period'):
                        subscription_period = metadata.get('subscription_billing_period')
                        subscription_interval = metadata.get('subscription_billing_interval', 1)
                        logger.info(f"🔄 SUBSCRIPTION from metadata: '{item.name}' -> {subscription_period} every {subscription_interval}")
                    elif metadata.get('subscription_billing_frequency'):
                        billing_frequency = metadata.get('subscription_billing_frequency')
                        if billing_frequency == 'monthly':
                            subscription_period = 'month'
                            subscription_interval = 1
                        elif billing_frequency == 'weekly':
                            subscription_period = 'week' 
                            subscription_interval = 1
                        elif billing_frequency == 'biweekly':
                            subscription_period = 'week'
                            subscription_interval = 2
                        elif billing_frequency == 'bimonthly':
                            subscription_period = 'month'
                            subscription_interval = 2
                        elif billing_frequency == 'quarterly':
                            subscription_period = 'month'
                            subscription_interval = 3
                        elif billing_frequency == 'yearly':
                            subscription_period = 'year'
                            subscription_interval = 1
                        logger.info(f"🔄 SUBSCRIPTION from billing_frequency: '{item.name}' -> {billing_frequency} mapped to {subscription_period} every {subscription_interval}")
                
                # Legacy metadata support
                if metadata.get('is_subscription'):
                    subscription_period = metadata.get('subscription_period', subscription_period)
                    subscription_interval = metadata.get('subscription_interval', subscription_interval)
                    logger.info(f"Product '{item.name}' subscription settings from metadata: period={subscription_period}, interval={subscription_interval}")
                
                # 🆕 TRIAL MEMBERSHIP: Check for trial membership metadata
                if metadata.get('isTrialMembership'):
                    is_subscription = True
                    trial_period_months = metadata.get('trialPeriodMonths', 0)
                    actual_membership_price = metadata.get('actualMembershipPrice', item.subtotal)
                    logger.info(f"🆕 TRIAL MEMBERSHIP detected: {item.name}, trial period: {trial_period_months} months, actual price: {actual_membership_price}")
                    
                    # For trial memberships, use monthly billing but with trial period
                    subscription_period = 'month'
                    subscription_interval = 1
                
                # 🔧 FIX: Regular Monthly Membership (no trial) - charge immediately, then monthly billing
                if metadata.get('isRegularMonthlyMembership'):
                    is_subscription = True
                    actual_membership_price = metadata.get('actualMembershipPrice', item.subtotal)
                    logger.info(f"🔧 REGULAR MONTHLY MEMBERSHIP detected: {item.name}, price: {actual_membership_price} (no trial)")
                    
                    # Only override period/interval if explicitly provided in metadata.
                    # For bundle children, these are NOT set — the DB-resolved values from
                    # ProductSubscription (set earlier) should be preserved.
                    # For standalone memberships, ProductSummaryModal.tsx always sets these explicitly.
                    if metadata.get('subscriptionPeriod'):
                        subscription_period = metadata['subscriptionPeriod']
                    if metadata.get('subscriptionInterval'):
                        subscription_interval = metadata['subscriptionInterval']
            
            # 🔧 CRITICAL FIX: Always apply metadata billing period/interval when present
            # The metadata from the frontend contains the CORRECT period for the selected
            # variation (e.g. "year" for 3408.60/year), but the DB lookup above may have
            # already set subscription_period to the PARENT product's period ("month").
            # This override ensures the variation-specific period is used.
            if metadata and isinstance(metadata, dict):
                if metadata.get('subscription_billing_period'):
                    old_period = subscription_period
                    subscription_period = metadata.get('subscription_billing_period')
                    subscription_interval = metadata.get('subscription_billing_interval', subscription_interval)
                    if old_period != subscription_period:
                        logger.info(f"🔧 PERIOD OVERRIDE from frontend metadata: '{item.name}' -> changed from '{old_period}' to '{subscription_period}' (interval={subscription_interval})")
            
            # 🆕 SUBSCRIPTION FIX: Check for subscription option from frontend
            # Look for subscription data in the POSOrderItem
            try:
                if hasattr(item, 'subscription') and item.subscription:
                    # Extract billing information from selected subscription option
                    subscription_option = item.subscription
                    logger.info(f"🔄 Found subscription option data from frontend: {subscription_option}")
                    
                    # 🔥 PRIORITY FIX: Check for direct period/interval fields first (supports custom intervals like 6 weeks)
                    # This is needed because billingFrequency only supports predefined values (weekly, biweekly, monthly, etc.)
                    # but products can have custom intervals like "Every 4 weeks" or "Every 6 weeks"
                    if 'period' in subscription_option and 'interval' in subscription_option:
                        subscription_period = subscription_option.get('period')
                        subscription_interval = subscription_option.get('interval')
                        is_subscription = True
                        logger.info(f"🔥 Using DIRECT period/interval from frontend: period={subscription_period}, interval={subscription_interval}")
                    else:
                        # Fallback: Map billingFrequency to period and interval (for backward compatibility)
                        billing_frequency = subscription_option.get('billingFrequency', 'monthly')
                        logger.info(f"🔄 Subscription billing frequency: {billing_frequency}")
                        
                        if billing_frequency == 'monthly':
                            subscription_period = 'month'
                            subscription_interval = 1
                            is_subscription = True
                        elif billing_frequency == 'weekly':
                            subscription_period = 'week'
                            subscription_interval = 1
                            is_subscription = True
                        elif billing_frequency == 'biweekly':
                            subscription_period = 'week'
                            subscription_interval = 2
                            is_subscription = True
                        elif billing_frequency == 'bimonthly':
                            subscription_period = 'month'
                            subscription_interval = 2
                            is_subscription = True
                        elif billing_frequency == 'quarterly':
                            subscription_period = 'month'
                            subscription_interval = 3
                            is_subscription = True
                        elif billing_frequency == 'yearly':
                            subscription_period = 'year'
                            subscription_interval = 1
                            is_subscription = True
                        
                        logger.info(f"🔄 Mapped from billingFrequency: period={subscription_period}, interval={subscription_interval}")
                    
                # Alternative: Check metadata for subscription option data
                elif 'subscription' in metadata and metadata['subscription']:
                    subscription_option = metadata['subscription']
                    logger.info(f"🔄 Found subscription option in metadata: {subscription_option}")
                    
                    # 🔥 PRIORITY FIX: Check for direct period/interval fields first (supports custom intervals like 6 weeks)
                    if 'period' in subscription_option and 'interval' in subscription_option:
                        subscription_period = subscription_option.get('period')
                        subscription_interval = subscription_option.get('interval')
                        is_subscription = True
                        logger.info(f"🔥 Using DIRECT period/interval from metadata: period={subscription_period}, interval={subscription_interval}")
                    else:
                        # Fallback: Map billingFrequency to period and interval
                        billing_frequency = subscription_option.get('billingFrequency', 'monthly')
                        logger.info(f"🔄 Subscription billing frequency from metadata: {billing_frequency}")
                        
                        if billing_frequency == 'monthly':
                            subscription_period = 'month'
                            subscription_interval = 1
                            is_subscription = True
                        elif billing_frequency == 'weekly':
                            subscription_period = 'week'
                            subscription_interval = 1
                            is_subscription = True
                        elif billing_frequency == 'biweekly':
                            subscription_period = 'week'
                            subscription_interval = 2
                            is_subscription = True
                        elif billing_frequency == 'bimonthly':
                            subscription_period = 'month'
                            subscription_interval = 2
                            is_subscription = True
                        elif billing_frequency == 'quarterly':
                            subscription_period = 'month'
                            subscription_interval = 3
                            is_subscription = True
                        elif billing_frequency == 'yearly':
                            subscription_period = 'year'
                            subscription_interval = 1
                            is_subscription = True
                        
                        logger.info(f"🔄 Mapped subscription from metadata: period={subscription_period}, interval={subscription_interval}")
                    
            except Exception as e:
                logger.error(f"Error processing subscription option data: {str(e)}")
        
            # CRITICAL LOGGING: Track subscription period resolution
            if is_subscription:
                if subscription_period == 'month' and subscription_interval == 1:
                    if hasattr(item, 'subscription') and item.subscription:
                        logger.info(f"✅ SUBSCRIPTION MAPPED: '{item.name}' -> Monthly from frontend selection")
                    else:
                        logger.warning(f"⚠️ SUBSCRIPTION DEFAULT: '{item.name}' -> Monthly from default (frontend mapping may have failed)")
                elif subscription_period == 'week' and subscription_interval == 2:
                    logger.warning(f"🚨 SUBSCRIPTION BIWEEKLY: '{item.name}' -> This may be incorrect for memberships!")
                else:
                    logger.info(f"ℹ️ SUBSCRIPTION CUSTOM: '{item.name}' -> {subscription_period} every {subscription_interval}")
            
            logger.info(f"📋 SUBSCRIPTION DETECTION RESULT for '{item.name}': is_subscription={is_subscription}, detection_source='{subscription_detection_source}', period={subscription_period}, interval={subscription_interval}")
        
            # 🔥🔥🔥 BUNDLE ITEM PROCESSING: Handle bundle items at original prices
            logger.info(f"🔥🔥🔥 DEBUGGING WooCommerce Item: '{item.name}'")
            logger.info(f"   Raw POS item data from database:")
            logger.info(f"     item.price: ${float(item.price):.2f}")
            logger.info(f"     item.subtotal: ${float(item.subtotal):.2f}")
            logger.info(f"     item.quantity: {item.quantity}")
            logger.info(f"     item.original_price: {getattr(item, 'original_price', 'None')}")
            logger.info(f"     item.discount_amount: {getattr(item, 'discount_amount', 'None')}")
            logger.info(f"     item.discount_source: {getattr(item, 'discount_source', 'None')}")
            logger.info(f"     item.metadata: {getattr(item, 'metadata', 'None')}")
            logger.info(f"     is_bundle_item: {is_bundle_item}")
            
            # ✅ BUNDLE ITEMS: Use original prices, discount will be applied at order level
            if is_bundle_item:
                # For bundle items, use original prices (no item-level discount)
                original_item_price = float(item.price)  # This is the original price
                item_subtotal = float(item.subtotal)  # This is the original subtotal
                item_unit_price = original_item_price  # Use original price
                
                logger.info(f"🔥 Bundle item - using original prices:")
                logger.info(f"   Original price per unit: ${original_item_price:.2f}")
                logger.info(f"   Original subtotal: ${item_subtotal:.2f}")
                logger.info(f"   Bundle discount will be applied at order level")
            else:
                # ✅ REGULAR ITEMS: Use discounted prices from POS order items
                # CRITICAL: For subscription products, we need to calculate the subscription price as the baseline
                # POS stores ORIGINAL PRODUCT price in item.price and FINAL DISCOUNTED subtotal in item.subtotal
                
                item_subtotal = float(item.subtotal)  # This is the FINAL DISCOUNTED subtotal
                item_unit_price = item_subtotal / item.quantity  # Calculate final discounted price per unit
                
                # 🔥 SUBSCRIPTION PRICING FIX: Calculate correct original price for subscription products
                if is_subscription and metadata:
                    # For subscription products, calculate the subscription price (before line item discounts)
                    # Check if we have subscription discount information in metadata
                    subscription_discount_percentage = metadata.get('subscription_discount_percentage', 0)
                    line_item_discount = metadata.get('line_item_discount', 0)
                    
                    if subscription_discount_percentage > 0:
                        # Calculate subscription price: regular_price * (1 - subscription_discount_percentage / 100)
                        regular_product_price = float(item.price)
                        subscription_price = regular_product_price * (1 - subscription_discount_percentage / 100)
                        original_item_price = subscription_price  # Use subscription price as baseline
                        
                        logger.info(f"🔥 SUBSCRIPTION ITEM - pricing calculation:")
                        logger.info(f"   Regular product price: ${regular_product_price:.2f}")
                        logger.info(f"   Subscription discount: {subscription_discount_percentage}%")
                        logger.info(f"   Subscription price (baseline): ${subscription_price:.2f}")
                        logger.info(f"   Final price after line discounts: ${item_unit_price:.2f}")
                        logger.info(f"   Line item discount: ${(subscription_price - item_unit_price):.2f}")
                    else:
                        # Fallback: try to reverse-engineer from POS data
                        # If no explicit discount percentage, assume item.price is subscription price
                        original_item_price = float(item.price)
                        logger.info(f"🔥 SUBSCRIPTION ITEM - using item.price as baseline: ${original_item_price:.2f}")
                else:
                    # Regular non-subscription item
                    original_item_price = float(item.price)  # This is the ORIGINAL product price
                
                logger.info(f"🔥 Item pricing - final calculated values:")
                logger.info(f"   Original/baseline price per unit: ${original_item_price:.2f}")
                logger.info(f"   Final subtotal: ${item_subtotal:.2f}")
                logger.info(f"   Final price per unit: ${item_unit_price:.2f}")
                logger.info(f"   Total discount amount: ${(original_item_price - item_unit_price):.2f}")
                logger.info(f"   Is subscription: {is_subscription}")
            
            # Create the line item
            # 🎯 Use variation name if we fetched it, otherwise use item name
            item_display_name = product_name_override if product_name_override else item.name
            
            # 🔥 ITEM-LEVEL DISCOUNT FIX: Calculate subtotal before discount for WooCommerce
            subtotal_before_discount = original_item_price * item.quantity
            
            line_item = {
                'name': item_display_name + bundle_parent_info,  # Use variation name if available
                'product_id': product_id,
                'quantity': item.quantity,
                # 🔥 CRITICAL: Use WooCommerce subtotal/total correctly:
                #   subtotal = line total BEFORE item discount (qty × original unit price)
                #   total    = line total AFTER item discount
                # WooCommerce computes discount as (subtotal - total), avoiding per-unit rounding errors
                'subtotal': f"{subtotal_before_discount:.2f}",  # Before item discount
                'total': f"{item_subtotal:.2f}",                # After item discount
                'meta_data': []
            }
            
            logger.info(f"🔥 WooCommerce line item pricing for {item.name}:")
            logger.info(f"   original unit price: ${original_item_price:.2f}")
            logger.info(f"   subtotal (before discount): ${subtotal_before_discount:.2f}")
            logger.info(f"   total (after discount): ${item_subtotal:.2f}")
            logger.info(f"   discount amount: ${(subtotal_before_discount - item_subtotal):.2f}")
            
            # 🔥 CRITICAL: Add variation_id for variable products
            if variation_id:
                line_item['variation_id'] = variation_id
                logger.info(f"🔥 Added variation_id {variation_id} to line item for {item.name}")
            
            # 🔥 NEW: Check if this is a credited service and add meta data for WordPress snippet
            credited_item = None
            for credited in credited_items:
                if (str(credited.get('productId')) == str(item.product_id) or 
                    str(credited.get('productId')) == str(product_id)):
                    credited_item = credited
                    break
            
            if credited_item:
                logger.info(f"💳 Adding credited service meta data for: {item.name}")
                # Add meta data for WordPress snippet to force $0 pricing
                line_item['meta_data'].extend([
                    {'key': 'ds_is_credited_service', 'value': '1'},
                    {'key': 'ds_credited_service_id', 'value': str(credited_item.get('creditedServiceId', ''))},
                    {'key': 'ds_original_product_id', 'value': str(credited_item.get('metadata', {}).get('originalProductId', ''))},
                    {'key': 'ds_series_count', 'value': '1'}
                ])
                
                # Also force the pricing to $0 for credited services
                line_item['price'] = '0.00'
                line_item['total'] = '0.00'
                line_item['subtotal'] = '0.00'
                logger.info(f"💳 Forced pricing to $0.00 for credited service: {item.name}")
            
            # 🎯 ADD FULFILLMENT LOCATION + ATUM mi_inventories for correct stock deduction
            # ATUM Multi-Inventory uses mi_inventories on line items (NOT meta_data) to determine
            # which inventory record to deduct stock from.
            # API format: "mi_inventories": [{"inventory_id": 152, "qty": 2}]
            if hasattr(item, 'fulfillment_location') and item.fulfillment_location:
                line_item['meta_data'].append({
                    'key': '_fulfillment_location',
                    'value': item.fulfillment_location
                })
                # Query ATUM REST API to find the per-product inventory record ID
                try:
                    atum_product_id = variation_id if variation_id else product_id
                    atum_inventory_id = wc.find_atum_inventory_id_for_location(atum_product_id, item.fulfillment_location)
                    if atum_inventory_id:
                        # CRITICAL: Use mi_inventories field - this is what ATUM actually reads for stock deduction
                        # Official ATUM REST API format: [{"inventory_id": 152, "qty": 1}]
                        line_item['mi_inventories'] = [{
                            'inventory_id': atum_inventory_id,
                            'qty': item.quantity,
                        }]
                        logger.info(f"🎯 ATUM mi_inventories set: inventory_id={atum_inventory_id}, qty={item.quantity} for '{item.fulfillment_location}' (product {atum_product_id})")
                    else:
                        logger.warning(f"🎯 Could not resolve ATUM inventory record for '{item.fulfillment_location}' product {atum_product_id} - ATUM will use default priority")
                except Exception as e:
                    logger.warning(f"🎯 Error resolving ATUM inventory for '{item.fulfillment_location}': {e} - ATUM will use default priority")
            # Store needs_shipping flag as WooCommerce meta for shipment tracking logic
            if hasattr(item, 'needs_shipping'):
                line_item['meta_data'].append({
                    'key': '_needs_shipping',
                    'value': '1' if item.needs_shipping else '0'
                })

            # Store variation name for receipt display
            pos_variation_name = metadata.get('variation_name', '') if metadata else ''
            if pos_variation_name:
                line_item['meta_data'].append({
                    'key': '_ds_variation_name',
                    'value': pos_variation_name
                })
            
            logger.info(f"🎯 Line item created: name='{item_display_name}', product_id={product_id}")
            
            # Add bundle metadata to WooCommerce line item if this is a bundle item
            # 🎯 ONLY show actual variation attributes (no series metadata)
            if is_bundle_item and metadata:
                bundle_meta = []
                
                # Add variation info for variable products (duration, period, etc.)
                variation_selection = metadata.get('variation_selection')
                if variation_selection:
                    variation_id_str = str(variation_selection)
                    
                    # Parse the variation to show user-friendly info
                    if '_pa_' in variation_id_str:
                        parts = variation_id_str.split('_pa_')
                        if len(parts) > 1:
                            attribute_part = parts[1]  # e.g., "duration_1", "period_1", "flavor_1"
                            
                            if attribute_part.startswith('duration_') or attribute_part.startswith('period_'):
                                # Show duration/period info
                                duration_value = metadata.get('duration_selection') or metadata.get('period_selection')
                                if not duration_value:
                                    duration_index = int(attribute_part.split('_')[1])
                                    duration_mappings = {1: '1 month', 2: '3 months', 3: '6 months', 4: '12 months'}
                                    duration_value = duration_mappings.get(duration_index, '1 month')
                                
                                bundle_meta.append({
                                    'key': 'Duration',
                                    'value': str(duration_value)
                                })
                            elif attribute_part.startswith('flavor_'):
                                # Show flavor info
                                flavor_value = metadata.get('flavor_selection')
                                if not flavor_value:
                                    # Try to get from common flavor mappings
                                    flavor_index = int(attribute_part.split('_')[1]) if len(attribute_part.split('_')) > 1 else 1
                                    flavor_mappings = {1: 'Creamy Chocolate', 2: 'Vanilla', 3: 'Strawberry'}
                                    flavor_value = flavor_mappings.get(flavor_index, 'Default Flavor')
                                
                                bundle_meta.append({
                                    'key': 'Flavor',
                                    'value': str(flavor_value)
                                })
                            elif not attribute_part.startswith('series_'):
                                # Show other variation types (skip series completely)
                                attr_parts = attribute_part.split('_')
                                attr_type = attr_parts[0].title()
                                attr_value = metadata.get(f'{attr_parts[0]}_selection')
                                
                                # Only add if we have a meaningful value
                                if attr_value:
                                    bundle_meta.append({
                                        'key': attr_type,
                                        'value': str(attr_value)
                                    })
                elif product_name_override and ' - ' in product_name_override:
                    # If we auto-selected a variation (product_name_override contains variation info)
                    # but don't have variation_selection in metadata, extract variation info from name
                    variation_part = product_name_override.split(' - ')[-1]  # Get last part after " - "
                    if variation_part and variation_part != item.name and variation_part.lower() != 'single':
                        bundle_meta.append({
                            'key': 'Variation',
                            'value': variation_part
                        })
                        logger.info(f"🎯 Added auto-selected variation info: {variation_part}")
                
                # Only add metadata if we have actual variation info to show
                if bundle_meta:
                    line_item['meta_data'] = bundle_meta
                    logger.info(f"🔥 Added bundle metadata to line item: {bundle_meta}")
                else:
                    logger.info(f"🔥 No variation metadata to add for bundle item")
            # 🎯 NEW BUNDLE STRUCTURE: Separate bundle items from regular items
            if is_bundle_item and metadata:
                bundle_parent_id = metadata.get('bundle_parent_id')
                bundle_parent_name = metadata.get('bundle_parent_name', 'Bundle Product')
                original_bundle_price = metadata.get('original_bundle_price', 0)
                
                # Track bundle parent (only once per bundle)
                if bundle_parent_id and bundle_parent_id not in bundle_parents:
                    # 🔥 Resolve bundle parent WooCommerce product ID from UUID
                    bundle_woo_product_id = 0
                    try:
                        from .models import Product as ProductModel
                        bundle_product = ProductModel.objects.filter(id=bundle_parent_id).first()
                        if bundle_product and bundle_product.woo_product_id:
                            bundle_woo_product_id = bundle_product.woo_product_id
                            logger.info(f"🎯 Resolved bundle parent WooCommerce ID: {bundle_woo_product_id} from UUID {bundle_parent_id}")
                        else:
                            logger.warning(f"🎯 Could not resolve WooCommerce ID for bundle parent UUID: {bundle_parent_id}")
                    except Exception as e:
                        logger.warning(f"🎯 Error resolving bundle parent WooCommerce ID: {e}")
                    
                    # 🎯 BUNDLE QUANTITY FIX: Read bundle_quantity from metadata to set correct qty
                    bundle_quantity = int(metadata.get('bundle_quantity', 1))
                    bundle_parents[bundle_parent_id] = {
                        'name': bundle_parent_name,
                        'price': float(original_bundle_price),
                        'product_id': bundle_woo_product_id,
                        'quantity': bundle_quantity
                    }
                    logger.info(f"🎯 Tracked bundle parent: {bundle_parent_name} - ${original_bundle_price} qty={bundle_quantity} (woo_id={bundle_woo_product_id})")
                
                # Modify line item for bundle child (set price to 0)
                # WooCommerce ignores 'price' field, so we need to force it with meta_data
                line_item['name'] = f"└─ {item_display_name}"  # Indent to show it's a child
                
                # Add bundle metadata to line item so we can identify which bundle it belongs to
                if 'meta_data' not in line_item:
                    line_item['meta_data'] = []
                
                # Force WooCommerce to use our custom pricing
                # NOTE: Use DS-prefixed keys to avoid triggering WC Product Bundles
                # recalculation hooks which override order totals.
                line_item['meta_data'].extend([
                    {
                        'key': '_ds_bundle_parent_id',
                        'value': bundle_parent_id
                    },
                    {
                        'key': '_ds_line_subtotal',
                        'value': '0.00'
                    },
                    {
                        'key': '_ds_line_total',
                        'value': '0.00'
                    },
                    {
                        'key': '_ds_bundle_child_item',
                        'value': 'true'
                    }
                ])
                
                # Set the totals (WooCommerce should respect these)
                line_item['subtotal'] = '0.00'
                line_item['total'] = '0.00'
                
                # Add to bundle children
                bundle_children.append(line_item)
                logger.info(f"🎯 Added bundle child: {item_display_name} at $0")
            else:
                # 🆕 TRIAL MEMBERSHIP: Add metadata for trial membership products
                if metadata and metadata.get('isTrialMembership'):
                    # Ensure meta_data exists
                    if 'meta_data' not in line_item:
                        line_item['meta_data'] = []
                    
                    # Add trial membership metadata for WooCommerce hook
                    line_item['meta_data'].extend([
                        {
                            'key': '_trial_membership',
                            'value': 'true'
                        },
                        {
                            'key': '_trial_period_months',
                            'value': str(metadata.get('trialPeriodMonths', 0))
                        },
                        {
                            'key': '_actual_membership_price',
                            'value': str(metadata.get('actualMembershipPrice', item.subtotal))
                        },
                        {
                            'key': '_checkout_price',
                            'value': '0.00'  # Always $0 for trial memberships
                        },
                        {
                            'key': '_line_subtotal_override',
                            'value': '0.00'
                        },
                        {
                            'key': '_line_total_override',
                            'value': '0.00'
                        }
                    ])
                    logger.info(f"🆕 TRIAL MEMBERSHIP: Added trial membership metadata to line item: {item.name}")
                    
                    # Ensure the line item pricing is set to $0
                    line_item['subtotal'] = '0.00'
                    line_item['total'] = '0.00'
                
                # 🆕 MODIFIED MEMBERSHIP PRICING: Add metadata for membership products with modified pricing
                elif metadata and (hasattr(item, 'original_price') and item.original_price is not None):
                    # Check if this is a membership product with modified pricing
                    is_membership_product = (
                        'membership' in item.name.lower() or 
                        'member' in item.name.lower() or 
                        'subscription' in item.name.lower()
                    )
                    
                    if is_membership_product and float(item.original_price) != float(item.price):
                        # Ensure meta_data exists
                        if 'meta_data' not in line_item:
                            line_item['meta_data'] = []
                        
                        # Calculate discount information
                        original_price = float(item.original_price)
                        current_price = item_unit_price  # This is already the discounted price
                        discount_amount = original_price - current_price
                        
                        # Add modified membership metadata for WooCommerce hook
                        line_item['meta_data'].extend([
                            {
                                'key': '_ds_pos_original_price',
                                'value': f"{original_price:.2f}"
                            },
                            {
                                'key': '_ds_pos_modified_price',
                                'value': f"{current_price:.2f}"
                            },
                            {
                                'key': '_ds_pos_discount_amount',
                                'value': f"{discount_amount:.2f}"
                            },
                            {
                                'key': '_modified_membership_pricing',
                                'value': 'true'
                            }
                        ])
                        logger.info(f"🆕 MODIFIED MEMBERSHIP: Added modified pricing metadata to line item: {item.name}")
                        logger.info(f"    Original: ${original_price:.2f} -> Modified: ${current_price:.2f} (Discount: ${discount_amount:.2f})")
                
                # 🆕 FALLBACK MODIFIED PRICING: Check for pricing modifications in metadata
                elif metadata and not metadata.get('isTrialMembership'):
                    # Extract discount information from metadata (from frontend)
                    original_price = metadata.get('original_price')
                    final_price = metadata.get('final_price')
                    manual_discount = metadata.get('manual_discount', 0)
                    
                    if original_price and final_price and float(original_price) != float(final_price):
                        # Check if this is a membership product
                        is_membership_product = (
                            'membership' in item.name.lower() or 
                            'member' in item.name.lower() or 
                            'subscription' in item.name.lower()
                        )
                        
                        if is_membership_product:
                            # Ensure meta_data exists
                            if 'meta_data' not in line_item:
                                line_item['meta_data'] = []
                            
                            # Add modified membership metadata for WooCommerce hook
                            discount_amount = float(original_price) - float(final_price)
                            line_item['meta_data'].extend([
                                {
                                    'key': '_ds_pos_original_price',
                                    'value': f"{float(original_price):.2f}"
                                },
                                {
                                    'key': '_ds_pos_modified_price',
                                    'value': f"{float(final_price):.2f}"
                                },
                                {
                                    'key': '_ds_pos_discount_amount',
                                    'value': f"{discount_amount:.2f}"
                                },
                                {
                                    'key': '_modified_membership_pricing',
                                    'value': 'true'
                                }
                            ])
                            logger.info(f"🆕 MODIFIED MEMBERSHIP (metadata): Added modified pricing metadata to line item: {item.name}")
                            logger.info(f"    Original: ${original_price} -> Modified: ${final_price} (Manual discount: {manual_discount})")
                
                # 🆕 DEBUG: Log all pricing information for debugging
                logger.info(f"🔍 PRICING DEBUG for {item_display_name}:")
                logger.info(f"   item.price (original): ${float(item.price):.2f}")
                logger.info(f"   item.subtotal (discounted): ${float(item.subtotal):.2f}")
                logger.info(f"   item_unit_price (calculated): ${item_unit_price:.2f}")
                logger.info(f"   item.quantity: {item.quantity}")
                logger.info(f"   original_item_price: ${original_item_price:.2f}")
                logger.info(f"   Has original_price field: {hasattr(item, 'original_price')} = {getattr(item, 'original_price', 'N/A')}")
                logger.info(f"   Is trial membership: {metadata and metadata.get('isTrialMembership')}")
                logger.info(f"   Item metadata keys: {list(metadata.keys()) if metadata else 'None'}")
                logger.info(f"   Line item meta_data count: {len(line_item.get('meta_data', []))}")
                
                # 🆕 UNIVERSAL PRICING: Add metadata for ALL products with modified pricing (not just memberships)
                # 🔥 SUBSCRIPTION FIX: Always send metadata for subscription products with discount
                has_subscription_discount = (is_subscription and metadata and 
                                           metadata.get('subscription_discount_percentage', 0) > 0)
                has_price_modification = abs(original_item_price - item_unit_price) > 0.01
                
                if has_price_modification or has_subscription_discount:
                    # Price has been modified - add metadata for ALL products
                    if 'meta_data' not in line_item:
                        line_item['meta_data'] = []
                    
                    # 🔥 REORDER FIX: Check if frontend already provided discount values
                    # This happens when reordering - we should preserve the original discount, not recalculate
                    frontend_original_price = None
                    frontend_discount_amount = None
                    frontend_discount_reason = None
                    
                    if metadata and isinstance(metadata, dict):
                        # Check for frontend-provided values (from reorder)
                        frontend_original_price = metadata.get('_ds_pos_original_price') or metadata.get('pos_original_price')
                        frontend_discount_amount = metadata.get('_ds_pos_discount_amount') or metadata.get('pos_discount_amount')
                        frontend_discount_reason = metadata.get('_ds_pos_discount_reason') or metadata.get('discount_reason')
                        
                        if frontend_original_price or frontend_discount_amount:
                            logger.info(f"🔥 REORDER: Using frontend-provided discount values:")
                            logger.info(f"   Frontend original price: ${frontend_original_price}")
                            logger.info(f"   Frontend discount amount: ${frontend_discount_amount}")
                            logger.info(f"   Frontend discount reason: {frontend_discount_reason}")
                    
                    # Use frontend values if provided, otherwise calculate
                    if frontend_original_price is not None and frontend_discount_amount is not None:
                        display_original_price = float(frontend_original_price)
                        discount_amount = float(frontend_discount_amount)
                        logger.info(f"🔥 REORDER: Using frontend discount: ${discount_amount:.2f} (original: ${display_original_price:.2f})")
                    else:
                        # Calculate discount information (default behavior)
                        discount_amount = original_item_price - item_unit_price
                        display_original_price = original_item_price
                    
                    # 🔥 CRITICAL FIX: For subscription products, use regular product price as "original"
                    # so WordPress shows the full discount from regular price to final price
                    # But skip this if frontend already provided values (reorder case)
                    if is_subscription and metadata and frontend_original_price is None:
                        # Use the regular product price as the "original" for display purposes
                        display_original_price = float(item.price)  # This is the actual original product price
                        # Recalculate total discount from regular price to final price
                        discount_amount = display_original_price - item_unit_price
                        
                        logger.info(f"🔥 SUBSCRIPTION PRICE DISPLAY FIX:")
                        logger.info(f"   Regular product price (display original): ${display_original_price:.2f}")
                        logger.info(f"   Final price: ${item_unit_price:.2f}")
                        logger.info(f"   Total discount (subscription + manual): ${discount_amount:.2f}")
                    
                    # Add universal modified pricing metadata for WooCommerce hook
                    pricing_metadata = [
                        {
                            'key': '_ds_pos_original_price',
                            'value': f"{display_original_price:.2f}"
                        },
                        {
                            'key': '_ds_pos_modified_price', 
                            'value': f"{item_unit_price:.2f}"
                        },
                        {
                            'key': '_ds_pos_discount_amount',
                            'value': f"{discount_amount:.2f}"
                        },
                        {
                            'key': '_ds_pos_modified_pricing',
                            'value': 'true'
                        },
                        {
                            'key': '_ds_pos_line_total',
                            'value': f"{item_subtotal:.2f}"
                        },
                        {
                            'key': '_ds_pos_quantity',
                            'value': str(item.quantity)
                        }
                    ]
                    
                    # 🆕 DISCOUNT REASON: Extract from metadata JSON and add if provided
                    discount_reason = None
                    if metadata and isinstance(metadata, dict):
                        discount_reason = metadata.get('discount_reason')
                    
                    if discount_reason:
                        pricing_metadata.append({
                            'key': '_ds_pos_discount_reason',
                            'value': discount_reason
                        })
                        logger.info(f"🆕 DISCOUNT REASON: Added discount reason to WooCommerce metadata: {discount_reason}")
                    
                    # 🔥 SUBSCRIPTION METADATA: Add subscription-specific metadata
                    if is_subscription:
                        subscription_discount_percentage = metadata.get('subscription_discount_percentage', 0) if metadata else 0
                        
                        # 🔥 CRITICAL FIX: Don't send subscription discount percentage to WordPress
                        # because we've already used the subscription price as the baseline.
                        # WordPress should NOT apply subscription discount again.
                        pricing_metadata.extend([
                            {
                                'key': '_ds_pos_is_subscription',
                                'value': 'true'
                            },
                            {
                                'key': '_ds_pos_subscription_already_applied',
                                'value': 'true'  # Flag to indicate subscription discount already in baseline
                            },
                            {
                                'key': '_ds_pos_subscription_discount_percentage_applied',
                                'value': str(subscription_discount_percentage)  # For reference only
                            },
                            {
                                'key': '_ds_pos_regular_product_price',
                                'value': str(float(item.price))  # Always store the original product price
                            }
                        ])
                        
                        # 🔄 SUBSCRIPTION FREQUENCY FIX: Store per-item subscription period/interval
                        # so receipt builder can read correct frequency for each item independently
                        if subscription_period:
                            pricing_metadata.extend([
                                {
                                    'key': '_subscription_period',
                                    'value': str(subscription_period)
                                },
                                {
                                    'key': '_subscription_period_interval',
                                    'value': str(subscription_interval or 1)
                                }
                            ])
                            logger.info(f"🔄 SUBSCRIPTION FREQUENCY: Added per-item frequency to line item meta_data: {subscription_period} every {subscription_interval}")
                        
                        logger.info(f"🔥 SUBSCRIPTION METADATA: Subscription discount already applied in baseline price, not sending to WordPress")
                    
                    line_item['meta_data'].extend(pricing_metadata)
                    
                    logger.info(f"🆕 UNIVERSAL PRICING: Added modified pricing metadata")
                    logger.info(f"    Original/baseline: ${original_item_price:.2f} -> Final: ${item_unit_price:.2f} (Discount: ${discount_amount:.2f})")
                    if is_subscription:
                        logger.info(f"    Subscription metadata added: discount_percentage={metadata.get('subscription_discount_percentage', 0) if metadata else 0}%")
                else:
                    # Still add pricing metadata for non-discounted items so that:
                    # 1. WordPress preserve-pricing hooks protect ALL POS items from WC membership recalculation
                    # 2. Receipt code always has reliable unit prices (avoids race condition where WC
                    #    transiently inflates subtotal before pricing lock hooks correct it)
                    if 'meta_data' not in line_item:
                        line_item['meta_data'] = []
                    line_item['meta_data'].extend([
                        {'key': '_ds_pos_original_price', 'value': f"{item_unit_price:.2f}"},
                        {'key': '_ds_pos_modified_price', 'value': f"{item_unit_price:.2f}"},
                        {'key': '_ds_pos_discount_amount', 'value': '0.00'},
                        {'key': '_ds_pos_modified_pricing', 'value': 'true'},
                    ])
                    logger.info(f"🔍 No price modification — added baseline pricing metadata (unit: ${item_unit_price:.2f})")

                # Regular item - add normally
                line_items.append(line_item)
                logger.info(f"🎯 Added regular item: {item_display_name} at ${item_unit_price}")
        
            # If this is a subscription product, add it to the subscription items list
            if is_subscription:
                # 🔥 SUBSCRIPTION RECURRING PRICE FIX:
                # The recurring price must include the subscription discount (e.g. WCSATT 5% off)
                # but must NOT include manual POS discounts (e.g. order-level coupons).
                #
                # Priority for recurring price:
                # 1. metadata.actualMembershipPrice — authoritative for membership products
                # 2. Regular price * (1 - subscription_discount_percentage) — for WCSATT products
                # 3. item.original_price — catalog price before POS discounts
                # 4. item.price — fallback
                
                subscription_discount_pct = float(metadata.get('subscription_discount_percentage', 0)) if metadata else 0
                original_price_from_metadata = float(metadata.get('actualMembershipPrice', 0)) if metadata and metadata.get('actualMembershipPrice') else None
                original_price_from_model = float(item.original_price) if item.original_price and float(item.original_price) > 0 else None
                regular_product_price = original_price_from_model or float(item.price)
                
                if original_price_from_metadata:
                    # actualMembershipPrice is the most authoritative — it already includes subscription discount
                    recurring_unit_price = original_price_from_metadata
                    logger.info(f"🔥 SUBSCRIPTION PRICING: Using metadata actualMembershipPrice ${recurring_unit_price:.2f} for recurring amount")
                elif subscription_discount_pct > 0:
                    # Apply subscription discount to regular price (e.g. WCSATT 5% off)
                    recurring_unit_price = regular_product_price * (1 - subscription_discount_pct / 100)
                    logger.info(f"🔥 SUBSCRIPTION PRICING: Applying {subscription_discount_pct}% subscription discount to ${regular_product_price:.2f} -> ${recurring_unit_price:.2f}")
                else:
                    # No subscription discount info — use regular product price as recurring
                    recurring_unit_price = regular_product_price
                    logger.info(f"🔥 SUBSCRIPTION PRICING: No subscription discount, using regular price ${recurring_unit_price:.2f}")
                
                original_subscription_price = recurring_unit_price * item.quantity
                
                subscription_data = {
                    'product_id': product_id,
                    'quantity': item.quantity,
                    'name': item.name,
                    'subtotal': str(original_subscription_price),
                    'total': str(original_subscription_price),
                    'period': subscription_period,
                    'interval': subscription_interval
                }
                
                logger.info(f"🔥 SUBSCRIPTION PRICING: Recurring price ${original_subscription_price:.2f} (item.price=${float(item.price):.2f}, item.subtotal=${float(item.subtotal):.2f}, discount={subscription_discount_pct}%)")
                logger.info(f"    Parent order can be $0 with coupon, but subscription will renew at ${original_subscription_price:.2f}")
                
                # 🆕 TRIAL MEMBERSHIP: Add trial membership data
                if metadata and metadata.get('isTrialMembership'):
                    subscription_data.update({
                        'is_trial_membership': True,
                        'trial_period_months': metadata.get('trialPeriodMonths', 0),
                        'actual_membership_price': str(float(metadata.get('actualMembershipPrice', item.subtotal))),
                        'checkout_price': str(float(metadata.get('checkoutPrice', 0)))
                    })
                    logger.info(f"🆕 TRIAL MEMBERSHIP: Added trial data to subscription: {subscription_data}")
                
                # 🔧 FIX: Regular Monthly Membership (no trial) - add subscription data
                if metadata and metadata.get('isRegularMonthlyMembership'):
                    subscription_data.update({
                        'is_trial_membership': False,  # Explicitly NOT a trial
                        'trial_period_months': 0,
                        'actual_membership_price': str(float(metadata.get('actualMembershipPrice', item.subtotal))),
                        'checkout_price': str(float(metadata.get('checkoutPrice', item.subtotal)))  # Full price charged immediately
                    })
                    logger.info(f"🔧 REGULAR MONTHLY MEMBERSHIP: Added subscription data (no trial): {subscription_data}")
                
                # 🔥 BUNDLE → SUBSCRIPTION TRIAL FIX: For bundle children, the frontend doesn't
                # know about the product's trial period. Resolve trial data with 3-tier fallback:
                #   1. ProductSubscription DB fields (trial_period, trial_length)
                #   2. ProductSubscription.woo_data -> subscription_data dict
                #   3. db_product.woo_data -> meta_data -> _subscription_trial_*
                if metadata and metadata.get('is_bundle_subscription_item') and db_product:
                    try:
                        resolved_trial_period = None
                        resolved_trial_length = None
                        trial_source = None
                        
                        # Tier 1: ProductSubscription dedicated DB fields
                        bundle_sub = ProductSubscription.objects.filter(product=db_product).first()
                        if bundle_sub:
                            logger.info(f"🔍 BUNDLE TRIAL CHECK: ProductSubscription found for '{item.name}': trial_period={bundle_sub.trial_period}, trial_length={bundle_sub.trial_length}")
                            if bundle_sub.trial_length and bundle_sub.trial_length > 0:
                                resolved_trial_period = bundle_sub.trial_period or 'month'
                                resolved_trial_length = bundle_sub.trial_length
                                trial_source = 'ProductSubscription DB fields'
                            
                            # Tier 2: ProductSubscription.woo_data JSON
                            if not resolved_trial_length and bundle_sub.woo_data:
                                woo_sub_data = bundle_sub.woo_data if isinstance(bundle_sub.woo_data, dict) else {}
                                sub_data_inner = woo_sub_data.get('subscription_data', woo_sub_data)
                                t_length = sub_data_inner.get('trial_length')
                                t_period = sub_data_inner.get('trial_period')
                                if t_length and int(t_length) > 0:
                                    resolved_trial_period = t_period or 'month'
                                    resolved_trial_length = int(t_length)
                                    trial_source = 'ProductSubscription.woo_data'
                                    logger.info(f"🔍 BUNDLE TRIAL CHECK: Found trial in woo_data: {resolved_trial_length} {resolved_trial_period}(s)")
                        else:
                            logger.info(f"🔍 BUNDLE TRIAL CHECK: No ProductSubscription record for '{item.name}'")
                        
                        # Tier 3: db_product.woo_data meta_data
                        if not resolved_trial_length and db_product.woo_data and isinstance(db_product.woo_data, dict):
                            product_meta = db_product.woo_data.get('meta_data', [])
                            for meta in product_meta:
                                if isinstance(meta, dict):
                                    if meta.get('key') == '_subscription_trial_length':
                                        val = meta.get('value')
                                        if val and int(val) > 0:
                                            resolved_trial_length = int(val)
                                    elif meta.get('key') == '_subscription_trial_period':
                                        resolved_trial_period = meta.get('value')
                            if resolved_trial_length and resolved_trial_length > 0:
                                resolved_trial_period = resolved_trial_period or 'month'
                                trial_source = 'db_product.woo_data meta_data'
                                logger.info(f"🔍 BUNDLE TRIAL CHECK: Found trial in product woo_data meta: {resolved_trial_length} {resolved_trial_period}(s)")
                        
                        # Tier 4: Live WooCommerce API call (guaranteed fresh data)
                        if not resolved_trial_length and db_product.woo_product_id:
                            try:
                                logger.info(f"🔍 BUNDLE TRIAL CHECK: Tiers 1-3 empty, calling WooCommerce API for product {db_product.woo_product_id}")
                                wc_product_resp = wc.get_product(db_product.woo_product_id)
                                if wc_product_resp and wc_product_resp.get('data'):
                                    wc_meta = wc_product_resp['data'].get('meta_data', [])
                                    for meta in wc_meta:
                                        if isinstance(meta, dict):
                                            if meta.get('key') == '_subscription_trial_length':
                                                val = meta.get('value')
                                                if val and int(val) > 0:
                                                    resolved_trial_length = int(val)
                                            elif meta.get('key') == '_subscription_trial_period':
                                                resolved_trial_period = meta.get('value')
                                    if resolved_trial_length and resolved_trial_length > 0:
                                        resolved_trial_period = resolved_trial_period or 'month'
                                        trial_source = 'WooCommerce API (live)'
                                        logger.info(f"🔍 BUNDLE TRIAL CHECK: Found trial via live API: {resolved_trial_length} {resolved_trial_period}(s)")
                                        
                                        # Also update the ProductSubscription record for future use
                                        if bundle_sub:
                                            bundle_sub.trial_period = resolved_trial_period
                                            bundle_sub.trial_length = resolved_trial_length
                                            bundle_sub.save(update_fields=['trial_period', 'trial_length'])
                                            logger.info(f"📋 Updated ProductSubscription with trial data from live API")
                            except Exception as api_err:
                                logger.warning(f"⚠️ WooCommerce API trial lookup failed: {api_err}")
                        
                        # Apply trial if found from any source
                        if resolved_trial_length and resolved_trial_length > 0:
                            # Convert to months
                            trial_months = resolved_trial_length
                            if resolved_trial_period == 'year':
                                trial_months = resolved_trial_length * 12
                            elif resolved_trial_period == 'day':
                                trial_months = max(1, resolved_trial_length // 30)
                            elif resolved_trial_period == 'week':
                                trial_months = max(1, resolved_trial_length // 4)
                            # else: 'month' → trial_months stays as-is
                            
                            subscription_data.update({
                                'is_trial_membership': True,
                                'trial_period_months': trial_months,
                                'checkout_price': '0',  # Bundle parent pays; no charge during trial
                            })
                            logger.info(f"🔥 BUNDLE TRIAL OVERRIDE: Product '{item.name}' has {resolved_trial_length} {resolved_trial_period}(s) free trial -> trial_period_months={trial_months} (source: {trial_source})")
                        else:
                            logger.info(f"ℹ️ Bundle child '{item.name}': no trial period found in any source (DB fields, woo_data, product meta)")
                    except Exception as e:
                        logger.warning(f"⚠️ Could not check trial data for bundle child '{item.name}': {e}")
                
                subscription_items.append(subscription_data)
                logger.info(f"✅ Added subscription item to list: {subscription_data}")
                logger.info(f"📊 Current subscription_items count: {len(subscription_items)}")
    
        # 🎯 NEW BUNDLE STRUCTURE: Add bundle parents first, then children
        logger.info(f"🎯 Processing {len(bundle_parents)} bundle parents and {len(bundle_children)} bundle children")
        
        # 🔥 FIX: Create a SEPARATE line item for the bundle parent instead of overwriting the first child.
        # The old approach replaced the first bundle child (e.g., Probiotic 25+ Capsules) with the bundle
        # parent display, effectively deleting that product from the order.
        
        for bundle_id, bundle_info in bundle_parents.items():
            bundle_qty = bundle_info.get('quantity', 1)
            bundle_total_price = bundle_info['price']  # Total price for all units (e.g. $994 for 2x $497)
            # Create a new line item for the bundle parent display
            bundle_parent_line_item = {
                'name': bundle_info['name'],
                'product_id': bundle_info.get('product_id', 0),
                'quantity': bundle_qty,
                'subtotal': str(bundle_total_price),
                'total': str(bundle_total_price),
                'meta_data': [
                    {'key': 'Bundle Type', 'value': 'Parent Item'},
                    {'key': 'Bundle ID', 'value': bundle_id},
                    {'key': 'Original Bundle Price', 'value': str(bundle_total_price)},
                    {'key': '_ds_line_subtotal', 'value': str(bundle_total_price)},
                    {'key': '_ds_line_total', 'value': str(bundle_total_price)},
                    {'key': '_ds_bundle_parent_item', 'value': 'true'},
                    {'key': '_ds_bundle_parent_id', 'value': bundle_id}
                ]
            }
            line_items.append(bundle_parent_line_item)
            logger.info(f"🎯 Created separate bundle parent line item: {bundle_info['name']} qty={bundle_qty} at ${bundle_total_price}")
        
        # Add all bundle children as separate $0 line items (none are overwritten)
        line_items.extend(bundle_children)
        logger.info(f"🎯 Added {len(bundle_children)} bundle children at $0 each")
        
        # 🎯 NEW BUNDLE STRUCTURE: No need for complex discount calculations
        # Bundle discounts are handled by showing parent at bundle price and children at $0
        total_bundle_discount = 0.0  # No separate discount needed
        logger.info(f"🎯 Using new bundle structure - no separate discounts needed")
        
        # Create the WooCommerce order data
        # Determine payment method and title based on the payment_method parameter
        woo_payment_method = 'POS'  # Default
        woo_payment_title = 'POS Payment'  # Default
        
        # Variables to store card brand information
        card_brand = None
        card_last4 = None
        
        # Extract card information from request data if provided directly
        if request.data.get('card_brand'):
            card_brand = request.data.get('card_brand')
        if request.data.get('card_last4'):
            card_last4 = request.data.get('card_last4')
            
        # Try to extract card information from payment transactions if not found in request
        if not card_brand or not card_last4:
            try:
                from payments.models import PaymentTransaction
                # Look for payment transactions related to this order
                payment_transactions = PaymentTransaction.objects.filter(
                    order_id=pos_order.order_number
                ).order_by('-created_at')
                
                if payment_transactions.exists():
                    latest_transaction = payment_transactions.first()
                    if not card_brand and hasattr(latest_transaction, 'card_brand') and latest_transaction.card_brand:
                        card_brand = latest_transaction.card_brand
                        logger.info(f"Extracted card brand '{card_brand}' from payment transaction")
                    if not card_last4 and hasattr(latest_transaction, 'card_last4') and latest_transaction.card_last4:
                        card_last4 = latest_transaction.card_last4
                        logger.info(f"Extracted card last4 '****{card_last4}' from payment transaction")
            except Exception as e:
                logger.warning(f"Could not extract card info from payment transactions: {str(e)}")
        
        # Map our payment method types to WooCommerce payment method types
        if payment_method:
            if payment_method == 'split_payment':
                woo_payment_method = 'split_payment'
                # Try to generate detailed split payment title from POS order data
                detailed_title = 'Split Payment'
                if pos_order.payment_method:
                    try:
                        if isinstance(pos_order.payment_method, str):
                            payment_data = json.loads(pos_order.payment_method)
                        else:
                            payment_data = pos_order.payment_method
                        
                        if payment_data.get('type') == 'split' and payment_data.get('splitPayments'):
                            # Extract payment method names from split payments
                            method_names = []
                            for split_payment in payment_data.get('splitPayments', []):
                                method = split_payment.get('method', {})
                                method_name = method.get('name') or method.get('type', 'Unknown')
                                if method_name not in method_names:  # Avoid duplicates
                                    method_names.append(method_name)
                                
                                # Extract card brand information from credit card payments in split
                                if method.get('type') == 'credit' and method.get('brand'):
                                    card_brand = method.get('brand')
                                    card_last4 = method.get('last4')
                            
                            if method_names:
                                detailed_title = f"Split Payment ({', '.join(method_names)})"
                    except Exception as e:
                        logger.warning(f"Could not parse split payment details: {str(e)}")
                
                woo_payment_title = detailed_title
            elif payment_method == 'cod':
                woo_payment_method = 'cod'
                woo_payment_title = 'Cash on Delivery'
            elif payment_method == 'cheque':
                woo_payment_method = 'cheque'
                woo_payment_title = 'Check Payment'
            elif payment_method == 'bacs':
                woo_payment_method = 'bacs'
                woo_payment_title = 'Bank Transfer'
            elif payment_method == 'credit_card':
                woo_payment_method = 'credit_card'
                woo_payment_title = 'Credit Card'
                # Try to extract card brand from request data if available
                if request.data.get('card_brand'):
                    card_brand = request.data.get('card_brand')
                    card_last4 = request.data.get('card_last4')
                    if card_brand and card_last4:
                        woo_payment_title = f'Credit Card ({card_brand} ****{card_last4})'
                    elif card_brand:
                        woo_payment_title = f'Credit Card ({card_brand})'
        
        # If we have a payment method from the POS order, use that as a backup
        if not payment_method and pos_order.payment_method:
            try:
                # Try to parse the payment method from the POS order
                if isinstance(pos_order.payment_method, str):
                    payment_data = json.loads(pos_order.payment_method)
                    if payment_data.get('type') == 'split':
                        woo_payment_method = 'split_payment'
                        # Generate detailed split payment title
                        detailed_title = 'Split Payment'
                        if payment_data.get('splitPayments'):
                            try:
                                # Extract payment method names from split payments
                                method_names = []
                                for split_payment in payment_data.get('splitPayments', []):
                                    method = split_payment.get('method', {})
                                    method_name = method.get('name') or method.get('type', 'Unknown')
                                    if method_name not in method_names:  # Avoid duplicates
                                        method_names.append(method_name)
                                
                                if method_names:
                                    detailed_title = f"Split Payment ({', '.join(method_names)})"
                            except Exception as e:
                                logger.warning(f"Could not parse split payment details in fallback: {str(e)}")
                        
                        woo_payment_title = detailed_title
                    elif payment_data.get('type') == 'cash':
                        woo_payment_method = 'cod'
                        woo_payment_title = 'Cash on Delivery'
                    elif payment_data.get('type') == 'check':
                        woo_payment_method = 'cheque'
                        woo_payment_title = 'Check Payment'
                    elif payment_data.get('type') == 'wire_transfer':
                        woo_payment_method = 'bacs'
                        woo_payment_title = 'Bank Transfer'
                    elif payment_data.get('type') == 'credit':
                        woo_payment_method = 'credit_card'
                        woo_payment_title = 'Credit Card'
                        # Extract card brand information from credit card payment
                        if payment_data.get('brand'):
                            card_brand = payment_data.get('brand')
                            card_last4 = payment_data.get('last4')
                            # Update payment title to include card brand if available
                            if card_brand and card_last4:
                                woo_payment_title = f'Credit Card ({card_brand} ****{card_last4})'
                            elif card_brand:
                                woo_payment_title = f'Credit Card ({card_brand})'
            except Exception as e:
                logger.error(f"Error parsing payment method from POS order: {str(e)}")
        
        # ✅ CALCULATE DISCOUNT INFORMATION FOR WOOCOMMERCE
        # Calculate original subtotal from line items by getting original product prices
        calculated_original_subtotal = 0.0
        calculated_discounted_subtotal = 0.0
        
        # 🎯 NEW BUNDLE STRUCTURE: Use line items as-is (no complex adjustments needed)
        # Bundle parents show at bundle price, children show at $0 - totals are already correct
        updated_line_items = line_items  # Use line items directly
        # 🎯 NEW BUNDLE STRUCTURE: Simple total calculation
        # Bundle parents are at bundle price, children are at $0 - totals are already correct
        calculated_original_subtotal = sum(float(item.get('subtotal', 0)) for item in updated_line_items)
        calculated_discounted_subtotal = calculated_original_subtotal  # Same as original since no separate discounts
        
        logger.info(f"🎯 Bundle structure totals: ${calculated_original_subtotal:.2f}")
        
        # ✅ HANDLE ORDER-LEVEL DISCOUNTS FROM POS
        # Check if we have order-level discount data from the frontend
        if order_discount and order_discount > 0:
            logger.info(f"💰 Processing order-level discount: {order_discount} ({order_discount_type})")
            
            # Calculate discount amount
            if order_discount_type == 'percentage':
                discount_amount = calculated_original_subtotal * (order_discount / 100)
            else:  # dollar amount
                discount_amount = float(order_discount)
            
            # Use discounted_total if provided, otherwise calculate it
            if discounted_total is not None:
                final_discounted_total = float(discounted_total)
                # Verify the discount calculation matches
                calculated_discount = calculated_original_subtotal - final_discounted_total
                logger.info(f"💰 Using provided discounted_total: ${final_discounted_total:.2f} (discount: ${calculated_discount:.2f})")
            else:
                final_discounted_total = max(0, calculated_original_subtotal - discount_amount)
                logger.info(f"💰 Calculated discounted_total: ${final_discounted_total:.2f} (discount: ${discount_amount:.2f})")
            
            final_original_subtotal = calculated_original_subtotal
            final_discount_total = final_original_subtotal - final_discounted_total
            
            # Create fee line for the discount (negative fee)
            fee_lines = [{
                'name': f'Discount ({order_discount_type})',
                'total': f'-{final_discount_total:.2f}',
                'tax_status': 'none'
            }]
            
            logger.info(f"💰 Order discount applied:")
            logger.info(f"   Original subtotal: ${final_original_subtotal:.2f}")
            logger.info(f"   Discount amount: ${final_discount_total:.2f}")
            logger.info(f"   Final total: ${final_discounted_total:.2f}")
        else:
            # No order-level discount
            final_original_subtotal = calculated_original_subtotal
            final_discounted_total = calculated_discounted_subtotal
            final_discount_total = 0.0
            fee_lines = []
            logger.info(f"🎯 No order-level discount applied: ${final_original_subtotal:.2f}")
        
        # Extract actual Authorize.Net transaction ID for split payments
        actual_transaction_id = pos_order.transaction_id if pos_order.transaction_id else ''
        
        # If transaction_id is ORD-xxx (order number), extract the real transaction ID from payment method
        if actual_transaction_id.startswith('ORD-') and pos_order.payment_method:
            try:
                payment_data = json.loads(pos_order.payment_method) if isinstance(pos_order.payment_method, str) else pos_order.payment_method
                
                # Check if it's a split payment
                if payment_data.get('type') == 'split':
                    split_payments = payment_data.get('splitPayments', [])
                    # Find the credit card payment
                    for split in split_payments:
                        method = split.get('method', {})
                        if method.get('type') == 'credit':
                            # Extract transaction ID from credit card payment
                            trans_id = method.get('id')
                            if trans_id and not trans_id.startswith('ORD-'):
                                actual_transaction_id = trans_id
                                logger.info(f"✅ Extracted credit card transaction ID from split payment: {trans_id}")
                                break
            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                logger.warning(f"Failed to parse payment method for transaction ID: {str(e)}")
        
        logger.info(f"Using transaction_id for WooCommerce order: {actual_transaction_id}")
        
        woo_order_data = {
            'status': 'pending',  # 🎯 CRITICAL FIX: Start with 'pending' to prevent ATUM reduce_stock_levels crash (Helpers.php:961 get_id() on bool)
            'customer_id': customer_id,
            'customer_note': '',  # Never put internal notes here — this field is visible on customer emails/receipts
            'line_items': updated_line_items,  # 🔥 CRITICAL FIX: Use UPDATED line items with discounted pricing
            'fee_lines': fee_lines,  # ✅ Add fee lines for discounts
            'payment_method': woo_payment_method,
            'payment_method_title': woo_payment_title,  # Use the mapped payment method title
            'transaction_id': actual_transaction_id,  # Use actual Authorize.Net transaction ID
            'created_via': 'DS POS',  # Set the origin to DS POS
            'source': 'DS POS',  # Set the source to DS POS
            'origin': 'DS POS',  # Add explicit origin field
            'meta_data': [
                {
                    'key': '_pos_order_id',
                    'value': str(pos_order.id)
                },
                {
                    'key': '_pos_order_number',
                    'value': pos_order.order_number
                },
                {
                    'key': '_pos_payment_method',
                    'value': str(pos_order.payment_method) if pos_order.payment_method else 'Unknown'
                },
                {
                    'key': '_pos_transaction_id',
                    'value': pos_order.transaction_id if pos_order.transaction_id else ''
                },
                {
                    'key': '_created_via',
                    'value': 'DS POS'
                },
                {
                    'key': '_source_name',
                    'value': 'DS POS'
                },
                {
                    'key': '_order_source',
                    'value': 'DS POS'
                },
                {
                    'key': 'source',
                    'value': 'DS POS'
                },
                {
                    'key': '_contains_subscription',
                    'value': 'true' if subscription_items else 'false'
                },
                # Add the created_by field to store the sales manager who created this order
                {
                    'key': '_created_by',
                    'value': created_by if created_by else 'System'
                },
                # Add the notes field if provided (from request)
                {
                    'key': '_pos_notes',
                    'value': notes if notes else ''
                },
                # Add the POS order notes from database if they exist
                {
                    'key': '_pos_order_notes',
                    'value': pos_order.notes if pos_order.notes else ''
                }
            ]
        }
        
        # ✅ ADD DISCOUNT METADATA TO WOOCOMMERCE ORDER
        if order_discount and order_discount > 0:
            woo_order_data['meta_data'].extend([
                {
                    'key': '_pos_order_discount',
                    'value': str(order_discount)
                },
                {
                    'key': '_pos_order_discount_type',
                    'value': order_discount_type
                },
                {
                    'key': '_pos_original_subtotal',
                    'value': f'{final_original_subtotal:.2f}'
                },
                {
                    'key': '_pos_discount_amount',
                    'value': f'{final_discount_total:.2f}'
                },
                {
                    'key': '_pos_discounted_total',
                    'value': f'{final_discounted_total:.2f}'
                }
            ])
            logger.info(f"💰 Added discount metadata to WooCommerce order")
        
        # 🔥 NEW: Add credited service meta data if there are any credited items
        if credited_items:
            logger.info(f"💳 Adding order-level meta data for {len(credited_items)} credited services")
            woo_order_data['meta_data'].append({
                'key': 'ds_pos_order',
                'value': '1'
            })
        
        # Add card brand information to meta_data if available
        if card_brand:
            logger.info(f"Adding card brand '{card_brand}' to WooCommerce order meta_data")
            woo_order_data['meta_data'].append({
                'key': '_card_brand',
                'value': card_brand
            })
            # Also add to payment method meta for better tracking
            woo_order_data['meta_data'].append({
                'key': '_payment_method',
                'value': f'Credit Card ({card_brand})'
            })
        
        if card_last4:
            logger.info(f"Adding card last4 '****{card_last4}' to WooCommerce order meta_data")
            woo_order_data['meta_data'].append({
                'key': '_card_last4',
                'value': card_last4
            })
        
        # Add skip GHL email flag to meta_data if set
        if skip_ghl_email:
            logger.info(f"🔕 Adding _skip_ghl_email meta to WooCommerce order")
            woo_order_data['meta_data'].append({
                'key': '_skip_ghl_email',
                'value': 'yes'
            })
        
        # Add credit bank redemption meta to WooCommerce order
        if credit_bank_redeemed:
            logger.info(f"🏦 Adding credit bank meta to WooCommerce order: redeemed=${credit_bank_redeemed}, remaining=${credit_bank_remaining}")
            woo_order_data['meta_data'].append({
                'key': '_credit_bank_redeemed',
                'value': credit_bank_redeemed
            })
            woo_order_data['meta_data'].append({
                'key': '_credit_bank_remaining',
                'value': credit_bank_remaining or '0.00'
            })
        
        # Log final payment method information
        logger.info(f"WooCommerce order payment method: {woo_payment_method}, title: {woo_payment_title}")
        
        # Add location properties to meta_data if they exist in the POS order
        if hasattr(pos_order, 'assigned_location') and pos_order.assigned_location:
            woo_order_data['meta_data'].append({
                'key': '_assigned_location',
                'value': pos_order.assigned_location
            })
        
        if hasattr(pos_order, 'pos_location_id') and pos_order.pos_location_id:
            woo_order_data['meta_data'].append({
                'key': '_pos_location_id', 
                'value': str(pos_order.pos_location_id)
            })
            
        if hasattr(pos_order, 'pos_location_name') and pos_order.pos_location_name:
            woo_order_data['meta_data'].append({
                'key': '_pos_location_name',
                'value': pos_order.pos_location_name
            })
        
        # ============================================================================
        # ORDER ORIGIN METADATA - Dynamic location-based origin display
        # ============================================================================
        logger.info("="*80)
        logger.info("🏢 SETTING ORDER ORIGIN METADATA")
        logger.info("="*80)
        
        # Log all available location fields for debugging
        logger.info(f"📍 Available location fields:")
        logger.info(f"   - pos_order.pos_location_name: {getattr(pos_order, 'pos_location_name', 'NOT SET')}")
        logger.info(f"   - pos_order.assigned_location: {getattr(pos_order, 'assigned_location', 'NOT SET')}")
        logger.info(f"   - pos_order.pos_location_id: {getattr(pos_order, 'pos_location_id', 'NOT SET')}")
        
        # Format: "DS POS {location_name}" based on assigned location (first word only)
        order_origin = 'DS POS'  # Default origin
        location_found = False
        
        # Priority 1: Use assigned_location (first word only) - e.g., "Boca Raton" -> "Boca"
        if hasattr(pos_order, 'assigned_location') and pos_order.assigned_location:
            location_name = pos_order.assigned_location.strip()
            # Extract first word for cleaner display
            if ' ' in location_name:
                # Get first word (e.g., "Boca Raton" -> "Boca")
                location_name = location_name.split()[0]
            order_origin = f'DS POS {location_name}'
            location_found = True
            logger.info(f"✅ Setting order origin from assigned_location: '{order_origin}' (original: '{pos_order.assigned_location}')")
        # Priority 2: Fall back to pos_location_name if assigned_location is not available
        elif hasattr(pos_order, 'pos_location_name') and pos_order.pos_location_name:
            location_name = pos_order.pos_location_name.strip()
            # Also extract first word from pos_location_name for consistency
            if ' ' in location_name or '|' in location_name:
                # Get first word/part (e.g., "Clinic | Doctors Studio" -> "Clinic")
                location_name = location_name.split()[0].split('|')[0].strip()
            order_origin = f'DS POS {location_name}'
            location_found = True
            logger.info(f"✅ Setting order origin from pos_location_name: '{order_origin}' (original: '{pos_order.pos_location_name}')")
        else:
            logger.warning(f"⚠️  No location information found, using default: '{order_origin}'")
        
        # Add origin metadata to WooCommerce order
        woo_order_data['meta_data'].append({
            'key': '_order_origin',
            'value': order_origin
        })
        logger.info(f"📝 Added _order_origin metadata to WooCommerce order data: '{order_origin}'")
        logger.info(f"🎯 Location found: {location_found}")
        logger.info("="*80)
    
        # Add shipping information if available
        # PRIORITY: POS order shipping address first (captures what user entered at checkout)
        # Then fall back to contact's shipping address from DB
        if pos_order.shipping_address:
            shipping = {
                'first_name': pos_order.contact.first_name if pos_order.contact else '',
                'last_name': pos_order.contact.last_name if pos_order.contact else '',
                'address_1': pos_order.shipping_address,
                'address_2': getattr(pos_order, 'shipping_address_2', '') or (getattr(pos_order.contact, 'shipping_address_2', '') if pos_order.contact else '') or '',
                'city': pos_order.shipping_city,
                'state': pos_order.shipping_state,
                'postcode': pos_order.shipping_postcode,
                'country': pos_order.shipping_country
            }
            woo_order_data['shipping'] = shipping
            
            logger.info(f"Using POS order shipping address for WooCommerce order: {pos_order.shipping_address}, {pos_order.shipping_city}, {pos_order.shipping_state}, {pos_order.shipping_postcode}")
        elif pos_order.contact and hasattr(pos_order.contact, 'shipping_address') and pos_order.contact.shipping_address:
            contact = pos_order.contact
            shipping = {
                'first_name': contact.first_name or '',
                'last_name': contact.last_name or '',
                'address_1': contact.shipping_address,
                'address_2': getattr(contact, 'shipping_address_2', '') or '',
                'city': contact.shipping_city,
                'state': contact.shipping_state,
                'postcode': contact.shipping_postcode,
                'country': contact.shipping_country or contact.shipping_state
            }
            woo_order_data['shipping'] = shipping
            
            logger.info(f"Fallback: Using contact shipping address for WooCommerce order: {contact.shipping_address}, {contact.shipping_city}, {contact.shipping_state}, {contact.shipping_postcode}")
        
        # Always add billing information for customer name on receipts
        if pos_order.contact:
            billing = {
                'first_name': pos_order.contact.first_name or '',
                'last_name': pos_order.contact.last_name or ''
            }
            
            # Add billing address if available from contact
            contact = pos_order.contact
            
            # Use contact's billing address fields
            if hasattr(contact, 'billing_address') and contact.billing_address:
                logger.info(f"Using contact billing address for WooCommerce order: {contact.billing_address}, {contact.billing_city}, {contact.billing_state}, {contact.billing_postcode}")
                billing.update({
                    'address_1': contact.billing_address,
                    'address_2': getattr(contact, 'billing_address_2', '') or '',
                    'city': contact.billing_city,
                    'state': contact.billing_state,
                    'postcode': contact.billing_postcode,
                    'country': contact.billing_country or contact.billing_state  # Use country if available, otherwise state as fallback
                })
            
            # Add email if available
            if hasattr(contact, 'email') and contact.email:
                billing['email'] = contact.email
                
            # Add phone if available
            if hasattr(contact, 'phone') and contact.phone:
                billing['phone'] = contact.phone
                
            woo_order_data['billing'] = billing
            
            # Add customer name to meta_data for easier access
            woo_order_data['meta_data'].append({
                'key': '_billing_first_name',
                'value': contact.first_name or ''
            })
            woo_order_data['meta_data'].append({
                'key': '_billing_last_name',
                'value': contact.last_name or ''
            })
        logger.info(f"   Customer ID: {woo_order_data['customer_id']}")
        logger.info(f"   Payment Method: {woo_order_data['payment_method']}")
        logger.info(f"   Payment Title: {woo_order_data['payment_method_title']}")
        
        logger.info(f"[DEBUG] LINE ITEMS ({len(woo_order_data['line_items'])} items):")
        for i, item in enumerate(woo_order_data['line_items']):
            logger.info(f"   Item {i+1}: {item['name']}")
            logger.info(f"     Product ID: {item['product_id']}")
            logger.info(f"     Quantity: {item['quantity']}")
            # Note: 'price' field removed to prevent WooCommerce catalog price override
            logger.info(f"     Subtotal: ${float(item['subtotal']):.2f}")
            logger.info(f"     Total: ${float(item['total']):.2f}")
            
        logger.info(f"[DEBUG] FEE LINES ({len(woo_order_data['fee_lines'])} fees):")
        for i, fee in enumerate(woo_order_data['fee_lines']):
            logger.info(f"   Fee {i+1}: {fee['name']}")
            logger.info(f"     Total: ${float(fee['total']):.2f}")
            logger.info(f"     Meta Data: {fee.get('meta_data', [])}")
            
        logger.info(f"[DEBUG] META DATA ({len(woo_order_data['meta_data'])} items):")
        for meta in woo_order_data['meta_data']:
            logger.info(f"   {meta['key']}: {meta['value']}")
        
        # Calculate order totals for verification
        total_line_items = sum(float(item['total']) for item in woo_order_data['line_items'])
        total_fees = sum(float(fee['total']) for fee in woo_order_data['fee_lines'])
        calculated_order_total = total_line_items + total_fees
        
        logger.info(f"[DEBUG] CALCULATED TOTALS:")
        logger.info(f"   Line Items Total: ${total_line_items:.2f}")
        logger.info(f"   Fees Total: ${total_fees:.2f}")
        logger.info(f"   Calculated Order Total: ${calculated_order_total:.2f}")
        
        # Create the order in WooCommerce
        logger.info(f"[DEBUG] SENDING ORDER TO WOOCOMMERCE...")
        result = wc.create_order(woo_order_data)
        
        # COMPREHENSIVE DEBUG: Log the WooCommerce API response
        logger.info(f"[DEBUG] WOOCOMMERCE API RESPONSE:")
        logger.info(f"   Status: {result.get('status', 'Unknown')}")
        logger.info(f"   Message: {result.get('message', 'No message')}")
        if 'data' in result:
            woo_data = result['data']
            if isinstance(woo_data, dict):
                logger.info(f"   Order ID: {woo_data.get('id', 'No ID')}")
                logger.info(f"   Order Number: {woo_data.get('number', 'No Number')}")
                logger.info(f"   Order Status: {woo_data.get('status', 'No Status')}")
                logger.info(f"   Order Total: ${float(woo_data.get('total', 0)):.2f}")
                
                # ✅ VERIFY ORDER ORIGIN METADATA WAS SAVED
                logger.info("="*80)
                logger.info("🔍 VERIFYING ORDER ORIGIN METADATA IN WOOCOMMERCE RESPONSE")
                logger.info("="*80)
                if 'meta_data' in woo_data:
                    meta_data = woo_data['meta_data']
                    logger.info(f"📦 Total metadata entries in response: {len(meta_data)}")
                    
                    # Find _order_origin in metadata
                    origin_meta = None
                    for meta in meta_data:
                        if meta.get('key') == '_order_origin':
                            origin_meta = meta
                            break
                    
                    if origin_meta:
                        logger.info(f"✅ SUCCESS: _order_origin metadata found in WooCommerce response!")
                        logger.info(f"   Key: {origin_meta.get('key')}")
                        logger.info(f"   Value: '{origin_meta.get('value')}'")
                        logger.info(f"   ID: {origin_meta.get('id', 'N/A')}")
                    else:
                        logger.error(f"❌ ERROR: _order_origin metadata NOT found in WooCommerce response!")
                        logger.error(f"   This means the metadata was not saved to WooCommerce")
                        logger.error(f"   Available metadata keys: {[m.get('key') for m in meta_data]}")
                else:
                    logger.error(f"❌ ERROR: No meta_data field in WooCommerce response!")
                logger.info("="*80)
                
                # Log line items from WooCommerce response
                if 'line_items' in woo_data:
                    logger.info(f"   🔥 WooCommerce Response Line Items:")
                    for i, item in enumerate(woo_data['line_items']):
                        logger.info(f"     Item {i+1}: {item.get('name', 'Unknown')}")
                        logger.info(f"       Price: ${float(item.get('price', 0)):.2f}")
                        logger.info(f"       Quantity: {item.get('quantity', 0)}")
                        logger.info(f"       Subtotal: ${float(item.get('subtotal', 0)):.2f}")
                        logger.info(f"       Total: ${float(item.get('total', 0)):.2f}")
                        
                # Log fee lines from WooCommerce response
                if 'fee_lines' in woo_data:
                    logger.info(f"   🔥 WooCommerce Response Fee Lines:")
                    for i, fee in enumerate(woo_data['fee_lines']):
                        logger.info(f"     Fee {i+1}: {fee.get('name', 'Unknown')}")
                        logger.info(f"       Total: ${float(fee.get('total', 0)):.2f}")
            else:
                logger.info(f"   Raw Data: {woo_data}")
        else:
            logger.info(f"   No data in response")
    
        # 🔥 DEBUG: Check why SSH might not be called for bundle orders
        logger.info(f"🔥 WooCommerce order creation result status: {result.get('status')}")
        if result.get('status') != 'success':
            logger.error(f"🔥 WooCommerce order creation FAILED - SSH will not be called!")
            logger.error(f"🔥 Error details: {result.get('message', 'No error message')}")
            logger.error(f"🔥 Full result: {result}")
        
        if result.get('status') == 'success':
            # Update the POS order with the WooCommerce order ID
            woo_order_id = None
            woo_data = result.get('data', {})
        
            # Check if data is a dictionary or string
            if isinstance(woo_data, dict):
                woo_order_id = woo_data.get('id')
            elif isinstance(woo_data, str):
                # Try to parse JSON if it's a string
                try:
                    import json
                    woo_data_dict = json.loads(woo_data)
                    woo_order_id = woo_data_dict.get('id')
                except Exception as e:
                    logger.error(f"Error parsing WooCommerce response: {str(e)}")
        
            if woo_order_id:
                # Store the WooCommerce order ID in the metadata
                try:
                    metadata = pos_order.metadata or {}
                
                    # Convert metadata to dictionary if it's a string
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except json.JSONDecodeError:
                            metadata = {}
                
                    if isinstance(metadata, dict):
                        # Store as a list if multiple subscriptions
                        if 'woo_order_id' not in metadata:
                            metadata['woo_order_id'] = woo_order_id
                        metadata['woo_order_id'] = woo_order_id
                        pos_order.metadata = metadata
                        pos_order.save()
                        logger.info(f"Successfully created order with ID: {woo_order_id}")
                        
                        # 🏦 AUTO-CREDIT: Detect credit bank products and add credits to customer's bank
                        try:
                            import re as _re_cb
                            if pos_order.contact:
                                for order_item in pos_order.items.all():
                                    item_name = order_item.name or ''
                                    # Match "BANK: Credit Bank $X" pattern (case-insensitive)
                                    cb_match = _re_cb.search(r'(?i)bank:\s*credit\s*bank\s*\$?([\d,]+(?:\.\d{2})?)', item_name)
                                    if cb_match:
                                        credit_amount_str = cb_match.group(1).replace(',', '')
                                        credit_amount = float(credit_amount_str)
                                        qty = order_item.quantity or 1
                                        total_credits = credit_amount * qty
                                        
                                        from credit_bank.models import CreditBankAccount
                                        from decimal import Decimal
                                        account, _ = CreditBankAccount.objects.get_or_create(
                                            customer=pos_order.contact,
                                            defaults={'balance': Decimal('0')}
                                        )
                                        account.add_credits(
                                            amount=Decimal(str(total_credits)),
                                            order_id=str(woo_order_id),
                                            description=f"Purchased {item_name} (Order #{woo_order_id})",
                                        )
                                        logger.info(f"🏦 AUTO-CREDIT: Added ${total_credits:.2f} to credit bank for {pos_order.contact.first_name} {pos_order.contact.last_name} from '{item_name}' x{qty}")
                        except Exception as e:
                            logger.error(f"🏦 Error processing credit bank auto-credit: {e}", exc_info=True)
                        
                        # 🏦 UPDATE CREDIT BANK REDEMPTIONS: Replace ORD- IDs with real WooCommerce order ID
                        try:
                            if pos_order.contact:
                                from credit_bank.models import CreditBankTransaction
                                from django.utils import timezone
                                from datetime import timedelta
                                # Find recent redemption transactions (last 5 min) with ORD- IDs for this customer
                                recent_cutoff = timezone.now() - timedelta(minutes=5)
                                updated_count = CreditBankTransaction.objects.filter(
                                    customer=pos_order.contact,
                                    order_id__startswith='ORD-',
                                    created_at__gte=recent_cutoff,
                                ).update(
                                    order_id=str(woo_order_id),
                                    description=f"Credits redeemed (Order #{woo_order_id})",
                                )
                                if updated_count > 0:
                                    logger.info(f"🏦 Updated {updated_count} credit bank redemption(s) with WooCommerce order ID {woo_order_id}")
                        except Exception as e:
                            logger.error(f"🏦 Error updating credit bank redemption order IDs: {e}", exc_info=True)
                        
                        # 🚀 PERFORMANCE: Run post-creation WC API calls in parallel
                        # (internal note + status update are independent operations)
                        from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
                        
                        final_status = determine_order_status_from_fulfillment(pos_order)
                        logger.info(f"🔥 ACTIVATING ORDER: Changing status from 'pending' to '{final_status}' for order {woo_order_id}")
                        
                        # Log fulfillment locations for debugging
                        for item in pos_order.items.all():
                            loc = getattr(item, 'fulfillment_location', 'Not set')
                            logger.info(f"   📦 Item: {item.name} → Location: {loc}")
                        
                        def _add_note():
                            if not internal_note:
                                return
                            try:
                                note_data = {
                                    'note': internal_note,
                                    'customer_note': False,
                                    'added_by_user': True
                                }
                                note_result = wc.add_order_note(woo_order_id, note_data)
                                if note_result.get('status') == 'success':
                                    logger.info(f"✅ Added internal note to WooCommerce order {woo_order_id}")
                                else:
                                    logger.warning(f"Failed to add internal note: {note_result}")
                            except Exception as e:
                                logger.error(f"Error adding internal note: {str(e)}")
                        
                        def _update_status():
                            try:
                                # Include the correct total in the status update to prevent
                                # WC Product Bundles from overriding it during calculate_totals()
                                update_data = {
                                    'status': final_status,
                                    'force': True,
                                    'total': f"{calculated_order_total:.2f}",
                                }
                                logger.info(f"🔥 Status update payload: status={final_status}, total=${calculated_order_total:.2f}")
                                update_response = wc.wcapi.put(f"orders/{woo_order_id}", update_data)
                                if update_response.ok:
                                    logger.info(f"✅ Order {woo_order_id} status updated to '{final_status}' with total ${calculated_order_total:.2f}")
                                    # Verify the total wasn't overridden
                                    resp_data = update_response.json()
                                    wc_total = float(resp_data.get('total', 0))
                                    if abs(wc_total - calculated_order_total) > 0.01:
                                        logger.warning(f"⚠️ WC total drift detected: sent ${calculated_order_total:.2f}, got back ${wc_total:.2f}")
                                else:
                                    logger.error(f"❌ Failed to update order status: {update_response.status_code} - {update_response.text[:500]}")
                                    # Fallback to standard method
                                    status_result = wc.update_order_status(woo_order_id, final_status)
                                    if status_result.get('status') == 'success':
                                        logger.info(f"✅ Order {woo_order_id} status updated via fallback")
                                    else:
                                        logger.error(f"❌ Fallback also failed: {status_result}")
                            except Exception as e:
                                logger.error(f"❌ Exception updating order status: {str(e)}")
                        
                        with ThreadPoolExecutor(max_workers=2) as post_executor:
                            post_futures = [
                                post_executor.submit(_add_note),
                                post_executor.submit(_update_status)
                            ]
                            for f in _as_completed(post_futures):
                                try:
                                    f.result()
                                except Exception as e:
                                    logger.error(f"Post-creation task error: {e}")
                        
                    else:
                        logger.error(f"Metadata is not a dictionary: {metadata}")
                except Exception as e:
                    logger.error(f"Error updating POS order metadata: {str(e)}")
        
            # Create subscriptions for subscription products
            subscription_created = False
            all_created_subscription_ids = []  # 🔥 FIX: Accumulate all subscription IDs across iterations
            logger.info(f"🔍 Checking subscription_items list: {subscription_items}")
            logger.info(f"🔍 subscription_items length: {len(subscription_items)}")
            if subscription_items:
                logger.info(f"Found {len(subscription_items)} subscription items to process")
                
                # 🔥 FIX: Move helper functions outside the loop (defined once, not per iteration)
                from datetime import datetime, timedelta
                import calendar

                def _add_months(dt, months):
                    # Add calendar months accurately without external deps (python-dateutil)
                    month_index = (dt.month - 1) + int(months)
                    year = dt.year + (month_index // 12)
                    month = (month_index % 12) + 1
                    last_day = calendar.monthrange(year, month)[1]
                    day = min(dt.day, last_day)
                    return dt.replace(year=year, month=month, day=day)

                def _add_years(dt, years):
                    return _add_months(dt, int(years) * 12)

                for subscription_item in subscription_items:
                  try:  # 🔥 FIX: Wrap each iteration in try/except so one failure doesn't abort remaining subscriptions
                    logger.info(f"Creating subscription for product: {subscription_item['name']}")

                    start_date = datetime.now()
                    
                    # 🆕 TRIAL MEMBERSHIP: Handle trial period calculation
                    is_trial_membership = subscription_item.get('is_trial_membership', False)
                    trial_period_months = subscription_item.get('trial_period_months', 0)
                    
                    if is_trial_membership and trial_period_months > 0:
                        # For trial memberships, first payment is after the trial period
                        # 🔧 FIX: Accurate calendar month calculation (avoid timedelta(days=30 * months))
                        next_payment = _add_months(start_date, trial_period_months)
                        logger.info(f"🆕 TRIAL MEMBERSHIP: Next payment scheduled after {trial_period_months} months trial period: {next_payment}")
                    else:
                        # Calculate next payment date based on period and interval (normal subscriptions)
                        if subscription_item['period'] == 'week':
                            next_payment = start_date + timedelta(weeks=subscription_item['interval'])
                        elif subscription_item['period'] == 'month':
                            # 🔧 FIX: Accurate calendar month calculation
                            next_payment = _add_months(start_date, subscription_item['interval'])
                        elif subscription_item['period'] == 'day':
                            next_payment = start_date + timedelta(days=subscription_item['interval'])
                        elif subscription_item['period'] == 'year':
                            # 🔧 FIX: Accurate calendar year calculation
                            next_payment = _add_years(start_date, subscription_item['interval'])
                        else:
                            next_payment = _add_months(start_date, 1)  # Default to monthly for memberships
                
                    # Format dates for WooCommerce API
                    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
                    next_payment_str = next_payment.strftime('%Y-%m-%d %H:%M:%S')
                
                    # Parse payment method from POS order
                    payment_method_title = 'Credit Card'  # Default fallback
                    logger.info(f"POS order payment_method field: {pos_order.payment_method}")
                    logger.info(f"POS order payment_method_title field: {getattr(pos_order, 'payment_method_title', 'Not found')}")
                    
                    try:
                        if pos_order.payment_method:
                            # Parse the JSON payment method from POS order
                            import json
                            payment_data = json.loads(pos_order.payment_method)
                            payment_method_title = payment_data.get('name', 'Credit Card')
                            logger.info(f"Parsed payment method from JSON: {payment_method_title}")
                    except (json.JSONDecodeError, AttributeError) as e:
                        logger.warning(f"Could not parse payment method from POS order: {e}")
                        # Use payment_method_title if available as fallback
                        if hasattr(pos_order, 'payment_method_title') and pos_order.payment_method_title:
                            payment_method_title = pos_order.payment_method_title
                            logger.info(f"Using payment_method_title fallback: {payment_method_title}")
                    
                    logger.info(f"Final payment method title for subscription: {payment_method_title}")
                
                    # Extract transaction_id from payment method or use POS order transaction_id
                    transaction_id = ''
                    if pos_order.transaction_id:
                        transaction_id = pos_order.transaction_id
                    elif pos_order.payment_method:
                        try:
                            payment_method_data = json.loads(pos_order.payment_method)
                            transaction_id = payment_method_data.get('id', '')
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"Could not parse payment method JSON for transaction_id: {pos_order.payment_method}")
                    
                    logger.info(f"Using transaction_id for subscription: {transaction_id}")
                    
                    # Create subscription data
                    subscription_data = {
                        'status': 'active',
                        'customer_id': customer_id,
                        'billing_period': subscription_item['period'],
                        'billing_interval': subscription_item['interval'],
                        'start_date': start_date_str,
                        'next_payment_date': next_payment_str,
                        'order_id': woo_order_id,  # Link to the parent order
                        'parent_id': woo_order_id,  # Explicitly set parent ID
                        'payment_method': _map_payment_method_for_woocommerce(payment_method_title, pos_order.payment_method),
                        'payment_method_title': payment_method_title,
                        'transaction_id': transaction_id,  # Add transaction_id to subscription
                        'created_via': 'DS POS',  # Match the order's created_via
                        'source': 'DS POS',  # Match the order's source
                        'origin': 'DS POS',  # Match the order's origin
                        'line_items': [
                            {
                                'product_id': subscription_item['product_id'],
                                'quantity': subscription_item['quantity'],
                                # 🔥 FIX: Use actual_membership_price for ALL memberships (trial and non-trial)
                                # This ensures manual discounts on the initial charge never leak into recurring billing.
                                # subscription_item['subtotal'] already uses original_price from Fix 1 above,
                                # but actual_membership_price (from frontend metadata) is the most authoritative source.
                                'subtotal': subscription_item.get('actual_membership_price', subscription_item['subtotal']),
                                'total': subscription_item.get('actual_membership_price', subscription_item['total'])
                            }
                        ],
                        'meta_data': [
                            {
                                'key': '_pos_order_id',
                                'value': str(pos_order.id)
                            },
                            {
                                'key': '_pos_order_number',
                                'value': pos_order.order_number
                            },
                            {
                                'key': '_created_via',
                                'value': 'DS POS'
                            },
                            {
                                'key': '_source_name',
                                'value': 'DS POS'
                            },
                            {
                                'key': '_order_source',
                                'value': 'DS POS'
                            },
                            {
                                'key': 'source',
                                'value': 'DS POS'
                            },
                            {
                                'key': '_order_number',
                                'value': woo_order_id
                            },
                            {
                                'key': '_subscription_parent_order',
                                'value': woo_order_id
                            },
                            {
                                'key': '_related_orders',
                                'value': [woo_order_id]
                            },
                            {
                                'key': '_subscription_renewal',
                                'value': 'parent'
                            },
                            {
                                'key': '_payment_method_title',
                                'value': payment_method_title
                            },
                            {
                                'key': '_pos_payment_method',
                                'value': pos_order.payment_method
                            },
                            {
                                'key': '_pos_transaction_id',
                                'value': transaction_id
                            }
                        ]
                    }
                    
                    # 🆕 TRIAL MEMBERSHIP: Add trial-specific metadata
                    if is_trial_membership:
                        subscription_data['meta_data'].extend([
                            {
                                'key': '_is_trial_membership',
                                'value': 'true'
                            },
                            {
                                'key': '_trial_period_months',
                                'value': str(trial_period_months)
                            },
                            {
                                'key': '_actual_membership_price',
                                'value': subscription_item.get('actual_membership_price', subscription_item['total'])
                            },
                            {
                                'key': '_checkout_price',
                                'value': subscription_item.get('checkout_price', '0')
                            },
                            {
                                'key': '_trial_end_date',
                                'value': next_payment_str  # This is when the trial ends and billing starts
                            }
                        ])
                        logger.info(f"🆕 TRIAL MEMBERSHIP: Added trial metadata to subscription")
                
                    # Add billing information if customer exists
                    if pos_order.contact:
                        contact = pos_order.contact
                        billing_info = {
                            'first_name': contact.first_name or '',
                            'last_name': contact.last_name or '',
                            'email': contact.email or ''
                        }
                        
                        # Use contact's billing address for billing fields
                        if hasattr(contact, 'billing_address') and contact.billing_address:
                            logger.info(f"Using contact billing address for subscription billing: {contact.billing_address}, {contact.billing_city}, {contact.billing_state}, {contact.billing_postcode}")
                            billing_info.update({
                                'address_1': contact.billing_address,
                                'address_2': getattr(contact, 'billing_address_2', '') or '',
                                'city': contact.billing_city,
                                'state': contact.billing_state,
                                'postcode': contact.billing_postcode,
                                'country': contact.billing_country or contact.billing_state  # Use country if available, otherwise state as fallback
                            })
                        else:
                            logger.warning("No billing or shipping address available for subscription")
                            
                        # Add phone if available
                        if hasattr(contact, 'phone') and contact.phone:
                            billing_info['phone'] = contact.phone
                            
                        subscription_data['billing'] = billing_info
                        logger.info(f"Final subscription billing data: {billing_info}")
                
                    # Add shipping information if available
                    # Use the same address for shipping as we did for billing to ensure consistency
                    if pos_order.contact:
                        shipping_info = {
                            'first_name': contact.first_name or '',
                            'last_name': contact.last_name or ''
                        }
                        
                        # Use contact's shipping address for shipping fields
                        if hasattr(contact, 'shipping_address') and contact.shipping_address:
                            logger.info(f"Using contact shipping address for subscription shipping: {contact.shipping_address}, {contact.shipping_city}, {contact.shipping_state}, {contact.shipping_postcode}")
                            shipping_info.update({
                                'address_1': contact.shipping_address,
                                'address_2': getattr(contact, 'shipping_address_2', '') or '',
                                'city': contact.shipping_city,
                                'state': contact.shipping_state,
                                'postcode': contact.shipping_postcode,
                                'country': contact.shipping_country or contact.shipping_state  # Use country if available, otherwise state as fallback
                            })
                        # Fall back to contact's billing address if shipping address is not available
                        elif hasattr(contact, 'billing_address') and contact.billing_address:
                            logger.info(f"Falling back to contact billing address for subscription shipping (shipping address not available): {contact.billing_address}, {contact.billing_city}, {contact.billing_state}, {contact.billing_postcode}")
                            shipping_info.update({
                                'address_1': contact.billing_address,
                                'address_2': getattr(contact, 'billing_address_2', '') or '',
                                'city': contact.billing_city,
                                'state': contact.billing_state,
                                'postcode': contact.billing_postcode,
                                'country': contact.billing_country or contact.billing_state  # Use country if available, otherwise state as fallback
                            })
                        else:
                            logger.warning("No shipping address available for subscription")
                            
                        # Add phone if available
                        if hasattr(contact, 'phone') and contact.phone:
                            shipping_info['phone'] = contact.phone
                            
                        subscription_data['shipping'] = shipping_info
                        logger.info(f"Final subscription shipping data: {shipping_info}")
                
                    # Log the subscription data being sent
                    logger.info(f"Subscription data being sent to WooCommerce: {json.dumps(subscription_data, indent=2)[:1500]}")
                    logger.info(f"Payment method in subscription data: {subscription_data.get('payment_method_title', 'NOT FOUND')}")
                
                    # Create the subscription in WooCommerce
                    # 🔥 Log final subscription line item pricing for verification
                    for li in subscription_data.get('line_items', []):
                        logger.info(f"🔥 FINAL SUBSCRIPTION LINE ITEM: product_id={li.get('product_id')}, qty={li.get('quantity')}, subtotal={li.get('subtotal')}, total={li.get('total')}")
                    logger.info(f"Creating WooCommerce subscription with payment method: {subscription_data.get('payment_method_title')}")
                    subscription_result = wc.create_subscription(subscription_data)
                    logger.info(f"WooCommerce subscription creation result: {subscription_result}")
                
                    if subscription_result.get('status') == 'success':
                        subscription_created = True
                        logger.info(f"Subscription created successfully with payment method: {payment_method_title}")
                        # Update the POS order with the WooCommerce subscription ID
                        woo_subscription_id = None
                        woo_sub_data = subscription_result.get('data', {})
                        
                        # Log the created subscription data to check payment method
                        if isinstance(woo_sub_data, dict):
                            created_payment_method = woo_sub_data.get('payment_method_title', 'NOT FOUND')
                            logger.info(f"Created subscription payment method title: {created_payment_method}")
                            if created_payment_method != payment_method_title:
                                logger.warning(f"Payment method mismatch! Expected: {payment_method_title}, Got: {created_payment_method}")
                    
                        # Check if data is a dictionary or string
                        if isinstance(woo_sub_data, dict):
                            woo_subscription_id = woo_sub_data.get('id')
                        elif isinstance(woo_sub_data, str):
                            # Try to parse JSON if it's a string
                            try:
                                import json
                                woo_sub_data_dict = json.loads(woo_sub_data)
                                woo_subscription_id = woo_sub_data_dict.get('id')
                            except Exception as e:
                                logger.error(f"Error parsing WooCommerce subscription response: {str(e)}")
                        
                        # Update subscription with transaction_id if we have both subscription ID and transaction_id
                        if woo_subscription_id and transaction_id:
                            try:
                                logger.info(f"Updating WooCommerce subscription {woo_subscription_id} with transaction_id: {transaction_id}")
                                
                                # Try multiple approaches to set transaction_id
                                # Approach 1: Set transaction_id directly
                                update_data = {
                                    'transaction_id': transaction_id,
                                    'meta_data': [
                                        {
                                            'key': '_transaction_id',
                                            'value': transaction_id
                                        },
                                        {
                                            'key': '_payment_transaction_id', 
                                            'value': transaction_id
                                        }
                                    ]
                                }
                                
                                update_result = wc.wcapi.put(f"subscriptions/{woo_subscription_id}", update_data)
                                logger.info(f"Update result status: {update_result.status_code}")
                                logger.info(f"Update result response: {update_result.text[:1000]}")
                                
                                if update_result.status_code == 200:
                                    logger.info(f"✅ Successfully updated subscription {woo_subscription_id} with transaction_id")
                                    
                                    # Verify the update by fetching the subscription
                                    verify_result = wc.wcapi.get(f"subscriptions/{woo_subscription_id}")
                                    if verify_result.status_code == 200:
                                        verify_data = verify_result.json()
                                        actual_transaction_id = verify_data.get('transaction_id', '')
                                        logger.info(f"Verification: subscription transaction_id is now: '{actual_transaction_id}'")
                                        if actual_transaction_id == transaction_id:
                                            logger.info(f"✅ Transaction ID successfully set in subscription!")
                                        else:
                                            logger.warning(f"⚠️ Transaction ID not set properly. Expected: '{transaction_id}', Got: '{actual_transaction_id}'")
                                else:
                                    logger.warning(f"⚠️ Failed to update subscription transaction_id. Status: {update_result.status_code}, Response: {update_result.text[:500]}")
                            except Exception as e:
                                logger.error(f"❌ Error updating subscription transaction_id: {str(e)}")
                        
                        # Link Authorize.net CIM payment profile to subscription for automatic renewals
                        if woo_subscription_id and pos_order.contact:
                            try:
                                cim_profile_id = getattr(pos_order.contact, 'authorize_net_customer_profile_id', None)
                                if cim_profile_id and str(cim_profile_id).strip().isdigit():
                                    cim_profile_id = str(cim_profile_id).strip()
                                    logger.info(f"🔗 Linking CIM profile {cim_profile_id} to subscription {woo_subscription_id}")
                                    
                                    from payments.authorize_net import AuthorizeNetGateway
                                    profiles = AuthorizeNetGateway.get_customer_payment_profiles(cim_profile_id)
                                    
                                    if profiles:
                                        # Try to match by card_last4 from the payment
                                        matched_profile = None
                                        if card_last4:
                                            for p in profiles:
                                                if p.get('last4') == str(card_last4):
                                                    matched_profile = p
                                                    break
                                        # Fallback: use the first (most recent) profile
                                        if not matched_profile:
                                            matched_profile = profiles[0]
                                        
                                        payment_profile_id = matched_profile['payment_profile_id']
                                        cim_brand = matched_profile.get('card_brand', 'Credit Card')
                                        cim_last4 = matched_profile.get('last4', '')
                                        cim_title = f'{cim_brand} ending in {cim_last4}' if cim_brand and cim_last4 else payment_method_title
                                        
                                        cim_update_data = {
                                            'payment_method': 'authnet',
                                            'payment_method_title': cim_title,
                                            'meta_data': [
                                                {'key': '_authnet_customer_id', 'value': cim_profile_id},
                                                {'key': '_authnet_card_id', 'value': str(payment_profile_id)},
                                                {'key': '_authnet_cc_type', 'value': cim_brand},
                                                {'key': '_authnet_cc_last4', 'value': cim_last4},
                                            ]
                                        }
                                        cim_result = wc.wcapi.put(f"subscriptions/{woo_subscription_id}", cim_update_data)
                                        if cim_result.status_code == 200:
                                            logger.info(f"✅ Linked CIM card {cim_brand} ****{cim_last4} (profile {payment_profile_id}) to subscription {woo_subscription_id}")
                                        else:
                                            logger.warning(f"⚠️ Failed to link CIM profile to subscription. Status: {cim_result.status_code}")
                                    else:
                                        logger.warning(f"⚠️ No CIM payment profiles found for customer profile {cim_profile_id}")
                                else:
                                    logger.info(f"ℹ️ No CIM profile on Contact — subscription will use manual renewal")
                            except Exception as e:
                                logger.error(f"❌ Error linking CIM profile to subscription: {str(e)}")
                    
                        if woo_subscription_id:
                            try:
                                metadata = pos_order.metadata or {}
                            
                                # Convert metadata to dictionary if it's a string
                                if isinstance(metadata, str):
                                    try:
                                        metadata = json.loads(metadata)
                                    except json.JSONDecodeError:
                                        metadata = {}
                            
                                if isinstance(metadata, dict):
                                    # Store as a list if multiple subscriptions
                                    if 'woo_subscription_ids' not in metadata:
                                        metadata['woo_subscription_ids'] = []
                                    metadata['woo_subscription_ids'].append(woo_subscription_id)
                                    pos_order.metadata = metadata
                                    pos_order.save()
                                    logger.info(f"Successfully created subscription with ID: {woo_subscription_id}")
                                else:
                                    logger.error(f"Metadata is not a dictionary: {metadata}")
                            except Exception as e:
                                logger.error(f"Error updating POS order metadata with subscription ID: {str(e)}")
                    
                        # 🔥 FIX: Accumulate subscription ID for parent order update after loop
                        all_created_subscription_ids.append(woo_subscription_id)
                        
                        # Add order note showing initial charge vs recurring price for admin visibility
                        try:
                            initial_charge = subscription_item.get('checkout_price', subscription_item.get('subtotal', '0'))
                            recurring_price = subscription_item.get('actual_membership_price', subscription_item['subtotal'])
                            period = subscription_item.get('period', 'month')
                            item_name = subscription_item.get('name', 'Subscription')
                            
                            # Only add note if there's a price difference (i.e. a discount was applied)
                            if float(initial_charge) != float(recurring_price):
                                note_text = (
                                    f"DS POS Subscription: {item_name}\n"
                                    f"Initial charge (today): ${float(initial_charge):.2f}\n"
                                    f"Recurring price: ${float(recurring_price):.2f}/{period}\n"
                                    f"Subscription ID: #{woo_subscription_id}"
                                )
                                note_data = {
                                    'note': note_text,
                                    'customer_note': False,
                                    'added_by_user': True
                                }
                                note_result = wc.add_order_note(woo_order_id, note_data)
                                if note_result.get('status') == 'success':
                                    logger.info(f"✅ Added subscription pricing note to order {woo_order_id}")
                                else:
                                    logger.warning(f"Failed to add subscription pricing note: {note_result}")
                        except Exception as e:
                            logger.error(f"Error adding subscription pricing note: {str(e)}")
                        
                        # Link the auto-created WC Membership to this Subscription
                        # WC Memberships plugin auto-creates (or reactivates) a membership
                        # when a membership product is purchased, but doesn't link it to
                        # the subscription when created via REST API.
                        # See docs/MEMBERSHIP_SUBSCRIPTION_LINKING.md
                        if woo_subscription_id and customer_id:
                            try:
                                memberships = wc.get_memberships(customer_id=customer_id)
                                linked = False
                                for m in memberships:
                                    m_order = m.get('order_id')
                                    m_sub = m.get('subscription_id')
                                    # Match by order_id (new membership) or unlinked membership
                                    if m_order == woo_order_id or not m_sub:
                                        link_data = {'subscription_id': woo_subscription_id}
                                        # Also update order_id if this was a reactivated old membership
                                        if m_order != woo_order_id:
                                            link_data['order_id'] = woo_order_id
                                        link_result = wc.update_membership(m['id'], link_data)
                                        if link_result.get('success'):
                                            logger.info(f"✅ Linked membership {m['id']} to subscription {woo_subscription_id}")
                                            linked = True
                                        else:
                                            logger.warning(f"⚠️ Failed to link membership {m['id']}: {link_result.get('error')}")
                                        break
                                if not linked:
                                    logger.info(f"ℹ️ No unlinked membership found for customer {customer_id} to link to subscription {woo_subscription_id}")
                            except Exception as e:
                                logger.error(f"❌ Error linking membership to subscription: {str(e)}")
                    else:
                        logger.error(f"Failed to create subscription for '{subscription_item.get('name', 'Unknown')}': {subscription_result.get('message', 'Unknown error')}")
                  except Exception as e:
                    logger.error(f"❌ Error creating subscription for '{subscription_item.get('name', 'Unknown')}': {str(e)}")
                    logger.exception("Full traceback:")
                    # Continue to next subscription item — don't abort the loop
                
                # 🔥 FIX: Update parent order with ALL subscription IDs once after the loop
                if all_created_subscription_ids:
                    try:
                        update_order_data = {
                            'meta_data': [
                                {
                                    'key': '_subscription_relationship_updated',
                                    'value': 'true'
                                },
                                {
                                    'key': '_subscription_renewal',
                                    'value': 'parent'
                                },
                                {
                                    'key': '_contains_subscription',
                                    'value': 'true'
                                },
                                {
                                    'key': '_related_subscriptions',
                                    'value': all_created_subscription_ids
                                }
                            ]
                        }
                        
                        update_result = wc.wcapi.put(f"orders/{woo_order_id}", update_order_data)
                        if update_result.ok:
                            logger.info(f"✅ Updated parent order {woo_order_id} with {len(all_created_subscription_ids)} subscription(s): {all_created_subscription_ids}")
                        else:
                            logger.error(f"Failed to update parent order with subscription relationship: {update_result.status_code}, {update_result.text}")
                    except Exception as e:
                        logger.error(f"❌ Error updating parent order with subscription IDs: {str(e)}")
                
                # 🔥 Summary logging
                logger.info(f"📊 SUBSCRIPTION CREATION SUMMARY: {len(all_created_subscription_ids)}/{len(subscription_items)} subscriptions created successfully. IDs: {all_created_subscription_ids}")
        
        # 🚀 PERFORMANCE: Run post-order background tasks (credit points, ATUM sync, GHL webhook)
        # in a daemon thread so the HTTP response returns immediately to the frontend.
        # These tasks don't affect the order itself — they update local DB caches and fire webhooks.
        if result.get('status') == 'success' and woo_order_id:
            import threading
            
            # Snapshot values needed by the background thread (avoid lazy-loading issues)
            _pos_order_id = str(pos_order.id)
            _pos_order_number = pos_order.order_number
            _contact_id = str(pos_order.contact.id) if pos_order.contact else None
            _contact_email = pos_order.contact.email if pos_order.contact else None
            _contact_phone = pos_order.contact.phone if pos_order.contact else None
            _contact_first_name = pos_order.contact.first_name if pos_order.contact else None
            _contact_last_name = pos_order.contact.last_name if pos_order.contact else None
            _contact_ghl_id = getattr(pos_order.contact, 'ghl_contact_id', '') if pos_order.contact else ''
            _woo_order_id = woo_order_id
            _woo_data = result.get('data', {})
            _skip_ghl_email = skip_ghl_email
            _has_contact = pos_order.contact is not None
            
            # Pre-fetch order items data before entering the thread (avoids DB access from thread)
            _order_items_for_credit = []
            try:
                from .models import Product as _Product
                for item in pos_order.items.all():
                    try:
                        product = _Product.objects.filter(woo_product_id=item.product_id).first()
                        if product:
                            metadata = {}
                            if hasattr(item, 'metadata') and item.metadata:
                                try:
                                    if isinstance(item.metadata, str):
                                        metadata = json.loads(item.metadata)
                                    else:
                                        metadata = item.metadata
                                    if not isinstance(metadata, dict):
                                        metadata = {}
                                except:
                                    metadata = {}
                            
                            series_count = item.quantity
                            if metadata.get('series_selection') is not None:
                                series_count = metadata.get('series_selection')
                            elif metadata.get('series_count') is not None:
                                series_count = metadata.get('series_count')
                            try:
                                series_count = int(series_count)
                            except (ValueError, TypeError):
                                series_count = item.quantity
                            
                            _order_items_for_credit.append({
                                'product_id': str(product.id),
                                'quantity': item.quantity,
                                'series_count': series_count,
                                'product_type': product.product_type,
                                'name': item.name,
                                'metadata': metadata
                            })
                    except Exception as e:
                        logger.warning(f"Could not prepare credit points data for item {item.name}: {str(e)}")
            except Exception as e:
                logger.warning(f"Could not prepare credit points items: {str(e)}")
            
            def _run_post_order_background_tasks():
                """Background thread: credit points + ATUM sync + GHL webhook."""
                import django
                from django.db import connection
                try:
                    # Ensure Django DB connection is usable in this thread
                    connection.ensure_connection()
                    
                    # ── 1. Credit service points ──
                    try:
                        from .models import CreditServicePoints
                        
                        order_data = {
                            'order_id': _pos_order_number,
                            'customer_id': _contact_id,
                            'items': _order_items_for_credit
                        }
                        
                        if order_data['customer_id'] and order_data['items']:
                            logger.info(f"[BG] Processing credit service points for order {_pos_order_number}")
                            credit_result = CreditServicePoints.process_order_completion(order_data)
                            processed_items = credit_result.get('processed_items', []) if isinstance(credit_result, dict) else []
                            added_items = [item for item in processed_items if item.get('action') == 'added']
                            skipped_items = [item for item in processed_items if item.get('action') == 'skipped']
                            deducted_items = [item for item in processed_items if item.get('action') == 'deducted']
                            errors = credit_result.get('errors', []) if isinstance(credit_result, dict) else []

                            logger.info(
                                f"[BG] Credit points summary for order {_pos_order_number}: "
                                f"added={len(added_items)} skipped={len(skipped_items)} "
                                f"deducted={len(deducted_items)} "
                                f"total_added={credit_result.get('total_points_added', 0)} "
                                f"total_deducted={credit_result.get('total_points_deducted', 0)}"
                            )

                            if skipped_items:
                                skip_reasons = [
                                    f"{item.get('product_name', 'Unknown')}: {item.get('reason', 'No reason provided')}"
                                    for item in skipped_items
                                ]
                                logger.warning(
                                    f"[BG] Credit points skipped items for order {_pos_order_number}: {skip_reasons}"
                                )

                            if errors:
                                logger.error(f"[BG] Credit points errors for order {_pos_order_number}: {errors}")

                            # region agent log
                            try:
                                from pathlib import Path
                                import json as _json
                                debug_payload = {
                                    "sessionId": "4b57f8",
                                    "runId": f"order-{_pos_order_number}",
                                    "hypothesisId": "H4",
                                    "location": "crm/views.py:_run_post_order_background_tasks:credit_summary",
                                    "message": "POS credit processing summary",
                                    "data": {
                                        "orderNumber": _pos_order_number,
                                        "hasCustomer": bool(order_data.get("customer_id")),
                                        "itemCount": len(order_data.get("items", [])),
                                        "addedItems": len(added_items),
                                        "skippedItems": len(skipped_items),
                                        "deductedItems": len(deducted_items),
                                        "totalPointsAdded": credit_result.get("total_points_added", 0),
                                        "totalPointsDeducted": credit_result.get("total_points_deducted", 0),
                                        "errorCount": len(errors),
                                    },
                                    "timestamp": int(timezone.now().timestamp() * 1000),
                                }
                                with Path(r"c:\laragon\www\NEWPOS\woo-ghl-contact-db\debug-4b57f8.log").open("a", encoding="utf-8") as _dbg_f:
                                    _dbg_f.write(_json.dumps(debug_payload, ensure_ascii=True) + "\n")
                            except Exception:
                                pass
                            # endregion

                            # region agent log
                            try:
                                from pathlib import Path
                                import json as _json
                                compact_skips = [
                                    {
                                        "productName": str(item.get("product_name", ""))[:120],
                                        "reason": str(item.get("reason", ""))[:200],
                                        "points": item.get("points", 0),
                                    }
                                    for item in skipped_items[:5]
                                ]
                                debug_payload = {
                                    "sessionId": "4b57f8",
                                    "runId": f"order-{_pos_order_number}",
                                    "hypothesisId": "H5",
                                    "location": "crm/views.py:_run_post_order_background_tasks:skip_details",
                                    "message": "POS credit skipped item details",
                                    "data": {
                                        "orderNumber": _pos_order_number,
                                        "skippedPreview": compact_skips,
                                    },
                                    "timestamp": int(timezone.now().timestamp() * 1000),
                                }
                                with Path(r"c:\laragon\www\NEWPOS\woo-ghl-contact-db\debug-4b57f8.log").open("a", encoding="utf-8") as _dbg_f:
                                    _dbg_f.write(_json.dumps(debug_payload, ensure_ascii=True) + "\n")
                            except Exception:
                                pass
                            # endregion

                            logger.info(f"[BG] Credit points detailed results: {credit_result}")
                        else:
                            logger.info("[BG] Skipping credit points - no customer or no eligible items")
                    except Exception as e:
                        logger.error(f"[BG] Error processing credit service points: {str(e)}")
                    
                    # ── 2. ATUM inventory sync ──
                    try:
                        from .atum_webhook_sync import sync_product_atum_inventory
                        from .models import Product
                        import time
                        
                        logger.info("[BG] " + "="*70)
                        logger.info("[BG] 🔄 STARTING ATUM INVENTORY SYNC AFTER ORDER CREATION")
                        logger.info("[BG] " + "="*70)
                        
                        # Wait for WordPress plugin to complete ATUM deduction
                        logger.info("[BG] ⏱️ Waiting 3 seconds for WordPress plugin to complete ATUM deduction...")
                        time.sleep(3)
                        logger.info("[BG] ⏱️ Wait complete, starting sync...")
                        
                        product_ids_to_sync = set()
                        
                        if isinstance(_woo_data, dict) and 'line_items' in _woo_data:
                            for line_item in _woo_data['line_items']:
                                pid = line_item.get('product_id')
                                vid = line_item.get('variation_id')
                                if vid and vid > 0 and pid and pid > 0:
                                    product_ids_to_sync.add(pid)
                                elif pid and pid > 0:
                                    product_ids_to_sync.add(pid)
                        
                        logger.info(f"[BG] 🔄 Found {len(product_ids_to_sync)} unique products to sync: {sorted(product_ids_to_sync)}")
                        
                        for woo_pid in product_ids_to_sync:
                            try:
                                product = Product.objects.filter(woo_product_id=woo_pid).first()
                                if product:
                                    logger.info(f"[BG] 🔄 Syncing ATUM inventory for: {product.name} (WooID: {woo_pid})")
                                    sync_result = sync_product_atum_inventory(product, dry_run=False)
                                    if sync_result.get('success'):
                                        logger.info(f"[BG] ✅ Sync complete for {product.name}: "
                                                     f"locations_added={sync_result.get('locations_added', 0)}, "
                                                     f"locations_removed={sync_result.get('locations_removed', 0)}")
                                    else:
                                        logger.warning(f"[BG] ⚠️ Sync failed for {product.name}: {sync_result.get('errors', [])}")
                                else:
                                    logger.warning(f"[BG] ⚠️ Product with WooCommerce ID {woo_pid} not found in Django DB")
                            except Exception as sync_err:
                                logger.error(f"[BG] ❌ Error syncing product {woo_pid}: {str(sync_err)}")
                        
                        logger.info("[BG] ✅ ATUM INVENTORY SYNC COMPLETE")
                    except Exception as e:
                        logger.error(f"[BG] ❌ Error during ATUM inventory sync: {str(e)}")
                    
                    # ── 3. GHL skip-email webhook ──
                    if _skip_ghl_email and _has_contact:
                        try:
                            import requests as http_requests
                            ghl_webhook_url = 'https://services.leadconnectorhq.com/hooks/pgfekl6sKgofVPSuYOJo/webhook-trigger/0954f538-5952-4f0c-9b05-ac4380f09985'
                            webhook_payload = {
                                'event': 'skip_ghl_email',
                                'email': _contact_email or '',
                                'phone': _contact_phone or '',
                                'ghl_contact_id': _contact_ghl_id or '',
                                'first_name': _contact_first_name or '',
                                'last_name': _contact_last_name or '',
                                'woo_order_id': str(_woo_order_id),
                                'pos_order_id': _pos_order_id,
                            }
                            logger.info(f"[BG] 🔕 Firing GHL skip-email webhook for {_contact_email}")
                            webhook_resp = http_requests.post(ghl_webhook_url, json=webhook_payload, timeout=10)
                            logger.info(f"[BG] 🔕 GHL webhook response: {webhook_resp.status_code}")
                        except Exception as webhook_err:
                            logger.error(f"[BG] 🔕 GHL skip-email webhook failed: {str(webhook_err)}")
                    
                finally:
                    # Close DB connection opened in this thread
                    connection.close()
            
            # Fire the background thread (daemon=True so it won't block process exit)
            bg_thread = threading.Thread(target=_run_post_order_background_tasks, daemon=True)
            bg_thread.start()
            logger.info(f"🚀 Background post-order tasks dispatched for WooCommerce order {woo_order_id}")

        # Include subscription creation status in response
        if result.get('status') == 'success':
            response_data = {
                'status': 'success',
                'message': f'Order successfully created in WooCommerce with ID: {woo_order_id}',
                'woo_order_id': woo_order_id,
                'pos_order_id': pos_order_id,
                'subscription_created': subscription_created
            }
                
            return Response(response_data)
        else:
            error_msg = result.get('message', 'Failed to create order in WooCommerce')
            queue_entry = _queue_failed_order(
                pos_order=pos_order,
                failed_step='woocommerce_order',
                error_message=error_msg,
                request_payload=request.data,
            )
            return Response({
                'status': 'queued',
                'message': (
                    'Order is queued — DO NOT reprocess. '
                    'WooCommerce is unresponsive. The payment has already been captured. '
                    'The order will be retried automatically.'
                ),
                'pos_order_id': pos_order_id,
                'queue_id': str(queue_entry.id) if queue_entry else None,
                'original_error': error_msg,
            }, status=status.HTTP_202_ACCEPTED)
    except Exception as e:
        error_msg = f'Error creating WooCommerce order: {str(e)}'
        import traceback as _tb
        tb = _tb.format_exc()
        _pos_order_ref = locals().get('pos_order')
        if _pos_order_ref:
            queue_entry = _queue_failed_order(
                pos_order=_pos_order_ref,
                failed_step='woocommerce_order',
                error_message=error_msg,
                error_traceback=tb,
                request_payload=request.data,
            )
            return Response({
                'status': 'queued',
                'message': (
                    'Order is queued — DO NOT reprocess. '
                    'An error occurred after payment was captured. '
                    'The order will be retried automatically.'
                ),
                'pos_order_id': str(_pos_order_ref.id),
                'queue_id': str(queue_entry.id) if queue_entry else None,
                'original_error': error_msg,
            }, status=status.HTTP_202_ACCEPTED)
        return Response({
            'status': 'error',
            'message': error_msg,
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_sync_status(request):
    return Response(sync_status)

@api_view(['POST'])
def stop_sync(request):
    """Stop the ongoing sync process."""
    sync_status['should_stop'] = True
    sync_status['status'] = 'stopping'
    sync_status['message'] = 'Stopping sync process...'
    return Response({'message': 'Stopping sync process'})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_subscription_products(request):
    """Sync subscription products and create ProductSubscription entries for products with subscription options."""
    try:
        # Get products from request or fetch from WooCommerce
        products_data = request.data.get('products', [])
        
        subscription_products_created = 0
        subscription_products_updated = 0
        
        logger.info(f"Starting subscription products sync for {len(products_data)} products")
        
        for product_data in products_data:
            product_id = product_data.get('id')
            product_name = product_data.get('name', 'Unknown')
            
            # Check if this product has subscription options
            has_subscription_options = (
                product_data.get('subscriptionOptions') and len(product_data.get('subscriptionOptions', [])) > 0
            ) or product_data.get('isSubscription', False)
            
            if has_subscription_options:
                logger.info(f"Found product with subscription options: {product_name} (ID: {product_id})")
                
                try:
                    # Try to find the product in our database
                    db_product = None
                    
                    # Try by product ID first
                    if product_id:
                        db_product = Product.objects.filter(id=product_id).first()
                    
                    # If not found, try by name
                    if not db_product:
                        db_product = Product.objects.filter(name__iexact=product_name).first()
                    
                    # If not found, try by SKU
                    if not db_product and product_data.get('sku'):
                        db_product = Product.objects.filter(sku=product_data.get('sku')).first()
                    
                    if db_product:
                        # Check if ProductSubscription already exists
                        existing_subscription = ProductSubscription.objects.filter(product=db_product).first()
                        
                        # Get subscription options from product data
                        subscription_options = product_data.get('subscriptionOptions', [])
                        
                        # Use the first subscription option as default, or create default values
                        if subscription_options:
                            first_option = subscription_options[0]
                            period = 'week'  # Default
                            interval = 2     # Default
                            
                            # Map billing frequency to period and interval
                            billing_freq = first_option.get('billingFrequency', 'biweekly')
                            if billing_freq == 'weekly':
                                period = 'week'
                                interval = 1
                            elif billing_freq == 'biweekly':
                                period = 'week'
                                interval = 2
                            elif billing_freq == 'monthly':
                                period = 'month'
                                interval = 1
                            elif billing_freq == 'bimonthly':
                                period = 'month'
                                interval = 2
                            elif billing_freq == 'quarterly':
                                period = 'month'
                                interval = 3
                            elif billing_freq == 'yearly':
                                period = 'year'
                                interval = 1
                        else:
                            # Default subscription settings
                            period = 'week'
                            interval = 2
                        
                        # Prepare subscription data
                        subscription_data = {
                            'period': period,
                            'interval': interval,
                            'woo_data': product_data
                        }
                        
                        if existing_subscription:
                            # Update existing
                            existing_subscription.period = period
                            existing_subscription.interval = interval
                            existing_subscription.woo_data = product_data
                            existing_subscription.save()
                            subscription_products_updated += 1
                            logger.info(f"Updated ProductSubscription for {product_name}")
                        else:
                            # Create new
                            ProductSubscription.objects.create(
                                product=db_product,
                                **subscription_data
                            )
                            subscription_products_created += 1
                            logger.info(f"Created ProductSubscription for {product_name}")
                    else:
                        logger.warning(f"Product not found in database: {product_name} (ID: {product_id})")
                        
                except Exception as e:
                    logger.error(f"Error processing subscription product {product_name}: {str(e)}")
                    continue
        
        return Response({
            'status': 'success',
            'message': f'Subscription products sync completed',
            'products_processed': len(products_data),
            'subscription_products_created': subscription_products_created,
            'subscription_products_updated': subscription_products_updated
        })
        
    except Exception as e:
        logger.error(f"Error during subscription products sync: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'Error during subscription products sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_woocommerce_data(request):
    """Sync data from WooCommerce."""
    try:
        # Reset stop flag
        sync_status['should_stop'] = False
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get sync type from request
        sync_type = request.data.get('type')
        logger.info(f"Received sync request with type: {sync_type}")
        logger.info(f"Request data: {request.data}")
        logger.info(f"Request content type: {request.content_type}")
        logger.info(f"Request headers: {request.headers}")
        
        # Validate sync type
        valid_types = ['customers', 'products', 'orders']
        if sync_type and sync_type not in valid_types:
            logger.error(f"Invalid sync type received: {sync_type}")
            return Response({'error': f'Invalid sync type. Must be one of: {", ".join(valid_types)}'}, status=400)
        
        # If no type specified, sync all
        if sync_type:
            sync_types = [sync_type]
            logger.info(f"Will sync only: {sync_type}")
        else:
            sync_types = valid_types
            logger.info("No type specified, will sync all types")
            
        logger.info(f"Will sync the following types: {sync_types}")
            
        for current_type in sync_types:
            logger.info(f"Starting sync for type: {current_type}")
            
            if sync_status['should_stop']:
                sync_status['status'] = 'stopped'
                sync_status['message'] = 'Sync process stopped by user'
                return Response({'message': 'Sync process stopped', 'status': 'stopped'})
                
            if current_type == 'customers':
                # Sync customers
                sync_status['status'] = 'in_progress'
                sync_status['message'] = 'Starting customer sync...'
                sync_status['progress'] = {'current': 0, 'total': 0, 'type': 'customers'}
                
                logger.info("Starting WooCommerce customer sync")
                
                # Use get_all_customers to fetch all customers with pagination
                customers = wc.get_all_customers()
                
                if customers:
                    total_customers = len(customers)
                    sync_status['progress']['total'] = total_customers
                    processed_count = 0
                    
                    for customer in customers:
                        if sync_status['should_stop']:
                            sync_status['status'] = 'stopped'
                            sync_status['message'] = 'Sync process stopped by user'
                            return Response({'message': 'Sync process stopped', 'status': 'stopped'})
                        
                        try:
                            process_customer(customer)
                            processed_count += 1
                            sync_status['progress']['current'] = processed_count
                            sync_status['message'] = f'Processing customer {processed_count}/{total_customers}'
                            logger.info(f"Processed customer {processed_count}/{total_customers}")
                        except Exception as e:
                            logger.error(f"Error processing customer: {str(e)}")
                
                if sync_type == 'customers':
                    logger.info("Completed customers sync, returning response")
                    sync_status['status'] = 'success'
                    sync_status['message'] = 'Customer sync completed successfully'
                    return Response({'message': 'Customer sync completed successfully', 'status': 'success'})
                else:
                    logger.info("Completed customers sync, continuing to next type")

            elif current_type == 'products':
                # Sync products
                sync_status['status'] = 'in_progress'
                sync_status['message'] = 'Starting product sync...'
                sync_status['progress'] = {'current': 0, 'total': 0, 'type': 'products'}
                
                logger.info("Starting WooCommerce product sync")
                
                # Get first page to determine total
                first_page = wc.get_products(page=1)
                if not first_page or 'total' not in first_page:
                    raise Exception("Failed to get product count from WooCommerce")
                    
                total_products = first_page['total']
                logger.info(f"Found {total_products} products to sync")
                
                sync_status['progress']['total'] = total_products
                processed_count = 0
                
                # Process first page results
                if first_page.get('data'):
                    for product in first_page['data']:
                        if sync_status['should_stop']:
                            sync_status['status'] = 'stopped'
                            sync_status['message'] = 'Sync process stopped by user'
                            return Response({'message': 'Sync process stopped', 'status': 'stopped'})
                            
                        try:
                            process_product(product, wc)
                            processed_count += 1
                            sync_status['progress']['current'] = processed_count
                            sync_status['message'] = f'Processing product {processed_count}/{total_products}'
                            logger.info(f"Processed product {processed_count}/{total_products}")
                        except Exception as e:
                            logger.error(f"Error processing product: {str(e)}")
                            
                # Process remaining pages
                current_page = 2
                total_pages = first_page.get('total_pages', 1)
                
                while current_page <= total_pages and processed_count < total_products:
                    if sync_status['should_stop']:
                        sync_status['status'] = 'stopped'
                        sync_status['message'] = 'Sync process stopped by user'
                        return Response({'message': 'Sync process stopped', 'status': 'stopped'})
                        
                    try:
                        logger.info(f"Processing page {current_page}/{total_pages}")
                        result = wc.get_products(page=current_page)
                        
                        if result and result.get('data'):
                            for product in result['data']:
                                if sync_status['should_stop']:
                                    sync_status['status'] = 'stopped'
                                    sync_status['message'] = 'Sync process stopped by user'
                                    return Response({'message': 'Sync process stopped', 'status': 'stopped'})
                                    
                                try:
                                    process_product(product, wc)
                                    processed_count += 1
                                    sync_status['progress']['current'] = processed_count
                                    sync_status['message'] = f'Processing product {processed_count}/{total_products}'
                                    logger.info(f"Processed product {processed_count}/{total_products}")
                                except Exception as e:
                                    logger.error(f"Error processing product: {str(e)}")
                                    continue
                                    
                        current_page += 1

                    except Exception as e:
                        logger.error(f"Error processing page {current_page}: {str(e)}")
                        sync_status['status'] = 'error'
                        sync_status['message'] = f'Error during sync: {str(e)}'
                        return Response({'error': str(e)}, status=500)

                if sync_type == 'products':
                    logger.info("Completed products sync, returning response")
                    sync_status['status'] = 'success'
                    sync_status['message'] = 'Product sync completed successfully'
                    return Response({'message': 'Product sync completed successfully', 'status': 'success'})
                else:
                    logger.info("Completed products sync, continuing to next type")

            elif current_type == 'orders':
                # Sync orders
                sync_status['status'] = 'in_progress'
                sync_status['message'] = 'Starting order sync...'
                sync_status['progress'] = {'current': 0, 'total': 0, 'type': 'orders'}
                
                logger.info("Starting WooCommerce order sync")
                orders = wc.get_orders()
                
                if orders:
                    total_orders = len(orders)
                    sync_status['progress']['total'] = total_orders
                    processed_count = 0
                    
                    for order in orders:
                        if sync_status['should_stop']:
                            sync_status['status'] = 'stopped'
                            sync_status['message'] = 'Sync process stopped by user'
                            return Response({'message': 'Sync process stopped', 'status': 'stopped'})
                        
                        try:
                            # TODO: Add process_order function
                            processed_count += 1
                            sync_status['progress']['current'] = processed_count
                            sync_status['message'] = f'Processing order {processed_count}/{total_orders}'
                            logger.info(f"Processed order {processed_count}/{total_orders}")
                        except Exception as e:
                            logger.error(f"Error processing order: {str(e)}")

                if sync_type == 'orders':
                    logger.info("Completed orders sync, returning response")
                    sync_status['status'] = 'success'
                    sync_status['message'] = 'Order sync completed successfully'
                    return Response({'message': 'Order sync completed successfully', 'status': 'success'})
                else:
                    logger.info("Completed orders sync, continuing to next type")

        # Only reach here if no specific type was specified (syncing all)
        logger.info("Completed all sync operations")
        sync_status['status'] = 'success'
        sync_status['message'] = 'All sync operations completed successfully'
        return Response({'message': 'All sync operations completed successfully', 'status': 'success'})

    except Exception as e:
        logger.error(f"Error syncing with WooCommerce: {str(e)}")
        sync_status['status'] = 'error'
        sync_status['message'] = f'Error during sync: {str(e)}'
        return Response({'error': str(e)}, status=500)

def _normalize_email_for_linking(value):
    if not isinstance(value, str):
        return ''
    normalized = value.strip().lower()
    return normalized if '@' in normalized else ''


def _prefer_contact_for_woo_link(candidates):
    if not candidates:
        return None
    # Prefer records that already have GHL identity, then the most recently updated.
    return sorted(
        candidates,
        key=lambda c: (1 if c.ghl_contact_id else 0, c.updated_at or c.created_at),
        reverse=True
    )[0]


def _reassign_woo_link_for_merged_email(woo_customer_id, email, reason='sync'):
    normalized_email = _normalize_email_for_linking(email)
    try:
        woo_customer_id_int = int(woo_customer_id)
    except (TypeError, ValueError):
        woo_customer_id_int = None

    if not woo_customer_id_int or not normalized_email:
        return None

    candidates = list(
        Contact.objects.filter(email__iexact=normalized_email).order_by('-updated_at')
    )
    if len(candidates) < 2:
        return None

    owner = next((c for c in candidates if c.woo_customer_id == woo_customer_id_int), None)
    preferred = _prefer_contact_for_woo_link(candidates)

    if not preferred:
        return None

    if preferred.woo_customer_id == woo_customer_id_int:
        return preferred

    if preferred.woo_customer_id and preferred.woo_customer_id != woo_customer_id_int:
        logger.warning(
            f"Skipping Woo link reassignment for email {normalized_email}: "
            f"preferred contact {preferred.id} already linked to Woo ID {preferred.woo_customer_id}"
        )
        return owner or preferred

    with transaction.atomic():
        if owner and owner.id != preferred.id:
            owner.woo_customer_id = None
            owner.save(update_fields=['woo_customer_id'])

        preferred.woo_customer_id = woo_customer_id_int
        preferred.save(update_fields=['woo_customer_id'])

    logger.info(
        f"Reassigned Woo customer link {woo_customer_id_int} to contact {preferred.id} "
        f"for merged email {normalized_email} (reason={reason})"
    )

    return preferred


def process_customer(customer):
    """Process a customer from WooCommerce and create/update in our database."""
    try:
        contact, created = Contact.objects.update_or_create(
            woo_customer_id=customer['id'],
            defaults={
                'first_name': customer['first_name'],
                'last_name': customer['last_name'],
                'email': customer['email'],
                'phone': customer['billing'].get('phone', ''),
                'billing_address': customer['billing'].get('address_1', ''),
                'billing_address_2': customer['billing'].get('address_2', ''),
                'billing_city': customer['billing'].get('city', ''),
                'billing_state': customer['billing'].get('state', ''),
                'billing_postcode': customer['billing'].get('postcode', ''),
                'billing_country': customer['billing'].get('country', ''),
                'shipping_address': customer['shipping'].get('address_1', ''),
                'shipping_address_2': customer['shipping'].get('address_2', ''),
                'shipping_city': customer['shipping'].get('city', ''),
                'shipping_state': customer['shipping'].get('state', ''),
                'shipping_postcode': customer['shipping'].get('postcode', ''),
                'shipping_country': customer['shipping'].get('country', ''),
                'shipping_same_as_billing': (
                    customer['billing'].get('address_1', '') == customer['shipping'].get('address_1', '') and
                    customer['billing'].get('city', '') == customer['shipping'].get('city', '') and
                    customer['billing'].get('state', '') == customer['shipping'].get('state', '') and
                    customer['billing'].get('postcode', '') == customer['shipping'].get('postcode', '') and
                    customer['billing'].get('country', '') == customer['shipping'].get('country', '')
                )
            }
        )
    except Exception as e:
        # If there's a unique constraint error (e.g., email already exists), 
        # try to find and update the existing contact with this email
        if 'unique constraint' in str(e).lower() and 'email' in str(e).lower():
            logger.warning(f"Email {customer['email']} already exists, attempting to find existing contact")
            try:
                # Find existing contact with this email
                existing_contact = Contact.objects.get(email=customer['email'])
                
                # Update the existing contact with the new WooCommerce ID and data
                existing_contact.woo_customer_id = customer['id']
                existing_contact.first_name = customer['first_name']
                existing_contact.last_name = customer['last_name']
                existing_contact.phone = customer['billing'].get('phone', '')
                existing_contact.billing_address = customer['billing'].get('address_1', '')
                existing_contact.billing_address_2 = customer['billing'].get('address_2', '')
                existing_contact.billing_city = customer['billing'].get('city', '')
                existing_contact.billing_state = customer['billing'].get('state', '')
                existing_contact.billing_postcode = customer['billing'].get('postcode', '')
                existing_contact.billing_country = customer['billing'].get('country', '')
                existing_contact.shipping_address = customer['shipping'].get('address_1', '')
                existing_contact.shipping_address_2 = customer['shipping'].get('address_2', '')
                existing_contact.shipping_city = customer['shipping'].get('city', '')
                existing_contact.shipping_state = customer['shipping'].get('state', '')
                existing_contact.shipping_postcode = customer['shipping'].get('postcode', '')
                existing_contact.shipping_country = customer['shipping'].get('country', '')
                existing_contact.shipping_same_as_billing = (
                    customer['billing'].get('address_1', '') == customer['shipping'].get('address_1', '') and
                    customer['billing'].get('city', '') == customer['shipping'].get('city', '') and
                    customer['billing'].get('state', '') == customer['shipping'].get('state', '') and
                    customer['billing'].get('postcode', '') == customer['shipping'].get('postcode', '') and
                    customer['billing'].get('country', '') == customer['shipping'].get('country', '')
                )
                existing_contact.save()
                
                contact = existing_contact
                created = False
                logger.info(f"Updated existing contact {contact.id} with new WooCommerce ID {customer['id']}")
                
            except Contact.DoesNotExist:
                # This shouldn't happen, but just in case
                raise e
        else:
            raise e
    
    reassigned_contact = _reassign_woo_link_for_merged_email(
        customer.get('id'),
        customer.get('email'),
        reason='process_customer'
    )
    if reassigned_contact and reassigned_contact.id != contact.id:
        logger.info(
            f"Using reassigned contact {reassigned_contact.id} for Woo customer {customer.get('id')} "
            f"instead of contact {contact.id}"
        )
        contact = reassigned_contact

    # Attempt to sync GHL contact ID if missing
    if not contact.ghl_contact_id and contact.email:
        try:
            from .ghl_api import search_ghl_contact_by_email, apply_ghl_contact_fields
            from django.utils import timezone
            
            logger.info(f"Attempting to sync GHL contact ID for customer: {contact.email}")
            
            ghl_contact_data = search_ghl_contact_by_email(contact.email)
            if ghl_contact_data and 'id' in ghl_contact_data:
                ghl_contact_id = ghl_contact_data['id']
                
                # Update contact with GHL data
                contact.ghl_contact_id = ghl_contact_id
                contact.ghl_last_sync = timezone.now()
                
                # Store additional GHL data if available
                if isinstance(ghl_contact_data, dict):
                    contact.ghl_data = ghl_contact_data
                
                contact.save(update_fields=['ghl_contact_id', 'ghl_last_sync', 'ghl_data'])
                
                # Apply GHL fields to POS contact (GHL is source of truth)
                ghl_updated = apply_ghl_contact_fields(contact, ghl_contact_data)
                
                logger.info(f"✅ Successfully synced GHL contact ID for {contact.email}: {ghl_contact_id} (GHL fields updated: {ghl_updated})")
            else:
                logger.info(f"No GHL contact found for {contact.email}")
                
        except Exception as e:
            logger.warning(f"Failed to sync GHL contact ID for {contact.email}: {str(e)}")
    
    return contact

def process_product(product, wc=None):
    """Process a product from WooCommerce and create/update in our database.
    
    Args:
        product (dict): The product data from WooCommerce
        wc (WooCommerceAPI, optional): WooCommerce API instance. If not provided, a new instance will be created.
    """
    # Determine product type
    product_type = product.get('type', 'simple')
    
    # IMPORTANT: Skip processing variations as standalone products
    # Variations should only exist in ProductVariation table, not as Product records
    
    # Enhanced variation detection - check multiple indicators
    # A variation has type='variation' OR parent_id > 0
    # A variable product (parent) has type='variable' with attributes and variations array
    is_variation = (
        product_type == 'variation' or 
        ('parent_id' in product and product.get('parent_id', 0) > 0)
    )
    
    logger.info(f"Product {product['id']} type detection in process_product: type={product_type}, parent_id={product.get('parent_id', 0)}, is_variation={is_variation}")
    
    # Additional variation detection for webhooks - check if product has variation-like characteristics
    # ONLY for products that don't have explicit type information (edge cases)
    if not is_variation and 'name' in product and product_type not in ['variable', 'variable-subscription', 'simple', 'grouped', 'external']:
        product_name = product['name']
        # Check if the product name contains strong variation indicators
        # But avoid false positives with variable products that might have similar naming
        if '(' in product_name and ')' in product_name and ' - ' in product_name:
            # This might be a variation - let's check if there's a parent product
            base_name = product_name.split(' - ')[0].split('(')[0].strip()
            
            # Look for potential parent products with similar names
            potential_parents = Product.objects.filter(
                name__startswith=base_name,  # More specific match
                product_type__in=['variable', 'variable-subscription']
            ).exclude(woo_product_id=product['id'])
            
            if potential_parents.exists():
                logger.warning(f"Product {product['id']}: '{product_name}' appears to be a variation based on name pattern and existing variable parents")
                is_variation = True
    
    if is_variation:
        logger.info(f"Skipping variation {product['id']}: {product['name']} - variations should not be standalone products")
        return None
    
    # Check if this is a product created from POS order
    is_pos_created = False
    if product.get('description', '').strip().startswith('<p>Product created from POS order ORD-'):
        is_pos_created = True
        logger.info(f"Product {product['id']}: {product['name']} was created from a POS order")
    
    # Check if there are existing products with the same name (for POS-created products)
    existing_products_same_name = []
    if is_pos_created:
        # Find products with the same name but different IDs
        existing_products_same_name = list(Product.objects.filter(
            name=product['name']
        ).exclude(
            woo_product_id=product['id']
        ))
        
        if existing_products_same_name:
            logger.info(f"Found {len(existing_products_same_name)} existing products with the same name: {product['name']}")
            
            # If we have multiple matches, prefer the one that's not from POS
            original_product = None
            for p in existing_products_same_name:
                # Try to get the WooCommerce product to check its description
                try:
                    if wc is None:
                        wc = WooCommerceAPI()
                    
                    # Add detailed logging for debugging
                    logger.info(f"Checking existing product {p.woo_product_id}: {p.name} for POS deduplication")
                    
                    woo_product = wc.get_product(p.woo_product_id)
                    
                    # Check if WooCommerce API returned None
                    if woo_product is None:
                        logger.warning(f"WooCommerce API returned None for product {p.woo_product_id} - product may have been deleted from WooCommerce")
                        continue  # Skip this product and try the next one
                    
                    if woo_product and 'description' in woo_product:
                        if not woo_product['description'].strip().startswith('<p>Product created from POS order ORD-'):
                            original_product = p
                            logger.info(f"Found original product {p.woo_product_id}: {p.name}")
                            break
                    else:
                        logger.warning(f"Product {p.woo_product_id} missing description field in WooCommerce data")
                        
                except Exception as e:
                    logger.error(f"Error checking product {p.woo_product_id}: {str(e)}")
                    # Continue to next product instead of failing completely
                    continue
            
            # If we didn't find a non-POS product, just use the first one
            if original_product is None and existing_products_same_name:
                original_product = existing_products_same_name[0]
                logger.info(f"Using first matching product {original_product.woo_product_id}: {original_product.name}")
            
            # If we found an original product, update our database to point the POS product ID to the original product
            if original_product:
                # Add null check to prevent AttributeError: 'NoneType' object has no attribute 'id'
                if original_product.id is None:
                    logger.error(f"Original product {original_product.woo_product_id} has None ID - cannot proceed with deduplication")
                    # Continue with normal product creation instead of failing
                else:
                    logger.info(f"Updating product {product['id']} to reference original product {original_product.woo_product_id}")
                    
                    # Update any order items that reference this product to use the original product
                    try:
                        updated_count = POSOrderItem.objects.filter(product_id=str(product['id'])).update(product_id=str(original_product.id))
                        logger.info(f"Updated {updated_count} order items to reference original product {original_product.id}")
                        
                        # Skip further processing of this product since we're treating it as a duplicate
                        return original_product
                        
                    except Exception as e:
                        logger.error(f"Error updating order items: {str(e)}")
                        # Continue with normal product creation if order update fails
            else:
                logger.info(f"No suitable original product found for POS product {product['id']}: {product['name']} - will create new product record")
    
    # Check if product already exists by WooCommerce ID
    existing_product = Product.objects.filter(woo_product_id=product['id']).first()
    
    # Check if this product has been used in any orders
    product_in_orders = False
    if existing_product:
        # Check if the product ID is used in any order items
        product_id_str = str(existing_product.id)
        product_in_orders = POSOrderItem.objects.filter(product_id=product_id_str).exists()
        
        if product_in_orders:
            logger.info(f"Product {product['id']}: {product['name']} has existing orders. Updating with caution.")
    
    # If product exists and has been purchased, we need to be careful about updates
    if existing_product and product_in_orders:
        # Update only non-critical fields that won't affect past orders
        existing_product.name = product['name']
        existing_product.description = product['description']
        existing_product.status = product['status']
        existing_product.stock_status = product.get('stock_status', 'instock')
        existing_product.stock_quantity = product.get('stock_quantity', 0)
        existing_product.categories = [cat['name'] for cat in product.get('categories', [])]
        existing_product.images = [img['src'] for img in product.get('images', [])]
        
        # CRITICAL: Fetch fresh pricing data from WooCommerce API before updating
        fresh_price = float(product['price'] or 0)
        fresh_regular_price = float(product.get('regular_price') or 0)
        fresh_sale_price = float(product.get('sale_price') or 0)
        
        try:
            # Fetch fresh product data from WooCommerce with cache-busting
            import time
            cache_bust = int(time.time())
            
            if wc is None:
                wc = WooCommerceAPI()
            
            wc_response = wc.get_product(product['id'], params={'_cb': cache_bust})
            
            if wc_response and wc_response.get('data'):
                wc_product = wc_response['data']
                
                # Use fresh WooCommerce data
                fresh_price = float(wc_product.get('price') or 0)
                fresh_regular_price = float(wc_product.get('regular_price') or 0)
                fresh_sale_price = float(wc_product.get('sale_price') or 0)
                
                logger.info(f"📊 Using FRESH prices for product with orders {product['id']}:")
                logger.info(f"   Webhook data: price={product.get('price')}, regular={product.get('regular_price')}, sale={product.get('sale_price')}")
                logger.info(f"   Fresh from WC: price={fresh_price}, regular={fresh_regular_price}, sale={fresh_sale_price}")
            else:
                logger.warning(f"Could not fetch fresh product data for update: {product['id']} - using webhook data")
                
        except Exception as e:
            logger.error(f"Error fetching fresh pricing for product update {product['id']}: {str(e)} - using webhook data")
        
        # Only update prices if they've changed significantly (optional)
        if abs(fresh_price - existing_product.price) > 0.01:
            existing_product.price = fresh_price
            existing_product.regular_price = fresh_regular_price
            existing_product.sale_price = fresh_sale_price
            
        existing_product.save()
        product_obj = existing_product
        created = False
    else:
        # CRITICAL: Fetch fresh pricing data from WooCommerce API before creating product
        # This ensures we have the most up-to-date prices, not potentially stale webhook data
        fresh_price = float(product['price'] or 0)
        fresh_regular_price = float(product.get('regular_price') or 0)
        fresh_sale_price = float(product.get('sale_price') or 0)
        
        try:
            # Fetch fresh product data from WooCommerce with cache-busting
            import time
            cache_bust = int(time.time())
            
            if wc is None:
                wc = WooCommerceAPI()
            
            wc_response = wc.get_product(product['id'], params={'_cb': cache_bust})
            
            if wc_response and wc_response.get('data'):
                wc_product = wc_response['data']
                
                # Use fresh WooCommerce data instead of webhook data
                fresh_price = float(wc_product.get('price') or 0)
                fresh_regular_price = float(wc_product.get('regular_price') or 0)
                fresh_sale_price = float(wc_product.get('sale_price') or 0)
                
                logger.info(f"📊 Using FRESH prices for product creation {product['id']}:")
                logger.info(f"   Webhook data: price={product.get('price')}, regular={product.get('regular_price')}, sale={product.get('sale_price')}")
                logger.info(f"   Fresh from WC: price={fresh_price}, regular={fresh_regular_price}, sale={fresh_sale_price}")
            else:
                logger.warning(f"Could not fetch fresh product data for creation: {product['id']} - using webhook data")
                
        except Exception as e:
            logger.error(f"Error fetching fresh pricing for product creation {product['id']}: {str(e)} - using webhook data")
        
        # Create or update the product normally if it doesn't have orders
        product_obj, created = Product.objects.update_or_create(
            woo_product_id=product['id'],
            defaults={
                'name': product['name'],
                'description': product['description'],
                'price': fresh_price,
                'regular_price': fresh_regular_price,
                'sale_price': fresh_sale_price,
                'status': product['status'],
                'stock_status': product.get('stock_status', 'instock'),
                'stock_quantity': product.get('stock_quantity', 0),
                'product_type': product_type,
                'categories': [cat['name'] for cat in product.get('categories', [])],
                'images': [img['src'] for img in product.get('images', [])],
            }
        )
    
    logger.info(f"{'Created' if created else 'Updated'} product {product['id']}: {product['name']} (Type: {product_type})")
    
    # If no WooCommerce API instance provided, create one
    if wc is None:
        wc = WooCommerceAPI()
    
    # Handle variations if product is variable
    if product_type in ['variable', 'variable-subscription']:
        try:
            variations = wc.get_product_variations(product['id'])
            
            # For products with orders, be careful with variations
            if existing_product and product_in_orders:
                # Get existing variations
                existing_variations = {var.woo_variation_id: var for var in ProductVariation.objects.filter(product=product_obj)}
                
                # Update or create variations
                for variation in variations:
                    var_id = variation['id']
                    if var_id in existing_variations:
                        # Update existing variation
                        var_obj = existing_variations[var_id]
                        var_obj.sku = variation.get('sku', '')
                        var_obj.price = float(variation.get('price') or 0)
                        var_obj.regular_price = float(variation.get('regular_price') or 0)
                        var_obj.sale_price = float(variation.get('sale_price') or 0)
                        var_obj.stock_quantity = variation.get('stock_quantity', 0)
                        var_obj.attributes = variation.get('attributes', [])
                        var_obj.woo_data = variation
                        var_obj.save()
                    else:
                        # Create new variation
                        var_obj = ProductVariation.objects.create(
                            product=product_obj,
                            woo_variation_id=var_id,
                            sku=variation.get('sku', ''),
                            price=float(variation.get('price') or 0),
                            regular_price=float(variation.get('regular_price') or 0),
                            sale_price=float(variation.get('sale_price') or 0),
                            stock_quantity=variation.get('stock_quantity', 0),
                            attributes=variation.get('attributes', []),
                            woo_data=variation
                        )
                    
                    # Extract subscription metadata for this variation
                    try:
                        variation_subscription_data = extract_subscription_metadata(variation)
                        if variation_subscription_data['has_subscription']:
                            # Update woo_data with subscription metadata
                            if not isinstance(var_obj.woo_data, dict):
                                var_obj.woo_data = {}
                            var_obj.woo_data['subscription_metadata'] = variation_subscription_data
                            var_obj.save(update_fields=['woo_data'])
                        else:
                            # 🧹 CLEANUP: Remove subscription metadata if disabled
                            meta_data = variation.get('meta_data', [])
                            is_wcsatt_disabled = any(
                                meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
                                for meta in meta_data
                            )
                            
                            if is_wcsatt_disabled and var_obj.woo_data and isinstance(var_obj.woo_data, dict):
                                if 'subscription_metadata' in var_obj.woo_data:
                                    del var_obj.woo_data['subscription_metadata']
                                    var_obj.save(update_fields=['woo_data'])
                                    logger.info(f"🗑️ Cleaned subscription data from variation {variation['id']}")
                    except Exception as sub_error:
                        logger.error(f"Error extracting subscription metadata for variation {variation['id']}: {str(sub_error)}")
            else:
                # Delete existing variations for this product
                ProductVariation.objects.filter(product=product_obj).delete()
                
                # Create new variations and extract subscription metadata
                variations_with_subscriptions = 0
                for variation in variations:
                    var_obj = ProductVariation.objects.create(
                        product=product_obj,
                        woo_variation_id=variation['id'],
                        sku=variation.get('sku', ''),
                        price=float(variation.get('price') or 0),
                        regular_price=float(variation.get('regular_price') or 0),
                        sale_price=float(variation.get('sale_price') or 0),
                        stock_quantity=variation.get('stock_quantity', 0),
                        attributes=variation.get('attributes', []),
                        woo_data=variation
                    )
                    
                    # Extract subscription metadata for this variation
                    try:
                        variation_subscription_data = extract_subscription_metadata(variation)
                        if variation_subscription_data['has_subscription']:
                            # Update woo_data with subscription metadata
                            if not isinstance(var_obj.woo_data, dict):
                                var_obj.woo_data = {}
                            var_obj.woo_data['subscription_metadata'] = variation_subscription_data
                            var_obj.save(update_fields=['woo_data'])
                            variations_with_subscriptions += 1
                        else:
                            # 🧹 CLEANUP: Remove subscription metadata if disabled
                            meta_data = variation.get('meta_data', [])
                            is_wcsatt_disabled = any(
                                meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
                                for meta in meta_data
                            )
                            
                            if is_wcsatt_disabled and var_obj.woo_data and isinstance(var_obj.woo_data, dict):
                                if 'subscription_metadata' in var_obj.woo_data:
                                    del var_obj.woo_data['subscription_metadata']
                                    var_obj.save(update_fields=['woo_data'])
                                    logger.info(f"🗑️ Cleaned subscription data from variation {variation['id']}")
                    except Exception as sub_error:
                        logger.error(f"Error extracting subscription metadata for variation {variation['id']}: {str(sub_error)}")
            
            logger.info(f"Processed {len(variations)} variations for product {product['id']} "
                       f"({variations_with_subscriptions} with subscription metadata)")
        except Exception as e:
            logger.error(f"Error processing variations for product {product['id']}: {str(e)}")
    
    # Handle subscription data if product is a subscription
    if product_type in ['subscription', 'variable-subscription']:
        try:
            subscription_data = wc.get_product_subscription_data(product['id'])
            
            if subscription_data:
                # For products with orders, be careful with subscription data
                if existing_product and product_in_orders:
                    # Get existing subscription
                    existing_sub = ProductSubscription.objects.filter(product=product_obj).first()
                    
                    # Extract subscription details
                    sub_data = subscription_data.get('subscription_data', {})
                    
                    if existing_sub:
                        # Update existing subscription
                        existing_sub.price = float(sub_data.get('price') or product_obj.price or 0)
                        existing_sub.period = sub_data.get('period', 'month')
                        existing_sub.interval = int(sub_data.get('interval', 1))
                        existing_sub.trial_period = sub_data.get('trial_period')
                        existing_sub.trial_length = int(sub_data.get('trial_length', 0)) if sub_data.get('trial_length') else None
                        existing_sub.sign_up_fee = float(sub_data.get('sign_up_fee', 0)) if sub_data.get('sign_up_fee') else None
                        existing_sub.woo_data = subscription_data
                        existing_sub.save()
                    else:
                        # Create new subscription
                        ProductSubscription.objects.create(
                            product=product_obj,
                            price=float(sub_data.get('price') or product_obj.price or 0),
                            period=sub_data.get('period', 'month'),
                            interval=int(sub_data.get('interval', 1)),
                            trial_period=sub_data.get('trial_period'),
                            trial_length=int(sub_data.get('trial_length', 0)) if sub_data.get('trial_length') else None,
                            sign_up_fee=float(sub_data.get('sign_up_fee', 0)) if sub_data.get('sign_up_fee') else None,
                            woo_data=subscription_data
                        )
                else:
                    # Delete existing subscription data for this product
                    ProductSubscription.objects.filter(product=product_obj).delete()
                    
                    # Extract subscription details
                    sub_data = subscription_data.get('subscription_data', {})
                    
                    # Create new subscription
                    ProductSubscription.objects.create(
                        product=product_obj,
                        price=float(sub_data.get('price') or product_obj.price or 0),
                        period=sub_data.get('period', 'month'),
                        interval=int(sub_data.get('interval', 1)),
                        trial_period=sub_data.get('trial_period'),
                        trial_length=int(sub_data.get('trial_length', 0)) if sub_data.get('trial_length') else None,
                        sign_up_fee=float(sub_data.get('sign_up_fee', 0)) if sub_data.get('sign_up_fee') else None,
                        woo_data=subscription_data
                    )
                
                logger.info(f"Processed subscription data for product {product['id']}")
        except Exception as e:
            logger.error(f"Error processing subscription data for product {product['id']}: {str(e)}")
    
    # 🎨 Handle Divi metadata for ALL product types (subscription, simple, variable, etc.)
    # This must run BEFORE the simple product check to capture Divi info for subscription-type products
    try:
        from .models import ProductSimple
        
        logger.info(f"🎨 Starting Divi metadata capture for product {product['id']} (type: {product_type})")
        
        # Get or create ProductSimple for this product (subscription products need this too for Divi)
        woo_sku = product.get('sku', '')
        product_simple, simple_created = ProductSimple.objects.get_or_create(
            product=product_obj,
            defaults={'sku': woo_sku, 'woo_data': {}}
        )
        
        if simple_created:
            logger.info(f"🎨 Created ProductSimple for Divi product {product['id']} (type: {product_type})")
        else:
            logger.info(f"🎨 Found existing ProductSimple for product {product['id']}")
        
        if product_simple:
            # Extract subscription metadata to check for Divi
            logger.info(f"🎨 Extracting subscription metadata for Divi check...")
            subscription_data = extract_subscription_metadata(product)
            logger.info(f"🎨 Divi check result: is_divi_managed={subscription_data.get('is_divi_managed', False)}")
            
            if subscription_data.get('is_divi_managed', False):
                # Get or initialize woo_data
                if not isinstance(product_simple.woo_data, dict):
                    product_simple.woo_data = {}
                
                # 🎨 Extract Divi metadata from product
                divi_metadata = {}
                is_divi_enabled = False
                meta_data = product.get('meta_data', [])
                for meta in meta_data:
                    if isinstance(meta, dict):
                        key = meta.get('key', '')
                        value = meta.get('value', '')
                        # Capture Divi-related metadata
                        if key in ['_divi_filters_post_type', '_et_builder_version', '_et_pb_use_builder', '_et_pb_built_for_post_type']:
                            divi_metadata[key] = value
                            # Check if Divi builder is enabled
                            if key == '_et_pb_use_builder' and value == 'on':
                                is_divi_enabled = True
                
                # 🧹 Cleanup: Remove Divi metadata if builder is disabled
                if divi_metadata and not is_divi_enabled:
                    # Divi builder is turned off - clean up metadata
                    if 'divi_metadata' in product_simple.woo_data:
                        del product_simple.woo_data['divi_metadata']
                        logger.info(f"🧹 Cleaned up Divi metadata for product {product['id']} - builder disabled")
                elif divi_metadata and is_divi_enabled:
                    # Store Divi metadata only if builder is enabled
                    product_simple.woo_data['divi_metadata'] = divi_metadata
                    logger.info(f"🎨 Captured Divi metadata for product {product['id']}: {divi_metadata}")
                
                # Store subscription metadata
                product_simple.woo_data['subscription_metadata'] = subscription_data
                logger.info(f"🎨 Stored Divi subscription metadata for product {product['id']}")
                
                # Save woo_data
                product_simple.save(update_fields=['woo_data'])
                logger.info(f"🎨 Saved Divi product metadata to ProductSimple for {product['id']}")
    except Exception as divi_error:
        logger.error(f"Error processing Divi metadata for product {product['id']}: {str(divi_error)}")
    
    # Handle SKU and subscription metadata for simple products
    if product_type == 'simple':
        try:
            woo_sku = product.get('sku', '')
            product_simple, simple_created = ProductSimple.objects.get_or_create(
                product=product_obj,
                defaults={'sku': woo_sku}
            )
            
            if not simple_created and product_simple.sku != woo_sku:
                old_sku = product_simple.sku
                product_simple.sku = woo_sku
                product_simple.save()
                logger.info(f"Updated simple product SKU: '{old_sku}' -> '{woo_sku}' for product {product['id']}")
            elif simple_created:
                logger.info(f"Created ProductSimple with SKU: '{woo_sku}' for product {product['id']}")
            
            # Extract and save subscription metadata (WCSATT support)
            try:
                subscription_data = extract_subscription_metadata(product)
                
                # Get or initialize woo_data
                if not isinstance(product_simple.woo_data, dict):
                    product_simple.woo_data = {}
                
                # 🎨 Extract Divi metadata from product
                divi_metadata = {}
                is_divi_enabled = False
                meta_data = product.get('meta_data', [])
                for meta in meta_data:
                    if isinstance(meta, dict):
                        key = meta.get('key', '')
                        value = meta.get('value', '')
                        # Capture Divi-related metadata
                        if key in ['_divi_filters_post_type', '_et_builder_version', '_et_pb_use_builder', '_et_pb_built_for_post_type']:
                            divi_metadata[key] = value
                            # Check if Divi builder is enabled
                            if key == '_et_pb_use_builder' and value == 'on':
                                is_divi_enabled = True
                
                # 🧹 Cleanup: Remove Divi metadata if builder is disabled
                if divi_metadata and not is_divi_enabled:
                    # Divi builder is turned off - clean up metadata
                    if 'divi_metadata' in product_simple.woo_data:
                        del product_simple.woo_data['divi_metadata']
                        logger.info(f"🧹 Cleaned up Divi metadata for product {product['id']} - builder disabled")
                elif divi_metadata and is_divi_enabled:
                    # Store Divi metadata only if builder is enabled
                    product_simple.woo_data['divi_metadata'] = divi_metadata
                    logger.info(f"🎨 Captured Divi metadata for product {product['id']}: {divi_metadata}")
                
                # Update subscription metadata in woo_data
                # Store if has_subscription OR is Divi-managed (Divi products may have has_subscription=False)
                if subscription_data['has_subscription'] or subscription_data.get('is_divi_managed', False):
                    product_simple.woo_data['subscription_metadata'] = subscription_data
                    if subscription_data['has_subscription']:
                        logger.info(f"Extracted subscription metadata for simple product {product['id']}: plugin={subscription_data.get('plugin_type')}, schemes={len(subscription_data.get('subscription_schemes', {}))}")
                    if subscription_data.get('is_divi_managed', False):
                        logger.info(f"🎨 Stored Divi subscription metadata for product {product['id']}")
                
                # Save woo_data if Divi metadata, subscription metadata, or Divi-managed flag was added
                if divi_metadata or subscription_data['has_subscription'] or subscription_data.get('is_divi_managed', False):
                    product_simple.save(update_fields=['woo_data'])
                    
                    # 🔧 CREATE ProductSubscription record (like sync_woo_subscriptions does)
                    try:
                        from .models import ProductSubscription
                        from decimal import Decimal
                        
                        # Get subscription pricing
                        sub_price = subscription_data.get('subscription_price')
                        if sub_price:
                            sub_price = Decimal(str(sub_price))
                        
                        # Get subscription period/interval from first scheme
                        sub_period = 'week'
                        sub_interval = 2
                        subscription_schemes = subscription_data.get('subscription_schemes', {})
                        if subscription_schemes:
                            # Get first scheme (e.g., '2_week')
                            first_scheme_key = list(subscription_schemes.keys())[0]
                            if '_' in first_scheme_key:
                                interval_str, period = first_scheme_key.split('_', 1)
                                sub_interval = int(interval_str) if interval_str.isdigit() else 2
                                sub_period = period
                        
                        # Check if ProductSubscription already exists
                        existing_sub = ProductSubscription.objects.filter(product=product_obj).first()
                        
                        if existing_sub:
                            # Update existing
                            existing_sub.price = sub_price
                            existing_sub.period = sub_period
                            existing_sub.interval = sub_interval
                            existing_sub.woo_data = product
                            existing_sub.save()
                            logger.info(f"📋 Updated ProductSubscription: {sub_interval} {sub_period}(s) at ${sub_price}")
                        else:
                            # Create new
                            ProductSubscription.objects.create(
                                product=product_obj,
                                price=sub_price,
                                period=sub_period,
                                interval=sub_interval,
                                woo_data=product
                            )
                            logger.info(f"📋 Created ProductSubscription: {sub_interval} {sub_period}(s) at ${sub_price}")
                    
                    except Exception as sub_create_error:
                        logger.error(f"Error creating ProductSubscription for product {product['id']}: {str(sub_create_error)}")
                else:
                    # 🧹 CLEANUP: Check if product is set to "one-time sell only"
                    meta_data = product.get('meta_data', [])
                    is_wcsatt_disabled = any(
                        meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
                        for meta in meta_data
                    )
                    
                    if is_wcsatt_disabled:
                        # 🎨 Check if this is a Divi product - Divi products have _wcsatt_disabled but still need subscription_metadata
                        is_divi_product = subscription_data.get('is_divi_managed', False)
                        
                        if is_divi_product:
                            logger.info(f"🎨 Product {product['id']} is Divi-managed - preserving subscription metadata despite _wcsatt_disabled")
                        else:
                            logger.info(f"🗑️ Product {product['id']} is set to 'Sell one-time only' - performing cleanup")
                            
                            # Remove existing ProductSubscription records
                            from .models import ProductSubscription
                            subscription_count = ProductSubscription.objects.filter(product=product_obj).count()
                            if subscription_count > 0:
                                ProductSubscription.objects.filter(product=product_obj).delete()
                                logger.info(f"🗑️ Removed {subscription_count} ProductSubscription records")
                            
                            # Clean up subscription metadata from woo_data
                            if product_simple.woo_data and isinstance(product_simple.woo_data, dict):
                                woo_data_cleaned = False
                                
                                # Remove subscription_metadata if exists
                                if 'subscription_metadata' in product_simple.woo_data:
                                    del product_simple.woo_data['subscription_metadata']
                                    woo_data_cleaned = True
                                    logger.info(f"🗑️ Removed subscription_metadata from woo_data")
                                
                                # Clean subscription-related meta_data
                                meta_data_list = product_simple.woo_data.get('meta_data', [])
                                original_count = len(meta_data_list)
                                meta_data_list = [meta for meta in meta_data_list if meta.get('key') not in [
                                    '_wcsatt_schemes',
                                    '_subscription_price',
                                    '_subscription_period',
                                    '_subscription_period_interval',
                                    '_subscription_length',
                                    '_subscription_trial_period',
                                    '_subscription_trial_length',
                                    '_subscription_sign_up_fee'
                                ]]
                                
                                if len(meta_data_list) < original_count:
                                    product_simple.woo_data['meta_data'] = meta_data_list
                                    woo_data_cleaned = True
                                
                                if woo_data_cleaned:
                                    product_simple.save(update_fields=['woo_data'])
                                    logger.info(f"🗑️ Cleaned subscription data from woo_data")
                    else:
                        logger.info(f"ℹ️ No subscription metadata found for simple product {product['id']}")
                    
            except Exception as sub_error:
                logger.error(f"Error extracting subscription metadata for simple product {product['id']}: {str(sub_error)}")
                
        except Exception as e:
            logger.error(f"Error processing simple product for product {product['id']}: {str(e)}")

    # Handle bundle data if product is a bundle
    if product_type == 'bundle':
        try:
            # Get or create ProductBundle record
            from .models import ProductBundle
            product_bundle, bundle_created = ProductBundle.objects.get_or_create(
                product_id=product_obj.id,
                defaults={
                    'woo_data': product,
                    'created_at': timezone.now(),
                    'updated_at': timezone.now()
                }
            )
            
            if not bundle_created:
                # Update existing ProductBundle with fresh data
                product_bundle.woo_data = product
                product_bundle.updated_at = timezone.now()
                product_bundle.save()
                logger.info(f"Updated existing ProductBundle record for product {product['id']}")
            else:
                logger.info(f"Created new ProductBundle record for product {product['id']}")
            
            # Log bundle data details
            bundled_items = product.get('bundled_items', [])
            logger.info(f"Processed bundle data for product {product['id']} with {len(bundled_items)} bundled items")
            
        except Exception as e:
            logger.error(f"Error processing bundle data for product {product['id']}: {str(e)}")
    
    return product_obj

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def sync_products(request, limit=0):
    """
    Sync products from WooCommerce with enhanced product type support.
    
    Args:
        request: The HTTP request
        limit: Maximum number of products to sync (0 for all)
        
    Returns:
        Response with sync status and results
    """
    try:
        # Get parameters from request or use defaults
        page = int(request.query_params.get('page', 1))
        per_page = int(request.query_params.get('per_page', 100))
        
        # Initialize response data
        response_data = {
            'status': 'success',
            'message': f'Starting WooCommerce product sync (limit={limit}, page={page}, per_page={per_page})',
            'products_processed': 0,
            'products': []
        }
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get first page to determine total
        first_page = wc.get_products(page=page, per_page=per_page)
        if not first_page or 'total' not in first_page:
            return Response({
                'status': 'error',
                'message': 'Failed to get product count from WooCommerce'
            }, status=400)
            
        total_products = first_page['total']
        total_pages = first_page.get('total_pages', 1)
        
        response_data['total_products'] = total_products
        response_data['total_pages'] = total_pages
        
        # Set limit if specified
        if limit > 0 and limit < total_products:
            response_data['message'] += f'. Limiting to {limit} products'
            total_products = limit
        
        processed_count = 0
        
        # Process first page results
        if first_page.get('data'):
            for product in first_page['data']:
                try:
                    product_obj = process_product(product, wc)
                    processed_count += 1
                    
                    # Add product info to response
                    product_info = {
                        'id': product['id'],
                        'name': product['name'],
                        'type': product_obj.product_type
                    }
                    response_data['products'].append(product_info)
                    
                    # Check if we've reached the limit
                    if limit > 0 and processed_count >= limit:
                        response_data['message'] += f'. Reached limit of {limit} products'
                        response_data['products_processed'] = processed_count
                        return Response(response_data)
                        
                except Exception as e:
                    logger.error(f'Error processing product {product["id"]}: {str(e)}')
                    # Add error info to response
                    product_info = {
                        'id': product['id'],
                        'name': product['name'],
                        'error': str(e)
                    }
                    response_data['products'].append(product_info)
        
        # Process remaining pages
        current_page = page + 1
        
        while current_page <= total_pages and (limit == 0 or processed_count < limit):
            try:
                logger.info(f"Processing page {current_page}/{total_pages}")
                result = wc.get_products(page=current_page, per_page=per_page)
                
                if result and result.get('data'):
                    for product in result['data']:
                        try:
                            product_obj = process_product(product, wc)
                            processed_count += 1
                            
                            # Add product info to response
                            product_info = {
                                'id': product['id'],
                                'name': product['name'],
                                'type': product_obj.product_type
                            }
                            response_data['products'].append(product_info)
                            
                            # Check if we've reached the limit
                            if limit > 0 and processed_count >= limit:
                                response_data['message'] += f'. Reached limit of {limit} products'
                                response_data['products_processed'] = processed_count
                                return Response(response_data)
                                
                        except Exception as e:
                            logger.error(f'Error processing product {product["id"]}: {str(e)}')
                            # Add error info to response
                            product_info = {
                                'id': product['id'],
                                'name': product['name'],
                                'error': str(e)
                            }
                            response_data['products'].append(product_info)
                            continue
                            
                current_page += 1
                
            except Exception as e:
                logger.error(f'Error processing page {current_page}: {str(e)}')
                current_page += 1
        
        response_data['products_processed'] = processed_count
        response_data['message'] += f'. Completed product sync. Processed {processed_count} products'
        return Response(response_data)
        
    except Exception as e:
        logger.error(f'Error in sync_products: {str(e)}')
        return Response({
            'status': 'error',
            'message': f'Error syncing products: {str(e)}'
        }, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_customers(request):
    """
    Get all customers from the database without pagination.
    This endpoint returns all customers in a single response.
    """
    try:
        # Get all contacts from the database
        contacts = Contact.objects.all().order_by('last_name', 'first_name')
        
        # Serialize the data
        serializer = ContactSerializer(contacts, many=True)
        
        # Log the number of customers being returned
        logger.info(f"Returning all {len(serializer.data)} customers")
        
        # Return the serialized data
        return Response({
            'status': 'success',
            'count': len(serializer.data),
            'customers': serializer.data
        })
    except Exception as e:
        logger.error(f"Error fetching all customers: {str(e)}")
        return Response({
            'status': 'error',
            'message': f"Failed to fetch customers: {str(e)}"
        }, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_all_products(request):
    """
    Get all products from WooCommerce API v3 with all their properties.
    
    This endpoint fetches all products from WooCommerce with complete details including:
    - Basic product information (name, description, price, etc.)
    - Categories and tags
    - Images
    - Attributes
    - Variations (for variable products)
    - Meta data
    - Shipping details
    - Dimensions and weight
    - woo_data field from appropriate database table based on product type
    
    Query Parameters:
        page (int): Page number (default: 1)
        per_page (int): Number of products per page (default: 20)
        search (str): Search term to filter products
        category (int): Category ID to filter products
        tag (int): Tag ID to filter products
        status (str): Product status (publish, draft, pending, etc.)
        featured (bool): Filter by featured products
        
    Returns:
        Response with products data and pagination information
    """
    try:
        # Get query parameters
        page = int(request.query_params.get('page', 1))
        per_page = int(request.query_params.get('per_page', 20))
        search = request.query_params.get('search', '')
        category = request.query_params.get('category', '')
        tag = request.query_params.get('tag', '')
        status = request.query_params.get('status', 'publish')
        featured = request.query_params.get('featured', '')
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Prepare query parameters
        params = {
            'page': page,
            'per_page': per_page,
            'status': status
        }
        
        # Add optional filters if provided
        if search:
            params['search'] = search
        if category:
            params['category'] = category
        if tag:
            params['tag'] = tag
        if featured:
            params['featured'] = featured.lower() == 'true'
            
        # Get products from WooCommerce
        result = wc.get_products(**params)
        
        if not result:
            return Response({
                'status': 'error',
                'message': 'Failed to fetch products from WooCommerce'
            }, status=400)
            
        # Extract products data and pagination info
        products = result.get('data', [])
        total_products = result.get('total', 0)
        total_pages = result.get('total_pages', 1)
        
        # Create a mapping of product names to SKUs from the database
        product_sku_map = {}
        
        try:
            # Use direct SQL queries to get SKUs from all relevant tables
            from django.db import connection
            with connection.cursor() as cursor:
                # Get SKUs from ProductSimple table - use exact name matching
                cursor.execute("""
                    SELECT p.name, ps.sku 
                    FROM crm_productsimple ps
                    JOIN crm_product p ON ps.product_id = p.id
                    WHERE ps.sku IS NOT NULL AND ps.sku != ''
                """)
                for row in cursor.fetchall():
                    product_name, sku = row
                    if product_name and sku:
                        product_sku_map[product_name] = sku
                        logger.info(f"Found SKU in ProductSimple for {product_name}: {sku}")
                
                # Get SKUs from ProductVariation table
                cursor.execute("""
                    SELECT p.name, pv.sku 
                    FROM crm_productvariation pv
                    JOIN crm_product p ON pv.product_id = p.id
                    WHERE pv.sku IS NOT NULL AND pv.sku != ''
                """)
                for row in cursor.fetchall():
                    product_name, sku = row
                    if product_name and sku:
                        product_sku_map[product_name] = sku
                        logger.info(f"Found SKU in ProductVariation for {product_name}: {sku}")
                
                # Get SKUs from ProductSubscription table's woo_data
                cursor.execute("""
                    SELECT p.name, ps.woo_data 
                    FROM crm_productsubscription ps
                    JOIN crm_product p ON ps.product_id = p.id
                    WHERE ps.woo_data IS NOT NULL
                """)
                for row in cursor.fetchall():
                    product_name, woo_data = row
                    if product_name and woo_data:
                        try:
                            if isinstance(woo_data, str):
                                import json
                                woo_data = json.loads(woo_data)
                            
                            # Check for SKU in woo_data
                            if isinstance(woo_data, dict):
                                if 'sku' in woo_data:
                                    product_sku_map[product_name] = woo_data['sku']
                                    logger.info(f"Found SKU in ProductSubscription woo_data for {product_name}: {woo_data['sku']}")
                                elif 'product_data' in woo_data and isinstance(woo_data['product_data'], dict) and 'sku' in woo_data['product_data']:
                                    product_sku_map[product_name] = woo_data['product_data']['sku']
                                    logger.info(f"Found SKU in ProductSubscription product_data for {product_name}: {woo_data['product_data']['sku']}")
                        except Exception as e:
                            logger.error(f"Error parsing woo_data for ProductSubscription {product_name}: {str(e)}")
                
                # Get SKUs from ProductBundle table's woo_data
                cursor.execute("""
                    SELECT p.name, pb.woo_data 
                    FROM crm_productbundle pb
                    JOIN crm_product p ON pb.product_id = p.id
                    WHERE pb.woo_data IS NOT NULL
                """)
                for row in cursor.fetchall():
                    product_name, woo_data = row
                    if product_name and woo_data:
                        try:
                            if isinstance(woo_data, str):
                                import json
                                woo_data = json.loads(woo_data)
                            
                            # Check for SKU in woo_data
                            if isinstance(woo_data, dict):
                                if 'sku' in woo_data:
                                    product_sku_map[product_name] = woo_data['sku']
                                    logger.info(f"Found SKU in ProductBundle woo_data for {product_name}: {woo_data['sku']}")
                                elif 'product_data' in woo_data and isinstance(woo_data['product_data'], dict) and 'sku' in woo_data['product_data']:
                                    product_sku_map[product_name] = woo_data['product_data']['sku']
                                    logger.info(f"Found SKU in ProductBundle product_data for {product_name}: {woo_data['product_data']['sku']}")
                        except Exception as e:
                            logger.error(f"Error parsing woo_data for ProductBundle {product_name}: {str(e)}")
                
                # Get SKUs from ProductGrouped table's woo_data
                cursor.execute("""
                    SELECT p.name, pg.woo_data 
                    FROM crm_productgrouped pg
                    JOIN crm_product p ON pg.product_id = p.id
                    WHERE pg.woo_data IS NOT NULL
                """)
                for row in cursor.fetchall():
                    product_name, woo_data = row
                    if product_name and woo_data:
                        try:
                            if isinstance(woo_data, str):
                                import json
                                woo_data = json.loads(woo_data)
                            
                            # Check for SKU in woo_data
                            if isinstance(woo_data, dict):
                                if 'sku' in woo_data:
                                    product_sku_map[product_name] = woo_data['sku']
                                    logger.info(f"Found SKU in ProductGrouped woo_data for {product_name}: {woo_data['sku']}")
                                elif 'product_data' in woo_data and isinstance(woo_data['product_data'], dict) and 'sku' in woo_data['product_data']:
                                    product_sku_map[product_name] = woo_data['product_data']['sku']
                                    logger.info(f"Found SKU in ProductGrouped product_data for {product_name}: {woo_data['product_data']['sku']}")
                        except Exception as e:
                            logger.error(f"Error parsing woo_data for ProductGrouped {product_name}: {str(e)}")
                            
                # Get SKUs directly from WooCommerce data
                cursor.execute("""
                    SELECT p.name, p.woo_data 
                    FROM crm_product p
                    WHERE p.woo_data IS NOT NULL
                """)
                for row in cursor.fetchall():
                    product_name, woo_data = row
                    if product_name and woo_data:
                        try:
                            if isinstance(woo_data, str):
                                import json
                                woo_data = json.loads(woo_data)
                            
                            # Check for SKU in woo_data
                            if isinstance(woo_data, dict):
                                if 'sku' in woo_data and woo_data['sku']:
                                    product_sku_map[product_name] = woo_data['sku']
                                    logger.info(f"Found SKU in Product woo_data for {product_name}: {woo_data['sku']}")
                        except Exception as e:
                            logger.error(f"Error parsing woo_data for Product {product_name}: {str(e)}")
        except Exception as e:
            logger.error(f"Error querying database for SKUs: {str(e)}")
        
        # Add SKUs to products using direct database queries for each product
        for product in products:
            product_name = product.get('name', '')
            product_id = product.get('id', 0)
            
            # First check if the product already has a SKU from WooCommerce
            if 'sku' in product and product['sku']:
                logger.info(f"Product {product_name} already has SKU from WooCommerce: {product['sku']}")
                continue
            
            # Try to get the SKU directly from the database for this specific product
            try:
                from django.db import connection
                with connection.cursor() as cursor:
                    # Try ProductSimple table first
                    cursor.execute("""
                        SELECT ps.sku 
                        FROM crm_productsimple ps
                        JOIN crm_product p ON ps.product_id = p.id
                        WHERE p.name = %s AND ps.sku IS NOT NULL AND ps.sku != ''
                    """, [product_name])
                    result = cursor.fetchone()
                    
                    if result and result[0]:
                        product['sku'] = result[0]
                        logger.info(f"Found SKU in ProductSimple for {product_name}: {result[0]}")
                        continue
                    
                    # Try ProductVariation table next
                    cursor.execute("""
                        SELECT pv.sku 
                        FROM crm_productvariation pv
                        JOIN crm_product p ON pv.product_id = p.id
                        WHERE p.name = %s AND pv.sku IS NOT NULL AND pv.sku != ''
                    """, [product_name])
                    result = cursor.fetchone()
                    
                    if result and result[0]:
                        product['sku'] = result[0]
                        logger.info(f"Found SKU in ProductVariation for {product_name}: {result[0]}")
                        continue
                    
                    # Check if we have a SKU for this product in our mapping as fallback
                    if product_name in product_sku_map:
                        product['sku'] = product_sku_map[product_name]
                        logger.info(f"Applied SKU from database mapping for {product_name}: {product_sku_map[product_name]}")
                        continue
                    
                    # If still no SKU found, use product ID as fallback
                    product['sku'] = f"ID-{product_id}"
                    logger.info(f"No SKU found in database for {product_name}, using product ID: {product_id}")
            except Exception as e:
                logger.error(f"Error getting SKU for {product_name}: {str(e)}")
                product['sku'] = f"ID-{product_id}"
                logger.info(f"Error occurred, using product ID as fallback: {product_id}")
        
        # Enhance products with woo_data for frontend use
        enhanced_products = []
        for product in products:
            product_id = product.get('id')
            product_type = product.get('type', 'simple')
            product_name = product.get('name', '')
            
            # Try to get woo_data to add to the product
            try:
                # Find the appropriate product model based on product type
                db_product = None
                from django.db import connection
                with connection.cursor() as cursor:
                    # Query to get the woo_data field
                    if product_type == 'simple':
                        cursor.execute("""
                            SELECT ps.woo_data
                            FROM crm_productsimple ps
                            JOIN crm_product p ON ps.product_id = p.id
                            WHERE p.name = %s
                        """, [product_name])
                    elif product_type == 'variable':
                        cursor.execute("""
                            SELECT pv.woo_data
                            FROM crm_productvariation pv
                            JOIN crm_product p ON pv.product_id = p.id
                            WHERE p.name = %s
                        """, [product_name])
                    elif product_type == 'subscription':
                        cursor.execute("""
                            SELECT ps.woo_data
                            FROM crm_productsubscription ps
                            JOIN crm_product p ON ps.product_id = p.id
                            WHERE p.name = %s
                        """, [product_name])
                    elif product_type == 'bundle':
                        cursor.execute("""
                            SELECT pb.woo_data
                            FROM crm_productbundle pb
                            JOIN crm_product p ON pb.product_id = p.id
                            WHERE p.name = %s
                        """, [product_name])
                    elif product_type == 'grouped':
                        cursor.execute("""
                            SELECT pg.woo_data
                            FROM crm_productgrouped pg
                            JOIN crm_product p ON pg.product_id = p.id
                            WHERE p.name = %s
                        """, [product_name])
                    
                    result = cursor.fetchone()
                    
                    if result and result[0]:
                        woo_data = result[0]
                        logger.info(f"Retrieved woo_data for {product_name}")
                        
                        # Handle both string and dict formats
                        if isinstance(woo_data, str):
                            try:
                                import json
                                woo_data = json.loads(woo_data)
                            except Exception as e:
                                logger.error(f"Error parsing woo_data string: {e}")
                        
                        # Add the woo_data to the product for frontend use
                        product['woo_data'] = woo_data
            except Exception as e:
                logger.error(f"Error retrieving woo_data for {product_name}: {str(e)}")
            
            # Log the final SKU value for debugging
            logger.info(f"Final SKU for {product_name}: {product.get('sku', 'N/A')}")
            
            # Only set a default SKU if one doesn't exist at all
            if not product.get('sku'):
                product['sku'] = str(product_id)
                logger.info(f"No SKU found for {product_name}, using product_id as default: {product['sku']}")
            
            # Add the product to the enhanced products list (only once)
            enhanced_products.append(product)
        
        # Log the number of products being returned
        logger.info(f"Returning {len(enhanced_products)} products with SKUs")
        
        # Return the response with pagination info
        return Response({
            'products': enhanced_products,
            'total': total_products,
            'total_pages': total_pages,
            'current_page': page,
        })
        
    except Exception as e:
        logger.error(f"Error in get_all_products: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'Error fetching products: {str(e)}'
        }, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_detail(request, product_id):
    """
    Get a specific product from WooCommerce API v3 with all its properties.
    
    This endpoint fetches a single product from WooCommerce with complete details including:
    - Basic product information (name, description, price, etc.)
    - Categories and tags
    - Images
    - Attributes
    - Variations (for variable products)
    - Meta data
    - Shipping details
    - Dimensions and weight
    - Subscription data (if applicable)
    - Bundle data (if applicable)
    - Grouped product data (if applicable)
    
    Args:
        request: The HTTP request
        product_id: The WooCommerce product ID
        
    Returns:
        Response with complete product data
    """
    try:
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get product from WooCommerce
        product = wc.get_product(product_id) 
        
        if not product or not product.get('data'):
            return Response({
                'status': 'error',
                'message': f'Product with ID {product_id} not found'
            }, status=404)
            
        # Extract product data
        product_data = product.get('data', {})
        
        # Fetch additional data based on product type
        product_type = product_data.get('type', 'simple')
        
        # For variable products, fetch variations
        if product_type == 'variable':
            try:
                variations = wc.get_product_variations(product_id)
                if variations and 'data' in variations:
                    product_data['variations_data'] = variations['data']
            except Exception as e:
                logger.error(f"Error fetching variations for product {product_id}: {str(e)}")
                product_data['variations_data'] = []
                product_data['variations_error'] = str(e)
        
        # Fetch subscription data if it's a subscription product
        if product_type in ['subscription', 'variable-subscription']:
            try:
                subscription_data = wc.get_product_subscription_data(product_id)
                if subscription_data:
                    product_data['subscription_data'] = subscription_data
            except Exception as e:
                logger.error(f"Error fetching subscription data for product {product_id}: {str(e)}")
                product_data['subscription_data'] = {}
                product_data['subscription_error'] = str(e)
                
        # Fetch bundle data if it's a bundle product
        if product_type == 'bundle':
            try:
                bundle_data = wc.get_product_bundle_data(product_id)
                if bundle_data:
                    product_data['bundle_data'] = bundle_data
            except Exception as e:
                logger.error(f"Error fetching bundle data for product {product_id}: {str(e)}")
                product_data['bundle_data'] = {}
                product_data['bundle_error'] = str(e)
                
        # Fetch grouped product data if it's a grouped product
        if product_type == 'grouped':
            try:
                grouped_data = wc.get_product_grouped_data(product_id)
                if grouped_data:
                    product_data['grouped_data'] = grouped_data
            except Exception as e:
                logger.error(f"Error fetching grouped data for product {product_id}: {str(e)}")
                product_data['grouped_data'] = {}
                product_data['grouped_error'] = str(e)
        
        # Check if product exists in our database and include our data if it does
        try:
            from .models import Product
            local_product = Product.objects.filter(woo_product_id=product_id).first()
            if local_product:
                product_data['local_data'] = {
                    'id': str(local_product.id),
                    'stock_quantity': local_product.stock_quantity,
                    'created_at': local_product.created_at.isoformat() if local_product.created_at else None,
                    'updated_at': local_product.updated_at.isoformat() if local_product.updated_at else None
                }
        except Exception as e:
            logger.error(f"Error fetching local product data for product {product_id}: {str(e)}")
            product_data['local_data_error'] = str(e)
        
        # Prepare response
        response_data = {
            'status': 'success',
            'product': product_data
        }
        
        return Response(response_data)
        
    except Exception as e:
        logger.error(f"Error in get_product_detail: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'Error fetching product: {str(e)}'
        }, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_children(request, product_id):
    """
    Get the children of a specific product based on its type (variable, bundle, subscription, or package).
    
    Args:
        request: The HTTP request
        product_id: UUID of the product or credited-service-{uuid} format
        
    Returns:
        Response with the product children data
    """
    try:
        # Handle credited service IDs by extracting the actual UUID
        actual_product_id = product_id
        if product_id.startswith('credited-service-'):
            actual_product_id = product_id.replace('credited-service-', '')
            # For credited services, return a simple response since they don't have children
            return Response({
                'product': {'id': product_id, 'name': 'Credited Service'},
                'children': {},
                'type': 'simple'
            })
        
        # Get the product
        product = Product.objects.get(id=actual_product_id)
        
        # Get children based on product type
        if product.product_type in ['variable', 'variable-subscription', 'variable_subscription']:
            children = ProductVariation.objects.filter(product_id=actual_product_id)
            serializer = ProductVariationSerializer(children, many=True)
            return Response({
                'product': ProductWithSKUSerializer(product).data,
                'children': serializer.data,
                'type': 'variable'
            })
            
        elif product.product_type == 'bundle':
            bundle = ProductBundle.objects.filter(product_id=actual_product_id).first()
            if bundle:
                serializer = ProductBundleSerializer(bundle)
                return Response({
                    'product': ProductWithSKUSerializer(product).data,
                    'children': serializer.data,
                    'type': 'bundle'
                })
                
        elif product.product_type == 'subscription' or product.product_type == 'variable_subscription':
            subscription = ProductSubscription.objects.filter(product_id=actual_product_id).first()
            if subscription:
                serializer = ProductSubscriptionSerializer(subscription)
                return Response({
                    'product': ProductWithSKUSerializer(product).data,
                    'children': serializer.data,
                    'type': 'subscription'
                })
                
        elif product.product_type == 'grouped':
            grouped = ProductGrouped.objects.filter(product_id=actual_product_id).first()
            if grouped:
                serializer = ProductGroupedSerializer(grouped)
                return Response({
                    'product': ProductWithSKUSerializer(product).data,
                    'children': serializer.data,
                    'type': 'grouped'
                })
                
        elif product.product_type == 'simple':
            simple = ProductSimple.objects.filter(product_id=actual_product_id).first()
            if simple:
                serializer = ProductSimpleSerializer(simple)
                
                # Extract subscription options from woo_data if available
                subscription_options = []
                subscription_resolution_source = 'none'
                if simple.woo_data:
                    def _safe_float(value, default=0.0):
                        try:
                            if isinstance(value, str):
                                cleaned = re.sub(r'[^0-9.\-]', '', value)
                                return float(cleaned) if cleaned not in ['', '-', '.'] else default
                            return float(value)
                        except (ValueError, TypeError):
                            return default

                    def _ensure_list(value):
                        if isinstance(value, list):
                            return value
                        if isinstance(value, str):
                            try:
                                parsed = json.loads(value)
                                return parsed if isinstance(parsed, list) else []
                            except Exception:
                                return []
                        return []

                    def _ensure_dict(value):
                        if isinstance(value, dict):
                            return value
                        if isinstance(value, str):
                            try:
                                parsed = json.loads(value)
                                return parsed if isinstance(parsed, dict) else {}
                            except Exception:
                                return {}
                        return {}

                    def _append_options_from_wcsatt_schemes(schemes, base_price):
                        count_before = len(subscription_options)
                        for scheme in schemes:
                            if not isinstance(scheme, dict):
                                continue
                            interval = scheme.get('subscription_period_interval', '')
                            period = scheme.get('subscription_period', '')
                            discount = scheme.get('subscription_discount', 0)
                            discount_value = _safe_float(discount, 0.0)

                            scheme_subscription_price = scheme.get('subscription_price', '')
                            if scheme_subscription_price not in ['', None]:
                                discounted_price = _safe_float(scheme_subscription_price, base_price)
                            else:
                                discounted_price = base_price * (1 - (discount_value / 100)) if base_price else 0

                            interval_int = int(_safe_float(interval, 1))
                            subscription_options.append({
                                'id': f"sub_{interval}_{period}",
                                'interval': str(interval),
                                'period': period,
                                'discount': int(discount_value) if discount_value else 0,
                                'price': round(discounted_price, 2),
                                'original_price': base_price,
                                'label': f"Subscribe and save - every {interval} {period}{'s' if interval_int > 1 else ''}"
                            })
                        return len(subscription_options) > count_before

                    def _append_options_from_satt_data(satt_data, base_price):
                        satt_data = _ensure_dict(satt_data)
                        subscription_schemes = satt_data.get('subscription_schemes', {})
                        if not isinstance(subscription_schemes, dict) or not subscription_schemes:
                            return False

                        count_before = len(subscription_options)
                        for scheme_key, scheme_data in subscription_schemes.items():
                            if not scheme_key or '_' not in scheme_key:
                                continue
                            try:
                                interval_str, period = scheme_key.split('_', 1)
                                interval = int(interval_str)
                            except (ValueError, TypeError):
                                continue

                            discount = 0.0
                            if isinstance(scheme_data, dict):
                                discount = _safe_float(scheme_data.get('subscription_discount', 0), 0.0)

                            discounted_price = base_price * (1 - (discount / 100)) if base_price else 0
                            subscription_options.append({
                                'id': f"sub_{interval}_{period}",
                                'interval': str(interval),
                                'period': period,
                                'discount': int(discount) if discount else 0,
                                'price': round(discounted_price, 2),
                                'original_price': base_price,
                                'label': f"Subscribe and save - every {interval} {period}{'s' if interval > 1 else ''}"
                            })
                        return len(subscription_options) > count_before

                    def _extract_schemes_from_text(raw_text):
                        """
                        Best-effort extractor for PHP-serialized/JSON-like option blobs.
                        Returns scheme objects compatible with _append_options_from_wcsatt_schemes.
                        """
                        if not raw_text:
                            return []

                        text = str(raw_text)
                        # Capture interval/period tokens like 1_week, 2_weeks, 3_month, 1_year.
                        matches = re.findall(r'(\d+)_((?:day|week|month|year)s?)', text, flags=re.IGNORECASE)
                        if not matches:
                            return []

                        # Try to capture one discount value if present in serialized/JSON content.
                        discount = 0.0
                        discount_patterns = [
                            r'subscription_discount"\s*[:=]\s*"?([0-9]+(?:\.[0-9]+)?)',
                            r'subscription_discount";[sid]:([0-9]+(?:\.[0-9]+)?)',
                        ]
                        for pattern in discount_patterns:
                            hit = re.search(pattern, text, flags=re.IGNORECASE)
                            if hit:
                                discount = _safe_float(hit.group(1), 0.0)
                                break

                        schemes = []
                        seen = set()
                        for interval_str, period_token in matches:
                            try:
                                interval_int = int(interval_str)
                            except (ValueError, TypeError):
                                continue

                            period = period_token.lower().rstrip('s')
                            key = (interval_int, period)
                            if key in seen:
                                continue
                            seen.add(key)

                            schemes.append({
                                'subscription_period_interval': interval_int,
                                'subscription_period': period,
                                'subscription_discount': discount,
                            })

                        return schemes

                    # 🎨 Check if this is a Divi product first
                    divi_metadata = simple.woo_data.get('divi_metadata', {})
                    subscription_metadata = simple.woo_data.get('subscription_metadata', {})
                    is_divi_managed = subscription_metadata.get('is_divi_managed', False) or bool(divi_metadata)
                    
                    if is_divi_managed:
                        # 🎨 Divi product - subscription handled at checkout, don't generate subscription options
                        logger.info(f"🎨 Divi product detected for {product.id} - skipping subscription options generation")
                        # Don't add any subscription_options - Divi will handle it at checkout
                        pass
                    else:
                        # 🔧 PRIORITY FIX: Check _wcsatt_schemes FIRST (most accurate, synced from WooCommerce)
                        # This contains the actual subscription prices calculated by sync_woo_subscriptions
                        schemes_found = False
                        local_price = _safe_float(simple.woo_data.get('price', 0), 0.0)
                        local_meta_data = _ensure_list(simple.woo_data.get('meta_data', []))

                        if local_meta_data:
                            for meta in local_meta_data:
                                if not isinstance(meta, dict):
                                    continue
                                if meta.get('key') == '_wcsatt_schemes' and meta.get('value'):
                                    schemes = _ensure_list(meta.get('value', []))
                                    schemes_found = _append_options_from_wcsatt_schemes(schemes, local_price)
                                    if schemes_found:
                                        subscription_resolution_source = 'wcsatt_schemes_local'
                                        logger.info(
                                            f"Subscription options resolved for product {product.id} via local _wcsatt_schemes "
                                            f"(count={len(subscription_options)})"
                                        )
                                        break
                        
                        # Fallback: If no _wcsatt_schemes found, resolve from local _satt_data
                        if not schemes_found and local_meta_data:
                            # Resolve global WCSATT plans from _satt_data when product is configured
                            # to use global subscription plans and _wcsatt_schemes is not present.
                            for meta in local_meta_data:
                                if not isinstance(meta, dict):
                                    continue
                                if meta.get('key') == '_satt_data' and meta.get('value'):
                                    schemes_found = _append_options_from_satt_data(meta.get('value'), local_price)
                                    if schemes_found:
                                        subscription_resolution_source = 'satt_data_local'
                                        logger.info(
                                            f"Subscription options resolved for product {product.id} via local _satt_data "
                                            f"(count={len(subscription_options)})"
                                        )
                                        break

                        # Final fallback: pull fresh Woo meta for newly created products not fully synced yet.
                        if not schemes_found and getattr(product, 'woo_product_id', None):
                            try:
                                wcapi = WooCommerceAPI()
                                wc_response = wcapi.wcapi.get(f"products/{product.woo_product_id}")
                                if wc_response.status_code == 200:
                                    wc_product = wc_response.json() if hasattr(wc_response, 'json') else {}
                                    fresh_meta_data = _ensure_list((wc_product or {}).get('meta_data', []))
                                    fresh_price = _safe_float((wc_product or {}).get('price', simple.woo_data.get('price', 0)), 0.0)

                                    for meta in fresh_meta_data:
                                        if not isinstance(meta, dict):
                                            continue
                                        if meta.get('key') == '_wcsatt_schemes' and meta.get('value'):
                                            schemes = _ensure_list(meta.get('value', []))
                                            schemes_found = _append_options_from_wcsatt_schemes(schemes, fresh_price)
                                            if schemes_found:
                                                subscription_resolution_source = 'wcsatt_schemes_fresh_wc'
                                                logger.info(
                                                    f"Subscription options resolved for product {product.id} via fresh WC _wcsatt_schemes "
                                                    f"(count={len(subscription_options)})"
                                                )
                                                break

                                    if not schemes_found:
                                        for meta in fresh_meta_data:
                                            if not isinstance(meta, dict):
                                                continue
                                            if meta.get('key') == '_satt_data' and meta.get('value'):
                                                schemes_found = _append_options_from_satt_data(meta.get('value'), fresh_price)
                                                if schemes_found:
                                                    subscription_resolution_source = 'satt_data_fresh_wc'
                                                    logger.info(
                                                        f"Subscription options resolved for product {product.id} via fresh WC _satt_data "
                                                        f"(count={len(subscription_options)})"
                                                    )
                                                    break

                                    # Persist fresh meta for subsequent reads when we successfully resolved schemes.
                                    if schemes_found:
                                        updated_woo_data = dict(simple.woo_data or {})
                                        updated_woo_data['meta_data'] = fresh_meta_data
                                        if (wc_product or {}).get('price') not in [None, '']:
                                            updated_woo_data['price'] = (wc_product or {}).get('price')
                                        simple.woo_data = updated_woo_data
                                        simple.save(update_fields=['woo_data'])
                                        logger.info(
                                            f"Persisted fresh Woo meta_data to ProductSimple for product {product.id} after subscription resolution"
                                        )
                            except Exception as wc_error:
                                logger.warning(
                                    f"Failed fresh Woo subscription scheme resolution for product {product.id}: {wc_error}"
                                )

                        # Fallback: query WordPress postmeta directly (captures keys not surfaced by REST API).
                        if not schemes_found and getattr(product, 'woo_product_id', None):
                            try:
                                from .views_woocommerce_points import get_wordpress_database_connection
                                with get_wordpress_database_connection() as wp_conn:
                                    with wp_conn.cursor() as cursor:
                                        cursor.execute(
                                            """
                                            SELECT meta_key, meta_value
                                            FROM wp_postmeta
                                            WHERE post_id = %s
                                            AND meta_key IN ('_wcsatt_schemes', '_satt_data')
                                            """,
                                            (product.woo_product_id,)
                                        )
                                        meta_rows = cursor.fetchall() or []

                                        postmeta_map = {
                                            row.get('meta_key'): row.get('meta_value')
                                            for row in meta_rows if isinstance(row, dict)
                                        }

                                        raw_wcsatt = postmeta_map.get('_wcsatt_schemes')
                                        if raw_wcsatt:
                                            schemes = _ensure_list(raw_wcsatt)
                                            if not schemes:
                                                schemes = _extract_schemes_from_text(raw_wcsatt)
                                            schemes_found = _append_options_from_wcsatt_schemes(schemes, local_price)
                                            if schemes_found:
                                                subscription_resolution_source = 'wcsatt_schemes_postmeta'
                                                logger.info(
                                                    f"Subscription options resolved for product {product.id} via wp_postmeta _wcsatt_schemes "
                                                    f"(count={len(subscription_options)})"
                                                )

                                        if not schemes_found:
                                            raw_satt_data = postmeta_map.get('_satt_data')
                                            if raw_satt_data:
                                                schemes_found = _append_options_from_satt_data(raw_satt_data, local_price)
                                                if not schemes_found:
                                                    schemes = _extract_schemes_from_text(raw_satt_data)
                                                    schemes_found = _append_options_from_wcsatt_schemes(schemes, local_price)
                                                if schemes_found:
                                                    subscription_resolution_source = 'satt_data_postmeta'
                                                    logger.info(
                                                        f"Subscription options resolved for product {product.id} via wp_postmeta _satt_data "
                                                        f"(count={len(subscription_options)})"
                                                    )
                            except Exception as postmeta_error:
                                logger.warning(
                                    f"Failed wp_postmeta subscription scheme resolution for product {product.id}: {postmeta_error}"
                                )

                        # Final fallback: resolve from global WCSATT settings in wp_options.
                        if not schemes_found:
                            try:
                                from .views_woocommerce_points import get_wordpress_database_connection
                                with get_wordpress_database_connection() as wp_conn:
                                    with wp_conn.cursor() as cursor:
                                        cursor.execute(
                                            """
                                            SELECT option_name, option_value
                                            FROM wp_options
                                            WHERE option_name LIKE '%wcsatt%'
                                               OR option_name LIKE '%subscription%scheme%'
                                               OR option_name LIKE '%all_products_for_subscriptions%'
                                            """
                                        )
                                        option_rows = cursor.fetchall() or []

                                        global_schemes = []
                                        for row in option_rows:
                                            if not isinstance(row, dict):
                                                continue
                                            option_value = row.get('option_value')
                                            if not option_value:
                                                continue

                                            # Try strict JSON/dict parsing first.
                                            parsed_dict = _ensure_dict(option_value)
                                            if parsed_dict:
                                                parsed_schemes = parsed_dict.get('subscription_schemes', {})
                                                if isinstance(parsed_schemes, dict) and parsed_schemes:
                                                    for scheme_key, scheme_data in parsed_schemes.items():
                                                        if not scheme_key or '_' not in scheme_key:
                                                            continue
                                                        try:
                                                            interval_str, period = scheme_key.split('_', 1)
                                                            interval_int = int(interval_str)
                                                        except (ValueError, TypeError):
                                                            continue
                                                        discount = 0.0
                                                        if isinstance(scheme_data, dict):
                                                            discount = _safe_float(scheme_data.get('subscription_discount', 0), 0.0)
                                                        global_schemes.append({
                                                            'subscription_period_interval': interval_int,
                                                            'subscription_period': str(period).rstrip('s'),
                                                            'subscription_discount': discount,
                                                        })
                                            else:
                                                # Best-effort extraction from serialized strings.
                                                global_schemes.extend(_extract_schemes_from_text(option_value))

                                        if global_schemes:
                                            # De-duplicate global schemes.
                                            deduped = {}
                                            for scheme in global_schemes:
                                                interval_val = int(_safe_float(scheme.get('subscription_period_interval', 1), 1))
                                                period_val = str(scheme.get('subscription_period', 'month')).rstrip('s')
                                                deduped[(interval_val, period_val)] = {
                                                    'subscription_period_interval': interval_val,
                                                    'subscription_period': period_val,
                                                    'subscription_discount': _safe_float(scheme.get('subscription_discount', 0), 0.0),
                                                }
                                            schemes_found = _append_options_from_wcsatt_schemes(list(deduped.values()), local_price)
                                            if schemes_found:
                                                subscription_resolution_source = 'wcsatt_global_wp_options'
                                                logger.info(
                                                    f"Subscription options resolved for product {product.id} via wp_options global schemes "
                                                    f"(count={len(subscription_options)})"
                                                )
                            except Exception as options_error:
                                logger.warning(
                                    f"Failed wp_options global subscription scheme resolution for product {product.id}: {options_error}"
                                )

                        # Fallback: If no schemes found, check subscription_metadata
                        if not schemes_found:
                            subscription_metadata = simple.woo_data.get('subscription_metadata', {})
                            if subscription_metadata and subscription_metadata.get('has_subscription', False):
                                # Check if we have WCSATT with multiple schemes
                                plugin_type = subscription_metadata.get('plugin_type', 'wcs')
                                subscription_schemes = subscription_metadata.get('subscription_schemes', {})
                                
                                if plugin_type == 'wcsatt' and subscription_schemes:
                                    # Extract ALL WCSATT subscription schemes
                                    discount = subscription_metadata.get('subscription_discount', '5%')
                                    
                                    # Get price from subscription_metadata or Product model
                                    subscription_price = subscription_metadata.get('subscription_price')
                                    if subscription_price:
                                        discounted_price = float(subscription_price)
                                    else:
                                        # Fallback: get price from Product model and calculate discount
                                        try:
                                            original_price = float(simple.product.price) if simple.product else 0
                                            if discount and original_price:
                                                discount_percent = float(discount.replace('%', '').strip())
                                                discounted_price = original_price * (1 - discount_percent / 100)
                                            else:
                                                discounted_price = original_price
                                        except (ValueError, TypeError):
                                            discounted_price = 0
                                    
                                    for scheme_key in subscription_schemes.keys():
                                        if scheme_key and '_' in scheme_key:
                                            try:
                                                interval_str, period = scheme_key.split('_', 1)
                                                interval = int(interval_str)
                                                
                                                subscription_options.append({
                                                    'id': f"sub_{interval}_{period}",
                                                    'interval': str(interval),  # 🔧 FIX: Ensure string type
                                                    'period': period,
                                                    'discount': int(discount.replace('%', '')) if isinstance(discount, str) else discount,  # 🔧 FIX: Ensure number type
                                                    'price': round(discounted_price, 2),
                                                    'original_price': float(simple.product.price) if simple.product else discounted_price,
                                                    'label': f"Subscribe and save - every {interval} {period}{'s' if interval > 1 else ''}"
                                                })
                                            except (ValueError, TypeError) as e:
                                                pass
                                else:
                                    # Single subscription (WCS or single WCSATT)
                                    period = subscription_metadata.get('subscription_period', 'month')
                                    interval = subscription_metadata.get('subscription_interval', 1)
                                    price = subscription_metadata.get('subscription_price')
                                    discount = subscription_metadata.get('subscription_discount')
                                    
                                    if price:
                                        try:
                                            subscription_options.append({
                                                'id': f"sub_{interval}_{period}",
                                                'interval': str(interval),  # 🔧 FIX: Ensure string type
                                                'period': period,
                                                'discount': int(discount.replace('%', '')) if isinstance(discount, str) else discount,  # 🔧 FIX: Ensure number type
                                                'price': float(price),
                                                'label': f"Subscribe and save - every {interval} {period}{'s' if interval > 1 else ''}"
                                            })
                                        except (ValueError, TypeError):
                                            pass

                        if not subscription_options and not is_divi_managed:
                            logger.warning(
                                f"No subscription options resolved for product {product.id} "
                                f"(source={subscription_resolution_source})"
                            )
                
                # Include Divi and subscription metadata in response
                divi_info = {}
                if simple.woo_data:
                    divi_metadata = simple.woo_data.get('divi_metadata', {})
                    subscription_metadata = simple.woo_data.get('subscription_metadata', {})
                    
                    if divi_metadata:
                        divi_info['is_divi_product'] = True
                        divi_info['divi_metadata'] = divi_metadata
                    
                    if subscription_metadata:
                        divi_info['subscription_metadata'] = subscription_metadata
                    
                return Response({
                    'product': ProductWithSKUSerializer(product).data,
                    'children': {
                        **serializer.data,
                        'subscription_options': subscription_options,
                        'subscription_resolution_source': subscription_resolution_source,
                        **divi_info  # Add Divi and subscription metadata if present
                    },
                    'type': 'simple'
                })
                
        # If no children found or product type not supported
        return Response({
            'product': ProductWithSKUSerializer(product).data,
            'children': [],
            'type': product.product_type
        })
            
    except Product.DoesNotExist:
        return Response(
            {"error": f"Product with ID {product_id} not found"}, 
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {"error": str(e)}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_pos_settings(request):
    """
    Get POS system settings from database
    
    Returns configuration settings for the POS system frontend
    """
    from .models import POSSetting
    
    # Get settings from database with fallback defaults
    return Response({
        'shipping': {
            'enabled': POSSetting.get_setting('enable_shipping', False)
        },
        'inventory': {
            'prevent_out_of_stock_cart': POSSetting.get_setting('prevent_out_of_stock_cart', True)
        },
        'categories': {
            'hidden_categories': POSSetting.get_setting('hidden_categories', []),
            'hidden_category_products': POSSetting.get_setting('hidden_category_products', [])
        },
        'display': {
            'show_product_description': POSSetting.get_setting('show_product_description', True)
        },
        'permissions_enabled': POSSetting.get_setting('permissions_enabled', False)
    })

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_pos_settings(request):
    """
    Update POS system settings in database
    
    Allows updating configuration settings for the POS system frontend
    """
    from .models import POSSetting
    
    # Get the setting to update
    setting_name = request.data.get('setting')
    setting_value = request.data.get('value')
    
    if not setting_name:
        return Response({"error": "Setting name is required"}, status=status.HTTP_400_BAD_REQUEST)
    
    # Map old environment variable names to new database keys
    setting_mapping = {
        'POS_ENABLE_SHIPPING': 'enable_shipping',
        'POS_PREVENT_OUT_OF_STOCK_CART': 'prevent_out_of_stock_cart',
        'POS_HIDDEN_CATEGORIES': 'hidden_categories',
        'POS_HIDDEN_CATEGORY_PRODUCTS': 'hidden_category_products',
        'POS_SHOW_PRODUCT_DESCRIPTION': 'show_product_description',
        'POS_MANUAL_EMAIL_RECEIPT': 'manual_email_receipt',
        'permissions_enabled': 'permissions_enabled'
    }
    
    # Only allow updating specific settings
    if setting_name not in setting_mapping:
        return Response({"error": f"Setting '{setting_name}' cannot be updated"}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Get the database key and description
        db_key = setting_mapping[setting_name]
        descriptions = {
            'enable_shipping': 'Enable shipping options during checkout',
            'prevent_out_of_stock_cart': 'Prevent adding out-of-stock products to cart',
            'hidden_categories': 'List of categories to hide from POS interface',
            'hidden_category_products': 'List of categories whose products should be hidden from POS',
            'show_product_description': 'Show product descriptions in product modal',
            'permissions_enabled': 'Enable role-based permission enforcement'
        }
        categories = {
            'enable_shipping': 'shipping',
            'prevent_out_of_stock_cart': 'inventory',
            'hidden_categories': 'categories',
            'hidden_category_products': 'categories',
            'show_product_description': 'display',
            'permissions_enabled': 'permissions'
        }
        
        # Determine setting type based on the key
        setting_types = {
            'enable_shipping': 'boolean',
            'prevent_out_of_stock_cart': 'boolean',
            'hidden_categories': 'json',
            'hidden_category_products': 'json',
            'show_product_description': 'boolean',
            'permissions_enabled': 'boolean'
        }
        
        # Update setting in database
        POSSetting.set_setting(
            key=db_key,
            value=setting_value,
            setting_type=setting_types.get(db_key, 'string'),
            description=descriptions.get(db_key, ''),
            category=categories.get(db_key, 'general'),
            user=request.user if request.user.is_authenticated else None
        )
        
        from users.activity_log import log_activity
        log_activity(request, f"Updated POS setting: {setting_name}", category='settings', details={
            'setting': setting_name,
            'db_key': db_key,
            'value': setting_value if not isinstance(setting_value, (list, dict)) else str(setting_value)[:200],
        })

        return Response({"success": f"Setting '{setting_name}' updated successfully"})
    except Exception as e:
        return Response({"error": f"Failed to update setting: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class POSLocationViewSet(viewsets.ModelViewSet):
    """ViewSet for managing POS locations"""
    queryset = POSLocation.objects.all()
    serializer_class = POSLocationSerializer
    permission_classes = [IsAuthenticated, IsSuperUserOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'location', 'location_id']
    ordering_fields = ['name', 'created_at']
    ordering = ['name']
    
    def get_queryset(self):
        """Filter active locations by default, allow admin to see all"""
        queryset = POSLocation.objects.all()
        
        # Filter by active status if requested
        is_active = self.request.query_params.get('is_active', None)
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        else:
            # By default, only show active locations for non-superuser users
            if not self.request.user.is_superuser:
                queryset = queryset.filter(is_active=True)
                
        return queryset
    
    @action(detail=False, methods=['get'])
    def active(self, request):
        """Get only active locations"""
        active_locations = POSLocation.objects.filter(is_active=True)
        serializer = self.get_serializer(active_locations, many=True)
        return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_subscription(request):
    """
    Create a new WooCommerce subscription only (no POS order).
    
    This endpoint creates a subscription by:
    1. Creating a WooCommerce subscription via the API only
    2. No POS order is created for manual subscription additions
    
    Expected payload:
    {
        "customer_id": "uuid",
        "product_id": "uuid", 
        "quantity": 1,
        "price": 35.5,
        "billing_period": "month",
        "billing_interval": 1,
        "start_date": "2025-07-18",
        "trial_period": 0,
        "status": "active",
        "payment_method": "cash",
        "notes": ""
    }
    """
    try:
        data = request.data
        logger.info(f"Creating subscription with data: {data}")
        
        # Validate required fields
        required_fields = ['customer_id', 'product_id', 'quantity', 'price', 'billing_period', 'billing_interval']
        for field in required_fields:
            if field not in data or not data[field]:
                return Response(
                    {'error': f'Missing required field: {field}'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Get customer and product
        try:
            customer = Contact.objects.get(id=data['customer_id'])
        except Contact.DoesNotExist:
            return Response(
                {'error': 'Customer not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
            
        try:
            product = Product.objects.get(id=data['product_id'])
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Calculate totals
        quantity = int(data['quantity'])
        price = float(data['price'])
        total = quantity * price
        
        # Extract and validate shipping address data from frontend
        shipping_data = data.get('shipping_address', {})
        logger.info(f"Received shipping address data: {shipping_data}")
        
        # Validate shipping address fields
        required_shipping_fields = ['address', 'city', 'state', 'postcode', 'country']
        for field in required_shipping_fields:
            if not shipping_data.get(field, '').strip():
                logger.warning(f"Missing or empty shipping field: {field}")
                return Response(
                    {'error': f'Shipping {field} is required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Validate WooCommerce customer and product IDs
        woo_customer_id = getattr(customer, 'woo_customer_id', None)
        woo_product_id = getattr(product, 'woo_product_id', None)
        
        if not woo_customer_id:
            logger.error(f"Customer {customer.id} missing woo_customer_id")
            return Response(
                {'error': 'Customer is not linked to WooCommerce'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
            
        if not woo_product_id:
            logger.error(f"Product {product.id} missing woo_product_id")
            return Response(
                {'error': 'Product is not linked to WooCommerce'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        logger.info(f"Creating WooCommerce subscription for customer {customer.id} (WooID: {woo_customer_id}) and product {product.id} (WooID: {woo_product_id})")
        
        # Create WooCommerce subscription
        try:
            wc = WooCommerceAPI()
            
            # Prepare subscription data for WooCommerce
            # Fix date format - WooCommerce requires 'Y-m-d H:i:s' format
            # Process all date fields (start_date, next_payment_date, last_order_date, end_date)
            date_fields = {
                'start_date': data.get('start_date', datetime.now().strftime('%Y-%m-%d')),
                'next_payment_date': data.get('next_payment_date', ''),
                'last_order_date': data.get('last_order_date', ''),
                'end_date': data.get('end_date', '')
            }
            
            # Format dates for WooCommerce API
            for field, value in date_fields.items():
                if value and len(value) == 10:  # If only date provided (YYYY-MM-DD)
                    date_fields[field] = value + ' 00:00:00'  # Add time component
                    
            # Set start_date_str for the main subscription data
            start_date_str = date_fields['start_date']
            
            # Ensure billing and shipping names are not empty
            first_name = customer.first_name or 'Customer'
            last_name = customer.last_name or 'Name'
            
            # Extract payment information from request data
            payment_info = data.get('payment_info', {})
            raw_transaction_id = payment_info.get('transaction_id', f'manual-{int(time.time())}')
            
            # Clean transaction ID by removing any prefixes (cash-, cc-, etc.)
            if '-' in raw_transaction_id:
                parts = raw_transaction_id.split('-', 1)
                if parts[0] in ['cash', 'cc', 'manual', 'other']:
                    transaction_id = parts[1]
                else:
                    transaction_id = raw_transaction_id
            else:
                transaction_id = raw_transaction_id
                
            payment_status = payment_info.get('payment_status', 'approved')
            auth_code = payment_info.get('auth_code', '')
            amount_paid = payment_info.get('amount_paid', total)
            
            # Generate a unique POS order ID and number for metadata
            pos_order_id = f'pos-{int(time.time())}-{random.randint(1000, 9999)}'
            pos_order_number = f'POS-{datetime.now().strftime("%Y%m%d")}-{random.randint(1000, 9999)}'
            
            logger.info(f"📝 Adding transaction ID to subscription metadata: {transaction_id}")
            
            # Extract billing address data from frontend if available, otherwise use shipping address
            billing_data = data.get('billing_address', {})
            logger.info(f"Received billing address data: {billing_data}")
            
            # Format billing and shipping addresses according to WooCommerce API requirements
            # WooCommerce expects specific field names and formats
            
            # REFACTORED: Prioritize shipping address over billing address
            # Always use shipping address for billing address (per refactored requirements)
            billing_address = {
                'first_name': first_name,
                'last_name': last_name,
                'company': '',
                # Always use shipping address fields first (per refactored requirements)
                'address_1': shipping_data.get('address', '').strip(),
                'address_2': shipping_data.get('address_2', '').strip(),
                'city': shipping_data.get('city', '').strip(),
                'state': shipping_data.get('state', '').strip(),
                'postcode': shipping_data.get('postcode', '').strip(),
                'country': shipping_data.get('country', 'US').strip().upper(),
                'email': customer.email or '',
                'phone': getattr(customer, 'phone', '')
            }
            
            # Log that we're using shipping address for billing
            logger.info(f"✅ Using shipping address as billing address for WooCommerce subscription (per refactored requirements)")
            logger.info(f"✅ Shipping address: {shipping_data.get('address')}, {shipping_data.get('city')}, {shipping_data.get('state')}, {shipping_data.get('postcode')}")
            
            # Create shipping address from shipping data
            shipping_address = {
                'first_name': first_name,
                'last_name': last_name,
                'company': '',
                'address_1': shipping_data.get('address', '').strip(),
                'address_2': shipping_data.get('address_2', '').strip(),
                'city': shipping_data.get('city', '').strip(),
                'state': shipping_data.get('state', '').strip(),
                'postcode': shipping_data.get('postcode', '').strip(),
                'country': shipping_data.get('country', 'US').strip().upper(),
                'email': customer.email or '',
                'phone': getattr(customer, 'phone', '')
            }
            
            # Log shipping address being used
            logger.info(f"✅ Using shipping address for WooCommerce subscription: {shipping_data.get('address')}, {shipping_data.get('city')}, {shipping_data.get('state')}, {shipping_data.get('postcode')}")
            
            subscription_data = {
                'status': data.get('status', 'active'),
                'billing_period': data['billing_period'],
                'billing_interval': int(data['billing_interval']),
                'start_date': start_date_str,
                'customer_id': int(woo_customer_id),
                'payment_method': data.get('payment_method', 'cash'),
                'payment_method_title': data.get('payment_method', 'Cash').title(),
                'line_items': [{
                    'product_id': int(woo_product_id),
                    'quantity': quantity,
                    'total': str(total)
                }],
                'billing': billing_address,
                'shipping': shipping_address,
                'meta_data': [
                    # Source information
                    {'key': '_source_name', 'value': 'DS POS' if data.get('source') == 'POS' else data.get('source', 'Manual Subscription')},
                    {'key': 'source', 'value': 'DS POS' if data.get('source') == 'POS' else data.get('source', 'Manual Subscription')},
                    {'key': '_order_source', 'value': 'DS POS' if data.get('source') == 'POS' else data.get('source', 'Manual Subscription')},
                    {'key': '_created_via', 'value': 'Subscription Management'},
                    {'key': '_shipping_address_validated', 'value': 'true'},
                    
                    # Payment and transaction metadata (important for WooCommerce compatibility)
                    {'key': '_payment_transaction_id', 'value': transaction_id},
                    {'key': '_pos_order_id', 'value': pos_order_id},
                    {'key': '_pos_order_number', 'value': pos_order_number},
                    {'key': '_pos_payment_method', 'value': data.get('payment_method', 'cash')},
                    {'key': '_pos_transaction_id', 'value': transaction_id},
                    {'key': '_related_orders', 'value': []},
                    {'key': '_subscription_parent_order', 'value': ''},
                    {'key': '_subscription_renewal', 'value': 'manual'},
                    
                    # Additional payment information
                    {'key': '_payment_status', 'value': payment_status},
                    {'key': '_auth_code', 'value': auth_code},
                    {'key': '_amount_paid', 'value': str(amount_paid)},
                    
                    # Date fields
                    {'key': 'next_payment_date', 'value': date_fields['next_payment_date'] if date_fields['next_payment_date'] else ''},
                    {'key': 'last_order_date', 'value': date_fields['last_order_date'] if date_fields['last_order_date'] else ''},
                    {'key': 'end_date', 'value': date_fields['end_date'] if date_fields['end_date'] else ''}
                ]
            }
            
            logger.info(f"Prepared WooCommerce subscription data: {subscription_data}")
            
            # Log billing and shipping address details specifically
            logger.info(f"📝 Billing address details:")
            logger.info(f"   First Name: {subscription_data['billing'].get('first_name', '')}")
            logger.info(f"   Last Name: {subscription_data['billing'].get('last_name', '')}")
            logger.info(f"   Address: {subscription_data['billing'].get('address_1', '')}")
            logger.info(f"   City: {subscription_data['billing'].get('city', '')}")
            logger.info(f"   State: {subscription_data['billing'].get('state', '')}")
            logger.info(f"   Postcode: {subscription_data['billing'].get('postcode', '')}")
            logger.info(f"   Country: {subscription_data['billing'].get('country', '')}")
            
            logger.info(f"📝 Shipping address details:")
            logger.info(f"   First Name: {subscription_data['shipping'].get('first_name', '')}")
            logger.info(f"   Last Name: {subscription_data['shipping'].get('last_name', '')}")
            logger.info(f"   Address: {subscription_data['shipping'].get('address_1', '')}")
            logger.info(f"   City: {subscription_data['shipping'].get('city', '')}")
            logger.info(f"   State: {subscription_data['shipping'].get('state', '')}")
            logger.info(f"   Postcode: {subscription_data['shipping'].get('postcode', '')}")
            logger.info(f"   Country: {subscription_data['shipping'].get('country', '')}")
            
            # Create WooCommerce subscription
            logger.info("Sending request to WooCommerce API...")
            woo_response = wc.wcapi.post('subscriptions', subscription_data)
            
            logger.info(f"WooCommerce API response status: {woo_response.status_code}")
            
            if woo_response.status_code == 201:
                woo_subscription = woo_response.json()
                logger.info(f"✅ Successfully created WooCommerce subscription {woo_subscription['id']}")
                logger.info(f"Subscription shipping address saved: {woo_subscription.get('shipping', {})}")
                
                # Return success response with WooCommerce subscription only
                return Response({
                    'success': True,
                    'id': woo_subscription['id'],
                    'order_number': f"WOO-SUB-{woo_subscription['id']}",
                    'woo_subscription_id': woo_subscription['id'],
                    'subscription': {
                        'id': woo_subscription['id'],
                        'status': woo_subscription['status'],
                        'total': str(total),
                        'billing_period': data['billing_period'],
                        'billing_interval': data['billing_interval'],
                        'start_date': data.get('start_date'),
                        'customer_id': customer.id,
                        'billing_address': {
                            'first_name': first_name,
                            'last_name': last_name,
                            'address_1': shipping_data.get('address', '').strip(),
                            'city': shipping_data.get('city', '').strip(),
                            'state': shipping_data.get('state', '').strip(),
                            'postcode': shipping_data.get('postcode', '').strip(),
                            'country': shipping_data.get('country', 'US').strip().upper()
                        },
                        'shipping_address': {
                            'first_name': first_name,
                            'last_name': last_name,
                            'address_1': shipping_data.get('address', '').strip(),
                            'city': shipping_data.get('city', '').strip(),
                            'state': shipping_data.get('state', '').strip(),
                            'postcode': shipping_data.get('postcode', '').strip(),
                            'country': shipping_data.get('country', 'US').strip().upper()
                        },
                        'line_items': [{
                            'product_id': product.id,
                            'name': product.name,
                            'quantity': quantity,
                            'total': str(total)
                        }],
                        'payment_method': data.get('payment_method'),
                        'notes': data.get('notes', ''),
                        'shipping_address': shipping_data  # Include shipping address in response
                    }
                }, status=status.HTTP_201_CREATED)
            else:
                logger.error(f"❌ Failed to create WooCommerce subscription")
                logger.error(f"Status Code: {woo_response.status_code}")
                logger.error(f"Response Text: {woo_response.text}")
                logger.error(f"Request Data: {subscription_data}")
                
                # Try to parse error details from WooCommerce response
                try:
                    error_data = woo_response.json()
                    error_message = error_data.get('message', 'Unknown WooCommerce error')
                    error_details = error_data.get('data', {})
                    logger.error(f"WooCommerce Error Message: {error_message}")
                    logger.error(f"WooCommerce Error Details: {error_details}")
                except:
                    error_message = 'Failed to parse WooCommerce error response'
                    error_details = woo_response.text
                
                # Return detailed error information
                return Response({
                    'success': False,
                    'error': 'Failed to create WooCommerce subscription',
                    'message': error_message,
                    'details': error_details,
                    'status_code': woo_response.status_code,
                    'shipping_data_sent': shipping_data
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as woo_error:
            logger.error(f"WooCommerce API error: {str(woo_error)}")
            # Return error since we only create WooCommerce subscriptions
            return Response({
                'success': False,
                'error': 'WooCommerce integration failed',
                'details': str(woo_error)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except Exception as e:
        logger.error(f"Error creating subscription: {str(e)}")
        return Response(
            {'error': f'Failed to create subscription: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_brands(request):
    """
    Get all unique brands from both local Brand table and WooCommerce API.
    
    This endpoint combines brands from the local database with fresh brands
    from WooCommerce to ensure all available brands are shown in filters,
    even if products haven't been synced yet.
    
    Query Parameters:
        include_woocommerce (bool): Whether to include WooCommerce brands (default: true)
    
    Returns:
        Response with an array of unique brand names
    """
    try:
        from .models import Brand
        from django.core.cache import cache

        # Check if WooCommerce brands should be included
        include_woocommerce = request.GET.get('include_woocommerce', 'true').lower() == 'true'
        
        # Check Redis cache first (10 min TTL)
        cache_key = f"product_brands:{include_woocommerce}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached, status=status.HTTP_200_OK)
        
        # Get all available brands (local + WooCommerce)
        unique_brands = Brand.get_all_available_brands(include_woocommerce=include_woocommerce)
        
        source_info = "local database"
        if include_woocommerce:
            source_info += " and WooCommerce API"
        
        logger.info(f"Found {len(unique_brands)} unique brands from {source_info}")
        
        cache.set(cache_key, unique_brands, 600)
        return Response(unique_brands, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error fetching product brands: {str(e)}")
        return Response(
            {'error': f'Failed to fetch product brands: {str(e)}'}, 
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# Sync API Endpoints for Manual Resync Operations

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_products_manual(request):
    """
    Manual sync of products from WooCommerce using management command.
    
    This endpoint calls the sync_woocommerce_products management command
    to fetch fresh product data from WooCommerce.
    
    Returns:
        Response with sync status and results
    """
    try:
        from django.core.management import call_command
        from io import StringIO
        import sys
        
        # Capture command output
        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()
        
        start_time = time.time()
        
        try:
            # Call the management command
            call_command('sync_woocommerce_products')
            output = captured_output.getvalue()
            duration = time.time() - start_time
            
            return Response({
                'success': True,
                'message': 'Products synced successfully from WooCommerce',
                'output': output,
                'duration': round(duration, 2)
            }, status=status.HTTP_200_OK)
            
        except Exception as cmd_error:
            output = captured_output.getvalue()
            return Response({
                'success': False,
                'message': f'Product sync failed: {str(cmd_error)}',
                'output': output
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        finally:
            sys.stdout = old_stdout
            
    except Exception as e:
        logger.error(f"Error in manual product sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start product sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_fresh_products_manual(request):
    """
    Fresh sync of products from WooCommerce using the comprehensive sync_fresh_products command.
    
    This endpoint runs asynchronously to prevent frontend timeouts on long operations.
    Returns immediate response and runs sync in background.
    
    Features:
    - Syncs all products from WooCommerce (including bundles, variations, subscriptions)
    - Removes orphaned products that no longer exist in WooCommerce
    - Updates bundle data with fresh WooCommerce information
    - Ensures parent-child relationships are correct
    
    Returns:
        Immediate response indicating sync has started
    """
    try:
        import threading
        from django.core.management import call_command
        from io import StringIO
        import sys
        
        def run_fresh_sync():
            """Background comprehensive fresh sync function"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    logger.info("Starting comprehensive background fresh products sync...")
                    
                    # Step 1: Fresh Products Sync (base sync)
                    logger.info("Step 1/7: Running fresh products sync...")
                    call_command('sync_fresh_products')
                    
                    # Step 2: Product Prices Sync
                    logger.info("Step 2/7: Running product prices sync...")
                    call_command('sync_product_prices')
                    
                    # Step 3: Product Variations Sync
                    logger.info("Step 3/7: Running product variations sync...")
                    call_command('sync_product_variations', '--remove-orphaned', '--verbose')
                    
                    # Step 4: Product SKUs Sync
                    logger.info("Step 4/7: Running product SKUs sync...")
                    import subprocess
                    sku_result = subprocess.run([
                        'python3', '/var/www/newpos-posds/woo-ghl-contact-db/backend/sync_product_skus.py',
                        '--verbose'
                    ], cwd='/var/www/newpos-posds/woo-ghl-contact-db/backend', capture_output=True, text=True)
                    
                    # Step 5: Bundle Products Comprehensive Sync
                    logger.info("Step 5/7: Running comprehensive bundle sync...")
                    call_command('sync_woocommerce_products', '--product-type=bundle')
                    
                    # Step 6: Bundle Default Series Sync
                    logger.info("Step 6/7: Running bundle default series sync...")
                    call_command('sync_bundle_default_series')
                    
                    # Step 7: Product Types Sync
                    logger.info("Step 7/7: Running product types sync...")
                    call_command('sync_product_types')
                    
                    output = captured_output.getvalue()
                    duration = time.time() - start_time
                    
                    # Parse output for comprehensive results
                    lines = output.split('\n')
                    woo_products_found = 0
                    orphaned_removed = 0
                    products_synced = 0
                    prices_updated = 0
                    variations_updated = 0
                    skus_updated = 0
                    bundles_synced = 0
                    
                    for line in lines:
                        if 'WooCommerce products found:' in line:
                            try:
                                woo_products_found = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'Orphaned products removed:' in line:
                            try:
                                orphaned_removed = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'Products synced:' in line:
                            try:
                                products_synced = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'prices updated:' in line.lower():
                            try:
                                prices_updated = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'variations updated:' in line.lower():
                            try:
                                variations_updated = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'skus updated:' in line.lower():
                            try:
                                skus_updated = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'bundles synced:' in line.lower():
                            try:
                                bundles_synced = int(line.split(':')[1].strip())
                            except:
                                pass
                    
                    logger.info(f"Comprehensive fresh sync completed in {duration:.2f}s:")
                    logger.info(f"  - Products synced: {products_synced}")
                    logger.info(f"  - Orphaned removed: {orphaned_removed}")
                    logger.info(f"  - Prices updated: {prices_updated}")
                    logger.info(f"  - Variations updated: {variations_updated}")
                    logger.info(f"  - SKUs updated: {skus_updated}")
                    logger.info(f"  - Bundles synced: {bundles_synced}")
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    logger.error(f"Background comprehensive fresh sync failed: {str(cmd_error)}")
                    logger.error(f"Command output: {output}")
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                logger.error(f"Error in background comprehensive fresh sync: {str(e)}")
        
        # Start background sync
        sync_thread = threading.Thread(target=run_fresh_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        return Response({
            'success': True,
            'message': 'Fresh products sync started successfully! Running comprehensive sync for all products. Check logs for progress.',
            'details': {
                'sync_type': 'fresh_comprehensive',
                'async': True,
                'status': 'started'
            }
        }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error starting fresh products sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start fresh products sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except Exception as e:
        logger.error(f'Error in sync_fresh_products_manual: {str(e)}')
        return Response({
            'success': False,
            'message': f'Fresh product sync error: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@csrf_exempt
def sync_comprehensive_products_manual(request):
    """
    Comprehensive product sync using check_and_sync_missing_products management command.
    
    This endpoint provides a single comprehensive sync that replaces multiple individual syncs:
    - Fresh Products Sync
    - Product Variations Sync  
    - Product Categories Sync
    - Product SKUs Sync
    - Product Prices Sync
    - Subscriptions metadata
    
    Features:
    - Uses the comprehensive check_and_sync_missing_products command
    - Supports dry-run mode for safe preview
    - Interactive sync with progress reporting
    - Comprehensive fixes for existing products
    - ATUM inventory location sync
    
    Body Parameters:
    - dry_run (bool): Preview mode without making changes
    - sync_missing (bool): Auto-sync all missing products without prompts
    - fix_product (int): Fix specific product by WooCommerce ID
    - verbose (bool): Detailed logging output
    
    Returns:
        Response with sync status and progress details
    """
    try:
        import threading
        from django.core.management import call_command
        from io import StringIO
        import sys
        import time
        
        # Extract parameters
        data = request.data if hasattr(request, 'data') else {}
        dry_run = data.get('dry_run', False)
        sync_missing = data.get('sync_missing', True)  # Default to auto-sync
        fix_product = data.get('fix_product')
        verbose = data.get('verbose', True)
        
        def run_comprehensive_sync():
            """Background comprehensive sync function"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    logger.info("Starting comprehensive product sync using check_and_sync_missing_products...")
                    
                    # Build command arguments
                    cmd_args = []
                    if dry_run:
                        cmd_args.append('--dry-run')
                    if sync_missing:
                        cmd_args.append('--sync-missing')
                    if verbose:
                        cmd_args.append('--verbose')
                    if fix_product:
                        cmd_args.extend(['--fix-product', str(fix_product)])
                    
                    # Call the comprehensive sync command
                    call_command('check_and_sync_missing_products', *cmd_args)
                    
                    output = captured_output.getvalue()
                    duration = time.time() - start_time
                    
                    logger.info(f"Comprehensive product sync completed in {duration:.2f}s")
                    logger.info(f"Command output summary: {len(output.split(chr(10)))} lines of output")
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    logger.error(f"Comprehensive product sync failed: {str(cmd_error)}")
                    logger.error(f"Command output: {output}")
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                logger.error(f"Error in comprehensive product sync: {str(e)}")
        
        # Start background sync if not dry-run or if specifically requested
        if not dry_run or sync_missing:
            sync_thread = threading.Thread(target=run_comprehensive_sync)
            sync_thread.daemon = True
            sync_thread.start()
            
            response_message = "Comprehensive product sync started! This replaces all individual product syncs with one powerful command."
            if dry_run:
                response_message = "Comprehensive product sync (DRY RUN) started! Check logs for preview of changes."
        else:
            # For dry-run without sync_missing, run synchronously for immediate feedback
            run_comprehensive_sync()
            response_message = "Comprehensive product sync (DRY RUN) completed! Check logs for preview of changes."
        
        return Response({
            'success': True,
            'message': response_message,
            'details': {
                'async': not (dry_run and not sync_missing),
                'status': 'started' if not (dry_run and not sync_missing) else 'completed',
                'sync_type': 'comprehensive_check_and_sync',
                'dry_run': dry_run,
                'sync_missing': sync_missing,
                'fix_product': fix_product,
                'verbose': verbose,
                'features': [
                    'Complete product synchronization (all types)',
                    'Missing product detection and sync',
                    'Comprehensive subscription metadata (5 schemes)',
                    'Product variations with attributes and pricing',
                    'Category serialization fixes',
                    'SKU sync for simple and variable products',
                    'Direct database pricing (bypasses cache)',
                    'ATUM inventory location sync',
                    'Orphaned product cleanup'
                ]
            }
        }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error starting comprehensive product sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start comprehensive product sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@csrf_exempt
def sync_customers_manual(request):
    """
    Manual sync of customers from WooCommerce using management command.
    
    This endpoint runs asynchronously to prevent frontend timeouts on long operations.
    Returns immediate response and runs sync in background.
    
    Features:
    - Calls the sync_woocommerce_customers management command
    - Fetches fresh customer data from WooCommerce
    - Handles large customer databases without timeout
    
    Returns:
        Immediate response indicating sync has started
    """
    try:
        import threading
        from django.core.management import call_command
        from io import StringIO
        import sys
        
        def run_customer_sync():
            """Background customer sync function"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    logger.info("Starting background customers sync...")
                    
                    # Call the management command
                    call_command('sync_woocommerce_customers')
                    output = captured_output.getvalue()
                    duration = time.time() - start_time
                    
                    logger.info(f"Background customers sync completed in {duration:.2f}s")
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    logger.error(f"Background customers sync failed: {str(cmd_error)}")
                    logger.error(f"Command output: {output}")
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                logger.error(f"Error in background customers sync: {str(e)}")
        
        # Start background sync
        sync_thread = threading.Thread(target=run_customer_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        return Response({
            'success': True,
            'message': 'Customers sync started successfully! Importing ALL customer profiles from WooCommerce. Check logs for progress.',
            'details': {
                'async': True,
                'status': 'started'
            }
        }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error starting customers sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start customers sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@csrf_exempt
def sync_users_manual(request):
    """
    Manual sync of WordPress admin/staff users from WooCommerce.
    
    This endpoint syncs WordPress users with admin roles (administrator, shop_manager, 
    editor, author) into the POS system's Django auth_user table.
    
    Returns:
        Response with sync status and results
    """
    try:
        from django.contrib.auth.models import User, Group
        from .woocommerce import WooCommerceAPI
        import time
        
        start_time = time.time()
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Fetch staff users from WordPress
        staff_data = wc_api.get_staff_users()
        
        if not staff_data or not staff_data.get('data'):
            return Response({
                'success': False,
                'message': 'No staff users found in WooCommerce'
            }, status=status.HTTP_404_NOT_FOUND)
        
        staff_users = staff_data['data']
        
        # WordPress role to Django group mapping
        role_mapping = {
            'administrator': 'POS Admin',
            'shop_manager': 'POS Manager', 
            'editor': 'POS Editor',
            'author': 'POS User'
        }
        
        # Create Django groups if they don't exist
        for group_name in role_mapping.values():
            Group.objects.get_or_create(name=group_name)
        
        synced_count = 0
        updated_count = 0
        created_count = 0
        
        for wp_user in staff_users:
            try:
                # Extract user data
                wp_id = wp_user.get('id')
                email = wp_user.get('email', '').strip()
                first_name = wp_user.get('first_name', '').strip()
                last_name = wp_user.get('last_name', '').strip()
                username = wp_user.get('username', email)
                wp_role = wp_user.get('wordpress_role', 'author')
                
                if not email:
                    logger.warning(f"Skipping WordPress user {wp_id} - no email")
                    continue
                
                # Check if user exists by email or username
                user = None
                try:
                    user = User.objects.get(email=email)
                    updated_count += 1
                except User.DoesNotExist:
                    try:
                        user = User.objects.get(username=username)
                        updated_count += 1
                    except User.DoesNotExist:
                        # Create new user
                        user = User.objects.create_user(
                            username=username,
                            email=email,
                            first_name=first_name,
                            last_name=last_name,
                            is_staff=True,  # WordPress admin users should have staff access
                            is_active=True
                        )
                        created_count += 1
                
                # Update user information
                user.first_name = first_name
                user.last_name = last_name
                user.email = email
                user.is_staff = True
                
                # Set superuser status for administrators
                if wp_role == 'administrator':
                    user.is_superuser = True
                
                user.save()
                
                # Assign to appropriate group
                if wp_role in role_mapping:
                    group = Group.objects.get(name=role_mapping[wp_role])
                    user.groups.clear()  # Clear existing groups
                    user.groups.add(group)
                
                synced_count += 1
                logger.info(f"Synced WordPress user: {email} (role: {wp_role})")
                
            except Exception as user_error:
                logger.error(f"Error syncing user {wp_user.get('email', 'unknown')}: {str(user_error)}")
                continue
        
        duration = time.time() - start_time
        
        return Response({
            'success': True,
            'message': f'Users/staff synced successfully from WordPress',
            'details': {
                'total_processed': len(staff_users),
                'synced_count': synced_count,
                'created_count': created_count,
                'updated_count': updated_count,
                'role_mapping': role_mapping
            },
            'duration': round(duration, 2)
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error in manual users/staff sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to sync users/staff: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_subscriptions_manual(request):
    """
    Manual sync of subscriptions from WooCommerce using management command.
    
    This endpoint runs asynchronously to prevent frontend timeouts on long operations.
    Returns immediate response and runs sync in background.
    
    Features:
    - Calls the sync_subscription_products management command
    - Ensures subscription table is populated
    - Handles large subscription databases without timeout
    
    Returns:
        Immediate response indicating sync has started
    """
    try:
        import threading
        from django.core.management import call_command
        from io import StringIO
        import sys
        
        def run_subscription_sync():
            """Background subscription sync function"""
            try:
                # Capture command output
                old_stdout = sys.stdout        
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    logger.info("Starting background subscriptions sync...")
                    
                    # Call the management command
                    call_command('sync_subscription_products')
                    output = captured_output.getvalue()
                    duration = time.time() - start_time
                    
                    logger.info(f"Background subscriptions sync completed in {duration:.2f}s")
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    logger.error(f"Background subscriptions sync failed: {str(cmd_error)}")
                    logger.error(f"Command output: {output}")
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                logger.error(f"Error in background subscriptions sync: {str(e)}")
        
        # Start background sync
        sync_thread = threading.Thread(target=run_subscription_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        return Response({
            'success': True,
            'message': 'Subscriptions sync started successfully! Importing subscription products and customer subscriptions. Check logs for progress.',
            'details': {
                'async': True,
                'status': 'started'
            }
        }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error starting subscriptions sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start subscriptions sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_inventory_locations_manual(request):
    """
    Manual sync of inventory locations using management command.
    
    This endpoint calls the sync_inventory_locations management command
    to sync inventory data from WooCommerce with timeout protection.
    
    Returns:
        Response with sync status and results
    """
    try:
        from django.core.management import call_command
        from io import StringIO
        import sys
        import threading
        import queue
        
        # Create a queue to capture results from the thread
        result_queue = queue.Queue()
        
        def run_sync():
            """Run sync in a separate thread"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    # Call the management command with create-default-location flag
                    call_command('sync_inventory_locations', verbose=True, create_default_location=True)
                    output = captured_output.getvalue()
                    duration = time.time() - start_time
                    
                    result_queue.put({
                        'success': True,
                        'message': 'Inventory locations synced successfully from WooCommerce',
                        'output': output,
                        'duration': round(duration, 2)
                    })
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    result_queue.put({
                        'success': False,
                        'message': f'Inventory locations sync failed: {str(cmd_error)}',
                        'output': output
                    })
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                result_queue.put({
                    'success': False,
                    'message': f'Thread error: {str(e)}',
                    'output': ''
                })
        
        # Start sync in background thread
        sync_thread = threading.Thread(target=run_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        # Wait for result with timeout (25 seconds to stay under 30s limit)
        try:
            result = result_queue.get(timeout=25)
            return Response(result, status=status.HTTP_200_OK if result['success'] else status.HTTP_500_INTERNAL_SERVER_ERROR)
        except queue.Empty:
            # Timeout occurred - sync is still running in background
            return Response({
                'success': True,
                'message': 'Inventory locations sync started successfully. This process may take several minutes to complete with 1,300+ products. The sync will continue in the background.',
                'output': 'Sync initiated - processing large product catalog...',
                'duration': 25,
                'note': 'For large catalogs, consider running this sync during off-peak hours or use the Django admin command directly.'
            }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error in manual inventory locations sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start inventory locations sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_atum_locations_manual(request):
    """
    Manual sync of ATUM inventory locations using management command.
    
    This endpoint calls the restore_atum_locations management command
    to sync ATUM inventory locations with timeout protection.
    
    Returns:
        Response with sync status and results
    """
    try:
        from django.core.management import call_command
        from io import StringIO
        import sys
        import threading
        import queue
        
        # Create a queue to capture results from the thread
        result_queue = queue.Queue()
        
        # Get batch size from request or use default
        batch_size = request.data.get('batch_size', 100)
        
        def run_sync():
            """Run sync in a separate thread"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    # Call the management command
                    call_command('restore_atum_locations', batch_size=batch_size, verbose=True)
                    output = captured_output.getvalue()
                    duration = time.time() - start_time
                    
                    result_queue.put({
                        'success': True,
                        'message': 'ATUM inventory locations synced successfully',
                        'output': output,
                        'duration': round(duration, 2)
                    })
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    result_queue.put({
                        'success': False,
                        'message': f'ATUM locations sync failed: {str(cmd_error)}',
                        'output': output
                    })
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                result_queue.put({
                    'success': False,
                    'message': f'Thread error: {str(e)}',
                    'output': ''
                })
        
        # Start sync in background thread
        sync_thread = threading.Thread(target=run_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        # Wait for result with timeout (25 seconds to stay under 30s limit)
        try:
            result = result_queue.get(timeout=25)
            return Response(result, status=status.HTTP_200_OK if result['success'] else status.HTTP_500_INTERNAL_SERVER_ERROR)
        except queue.Empty:
            # Timeout occurred - sync is still running in background
            return Response({
                'success': True,
                'message': 'ATUM locations sync started successfully. This process may take several minutes to complete. The sync will continue in the background.',
                'output': 'Sync initiated - processing product catalog...',
                'duration': 25,
                'note': 'The sync will automatically process all products in batches of ' + str(batch_size)
            }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error in manual ATUM locations sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start ATUM locations sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_brands_manual(request):
    """
    Manual sync of brands from WooCommerce using management command.
    
    This endpoint calls the sync_brands management command
    to extract and sync brand data from product attributes.
    
    Returns:
        Response with sync status and results
    """
    try:
        from django.core.management import call_command
        from io import StringIO
        import sys
        
        # Capture command output
        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()
        
        start_time = time.time()
        
        try:
            # Call the management command with verbose flag
            call_command('sync_brands', verbose=True)
            output = captured_output.getvalue()
            duration = time.time() - start_time
            
            return Response({
                'success': True,
                'message': 'Brands synced successfully from WooCommerce',
                'output': output,
                'duration': round(duration, 2)
            }, status=status.HTTP_200_OK)
            
        except Exception as cmd_error:
            output = captured_output.getvalue()
            return Response({
                'success': False,
                'message': f'Brand sync failed: {str(cmd_error)}',
                'output': output
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        finally:
            sys.stdout = old_stdout
            
    except Exception as e:
        logger.error(f"Error in manual brand sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start brand sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_atum_inventory_manual(request):
    """
    Manual sync of ATUM inventory locations and product inventories.
    
    This endpoint calls the sync_atum_inventory management command
    to sync ATUM inventory data from WooCommerce with timeout protection.
    
    Returns:
        Response with sync status and results
    """
    try:
        from django.core.management import call_command
        from io import StringIO
        import sys
        import threading
        import queue
        
        # Create a queue to capture results from the thread
        result_queue = queue.Queue()
        
        def run_sync():
            """Run sync in a separate thread"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    # Call the management command with verbose flag
                    call_command('sync_atum_inventory', verbose=True)
                    output = captured_output.getvalue()
                    duration = time.time() - start_time
                    
                    result_queue.put({
                        'success': True,
                        'message': 'ATUM inventory synced successfully from WooCommerce',
                        'output': output,
                        'duration': round(duration, 2)
                    })
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    result_queue.put({
                        'success': False,
                        'message': f'ATUM inventory sync failed: {str(cmd_error)}',
                        'output': output
                    })
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                result_queue.put({
                    'success': False,
                    'message': f'Thread error: {str(e)}',
                    'output': ''
                })
        
        # Start sync in background thread
        sync_thread = threading.Thread(target=run_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        # Wait for result with timeout (25 seconds to stay under 30s limit)
        try:
            result = result_queue.get(timeout=25)
            return Response(result, status=status.HTTP_200_OK if result['success'] else status.HTTP_500_INTERNAL_SERVER_ERROR)
        except queue.Empty:
            # Timeout occurred - sync is still running in background
            return Response({
                'success': True,
                'message': 'ATUM inventory sync started successfully. This process may take several minutes to complete. The sync will continue in the background.',
                'output': 'ATUM sync initiated - processing inventory locations and product assignments...',
                'duration': 25,
                'note': 'The sync is processing ATUM inventory data and will continue running in the background.'
            }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error in manual ATUM inventory sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start ATUM inventory sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_product_prices_manual(request):
    """
    Manual sync of product prices from WooCommerce using management command.
    
    This endpoint runs asynchronously to prevent frontend timeouts on long operations.
    Returns immediate response and runs sync in background.
    
    Features:
    - Syncs product prices from WooCommerce to the POS database
    - Supports both full sync (all products) and single product sync
    - Updates simple, variable, subscription, and bundle product prices
    
    Request body (optional):
        {
            "product_id": "woo_product_id",  // Optional: sync specific product
            "dry_run": true  // Optional: preview changes without applying
        }
    
    Returns:
        Immediate response indicating sync has started
    """
    try:
        import threading
        from django.core.management import call_command
        from io import StringIO
        import sys
        import json
        
        # Parse request data - use request.data for DRF, avoid reading body twice
        request_data = {}
        if hasattr(request, 'data'):
            request_data = request.data or {}
        
        product_id = request_data.get('product_id')
        dry_run = request_data.get('dry_run', False)
        
        def run_price_sync():
            """Background price sync function"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    # Build command arguments
                    command_args = []
                    if product_id:
                        command_args.extend(['--product-id', str(product_id)])
                    if dry_run:
                        command_args.append('--dry-run')
                    
                    logger.info(f"Starting background price sync with args: {command_args}")
                    
                    # Execute the management command
                    call_command('sync_product_prices', *command_args)
                    output = captured_output.getvalue()
                    
                    duration = time.time() - start_time
                    
                    # Parse output for statistics
                    products_processed = 0
                    prices_updated = 0
                    variations_updated = 0
                    subscriptions_updated = 0
                    
                    if output:
                        lines = output.split('\n')
                        for line in lines:
                            if 'products processed:' in line.lower():
                                try:
                                    products_processed = int(line.split(':')[1].strip())
                                except:
                                    pass
                            elif 'prices updated:' in line.lower():
                                try:
                                    prices_updated = int(line.split(':')[1].strip())
                                except:
                                    pass
                            elif 'variations updated:' in line.lower():
                                try:
                                    variations_updated = int(line.split(':')[1].strip())
                                except:
                                    pass
                            elif 'subscriptions updated:' in line.lower():
                                try:
                                    subscriptions_updated = int(line.split(':')[1].strip())
                                except:
                                    pass
                    
                    logger.info(f"Background price sync completed: {prices_updated} products, {variations_updated} variations, {subscriptions_updated} subscriptions updated in {duration:.2f}s")
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    logger.error(f"Background price sync failed: {str(cmd_error)}")
                    logger.error(f"Command output: {output}")
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                logger.error(f"Error in background price sync: {str(e)}")
        
        # Start background sync
        sync_thread = threading.Thread(target=run_price_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        sync_type = 'dry-run' if dry_run else 'live'
        scope = f'single product ({product_id})' if product_id else 'all products'
        
        return Response({
            'success': True,
            'message': f'Price sync started successfully! Running {sync_type} sync for {scope}. Check logs for progress.',
            'details': {
                'sync_type': sync_type,
                'scope': scope,
                'dry_run': dry_run,
                'async': True,
                'status': 'started'
            }
        }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error starting price sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start price sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_product_variations_manual(request):
    """
    Manual sync of product variations from WooCommerce using management command.
    
    This endpoint runs asynchronously to prevent frontend timeouts on long operations.
    Returns immediate response and runs sync in background.
    
    Features:
    - Updates existing variations with fresh WooCommerce data
    - Creates new variations that exist in WooCommerce but not locally
    - Removes orphaned variations that no longer exist in WooCommerce
    - Syncs all variation fields: SKU, prices, stock, attributes
    
    Request body (optional):
        {
            "product_id": "woo_product_id",  // Optional: sync specific product
            "remove_orphaned": true,         // Optional: remove orphaned variations (default: true)
            "dry_run": false,               // Optional: preview changes without applying
            "verbose": true                 // Optional: detailed output (default: true)
        }
    
    Returns:
        Immediate response indicating sync has started
    """
    try:
        import threading
        from django.core.management import call_command
        from io import StringIO
        import sys
        import json
        
        # Parse request data - use request.data for DRF, avoid reading body twice
        request_data = {}
        if hasattr(request, 'data'):
            request_data = request.data or {}
        
        product_id = request_data.get('product_id')
        remove_orphaned = request_data.get('remove_orphaned', True)
        dry_run = request_data.get('dry_run', False)
        verbose = request_data.get('verbose', True)
        
        def run_sync():
            """Background sync function"""
            try:
                # Capture command output
                old_stdout = sys.stdout
                sys.stdout = captured_output = StringIO()
                
                start_time = time.time()
                
                try:
                    # Build command arguments
                    command_args = []
                    if product_id:
                        command_args.extend(['--product-id', str(product_id)])
                    if remove_orphaned:
                        command_args.append('--remove-orphaned')
                    if dry_run:
                        command_args.append('--dry-run')
                    if verbose:
                        command_args.append('--verbose')
                    
                    logger.info(f"Starting background variation sync with args: {command_args}")
                    
                    # Execute the management command
                    call_command('sync_product_variations', *command_args)
                    output = captured_output.getvalue()
                    
                    duration = time.time() - start_time
                    
                    # Parse output for statistics
                    variations_updated = 0
                    variations_created = 0
                    variations_removed = 0
                    products_processed = 0
                    
                    if output:
                        lines = output.split('\n')
                        for line in lines:
                            if 'variations updated:' in line.lower():
                                try:
                                    variations_updated = int(line.split(':')[1].strip())
                                except:
                                    pass
                            elif 'variations created:' in line.lower():
                                try:
                                    variations_created = int(line.split(':')[1].strip())
                                except:
                                    pass
                            elif 'variations removed:' in line.lower():
                                try:
                                    variations_removed = int(line.split(':')[1].strip())
                                except:
                                    pass
                            elif 'products processed:' in line.lower():
                                try:
                                    products_processed = int(line.split(':')[1].strip())
                                except:
                                    pass
                    
                    logger.info(f"Background variation sync completed: {variations_updated} updated, {variations_created} created, {variations_removed} removed in {duration:.2f}s")
                    
                except Exception as cmd_error:
                    output = captured_output.getvalue()
                    logger.error(f"Background variation sync failed: {str(cmd_error)}")
                    logger.error(f"Command output: {output}")
                    
                finally:
                    sys.stdout = old_stdout
                    
            except Exception as e:
                logger.error(f"Error in background variation sync: {str(e)}")
        
        # Start background sync
        sync_thread = threading.Thread(target=run_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        sync_type = 'dry-run' if dry_run else 'live'
        scope = f'single product ({product_id})' if product_id else 'all products'
        
        return Response({
            'success': True,
            'message': f'Variation sync started successfully! Running {sync_type} sync for {scope}. Check logs for progress.',
            'details': {
                'sync_type': sync_type,
                'scope': scope,
                'remove_orphaned': remove_orphaned,
                'dry_run': dry_run,
                'async': True,
                'status': 'started'
            }
        }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error starting variation sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start variation sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def get_variation_id_by_attributes(request):
    """
    Get WooCommerce variation ID by product UUID and attributes
    """
    try:
        product_uuid = request.data.get('product_uuid')
        attributes = request.data.get('attributes', {})  # e.g., {'Series': 'Single'}
        
        logger.info(f"🔥 VARIATION LOOKUP: UUID={product_uuid}, attributes={attributes}")
        
        if not product_uuid:
            return Response({'error': 'Product UUID is required'}, status=400)
        
        if not attributes:
            return Response({'error': 'Attributes are required'}, status=400)
        
        # Find the product by UUID (using id field which is the UUID primary key)
        product = Product.objects.filter(id=product_uuid).first()
        if not product:
            logger.warning(f"🔥 VARIATION LOOKUP: Product not found for UUID {product_uuid}")
            return Response({'error': 'Product not found'}, status=404)
        
        woo_product_id = product.woo_product_id
        if not woo_product_id:
            logger.warning(f"🔥 VARIATION LOOKUP: No WooCommerce product ID for UUID {product_uuid}")
            return Response({'error': 'No WooCommerce product ID'}, status=404)
        
        logger.info(f"🔥 VARIATION LOOKUP: Found WooCommerce product ID {woo_product_id} for UUID {product_uuid}")
        
        # Fetch variations from WooCommerce API
        wc = WooCommerceAPI()
        variations = wc.get_product_variations(woo_product_id)
        
        if variations:
            logger.info(f"🔥 VARIATION LOOKUP: Found {len(variations)} variations for product {woo_product_id}")
            
            # Find matching variation by attributes
            for variation in variations:
                if variation.get('attributes'):
                    match = True
                    variation_attrs = variation['attributes']
                    
                    logger.info(f"🔥 VARIATION LOOKUP: Checking variation {variation['id']} with attributes: {variation_attrs}")
                    
                    for attr_name, attr_value in attributes.items():
                        # Try multiple attribute name formats
                        variation_attr = None
                        for v_attr in variation_attrs:
                            v_attr_name = v_attr.get('name', '')
                            if (v_attr_name == attr_name or 
                                v_attr_name == f'pa_{attr_name.lower()}' or
                                v_attr_name.lower() == attr_name.lower()):
                                variation_attr = v_attr
                                break
                        
                        if not variation_attr:
                            logger.info(f"🔥 VARIATION LOOKUP: Attribute {attr_name} not found in variation {variation['id']}")
                            match = False
                            break
                        
                        variation_value = variation_attr.get('option', '')
                        if variation_value != attr_value:
                            logger.info(f"🔥 VARIATION LOOKUP: Attribute value mismatch: {variation_value} != {attr_value}")
                            match = False
                            break
                    
                    if match:
                        logger.info(f"🔥 VARIATION LOOKUP SUCCESS: Found matching variation {variation['id']} for attributes {attributes}")
                        return Response({
                            'variation_id': variation['id'],
                            'variation_name': variation.get('name', ''),
                            'attributes': variation.get('attributes', []),
                            'price': variation.get('price', ''),
                            'sku': variation.get('sku', '')
                        })
        
        logger.warning(f"🔥 VARIATION LOOKUP: No matching variation found for attributes {attributes}")
        return Response({'error': 'Variation not found'}, status=404)
        
    except Exception as e:
        logger.error(f"🔥 VARIATION LOOKUP ERROR: {str(e)}")
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_product_skus_manual(request):
    """
    Manual sync of product SKUs from WooCommerce using the standalone SKU sync script.
    
    This endpoint runs asynchronously to prevent frontend timeouts on long operations.
    Returns immediate response and runs sync in background.
    
    Request body (optional):
    {
        "product_id": 123,      # Optional: Sync specific product by WooCommerce ID
        "dry_run": true,        # Optional: Preview changes without updating database
        "verbose": true         # Optional: Enable verbose logging
    }
    
    Returns:
        Immediate response indicating sync has started
    """
    try:
        import threading
        import subprocess
        import sys
        import time
        
        # Get request parameters
        data = request.data if hasattr(request, 'data') else {}
        product_id = data.get('product_id')
        dry_run = data.get('dry_run', False)
        verbose = data.get('verbose', False)
        
        def run_sku_sync():
            """Background SKU sync function"""
            try:
                start_time = time.time()
                
                # Build command arguments
                script_path = '/var/www/newpos-posds/woo-ghl-contact-db/backend/sync_product_skus.py'
                cmd = ['python3', script_path]
                
                if dry_run:
                    cmd.append('--dry-run')
                if verbose:
                    cmd.append('--verbose')
                if product_id:
                    cmd.extend(['--product-id', str(product_id)])
                
                logger.info(f"Starting background SKU sync with command: {' '.join(cmd)}")
                
                # Execute the sync script
                try:
                    result = subprocess.run(
                        cmd,
                        cwd='/var/www/newpos-posds/woo-ghl-contact-db/backend',
                        capture_output=True,
                        text=True,
                        timeout=600  # 10 minute timeout for background process
                    )
                    
                    duration = time.time() - start_time
                    
                    # Parse output for statistics
                    output = result.stdout + result.stderr
                    products_processed = 0
                    simple_skus_updated = 0
                    variation_skus_updated = 0
                    skipped = 0
                    errors = 0
                    
                    # Extract statistics from output
                    for line in output.split('\n'):
                        if 'Products processed:' in line:
                            try:
                                products_processed = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'Simple product SKUs updated:' in line:
                            try:
                                simple_skus_updated = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'Variation SKUs updated:' in line:
                            try:
                                variation_skus_updated = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'Products skipped:' in line:
                            try:
                                skipped = int(line.split(':')[1].strip())
                            except:
                                pass
                        elif 'Errors:' in line:
                            try:
                                errors = int(line.split(':')[1].strip())
                            except:
                                pass
                    
                    if result.returncode == 0:
                        logger.info(f"Background SKU sync completed: {simple_skus_updated} simple SKUs, {variation_skus_updated} variation SKUs updated in {duration:.2f}s")
                    else:
                        logger.error(f"Background SKU sync failed with return code {result.returncode}")
                        logger.error(f"Command output: {output}")
                        
                except subprocess.TimeoutExpired:
                    logger.error("Background SKU sync timed out after 10 minutes")
                    
                except Exception as cmd_error:
                    logger.error(f"Background SKU sync failed: {str(cmd_error)}")
                    
            except Exception as e:
                logger.error(f"Error in background SKU sync: {str(e)}")
        
        # Start background sync
        sync_thread = threading.Thread(target=run_sku_sync)
        sync_thread.daemon = True
        sync_thread.start()
        
        sync_type = 'dry-run' if dry_run else 'live'
        scope = f'single product ({product_id})' if product_id else 'all products'
        
        return Response({
            'success': True,
            'message': f'SKU sync started successfully! Running {sync_type} sync for {scope}. Check logs for progress.',
            'details': {
                'sync_type': sync_type,
                'scope': scope,
                'dry_run': dry_run,
                'async': True,
                'status': 'started'
            }
        }, status=status.HTTP_200_OK)
            
    except Exception as e:
        logger.error(f"Error starting SKU sync: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to start SKU sync: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_product_sync(request, product_id):
    """
    Update a product in both local database and WooCommerce.
    
    This endpoint updates the product in the local database first,
    then syncs the changes back to WooCommerce to keep both systems in sync.
    
    Args:
        request: The HTTP request with product update data
        product_id: UUID of the product to update
        
    Returns:
        Response with updated product data and sync status
    """
    try:
        from .woocommerce import WooCommerceAPI
        import json
        
        # Get the product from local database
        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Product not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Update local database first
        serializer = ProductWithSKUSerializer(product, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response({
                'success': False,
                'message': 'Invalid product data',
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
        
        updated_product = serializer.save()
        
        # Sync changes to WooCommerce if product has WooCommerce ID
        woo_sync_success = True
        woo_sync_message = "No WooCommerce sync needed"
        
        if hasattr(updated_product, 'woo_product_id') and updated_product.woo_product_id:
            try:
                wc = WooCommerceAPI()
                
                # Prepare WooCommerce update data
                woo_update_data = {
                    'name': updated_product.name,
                    'description': updated_product.description or '',
                    'regular_price': str(updated_product.price),
                    'status': updated_product.status,  # Use the actual status field from Product model
                    'manage_stock': True,
                    'stock_quantity': updated_product.stock_quantity,
                }
                
                # Add categories if available
                if updated_product.categories:
                    # Get or create categories in WooCommerce and map to IDs
                    categories = []
                    for category_name in updated_product.categories:
                        try:
                            # First, try to find existing category
                            category_response = wc.wcapi.get("products/categories", params={
                                'search': category_name,
                                'per_page': 1
                            })
                            
                            if category_response.status_code == 200 and category_response.json():
                                # Use existing category
                                existing_category = category_response.json()[0]
                                categories.append({'id': existing_category['id']})
                                logger.info(f"Found existing WooCommerce category: {category_name} (ID: {existing_category['id']})")
                            else:
                                # Create new category
                                create_response = wc.wcapi.post("products/categories", {
                                    'name': category_name
                                })
                                
                                if create_response.status_code == 201:
                                    new_category = create_response.json()
                                    categories.append({'id': new_category['id']})
                                    logger.info(f"Created new WooCommerce category: {category_name} (ID: {new_category['id']})")
                                else:
                                    logger.warning(f"Failed to create WooCommerce category: {category_name}")
                                    # Fallback to name-based approach
                                    categories.append({'name': category_name})
                                    
                        except Exception as cat_error:
                            logger.error(f"Error processing category {category_name}: {str(cat_error)}")
                            # Fallback to name-based approach
                            categories.append({'name': category_name})
                    
                    if categories:
                        woo_update_data['categories'] = categories
                
                # Update product in WooCommerce
                logger.info(f"Syncing product {product_id} to WooCommerce with data: {woo_update_data}")
                woo_response = wc.wcapi.put(f"products/{updated_product.woo_product_id}", woo_update_data)
                
                if woo_response.status_code == 200:
                    woo_sync_success = True
                    woo_sync_message = "Successfully synced to WooCommerce"
                    logger.info(f"Successfully synced product {product_id} to WooCommerce")
                else:
                    woo_sync_success = False
                    woo_sync_message = f"WooCommerce sync failed: {woo_response.status_code}"
                    logger.warning(f"WooCommerce sync failed for product {product_id}: Status {woo_response.status_code}, Response: {woo_response.text}")
                    
            except Exception as woo_error:
                woo_sync_success = False
                woo_sync_message = f"WooCommerce sync error: {str(woo_error)}"
                logger.error(f"Error syncing product {product_id} to WooCommerce: {str(woo_error)}")
        
        return Response({
            'success': True,
            'message': 'Product updated successfully',
            'product': serializer.data,
            'woocommerce_sync': {
                'success': woo_sync_success,
                'message': woo_sync_message
            }
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Error updating product {product_id}: {str(e)}")
        return Response({
            'success': False,
            'message': f'Failed to update product: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.utils import timezone
import json

def sync_bundle_data_from_webhook(product, woo_product_id):
    """
    Sync bundle data from WooCommerce for a specific product.
    This function fetches fresh bundle data from WooCommerce and updates
    the ProductBundle table with current bundled_items and default_variation_attributes.
    
    Args:
        product: The Product instance
        woo_product_id: WooCommerce product ID
        
    Returns:
        bool: True if bundle data was successfully synced, False otherwise
    """
    try:
        import os
        from woocommerce import API
        
        # Initialize WooCommerce API
        from core.secrets import get_secret
        wcapi = API(
            url=get_secret('WOO_API_URL', 'https://store.doctorsstudio.com'),
            consumer_key=get_secret('WOO_CONSUMER_KEY', ''),
            consumer_secret=get_secret('WOO_CONSUMER_SECRET', ''),
            version="wc/v3",
            timeout=30
        )
        
        if not wcapi.consumer_key or not wcapi.consumer_secret:
            logger.error("WooCommerce API credentials not configured for bundle sync")
            return False
            
        logger.info(f"🔧 Fetching bundle data from WooCommerce for product {woo_product_id}")
        
        # Fetch product data from WooCommerce
        response = wcapi.get(f"products/{woo_product_id}")
        
        if response.status_code != 200:
            logger.error(f"Failed to fetch product {woo_product_id} from WooCommerce: {response.status_code}")
            return False
            
        woo_product_data = response.json()
        
        # Check if this is actually a bundle product
        if woo_product_data.get('type') != 'bundle':
            logger.info(f"Product {woo_product_id} is not a bundle type in WooCommerce")
            return False
            
        # Extract bundle-specific data
        bundled_items = woo_product_data.get('bundled_items', [])
        
        if not bundled_items:
            logger.warning(f"No bundled_items found for bundle product {woo_product_id}")
            # Still update with empty bundle data to keep it current
        
        logger.info(f"Found {len(bundled_items)} bundled items for product {woo_product_id}")
        
        # Process and validate bundle items
        processed_bundle_items = []
        default_series_by_item = {}
        
        for item in bundled_items:
            item_id = item.get('product_id')
            if not item_id:
                logger.warning(f"Bundle item missing product_id in bundle {woo_product_id}")
                continue
                
            # Check if bundled product exists in our database
            from .models import Product
            try:
                bundled_product = Product.objects.get(woo_product_id=item_id)
                item['local_product_id'] = str(bundled_product.id)
                
                # Extract and store default series selections
                default_attrs = item.get('default_variation_attributes', [])
                if default_attrs:
                    # Store default series selections for this item
                    default_series_by_item[str(item_id)] = default_attrs
                    logger.info(f"Default series for item {item_id}: {default_attrs}")
                    
                processed_bundle_items.append(item)
                
            except Product.DoesNotExist:
                logger.warning(f"Bundled product {item_id} not found in local database")
                # Still include the item but mark it as not found
                item['local_product_id'] = None
                item['not_found'] = True
                processed_bundle_items.append(item)
        
        # Add processed data to the WooCommerce data
        woo_product_data['processed_bundle_items'] = processed_bundle_items
        woo_product_data['default_series_by_item'] = default_series_by_item
        
        # Get or create ProductBundle record
        from .models import ProductBundle
        product_bundle, created = ProductBundle.objects.get_or_create(
            product_id=product.id,
            defaults={
                'woo_data': woo_product_data,
                'created_at': timezone.now(),
                'updated_at': timezone.now()
            }
        )
        
        if not created:
            # Update existing ProductBundle with fresh data
            product_bundle.woo_data = woo_product_data
            product_bundle.updated_at = timezone.now()
            product_bundle.save()
            logger.info(f"✅ Updated existing ProductBundle record for product {product.id}")
        else:
            logger.info(f"✅ Created new ProductBundle record for product {product.id}")
        
        # Log bundle data details for debugging
        if processed_bundle_items:
            logger.info(f"Bundle items processed and synced:")
            for item in processed_bundle_items[:3]:  # Log first 3 items to avoid spam
                item_id = item.get('product_id', 'Unknown')
                item_name = item.get('title', 'Unknown')
                local_id = item.get('local_product_id', 'Not found')
                default_attrs = item.get('default_variation_attributes', [])
                logger.info(f"  - {item_name} (WooID: {item_id}, LocalID: {local_id}) - Default attrs: {len(default_attrs)}")
        
        # Add a cache invalidation flag to indicate bundle data has changed
        # This can be used by the frontend to refresh bundle data
        product_bundle.woo_data['last_updated'] = timezone.now().isoformat()
        product_bundle.save(update_fields=['woo_data'])
        
        return True
        
    except Exception as e:
        logger.error(f"Error syncing bundle data for product {product.id}: {str(e)}")
        return False


def _sync_product_sku_webhook(product, webhook_data):
    """
    Sync product SKU from webhook data for both simple and variable products.
    Returns statistics about the sync operation.
    """
    from .models import ProductSimple, ProductVariation
    
    stats = {
        'updated': False,
        'simple_sku_updated': False,
        'variation_skus_updated': 0,
        'errors': []
    }
    
    try:
        woo_sku = webhook_data.get('sku', '') or ''
        product_type = product.product_type
        
        # Update ProductSimple SKU for both simple and variable products
        # This ensures the parent SKU is always in sync
        try:
            product_simple, created = ProductSimple.objects.get_or_create(
                product=product,
                defaults={'sku': woo_sku}
            )
            
            if not created and product_simple.sku != woo_sku:
                old_sku = product_simple.sku
                product_simple.sku = woo_sku
                product_simple.save()
                logger.info(f"Updated ProductSimple SKU via webhook: '{old_sku}' -> '{woo_sku}'")
                stats['simple_sku_updated'] = True
                stats['updated'] = True
            elif created:
                logger.info(f"Created ProductSimple with SKU via webhook: '{woo_sku}'")
                stats['simple_sku_updated'] = True
                stats['updated'] = True
                
        except Exception as e:
            error_msg = f"Error updating ProductSimple SKU: {str(e)}"
            logger.error(error_msg)
            stats['errors'].append(error_msg)
        
        # For variable products, also sync variation SKUs
        if product_type in ['variable', 'variable_subscription', 'variable-subscription']:
            try:
                # Initialize WooCommerce API
                from .woocommerce import WooCommerceAPI
                wc_api = WooCommerceAPI()
                
                # Fetch variations from WooCommerce
                woo_variations_response = wc_api.wcapi.get(f"products/{product.woo_product_id}/variations")
                
                if woo_variations_response.status_code == 200:
                    woo_variations = woo_variations_response.json()
                    
                    for woo_variation in woo_variations:
                        woo_variation_id = woo_variation.get('id')
                        woo_variation_sku = woo_variation.get('sku', '') or ''
                        
                        try:
                            # Find local variation
                            local_variation = ProductVariation.objects.filter(
                                product=product,
                                woo_variation_id=woo_variation_id
                            ).first()
                            
                            if local_variation and local_variation.sku != woo_variation_sku:
                                old_variation_sku = local_variation.sku
                                local_variation.sku = woo_variation_sku
                                local_variation.save()
                                logger.info(f"Updated variation SKU via webhook: '{old_variation_sku}' -> '{woo_variation_sku}' (Variation ID: {woo_variation_id})")
                                stats['variation_skus_updated'] += 1
                                stats['updated'] = True
                                
                        except Exception as e:
                            error_msg = f"Error updating variation {woo_variation_id} SKU: {str(e)}"
                            logger.error(error_msg)
                            stats['errors'].append(error_msg)
                            
                else:
                    error_msg = f"Failed to fetch variations from WooCommerce: {woo_variations_response.status_code}"
                    logger.error(error_msg)
                    stats['errors'].append(error_msg)
                    
            except Exception as e:
                error_msg = f"Error syncing variable product SKUs: {str(e)}"
                logger.error(error_msg)
                stats['errors'].append(error_msg)
        
        # For bundle products, also sync bundle-specific SKU data
        elif product_type == 'bundle':
            try:
                from .models import ProductBundle
                
                product_bundle = ProductBundle.objects.filter(product=product).first()
                if product_bundle:
                    if not product_bundle.woo_data:
                        product_bundle.woo_data = {}
                    
                    old_bundle_sku = product_bundle.woo_data.get('sku', '')
                    if old_bundle_sku != woo_sku:
                        product_bundle.woo_data['sku'] = woo_sku
                        product_bundle.save()
                        logger.info(f"Updated bundle SKU in woo_data via webhook: '{old_bundle_sku}' -> '{woo_sku}'")
                        stats['updated'] = True
                        
            except Exception as e:
                error_msg = f"Error syncing bundle product SKU: {str(e)}"
                logger.error(error_msg)
                stats['errors'].append(error_msg)
        
        # For subscription products, also sync subscription-specific SKU data  
        elif product_type == 'subscription':
            try:
                from .models import ProductSubscription
                
                product_subscription = ProductSubscription.objects.filter(product=product).first()
                if product_subscription and hasattr(product_subscription, 'woo_data'):
                    if not product_subscription.woo_data:
                        product_subscription.woo_data = {}
                    
                    old_sub_sku = product_subscription.woo_data.get('sku', '')
                    if old_sub_sku != woo_sku:
                        product_subscription.woo_data['sku'] = woo_sku
                        product_subscription.save()
                        logger.info(f"Updated subscription SKU in woo_data via webhook: '{old_sub_sku}' -> '{woo_sku}'")
                        stats['updated'] = True
                        
            except Exception as e:
                error_msg = f"Error syncing subscription product SKU: {str(e)}"
                logger.error(error_msg)
                stats['errors'].append(error_msg)
    
    except Exception as e:
        error_msg = f"General error in SKU sync: {str(e)}"
        logger.error(error_msg)
        stats['errors'].append(error_msg)
    
    return stats


def detect_stale_webhook_data(webhook_data):
    """
    Detect if webhook data is stale/inconsistent based on common patterns.
    
    Returns:
        str: Description of stale data issue if detected, None otherwise
    """
    try:
        price = webhook_data.get('price')
        regular_price = webhook_data.get('regular_price') 
        sale_price = webhook_data.get('sale_price')
        on_sale = webhook_data.get('on_sale', False)
        
        # Convert to strings for comparison (WooCommerce inconsistency)
        price_str = str(price) if price is not None else ''
        regular_price_str = str(regular_price) if regular_price is not None else ''
        sale_price_str = str(sale_price) if sale_price is not None else ''
        
        # Pattern 1: on_sale=false but sale_price exists and differs from regular_price
        if (not on_sale and 
            sale_price_str != '' and 
            sale_price_str != '0' and 
            sale_price_str != regular_price_str):
            return f"on_sale=false but sale_price={sale_price_str} exists (should be empty)"
        
        # Pattern 2: sale_price exists but equals regular_price (redundant sale)
        if (sale_price_str != '' and 
            sale_price_str != '0' and 
            sale_price_str == regular_price_str):
            return f"sale_price={sale_price_str} equals regular_price (redundant sale)"
        
        # Pattern 3: price doesn't match expected value based on sale logic
        expected_price = sale_price_str if (on_sale and sale_price_str not in ['', '0']) else regular_price_str
        if price_str != expected_price and price_str != '':
            return f"price={price_str} doesn't match expected={expected_price} (on_sale={on_sale})"
        
        return None  # No stale data detected
        
    except Exception as e:
        logger.error(f"Error detecting stale webhook data: {str(e)}")
        return None  # If we can't determine, proceed with update


def detect_stale_variation_data(variation_data, parent_webhook_time=None):
    """
    Detect if variation data from API is stale compared to recent webhook.
    
    Args:
        variation_data: List of variation data from WooCommerce API
        parent_webhook_time: When the parent product webhook was received
    
    Returns:
        str: Description of stale data issue if detected, None otherwise
    """
    try:
        if not variation_data or not parent_webhook_time:
            return None
            
        from datetime import datetime, timezone

        def _parse_datetime(value):
            if not value:
                return None
            if isinstance(value, datetime):
                dt = value
            else:
                s = str(value).strip()
                try:
                    # WooCommerce commonly returns ISO8601 with 'Z'
                    if s.endswith('Z'):
                        s = s[:-1] + '+00:00'
                    dt = datetime.fromisoformat(s)
                except ValueError:
                    # Fallbacks for common non-ISO formats
                    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                        try:
                            dt = datetime.strptime(s, fmt)
                            break
                        except ValueError:
                            dt = None
                    if dt is None:
                        return None

            # Normalize tzinfo to avoid naive/aware comparison errors
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        
        # Check if any variation was modified after the webhook
        for variation in variation_data:
            var_modified = variation.get('date_modified_gmt')
            if var_modified:
                var_time = _parse_datetime(var_modified)
                webhook_time = _parse_datetime(parent_webhook_time)

                if not var_time or not webhook_time:
                    continue
                
                # If variation was modified more recently than webhook, API data might be stale
                if var_time > webhook_time:
                    logger.warning(f" Variation {variation['id']} modified at {var_time} (after webhook at {webhook_time})")
                    return f"Variation {variation['id']} has newer modification time than parent webhook"
        
        return None
        
    except Exception as e:
        logger.error(f"Error detecting stale variation data: {str(e)}")
        return None


def sync_purchase_price_from_webhook(product, webhook_data, updated_fields):
    """
    Sync purchase_price (cost price) from WooCommerce wp_atum_product_data table.
    Uses direct Cloud SQL query since ATUM doesn't expose purchase_price
    through the WooCommerce REST API meta_data.
    Also syncs purchase_price for all variations of variable products.
    """
    from decimal import Decimal, InvalidOperation
    import pymysql
    from core.secrets import get_woo_db_config
    
    def safe_decimal(value):
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    
    conn = None
    try:
        woo_cfg = get_woo_db_config()
        
        conn = pymysql.connect(
            host=woo_cfg['db_host'],
            port=int(woo_cfg['db_port']),
            user=woo_cfg['db_username'],
            password=woo_cfg['db_password'],
            database=woo_cfg['db_name'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=15,
        )
        
        cursor = conn.cursor()
        
        # Sync parent product purchase_price
        cursor.execute(
            "SELECT purchase_price FROM wp_atum_product_data WHERE product_id = %s",
            (product.woo_product_id,)
        )
        row = cursor.fetchone()
        
        purchase_price = safe_decimal(row['purchase_price']) if row else None
        parent_updated = False
        
        if purchase_price is not None and product.purchase_price != purchase_price:
            old_value = product.purchase_price
            product.purchase_price = purchase_price
            updated_fields.append('purchase_price')
            logger.info(f"[PRODUCT_WEBHOOK] Updated purchase_price for product {product.woo_product_id}: {old_value} → {purchase_price} (source: wp_atum_product_data)")
            parent_updated = True
        elif purchase_price is not None:
            logger.info(f"[PRODUCT_WEBHOOK] purchase_price unchanged for product {product.woo_product_id}: {purchase_price}")
        else:
            logger.info(f"[PRODUCT_WEBHOOK] No purchase_price in wp_atum_product_data for product {product.woo_product_id}")
        
        # Sync variation purchase_prices for variable products
        if product.product_type in ('variable', 'variable_subscription', 'variable-subscription'):
            from crm.models import ProductVariation
            variations = list(ProductVariation.objects.filter(product=product))
            if variations:
                var_woo_ids = [v.woo_variation_id for v in variations]
                placeholders = ','.join(['%s'] * len(var_woo_ids))
                cursor.execute(
                    f"SELECT product_id, purchase_price FROM wp_atum_product_data WHERE product_id IN ({placeholders})",
                    var_woo_ids
                )
                var_atum_prices = {row['product_id']: row['purchase_price'] for row in cursor.fetchall()}
                
                for variation in variations:
                    var_price = safe_decimal(var_atum_prices.get(variation.woo_variation_id))
                    if var_price is not None and variation.purchase_price != var_price:
                        old_val = variation.purchase_price
                        variation.purchase_price = var_price
                        variation.save(update_fields=['purchase_price', 'updated_at'])
                        logger.info(f"[PRODUCT_WEBHOOK] Updated variation {variation.woo_variation_id} purchase_price: {old_val} → {var_price}")
        
        return parent_updated
        
    except Exception as e:
        logger.error(f"[PRODUCT_WEBHOOK] Error syncing purchase_price for product {product.woo_product_id}: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()


def update_product_prices(product, webhook_data, updated_fields):
    """
    Update product prices from webhook data with comprehensive handling.
    Uses the same robust logic as the sync_product_prices management command.
    """
    from decimal import Decimal, InvalidOperation
    from .woocommerce import WooCommerceAPI
    
    def safe_decimal(value):
        """Safely convert value to Decimal - same logic as sync script"""
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    
    # CRITICAL: Check if webhook data itself is stale/inconsistent
    webhook_stale_detected = detect_stale_webhook_data(webhook_data)
    if webhook_stale_detected:
        logger.warning(f"🚨 STALE WEBHOOK DATA detected for product {product.woo_product_id}: {webhook_stale_detected}")
        logger.warning(f"   Skipping pricing update due to inconsistent webhook data")
        return updated_fields
    
    try:
        # Get fresh product data from WooCommerce with cache-busting
        # This ensures we have the most up-to-date pricing data
        wc_api = WooCommerceAPI()
        
        # Add cache-busting parameter to force fresh data
        import time
        cache_bust = int(time.time())
        wc_response = wc_api.get_product(product.woo_product_id, params={'_cb': cache_bust})
        
        if not wc_response or wc_response.get('status') == 'error' or not wc_response.get('data'):
            logger.warning(f"Could not fetch fresh product data for webhook update: {product.woo_product_id}")
            # Fallback to webhook data if API fails
            return update_prices_from_webhook_data_only(product, webhook_data, updated_fields, safe_decimal)
        
        # Extract fresh WooCommerce data
        wc_product = wc_response['data']
        
        # Use fresh WooCommerce data instead of potentially stale webhook data
        wc_price = safe_decimal(wc_product.get('price'))
        wc_regular_price = safe_decimal(wc_product.get('regular_price'))
        wc_sale_price = safe_decimal(wc_product.get('sale_price'))
        
        # Debug logging for price comparison
        logger.info(f"📊 Price comparison for product {product.woo_product_id}:")
        logger.info(f"   Current in DB: price={product.price}, regular={product.regular_price}, sale={product.sale_price}")
        logger.info(f"   Fresh from WC: price={wc_price}, regular={wc_regular_price}, sale={wc_sale_price}")
        logger.info(f"   Webhook data: price={webhook_data.get('price')}, regular={webhook_data.get('regular_price')}, sale='{webhook_data.get('sale_price')}'")
        
        price_updated = False
        changes = []
        
        # Update regular_price
        if product.regular_price != wc_regular_price:
            changes.append(f'regular_price: {product.regular_price} → {wc_regular_price}')
            product.regular_price = wc_regular_price
            updated_fields.append('regular_price')
            price_updated = True
        
        # Update sale_price  
        if product.sale_price != wc_sale_price:
            changes.append(f'sale_price: {product.sale_price} → {wc_sale_price}')
            product.sale_price = wc_sale_price
            updated_fields.append('sale_price')
            price_updated = True
        
        # Update main price
        if product.price != wc_price:
            changes.append(f'price: {product.price} → {wc_price}')
            product.price = wc_price
            updated_fields.append('price')
            price_updated = True
        
        # If API returned same data as DB (stale cache), use webhook data instead
        webhook_price_str = str(webhook_data.get('price', ''))
        webhook_regular_str = str(webhook_data.get('regular_price', ''))
        webhook_sale_str = str(webhook_data.get('sale_price', ''))
        
        db_price_str = str(product.price) if product.price is not None else ''
        db_regular_str = str(product.regular_price) if product.regular_price is not None else ''
        db_sale_str = str(product.sale_price) if product.sale_price is not None else ''
        
        if not changes and (
            webhook_price_str != db_price_str or
            webhook_regular_str != db_regular_str or
            webhook_sale_str != db_sale_str
        ):
            logger.warning(f"API returned stale data - falling back to webhook data for product {product.woo_product_id}")
            return update_prices_from_webhook_data_only(product, webhook_data, updated_fields, safe_decimal)
        
        if changes:
            logger.info(f"Webhook price updates for product {product.woo_product_id}: {', '.join(changes)}")
        else:
            logger.info(f"No price changes detected for product {product.woo_product_id}")
        
        # Update variations if this is a variable product
        if price_updated and product.product_type in ['variable', 'variable_subscription', 'variable-subscription']:
            try:
                update_variation_prices_from_webhook_fresh(wc_api, product)
            except Exception as e:
                logger.error(f"Error updating variation prices via webhook for product {product.woo_product_id}: {str(e)}")
        
        # Update subscription prices if applicable
        if price_updated and product.product_type in ['subscription', 'variable_subscription', 'variable-subscription']:
            try:
                update_subscription_prices_from_webhook(product, wc_product, updated_fields)
            except Exception as e:
                logger.error(f"Error updating subscription prices via webhook for product {product.woo_product_id}: {str(e)}")
        
        return price_updated
        
    except Exception as e:
        logger.error(f"Error in webhook price update for product {product.woo_product_id}: {str(e)}")
        # Fallback to basic webhook data processing
        logger.info(f"Using webhook data fallback for product {product.woo_product_id}")
        return update_prices_from_webhook_data_only(product, webhook_data, updated_fields, safe_decimal)


def update_prices_from_webhook_data_only(product, webhook_data, updated_fields, safe_decimal):
    """Fallback method using only webhook data when API calls fail"""
    # CRITICAL: Even in fallback, check for stale webhook data!
    webhook_stale_detected = detect_stale_webhook_data(webhook_data)
    if webhook_stale_detected:
        logger.warning(f"🚨 STALE WEBHOOK DATA detected in FALLBACK for product {product.woo_product_id}: {webhook_stale_detected}")
        logger.warning(f"   Skipping fallback pricing update due to inconsistent webhook data")
        return updated_fields  # Return without updating prices
    
    price_updated = False
    
    # Handle regular_price from webhook data
    if 'regular_price' in webhook_data:
        new_regular_price = safe_decimal(webhook_data['regular_price'])
        if product.regular_price != new_regular_price:
            product.regular_price = new_regular_price
            updated_fields.append('regular_price')
            price_updated = True
            logger.info(f"Fallback: Updated regular_price via webhook: {product.regular_price} -> {new_regular_price}")
    
    # Handle sale_price from webhook data
    if 'sale_price' in webhook_data:
        new_sale_price = safe_decimal(webhook_data['sale_price'])
        if product.sale_price != new_sale_price:
            product.sale_price = new_sale_price
            updated_fields.append('sale_price')
            price_updated = True
            logger.info(f"Fallback: Updated sale_price via webhook: {product.sale_price} -> {new_sale_price}")
    
    # Handle main price from webhook data
    if 'price' in webhook_data:
        new_price = safe_decimal(webhook_data['price'])
        if product.price != new_price:
            product.price = new_price
            updated_fields.append('price')
            price_updated = True
            logger.info(f"Fallback: Updated price via webhook: {product.price} -> {new_price}")
    
    return price_updated


def update_variation_prices_from_webhook_fresh(wc_api, product):
    """Update variation prices using fresh WooCommerce data - enhanced version"""
    from decimal import Decimal, InvalidOperation
    
    def safe_decimal(value):
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    
    try:
        # Get fresh variation data from WooCommerce with cache-busting
        variations = wc_api.get_product_variations(product.woo_product_id)
        
        if not variations:
            return
        
        for wc_variation in variations:
            variation_id = wc_variation.get('id')
            
            try:
                local_variation = ProductVariation.objects.get(
                    product=product,
                    woo_variation_id=variation_id
                )
                
                # Extract fresh variation prices
                new_price = safe_decimal(wc_variation.get('price'))
                new_regular_price = safe_decimal(wc_variation.get('regular_price'))
                new_sale_price = safe_decimal(wc_variation.get('sale_price'))
                
                updated = False
                changes = []
                
                # Check for price changes
                if local_variation.price != new_price:
                    changes.append(f'price: {local_variation.price} → {new_price}')
                    local_variation.price = new_price
                    updated = True
                
                if local_variation.regular_price != new_regular_price:
                    changes.append(f'regular_price: {local_variation.regular_price} → {new_regular_price}')
                    local_variation.regular_price = new_regular_price
                    updated = True
                
                if local_variation.sale_price != new_sale_price:
                    changes.append(f'sale_price: {local_variation.sale_price} → {new_sale_price}')
                    local_variation.sale_price = new_sale_price
                    updated = True
                
                if updated:
                    local_variation.save(update_fields=['price', 'regular_price', 'sale_price', 'updated_at'])
                    logger.info(f"Webhook updated variation {variation_id} prices: {', '.join(changes)}")
                    
            except ProductVariation.DoesNotExist:
                logger.warning(f"Variation {variation_id} not found in database for product {product.woo_product_id}")
                continue
                
    except Exception as e:
        logger.error(f"Error updating variation prices from webhook: {str(e)}")


def update_subscription_prices_from_webhook(product, wc_product, updated_fields):
    """Update subscription prices from fresh WooCommerce data"""
    from decimal import Decimal, InvalidOperation
    
    def safe_decimal(value):
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    
    try:
        from .models import ProductSubscription
        subscription = ProductSubscription.objects.get(product=product)
        
        # Extract subscription price data from fresh WooCommerce data
        wc_subscription_price = safe_decimal(wc_product.get('subscription_price', wc_product.get('price')))
        wc_sign_up_fee = safe_decimal(wc_product.get('subscription_sign_up_fee'))
        
        updated = False
        changes = []
        
        # Check for subscription price changes
        if subscription.price != wc_subscription_price:
            changes.append(f'subscription_price: {subscription.price} → {wc_subscription_price}')
            subscription.price = wc_subscription_price
            updated = True
        
        if subscription.sign_up_fee != wc_sign_up_fee:
            changes.append(f'sign_up_fee: {subscription.sign_up_fee} → {wc_sign_up_fee}')
            subscription.sign_up_fee = wc_sign_up_fee
            updated = True
        
        if updated:
            subscription.save(update_fields=['price', 'sign_up_fee', 'updated_at'])
            logger.info(f"Webhook updated subscription prices for product {product.woo_product_id}: {', '.join(changes)}")
            
    except ProductSubscription.DoesNotExist:
        logger.debug(f"No subscription data found for product {product.woo_product_id}")
    except Exception as e:
        logger.error(f"Error updating subscription prices from webhook: {str(e)}")


def _get_brand_sync_message(brand_sync_result):
    """
    Generate a descriptive message for brand sync results.
    
    Args:
        brand_sync_result: Dictionary containing brand sync results
        
    Returns:
        str: Human-readable message describing the brand sync outcome
    """
    if not brand_sync_result:
        return "Brand sync not attempted"
    
    if brand_sync_result.get('error'):
        return f"Brand sync failed: {brand_sync_result['error']}"
    
    if not brand_sync_result.get('mismatch_detected', False):
        local_brand = brand_sync_result.get('local_brand')
        if local_brand:
            return f"Brand is in sync: '{local_brand}'"
        else:
            return "No brand information found"
    
    # Mismatch was detected
    local_brand = brand_sync_result.get('local_brand', 'Unknown')
    wc_brand = brand_sync_result.get('wc_brand', 'Unknown')
    
    if brand_sync_result.get('sync_successful', False):
        return f"Brand mismatch corrected: '{local_brand}' → '{wc_brand}'"
    else:
        return f"Brand mismatch detected but correction failed: '{local_brand}' vs '{wc_brand}'"


def _detect_and_fix_brand_mismatch(product, webhook_data):
    """
    Proactively detect and fix brand mismatches between local database and WooCommerce.
    
    This function:
    1. Fetches current brand from local database
    2. Fetches current brand from WooCommerce API (fresh data)
    3. Compares brands and fixes mismatches
    4. Returns detailed sync results
    
    Args:
        product: The Product instance
        webhook_data: Webhook data (used for logging context)
    
    Returns:
        dict: Sync results with mismatch detection and correction status
    """
    from .models import ProductSimple, ProductBundle, ProductVariation
    from .woocommerce import WooCommerceAPI
    
    sync_result = {
        'mismatch_detected': False,
        'local_brand': None,
        'wc_brand': None,
        'sync_successful': False,
        'error': None
    }
    
    try:
        logger.info(f"🔍 Checking for brand mismatch on product {product.id} (WooID: {product.woo_product_id})")
        
        # Helper function to extract brand from woo_data
        def get_brand_from_woo_data(woo_data):
            if not woo_data or 'attributes' not in woo_data:
                return None
            
            attributes = woo_data['attributes']
            if not isinstance(attributes, list):
                return None
            
            for attr in attributes:
                if isinstance(attr, dict) and attr.get('name', '').lower() == 'brand':
                    options = attr.get('options', [])
                    if options and len(options) > 0:
                        return options[0]
            return None
        
        # Get current local brand
        local_brand = None
        simple_product = ProductSimple.objects.filter(product=product).first()
        if simple_product:
            local_brand = get_brand_from_woo_data(simple_product.woo_data)
        
        # If no simple product, check bundle
        if not local_brand:
            bundle_product = ProductBundle.objects.filter(product=product).first()
            if bundle_product:
                local_brand = get_brand_from_woo_data(bundle_product.woo_data)
        
        # If no bundle, check variable product variations
        if not local_brand and product.product_type in ['variable', 'variable_subscription', 'variable-subscription']:
            variations = ProductVariation.objects.filter(product=product)
            if variations.exists():
                first_var = variations.first()
                if first_var.woo_data and 'parent_attributes' in first_var.woo_data:
                    local_brand = get_brand_from_woo_data({'attributes': first_var.woo_data['parent_attributes']})
        
        sync_result['local_brand'] = local_brand
        
        # Get current WooCommerce brand (fresh data)
        wc_api = WooCommerceAPI()
        wc_response = wc_api.get_product(product.woo_product_id)
        
        if not wc_response or wc_response.get('status') != 'success':
            sync_result['error'] = f"Failed to fetch product from WooCommerce: {wc_response}"
            logger.warning(f"Could not fetch WooCommerce data for brand check: {product.woo_product_id}")
            return sync_result
        
        wc_product = wc_response['data']
        wc_attributes = wc_product.get('attributes', [])
        
        wc_brand = None
        for attr in wc_attributes:
            if attr.get('name', '').lower() == 'brand':
                options = attr.get('options', [])
                if options:
                    wc_brand = options[0]
                    break
        
        sync_result['wc_brand'] = wc_brand
        
        # Compare brands
        if local_brand != wc_brand:
            sync_result['mismatch_detected'] = True
            logger.warning(f"🚨 Brand mismatch detected for product {product.id}:")
            logger.warning(f"   Local: '{local_brand}' vs WooCommerce: '{wc_brand}'")
            
            # Fix the mismatch using existing sync function
            try:
                sync_success = sync_product_attributes_from_webhook(product, wc_attributes)
                if sync_success:
                    sync_result['sync_successful'] = True
                    logger.info(f"✅ Brand mismatch corrected: '{local_brand}' → '{wc_brand}'")
                else:
                    sync_result['error'] = "Attribute sync function returned False"
                    logger.error(f"❌ Failed to correct brand mismatch for product {product.id}")
            except Exception as sync_error:
                sync_result['error'] = str(sync_error)
                logger.error(f"❌ Error correcting brand mismatch: {str(sync_error)}")
        else:
            logger.info(f"✅ Brand is in sync for product {product.id}: '{local_brand}'")
        
    except Exception as e:
        sync_result['error'] = str(e)
        logger.error(f"Error during brand mismatch detection for product {product.id}: {str(e)}")
    
    return sync_result


def analyze_webhook_updates(updated_fields, webhook_data, product):
    """
    Analyze and categorize webhook updates to provide detailed information
    about what was changed during the webhook processing.
    
    Args:
        updated_fields: List of field names that were updated
        webhook_data: The webhook data received from WooCommerce
        product: The Product instance that was updated
        
    Returns:
        dict: Categorized update information
    """
    update_analysis = {
        'basic_info': [],
        'pricing': [],
        'inventory': [],
        'categories': [],
        'images': [],
        'attributes': [],
        'variations': [],
        'bundle_data': [],
        'locations': [],
        'other': []
    }
    
    for field in updated_fields:
        if field in ['name', 'description', 'status', 'slug']:
            update_analysis['basic_info'].append({
                'field': field,
                'description': f"Product {field} updated",
                'new_value': webhook_data.get(field, 'N/A')
            })
        elif field in ['price', 'regular_price', 'sale_price']:
            update_analysis['pricing'].append({
                'field': field,
                'description': f"Product {field} updated",
                'new_value': webhook_data.get(field, 'N/A')
            })
        elif field in ['stock_quantity', 'stock_status', 'manage_stock']:
            update_analysis['inventory'].append({
                'field': field,
                'description': f"Inventory {field} updated",
                'new_value': webhook_data.get(field, 'N/A')
            })
        elif field == 'categories':
            categories = webhook_data.get('categories', [])
            category_names = [cat.get('name', 'Unknown') for cat in categories if isinstance(cat, dict)]
            update_analysis['categories'].append({
                'field': field,
                'description': f"Product categories updated",
                'new_value': category_names
            })
        elif field == 'images':
            images = webhook_data.get('images', [])
            image_count = len(images) if isinstance(images, list) else 0
            update_analysis['images'].append({
                'field': field,
                'description': f"Product images updated ({image_count} images)",
                'new_value': f"{image_count} images"
            })
        elif field == 'attributes':
            attributes = webhook_data.get('attributes', [])
            attr_names = [attr.get('name', 'Unknown') for attr in attributes if isinstance(attr, dict)]
            update_analysis['attributes'].append({
                'field': field,
                'description': f"Product attributes updated (including brand, vendor, etc.)",
                'new_value': attr_names
            })
        elif field == 'brand_corrected':
            update_analysis['attributes'].append({
                'field': field,
                'description': "Product brand mismatch detected and corrected",
                'new_value': "Brand synchronized"
            })
        elif field in ['variations_synced', 'variations_updated', 'variations_created']:
            update_analysis['variations'].append({
                'field': field,
                'description': f"Product variations {field.replace('_', ' ')}",
                'new_value': "Variation data synchronized"
            })
        elif field in ['bundle_items', 'bundle_sync', 'default_variation_attributes']:
            update_analysis['bundle_data'].append({
                'field': field,
                'description': f"Bundle {field.replace('_', ' ')} updated",
                'new_value': "Bundle data synchronized"
            })
        elif field in ['atum_locations', 'inventory_locations']:
            update_analysis['locations'].append({
                'field': field,
                'description': f"Inventory locations updated",
                'new_value': "Location data synchronized"
            })
        else:
            update_analysis['other'].append({
                'field': field,
                'description': f"Field '{field}' updated",
                'new_value': webhook_data.get(field, 'N/A')
            })
    
    return update_analysis


def sync_product_attributes_from_webhook(product, attributes_data):
    """
    Sync product attributes (including brand) from WooCommerce webhook data.
    Updates the woo_data field in ProductSimple, ProductVariation, or ProductBundle tables.
    
    Args:
        product: The Product instance
        attributes_data: List of attribute dictionaries from WooCommerce
    """
    logger.info(f"Syncing attributes for product {product.id}: {attributes_data}")
    
    try:
        from .models import ProductSimple, ProductVariation, ProductBundle
        
        # Try to update ProductSimple first (most common)
        simple_product = ProductSimple.objects.filter(product=product).first()
        if simple_product:
            # Update woo_data with new attributes
            woo_data = simple_product.woo_data or {}
            woo_data['attributes'] = attributes_data
            simple_product.woo_data = woo_data
            simple_product.save(update_fields=['woo_data', 'updated_at'])
            logger.info(f"Updated ProductSimple attributes for product {product.id}")
            return True
        
        # Try ProductBundle if not simple
        bundle_product = ProductBundle.objects.filter(product=product).first()
        if bundle_product:
            # Update woo_data with new attributes
            woo_data = bundle_product.woo_data or {}
            woo_data['attributes'] = attributes_data
            bundle_product.woo_data = woo_data
            bundle_product.save(update_fields=['woo_data', 'updated_at'])
            logger.info(f"Updated ProductBundle attributes for product {product.id}")
            return True
        
        # For variable products, update the parent product's attributes
        # Individual variations will be synced separately
        if product.product_type in ['variable', 'variable_subscription', 'variable-subscription']:
            # Check if we have any variations to update
            variations = ProductVariation.objects.filter(product=product)
            if variations.exists():
                # Update the first variation's parent attributes
                first_variation = variations.first()
                woo_data = first_variation.woo_data or {}
                woo_data['parent_attributes'] = attributes_data
                first_variation.woo_data = woo_data
                first_variation.save(update_fields=['woo_data', 'updated_at'])
                logger.info(f"Updated ProductVariation parent attributes for product {product.id}")
                return True
        
        logger.warning(f"No product type table found to update attributes for product {product.id}")
        return False
        
    except Exception as e:
        logger.error(f"Error syncing product attributes for product {product.id}: {str(e)}")
        return False


@csrf_exempt
def woocommerce_product_webhook(request):
    """
    Webhook endpoint for WooCommerce product updates.
    
    This endpoint receives notifications from WooCommerce when products are
    created, updated, or deleted, and syncs the changes to the local database.
    
    Expected webhook events:
    - product.created
    - product.updated  
    - product.deleted
    
    Returns:
        JSON response with sync status
    """
    import time
    start_time = time.time()
    webhook_log = None
    
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Only POST method allowed'
        }, status=405)
    
    try:
        import os
        from .models import WebhookLog
        
        # Create initial webhook log entry
        webhook_log = WebhookLog.objects.create(
            webhook_type='product',
            source_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            content_type=request.META.get('CONTENT_TYPE', ''),
            request_body=request.body.decode('utf-8', errors='ignore') if request.body else ''
        )
        
        logger.info(f"[PRODUCT_WEBHOOK] 🔗 Received WooCommerce webhook request (Log ID: {webhook_log.id})")
        logger.info(f"[PRODUCT_WEBHOOK] Content-Type: {request.META.get('CONTENT_TYPE', 'Not set')}")
        logger.info(f"[PRODUCT_WEBHOOK] User-Agent: {request.META.get('HTTP_USER_AGENT', 'Not set')}")
        logger.info(f"[PRODUCT_WEBHOOK] Request body length: {len(request.body) if request.body else 0}")
        
        webhook_log.mark_processing()
        
        # Log raw request body for debugging
        if request.body:
            logger.info(f"[PRODUCT_WEBHOOK] Raw request body: {request.body}")
            logger.info(f"[PRODUCT_WEBHOOK] Raw request body decoded: {request.body.decode('utf-8', errors='ignore')}")
        
        # Parse webhook data from request body
        webhook_data = None
        try:
            if request.body:
                body_str = request.body.decode('utf-8')
                if body_str.strip():  # Check if body is not just whitespace
                    # Check if this is JSON or form data
                    if body_str.startswith('{') and body_str.endswith('}'):
                        # This looks like JSON
                        webhook_data = json.loads(body_str)
                        logger.info(f"[PRODUCT_WEBHOOK] Successfully parsed JSON webhook data: {type(webhook_data)}")
                        logger.info(f"[PRODUCT_WEBHOOK] Webhook data keys: {list(webhook_data.keys()) if isinstance(webhook_data, dict) else 'Not a dict'}")
                        logger.info(f"[PRODUCT_WEBHOOK] Full webhook data: {webhook_data}")
                    else:
                        # This looks like form data (e.g., webhook_id=20)
                        logger.info(f"[PRODUCT_WEBHOOK] Received form data: {body_str}")
                        # Parse form data
                        from urllib.parse import parse_qs
                        parsed_data = parse_qs(body_str)
                        
                        # Check if this is a test webhook
                        if 'webhook_id' in parsed_data:
                            logger.info(f"[PRODUCT_WEBHOOK] This is a WooCommerce test webhook with ID: {parsed_data['webhook_id']}")
                            return JsonResponse({
                                'success': True,
                                'message': f'WooCommerce test webhook received successfully (ID: {parsed_data["webhook_id"][0]})'
                            }, status=200)
                        else:
                            # Try to extract product data from form data if available
                            webhook_data = {}
                            for key, value_list in parsed_data.items():
                                webhook_data[key] = value_list[0] if value_list else None
                            logger.info(f"[PRODUCT_WEBHOOK] Parsed form data into webhook_data: {webhook_data}")
                else:
                    logger.warning("[PRODUCT_WEBHOOK] Request body is empty or whitespace only")
                    # For WooCommerce test webhooks, return success even with empty body
                    return JsonResponse({
                        'success': True,
                        'message': 'Webhook test received - empty body is OK for test webhooks'
                    }, status=200)
            else:
                logger.warning("[PRODUCT_WEBHOOK] No request body received")
                # For WooCommerce test webhooks, return success even with no body
                return JsonResponse({
                    'success': True,
                    'message': 'Webhook test received - no body is OK for test webhooks'
                }, status=200)
        except json.JSONDecodeError as e:
            logger.error(f"[PRODUCT_WEBHOOK] Failed to parse JSON from request body: {e}")
            return JsonResponse({
                'success': False,
                'message': 'Invalid JSON data received'
            }, status=400)
        except Exception as e:
            logger.error(f"[PRODUCT_WEBHOOK] Error parsing webhook data: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Error parsing webhook data: {str(e)}'
            }, status=400)
        
        if not isinstance(webhook_data, dict):
            logger.error(f"[PRODUCT_WEBHOOK] Webhook data is not a dictionary: {type(webhook_data)}")
            return JsonResponse({
                'success': False,
                'message': f'Expected JSON object, got {type(webhook_data).__name__}'
            }, status=400)
        
        woo_product_id = webhook_data.get('id')
        if not woo_product_id:
            logger.warning(f"[PRODUCT_WEBHOOK] No product ID found in webhook data. Available keys: {list(webhook_data.keys())}")
            # This might be a test webhook from WooCommerce - return success
            if webhook_log:
                webhook_log.mark_success("Test webhook - no product ID")
            return JsonResponse({
                'success': True,
                'message': 'Webhook received but no product ID - this may be a test webhook',
                'available_keys': list(webhook_data.keys())
            }, status=200)
        
        # Update webhook log with product ID
        if webhook_log:
            webhook_log.woo_product_id = woo_product_id
            webhook_log.save(update_fields=['woo_product_id'])
        
        logger.info(f"[PRODUCT_WEBHOOK] Processing webhook for WooCommerce product ID: {woo_product_id}")
        
        # Check for product deletion event
        # WooCommerce sends deletion webhooks with minimal data:
        # 1. Only 'id' field present (permanent delete or moved to trash)
        # 2. status='trash' (soft delete)
        # 3. Has date_modified_gmt but no name (deleted product)
        is_minimal_payload = len(webhook_data) == 1 and 'id' in webhook_data
        is_trash_status = webhook_data.get('status') == 'trash'
        is_deleted_format = webhook_data.get('date_modified_gmt') and not webhook_data.get('name')
        
        if is_minimal_payload or is_trash_status or is_deleted_format:
            delete_reason = 'minimal payload (only ID)' if is_minimal_payload else f"status={webhook_data.get('status')}"
            logger.info(f"[PRODUCT_WEBHOOK] Product {woo_product_id} appears to be deleted ({delete_reason})")
            
            try:
                local_product = Product.objects.get(woo_product_id=woo_product_id)
                product_name = local_product.name
                
                # Remove related data first to prevent orphaned records
                from .models import (
                    ProductVariation, ProductBundle, ProductSubscription, 
                    ProductInventory, ProductSimple, ProductGrouped
                )
                
                # Count deletions for logging
                variations_deleted = ProductVariation.objects.filter(product=local_product).count()
                bundles_deleted = ProductBundle.objects.filter(product=local_product).count()
                subscriptions_deleted = ProductSubscription.objects.filter(product=local_product).count()
                inventory_deleted = ProductInventory.objects.filter(product=local_product).count()
                simple_deleted = ProductSimple.objects.filter(product=local_product).count()
                grouped_deleted = ProductGrouped.objects.filter(product=local_product).count()
                
                # Delete all related records
                ProductVariation.objects.filter(product=local_product).delete()
                ProductBundle.objects.filter(product=local_product).delete()
                ProductSubscription.objects.filter(product=local_product).delete()
                ProductInventory.objects.filter(product=local_product).delete()
                ProductSimple.objects.filter(product=local_product).delete()
                ProductGrouped.objects.filter(product=local_product).delete()
                
                logger.info(f"[PRODUCT_WEBHOOK] Deleted related records for product {product_name}: "
                           f"{variations_deleted} variations, {bundles_deleted} bundles, "
                           f"{subscriptions_deleted} subscriptions, {inventory_deleted} inventory, "
                           f"{simple_deleted} simple, {grouped_deleted} grouped")
                
                # Remove the product itself
                local_product.delete()
                
                logger.info(f"[PRODUCT_WEBHOOK] ✅ Successfully deleted product {product_name} (WooCommerce ID: {woo_product_id}) from local database")
                
                # Update webhook log with success
                if webhook_log:
                    webhook_log.mark_success(f"Product deleted: {product_name}")
                
                return JsonResponse({
                    'success': True,
                    'message': f'Product {woo_product_id} deleted successfully',
                    'action': 'product_deleted',
                    'product_name': product_name,
                    'deleted_records': {
                        'variations': variations_deleted,
                        'bundles': bundles_deleted,
                        'subscriptions': subscriptions_deleted,
                        'inventory': inventory_deleted,
                        'simple': simple_deleted,
                        'grouped': grouped_deleted
                    }
                }, status=200)
                
            except Product.DoesNotExist:
                logger.info(f"[PRODUCT_WEBHOOK] Product {woo_product_id} was already deleted or doesn't exist locally")
                
                # Update webhook log with info
                if webhook_log:
                    webhook_log.mark_success(f"Product {woo_product_id} already deleted")
                
                return JsonResponse({
                    'success': True,
                    'message': f'Product {woo_product_id} was already deleted or doesn\'t exist locally',
                    'action': 'product_already_deleted'
                }, status=200)
                
            except Exception as delete_error:
                logger.error(f"[PRODUCT_WEBHOOK] Error deleting product {woo_product_id}: {str(delete_error)}")
                
                # Update webhook log with error
                if webhook_log:
                    webhook_log.mark_error(f"Delete failed: {str(delete_error)}")
                
                return JsonResponse({
                    'success': False,
                    'message': f'Failed to delete product {woo_product_id}: {str(delete_error)}'
                }, status=500)

        # Check if product exists in local database for update/creation
        try:
            local_product = Product.objects.get(woo_product_id=woo_product_id)
            
            # Update local product with WooCommerce data
            updated_fields = []
            if 'name' in webhook_data and webhook_data['name']:
                local_product.name = webhook_data['name']
                updated_fields.append('name')
            if 'description' in webhook_data:
                local_product.description = webhook_data['description'] or ''
                updated_fields.append('description')
            # Enhanced price handling for WooCommerce webhook updates
            price_updated = update_product_prices(local_product, webhook_data, updated_fields)
            # Sync purchase_price from WooCommerce meta_data (_purchase_price from ATUM)
            purchase_price_synced = sync_purchase_price_from_webhook(local_product, webhook_data, updated_fields)
            if 'status' in webhook_data and webhook_data['status']:
                local_product.status = webhook_data['status']
                updated_fields.append('status')
            if 'stock_status' in webhook_data and webhook_data['stock_status']:
                local_product.stock_status = webhook_data['stock_status']
                updated_fields.append('stock_status')
            if 'stock_quantity' in webhook_data:
                # Handle empty or invalid stock quantity
                stock_quantity = webhook_data['stock_quantity']
                if stock_quantity is not None and str(stock_quantity).strip():
                    try:
                        local_product.stock_quantity = int(stock_quantity) if stock_quantity else None
                        updated_fields.append('stock_quantity')
                    except (ValueError, TypeError) as e:
                        logger.warning(f"[PRODUCT_WEBHOOK] Invalid stock_quantity value: {stock_quantity}, error: {e}")
                else:
                    local_product.stock_quantity = None
                    updated_fields.append('stock_quantity')
            
            # Update categories if provided - but avoid overriding recent manual changes
            if 'categories' in webhook_data and isinstance(webhook_data['categories'], list):
                categories = [cat.get('name', '') for cat in webhook_data['categories'] if isinstance(cat, dict)]
                
                # Check if product was recently updated (within last 2 minutes) to avoid webhook race condition
                from django.utils import timezone
                from datetime import timedelta
                
                recent_update_threshold = timezone.now() - timedelta(minutes=2)
                is_recently_updated = local_product.updated_at and local_product.updated_at > recent_update_threshold
                
                # Only update categories if not recently updated, or if the webhook categories are different from "Uncategorized"
                should_update_categories = True
                if is_recently_updated:
                    # If recently updated and webhook is trying to set to "Uncategorized", skip it
                    if len(categories) == 1 and categories[0] == 'Uncategorized' and local_product.categories != ['Uncategorized']:
                        logger.info(f"[PRODUCT_WEBHOOK] Skipping webhook category update to 'Uncategorized' for recently updated product {local_product.id}")
                        should_update_categories = False
                
                if should_update_categories:
                    local_product.categories = categories
                    updated_fields.append('categories')
                    logger.info(f"[PRODUCT_WEBHOOK] Updated categories via webhook: {categories}")
                else:
                    logger.info(f"[PRODUCT_WEBHOOK] Preserved existing categories: {local_product.categories}")
            
            # Update images if provided
            if 'images' in webhook_data and isinstance(webhook_data['images'], list):
                images = [img.get('src', '') for img in webhook_data['images'] if isinstance(img, dict)]
                local_product.images = images
                updated_fields.append('images')
            
            # Update product attributes (including brand) if provided
            if 'attributes' in webhook_data and isinstance(webhook_data['attributes'], list):
                try:
                    sync_product_attributes_from_webhook(local_product, webhook_data['attributes'])
                    updated_fields.append('attributes')
                    logger.info(f"[PRODUCT_WEBHOOK] Updated product attributes via webhook for product {local_product.id}")
                except Exception as attr_error:
                    logger.error(f"[PRODUCT_WEBHOOK] Error syncing product attributes via webhook: {str(attr_error)}")
            
            # Enhanced Brand Mismatch Detection and Correction
            # This proactively checks for brand mismatches even when attributes aren't explicitly in webhook data
            brand_sync_result = None
            try:
                brand_sync_result = _detect_and_fix_brand_mismatch(local_product, webhook_data)
                if brand_sync_result and brand_sync_result.get('mismatch_detected', False):
                    updated_fields.append('brand_corrected')
                    logger.info(f"[PRODUCT_WEBHOOK] Brand mismatch detected and corrected: {brand_sync_result}")
            except Exception as brand_error:
                logger.error(f"[PRODUCT_WEBHOOK] Error during brand mismatch detection: {str(brand_error)}")
                brand_sync_result = {
                    'mismatch_detected': False,
                    'error': str(brand_error)
                }
            
            # Update product type if it has changed
            if 'type' in webhook_data:
                woo_product_type = webhook_data['type']
                
                # Type mapping for consistency
                type_mapping = {
                    'simple': 'simple',
                    'variable': 'variable',
                    'bundle': 'bundle',
                    'subscription': 'subscription',
                    'variable-subscription': 'variable-subscription',
                    'grouped': 'grouped',
                    'external': 'external'
                }
                
                normalized_type = type_mapping.get(woo_product_type, woo_product_type)
                
                if local_product.product_type != normalized_type:
                    old_type = local_product.product_type
                    local_product.product_type = normalized_type
                    updated_fields.append('product_type')
                    logger.info(f"[PRODUCT_WEBHOOK] Updated product type via webhook: '{old_type}' -> '{normalized_type}'")
            
            # Enhanced SKU sync for both simple and variable products
            sku_sync_stats = None
            if 'sku' in webhook_data:
                try:
                    sku_sync_stats = _sync_product_sku_webhook(local_product, webhook_data)
                    if sku_sync_stats and sku_sync_stats.get('updated', False):
                        updated_fields.append('sku')
                        logger.info(f"[PRODUCT_WEBHOOK] SKU sync completed via webhook: {sku_sync_stats}")
                except Exception as sku_error:
                    logger.error(f"[PRODUCT_WEBHOOK] Error during SKU sync via webhook: {str(sku_error)}")
            
            # Log product data before saving
            logger.info(f"[PRODUCT_WEBHOOK] About to save product {local_product.id} with data:")
            logger.info(f"[PRODUCT_WEBHOOK]   Name: {local_product.name}")
            logger.info(f"[PRODUCT_WEBHOOK]   Price: {local_product.price}")
            logger.info(f"[PRODUCT_WEBHOOK]   Regular Price: {local_product.regular_price}")
            logger.info(f"[PRODUCT_WEBHOOK]   Sale Price: {local_product.sale_price}")
            logger.info(f"[PRODUCT_WEBHOOK]   Status: {local_product.status}")
            logger.info(f"[PRODUCT_WEBHOOK]   Stock Status: {local_product.stock_status}")
            logger.info(f"[PRODUCT_WEBHOOK]   Stock Quantity: {local_product.stock_quantity}")
            
            local_product.save()
            
            # Verify product still exists after save
            try:
                verification_product = Product.objects.get(id=local_product.id)
                logger.info(f"[PRODUCT_WEBHOOK] ✅ Product verification after save: {verification_product.name} (ID: {verification_product.id})")
            except Product.DoesNotExist:
                logger.error(f"[PRODUCT_WEBHOOK] ❌ Product {local_product.id} no longer exists after save!")
            
            # Comprehensive variation sync for variable products
            variation_sync_stats = None
            if local_product.product_type in ['variable', 'variable_subscription', 'variable-subscription']:
                try:
                    logger.info(f"[PRODUCT_WEBHOOK] 🔄 Running comprehensive variation sync for product {woo_product_id}")
                    variation_sync_stats = sync_product_variations_webhook(local_product)
                    
                    if variation_sync_stats:
                        total_changes = variation_sync_stats.get('updated', 0) + variation_sync_stats.get('created', 0) + variation_sync_stats.get('removed', 0)
                        if total_changes > 0:
                            updated_fields.append('variations_synced')
                            logger.info(f"[PRODUCT_WEBHOOK] ✅ Variation sync completed: {variation_sync_stats}")
                        else:
                            logger.info(f"[PRODUCT_WEBHOOK] ℹ️ No variation changes needed")
                    
                except Exception as variation_error:
                    logger.error(f"[PRODUCT_WEBHOOK] Error during comprehensive variation sync: {str(variation_error)}")

            # Handle bundle data sync for bundle products AND products that are part of bundles
            bundle_sync_status = None
            comprehensive_sync_status = None
            default_series_sync_status = None
            
            # Check if this product is a bundle OR if it's part of any bundles
            is_bundle_product = local_product.product_type == 'bundle'
            is_part_of_bundles = False
            affected_bundles = []
            
            if not is_bundle_product:
                # Check if this product is part of any bundles
                from .models import ProductBundle
                for bundle in ProductBundle.objects.all():
                    woo_data = bundle.woo_data
                    if isinstance(woo_data, dict):
                        bundled_items = woo_data.get('bundled_items', [])
                        for item in bundled_items:
                            if item.get('product_id') == woo_product_id:
                                is_part_of_bundles = True
                                affected_bundles.append(bundle.product)
                                break
            
            if is_bundle_product:
                logger.info(f"[PRODUCT_WEBHOOK] 🔧 Product {local_product.id} is a bundle - running comprehensive bundle sync")
            elif is_part_of_bundles:
                logger.info(f"[PRODUCT_WEBHOOK] 🔧 Product {local_product.id} is part of {len(affected_bundles)} bundle(s) - running comprehensive bundle sync")
                for bundle_product in affected_bundles:
                    logger.info(f"[PRODUCT_WEBHOOK]   📦 Affected bundle: {bundle_product.name} (ID: {bundle_product.id})")
            
            if is_bundle_product or is_part_of_bundles:
                
                # Run comprehensive bundle sync
                try:
                    from .bundle_sync_utils import run_comprehensive_bundle_sync, run_bundle_default_series_sync
                    comprehensive_sync_status = run_comprehensive_bundle_sync(woo_product_id)
                    default_series_sync_status = run_bundle_default_series_sync(woo_product_id)
                    
                    if comprehensive_sync_status and default_series_sync_status:
                        updated_fields.append('comprehensive_bundle_data')
                        logger.info(f"[PRODUCT_WEBHOOK] ✅ Comprehensive bundle sync completed for product {woo_product_id}")
                    else:
                        logger.warning(f"[PRODUCT_WEBHOOK] ⚠️ Some bundle sync operations failed for product {woo_product_id}")
                        
                except ImportError:
                    # Fallback to original webhook sync if utils not available
                    logger.info(f"[PRODUCT_WEBHOOK] 🔧 Bundle sync utils not available - using fallback webhook sync")
                    bundle_sync_status = sync_bundle_data_from_webhook(local_product, woo_product_id)
                    if bundle_sync_status:
                        updated_fields.append('bundle_data')
                except Exception as sync_error:
                    logger.error(f"[PRODUCT_WEBHOOK] Error in comprehensive bundle sync: {str(sync_error)}")
                    # Fallback to original webhook sync
                    bundle_sync_status = sync_bundle_data_from_webhook(local_product, woo_product_id)
                    if bundle_sync_status:
                        updated_fields.append('bundle_data')

            # Subscription Data Sync - Sync subscription metadata for simple and variable products
            subscription_sync_status = None
            subscription_changes_detected = False
            subscription_sync_attempted = False
            
            try:
                # Check if this webhook contains subscription-related changes
                subscription_changes_detected = has_subscription_data_changes(webhook_data)
                
                if subscription_changes_detected:
                    logger.info(f"[PRODUCT_WEBHOOK] 📅 Subscription metadata changes detected for product {woo_product_id} - running sync")
                    subscription_sync_attempted = True
                    
                    # Sync subscription metadata for this product
                    subscription_sync_status = sync_product_subscription_metadata(local_product, webhook_data)
                    
                    # 🎨 DIVI METADATA CAPTURE - Store Divi metadata in ProductSimple for webhook updates
                    try:
                        from .models import ProductSimple
                        
                        # Extract subscription metadata to check for Divi
                        subscription_data = extract_subscription_metadata(webhook_data)
                        
                        if subscription_data.get('is_divi_managed', False):
                            logger.info(f"[PRODUCT_WEBHOOK] 🎨 Divi product detected in webhook update for {woo_product_id} - capturing metadata")
                            
                            # Get or create ProductSimple
                            product_simple, simple_created = ProductSimple.objects.get_or_create(
                                product=local_product,
                                defaults={'sku': webhook_data.get('sku', ''), 'woo_data': {}}
                            )
                            
                            if simple_created:
                                logger.info(f"[PRODUCT_WEBHOOK] 🎨 Created ProductSimple for Divi product {woo_product_id}")
                            
                            # Initialize woo_data
                            if not isinstance(product_simple.woo_data, dict):
                                product_simple.woo_data = {}
                            
                            # Extract Divi metadata
                            divi_metadata = {}
                            is_divi_enabled = False
                            meta_data = webhook_data.get('meta_data', [])
                            for meta in meta_data:
                                if isinstance(meta, dict):
                                    key = meta.get('key', '')
                                    value = meta.get('value', '')
                                    if key in ['_divi_filters_post_type', '_et_builder_version', '_et_pb_use_builder', '_et_pb_built_for_post_type']:
                                        divi_metadata[key] = value
                                        # Check if Divi builder is enabled
                                        if key == '_et_pb_use_builder' and value == 'on':
                                            is_divi_enabled = True
                            
                            # 🧹 Cleanup: Remove Divi metadata if builder is disabled
                            if divi_metadata and not is_divi_enabled:
                                # Divi builder is turned off - clean up metadata
                                if 'divi_metadata' in product_simple.woo_data:
                                    del product_simple.woo_data['divi_metadata']
                                    logger.info(f"[PRODUCT_WEBHOOK] 🧹 Cleaned up Divi metadata for product {woo_product_id} - builder disabled")
                            elif divi_metadata and is_divi_enabled:
                                # Store Divi metadata only if builder is enabled
                                product_simple.woo_data['divi_metadata'] = divi_metadata
                                logger.info(f"[PRODUCT_WEBHOOK] 🎨 Captured Divi metadata for product {woo_product_id}: {divi_metadata}")
                            
                            product_simple.woo_data['subscription_metadata'] = subscription_data
                            product_simple.save(update_fields=['woo_data'])
                            logger.info(f"[PRODUCT_WEBHOOK] 🎨 Saved Divi product metadata to ProductSimple for {woo_product_id}")
                            
                    except Exception as divi_error:
                        logger.error(f"[PRODUCT_WEBHOOK] Error capturing Divi metadata in webhook: {str(divi_error)}")
                    
                    if subscription_sync_status and subscription_sync_status.get('success', False):
                        # Check if any changes were made
                        changes_made = (
                            subscription_sync_status.get('simple_updated', False) or
                            subscription_sync_status.get('variations_updated', 0) > 0 or
                            subscription_sync_status.get('subscription_records_created', 0) > 0
                        )
                        
                        if changes_made:
                            updated_fields.append('subscription_metadata')
                            logger.info(f"[PRODUCT_WEBHOOK] ✅ Subscription metadata sync completed for product {woo_product_id}: {subscription_sync_status}")
                        else:
                            logger.info(f"[PRODUCT_WEBHOOK] ℹ️ Subscription metadata sync completed - no changes needed for product {woo_product_id}")
                    else:
                        logger.warning(f"[PRODUCT_WEBHOOK] ⚠️ Subscription metadata sync failed for product {woo_product_id}: {subscription_sync_status}")
                else:
                    logger.debug(f"[PRODUCT_WEBHOOK] No subscription metadata changes detected for product {woo_product_id}")
                    
            except Exception as subscription_error:
                logger.error(f"[PRODUCT_WEBHOOK] Error during subscription metadata sync for product {woo_product_id}: {str(subscription_error)}")
                subscription_sync_attempted = True
                subscription_sync_status = {
                    'success': False,
                    'error': str(subscription_error)
                }
            
            # ATUM Inventory Sync - Real-time sync when inventory-related changes are detected
            atum_sync_status = None
            atum_changes_detected = False
            atum_sync_attempted = False
            
            try:
                from .atum_webhook_sync import has_atum_inventory_changes, sync_product_atum_inventory
                
                # Check if this webhook contains ATUM inventory changes
                atum_changes_detected = has_atum_inventory_changes(webhook_data)
                
                if atum_changes_detected:
                    logger.info(f"[PRODUCT_WEBHOOK] 🏭 ATUM inventory changes detected for product {woo_product_id} - running sync")
                    atum_sync_attempted = True
                    
                    # Sync ATUM inventory for this product
                    atum_sync_status = sync_product_atum_inventory(local_product, dry_run=False)
                    
                    if atum_sync_status and atum_sync_status.get('success', False):
                        # Check if any changes were made
                        changes_made = (
                            atum_sync_status.get('product_updated', False) or
                            atum_sync_status.get('variations_updated', 0) > 0 or
                            atum_sync_status.get('locations_added', 0) > 0 or
                            atum_sync_status.get('locations_removed', 0) > 0
                        )
                        
                        if changes_made:
                            updated_fields.append('atum_inventory')
                            logger.info(f"[PRODUCT_WEBHOOK] ✅ ATUM inventory sync completed for product {woo_product_id}: {atum_sync_status}")
                        else:
                            logger.info(f"[PRODUCT_WEBHOOK] ℹ️ ATUM inventory sync completed - no changes needed for product {woo_product_id}")
                    else:
                        logger.warning(f"[PRODUCT_WEBHOOK] ⚠️ ATUM inventory sync failed for product {woo_product_id}: {atum_sync_status}")
                else:
                    logger.debug(f"[PRODUCT_WEBHOOK] No ATUM inventory changes detected for product {woo_product_id}")
                    
            except ImportError:
                logger.warning(f"[PRODUCT_WEBHOOK] ATUM webhook sync module not available - skipping ATUM sync for product {woo_product_id}")
                atum_sync_status = {
                    'success': False,
                    'error': 'ATUM webhook sync module not available'
                }
            except Exception as atum_error:
                logger.error(f"[PRODUCT_WEBHOOK] Error during ATUM inventory sync for product {woo_product_id}: {str(atum_error)}")
                atum_sync_attempted = True
                atum_sync_status = {
                    'success': False,
                    'error': str(atum_error)
                }
            
            # Update webhook log with ATUM sync information
            if webhook_log:
                webhook_log.update_atum_sync_info(
                    changes_detected=atum_changes_detected,
                    sync_attempted=atum_sync_attempted,
                    sync_results=atum_sync_status
                )
            
            logger.info(f"[PRODUCT_WEBHOOK] Successfully updated local product {local_product.id} from WooCommerce webhook")
            logger.info(f"[PRODUCT_WEBHOOK] Updated fields: {updated_fields}")
            
            # Prepare response with bundle and ATUM sync status
            response_data = {
                'success': True,
                'message': f'Product {woo_product_id} updated successfully',
                'product_id': str(local_product.id),
                'updated_fields': updated_fields
            }
            
            # Add bundle sync information if applicable
            if is_bundle_product or is_part_of_bundles:
                if comprehensive_sync_status is not None and default_series_sync_status is not None:
                    # Comprehensive sync was attempted
                    response_data['bundle_sync'] = {
                        'attempted': True,
                        'is_bundle_product': is_bundle_product,
                        'is_part_of_bundles': is_part_of_bundles,
                        'affected_bundles_count': len(affected_bundles),
                        'comprehensive_sync': comprehensive_sync_status,
                        'default_series_sync': default_series_sync_status,
                        'success': comprehensive_sync_status and default_series_sync_status,
                        'message': 'Comprehensive bundle sync completed' if (comprehensive_sync_status and default_series_sync_status) else 'Some bundle sync operations failed'
                    }
                elif bundle_sync_status is not None:
                    # Fallback sync was used
                    response_data['bundle_sync'] = {
                        'attempted': True,
                        'is_bundle_product': is_bundle_product,
                        'is_part_of_bundles': is_part_of_bundles,
                        'affected_bundles_count': len(affected_bundles),
                        'fallback_sync': bundle_sync_status,
                        'success': bundle_sync_status,
                        'message': 'Bundle data synced from WooCommerce (fallback)' if bundle_sync_status else 'Bundle data sync failed'
                    }
                else:
                    # No sync was attempted
                    response_data['bundle_sync'] = {
                        'attempted': False,
                        'is_bundle_product': is_bundle_product,
                        'is_part_of_bundles': is_part_of_bundles,
                        'affected_bundles_count': len(affected_bundles),
                        'success': False,
                        'message': 'Bundle sync was not attempted'
                    }
            
            # Add ATUM inventory sync information if applicable
            if atum_sync_status is not None:
                response_data['atum_sync'] = {
                    'attempted': True,
                    'success': atum_sync_status.get('success', False),
                    'product_updated': atum_sync_status.get('product_updated', False),
                    'variations_processed': atum_sync_status.get('variations_processed', 0),
                    'variations_updated': atum_sync_status.get('variations_updated', 0),
                    'locations_added': atum_sync_status.get('locations_added', 0),
                    'locations_removed': atum_sync_status.get('locations_removed', 0),
                    'errors': atum_sync_status.get('errors', []),
                    'message': 'ATUM inventory sync completed' if atum_sync_status.get('success', False) else f'ATUM inventory sync failed: {atum_sync_status.get("error", "Unknown error")}'
                }
            
            # Add brand sync information if applicable
            if brand_sync_result is not None:
                response_data['brand_sync'] = {
                    'attempted': True,
                    'mismatch_detected': brand_sync_result.get('mismatch_detected', False),
                    'local_brand': brand_sync_result.get('local_brand'),
                    'wc_brand': brand_sync_result.get('wc_brand'),
                    'sync_successful': brand_sync_result.get('sync_successful', False),
                    'error': brand_sync_result.get('error'),
                    'message': _get_brand_sync_message(brand_sync_result)
                }
            
            # Add subscription sync information if applicable
            if subscription_sync_status is not None:
                response_data['subscription_sync'] = {
                    'attempted': subscription_sync_attempted,
                    'success': subscription_sync_status.get('success', False),
                    'simple_updated': subscription_sync_status.get('simple_updated', False),
                    'variations_updated': subscription_sync_status.get('variations_updated', 0),
                    'subscription_records_created': subscription_sync_status.get('subscription_records_created', 0),
                    'subscription_records_updated': subscription_sync_status.get('subscription_records_updated', 0),
                    'errors': subscription_sync_status.get('errors', []),
                    'message': 'Subscription metadata sync completed' if subscription_sync_status.get('success', False) else f'Subscription metadata sync failed: {"; ".join(subscription_sync_status.get("errors", []))}'
                }
            
            # Mark webhook as successful
            if webhook_log:
                processing_time = int((time.time() - start_time) * 1000)
                webhook_log.local_product_id = local_product.id
                webhook_log.action_taken = 'update'
                webhook_log.updated_fields = updated_fields
                
                # Generate detailed update analysis
                try:
                    update_analysis = analyze_webhook_updates(updated_fields, webhook_data, local_product)
                    # Store the analysis in the webhook log for detailed reporting
                    webhook_log.update_analysis = update_analysis
                    
                    detailed_message = f"Product {woo_product_id} updated successfully. "
                    
                    # Add summary of what was updated
                    update_summary = []
                    for category, updates in update_analysis.items():
                        if updates:
                            update_summary.append(f"{category.replace('_', ' ').title()}: {len(updates)} changes")
                    
                    if update_summary:
                        detailed_message += f"Updates: {', '.join(update_summary)}"
                    
                    webhook_log.mark_success(
                        response_message=detailed_message,
                        processing_time_ms=processing_time
                    )
                except Exception as analysis_error:
                    logger.warning(f"[PRODUCT_WEBHOOK] Error generating update analysis: {str(analysis_error)}")
                    webhook_log.mark_success(
                        response_message=f"Product {woo_product_id} updated successfully",
                        processing_time_ms=processing_time
                    )
            
            return JsonResponse(response_data, status=200)
            
        except Product.DoesNotExist:
            # Product doesn't exist locally - this could be a new product creation
            logger.info(f"[PRODUCT_WEBHOOK] Webhook received for product {woo_product_id} that doesn't exist locally - attempting to create")
            
            # CRITICAL: Check if this is actually a variation before creating as standalone product
            # A variation has type='variation' OR parent_id > 0
            # A variable product (parent) has type='variable' with attributes and variations array
            webhook_product_type = webhook_data.get('type', 'simple')
            is_webhook_variation = (
                webhook_product_type == 'variation' or
                ('parent_id' in webhook_data and webhook_data.get('parent_id', 0) > 0)
            )
            
            # Log detection for debugging
            logger.info(f"[PRODUCT_WEBHOOK] Product {woo_product_id} type detection: type={webhook_product_type}, parent_id={webhook_data.get('parent_id', 0)}, is_variation={is_webhook_variation}")
            
            # Additional check for variation-like names (like IV Therapy products)
            # ONLY for products that don't have explicit type information (edge cases)
            if not is_webhook_variation and 'name' in webhook_data and webhook_product_type not in ['variable', 'variable-subscription', 'simple', 'grouped', 'external']:
                webhook_product_name = webhook_data['name']
                
                # Check if the product name contains strong variation indicators
                # Require BOTH parentheses AND dash to avoid false positives
                if '(' in webhook_product_name and ')' in webhook_product_name and ' - ' in webhook_product_name:
                    # Extract base name to look for parent products
                    base_name_parts = webhook_product_name.split('(')[0].split(' - ')[0].strip()
                    
                    # Look for existing variable products with similar base names (more specific match)
                    potential_parents = Product.objects.filter(
                        name__startswith=base_name_parts,
                        product_type__in=['variable', 'variable-subscription']
                    ).exclude(woo_product_id=woo_product_id)
                    
                    if potential_parents.exists():
                        logger.warning(f"[PRODUCT_WEBHOOK] Product {woo_product_id}: '{webhook_product_name}' appears to be a variation based on name pattern and existing variable parents")
                        is_webhook_variation = True
            
            if is_webhook_variation:
                logger.warning(f"[PRODUCT_WEBHOOK] Webhook for product {woo_product_id} (type={webhook_product_type}, parent_id={webhook_data.get('parent_id', 0)}) is a variation - skipping standalone product creation")
                
                # Mark webhook as successful but no action taken
                if webhook_log:
                    processing_time = int((time.time() - start_time) * 1000)
                    webhook_log.action_taken = 'variation_skipped'
                    webhook_log.mark_success(
                        response_message=f"Variation {woo_product_id} (type={webhook_product_type}) webhook skipped - variations are handled by parent product",
                        processing_time_ms=processing_time
                    )
                
                return JsonResponse({
                    'success': True,
                    'message': f'Variation {woo_product_id} webhook processed - skipped standalone creation',
                    'action': 'variation_skipped',
                    'reason': f'Product type={webhook_product_type}, parent_id={webhook_data.get("parent_id", 0)} - variations handled by parent'
                }, status=200)
            
            try:
                # Use the existing process_product function to create the new product
                from .woocommerce import WooCommerceAPI
                wc = WooCommerceAPI()
                
                logger.info(f"[PRODUCT_WEBHOOK] Creating new {webhook_product_type} product {woo_product_id}: '{webhook_data.get('name', 'Unknown')}'")
                
                # Create new product using webhook data and WooCommerce API for additional details
                new_product = process_product(webhook_data, wc)
                
                # Verify the new product was created successfully
                if new_product is None:
                    raise Exception("process_product returned None - product creation failed")
                
                logger.info(f"[PRODUCT_WEBHOOK] ✅ Successfully created new {webhook_product_type} product {new_product.id} (WooID: {woo_product_id}) from webhook")
                
                # Mark webhook as successful
                if webhook_log:
                    processing_time = int((time.time() - start_time) * 1000)
                    webhook_log.local_product_id = new_product.id
                    webhook_log.action_taken = 'create'
                    webhook_log.mark_success(
                        response_message=f"New {webhook_product_type} product '{new_product.name}' created successfully (WooID: {woo_product_id})",
                        processing_time_ms=processing_time
                    )
                
                return JsonResponse({
                    'success': True,
                    'message': f'New {webhook_product_type} product {woo_product_id} created successfully',
                    'product_id': str(new_product.id),
                    'product_name': new_product.name,
                    'product_type': webhook_product_type,
                    'action': 'product_created'
                }, status=201)
                
            except Exception as create_error:
                logger.error(f"[PRODUCT_WEBHOOK] Failed to create product {woo_product_id} from webhook: {str(create_error)}")
                
                # Mark webhook as failed
                if webhook_log:
                    processing_time = int((time.time() - start_time) * 1000)
                    webhook_log.action_taken = 'create_failed'
                    webhook_log.mark_failed(
                        error_message=f"Failed to create product {woo_product_id}: {str(create_error)}",
                        processing_time_ms=processing_time
                    )
                
                return JsonResponse({
                    'success': False,
                    'message': f'Failed to create product {woo_product_id}: {str(create_error)}',
                    'action_needed': 'sync_products'
                }, status=500)
            
    except Exception as e:
        logger.error(f"[PRODUCT_WEBHOOK] Error processing WooCommerce webhook: {str(e)}")
        
        # Mark webhook as failed
        if webhook_log:
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.action_taken = 'error'
            webhook_log.mark_failed(
                error_message=f"Webhook processing failed: {str(e)}",
                processing_time_ms=processing_time
            )
        
        return JsonResponse({
            'success': False,
            'message': f'Webhook processing failed: {str(e)}'
        }, status=500)


@csrf_exempt
def woocommerce_customer_webhook(request):
    """
    Webhook endpoint for WooCommerce customer updates.
    
    This endpoint receives notifications from WooCommerce when customers are
    created, updated, or deleted, and syncs the changes to the local database.
    
    Expected webhook events:
    - customer.created
    - customer.updated  
    - customer.deleted
    
    Returns:
        JSON response with sync status
    """
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Only POST method allowed'
        }, status=405)
    
    try:
        import os
        
        logger.info(f"Received WooCommerce customer webhook request")
        logger.info(f"Content-Type: {request.META.get('CONTENT_TYPE', 'Not set')}")
        logger.info(f"User-Agent: {request.META.get('HTTP_USER_AGENT', 'Not set')}")
        logger.info(f"Request body length: {len(request.body) if request.body else 0}")
        
        # Log raw request body for debugging
        if request.body:
            logger.info(f"Raw request body: {request.body}")
            logger.info(f"Raw request body decoded: {request.body.decode('utf-8', errors='ignore')}")
        
        # Parse webhook data from request body
        webhook_data = None
        try:
            if request.body:
                body_str = request.body.decode('utf-8')
                if body_str.strip():  # Check if body is not just whitespace
                    # Check if this is JSON or form data
                    if body_str.startswith('{') and body_str.endswith('}'):
                        # This looks like JSON
                        webhook_data = json.loads(body_str)
                        logger.info(f"Successfully parsed JSON customer webhook data: {type(webhook_data)}")
                        logger.info(f"Webhook data keys: {list(webhook_data.keys()) if isinstance(webhook_data, dict) else 'Not a dict'}")
                        logger.info(f"Full webhook data: {webhook_data}")
                    else:
                        # This looks like form data (e.g., webhook_id=20)
                        logger.info(f"Received form data: {body_str}")
                        # Parse form data
                        from urllib.parse import parse_qs
                        parsed_data = parse_qs(body_str)
                        
                        # Check if this is a test webhook
                        if 'webhook_id' in parsed_data:
                            logger.info(f"This is a WooCommerce customer test webhook with ID: {parsed_data['webhook_id']}")
                            return JsonResponse({
                                'success': True,
                                'message': f'WooCommerce customer test webhook received successfully (ID: {parsed_data["webhook_id"][0]})'
                            }, status=200)
                        else:
                            # Try to extract customer data from form data if available
                            webhook_data = {}
                            for key, value_list in parsed_data.items():
                                webhook_data[key] = value_list[0] if value_list else None
                            logger.info(f"Parsed form data into webhook_data: {webhook_data}")
                else:
                    logger.warning("Request body is empty or whitespace only")
                    # For WooCommerce test webhooks, return success even with empty body
                    return JsonResponse({
                        'success': True,
                        'message': 'Customer webhook test received - empty body is OK for test webhooks'
                    }, status=200)
            else:
                logger.warning("No request body received")
                # For WooCommerce test webhooks, return success even with no body
                return JsonResponse({
                    'success': True,
                    'message': 'Customer webhook test received - no body is OK for test webhooks'
                }, status=200)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from customer webhook request body: {e}")
            return JsonResponse({
                'success': False,
                'message': 'Invalid JSON data received'
            }, status=400)
        except Exception as e:
            logger.error(f"Error parsing customer webhook data: {e}")
            return JsonResponse({
                'success': False,
                'message': f'Error parsing customer webhook data: {str(e)}'
            }, status=400)
        
        if not isinstance(webhook_data, dict):
            logger.error(f"Customer webhook data is not a dictionary: {type(webhook_data)}")
            return JsonResponse({
                'success': False,
                'message': f'Expected JSON object, got {type(webhook_data).__name__}'
            }, status=400)
        
        woo_customer_id = webhook_data.get('id')
        if not woo_customer_id:
            logger.warning(f"No customer ID found in webhook data. Available keys: {list(webhook_data.keys())}")
            # This might be a test webhook from WooCommerce - return success
            return JsonResponse({
                'success': True,
                'message': 'Customer webhook received but no customer ID - this may be a test webhook',
                'available_keys': list(webhook_data.keys())
            }, status=200)
        
        logger.info(f"Processing customer webhook for WooCommerce customer ID: {woo_customer_id}")
        
        # Check for customer deletion event
        # WooCommerce sends deletion webhooks with minimal data or when customer is deleted
        if webhook_data.get('date_modified_gmt') and not webhook_data.get('email'):
            logger.info(f"Customer {woo_customer_id} appears to be deleted (no email in webhook)")
            
            try:
                from .models import Contact
                local_customer = Contact.objects.get(woo_customer_id=woo_customer_id)
                
                # Store customer info before deletion
                customer_name = f"{local_customer.first_name} {local_customer.last_name}"
                customer_email = local_customer.email
                
                # Remove the customer
                local_customer.delete()
                
                logger.info(f"Successfully deleted customer {customer_name} ({customer_email}) - WooCommerce ID: {woo_customer_id} from local database")
                
                return JsonResponse({
                    'success': True,
                    'message': f'Customer {woo_customer_id} deleted successfully',
                    'action': 'customer_deleted',
                    'customer_name': customer_name,
                    'customer_email': customer_email
                }, status=200)
                
            except Contact.DoesNotExist:
                logger.info(f"Customer {woo_customer_id} was already deleted or doesn't exist locally")
                return JsonResponse({
                    'success': True,
                    'message': f'Customer {woo_customer_id} was already deleted or doesn\'t exist locally',
                    'action': 'customer_already_deleted'
                }, status=200)
                
            except Exception as delete_error:
                logger.error(f"Error deleting customer {woo_customer_id}: {str(delete_error)}")
                return JsonResponse({
                    'success': False,
                    'message': f'Failed to delete customer {woo_customer_id}: {str(delete_error)}'
                }, status=500)

        # Check if customer exists in local database for update/creation
        try:
            from .models import Contact
            local_customer = Contact.objects.get(woo_customer_id=woo_customer_id)
            
            # Store original values for comparison
            original_values = {
                'first_name': local_customer.first_name,
                'last_name': local_customer.last_name,
                'email': local_customer.email,
                'phone': local_customer.phone,
                'billing_address': local_customer.billing_address,
                'billing_city': local_customer.billing_city,
                'billing_state': local_customer.billing_state,
                'billing_postcode': local_customer.billing_postcode,
                'billing_country': local_customer.billing_country
            }
            
            # Update local customer with WooCommerce data
            updated_fields = []
            changes_made = []
            
            # Update basic fields
            if 'first_name' in webhook_data:
                new_value = webhook_data['first_name'] or ''
                if local_customer.first_name != new_value:
                    changes_made.append(f"first_name: '{local_customer.first_name}' → '{new_value}'")
                    local_customer.first_name = new_value
                    updated_fields.append('first_name')
                    
            if 'last_name' in webhook_data:
                new_value = webhook_data['last_name'] or ''
                if local_customer.last_name != new_value:
                    changes_made.append(f"last_name: '{local_customer.last_name}' → '{new_value}'")
                    local_customer.last_name = new_value
                    updated_fields.append('last_name')
                    
            if 'email' in webhook_data and webhook_data['email']:
                new_value = webhook_data['email']
                if local_customer.email != new_value:
                    changes_made.append(f"email: '{local_customer.email}' → '{new_value}'")
                    local_customer.email = new_value
                    updated_fields.append('email')
            
            # Update billing information if provided
            if 'billing' in webhook_data and isinstance(webhook_data['billing'], dict):
                billing = webhook_data['billing']
                
                if 'phone' in billing:
                    new_value = billing['phone'] or ''
                    if local_customer.phone != new_value:
                        changes_made.append(f"phone: '{local_customer.phone}' → '{new_value}'")
                        local_customer.phone = new_value
                        updated_fields.append('phone')
                        
                if 'address_1' in billing:
                    new_value = billing['address_1'] or ''
                    if local_customer.billing_address != new_value:
                        changes_made.append(f"billing_address: '{local_customer.billing_address}' → '{new_value}'")
                        local_customer.billing_address = new_value
                        updated_fields.append('billing_address')
                        
                if 'city' in billing:
                    new_value = billing['city'] or ''
                    if local_customer.billing_city != new_value:
                        changes_made.append(f"billing_city: '{local_customer.billing_city}' → '{new_value}'")
                        local_customer.billing_city = new_value
                        updated_fields.append('billing_city')
                        
                if 'state' in billing:
                    new_value = billing['state'] or ''
                    if local_customer.billing_state != new_value:
                        changes_made.append(f"billing_state: '{local_customer.billing_state}' → '{new_value}'")
                        local_customer.billing_state = new_value
                        updated_fields.append('billing_state')
                        
                if 'postcode' in billing:
                    new_value = billing['postcode'] or ''
                    if local_customer.billing_postcode != new_value:
                        changes_made.append(f"billing_postcode: '{local_customer.billing_postcode}' → '{new_value}'")
                        local_customer.billing_postcode = new_value
                        updated_fields.append('billing_postcode')
                        
                if 'country' in billing:
                    new_value = billing['country'] or ''
                    if local_customer.billing_country != new_value:
                        changes_made.append(f"billing_country: '{local_customer.billing_country}' → '{new_value}'")
                        local_customer.billing_country = new_value
                        updated_fields.append('billing_country')
            
            # Update shipping information if provided
            if 'shipping' in webhook_data and isinstance(webhook_data['shipping'], dict):
                shipping = webhook_data['shipping']
                
                if 'address_1' in shipping:
                    new_value = shipping['address_1'] or ''
                    if local_customer.shipping_address != new_value:
                        changes_made.append(f"shipping_address: '{local_customer.shipping_address}' → '{new_value}'")
                        local_customer.shipping_address = new_value
                        updated_fields.append('shipping_address')
                        
                if 'city' in shipping:
                    new_value = shipping['city'] or ''
                    if local_customer.shipping_city != new_value:
                        changes_made.append(f"shipping_city: '{local_customer.shipping_city}' → '{new_value}'")
                        local_customer.shipping_city = new_value
                        updated_fields.append('shipping_city')
                        
                if 'state' in shipping:
                    new_value = shipping['state'] or ''
                    if local_customer.shipping_state != new_value:
                        changes_made.append(f"shipping_state: '{local_customer.shipping_state}' → '{new_value}'")
                        local_customer.shipping_state = new_value
                        updated_fields.append('shipping_state')
                        
                if 'postcode' in shipping:
                    new_value = shipping['postcode'] or ''
                    if local_customer.shipping_postcode != new_value:
                        changes_made.append(f"shipping_postcode: '{local_customer.shipping_postcode}' → '{new_value}'")
                        local_customer.shipping_postcode = new_value
                        updated_fields.append('shipping_postcode')
                        
                if 'country' in shipping:
                    new_value = shipping['country'] or ''
                    if local_customer.shipping_country != new_value:
                        changes_made.append(f"shipping_country: '{local_customer.shipping_country}' → '{new_value}'")
                        local_customer.shipping_country = new_value
                        updated_fields.append('shipping_country')
            
            # Determine shipping_same_as_billing from webhook billing vs shipping data
            if 'billing' in webhook_data and isinstance(webhook_data.get('billing'), dict) and \
               'shipping' in webhook_data and isinstance(webhook_data.get('shipping'), dict):
                wb = webhook_data['billing']
                ws = webhook_data['shipping']
                addresses_differ = (
                    (wb.get('address_1') or '') != (ws.get('address_1') or '') or
                    (wb.get('city') or '') != (ws.get('city') or '') or
                    (wb.get('state') or '') != (ws.get('state') or '') or
                    (wb.get('postcode') or '') != (ws.get('postcode') or '') or
                    (wb.get('country') or '') != (ws.get('country') or '')
                )
                new_flag = not addresses_differ
                if local_customer.shipping_same_as_billing != new_flag:
                    changes_made.append(f"shipping_same_as_billing: {local_customer.shipping_same_as_billing} → {new_flag}")
                    local_customer.shipping_same_as_billing = new_flag
                    updated_fields.append('shipping_same_as_billing')
            
            # Attempt to sync GHL contact ID if missing or if email changed
            ghl_sync_attempted = False
            ghl_sync_result = None
            
            if (not local_customer.ghl_contact_id or 'email' in updated_fields) and local_customer.email:
                try:
                    from .ghl_api import search_ghl_contact_by_email, apply_ghl_contact_fields
                    from django.utils import timezone
                    
                    logger.info(f"Attempting to sync GHL contact ID for updated customer: {local_customer.email}")
                    
                    ghl_sync_attempted = True
                    
                    ghl_contact_data = search_ghl_contact_by_email(local_customer.email)
                    if ghl_contact_data and 'id' in ghl_contact_data:
                        ghl_contact_id = ghl_contact_data['id']
                        
                        # Check if this is a new GHL ID or an update
                        is_new_ghl_id = not local_customer.ghl_contact_id
                        old_ghl_id = local_customer.ghl_contact_id
                        
                        # Update contact with GHL data
                        local_customer.ghl_contact_id = ghl_contact_id
                        local_customer.ghl_last_sync = timezone.now()
                        
                        # Store additional GHL data if available
                        if isinstance(ghl_contact_data, dict):
                            local_customer.ghl_data = ghl_contact_data
                        
                        # Add GHL fields to updated fields if they changed
                        if is_new_ghl_id:
                            updated_fields.extend(['ghl_contact_id', 'ghl_last_sync', 'ghl_data'])
                            changes_made.append(f"ghl_contact_id: None → '{ghl_contact_id}'")
                            ghl_sync_result = f"Found and added GHL contact ID: {ghl_contact_id}"
                        elif old_ghl_id != ghl_contact_id:
                            updated_fields.extend(['ghl_contact_id', 'ghl_last_sync', 'ghl_data'])
                            changes_made.append(f"ghl_contact_id: '{old_ghl_id}' → '{ghl_contact_id}'")
                            ghl_sync_result = f"Updated GHL contact ID: {old_ghl_id} → {ghl_contact_id}"
                        else:
                            # Just update sync timestamp and data
                            updated_fields.extend(['ghl_last_sync', 'ghl_data'])
                            ghl_sync_result = f"Refreshed GHL data for existing ID: {ghl_contact_id}"
                        
                        # Apply GHL fields to POS contact (GHL is source of truth)
                        ghl_field_updates = apply_ghl_contact_fields(local_customer, ghl_contact_data, save=False)
                        if ghl_field_updates:
                            updated_fields.extend(ghl_field_updates)
                            changes_made.append(f"GHL→POS fields: {ghl_field_updates}")
                        
                        logger.info(f"✅ {ghl_sync_result} (GHL fields updated: {ghl_field_updates})")
                    else:
                        ghl_sync_result = f"No GHL contact found for {local_customer.email}"
                        logger.info(ghl_sync_result)
                        
                except Exception as e:
                    ghl_sync_result = f"Failed to sync GHL contact ID: {str(e)}"
                    logger.warning(f"Failed to sync GHL contact ID for {local_customer.email}: {str(e)}")
            
            # Only save if there were actual changes
            if updated_fields:
                # Log customer data before saving
                logger.info(f"Updating customer {local_customer.id} (WooID: {woo_customer_id})")
                logger.info(f"Changes made: {changes_made}")
                
                local_customer.save()

                reassigned_customer = _reassign_woo_link_for_merged_email(
                    woo_customer_id,
                    local_customer.email,
                    reason='customer_webhook_update'
                )
                if reassigned_customer and reassigned_customer.id != local_customer.id:
                    local_customer = reassigned_customer
                
                logger.info(f"Successfully updated local customer {local_customer.id} from WooCommerce webhook")
                logger.info(f"Updated fields: {updated_fields}")
                
                response_data = {
                    'success': True,
                    'message': f'Customer {woo_customer_id} updated successfully',
                    'customer_id': str(local_customer.id),
                    'updated_fields': updated_fields,
                    'changes_made': changes_made,
                    'action': 'customer_updated'
                }
                
                # Add GHL sync information to response
                if ghl_sync_attempted:
                    response_data['ghl_sync_attempted'] = True
                    response_data['ghl_sync_result'] = ghl_sync_result
                    response_data['ghl_contact_id'] = local_customer.ghl_contact_id
                
                return JsonResponse(response_data, status=200)
            else:
                # Even if no other changes, try to sync GHL contact ID if missing
                if not local_customer.ghl_contact_id and local_customer.email:
                    try:
                        from .ghl_api import search_ghl_contact_by_email, apply_ghl_contact_fields
                        from django.utils import timezone
                        
                        logger.info(f"No field changes, but attempting GHL sync for customer: {local_customer.email}")
                        
                        ghl_contact_data = search_ghl_contact_by_email(local_customer.email)
                        if ghl_contact_data and 'id' in ghl_contact_data:
                            ghl_contact_id = ghl_contact_data['id']
                            
                            # Update contact with GHL data
                            local_customer.ghl_contact_id = ghl_contact_id
                            local_customer.ghl_last_sync = timezone.now()
                            
                            # Store additional GHL data if available
                            if isinstance(ghl_contact_data, dict):
                                local_customer.ghl_data = ghl_contact_data
                            
                            save_fields = ['ghl_contact_id', 'ghl_last_sync', 'ghl_data']
                            
                            # Apply GHL fields to POS contact (GHL is source of truth)
                            ghl_field_updates = apply_ghl_contact_fields(local_customer, ghl_contact_data, save=False)
                            if ghl_field_updates:
                                save_fields.extend(ghl_field_updates)
                            
                            local_customer.save(update_fields=save_fields)
                            
                            logger.info(f"✅ Added GHL contact ID during no-change webhook: {ghl_contact_id} (GHL fields updated: {ghl_field_updates})")
                            
                            return JsonResponse({
                                'success': True,
                                'message': f'Customer {woo_customer_id} - added GHL contact ID',
                                'customer_id': str(local_customer.id),
                                'action': 'customer_ghl_added',
                                'ghl_contact_id': ghl_contact_id,
                                'ghl_sync_result': f'Found and added GHL contact ID: {ghl_contact_id}'
                            }, status=200)
                        else:
                            logger.info(f"No GHL contact found for {local_customer.email}")
                            
                    except Exception as e:
                        logger.warning(f"Failed to sync GHL contact ID for {local_customer.email}: {str(e)}")
                
                logger.info(f"No changes detected for customer {woo_customer_id} - webhook data matches local data")
                reassigned_customer = _reassign_woo_link_for_merged_email(
                    woo_customer_id,
                    local_customer.email,
                    reason='customer_webhook_no_change'
                )
                if reassigned_customer and reassigned_customer.id != local_customer.id:
                    local_customer = reassigned_customer
                return JsonResponse({
                    'success': True,
                    'message': f'Customer {woo_customer_id} - no changes needed',
                    'customer_id': str(local_customer.id),
                    'action': 'customer_no_changes'
                }, status=200)
            
        except Contact.DoesNotExist:
            # Customer doesn't exist locally - this could be a new customer creation
            logger.info(f"Customer webhook received for customer {woo_customer_id} that doesn't exist locally - attempting to create")
            
            try:
                # Use the existing process_customer function to create the new customer
                # This function now includes GHL contact ID sync
                new_customer = process_customer(webhook_data)
                
                logger.info(f"Successfully created new customer {woo_customer_id} from WooCommerce webhook")
                
                # Prepare response data
                response_data = {
                    'success': True,
                    'message': f'New customer {woo_customer_id} created successfully',
                    'customer_id': str(new_customer.id),
                    'action': 'customer_created',
                    'customer_name': f"{new_customer.first_name} {new_customer.last_name}",
                    'customer_email': new_customer.email
                }
                
                # Add GHL sync information if available
                if new_customer.ghl_contact_id:
                    response_data['ghl_contact_id'] = new_customer.ghl_contact_id
                    response_data['ghl_sync_result'] = f'Found and added GHL contact ID: {new_customer.ghl_contact_id}'
                    response_data['ghl_synced'] = True
                else:
                    response_data['ghl_synced'] = False
                    response_data['ghl_sync_result'] = f'No GHL contact found for {new_customer.email}'
                
                return JsonResponse(response_data, status=201)
                
            except Exception as create_error:
                logger.error(f"Failed to create customer {woo_customer_id} from webhook: {str(create_error)}")
                
                return JsonResponse({
                    'success': False,
                    'message': f'Failed to create customer {woo_customer_id}: {str(create_error)}',
                    'action_needed': 'sync_customers'
                }, status=500)
            
    except Exception as e:
        logger.error(f"Error processing WooCommerce customer webhook: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f'Customer webhook processing failed: {str(e)}'
        }, status=500)


@api_view(['POST', 'GET'])
@permission_classes([IsAuthenticated])
def woocommerce_webhook_test(request):
    """
    Simple webhook test endpoint to debug WooCommerce webhook format.
    """
    try:
        import json
        
        logger.info("=== WEBHOOK TEST ENDPOINT ===")
        logger.info(f"Method: {request.method}")
        logger.info(f"Content-Type: {request.META.get('CONTENT_TYPE', 'Not set')}")
        logger.info(f"Headers: {dict(request.META)}")
        
        # Log raw body
        if hasattr(request, 'body') and request.body:
            logger.info(f"Raw body: {request.body}")
            logger.info(f"Raw body decoded: {request.body.decode('utf-8', errors='ignore')}")
        
        # Log request.data
        if hasattr(request, 'data'):
            logger.info(f"Request.data: {request.data}")
            logger.info(f"Request.data type: {type(request.data)}")
        
        return Response({
            'success': True,
            'message': 'Webhook test received successfully',
            'method': request.method,
            'content_type': request.META.get('CONTENT_TYPE', 'Not set'),
            'has_body': bool(request.body if hasattr(request, 'body') else False),
            'has_data': bool(request.data if hasattr(request, 'data') else False),
            'data_type': str(type(request.data)) if hasattr(request, 'data') else 'No data'
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Webhook test error: {str(e)}")
        return Response({
            'success': False,
            'message': f'Webhook test failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def sync_product_variations_webhook(product):
    """
    Sync product variations for webhook - uses DIRECT DATABASE queries for 100% fresh data.
    Bypasses ALL caching layers (REST API, WooCommerce cache, etc.)
    
    Args:
        product: Product instance to sync variations for
        
    Returns:
        dict: Statistics of sync operation (updated, created, removed, errors)
    """
    from .woocommerce import WooCommerceAPI
    from .models import ProductVariation
    from decimal import Decimal, InvalidOperation
    import json
    import os
    import pymysql
    from contextlib import contextmanager
    
    stats = {
        'updated': 0,
        'created': 0,
        'removed': 0,
        'errors': 0
    }
    
    @contextmanager
    def get_woo_database_connection():
        """Direct connection to WooCommerce Cloud SQL database"""
        connection = None
        
        try:
            from core.secrets import get_woo_db_config
            woo_cfg = get_woo_db_config()
            
            logger.info(f"💾 DB Connection params: db_host={woo_cfg['db_host']}, db_port={woo_cfg['db_port']}, db_name={woo_cfg['db_name']}")
            
            if not all([woo_cfg['db_host'], woo_cfg['db_name'], woo_cfg['db_username'], woo_cfg['db_password']]):
                raise ValueError("Missing required database connection credentials")
            
            connection = pymysql.connect(
                host=woo_cfg['db_host'],
                port=int(woo_cfg['db_port']),
                user=woo_cfg['db_username'],
                password=woo_cfg['db_password'],
                database=woo_cfg['db_name'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10
            )
            logger.info(f"💾 Database connection established")
            
            yield connection
            
        except Exception as e:
            logger.error(f"WooCommerce DB connection error: {str(e)}")
            raise
        finally:
            if connection:
                connection.close()
    
    def get_fresh_variation_data_from_db(parent_product_id):
        """Get 100% fresh variation data directly from WooCommerce database"""
        try:
            # Validate and convert parent_product_id to integer
            if not parent_product_id:
                raise ValueError("parent_product_id is required")
            
            parent_id_int = int(str(parent_product_id))  # Convert to string first, then int
            logger.info(f"💾 Converting product ID '{parent_product_id}' to integer: {parent_id_int}")
            
        except (ValueError, TypeError) as e:
            logger.error(f"💾 Invalid parent_product_id '{parent_product_id}': {str(e)}")
            raise ValueError(f"Invalid parent_product_id: {parent_product_id}")
        # Use separate queries to avoid MAX() issues with mixed data types
        # First get all variations for this parent
        variations_query = """
        SELECT 
            p.ID as variation_id,
            p.post_title,
            p.post_name,
            p.post_status,
            p.menu_order
        FROM wp_posts p
        WHERE p.post_parent = %s 
          AND p.post_type = 'product_variation'
          AND p.post_status IN ('publish', 'private')
        ORDER BY p.menu_order, p.ID
        """
        
        # Then get meta data for each variation individually
        meta_query = """
        SELECT 
            pm.meta_key,
            pm.meta_value
        FROM wp_postmeta pm
        WHERE pm.post_id = %s 
          AND pm.meta_key IN ('_price', '_regular_price', '_sale_price', '_sku', 
                               '_stock_quantity', '_manage_stock', '_stock_status')
        """
        
        with get_woo_database_connection() as conn:
            cursor = conn.cursor()
            logger.info(f"💾 Executing main query with parent_id: {parent_id_int} (type: {type(parent_id_int)})")
            
            try:
                # First get basic variation info
                cursor.execute(variations_query, (parent_id_int,))
                variations = cursor.fetchall()
                logger.info(f"💾 Variations query successful, got {len(variations)} variations")
                
                # Then get meta data for each variation
                for variation in variations:
                    variation_id = variation['variation_id']
                    cursor.execute(meta_query, (int(variation_id),))
                    meta_data = cursor.fetchall()
                    
                    # Process meta data into the variation record
                    for meta in meta_data:
                        key = meta['meta_key'].replace('_', '')  # Remove underscore prefix
                        variation[key] = meta['meta_value']
                    
                    # Set default values for missing meta keys
                    for default_key in ['price', 'regular_price', 'sale_price', 'sku', 
                                       'stock_quantity', 'manage_stock', 'stock_status']:
                        if default_key not in variation:
                            variation[default_key] = None
                
                logger.info(f"💾 Meta data query successful for all variations")
                
            except Exception as main_query_error:
                logger.error(f"💾 Query failed: {str(main_query_error)}")
                raise
            
            logger.info(f"💾 Direct DB query found {len(variations)} variations for product {parent_product_id}")
            
            # Convert to format similar to REST API
            formatted_variations = []
            for i, var in enumerate(variations):
                logger.info(f"💾 Processing variation {i+1}/{len(variations)}: ID={var['variation_id']} (type: {type(var['variation_id'])})")
                
                # Get variation attributes - use the exact working query from DBeaver
                attr_query = """
                SELECT 
                    pm.meta_key,
                    pm.meta_value,
                    MIN(t1.name) as term_name_by_slug,
                    MIN(t1.slug) as term_slug_by_slug,
                    COALESCE(MIN(t1.name), pm.meta_value) as display_value
                FROM wp_postmeta pm
                LEFT JOIN wp_terms t1 ON pm.meta_value = t1.slug
                WHERE pm.post_id = %s 
                  AND pm.meta_key LIKE 'attribute_%%'
                  AND pm.meta_value IS NOT NULL
                  AND pm.meta_value != ''
                GROUP BY pm.meta_key, pm.meta_value
                ORDER BY pm.meta_key
                """
                
                try:
                    # Ensure variation_id is integer for MariaDB
                    variation_id_int = int(var['variation_id']) if var['variation_id'] is not None else 0
                    logger.info(f"💾 Executing attribute query with variation_id: {variation_id_int}")
                    cursor.execute(attr_query, (variation_id_int,))
                    attributes = cursor.fetchall()
                    logger.info(f"💾 Attribute query successful, got {len(attributes)} attributes")
                except Exception as attr_query_error:
                    logger.error(f"💾 Attribute query failed for variation {var['variation_id']}: {str(attr_query_error)}")
                    attributes = []  # Continue with empty attributes
                
                # Build variation name from attributes - focus on meaningful values only
                variation_name_parts = []
                formatted_attributes = []
                seen_attributes = {}  # Track unique attributes to avoid duplicates
                
                logger.info(f"💾 Raw attributes for variation {var['variation_id']}: {[attr for attr in attributes]}")
                
                for attr in attributes:
                    attr_key = attr['meta_key'].replace('attribute_', '').replace('pa_', '')
                    display_value = attr['display_value']  # This should be "Chocolate" or "Strawberry Vanilla"
                    raw_value = attr['meta_value']         # This is "chocolate-f" or "strawberry-vanilla"
                    term_name = attr.get('term_name_by_slug')  # The actual term name from database
                    
                    logger.info(f"💾 Attribute: key='{attr_key}', raw='{raw_value}', display='{display_value}', term_name='{term_name}'")
                    
                    # Skip empty or meaningless values
                    if not display_value or display_value in ['', '0', 'null']:
                        continue
                    
                    # Use the display value directly (it should be clean from COALESCE)
                    clean_value = display_value.strip()
                    
                    # Skip if we already have this exact attribute value (deduplication)
                    attr_signature = f"{attr_key}:{clean_value}"
                    if attr_signature in seen_attributes:
                        logger.info(f"🔄 Skipping duplicate attribute: {attr_signature}")
                        continue
                    
                    seen_attributes[attr_signature] = True
                    
                    # Priority system for variation naming and attribute filtering:
                    # 1. flavor, size, quantity, color - these are most important
                    # 2. Skip technical attributes like size_qty if we already have quantity
                    priority_attributes = ['flavor', 'size', 'quantity', 'color', 'type']
                    
                    is_priority_attr = attr_key.lower() in priority_attributes
                    
                    # Only add to variation name if it's meaningful and not just the attribute key
                    if clean_value and clean_value.lower() != attr_key.lower():
                        # For priority attributes or if we don't have any name parts yet
                        if is_priority_attr or len(variation_name_parts) == 0:
                            # Avoid adding similar values (like "30 Tablets" and "30 Capsules")
                            is_similar = any(clean_value.split()[0] in existing.split()[0] for existing in variation_name_parts)
                            if not is_similar:
                                variation_name_parts.append(clean_value)
                    
                    # Smart attribute filtering - keep only the most important/clean attribute
                    should_include_attr = True
                    
                    # Define groups of semantically related attributes that can be deduped against each other
                    # Only attributes in the SAME group should be considered duplicates by number
                    size_quantity_group = ['quantity', 'size', 'size_qty', 'count', 'ct']
                    
                    # Extract the number from the value for similarity comparison
                    try:
                        current_number = clean_value.split()[0]  # e.g., "30" from "30 Tablets"
                    except (IndexError, ValueError):
                        current_number = None
                    
                    # Check if we already have an attribute with the same number
                    # ONLY dedup attributes that are in the same semantic group (e.g., quantity vs size_qty)
                    # Never dedup across different attribute types (e.g., quantity vs strength)
                    if current_number:
                        for existing_attr in formatted_attributes:
                            existing_key_display = existing_attr['name']  # This is the capitalized display name
                            existing_key_raw = existing_key_display.lower().replace(' ', '_')  # Convert back to raw for comparison
                            existing_value = existing_attr['option']
                            
                            # Only compare attributes in the same semantic group
                            current_in_size_group = attr_key.lower() in size_quantity_group
                            existing_in_size_group = existing_key_raw in size_quantity_group
                            
                            # Skip number comparison if attributes are in different groups
                            if current_in_size_group != existing_in_size_group:
                                continue
                            
                            try:
                                existing_number = existing_value.split()[0]  # e.g., "30" from "30 Tablets"
                                
                                # If same number AND same attribute group, prefer priority attributes (quantity > size_qty)
                                if current_number == existing_number:
                                    # If existing is priority and current is not, skip current
                                    if existing_key_raw in priority_attributes and not is_priority_attr:
                                        should_include_attr = False
                                        logger.info(f"🚫 Skipping '{attr_key}:{clean_value}' - priority '{existing_key_display}:{existing_value}' already exists")
                                        break
                                    # If current is priority and existing is not, remove existing and add current
                                    elif is_priority_attr and existing_key_raw not in priority_attributes:
                                        formatted_attributes.remove(existing_attr)
                                        logger.info(f"🔄 Replacing '{existing_key_display}:{existing_value}' with priority '{attr_key.title()}:{clean_value}'")
                                        break
                                    # If both have same priority level, keep the first one (quantity vs size)
                                    elif not is_priority_attr and existing_key_raw not in priority_attributes:
                                        should_include_attr = False
                                        logger.info(f"🚫 Skipping duplicate number '{attr_key}:{clean_value}' - '{existing_key_display}:{existing_value}' already exists")
                                        break
                                        
                            except (IndexError, ValueError):
                                continue
                    
                    # Only add to final attributes if it should be included
                    if should_include_attr:
                        # Capitalize the attribute name for proper display
                        capitalized_name = attr_key.replace('_', ' ').title()
                        
                        formatted_attributes.append({
                            'name': capitalized_name,  # Capitalize: quantity -> Quantity, size_qty -> Size Qty
                            'slug': attr.get('term_slug_by_slug', raw_value),
                            'option': clean_value  # Use clean value instead of raw slug!
                        })
                
                # Create variation name - should now be clean like "Chocolate", "Strawberry Vanilla"
                if variation_name_parts:
                    # Take the first meaningful name part (usually flavor)
                    variation_name = variation_name_parts[0]
                else:
                    # Fallback to variation ID format
                    variation_name = f"Variation {var['variation_id']}"
                
                formatted_var = {
                    'id': var['variation_id'],
                    'name': variation_name,  # Clean, single attribute name
                    'sku': var['sku'] or '',
                    'price': var['price'] or '',
                    'regular_price': var['regular_price'] or '',
                    'sale_price': var['sale_price'] or '',
                    'stock_quantity': var['stock_quantity'],
                    'manage_stock': var['manage_stock'] == 'yes',
                    'stock_status': var['stock_status'] or 'instock',
                    'menu_order': var['menu_order'] or 0,
                    'attributes': formatted_attributes
                }
                
                logger.info(f"💾 Built variation name: '{variation_name}' from {len(attributes)} attributes (parts: {variation_name_parts})")
                logger.info(f"💾 Final formatted variation: {formatted_var}")
                formatted_variations.append(formatted_var)
            
            logger.info(f"💾 Successfully formatted {len(formatted_variations)} variations with names")
            return formatted_variations
    
    try:
        logger.info(f"💾 Getting 100% FRESH variation data via DIRECT DATABASE query (product {product.woo_product_id})")
        
        # Get variations directly from WooCommerce database - bypasses ALL cache layers!
        try:
            wc_variations = get_fresh_variation_data_from_db(product.woo_product_id)
        except Exception as db_error:
            logger.error(f"💾 Direct DB query failed, falling back to API: {str(db_error)}")
            # Fallback to API if direct DB fails
            wc_api = WooCommerceAPI()
            logger.info(f"🔄 Fallback: Fetching variation data via API with cache-busting")
            wc_variations = wc_api.get_product_variations(product.woo_product_id, force_fresh=True)
        
        if wc_variations is None or len(wc_variations) == 0:
            logger.info(f"No variations found for product {product.woo_product_id}")
            return stats
            
        logger.info(f"✨ Successfully retrieved {len(wc_variations)} variations with FRESH pricing data")
        
        # Get current variations from database
        current_variations = ProductVariation.objects.filter(product=product)
        current_variation_ids = set(var.woo_variation_id for var in current_variations)
        wc_variation_ids = set(var['id'] for var in wc_variations)
        
        logger.info(f"Webhook variation sync: WooCommerce={len(wc_variations)}, Database={len(current_variations)}")
        
        # Track if we need to update parent pricing
        should_update_parent_pricing = False
        variation_prices = []
        
        # Process each WooCommerce variation
        for wc_variation in wc_variations:
            variation_id = wc_variation.get('id')
            
            # Collect variation price for parent pricing calculation
            variation_price = wc_variation.get('price')
            if variation_price:
                try:
                    price_float = float(variation_price)
                    if price_float > 0:
                        variation_prices.append(price_float)
                except (ValueError, TypeError):
                    pass
            
            try:
                # Try to get existing variation
                try:
                    variation = ProductVariation.objects.get(
                        product=product,
                        woo_variation_id=variation_id
                    )
                    
                    # Update existing variation
                    if update_variation_from_wc_data_webhook(variation, wc_variation):
                        stats['updated'] += 1
                        should_update_parent_pricing = True
                        
                except ProductVariation.DoesNotExist:
                    # Create new variation
                    if create_variation_from_wc_data_webhook(product, wc_variation):
                        stats['created'] += 1
                        should_update_parent_pricing = True
                    
            except Exception as e:
                stats['errors'] += 1
                logger.error(f"Error processing variation {variation_id} for product {product.woo_product_id}: {str(e)}")
        
        # Remove orphaned variations (variations that no longer exist in WooCommerce)
        orphaned_ids = current_variation_ids - wc_variation_ids
        if orphaned_ids:
            for variation in current_variations:
                if variation.woo_variation_id in orphaned_ids:
                    logger.info(f"Removing orphaned variation {variation.woo_variation_id} via webhook")
                    variation.delete()
                    stats['removed'] += 1
                    should_update_parent_pricing = True
        
        # Update parent product pricing based on variations
        if should_update_parent_pricing and variation_prices:
            try:
                # Calculate parent price (use lowest variation price)
                min_price = min(variation_prices)
                first_price = variation_prices[0] if variation_prices else min_price
                
                # Use minimum price as parent price (WooCommerce standard behavior)
                new_parent_price = min_price
                
                # Update parent product price if it's different
                old_parent_price = float(product.price) if product.price else 0.0
                
                if abs(old_parent_price - new_parent_price) > 0.01:  # Avoid floating point precision issues
                    logger.info(f"💰 Updating parent product price: ${old_parent_price:.2f} → ${new_parent_price:.2f}")
                    
                    # Update the parent product
                    from decimal import Decimal
                    product.price = Decimal(str(new_parent_price))
                    
                    # Also update sale_price if it exists and is higher than the new price
                    if product.sale_price and float(product.sale_price) > new_parent_price:
                        product.sale_price = Decimal(str(new_parent_price))
                        logger.info(f"💰 Updated parent sale_price to match new price: ${new_parent_price:.2f}")
                    
                    product.save(update_fields=['price', 'sale_price'])
                    stats['parent_price_updated'] = True
                    logger.info(f"✅ Parent product pricing updated successfully")
                else:
                    logger.info(f"💰 Parent product price unchanged: ${old_parent_price:.2f}")
                    stats['parent_price_updated'] = False
                    
            except Exception as price_error:
                logger.error(f"❌ Error updating parent product pricing: {str(price_error)}")
                stats['parent_price_error'] = str(price_error)
        elif variation_prices:
            logger.info(f"💰 Parent pricing not updated - no significant changes detected")
        else:
            logger.warning(f"💰 No valid variation prices found for parent pricing update")
        
        return stats
        
    except Exception as e:
        stats['errors'] += 1
        logger.error(f"Error syncing variations for product {product.woo_product_id}: {str(e)}")
        return stats


def update_variation_from_wc_data_webhook(variation, wc_variation):
    """Update existing variation with WooCommerce data for webhook context"""
    from decimal import Decimal, InvalidOperation
    
    updated = False
    
    def safe_decimal(value):
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    
    # Extract data from WooCommerce variation (only fields that exist in ProductVariation model)
    # Note: wc_variation now contains clean attribute data from our DB query
    wc_data = {
        'sku': wc_variation.get('sku', ''),
        'price': safe_decimal(wc_variation.get('price')),
        'regular_price': safe_decimal(wc_variation.get('regular_price')),
        'sale_price': safe_decimal(wc_variation.get('sale_price')),
        'stock_quantity': wc_variation.get('stock_quantity'),
        'menu_order': wc_variation.get('menu_order', 0),
        'attributes': wc_variation.get('attributes', []),  # Now contains clean names!
        'woo_data': wc_variation,  # Store complete data with clean attributes
    }
    
    # Check each field for changes (only fields that exist in ProductVariation model)
    field_mappings = {
        'sku': 'sku', 
        'price': 'price',
        'regular_price': 'regular_price',
        'sale_price': 'sale_price',
        'stock_quantity': 'stock_quantity',
        'menu_order': 'menu_order',
    }
    
    for wc_field, db_field in field_mappings.items():
        old_value = getattr(variation, db_field)
        new_value = wc_data[wc_field]
        
        if old_value != new_value:
            setattr(variation, db_field, new_value)
            updated = True
    
    # Handle attributes JSON field
    old_attributes = getattr(variation, 'attributes')
    new_attributes = wc_data['attributes']
    if json.dumps(old_attributes, sort_keys=True) != json.dumps(new_attributes, sort_keys=True):
        variation.attributes = new_attributes
        updated = True
    
    # Handle woo_data — MERGE instead of replace to preserve subscription_metadata
    # and other data added by sync_product_subscription_metadata
    old_woo_data = getattr(variation, 'woo_data') or {}
    new_woo_data = wc_data['woo_data']
    
    # Preserve these keys from old woo_data if they exist
    preserved_keys = ['subscription_metadata', 'last_subscription_sync', 'meta_data']
    preserved = {}
    if isinstance(old_woo_data, dict):
        for key in preserved_keys:
            if key in old_woo_data:
                preserved[key] = old_woo_data[key]
    
    # Merge: new data wins for basic fields, preserved keys are kept
    if isinstance(new_woo_data, dict):
        merged_woo_data = {**new_woo_data, **preserved}
    else:
        merged_woo_data = new_woo_data
    
    if json.dumps(old_woo_data, sort_keys=True, default=str) != json.dumps(merged_woo_data, sort_keys=True, default=str):
        variation.woo_data = merged_woo_data
        updated = True
    
    # Save changes
    if updated:
        variation.save()
    
    return updated


def create_variation_from_wc_data_webhook(product, wc_variation):
    """Create new variation from WooCommerce data for webhook context"""
    from decimal import Decimal, InvalidOperation
    
    def safe_decimal(value):
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    
    try:
        variation_data = {
            'product': product,
            'woo_variation_id': wc_variation.get('id'),
            'sku': wc_variation.get('sku', ''),
            'price': safe_decimal(wc_variation.get('price')),
            'regular_price': safe_decimal(wc_variation.get('regular_price')),
            'sale_price': safe_decimal(wc_variation.get('sale_price')),
            'stock_quantity': wc_variation.get('stock_quantity'),
            'menu_order': wc_variation.get('menu_order', 0),
            'attributes': wc_variation.get('attributes', []),  # Now contains clean names!
            'woo_data': wc_variation,  # Store complete data with clean attributes
        }
        
        logger.info(f"Creating new variation {wc_variation.get('id')} via webhook")
        ProductVariation.objects.create(**variation_data)
        return True
        
    except Exception as e:
        logger.error(f"Error creating variation {wc_variation.get('id')} for product {product.woo_product_id}: {str(e)}")
        return False


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_woocommerce_order_status(request, order_id):
    """
    Update the status of a WooCommerce order directly via WooCommerce API
    """
    try:
        new_status = request.data.get('status')
        
        # Validate the new status
        valid_statuses = ['pending', 'processing', 'completed', 'cancelled', 'refunded']
        if new_status not in valid_statuses:
            return Response(
                {"error": f"Invalid status. Must be one of: {', '.join(valid_statuses)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Initialize WooCommerce API
        from .woocommerce import WooCommerceAPI
        wc_api = WooCommerceAPI()
        
        # Get current order data first
        try:
            current_order = wc_api.get_order(order_id)
            if not current_order:
                return Response(
                    {"error": f"WooCommerce order {order_id} not found"},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            old_status = current_order.get('status', 'unknown')
            
        except Exception as e:
            logger.error(f"Failed to get WooCommerce order {order_id}: {str(e)}")
            return Response(
                {"error": f"Failed to get WooCommerce order: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if new_status == 'completed':
            completion_check = validate_woocommerce_order_can_complete(wc_api, current_order)
            if not completion_check.get("eligible"):
                return Response(
                    {
                        "error": completion_check.get("message"),
                        "code": completion_check.get("code"),
                        "completion_validation": completion_check,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Update the order status in WooCommerce
        try:
            woo_sync_result = wc_api.update_order_status(order_id, new_status)
            logger.info(f"Updated WooCommerce order {order_id} status from {old_status} to {new_status}")
            
            # Find and update corresponding POS order
            pos_sync_result = None
            try:
                # Look for corresponding POS order using the same logic as the webhook
                from .webhooks_order import find_corresponding_pos_order
                pos_order = find_corresponding_pos_order(current_order)
                
                if pos_order:
                    # Pass through WooCommerce status directly
                    new_pos_status = new_status
                    
                    if new_pos_status and pos_order.status != new_pos_status:
                        old_pos_status = pos_order.status
                        pos_order.status = new_pos_status
                        
                        # Update completed_at timestamp if marking as completed
                        if new_pos_status == 'completed' and old_pos_status != 'completed':
                            from django.utils import timezone
                            pos_order.completed_at = timezone.now()
                        
                        pos_order.save()
                        
                        logger.info(f"✅ Updated corresponding POS order {pos_order.id}: {old_pos_status} → {new_pos_status}")
                        pos_sync_result = {
                            "status": "success",
                            "message": f"POS order {pos_order.id} updated to {new_pos_status}",
                            "pos_order_id": str(pos_order.id),
                            "old_pos_status": old_pos_status,
                            "new_pos_status": new_pos_status
                        }
                    else:
                        pos_sync_result = {
                            "status": "skipped",
                            "message": f"No POS status update needed (current: {pos_order.status})"
                        }
                else:
                    pos_sync_result = {
                        "status": "not_found",
                        "message": f"No corresponding POS order found for WooCommerce order {order_id}"
                    }
                    
            except Exception as pos_e:
                logger.error(f"Failed to sync POS order: {str(pos_e)}")
                pos_sync_result = {
                    "status": "error",
                    "message": f"Failed to sync POS order: {str(pos_e)}"
                }
            
            # Return response in same format as POS order update
            response_data = {
                "order": {"id": order_id, "status": new_status},
                "old_status": old_status,
                "new_status": new_status,
                "woocommerce_sync": {
                    "status": "success",
                    "message": f"Successfully updated WooCommerce order {order_id}",
                    "data": woo_sync_result
                },
                "pos_sync": pos_sync_result
            }
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Failed to update WooCommerce order {order_id} status: {str(e)}")
            return Response(
                {"error": f"Failed to update WooCommerce order status: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
    except Exception as e:
        logger.error(f"Error in update_woocommerce_order_status: {str(e)}")
        return Response(
            {"error": f"Internal server error: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_membership_to_ghl(request):
    """
    Dedicated endpoint for syncing membership purchases to GHL
    
    POST /api/sync-membership-ghl/
    """
    try:
        from .ghl_membership_sync import (
            is_membership_product, 
            sync_membership_purchase_to_ghl
        )
        
        order_data = request.data
        logger.info(f"🎯 Processing GHL membership sync request: {order_data}")
        
        # Validate required fields
        customer_id = order_data.get('customer_id')
        if not customer_id:
            return Response({'error': 'customer_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        items = order_data.get('items', [])
        if not items:
            return Response({'error': 'items array is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get location_id from request (critical for GHL API)
        location_id = order_data.get('location_id')
        logger.info(f"📍 GHL Location ID received: {location_id}")
        
        # Get customer by UUID
        try:
            customer = Contact.objects.get(id=customer_id)
            logger.info(f"✅ Found customer for GHL sync: {customer.email}")
        except Contact.DoesNotExist:
            logger.error(f"❌ Customer not found: {customer_id}")
            return Response({'error': f'Customer not found: {customer_id}'}, status=status.HTTP_404_NOT_FOUND)
        
        if not customer.email:
            return Response({'error': 'Customer email is required for GHL sync'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Process membership items
        sync_results = {
            'processed_memberships': [],
            'skipped_items': [],
            'errors': []
        }
        
        for item in items:
            try:
                # Get product name
                product_name = item.get('product_name', '')
                
                # If no product name, try to get it from database
                if not product_name:
                    try:
                        product = Product.objects.get(id=item.get('product_id'))
                        product_name = product.name
                        logger.info(f"📦 Retrieved product name from database: {product_name}")
                    except Product.DoesNotExist:
                        logger.warning(f"⚠️ Product not found: {item.get('product_id')}")
                        sync_results['skipped_items'].append({
                            'product_id': item.get('product_id'),
                            'reason': 'Product not found'
                        })
                        continue
                
                logger.info(f"🔍 Checking product for membership sync: {product_name}")
                
                # Check if this is a membership product
                if is_membership_product(product_name):
                    logger.info(f"✅ Processing membership product: {product_name}")
                    
                    # Extract metadata
                    metadata = item.get('metadata', {})
                    
                    # Determine pricing
                    actual_price = float(metadata.get('actualMembershipPrice', 0))
                    checkout_price = float(metadata.get('checkoutPrice', 0))
                    
                    # Use actual price if available, otherwise use checkout price
                    amount_paid = actual_price if actual_price > 0 else checkout_price
                    
                    # Check if this is a trial membership
                    is_trial = metadata.get('isTrialMembership', False)
                    trial_months = int(metadata.get('trialPeriodMonths', 0)) if is_trial else 0
                    
                    # Prepare membership data for GHL sync
                    membership_data = {
                        'product_name': product_name,
                        'amount_paid': amount_paid,
                        'billing_interval': trial_months if is_trial and trial_months > 0 else 1,
                        'billing_period': 'month',
                        'start_date': timezone.now(),
                        'subscription_id': 'N/A',
                        'order_id': order_data.get('order_id', 'N/A'),
                        'is_trial': is_trial,
                        'trial_period_months': trial_months
                    }
                    
                    logger.info(f"🎯 Syncing membership to GHL: {membership_data}")
                    
                    # Sync to GHL with location_id
                    ghl_sync_result = sync_membership_purchase_to_ghl(
                        customer.email, 
                        membership_data,
                        location_id  # 🔥 CRITICAL: Pass location_id to GHL API
                    )
                    
                    sync_results['processed_memberships'].append({
                        'product_name': product_name,
                        'customer_email': customer.email,
                        'amount_paid': amount_paid,
                        'is_trial': is_trial,
                        'ghl_sync_result': ghl_sync_result
                    })
                    
                    logger.info(f"✅ GHL membership sync completed for {product_name}: {ghl_sync_result}")
                    
                else:
                    logger.info(f"⏭️ Skipping non-membership product: {product_name}")
                    sync_results['skipped_items'].append({
                        'product_name': product_name,
                        'reason': 'Not a membership product'
                    })
                    
            except Exception as e:
                error_msg = f"Error processing item {item.get('product_name', 'Unknown')}: {str(e)}"
                logger.error(error_msg)
                sync_results['errors'].append(error_msg)
        
        # Determine response status
        if sync_results['processed_memberships']:
            response_status = 'success'
            message = f"Successfully synced {len(sync_results['processed_memberships'])} membership(s) to GHL"
        elif sync_results['skipped_items'] and not sync_results['errors']:
            response_status = 'skipped' 
            message = "No membership products found to sync"
        else:
            response_status = 'error'
            message = "Failed to process membership sync"
        
        return Response({
            'status': response_status,
            'message': message,
            'results': sync_results
        })
        
    except Exception as e:
        logger.error(f"❌ Critical error in GHL membership sync: {str(e)}")
        return Response({'error': f'Critical error: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ==========================================
# SUBSCRIPTION WEBHOOK SYNC FUNCTIONS
# ==========================================

def has_subscription_data_changes(webhook_data):
    """
    Check if the webhook data contains subscription-related changes.
    
    Args:
        webhook_data (dict): The webhook data from WooCommerce
    
    Returns:
        bool: True if subscription-related data is present
    """
    if not isinstance(webhook_data, dict):
        return False
    
    # Check for subscription-related fields in webhook data
    subscription_indicators = [
        'meta_data',  # Contains subscription metadata
        'type',       # Product type might be subscription
        'variations', # Variable products might have subscription variations
    ]
    
    # Check if webhook contains any subscription indicators
    for indicator in subscription_indicators:
        if indicator in webhook_data:
            # For meta_data, check if it contains subscription keys
            if indicator == 'meta_data' and isinstance(webhook_data[indicator], list):
                for meta in webhook_data[indicator]:
                    if isinstance(meta, dict) and 'key' in meta:
                        key = meta.get('key', '')
                        if any(sub_key in key for sub_key in ['_subscription_', '_wcs_']):
                            logger.info(f"📅 Subscription metadata found in webhook: {key}")
                            return True
            
            # For type, check if it's a subscription type
            elif indicator == 'type':
                product_type = webhook_data[indicator]
                if product_type in ['subscription', 'variable-subscription']:
                    logger.info(f"📅 Subscription product type detected: {product_type}")
                    return True
            
            # Always check variations since they might have subscription data
            elif indicator == 'variations':
                logger.info(f"📅 Product has variations - will check for subscription metadata")
                return True
    
    # Also check if the product type suggests it might have subscription options
    product_type = webhook_data.get('type', 'simple')
    if product_type in ['simple', 'variable']:
        # Simple and variable products can have subscription options via WooCommerce Subscriptions plugin
        logger.info(f"📅 Product type '{product_type}' can have subscription options - checking metadata")
        return True
    
    return False


def extract_subscription_metadata(webhook_data):
    """
    Extract subscription metadata from WooCommerce webhook data.
    Supports both WooCommerce Subscriptions and WooCommerce All Products for Subscriptions (WCSATT).
    
    Args:
        webhook_data (dict): The webhook data from WooCommerce
    
    Returns:
        dict: Extracted subscription data
    """
    subscription_data = {
        'has_subscription': False,
        'subscription_price': None,
        'subscription_period': None,
        'subscription_interval': None,
        'subscription_sign_up_fee': None,
        'subscription_trial_period': None,
        'subscription_trial_length': None,
        'subscription_limit': None,
        'subscription_length': None,
        'one_time_shipping': None,
        'subscription_schemes': {},
        'subscription_discount': None,
        'plugin_type': None,  # 'wcs' or 'wcsatt'
        'meta_data': []
    }
    
    # Extract metadata
    meta_data = webhook_data.get('meta_data', [])
    if isinstance(meta_data, list):
        for meta in meta_data:
            if isinstance(meta, dict) and 'key' in meta and 'value' in meta:
                key = meta.get('key')
                value = meta.get('value')
                
                # Check for WooCommerce Subscriptions (standard) fields
                if '_subscription_' in key or '_wcs_' in key:
                    subscription_data['meta_data'].append(meta)
                    subscription_data['has_subscription'] = True
                    subscription_data['plugin_type'] = 'wcs'
                    
                    # Map specific keys to structured data
                    if key == '_subscription_price':
                        subscription_data['subscription_price'] = value
                    elif key == '_subscription_period':
                        subscription_data['subscription_period'] = value
                    elif key == '_subscription_period_interval':
                        try:
                            subscription_data['subscription_interval'] = int(value) if value else 1
                        except (ValueError, TypeError):
                            subscription_data['subscription_interval'] = 1
                    elif key == '_subscription_sign_up_fee':
                        subscription_data['subscription_sign_up_fee'] = value
                    elif key == '_subscription_trial_period':
                        subscription_data['subscription_trial_period'] = value
                    elif key == '_subscription_trial_length':
                        try:
                            subscription_data['subscription_trial_length'] = int(value) if value else None
                        except (ValueError, TypeError):
                            subscription_data['subscription_trial_length'] = None
                    elif key == '_subscription_limit':
                        subscription_data['subscription_limit'] = value
                    elif key == '_subscription_length':
                        subscription_data['subscription_length'] = value
                    elif key == '_subscription_one_time_shipping':
                        subscription_data['one_time_shipping'] = value
                
                # Check for WooCommerce All Products for Subscriptions (WCSATT) fields
                elif '_wcsatt_' in key or key == '_satt_data':
                    subscription_data['meta_data'].append(meta)
                    
                    if key == '_satt_data' and isinstance(value, dict):
                        # Extract WCSATT subscription schemes
                        subscription_schemes = value.get('subscription_schemes', {})
                        if subscription_schemes:
                            subscription_data['has_subscription'] = True
                            subscription_data['plugin_type'] = 'wcsatt'  # Override any previous plugin type
                            subscription_data['subscription_schemes'] = subscription_schemes
                            
                            # Extract discount from price_html if available
                            price_html = webhook_data.get('price_html', '')
                            if 'wcsatt-sub-discount' in price_html:
                                import re
                                discount_match = re.search(r'wcsatt-sub-discount[^>]*>([^<]+)', price_html)
                                if discount_match:
                                    import html
                                    discount_text = html.unescape(discount_match.group(1))  # Convert &#37; to %
                                    subscription_data['subscription_discount'] = discount_text
                            
                            # Store all subscription schemes - we'll process them in the frontend API
                            subscription_data['subscription_schemes'] = subscription_schemes
                            
                            # Set the first scheme as default for backward compatibility
                            first_scheme = next(iter(subscription_schemes.items()), None)
                            if first_scheme:
                                scheme_key, scheme_data = first_scheme
                                if scheme_key and '_' in scheme_key:
                                    try:
                                        interval_str, period = scheme_key.split('_', 1)
                                        interval = int(interval_str)
                                        
                                        subscription_data['subscription_period'] = period
                                        subscription_data['subscription_interval'] = interval
                                        
                                        # Calculate price with discount
                                        original_price = webhook_data.get('price')
                                        if original_price and subscription_data['subscription_discount']:
                                            try:
                                                discount_percent = float(subscription_data['subscription_discount'].replace('%', '').strip())
                                                discounted_price = float(original_price) * (1 - discount_percent / 100)
                                                subscription_data['subscription_price'] = str(round(discounted_price, 2))
                                            except (ValueError, TypeError):
                                                subscription_data['subscription_price'] = original_price
                                        else:
                                            subscription_data['subscription_price'] = original_price
                                    except (ValueError, TypeError) as e:
                                        logger.warning(f"Error parsing WCSATT scheme key '{scheme_key}': {e}")
                            
                            logger.info(f"📅 Found WCSATT subscription schemes: {list(subscription_schemes.keys())}")
                    
                    elif key.startswith('_wcsatt_'):
                        # Other WCSATT settings  
                        subscription_data['meta_data'].append(meta)
                        subscription_data['plugin_type'] = 'wcsatt'  # Override any previous plugin type
                        if key == '_wcsatt_force_subscription':
                            subscription_data['has_subscription'] = subscription_data['has_subscription'] or (value == 'yes')
    
    # Final check: If we have subscription_schemes, this must be WCSATT
    if subscription_data.get('subscription_schemes') and len(subscription_data['subscription_schemes']) > 0:
        subscription_data['plugin_type'] = 'wcsatt'
    
    # 🔧 CRITICAL FIX: Check if WCSATT is explicitly disabled
    # This must be done AFTER processing all meta_data to override has_subscription
    meta_data = webhook_data.get('meta_data', [])
    is_wcsatt_disabled = any(
        meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
        for meta in meta_data
    )
    
    if is_wcsatt_disabled:
        logger.info(f"❌ WCSATT disabled (_wcsatt_disabled=yes) - overriding has_subscription to False")
        subscription_data['has_subscription'] = False
        subscription_data['plugin_type'] = None  # Not a subscription product
        subscription_data['is_divi_managed'] = False
        subscription_data['subscription_schemes'] = {}
    
    # 🎨 Check if this is a Divi-managed subscription product
    # A product is Divi-managed if:
    # 1. Product type is "subscription" or "simple" (Divi products are always "Simple subscription")
    #    Variable products are NEVER Divi-managed — they use WCSATT on their variations
    # 2. Built with Divi Builder (ENABLED, not just metadata exists) AND
    # 3. Either:
    #    a) Has WCSATT subscription schemes (WCSATT enabled), OR
    #    b) Has native subscription metadata (fixed subscription product)
    product_type = webhook_data.get('type', '')
    is_divi_eligible_type = product_type in ['subscription', 'simple', 'simple-subscription']
    
    is_page_built_with_divi = any(
        meta.get('key') == '_et_pb_use_builder' and meta.get('value') == 'on'
        for meta in meta_data
    )
    
    has_satt_schemes = subscription_data.get('subscription_schemes') and len(subscription_data.get('subscription_schemes', {})) > 0
    
    # Check if this has native subscription metadata (regardless of WCSATT)
    has_native_subscription = (
        subscription_data.get('subscription_price') or
        subscription_data.get('subscription_trial_length')
    )
    
    # Mark as Divi-managed if eligible type AND built with Divi AND has subscription data
    if is_divi_eligible_type and is_page_built_with_divi and (has_satt_schemes or has_native_subscription):
        if has_satt_schemes and not is_wcsatt_disabled:
            logger.info(f"🎨 Divi-managed subscription product detected (WCSATT schemes, type={product_type})")
            subscription_data['is_divi_managed'] = True
            subscription_data['plugin_type'] = 'divi'
        elif has_native_subscription:
            logger.info(f"🎨 Divi-managed subscription product detected (Native WooCommerce Subscriptions, type={product_type})")
            subscription_data['is_divi_managed'] = True
            subscription_data['plugin_type'] = 'divi'
            subscription_data['has_subscription'] = True  # Override WCSATT disabled check for native subscriptions
        
        if not subscription_data.get('has_subscription'):
            logger.info(f"🎨 Divi product has no pre-defined subscription - will be handled at checkout")
    else:
        # Not a Divi-managed subscription product
        subscription_data['is_divi_managed'] = False
        if not is_divi_eligible_type and is_page_built_with_divi:
            logger.info(f"✅ Product uses Divi builder but type '{product_type}' is not eligible for Divi subscription management")
        elif is_wcsatt_disabled:
            logger.info(f"✅ Product is NOT Divi-managed (not built with Divi or no subscription data)")
    
    logger.info(f"📅 Extracted subscription metadata ({subscription_data.get('plugin_type', 'unknown')} plugin): {subscription_data}")
    return subscription_data


def sync_product_subscription_metadata(product, webhook_data):
    """
    Sync subscription metadata to ProductSimple, ProductVariation, and ProductSubscription records.
    
    Args:
        product (Product): The local Product instance
        webhook_data (dict): The webhook data from WooCommerce
    
    Returns:
        dict: Sync status and statistics
    """
    sync_status = {
        'success': False,
        'simple_updated': False,
        'variations_updated': 0,
        'subscription_records_created': 0,
        'subscription_records_updated': 0,
        'subscription_records_removed': 0,  # 🔧 NEW: Track cleanup operations
        'cleanup_performed': False,
        'woo_data_cleaned': False,
        'errors': []
    }
    
    try:
        # Extract subscription metadata from webhook
        subscription_data = extract_subscription_metadata(webhook_data)
        
        if not subscription_data['has_subscription']:
            # 🎨 Check if this is a Divi product before assuming no subscription
            is_divi_product = subscription_data.get('is_divi_managed', False)
            
            if is_divi_product:
                logger.info(f"🎨 Divi product detected - subscription metadata already stored, skipping cleanup")
                sync_status['success'] = True
                return sync_status
            
            logger.info(f"📅 No subscription metadata found for product {product.id}")
            
            # 🧹 CLEANUP: Check if this product is explicitly disabled for subscriptions
            meta_data = webhook_data.get('meta_data', [])
            is_wcsatt_disabled = any(
                meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
                for meta in meta_data
            )
            
            if is_wcsatt_disabled:
                logger.info(f"🗑️ Product {product.name} is set to 'Sell one-time only' - performing cleanup")
                
                # Remove existing ProductSubscription records
                from .models import ProductSubscription, ProductSimple
                existing_subscriptions = ProductSubscription.objects.filter(product=product)
                subscription_count = existing_subscriptions.count()
                subscription_removed = False
                
                if subscription_count > 0:
                    existing_subscriptions.delete()
                    subscription_removed = True
                    logger.info(f"🗑️ Removed {subscription_count} ProductSubscription records")
                
                # Clean up subscription and Divi data from ProductSimple woo_data
                simple_product = ProductSimple.objects.filter(product=product).first()
                woo_data_cleaned = False
                
                if simple_product and simple_product.woo_data:
                    woo_data = simple_product.woo_data
                    if isinstance(woo_data, dict):
                        # 🧹 Remove subscription_metadata completely (including Divi data)
                        if 'subscription_metadata' in woo_data:
                            sub_meta = woo_data['subscription_metadata']
                            # Check if it has is_divi_managed flag
                            if isinstance(sub_meta, dict) and sub_meta.get('is_divi_managed'):
                                logger.info(f"🗑️ Removing incorrect Divi-managed flag (WCSATT is disabled)")
                            del woo_data['subscription_metadata']
                            woo_data_cleaned = True
                            logger.info(f"🗑️ Removed subscription_metadata (product is one-time only)")
                        
                        # Clean meta_data - remove all subscription and WCSATT-related fields
                        meta_data_list = woo_data.get('meta_data', [])
                        original_count = len(meta_data_list)
                        meta_data_list = [meta for meta in meta_data_list if meta.get('key') not in [
                            '_wcsatt_schemes',
                            '_wcsatt_force_subscription',
                            '_wcsatt_disabled',
                            '_satt_data',
                            '_subscription_price',
                            '_subscription_period',
                            '_subscription_period_interval',
                            '_subscription_length',
                            '_subscription_trial_period',
                            '_subscription_trial_length',
                            '_subscription_sign_up_fee',
                            '_subscription_one_time_shipping'
                        ]]
                        
                        if len(meta_data_list) < original_count:
                            woo_data['meta_data'] = meta_data_list
                            woo_data_cleaned = True
                            logger.info(f"🗑️ Cleaned {original_count - len(meta_data_list)} subscription/WCSATT fields from meta_data")
                        
                        if woo_data_cleaned:
                            simple_product.woo_data = woo_data
                            simple_product.save(update_fields=['woo_data'])
                            logger.info(f"✅ Cleaned all subscription and Divi data from woo_data")
                
                if subscription_removed or woo_data_cleaned:
                    sync_status['success'] = True
                    sync_status['cleanup_performed'] = True
                    sync_status['subscription_records_removed'] = subscription_count if subscription_removed else 0
                    sync_status['woo_data_cleaned'] = woo_data_cleaned
                    logger.info(f"✅ Webhook cleanup completed for disabled product {product.name}")
                    return sync_status
            
            # 🔧 CRITICAL FIX: For variable-subscription products, subscription data lives on
            # the VARIATIONS, not the parent product. Don't return early — continue to the
            # variation processing section below which fetches each variation individually.
            if product.product_type not in ['variable', 'variable-subscription', 'variable_subscription']:
                sync_status['success'] = True  # Not an error, just no subscription data
                return sync_status
            else:
                logger.info(f"📅 Parent has no subscription meta, but product is {product.product_type} — continuing to sync variation subscription data")
        
        logger.info(f"📅 Syncing subscription metadata for product {product.name} (ID: {product.id})")
        
        # Handle Simple Products
        if product.product_type in ['simple', 'subscription']:
            try:
                from .models import ProductSimple, ProductSubscription
                
                # Update ProductSimple record with subscription metadata
                simple_product = ProductSimple.objects.filter(product=product).first()
                if simple_product:
                    # Add subscription metadata to woo_data
                    if not isinstance(simple_product.woo_data, dict):
                        simple_product.woo_data = {}
                    
                    simple_product.woo_data['subscription_metadata'] = subscription_data
                    simple_product.woo_data['last_subscription_sync'] = timezone.now().isoformat()
                    simple_product.save(update_fields=['woo_data'])
                    
                    sync_status['simple_updated'] = True
                    logger.info(f"📅 Updated ProductSimple with subscription metadata for {product.name}")
                
                # Create/update ProductSubscription record if subscription data exists
                if subscription_data['subscription_price'] or subscription_data['subscription_period']:
                    subscription_obj, created = ProductSubscription.objects.get_or_create(
                        product=product,
                        defaults={
                            'price': subscription_data['subscription_price'],
                            'period': subscription_data['subscription_period'] or 'month',
                            'interval': subscription_data['subscription_interval'] or 1,
                            'trial_period': subscription_data['subscription_trial_period'],
                            'trial_length': subscription_data['subscription_trial_length'],
                            'sign_up_fee': subscription_data['subscription_sign_up_fee'],
                            'woo_data': webhook_data
                        }
                    )
                    
                    if created:
                        sync_status['subscription_records_created'] += 1
                        logger.info(f"📅 Created ProductSubscription record for {product.name}")
                    else:
                        # Update existing record
                        if subscription_data['subscription_price']:
                            subscription_obj.price = subscription_data['subscription_price']
                        if subscription_data['subscription_period']:
                            subscription_obj.period = subscription_data['subscription_period']
                        if subscription_data['subscription_interval']:
                            subscription_obj.interval = subscription_data['subscription_interval']
                        if subscription_data['subscription_trial_period']:
                            subscription_obj.trial_period = subscription_data['subscription_trial_period']
                        if subscription_data['subscription_trial_length']:
                            subscription_obj.trial_length = subscription_data['subscription_trial_length']
                        if subscription_data['subscription_sign_up_fee']:
                            subscription_obj.sign_up_fee = subscription_data['subscription_sign_up_fee']
                        
                        subscription_obj.woo_data = webhook_data
                        subscription_obj.save()
                        
                        sync_status['subscription_records_updated'] += 1
                        logger.info(f"📅 Updated ProductSubscription record for {product.name}")
                
            except Exception as simple_error:
                error_msg = f"Error syncing simple product subscription metadata: {str(simple_error)}"
                sync_status['errors'].append(error_msg)
                logger.error(f"❌ {error_msg}")
        
        # Handle Variable Products - fetch and sync variations
        elif product.product_type in ['variable', 'variable-subscription', 'variable_subscription']:
            try:
                from .woocommerce import WooCommerceAPI
                from .models import ProductVariation, ProductSubscription
                
                # Get variations from WooCommerce API to get their subscription metadata
                wc_api = WooCommerceAPI()
                woo_variations = wc_api.get_product_variations(product.woo_product_id)
                
                if woo_variations:
                    for woo_variation in woo_variations:
                        variation_id = woo_variation.get('id')
                        
                        # Find local variation record
                        local_variation = ProductVariation.objects.filter(
                            product=product,
                            woo_variation_id=variation_id
                        ).first()
                        
                        if local_variation:
                            # Extract subscription metadata from this variation
                            variation_subscription_data = extract_subscription_metadata(woo_variation)
                            
                            # Update ProductVariation with subscription metadata
                            if not isinstance(local_variation.woo_data, dict):
                                local_variation.woo_data = {}
                            
                            local_variation.woo_data['subscription_metadata'] = variation_subscription_data
                            local_variation.woo_data['last_subscription_sync'] = timezone.now().isoformat()
                            
                            # 🔧 CRITICAL FIX: Generate _wcsatt_schemes in meta_data for frontend
                            # The frontend (ProductSummaryModal) looks for _wcsatt_schemes in
                            # variation.woo_data.meta_data to show the subscription dropdown.
                            # For products using global subscription plans, the variation's
                            # WooCommerce API data won't contain _wcsatt_schemes, so we must
                            # generate them from the parent product's subscription_schemes.
                            try:
                                # Check if variation already has _wcsatt_schemes from WooCommerce
                                existing_meta = local_variation.woo_data.get('meta_data', [])
                                has_existing_schemes = any(
                                    isinstance(m, dict) and m.get('key') == '_wcsatt_schemes'
                                    for m in existing_meta
                                )
                                
                                if not has_existing_schemes:
                                    # Use parent product's subscription schemes to generate variation schemes
                                    parent_schemes = subscription_data.get('subscription_schemes', {})
                                    parent_has_subscription = subscription_data.get('has_subscription', False)
                                    
                                    if parent_has_subscription and parent_schemes:
                                        # Get variation price
                                        base_price = float(local_variation.price) if local_variation.price else 0
                                        if base_price == 0:
                                            base_price = float(local_variation.woo_data.get('price', 0) or 0)
                                        if base_price == 0:
                                            base_price = float(local_variation.woo_data.get('regular_price', 0) or 0)
                                        
                                        if base_price > 0:
                                            # Extract discount percentage from parent data
                                            discount_str = subscription_data.get('subscription_discount', '5%')
                                            try:
                                                discount = float(str(discount_str).replace('%', '').strip()) if discount_str else 5
                                            except (ValueError, TypeError):
                                                discount = 5
                                            
                                            discounted_price = base_price * (1 - discount / 100)
                                            
                                            # Build _wcsatt_schemes from parent scheme keys
                                            wcsatt_schemes = []
                                            for position, scheme_key in enumerate(sorted(parent_schemes.keys())):
                                                if '_' in scheme_key:
                                                    try:
                                                        interval_str, period = scheme_key.split('_', 1)
                                                        interval = int(interval_str)
                                                        wcsatt_schemes.append({
                                                            "position": str(position),
                                                            "subscription_price": str(round(discounted_price, 2)),
                                                            "subscription_length": "0",
                                                            "subscription_period": period,
                                                            "subscription_discount": discount,
                                                            "subscription_sale_price": "",
                                                            "subscription_regular_price": str(base_price),
                                                            "subscription_pricing_method": "inherit",
                                                            "subscription_period_interval": str(interval),
                                                            "subscription_payment_sync_date": 0
                                                        })
                                                    except (ValueError, TypeError):
                                                        continue
                                            
                                            if wcsatt_schemes:
                                                # Remove any existing _wcsatt_schemes entries
                                                meta_data = [m for m in existing_meta if not (isinstance(m, dict) and m.get('key') == '_wcsatt_schemes')]
                                                meta_data.append({
                                                    "key": "_wcsatt_schemes",
                                                    "value": wcsatt_schemes
                                                })
                                                local_variation.woo_data['meta_data'] = meta_data
                                                logger.info(f"📅 Generated {len(wcsatt_schemes)} _wcsatt_schemes for variation {variation_id} (price: ${base_price}, discount: {discount}%)")
                            except Exception as scheme_error:
                                logger.warning(f"⚠️ Error generating _wcsatt_schemes for variation {variation_id}: {str(scheme_error)}")
                            
                            local_variation.save(update_fields=['woo_data'])
                            
                            sync_status['variations_updated'] += 1
                            logger.info(f"📅 Updated ProductVariation {variation_id} with subscription metadata")
                            
                            # Create ProductSubscription record for variation if it has subscription data
                            if variation_subscription_data['has_subscription'] and (
                                variation_subscription_data['subscription_price'] or 
                                variation_subscription_data['subscription_period']
                            ):
                                # For variations, we create a ProductSubscription record linked to the parent product
                                # but store variation-specific data in woo_data
                                variation_woo_data = dict(woo_variation)
                                variation_woo_data['variation_id'] = variation_id
                                variation_woo_data['subscription_metadata'] = variation_subscription_data
                                
                                subscription_obj, created = ProductSubscription.objects.get_or_create(
                                    product=product,
                                    defaults={
                                        'price': variation_subscription_data['subscription_price'],
                                        'period': variation_subscription_data['subscription_period'] or 'month',
                                        'interval': variation_subscription_data['subscription_interval'] or 1,
                                        'trial_period': variation_subscription_data['subscription_trial_period'],
                                        'trial_length': variation_subscription_data['subscription_trial_length'],
                                        'sign_up_fee': variation_subscription_data['subscription_sign_up_fee'],
                                        'woo_data': variation_woo_data
                                    }
                                )
                                
                                if created:
                                    sync_status['subscription_records_created'] += 1
                                    logger.info(f"📅 Created ProductSubscription record for variation {variation_id}")
                                else:
                                    # Update with latest variation data (keep the first variation's data as primary)
                                    if not subscription_obj.woo_data.get('variations_data'):
                                        subscription_obj.woo_data['variations_data'] = []
                                    
                                    # Add this variation's data
                                    subscription_obj.woo_data['variations_data'].append(variation_woo_data)
                                    subscription_obj.save()
                                    
                                    sync_status['subscription_records_updated'] += 1
                                    logger.info(f"📅 Updated ProductSubscription with variation {variation_id} data")
                
            except Exception as variation_error:
                error_msg = f"Error syncing variable product subscription metadata: {str(variation_error)}"
                sync_status['errors'].append(error_msg)
                logger.error(f"❌ {error_msg}")
        
        # Mark as successful if no errors occurred
        sync_status['success'] = len(sync_status['errors']) == 0
        
        logger.info(f"📅 Subscription metadata sync completed for product {product.name}: {sync_status}")
        return sync_status
        
    except Exception as e:
        error_msg = f"Critical error in subscription metadata sync: {str(e)}"
        sync_status['errors'].append(error_msg)
        logger.error(f"❌ {error_msg}")
        return sync_status
