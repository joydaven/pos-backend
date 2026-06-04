"""
Bundle product API views for serving bundle data from local database
"""

import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Product, ProductBundle, ProductVariation

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def batch_product_details(request):
    """
    Batch fetch product details for multiple WooCommerce product IDs from the LOCAL database.
    
    Returns data in the SAME shape as the individual /api/product/{woo_id}/ endpoint
    (which calls WooCommerce API), but sourced entirely from local DB — no external calls.
    
    This replaces N individual WooCommerce API calls with a single DB query.
    
    Request body: { "woo_product_ids": [5765, 5777, 5783, ...] }
    Response: { "products": { "5765": { ...same shape as WooCommerce response... }, ... } }
    """
    try:
        woo_ids = request.data.get('woo_product_ids', [])
        if not woo_ids:
            return Response({'error': 'woo_product_ids is required'}, status=400)
        
        # Single query to get all products
        products = Product.objects.filter(woo_product_id__in=woo_ids)
        products_by_woo_id = {p.woo_product_id: p for p in products}
        
        # Single query to get all variations for variable products
        variable_product_uuids = [
            p.id for p in products_by_woo_id.values() 
            if p.product_type in ('variable', 'variable_subscription')
        ]
        variations_by_product = {}
        if variable_product_uuids:
            all_variations = ProductVariation.objects.filter(product_id__in=variable_product_uuids)
            for v in all_variations:
                pid = v.product_id
                if pid not in variations_by_product:
                    variations_by_product[pid] = []
                variations_by_product[pid].append(v)
        
        # Build response matching WooCommerce API shape
        result = {}
        for woo_id in woo_ids:
            woo_id = int(woo_id)
            product = products_by_woo_id.get(woo_id)
            
            if not product:
                result[str(woo_id)] = {
                    'status': 'error',
                    'message': f'Product {woo_id} not found'
                }
                continue
            
            # Build attributes list from variations' woo_data
            # This matches the WooCommerce API attributes format
            attributes_map = {}
            product_variations = variations_by_product.get(product.id, [])
            
            for v in product_variations:
                if v.woo_data and 'attributes' in v.woo_data:
                    for attr in v.woo_data['attributes']:
                        attr_name = attr.get('name', '')
                        attr_slug = attr.get('slug', '')
                        attr_option = attr.get('option', '')
                        attr_id = attr.get('id', 0)
                        
                        if attr_name not in attributes_map:
                            attributes_map[attr_name] = {
                                'id': attr_id,
                                'name': attr_name,
                                'slug': attr_slug,
                                'position': 0,
                                'visible': True,
                                'variation': True,
                                'options': set()
                            }
                        attributes_map[attr_name]['options'].add(attr_option)
            
            # Convert sets to sorted lists
            attributes = []
            for attr_info in attributes_map.values():
                attributes.append({
                    'id': attr_info['id'],
                    'name': attr_info['name'],
                    'slug': attr_info['slug'],
                    'position': attr_info['position'],
                    'visible': attr_info['visible'],
                    'variation': attr_info['variation'],
                    'options': sorted(list(attr_info['options']))
                })
            
            # Build variation IDs list (matching WooCommerce format: array of integers)
            variation_ids = [v.woo_variation_id for v in product_variations if v.woo_variation_id]
            
            # Build product data matching WooCommerce API response shape
            product_data = {
                'id': woo_id,
                'name': product.name,
                'type': product.product_type,
                'price': str(float(product.price)) if product.price else '0',
                'regular_price': str(float(product.regular_price)) if product.regular_price else '',
                'sale_price': str(float(product.sale_price)) if product.sale_price else '',
                'sku': '',
                'stock_status': product.stock_status or 'instock',
                'stock_quantity': product.stock_quantity,
                'attributes': attributes,
                'variations': variation_ids,
                'tags': [],  # Tags not stored locally, but frontend handles empty gracefully
                'local_data': {
                    'id': str(product.id),
                    'stock_quantity': product.stock_quantity,
                    'created_at': product.created_at.isoformat() if product.created_at else None,
                    'updated_at': product.updated_at.isoformat() if product.updated_at else None
                }
            }
            
            # Wrap in same shape as get_product_detail: { status, product }
            result[str(woo_id)] = {
                'status': 'success',
                'product': product_data
            }
        
        return Response({'products': result})
        
    except Exception as e:
        logger.error(f"Error in batch_product_details: {str(e)}", exc_info=True)
        return Response({'error': str(e)}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def batch_variation_lookup(request):
    """
    Batch lookup variations for multiple products at once from the LOCAL database.
    
    Returns data in the SAME shape as the individual get_variation_id_by_attributes endpoint,
    but for ALL products and ALL their variation options in a single DB query.
    
    This replaces N individual getVariationIdByAttributes calls.
    
    Request body: { "product_uuids": ["uuid1", "uuid2", ...] }
    Response: { "variations": { "uuid1": [ { variation_id, variation_name, attributes, price, sku }, ... ], ... } }
    """
    try:
        product_uuids = request.data.get('product_uuids', [])
        if not product_uuids:
            return Response({'error': 'product_uuids is required'}, status=400)
        
        # Single query to get ALL variations for all requested products
        all_variations = ProductVariation.objects.filter(product_id__in=product_uuids)
        
        # Group by product UUID
        result = {}
        for v in all_variations:
            pid = str(v.product_id)
            if pid not in result:
                result[pid] = []
            
            attrs = []
            if v.woo_data and 'attributes' in v.woo_data:
                attrs = v.woo_data['attributes']
            
            # Build variation name from attributes (e.g. "Single" or "3 series")
            var_name = ' / '.join([a.get('option', '') for a in attrs]) if attrs else ''
            
            # Match the shape returned by get_variation_id_by_attributes
            result[pid].append({
                'variation_id': v.woo_variation_id,
                'variation_name': var_name,
                'attributes': attrs,
                'price': str(float(v.price)) if v.price else '0',
                'sku': v.sku or ''
            })
        
        # Ensure all requested UUIDs have an entry (even if empty)
        for uuid in product_uuids:
            if str(uuid) not in result:
                result[str(uuid)] = []
        
        return Response({'variations': result})
        
    except Exception as e:
        logger.error(f"Error in batch_variation_lookup: {str(e)}", exc_info=True)
        return Response({'error': str(e)}, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_bundle_product(request, woo_product_id):
    """
    Get a bundle product's data from the local database instead of WooCommerce.
    This prevents 404 errors when bundled products don't exist in our database.
    
    Args:
        request: The HTTP request
        woo_product_id: The WooCommerce product ID
        
    Returns:
        Response with bundle product data from our local database
    """
    try:
        # Try to find the product in our local database
        try:
            product = Product.objects.get(woo_product_id=woo_product_id)
        except Product.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Product with WooCommerce ID {woo_product_id} not found in local database'
            }, status=404)
        
        # Check if this is a bundle product
        if product.product_type != 'bundle':
            return Response({
                'status': 'error',
                'message': f'Product with ID {woo_product_id} is not a bundle product'
            }, status=400)
        
        # Get the bundle data from our local database
        try:
            product_bundle = ProductBundle.objects.get(product_id=product.id)
            bundle_data = product_bundle.woo_data
        except ProductBundle.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Bundle data not found for product {woo_product_id}'
            }, status=404)
        
        # Prepare the response with product and bundle data
        response_data = {
            'status': 'success',
            'product': {
                'id': woo_product_id,
                'name': product.name,
                'type': product.product_type,
                'price': product.price,
                'regular_price': product.regular_price,
                'description': product.description,
                'short_description': product.short_description,
                'bundle_data': bundle_data,
                'local_data': {
                    'id': str(product.id),
                    'stock_quantity': product.stock_quantity,
                    'created_at': product.created_at.isoformat() if product.created_at else None,
                    'updated_at': product.updated_at.isoformat() if product.updated_at else None
                }
            }
        }
        
        return Response(response_data)
        
    except Exception as e:
        logger.error(f"Error in get_bundle_product: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'Error fetching bundle product: {str(e)}'
        }, status=500)
