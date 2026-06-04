#!/usr/bin/env python3
"""
Script to sync product SKUs from WooCommerce to local database.

This script fetches SKU data from WooCommerce API and updates the corresponding
SKU fields in the product child tables (ProductSimple, ProductVariation).

Usage:
    python3 sync_product_skus.py [--dry-run] [--product-id PRODUCT_ID] [--verbose]
"""

import os
import sys
import django
import logging
import argparse
from decimal import Decimal

# Setup Django environment
sys.path.append('/var/www/newpos-posds/woo-ghl-contact-db/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'doctorsstudio.settings')
django.setup()

from django.db import transaction
from crm.models import Product, ProductSimple, ProductVariation
from crm.woocommerce import WooCommerceAPI

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('/var/www/newpos-posds/woo-ghl-contact-db/backend/logs/sku_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SKUSyncManager:
    def __init__(self, dry_run=False, verbose=False):
        self.dry_run = dry_run
        self.verbose = verbose
        self.wc_api = WooCommerceAPI()
        self.stats = {
            'products_processed': 0,
            'simple_skus_updated': 0,
            'variation_skus_updated': 0,
            'errors': 0,
            'skipped': 0
        }
    
    def log_info(self, message):
        """Log info message if verbose mode is enabled"""
        if self.verbose:
            logger.info(message)
    
    def sync_product_sku(self, product_id=None):
        """
        Sync SKUs for a specific product or all products
        
        Args:
            product_id (int, optional): Specific WooCommerce product ID to sync
        """
        try:
            if product_id:
                # Sync specific product
                self._sync_single_product(product_id)
            else:
                # Sync all products
                self._sync_all_products()
                
            self._print_summary()
            
        except Exception as e:
            logger.error(f"Error in SKU sync: {str(e)}")
            raise
    
    def _sync_single_product(self, woo_product_id):
        """Sync SKU for a single product"""
        try:
            # Check if product exists locally
            try:
                local_product = Product.objects.get(woo_product_id=woo_product_id)
            except Product.DoesNotExist:
                logger.warning(f"Product with WooCommerce ID {woo_product_id} not found locally")
                self.stats['skipped'] += 1
                return
            
            # Fetch product data from WooCommerce
            response = self.wc_api.wcapi.get(f"products/{woo_product_id}")
            if not response.ok:
                logger.error(f"Failed to fetch product {woo_product_id} from WooCommerce: {response.status_code}")
                self.stats['errors'] += 1
                return
            
            woo_product = response.json()
            self._process_product_sku(local_product, woo_product)
            
        except Exception as e:
            logger.error(f"Error syncing product {woo_product_id}: {str(e)}")
            self.stats['errors'] += 1
    
    def _sync_all_products(self):
        """Sync SKUs for all products"""
        page = 1
        per_page = 50
        
        while True:
            try:
                self.log_info(f"Fetching products page {page}...")
                
                # Fetch products from WooCommerce
                response = self.wc_api.wcapi.get("products", params={
                    'page': page,
                    'per_page': per_page,
                    'status': 'any'  # Include all product statuses
                })
                
                if not response.ok:
                    logger.error(f"Failed to fetch products page {page}: {response.status_code}")
                    break
                
                products = response.json()
                if not products:
                    break
                
                # Process each product
                for woo_product in products:
                    try:
                        woo_product_id = woo_product.get('id')
                        if not woo_product_id:
                            continue
                        
                        # Check if product exists locally
                        try:
                            local_product = Product.objects.get(woo_product_id=woo_product_id)
                        except Product.DoesNotExist:
                            self.log_info(f"Product {woo_product_id} not found locally - skipping")
                            self.stats['skipped'] += 1
                            continue
                        
                        self._process_product_sku(local_product, woo_product)
                        
                    except Exception as e:
                        logger.error(f"Error processing product {woo_product.get('id', 'unknown')}: {str(e)}")
                        self.stats['errors'] += 1
                        continue
                
                page += 1
                
            except Exception as e:
                logger.error(f"Error fetching products page {page}: {str(e)}")
                break
    
    def _process_product_sku(self, local_product, woo_product):
        """Process SKU data for a single product"""
        try:
            self.stats['products_processed'] += 1
            product_type = woo_product.get('type', 'simple')
            product_name = woo_product.get('name', 'Unknown')
            woo_sku = woo_product.get('sku', '')
            
            self.log_info(f"Processing {product_type} product: {product_name} (ID: {local_product.woo_product_id})")
            
            if product_type == 'simple':
                self._update_simple_product_sku(local_product, woo_sku)
            elif product_type == 'variable':
                # For variable products, update both parent SKU and variation SKUs
                self._update_simple_product_sku(local_product, woo_sku)  # Update parent SKU
                self._update_variable_product_skus(local_product, woo_product)  # Update variation SKUs
            elif product_type in ['bundle', 'subscription', 'grouped']:
                # These product types may have SKUs in their child tables
                self._update_special_product_sku(local_product, woo_sku, product_type)
            else:
                self.log_info(f"Unsupported product type: {product_type}")
                
        except Exception as e:
            logger.error(f"Error processing product SKU for {local_product.woo_product_id}: {str(e)}")
            self.stats['errors'] += 1
    
    def _update_simple_product_sku(self, local_product, woo_sku):
        """Update SKU for simple product"""
        try:
            # Get or create ProductSimple record
            product_simple, created = ProductSimple.objects.get_or_create(
                product=local_product,
                defaults={'sku': woo_sku}
            )
            
            if not created and product_simple.sku != woo_sku:
                old_sku = product_simple.sku
                if not self.dry_run:
                    product_simple.sku = woo_sku
                    product_simple.save()
                
                logger.info(f"{'[DRY RUN] ' if self.dry_run else ''}Updated simple product SKU: {local_product.name}")
                logger.info(f"  Old SKU: '{old_sku}' -> New SKU: '{woo_sku}'")
                self.stats['simple_skus_updated'] += 1
            elif created:
                logger.info(f"{'[DRY RUN] ' if self.dry_run else ''}Created ProductSimple with SKU: {woo_sku}")
                self.stats['simple_skus_updated'] += 1
            else:
                self.log_info(f"SKU already up to date for simple product: {local_product.name}")
                
        except Exception as e:
            logger.error(f"Error updating simple product SKU: {str(e)}")
            self.stats['errors'] += 1
    
    def _update_variable_product_skus(self, local_product, woo_product):
        """Update SKUs for variable product variations"""
        try:
            # Fetch variations from WooCommerce
            woo_product_id = local_product.woo_product_id
            variations_response = self.wc_api.wcapi.get(f"products/{woo_product_id}/variations", params={'per_page': 100})
            
            if not variations_response.ok:
                logger.error(f"Failed to fetch variations for product {woo_product_id}: {variations_response.status_code}")
                return
            
            woo_variations = variations_response.json()
            
            for woo_variation in woo_variations:
                try:
                    woo_variation_id = woo_variation.get('id')
                    woo_sku = woo_variation.get('sku', '')
                    
                    if not woo_variation_id:
                        continue
                    
                    # Find local variation
                    try:
                        local_variation = ProductVariation.objects.get(
                            product=local_product,
                            woo_variation_id=woo_variation_id
                        )
                        
                        if local_variation.sku != woo_sku:
                            old_sku = local_variation.sku
                            if not self.dry_run:
                                local_variation.sku = woo_sku
                                local_variation.save()
                            
                            logger.info(f"{'[DRY RUN] ' if self.dry_run else ''}Updated variation SKU: {local_product.name} (Variation ID: {woo_variation_id})")
                            logger.info(f"  Old SKU: '{old_sku}' -> New SKU: '{woo_sku}'")
                            self.stats['variation_skus_updated'] += 1
                        else:
                            self.log_info(f"SKU already up to date for variation {woo_variation_id}")
                            
                    except ProductVariation.DoesNotExist:
                        logger.warning(f"Variation {woo_variation_id} not found locally for product {woo_product_id}")
                        continue
                        
                except Exception as e:
                    logger.error(f"Error processing variation {woo_variation.get('id', 'unknown')}: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error updating variable product SKUs: {str(e)}")
            self.stats['errors'] += 1
    
    def _update_special_product_sku(self, local_product, woo_sku, product_type):
        """Update SKU for bundle, subscription, or grouped products"""
        try:
            from crm.models import ProductBundle, ProductSubscription, ProductGrouped
            
            if product_type == 'bundle':
                # For bundle products, store SKU in both ProductSimple and ProductBundle.woo_data
                self._update_simple_product_sku(local_product, woo_sku)
                
                # Also update the bundle's woo_data with SKU info
                try:
                    product_bundle = ProductBundle.objects.filter(product=local_product).first()
                    if product_bundle:
                        if not product_bundle.woo_data:
                            product_bundle.woo_data = {}
                        
                        old_bundle_sku = product_bundle.woo_data.get('sku', '')
                        if old_bundle_sku != woo_sku:
                            if not self.dry_run:
                                product_bundle.woo_data['sku'] = woo_sku
                                product_bundle.save()
                            
                            logger.info(f"{'[DRY RUN] ' if self.dry_run else ''}Updated bundle SKU in woo_data: {local_product.name}")
                            logger.info(f"  Old SKU: '{old_bundle_sku}' -> New SKU: '{woo_sku}'")
                            self.stats['simple_skus_updated'] += 1
                            
                except Exception as bundle_error:
                    logger.error(f"Error updating ProductBundle woo_data SKU: {str(bundle_error)}")
                    
            elif product_type == 'subscription':
                # For subscription products, store SKU in ProductSimple
                self._update_simple_product_sku(local_product, woo_sku)
                
                # Also update subscription's woo_data if needed
                try:
                    product_subscription = ProductSubscription.objects.filter(product=local_product).first()
                    if product_subscription and hasattr(product_subscription, 'woo_data'):
                        if not product_subscription.woo_data:
                            product_subscription.woo_data = {}
                        
                        old_sub_sku = product_subscription.woo_data.get('sku', '')
                        if old_sub_sku != woo_sku:
                            if not self.dry_run:
                                product_subscription.woo_data['sku'] = woo_sku
                                product_subscription.save()
                            
                            logger.info(f"{'[DRY RUN] ' if self.dry_run else ''}Updated subscription SKU in woo_data: {local_product.name}")
                            
                except Exception as sub_error:
                    logger.error(f"Error updating ProductSubscription woo_data SKU: {str(sub_error)}")
                    
            elif product_type == 'grouped':
                # For grouped products, store SKU in ProductSimple
                self._update_simple_product_sku(local_product, woo_sku)
                self.log_info(f"Updated grouped product SKU: {local_product.name}")
                
            else:
                self.log_info(f"Unsupported special product type: {product_type}")
            
        except Exception as e:
            logger.error(f"Error updating {product_type} product SKU: {str(e)}")
            self.stats['errors'] += 1
    
    def _print_summary(self):
        """Print sync summary"""
        logger.info("\n" + "="*60)
        logger.info("SKU SYNC SUMMARY")
        logger.info("="*60)
        logger.info(f"Products processed: {self.stats['products_processed']}")
        logger.info(f"Simple product SKUs updated: {self.stats['simple_skus_updated']}")
        logger.info(f"Variation SKUs updated: {self.stats['variation_skus_updated']}")
        logger.info(f"Products skipped: {self.stats['skipped']}")
        logger.info(f"Errors: {self.stats['errors']}")
        logger.info("="*60)
        
        if self.dry_run:
            logger.info("DRY RUN MODE - No changes were made to the database")

def main():
    parser = argparse.ArgumentParser(description='Sync product SKUs from WooCommerce')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without updating database')
    parser.add_argument('--product-id', type=int, help='Sync specific product by WooCommerce ID')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    logger.info(f"Starting SKU sync {'(DRY RUN)' if args.dry_run else ''}")
    
    sync_manager = SKUSyncManager(dry_run=args.dry_run, verbose=args.verbose)
    
    try:
        sync_manager.sync_product_sku(product_id=args.product_id)
        logger.info("SKU sync completed successfully")
    except Exception as e:
        logger.error(f"SKU sync failed: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main()
