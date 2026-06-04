#!/usr/bin/env python
"""
Comprehensive bundle product sync from WooCommerce to local database.
This script fetches fresh bundle data from WooCommerce API and ensures our database
has accurate bundle contents by:
1. Fetching current bundle data from WooCommerce
2. Comparing with local database
3. Adding missing products that exist in WooCommerce
4. Removing products that no longer exist in WooCommerce bundles
"""

import os
import sys
import json
import logging
import requests
import django
from datetime import datetime

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'doctorsstudio.settings')
django.setup()

from crm.models import Product, ProductBundle
from crm.config import WOO_API_URL, WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

class ComprehensiveBundleSync:
    def __init__(self):
        self.woo_url = WOO_API_URL
        self.woo_key = WOO_CONSUMER_KEY
        self.woo_secret = WOO_CONSUMER_SECRET
        self.stats = {
            'bundles_processed': 0,
            'products_added': 0,
            'products_removed': 0,
            'bundles_updated': 0,
            'errors': 0
        }

    def fetch_woocommerce_bundle(self, woo_product_id):
        """Fetch bundle data from WooCommerce API"""
        try:
            url = f"{self.woo_url}/wp-json/wc/v3/products/{woo_product_id}"
            auth = (self.woo_key, self.woo_secret)
            
            response = requests.get(url, auth=auth, timeout=30)
            response.raise_for_status()
            
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching WooCommerce bundle {woo_product_id}: {str(e)}")
            return None

    def fetch_woocommerce_product(self, woo_product_id):
        """Fetch individual product data from WooCommerce API"""
        try:
            url = f"{self.woo_url}/wp-json/wc/v3/products/{woo_product_id}"
            auth = (self.woo_key, self.woo_secret)
            
            response = requests.get(url, auth=auth, timeout=30)
            response.raise_for_status()
            
            return response.json()
        except Exception as e:
            logger.warning(f"Product {woo_product_id} not found in WooCommerce: {str(e)}")
            return None

    def sync_product_prices(self, local_product, woo_product_data):
        """Update local product prices from WooCommerce data"""
        try:
            updated = False
            
            # Update price fields
            new_price = float(woo_product_data.get('price', 0))
            new_regular_price = float(woo_product_data.get('regular_price', 0))
            new_sale_price = float(woo_product_data.get('sale_price', 0)) if woo_product_data.get('sale_price') else None
            
            if local_product.price != new_price:
                local_product.price = new_price
                updated = True
                
            if local_product.regular_price != new_regular_price:
                local_product.regular_price = new_regular_price
                updated = True
                
            if local_product.sale_price != new_sale_price:
                local_product.sale_price = new_sale_price
                updated = True
            
            # Update other important fields
            new_stock = woo_product_data.get('stock_quantity', 0)
            if local_product.stock_quantity != new_stock:
                local_product.stock_quantity = new_stock
                updated = True
            
            if updated:
                local_product.updated_at = datetime.now()
                local_product.save()
                logger.info(f"  💰 Updated prices: {local_product.name} -> Price: ${new_price}, Regular: ${new_regular_price}")
                self.stats['products_updated'] = self.stats.get('products_updated', 0) + 1
                
            return True
            
        except Exception as e:
            logger.error(f"Error updating product prices for {local_product.name}: {str(e)}")
            return False

    def sync_missing_product(self, woo_product_id):
        """Add missing product to local database if it exists in WooCommerce"""
        try:
            # Check if product already exists locally
            existing_product = Product.objects.filter(woo_product_id=woo_product_id).first()
            if existing_product:
                # Product exists, but let's update its prices
                woo_product = self.fetch_woocommerce_product(woo_product_id)
                if woo_product:
                    self.sync_product_prices(existing_product, woo_product)
                return True
            
            # Fetch product from WooCommerce
            woo_product = self.fetch_woocommerce_product(woo_product_id)
            if not woo_product:
                return False
            
            # Create product in local database
            product = Product.objects.create(
                woo_product_id=woo_product_id,
                name=woo_product.get('name', f'Product {woo_product_id}'),
                description=woo_product.get('description', ''),
                short_description=woo_product.get('short_description', ''),
                price=float(woo_product.get('price', 0)),
                regular_price=float(woo_product.get('regular_price', 0)),
                sale_price=float(woo_product.get('sale_price', 0)) if woo_product.get('sale_price') else None,
                product_type=woo_product.get('type', 'simple'),
                status=woo_product.get('status', 'publish'),
                stock_quantity=woo_product.get('stock_quantity', 0),
                sku=woo_product.get('sku', ''),
                weight=float(woo_product.get('weight', 0)) if woo_product.get('weight') else None,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            logger.info(f"✅ Added missing product: {product.name} (WooID: {woo_product_id}, LocalID: {product.id})")
            self.stats['products_added'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Error syncing missing product {woo_product_id}: {str(e)}")
            self.stats['errors'] += 1
            return False

    def sync_bundle_from_woocommerce(self, bundle_product):
        """Sync a single bundle with fresh data from WooCommerce"""
        try:
            logger.info(f"\n=== Syncing Bundle: {bundle_product.name} (WooID: {bundle_product.woo_product_id}) ===")
            
            # Fetch fresh bundle data from WooCommerce
            woo_bundle_data = self.fetch_woocommerce_bundle(bundle_product.woo_product_id)
            if not woo_bundle_data:
                logger.error(f"Failed to fetch bundle data from WooCommerce")
                self.stats['errors'] += 1
                return False
            
            # Update bundle product prices first
            self.sync_product_prices(bundle_product, woo_bundle_data)
            
            # Extract bundled items
            bundled_items = woo_bundle_data.get('bundled_items', [])
            if not bundled_items:
                logger.warning(f"No bundled items found in WooCommerce for bundle {bundle_product.woo_product_id}")
                return False
            
            logger.info(f"WooCommerce has {len(bundled_items)} bundled items")
            
            # Process each bundled item
            processed_bundle_items = []
            default_series_by_item = {}
            missing_products = []
            
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
                    default_attrs = item.get('default_variation_attributes', {})
                    if default_attrs:
                        default_series_by_item[str(item_id)] = default_attrs
                        logger.info(f"Default series for item {item_id}: {default_attrs}")
                    
                    processed_bundle_items.append(item)
                    logger.info(f"  ✅ {item.get('title')} (WooID: {item_id}) -> LocalID: {bundled_product.id}")
                    
                except Product.DoesNotExist:
                    logger.warning(f"  ❌ {item.get('title')} (WooID: {item_id}) -> NOT FOUND in local database")
                    missing_products.append(item_id)
            
            # Try to sync missing products
            if missing_products:
                logger.info(f"\nAttempting to sync {len(missing_products)} missing products...")
                for missing_id in missing_products:
                    if self.sync_missing_product(missing_id):
                        # Re-process this item now that we have the product
                        for item in bundled_items:
                            if item.get('product_id') == missing_id:
                                try:
                                    bundled_product = Product.objects.get(woo_product_id=missing_id)
                                    item['local_product_id'] = str(bundled_product.id)
                                    
                                    default_attrs = item.get('default_variation_attributes', {})
                                    if default_attrs:
                                        default_series_by_item[str(missing_id)] = default_attrs
                                    
                                    processed_bundle_items.append(item)
                                    logger.info(f"  ✅ Added after sync: {item.get('title')} (WooID: {missing_id})")
                                    break
                                except Product.DoesNotExist:
                                    continue
            
            # Update bundle data with fresh WooCommerce data
            woo_bundle_data['processed_bundle_items'] = processed_bundle_items
            woo_bundle_data['default_series_by_item'] = default_series_by_item
            woo_bundle_data['last_synced'] = datetime.now().isoformat()
            
            # Also update the original bundled_items with fresh default_variation_attributes
            for original_item in woo_bundle_data.get('bundled_items', []):
                item_id = original_item.get('product_id')
                if item_id and str(item_id) in default_series_by_item:
                    original_item['default_variation_attributes'] = default_series_by_item[str(item_id)]
                    logger.info(f"  🔄 Updated default series in original item {item_id}: {default_series_by_item[str(item_id)]}")
            
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
            
            logger.info(f"✅ Bundle updated with {len(processed_bundle_items)} valid items")
            self.stats['bundles_updated'] += 1
            self.stats['bundles_processed'] += 1
            return True
            
        except Exception as e:
            logger.error(f"Error syncing bundle {bundle_product.name}: {str(e)}")
            self.stats['errors'] += 1
            return False

    def run_comprehensive_sync(self):
        """Run comprehensive sync for all bundle products"""
        logger.info("=== Starting Comprehensive Bundle Sync ===")
        
        # Find all bundle products
        bundle_products = Product.objects.filter(product_type='bundle')
        logger.info(f"Found {bundle_products.count()} bundle products to sync")
        
        for bundle_product in bundle_products:
            self.sync_bundle_from_woocommerce(bundle_product)
        
        # Print summary
        logger.info(f"\n=== Sync Summary ===")
        logger.info(f"Bundles processed: {self.stats['bundles_processed']}")
        logger.info(f"Bundles updated: {self.stats['bundles_updated']}")
        logger.info(f"Products added: {self.stats['products_added']}")
        logger.info(f"Products updated: {self.stats.get('products_updated', 0)}")
        logger.info(f"Errors: {self.stats['errors']}")
        
        if self.stats['errors'] == 0:
            logger.info("✅ Comprehensive bundle sync with price updates completed successfully!")
        else:
            logger.warning(f"⚠️ Sync completed with {self.stats['errors']} errors")

if __name__ == "__main__":
    sync = ComprehensiveBundleSync()
    sync.run_comprehensive_sync()
