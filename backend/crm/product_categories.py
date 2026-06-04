from django.db import connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Product
from .woocommerce import WooCommerceAPI
import logging
import json

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_categories(request):
    """
    Get all unique product categories from the database
    
    This endpoint executes a direct SQL query to extract all unique categories
    from the crm_product table, handling the JSON structure of the categories field.
    
    It also returns detailed information about how categories are stored to help
    with debugging and frontend implementation.
    """
    try:
        from django.core.cache import cache
        
        # Get category parameter if provided
        category_filter = request.query_params.get('category', None)
        
        # Check Redis cache first (10 min TTL)
        cache_key = f"product_categories:{category_filter or 'all'}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)
        
        with connection.cursor() as cursor:
            # SQL query to extract and flatten categories from JSONField
            cursor.execute("""
                SELECT DISTINCT jsonb_array_elements_text(categories) as category
                FROM crm_product
                WHERE categories IS NOT NULL AND categories != '[]'::jsonb
                ORDER BY category
            """)
            
            # Fetch all unique categories
            categories = [row[0] for row in cursor.fetchall()]
            
            logger.info(f"Retrieved {len(categories)} unique product categories")
            
            # If a category filter is provided, get sample products for debugging
            sample_products = []
            if category_filter:
                # Get sample products with this category
                products = Product.objects.filter(categories__contains=[category_filter])[:5]
                
                for product in products:
                    sample_products.append({
                        'id': str(product.id),
                        'name': product.name,
                        'categories': product.categories,
                        'categories_type': str(type(product.categories))
                    })
            
            result = {
                'categories': categories,
                'sample_products': sample_products,
                'category_filter': category_filter,
                'debug_info': {
                    'categories_count': len(categories),
                    'sample_category_format': json.dumps(categories[0]) if categories else None
                }
            }
            cache.set(cache_key, result, 600)
            return Response(result)
            
    except Exception as e:
        logger.error(f"Error retrieving product categories: {str(e)}")
        return Response({
            'error': 'Failed to retrieve product categories',
            'detail': str(e)
        }, status=500)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_hierarchical_categories(request):
    """
    Get hierarchical product categories from WooCommerce API
    
    This endpoint fetches categories with parent-child relationships from WooCommerce,
    including id, name, parent fields to represent the hierarchy.
    """
    try:
        from django.core.cache import cache
        
        # Check Redis cache first (10 min TTL)
        cache_key = 'hierarchical_categories'
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)
        
        # Initialize WooCommerce API client
        wc_api = WooCommerceAPI()
        
        # Fetch all categories from WooCommerce
        categories = wc_api.get_all_product_categories()
        
        if not categories:
            logger.warning("No categories found from WooCommerce API")
            return Response({
                'categories': [],
                'message': 'No categories found'
            })
        
        # Process categories to ensure consistent format
        processed_categories = []
        for category in categories:
            processed_categories.append({
                'id': category.get('id'),
                'name': category.get('name'),
                'parent': category.get('parent', 0),
                'count': category.get('count', 0),
                'slug': category.get('slug', ''),
                'description': category.get('description', ''),
                'image': category.get('image', {}).get('src', '') if category.get('image') else ''
            })
        
        logger.info(f"Retrieved {len(processed_categories)} hierarchical categories from WooCommerce")
        
        result = {'categories': processed_categories}
        cache.set(cache_key, result, 600)
        return Response(result)
        
    except Exception as e:
        logger.error(f"Error retrieving hierarchical product categories: {str(e)}")
        return Response({
            'error': 'Failed to retrieve hierarchical product categories',
            'detail': str(e)
        }, status=500)
