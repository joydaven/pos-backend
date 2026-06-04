from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import Product, ProductVariation
from crm.woocommerce import WooCommerceAPI
from decimal import Decimal, InvalidOperation
import logging
import time
import json

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync product variations from WooCommerce - update existing, create new, and remove orphaned variations'

    def add_arguments(self, parser):
        parser.add_argument(
            '--product-id',
            type=int,
            help='Sync variations for a specific product ID only'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of products to process per batch (default: 50)'
        )
        parser.add_argument(
            '--sleep',
            type=float,
            default=0.5,
            help='Sleep time between API calls in seconds'
        )
        parser.add_argument(
            '--remove-orphaned',
            action='store_true',
            help='Remove variations that no longer exist in WooCommerce'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.verbose = options['verbose']
        self.batch_size = options['batch_size']
        self.sleep_time = options['sleep']
        self.remove_orphaned = options['remove_orphaned']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        if self.remove_orphaned:
            self.stdout.write(self.style.WARNING('ORPHANED REMOVAL ENABLED - Variations not in WooCommerce will be deleted'))
        
        try:
            wc_api = WooCommerceAPI()
            
            if options['product_id']:
                self.sync_single_product_variations(wc_api, options['product_id'])
            else:
                self.sync_all_product_variations(wc_api)
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error during variation sync: {str(e)}'))
            logger.error(f'Variation sync error: {str(e)}', exc_info=True)

    def sync_single_product_variations(self, wc_api, product_id):
        """Sync variations for a single product"""
        try:
            product = Product.objects.get(woo_product_id=product_id)
            self.stdout.write(f'Syncing variations for product: {product.name} (ID: {product_id})')
            
            if product.product_type not in ['variable', 'variable_subscription']:
                self.stdout.write(f'Product {product.name} is not a variable product (type: {product.product_type})')
                return
            
            stats = self.sync_product_variations(wc_api, product)
            self.print_sync_stats(product.name, stats)
                
        except Product.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Product with WooCommerce ID {product_id} not found in database'))

    def sync_all_product_variations(self, wc_api):
        """Sync variations for all variable products"""
        variable_products = Product.objects.filter(
            product_type__in=['variable', 'variable_subscription']
        ).order_by('woo_product_id')
        
        total_products = variable_products.count()
        self.stdout.write(f'Starting variation sync for {total_products} variable products...')
        
        total_stats = {
            'updated': 0,
            'created': 0,
            'removed': 0,
            'errors': 0
        }
        
        for i, product in enumerate(variable_products, 1):
            try:
                if self.verbose:
                    self.stdout.write(f'[{i}/{total_products}] Processing: {product.name}')
                
                stats = self.sync_product_variations(wc_api, product)
                
                # Accumulate stats
                for key in total_stats:
                    total_stats[key] += stats.get(key, 0)
                
                if stats['updated'] > 0 or stats['created'] > 0 or stats['removed'] > 0:
                    if self.verbose:
                        self.print_sync_stats(product.name, stats)
                
                # Add delay to avoid overwhelming the API
                if i % self.batch_size == 0:
                    time.sleep(self.sleep_time)
                    self.stdout.write(f'Processed {i}/{total_products} products...')
                else:
                    time.sleep(self.sleep_time / 2)
                    
            except Exception as e:
                total_stats['errors'] += 1
                self.stdout.write(self.style.ERROR(f'  ✗ Error processing {product.name}: {str(e)}'))
                logger.error(f'Error syncing variations for product {product.woo_product_id}: {str(e)}')
        
        self.stdout.write(self.style.SUCCESS(f'\nVariation sync completed!'))
        self.stdout.write(f'Total products processed: {total_products}')
        self.stdout.write(f'Variations updated: {total_stats["updated"]}')
        self.stdout.write(f'Variations created: {total_stats["created"]}')
        self.stdout.write(f'Variations removed: {total_stats["removed"]}')
        self.stdout.write(f'Errors: {total_stats["errors"]}')

    def sync_product_variations(self, wc_api, product):
        """Sync variations for a specific product"""
        stats = {
            'updated': 0,
            'created': 0,
            'removed': 0,
            'errors': 0
        }
        
        try:
            # Get variations from WooCommerce
            wc_variations = wc_api.get_product_variations(product.woo_product_id)
            
            if wc_variations is None:
                if self.verbose:
                    self.stdout.write(f'  - No variations found in WooCommerce for {product.name}')
                return stats
            
            # Get current variations from database
            current_variations = ProductVariation.objects.filter(product=product)
            current_variation_ids = set(var.woo_variation_id for var in current_variations)
            wc_variation_ids = set(var['id'] for var in wc_variations)
            
            if self.verbose:
                self.stdout.write(f'  WooCommerce variations: {len(wc_variations)}')
                self.stdout.write(f'  Database variations: {len(current_variations)}')
            
            # Process each WooCommerce variation
            for wc_variation in wc_variations:
                variation_id = wc_variation.get('id')
                
                try:
                    # Try to get existing variation
                    try:
                        variation = ProductVariation.objects.get(
                            product=product,
                            woo_variation_id=variation_id
                        )
                        
                        # Update existing variation
                        if self.update_variation_from_wc_data(variation, wc_variation):
                            stats['updated'] += 1
                            
                    except ProductVariation.DoesNotExist:
                        # Create new variation
                        if self.create_variation_from_wc_data(product, wc_variation):
                            stats['created'] += 1
                        
                except Exception as e:
                    stats['errors'] += 1
                    if self.verbose:
                        self.stdout.write(f'    ✗ Error processing variation {variation_id}: {str(e)}')
                    logger.error(f'Error processing variation {variation_id} for product {product.woo_product_id}: {str(e)}')
            
            # Remove orphaned variations if requested
            if self.remove_orphaned:
                orphaned_ids = current_variation_ids - wc_variation_ids
                if orphaned_ids:
                    for variation in current_variations:
                        if variation.woo_variation_id in orphaned_ids:
                            if self.verbose:
                                self.stdout.write(f'    🗑️  Removing orphaned variation {variation.woo_variation_id}')
                            
                            if not self.dry_run:
                                variation.delete()
                            stats['removed'] += 1
            
            return stats
            
        except Exception as e:
            stats['errors'] += 1
            logger.error(f'Error syncing variations for product {product.woo_product_id}: {str(e)}')
            raise

    def update_variation_from_wc_data(self, variation, wc_variation):
        """Update existing variation with WooCommerce data"""
        updated = False
        changes = []
        
        # Extract data from WooCommerce variation (only fields that exist in ProductVariation model)
        wc_data = {
            'sku': wc_variation.get('sku', ''),
            'price': self.safe_decimal(wc_variation.get('price')),
            'regular_price': self.safe_decimal(wc_variation.get('regular_price')),
            'sale_price': self.safe_decimal(wc_variation.get('sale_price')),
            'stock_quantity': wc_variation.get('stock_quantity'),
            'menu_order': wc_variation.get('menu_order', 0),
            'attributes': wc_variation.get('attributes', []),
            'woo_data': wc_variation,  # Store complete WooCommerce data
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
                changes.append(f'{db_field}: {repr(old_value)} → {repr(new_value)}')
                if not self.dry_run:
                    setattr(variation, db_field, new_value)
                    updated = True
        
        # Handle JSON fields separately (only fields that exist in ProductVariation model)
        json_fields = {
            'attributes': 'attributes',
            'woo_data': 'woo_data',
        }
        
        for wc_field, db_field in json_fields.items():
            old_value = getattr(variation, db_field)
            new_value = wc_data[wc_field]
            
            # Compare JSON data
            if json.dumps(old_value, sort_keys=True) != json.dumps(new_value, sort_keys=True):
                changes.append(f'{db_field}: updated')
                if not self.dry_run:
                    setattr(variation, db_field, new_value)
                    updated = True
        
        # Save changes
        if updated and not self.dry_run:
            variation.save()
        
        # Log changes
        if changes and self.verbose:
            variation_name = f"Variation {variation.woo_variation_id}"
            if variation.attributes:
                attr_str = ", ".join([f"{attr.get('name', '')}: {attr.get('option', '')}" 
                                    for attr in variation.attributes if attr.get('name')])
                if attr_str:
                    variation_name += f" ({attr_str})"
            self.stdout.write(f'    ✓ Updated {variation_name}: {", ".join(changes[:3])}{"..." if len(changes) > 3 else ""}')
        
        return updated or len(changes) > 0  # Return True even in dry run if changes detected

    def create_variation_from_wc_data(self, product, wc_variation):
        """Create new variation from WooCommerce data"""
        try:
            variation_data = {
                'product': product,
                'woo_variation_id': wc_variation.get('id'),
                'sku': wc_variation.get('sku', ''),
                'price': self.safe_decimal(wc_variation.get('price')),
                'regular_price': self.safe_decimal(wc_variation.get('regular_price')),
                'sale_price': self.safe_decimal(wc_variation.get('sale_price')),
                'stock_quantity': wc_variation.get('stock_quantity'),
                'menu_order': wc_variation.get('menu_order', 0),
                'attributes': wc_variation.get('attributes', []),
                'woo_data': wc_variation,  # Store complete WooCommerce data
            }
            
            if self.verbose:
                attr_str = ""
                if variation_data['attributes']:
                    attr_str = ", ".join([f"{attr.get('name', '')}: {attr.get('option', '')}" 
                                        for attr in variation_data['attributes'] if attr.get('name')])
                    attr_str = f" ({attr_str})"
                self.stdout.write(f'    ➕ Creating variation {wc_variation.get("id")}{attr_str}')
            
            if not self.dry_run:
                ProductVariation.objects.create(**variation_data)
            
            return True
            
        except Exception as e:
            if self.verbose:
                self.stdout.write(f'    ✗ Error creating variation {wc_variation.get("id")}: {str(e)}')
            logger.error(f'Error creating variation {wc_variation.get("id")} for product {product.woo_product_id}: {str(e)}')
            return False

    def print_sync_stats(self, product_name, stats):
        """Print sync statistics for a product"""
        changes = []
        if stats['updated'] > 0:
            changes.append(f"updated: {stats['updated']}")
        if stats['created'] > 0:
            changes.append(f"created: {stats['created']}")
        if stats['removed'] > 0:
            changes.append(f"removed: {stats['removed']}")
        if stats['errors'] > 0:
            changes.append(f"errors: {stats['errors']}")
        
        if changes:
            self.stdout.write(f'  ✓ {product_name}: {", ".join(changes)}')

    def safe_decimal(self, value):
        """Safely convert a value to Decimal, handling empty strings and None"""
        if value is None or value == '' or value == 0:
            return None
        
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
