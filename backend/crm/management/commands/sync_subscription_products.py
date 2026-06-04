"""
Management command to sync subscription products from WooCommerce and ensure all products
with subscription options are properly marked in the ProductSubscription table.
"""

import json
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import Product, ProductSubscription
from crm.woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync subscription products from WooCommerce and create ProductSubscription entries'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run in dry-run mode without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update existing ProductSubscription entries',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']
        
        self.stdout.write(self.style.SUCCESS('Starting subscription products sync...'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        try:
            # Initialize WooCommerce API
            wc = WooCommerceAPI()
            
            # Get all products from WooCommerce
            self.stdout.write('Fetching products from WooCommerce...')
            all_products = []
            page = 1
            per_page = 100
            
            while True:
                products = wc.wcapi.get("products", params={
                    "page": page,
                    "per_page": per_page,
                    "status": "publish"
                }).json()
                
                if not products:
                    break
                    
                all_products.extend(products)
                self.stdout.write(f'Fetched {len(products)} products from page {page}')
                
                if len(products) < per_page:
                    break
                    
                page += 1
            
            self.stdout.write(f'Total products fetched: {len(all_products)}')
            
            # Process each product
            subscription_products_found = 0
            subscription_products_created = 0
            subscription_products_updated = 0
            
            with transaction.atomic():
                for woo_product in all_products:
                    product_id = woo_product.get('id')
                    product_name = woo_product.get('name', 'Unknown')
                    product_type = woo_product.get('type', 'simple')
                    
                    # Check if this is a subscription product
                    is_subscription = False
                    subscription_data = {}
                    
                    # Check for WooCommerce Subscriptions plugin data
                    meta_data = woo_product.get('meta_data', [])
                    for meta in meta_data:
                        key = meta.get('key', '')
                        value = meta.get('value', '')
                        
                        # Check for subscription-related meta keys
                        if key in ['_subscription_price', '_subscription_period', '_subscription_period_interval']:
                            is_subscription = True
                            
                        if key == '_subscription_price':
                            subscription_data['price'] = value
                        elif key == '_subscription_period':
                            subscription_data['period'] = value
                        elif key == '_subscription_period_interval':
                            subscription_data['interval'] = value
                        elif key == '_subscription_sign_up_fee':
                            subscription_data['sign_up_fee'] = value
                        elif key == '_subscription_trial_period':
                            subscription_data['trial_period'] = value
                        elif key == '_subscription_trial_length':
                            subscription_data['trial_length'] = value
                    
                    # Also check product type
                    if product_type in ['subscription', 'variable-subscription']:
                        is_subscription = True
                    
                    # Check for subscription variations
                    if product_type == 'variable':
                        variations = wc.wcapi.get(f"products/{product_id}/variations").json()
                        for variation in variations:
                            var_meta = variation.get('meta_data', [])
                            for meta in var_meta:
                                if meta.get('key', '') in ['_subscription_price', '_subscription_period']:
                                    is_subscription = True
                                    break
                            if is_subscription:
                                break
                    
                    if is_subscription:
                        subscription_products_found += 1
                        self.stdout.write(f'Found subscription product: {product_name} (ID: {product_id})')
                        
                        # Try to find the product in our database
                        try:
                            # Find the corresponding product in our database
                            db_product = Product.objects.filter(woo_product_id=woo_product['id']).first()
                            
                            # If not found, try by name
                            if not db_product:
                                db_product = Product.objects.filter(name__iexact=product_name).first()
                            
                            # If still not found, try by SKU
                            if not db_product and woo_product.get('sku'):
                                db_product = Product.objects.filter(sku=woo_product.get('sku')).first()
                            
                            if db_product:
                                # Check if ProductSubscription already exists
                                existing_subscription = ProductSubscription.objects.filter(product=db_product).first()
                                
                                if existing_subscription and not force:
                                    self.stdout.write(f'  - ProductSubscription already exists for {product_name}')
                                    continue
                                
                                # Prepare subscription data
                                subscription_defaults = {
                                    'price': subscription_data.get('price'),
                                    'period': subscription_data.get('period', 'week'),
                                    'interval': int(subscription_data.get('interval', 2)),
                                    'trial_period': subscription_data.get('trial_period'),
                                    'trial_length': subscription_data.get('trial_length'),
                                    'sign_up_fee': subscription_data.get('sign_up_fee'),
                                    'woo_data': woo_product
                                }
                                
                                # Remove None values
                                subscription_defaults = {k: v for k, v in subscription_defaults.items() if v is not None}
                                
                                if not dry_run:
                                    if existing_subscription:
                                        # Update existing
                                        for key, value in subscription_defaults.items():
                                            setattr(existing_subscription, key, value)
                                        existing_subscription.save()
                                        subscription_products_updated += 1
                                        self.stdout.write(f'  - Updated ProductSubscription for {product_name}')
                                    else:
                                        # Create new
                                        ProductSubscription.objects.create(
                                            product=db_product,
                                            **subscription_defaults
                                        )
                                        subscription_products_created += 1
                                        self.stdout.write(f'  - Created ProductSubscription for {product_name}')
                                else:
                                    self.stdout.write(f'  - Would create/update ProductSubscription for {product_name}')
                            else:
                                self.stdout.write(f'  - Product not found in database: {product_name}')
                                
                        except Exception as e:
                            self.stdout.write(
                                self.style.ERROR(f'Error processing product {product_name}: {str(e)}')
                            )
                            continue
            
            # Summary
            self.stdout.write(self.style.SUCCESS('\n=== SYNC SUMMARY ==='))
            self.stdout.write(f'Total products processed: {len(all_products)}')
            self.stdout.write(f'Subscription products found: {subscription_products_found}')
            self.stdout.write(f'ProductSubscription entries created: {subscription_products_created}')
            self.stdout.write(f'ProductSubscription entries updated: {subscription_products_updated}')
            
            if dry_run:
                self.stdout.write(self.style.WARNING('DRY RUN COMPLETED - No changes were made'))
            else:
                self.stdout.write(self.style.SUCCESS('SYNC COMPLETED SUCCESSFULLY'))
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error during sync: {str(e)}')
            )
            raise
