#!/usr/bin/env python
"""
Bundle Default Series Sync Script

This script syncs all bundle products with fresh default series data from WooCommerce,
using cache-busting to ensure the latest data is fetched.

Usage:
python manage.py shell -c "exec(open('sync_bundle_default_series.py').read())"

OR create a Django management command:
python manage.py sync_bundle_default_series

This script will:
1. Find all bundle products in the database
2. Fetch fresh data from WooCommerce with cache-busting
3. Update default_variation_attributes for all bundled items
4. Save updated bundle data to the database
"""

import os
import sys
import django
import requests
import time
import logging
from datetime import datetime

# Add the project directory to the Python path
sys.path.append('/var/www/newpos-posds/woo-ghl-contact-db/backend')

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'doctorsstudio.settings')
django.setup()

# Import Django models and config (after Django setup)
from crm.models import Product, ProductBundle
from crm.config import WOO_API_URL, WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET

# Configure logging
import sys
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

class BundleDefaultSeriesSync:
    def __init__(self):
        self.woo_url = WOO_API_URL
        self.woo_key = WOO_CONSUMER_KEY
        self.woo_secret = WOO_CONSUMER_SECRET
        self.stats = {
            'bundles_processed': 0,
            'bundles_updated': 0,
            'items_with_defaults': 0,
            'errors': 0
        }

    def fetch_bundle_with_cache_bust(self, woo_product_id):
        """Fetch bundle data from WooCommerce with cache-busting"""
        try:
            url = f"{self.woo_url}/wp-json/wc/v3/products/{woo_product_id}"
            # Add timestamp to bust any caching
            params = {'_': int(time.time())}
            auth = (self.woo_key, self.woo_secret)
            
            response = requests.get(url, auth=auth, params=params, timeout=30)
            response.raise_for_status()
            
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching WooCommerce bundle {woo_product_id}: {str(e)}")
            return None

    def sync_bundle_default_series(self, bundle_product):
        """Sync default series for a specific bundle product"""
        try:
            logger.info(f"\n=== Syncing Bundle: {bundle_product.name} (WooID: {bundle_product.woo_product_id}) ===")
            
            # Fetch fresh data from WooCommerce with cache-busting
            woo_bundle_data = self.fetch_bundle_with_cache_bust(bundle_product.woo_product_id)
            if not woo_bundle_data:
                logger.error(f"Failed to fetch bundle data for {bundle_product.name}")
                self.stats['errors'] += 1
                return False
            
            bundled_items = woo_bundle_data.get('bundled_items', [])
            if not bundled_items:
                logger.info(f"No bundled items found for {bundle_product.name}")
                return True
            
            logger.info(f"WooCommerce has {len(bundled_items)} bundled items")
            
            # Process bundled items and extract default series
            processed_bundle_items = []
            default_series_by_item = {}
            items_with_defaults_count = 0
            
            for item in bundled_items:
                item_id = item.get('product_id')
                if not item_id:
                    logger.warning(f"Bundle item missing product_id")
                    continue
                
                # Check if bundled product exists in our database
                try:
                    bundled_product = Product.objects.get(woo_product_id=item_id)
                    item['local_product_id'] = str(bundled_product.id)
                    
                    # Extract and store default series selections
                    default_attrs = item.get('default_variation_attributes', [])
                    if default_attrs:
                        default_series_by_item[str(item_id)] = default_attrs
                        items_with_defaults_count += 1
                        
                        # Log the default series for this item
                        series_option = "Unknown"
                        for attr in default_attrs:
                            if attr.get('name') == 'Series':
                                series_option = attr.get('option')
                                break
                        
                        logger.info(f"  🔄 {item.get('title')} → Default series: {series_option}")
                    
                    processed_bundle_items.append(item)
                    logger.info(f"  ✅ {item.get('title')} (WooID: {item_id}) -> LocalID: {bundled_product.id}")
                    
                except Product.DoesNotExist:
                    logger.warning(f"  ❌ {item.get('title')} (WooID: {item_id}) -> NOT FOUND in local database")
            
            # Update bundle data with fresh WooCommerce data
            woo_bundle_data['processed_bundle_items'] = processed_bundle_items
            woo_bundle_data['default_series_by_item'] = default_series_by_item
            woo_bundle_data['last_synced'] = datetime.now().isoformat()
            
            # Also update the original bundled_items with fresh default_variation_attributes
            for original_item in woo_bundle_data.get('bundled_items', []):
                item_id = original_item.get('product_id')
                if item_id and str(item_id) in default_series_by_item:
                    original_item['default_variation_attributes'] = default_series_by_item[str(item_id)]
                    logger.info(f"  🔄 Updated default series in original item {item_id}")
            
            logger.info(f"✅ Updated bundle with fresh default series data from WooCommerce")
            
            # Update or create ProductBundle record
            product_bundle, created = ProductBundle.objects.get_or_create(
                product_id=bundle_product.id,
                defaults={
                    'woo_data': woo_bundle_data,
                    'created_at': datetime.now(),
                    'updated_at': datetime.now()
                }
            )
            
            if not created:
                product_bundle.woo_data = woo_bundle_data
                product_bundle.updated_at = datetime.now()
                product_bundle.save()
                logger.info(f"✅ Updated existing ProductBundle record")
            else:
                logger.info(f"✅ Created new ProductBundle record")
            
            logger.info(f"✅ Bundle updated with {len(processed_bundle_items)} valid items")
            
            if items_with_defaults_count > 0:
                logger.info(f"📋 Items with default series: {items_with_defaults_count}")
                self.stats['items_with_defaults'] += items_with_defaults_count
            
            self.stats['bundles_updated'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Error syncing bundle {bundle_product.name}: {str(e)}")
            self.stats['errors'] += 1
            return False

    def run_sync(self):
        """Run the complete bundle default series sync"""
        logger.info("=== Starting Bundle Default Series Sync ===")
        logger.info("This sync uses cache-busting to ensure fresh data from WooCommerce")
        
        # Find all bundle products
        bundle_products = Product.objects.filter(product_type='bundle')
        logger.info(f"Found {bundle_products.count()} bundle products to sync")
        
        for bundle_product in bundle_products:
            self.sync_bundle_default_series(bundle_product)
            self.stats['bundles_processed'] += 1
        
        # Print summary
        logger.info(f"\n=== Sync Summary ===")
        logger.info(f"Bundles processed: {self.stats['bundles_processed']}")
        logger.info(f"Bundles updated: {self.stats['bundles_updated']}")
        logger.info(f"Items with default series: {self.stats['items_with_defaults']}")
        logger.info(f"Errors: {self.stats['errors']}")
        
        if self.stats['errors'] == 0:
            logger.info(f"✅ Bundle default series sync completed successfully!")
        else:
            logger.warning(f"⚠️  Bundle default series sync completed with {self.stats['errors']} errors")

def main():
    """Main function to run the sync"""
    try:
        sync = BundleDefaultSeriesSync()
        sync.run_sync()
    except KeyboardInterrupt:
        logger.info("\n❌ Sync interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Fatal error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
