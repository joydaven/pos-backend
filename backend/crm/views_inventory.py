from django.shortcuts import get_object_or_404
from django.db import models
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, viewsets
from .models import Product, InventoryLocation, ProductInventory, ProductVariation
from .serializers_inventory import InventoryLocationSerializer, ProductInventorySerializer, ProductWithInventorySerializer
from .woocommerce import WooCommerceAPI

class InventoryLocationViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing inventory locations
    """
    queryset = InventoryLocation.objects.all()
    serializer_class = InventoryLocationSerializer
    permission_classes = [IsAuthenticated]

class ProductInventoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing product inventory across locations
    """
    queryset = ProductInventory.objects.all()
    serializer_class = ProductInventorySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = ProductInventory.objects.all()
        product_id = self.request.query_params.get('product_id', None)
        location_id = self.request.query_params.get('location_id', None)
        
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        if location_id:
            queryset = queryset.filter(location_id=location_id)
            
        return queryset

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_inventory(request, product_id):
    """
    Get inventory information for a specific product across all locations
    """
    try:
        product = get_object_or_404(Product, id=product_id)
        serializer = ProductWithInventorySerializer(product)
        return Response(serializer.data)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_atum_locations(request, product_id):
    """
    Get ATUM inventory locations assigned to a specific product or variation
    Returns only locations that meet ALL of the following criteria:
    1. Have a valid ATUM location ID (not null)
    2. Have been synced from ATUM inventory (notes contain 'Synced from ATUM inventory')
    3. Are associated with the specified product
    4. If variation_id is provided, filter for that specific variation
    
    Query parameters:
    - variation_id: Optional WooCommerce variation ID to filter locations for specific variation
    - include_stock: Optional boolean to include stock quantities (default: false)
    
    This ensures that only legitimate ATUM locations are returned, excluding regular WooCommerce
    inventory locations like 'Default Location' or 'Main Warehouse'.
    """
    try:
        # Handle credited service IDs by extracting the actual UUID
        actual_product_id = product_id
        if product_id.startswith('credited-service-'):
            actual_product_id = product_id.replace('credited-service-', '')
            # For credited services, return empty locations since they don't have inventory
            return Response({
                'locations': [],
                'data_source': 'credited_service',
                'message': 'Credited services do not have inventory locations'
            })
        
        # Try to find product by UUID first, then by WooCommerce ID
        product = None
        
        # Check if it's a valid UUID format
        import re
        uuid_pattern = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)
        
        if uuid_pattern.match(str(actual_product_id)):
            # It's a UUID, try to find by id
            try:
                product = Product.objects.get(id=actual_product_id)
            except Product.DoesNotExist:
                pass
        
        # If not found by UUID, try by WooCommerce ID
        if not product:
            try:
                woo_id = int(actual_product_id)
                product = Product.objects.get(woo_product_id=woo_id)
            except (ValueError, Product.DoesNotExist):
                pass
        
        if not product:
            return Response({
                'error': f'Product not found with ID: {product_id}',
                'locations': []
            }, status=404)
        
        variation_id = request.GET.get('variation_id')
        include_stock = request.GET.get('include_stock', 'false').lower() == 'true'
        
        # Base filter for ATUM inventory locations
        base_filter = {
            'product': product,
            'location__atum_location_id__isnull': False,
            'notes__icontains': 'Synced from ATUM inventory'
        }
        
        if variation_id:
            # Filter for specific variation using the variation field
            try:
                variation = ProductVariation.objects.get(
                    product=product,
                    woo_variation_id=variation_id
                )
                base_filter['variation'] = variation
            except ProductVariation.DoesNotExist:
                return Response({
                    'error': f'Variation {variation_id} not found for product {product_id}'
                }, status=404)
            
            product_inventories = ProductInventory.objects.filter(
                **base_filter
            ).select_related('location')
        else:
            # Filter for product-level inventory (exclude variation-specific records)
            base_filter['variation__isnull'] = True
            product_inventories = ProductInventory.objects.filter(
                **base_filter
            ).select_related('location')
        
        if include_stock:
            # Return locations with live ATUM stock quantities
            locations_with_stock = []
            
            try:
                # Initialize WooCommerce API to fetch live stock data
                wc_api = WooCommerceAPI()
                
                # Determine which product ID to use for WooCommerce API
                if variation_id:
                    # For variations, use the variation ID
                    wc_product_id = variation_id
                else:
                    # For simple products, use the main product's WooCommerce ID
                    wc_product_id = product.woo_product_id
                
                # Get WooCommerce product data with cache-busting
                import time
                cache_buster = int(time.time())
                
                # Try to get fresh product data with cache-busting
                try:
                    response = wc_api.wcapi.get(f"products/{wc_product_id}?_={cache_buster}")
                    if response.ok:
                        actual_product_data = response.json()
                    else:
                        product_data = wc_api.get_product(wc_product_id)
                        actual_product_data = product_data.get('data', product_data) if product_data else {}
                except:
                    product_data = wc_api.get_product(wc_product_id)
                    actual_product_data = product_data.get('data', product_data) if product_data else {}
                
                # Try ATUM inventory API with cache-busting
                try:
                    response = wc_api.wcapi.get(f"products/{wc_product_id}/inventories?_={cache_buster}")
                    if response.ok:
                        atum_inventories = response.json()
                    else:
                        atum_inventories = wc_api.get_product_inventories(wc_product_id)
                except:
                    atum_inventories = wc_api.get_product_inventories(wc_product_id)
                
                # Get WooCommerce stock information
                wc_stock_status = actual_product_data.get('stock_status', 'outofstock')
                wc_stock_quantity = actual_product_data.get('stock_quantity', 0)
                wc_manage_stock = actual_product_data.get('manage_stock', False)
                
                # Create location-specific stock mapping from ATUM inventory
                atum_stock_map = {}
                for atum_inv in atum_inventories:
                    location_id = atum_inv.get('id')
                    location_name = atum_inv.get('name', '')
                    atum_meta = atum_inv.get('meta_data', {})
                    atum_stock_status = atum_meta.get('stock_status', 'outofstock')
                    atum_stock_qty = atum_meta.get('stock_quantity')
                    atum_manage_stock = atum_meta.get('manage_stock', False)
                    
                    # Use ATUM location stock status and quantity
                    stock_status = atum_stock_status
                    
                    # Use actual ATUM stock quantity if available
                    if atum_stock_qty is not None:
                        try:
                            stock_quantity = int(atum_stock_qty)
                        except (ValueError, TypeError):
                            stock_quantity = 0
                    else:
                        # If no quantity available, use stock status to determine display
                        if stock_status == 'instock':
                            stock_quantity = None  # Will be handled below
                        else:
                            stock_quantity = 0
                    
                    # Handle special case where quantity is None
                    if stock_quantity is None:
                        # Show as null/None for frontend to display as "-"
                        stock_quantity = None
                    # Use actual quantities - trust ATUM data
                    
                    atum_stock_map[location_id] = {
                        'stock_quantity': stock_quantity,
                        'stock_status': stock_status,
                        'location_name': location_name,
                        'manage_stock': atum_manage_stock
                    }
                
                # Create name-based mapping since ATUM API returns different IDs than our database
                atum_name_map = {}
                for atum_id, atum_data in atum_stock_map.items():
                    location_name = atum_data['location_name']
                    atum_name_map[location_name] = atum_data

                # Update the response with location-specific stock data
                locations_with_stock = []
                for inventory in product_inventories:
                    location_data = InventoryLocationSerializer(inventory.location).data
                    location_name = inventory.location.name
                    
                    # Use database inventory quantity instead of live ATUM data
                    location_data['stock_quantity'] = inventory.quantity
                    
                    # Determine stock status based on database quantity
                    if inventory.quantity is None:
                        location_data['stock_status'] = 'instock'  # No quantity tracking
                    elif inventory.quantity > 0:
                        location_data['stock_status'] = 'instock'
                    else:
                        location_data['stock_status'] = 'outofstock'
                    
                    location_data['data_source'] = 'database_inventory'
                    locations_with_stock.append(location_data)
                
            except Exception as e:
                # If WooCommerce API fails, fall back to database values
                print(f'Error fetching live WooCommerce data: {e}')
                locations_with_stock = []
                for inventory in product_inventories:
                    location_data = InventoryLocationSerializer(inventory.location).data
                    location_data['stock_quantity'] = inventory.quantity
                    location_data['data_source'] = 'database_fallback'
                    locations_with_stock.append(location_data)
            
            return Response(locations_with_stock)
        else:
            # Extract unique locations from the inventory records (original behavior)
            locations = []
            seen_location_ids = set()
            
            for inventory in product_inventories:
                location = inventory.location
                if location.id not in seen_location_ids:
                    locations.append(location)
                    seen_location_ids.add(location.id)
            
            # 🏢 FALLBACK: If no product-specific locations found, return all ATUM locations
            if len(locations) == 0:
                print(f'No product-specific ATUM locations found for product {product_id}, returning all ATUM locations')
                # Get all ATUM locations (locations with atum_location_id set)
                all_atum_locations = InventoryLocation.objects.filter(
                    atum_location_id__isnull=False
                ).distinct()
                
                if all_atum_locations.exists():
                    serializer = InventoryLocationSerializer(all_atum_locations, many=True)
                    response_data = serializer.data
                    # Add flag to indicate this is a fallback
                    for loc in response_data:
                        loc['is_fallback'] = True
                    return Response(response_data)
            
            # Serialize the locations
            serializer = InventoryLocationSerializer(locations, many=True)
            return Response(serializer.data)
        
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_product_inventory(request, product_id, location_id):
    """
    Update inventory for a specific product at a specific location
    """
    try:
        product = get_object_or_404(Product, id=product_id)
        location = get_object_or_404(InventoryLocation, id=location_id)
        
        # Get or create inventory record
        inventory, created = ProductInventory.objects.get_or_create(
            product=product,
            location=location,
            defaults={
                'quantity': request.data.get('quantity', 0),
                'reorder_level': request.data.get('reorder_level', 0),
                'reorder_quantity': request.data.get('reorder_quantity', 0),
                'is_available': request.data.get('is_available', True),
                'notes': request.data.get('notes', '')
            }
        )
        
        if not created:
            # Update existing record
            inventory.quantity = request.data.get('quantity', inventory.quantity)
            inventory.reorder_level = request.data.get('reorder_level', inventory.reorder_level)
            inventory.reorder_quantity = request.data.get('reorder_quantity', inventory.reorder_quantity)
            inventory.is_available = request.data.get('is_available', inventory.is_available)
            inventory.notes = request.data.get('notes', inventory.notes)
            inventory.save()
            
        serializer = ProductInventorySerializer(inventory)
        return Response(serializer.data)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
