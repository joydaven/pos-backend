from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from crm.models import Product
from crm.woocommerce import WooCommerceAPI
import logging
import json

logger = logging.getLogger(__name__)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@csrf_exempt
def sync_product_categories_manual(request):
    """
    Manual API endpoint to sync product categories from WooCommerce.
    This fixes discrepancies where local products have outdated category slugs.
    """
    try:
        logger.info("Starting manual product category sync...")
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Load categories from WooCommerce
        categories = wc_api.get_all_product_categories()
        if not categories:
            return Response({
                'success': False,
                'message': 'Failed to load categories from WooCommerce',
                'error': 'No categories returned from WooCommerce API'
            }, status=500)
        
        # Create category mapping (slug -> name)
        category_name_map = {}
        for cat in categories:
            cat_name = cat.get('name', '')
            cat_slug = cat.get('slug', '')
            
            # Map slug to name (precision-path -> Precision Path)
            if cat_slug:
                category_name_map[cat_slug] = cat_name
            
            # Map name to name (for exact matches)
            if cat_name:
                category_name_map[cat_name] = cat_name
        
        # Process all products
        products = Product.objects.all()
        stats = {
            'total_products': products.count(),
            'products_updated': 0,
            'categories_mapped': len(categories),
            'errors': 0,
            'updated_products': []
        }
        
        for product in products:
            try:
                # Get current categories
                current_categories = product.categories or []
                if not current_categories:
                    continue
                
                # Normalize each category
                updated_categories = []
                categories_changed = False
                changes = []
                
                for category in current_categories:
                    # Direct lookup in mapping
                    normalized = category
                    if category in category_name_map:
                        normalized = category_name_map[category]
                    else:
                        # Case-insensitive lookup
                        category_lower = category.lower()
                        for key, value in category_name_map.items():
                            if key.lower() == category_lower:
                                normalized = value
                                break
                    
                    updated_categories.append(normalized)
                    
                    if normalized != category:
                        categories_changed = True
                        changes.append(f"'{category}' -> '{normalized}'")
                
                # Update product if categories changed
                if categories_changed:
                    product.categories = updated_categories
                    product.save(update_fields=['categories'])
                    stats['products_updated'] += 1
                    stats['updated_products'].append({
                        'id': str(product.id),
                        'name': product.name,
                        'changes': changes
                    })
                    logger.info(f"Updated product {product.id} ({product.name}): {', '.join(changes)}")
                
            except Exception as e:
                logger.error(f"Error processing product {product.id}: {str(e)}")
                stats['errors'] += 1
        
        logger.info(f"Category sync completed: {stats['products_updated']} products updated, {stats['errors']} errors")
        
        return Response({
            'success': True,
            'message': f'Successfully synced product categories. Updated {stats["products_updated"]} products.',
            'stats': stats
        })
        
    except Exception as e:
        logger.error(f"Error in manual category sync: {str(e)}")
        return Response({
            'success': False,
            'message': 'Failed to sync product categories',
            'error': str(e)
        }, status=500)
