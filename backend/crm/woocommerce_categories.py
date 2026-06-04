from .woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)

def get_all_product_categories():
    """
    Helper function to fetch all product categories from WooCommerce API
    with hierarchical structure.
    
    Returns:
        List of category objects with id, name, parent fields
    """
    try:
        # Initialize WooCommerce API client
        wc_api = WooCommerceAPI()
        
        # Fetch all categories from WooCommerce
        categories = wc_api.get_all_product_categories()
        
        if not categories:
            logger.warning("No categories found from WooCommerce API")
            return []
        
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
        return processed_categories
        
    except Exception as e:
        logger.error(f"Error retrieving hierarchical product categories: {str(e)}")
        return []
