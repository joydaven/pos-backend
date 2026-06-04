"""
Django management command to sync WooCommerce inventory data with local crm_inventorylocation table.
This script fetches product inventory data from WooCommerce and updates the local database.

Usage:
    python manage.py sync_inventory_locations
    python manage.py sync_inventory_locations --dry-run  # Preview changes without applying
    python manage.py sync_inventory_locations --location-code CLINIC  # Sync specific location only
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, close_old_connections, connection
from django.utils import timezone
from crm.models import Product, InventoryLocation, ProductInventory
from crm.woocommerce import WooCommerceAPI
import logging
import json
import time

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync WooCommerce inventory data with local crm_inventorylocation table'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without applying them to the database',
        )
        parser.add_argument(
            '--location-code',
            type=str,
            help='Sync inventory for a specific location code only',
        )
        parser.add_argument(
            '--create-default-location',
            action='store_true',
            help='Create a default inventory location if none exists',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose output',
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.location_code = options['location_code']
        self.create_default = options['create_default_location']
        self.verbose = options['verbose']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be applied'))
        
        try:
            # Initialize WooCommerce API
            self.wc_api = WooCommerceAPI()
            
            # Ensure we have inventory locations
            self.ensure_inventory_locations()
            
            # Sync inventory data
            self.sync_inventory_data()
            
            self.stdout.write(self.style.SUCCESS('Inventory sync completed successfully'))
            
        except Exception as e:
            logger.error(f"Inventory sync failed: {str(e)}")
            raise CommandError(f'Inventory sync failed: {str(e)}')

    def ensure_inventory_locations(self):
        """Ensure we have inventory locations to sync to"""
        locations = InventoryLocation.objects.filter(is_active=True)
        
        if self.location_code:
            locations = locations.filter(code=self.location_code)
            if not locations.exists():
                raise CommandError(f'Location with code "{self.location_code}" not found')
        
        if not locations.exists():
            if self.create_default:
                self.stdout.write('Creating default inventory location...')
                if not self.dry_run:
                    InventoryLocation.objects.create(
                        name='Main Warehouse',
                        code='MAIN',
                        description='Default inventory location synced from WooCommerce',
                        is_default=True,
                        is_active=True
                    )
                self.stdout.write(self.style.SUCCESS('Created default inventory location: MAIN'))
            else:
                raise CommandError(
                    'No active inventory locations found. '
                    'Use --create-default-location to create one, or add locations manually.'
                )

    def sync_inventory_data(self):
        """Main method to sync inventory data from WooCommerce"""
        self.stdout.write('Starting inventory sync from WooCommerce...')
        
        # Get active inventory locations
        locations = InventoryLocation.objects.filter(is_active=True)
        if self.location_code:
            locations = locations.filter(code=self.location_code)
        
        # Statistics
        stats = {
            'products_processed': 0,
            'inventory_created': 0,
            'inventory_updated': 0,
            'products_not_found': 0,
            'errors': 0
        }
        
        # Fetch all products from WooCommerce with pagination
        page = 1
        while True:
            try:
                self.stdout.write(f'Fetching WooCommerce products page {page}...')
                response = self.wc_api.get_products(page=page, per_page=100)
                
                if not response['data']:
                    break
                
                # Close old DB connections before processing each page
                close_old_connections()
                
                # Process products in this page
                total_in_page = len(response['data'])
                for idx, wc_product in enumerate(response['data'], 1):
                    try:
                        self.stdout.write(f'  Processing product {idx}/{total_in_page} on page {page}: {wc_product.get("name", "Unknown")} (ID: {wc_product.get("id", "?")})')
                        self.process_product_inventory(wc_product, locations, stats)
                    except Exception as e:
                        # If connection closed, try to recover and retry once
                        if 'connection' in str(e).lower() and 'closed' in str(e).lower():
                            try:
                                self.stdout.write(self.style.WARNING(f'  DB connection lost, reconnecting...'))
                                close_old_connections()
                                connection.ensure_connection()
                                self.process_product_inventory(wc_product, locations, stats)
                                continue
                            except Exception as retry_e:
                                stats['errors'] += 1
                                logger.error(f"Error processing product {wc_product.get('id', 'unknown')} after retry: {str(retry_e)}")
                                self.stdout.write(
                                    self.style.ERROR(f"Error processing product {wc_product.get('name', 'unknown')} after retry: {str(retry_e)}")
                                )
                        else:
                            stats['errors'] += 1
                            logger.error(f"Error processing product {wc_product.get('id', 'unknown')}: {str(e)}")
                            self.stdout.write(
                                self.style.ERROR(f"Error processing product {wc_product.get('name', 'unknown')}: {str(e)}")
                            )
                
                self.stdout.write(self.style.SUCCESS(f'Page {page}/{response["total_pages"]} completed. Products processed so far: {stats["products_processed"]}'))
                
                # Close DB connections between pages to prevent stale connections
                close_old_connections()
                
                # Check if we've reached the last page
                if page >= response['total_pages']:
                    break
                    
                page += 1
                
            except Exception as e:
                logger.error(f"Error fetching products from WooCommerce: {str(e)}")
                raise CommandError(f'Failed to fetch products from WooCommerce: {str(e)}')
        
        # Print statistics
        self.print_sync_statistics(stats)

    def process_product_inventory(self, wc_product, locations, stats):
        """Process inventory data for a single WooCommerce product"""
        wc_product_id = wc_product.get('id')
        product_name = wc_product.get('name', 'Unknown')
        stock_quantity = wc_product.get('stock_quantity', 0)
        stock_status = wc_product.get('stock_status', 'outofstock')
        manage_stock = wc_product.get('manage_stock', False)
        
        if self.verbose:
            self.stdout.write(f'Processing product: {product_name} (ID: {wc_product_id})')
        
        stats['products_processed'] += 1
        
        # Find corresponding local product
        try:
            local_product = Product.objects.get(woo_product_id=wc_product_id)
        except Product.DoesNotExist:
            stats['products_not_found'] += 1
            if self.verbose:
                self.stdout.write(
                    self.style.WARNING(f'Local product not found for WooCommerce ID: {wc_product_id}')
                )
            return
        
        # Process inventory for each location
        for location in locations:
            self.update_product_inventory(
                local_product, 
                location, 
                wc_product, 
                stock_quantity, 
                stock_status, 
                manage_stock, 
                stats
            )

    def update_product_inventory(self, product, location, wc_product, stock_quantity, stock_status, manage_stock, stats):
        """Update or create ProductInventory record"""
        
        # Determine availability based on stock status and quantity
        is_available = stock_status == 'instock' and (not manage_stock or (stock_quantity and stock_quantity > 0))
        
        # Ensure stock_quantity is not None
        if stock_quantity is None:
            stock_quantity = 0
        
        # Get or create ProductInventory record
        inventory, created = ProductInventory.objects.get_or_create(
            product=product,
            location=location,
            defaults={
                'quantity': stock_quantity,
                'is_available': is_available,
                'reorder_level': 0,
                'reorder_quantity': 0,
                'notes': f'Synced from WooCommerce on {timezone.now().strftime("%Y-%m-%d %H:%M:%S")}'
            }
        )
        
        if not self.dry_run:
            if created:
                stats['inventory_created'] += 1
                if self.verbose:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Created inventory record: {product.name} @ {location.name} '
                            f'(Qty: {stock_quantity}, Available: {is_available})'
                        )
                    )
            else:
                # Update existing record
                updated = False
                if inventory.quantity != stock_quantity:
                    inventory.quantity = stock_quantity
                    updated = True
                
                if inventory.is_available != is_available:
                    inventory.is_available = is_available
                    updated = True
                
                if updated:
                    inventory.notes = f'Updated from WooCommerce on {timezone.now().strftime("%Y-%m-%d %H:%M:%S")}'
                    inventory.save()
                    stats['inventory_updated'] += 1
                    
                    if self.verbose:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'Updated inventory record: {product.name} @ {location.name} '
                                f'(Qty: {stock_quantity}, Available: {is_available})'
                            )
                        )
        else:
            # Dry run mode - just log what would happen
            action = 'CREATE' if created else 'UPDATE'
            self.stdout.write(
                f'[DRY RUN] {action} inventory: {product.name} @ {location.name} '
                f'(Qty: {stock_quantity}, Available: {is_available})'
            )
            
            if created:
                stats['inventory_created'] += 1
            else:
                stats['inventory_updated'] += 1

    def print_sync_statistics(self, stats):
        """Print sync statistics"""
        self.stdout.write('\n' + '='*50)
        self.stdout.write(self.style.SUCCESS('INVENTORY SYNC STATISTICS'))
        self.stdout.write('='*50)
        self.stdout.write(f'Products processed: {stats["products_processed"]}')
        self.stdout.write(f'Inventory records created: {stats["inventory_created"]}')
        self.stdout.write(f'Inventory records updated: {stats["inventory_updated"]}')
        self.stdout.write(f'Products not found locally: {stats["products_not_found"]}')
        self.stdout.write(f'Errors encountered: {stats["errors"]}')
        self.stdout.write('='*50)
        
        if stats['products_not_found'] > 0:
            self.stdout.write(
                self.style.WARNING(
                    f'\nNote: {stats["products_not_found"]} products from WooCommerce were not found '
                    'in the local database. Consider running sync_woocommerce_products first.'
                )
            )
