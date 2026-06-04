from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import Product, ProductInventory, InventoryLocation, ProductVariation
from crm.woocommerce import WooCommerceAPI
import logging
import time

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Restore correct ATUM inventory locations for each product based on ATUM API data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes',
        )
        parser.add_argument(
            '--product-id',
            type=str,
            help='Process only a specific product UUID',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of products to process in each batch',
        )
        parser.add_argument(
            '--sleep',
            type=float,
            default=0.5,
            help='Sleep time between API calls in seconds',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting restoration of ATUM inventory locations...'))
        
        dry_run = options['dry_run']
        product_id = options['product_id']
        batch_size = options['batch_size']
        sleep_time = options['sleep']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        # Initialize WooCommerce API
        try:
            wc_api = WooCommerceAPI()
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to initialize WooCommerce API: {str(e)}')
            )
            return
        
        # Get products to process
        if product_id:
            products = Product.objects.filter(id=product_id)
            if not products.exists():
                self.stdout.write(
                    self.style.ERROR(f'Product with ID {product_id} not found')
                )
                return
        else:
            products = Product.objects.all()
        
        total_products = products.count()
        self.stdout.write(f'Found {total_products} products to process')
        
        # Process products in batches
        processed = 0
        updated = 0
        errors = 0
        
        # Process in batches
        for i in range(0, total_products, batch_size):
            batch = products[i:i+batch_size]
            batch_count = batch.count()
            
            self.stdout.write(f'Processing batch {i//batch_size + 1} of {(total_products + batch_size - 1) // batch_size} ({batch_count} products)')
            
            for product in batch:
                processed += 1
                
                try:
                    # Get ATUM inventory data for this product from WooCommerce API
                    self.stdout.write(f'Fetching ATUM inventory for product: {product.name} (WooID: {product.woo_product_id})')
                    
                    # Get product inventories from ATUM API
                    product_inventories = wc_api.get_product_inventories(product.woo_product_id)
                    
                    if not product_inventories:
                        self.stdout.write(self.style.WARNING(f'No ATUM inventory found for product: {product.name}'))
                        
                        # Remove any existing ATUM inventory records for this product
                        existing_atum_inventories = ProductInventory.objects.filter(
                            product=product,
                            location__atum_location_id__isnull=False,
                            notes__contains='Synced from ATUM inventory'
                        )
                        
                        if existing_atum_inventories.exists():
                            count = existing_atum_inventories.count()
                            if not dry_run:
                                with transaction.atomic():
                                    deleted_count = existing_atum_inventories.delete()[0]
                                    self.stdout.write(f'  Removed {deleted_count} orphaned ATUM inventory records')
                                    updated += 1
                            else:
                                self.stdout.write(f'  Would remove {count} orphaned ATUM inventory records (dry run)')
                        
                        continue
                    
                    self.stdout.write(f'Found {len(product_inventories)} ATUM inventory locations for product: {product.name}')
                    
                    # Handle location name mappings for common mismatches
                    location_name_mappings = {
                        'Boca Clinic': 'Boca Raton Clinic',
                        'Jupiter Clinic': 'Jupiter Clinic',
                        'Chicago Clinic': 'Chicago Clinic',
                        'Vendor Fulfillment': 'Vendor Fulfillment',
                        'Dropship': 'Dropship',
                        'Clinic Inventory': 'Clinic Inventory',
                        'Main Inventory': 'Main Inventory',
                        'Clinical Inventory': 'Clinical Inventory',
                    }
                    
                    # Get existing inventory records for this product
                    existing_inventories = ProductInventory.objects.filter(
                        product=product,
                        location__atum_location_id__isnull=False,
                        notes__contains='Synced from ATUM inventory'
                    )
                    
                    # Create a set of existing ATUM location IDs
                    existing_location_ids = set(existing_inventories.values_list('location__atum_location_id', flat=True))
                    
                    # Create a set of ATUM location IDs from API data
                    api_locations = []
                    for inventory_data in product_inventories:
                        location_name = inventory_data.get('name', '')
                        if not location_name:
                            continue
                        
                        # Use mapped name if available, otherwise use original
                        mapped_location_name = location_name_mappings.get(location_name, location_name)
                        
                        try:
                            # Find the corresponding InventoryLocation by name
                            location = InventoryLocation.objects.get(
                                name=mapped_location_name,
                                atum_location_id__isnull=False
                            )
                            
                            api_locations.append({
                                'location': location,
                                'inventory_id': inventory_data.get('id'),
                                'quantity': inventory_data.get('meta_data', {}).get('stock_quantity', 0),
                                'location_name': mapped_location_name
                            })
                            
                        except InventoryLocation.DoesNotExist:
                            self.stdout.write(
                                self.style.WARNING(f'ATUM location "{mapped_location_name}" not found in database')
                            )
                    
                    api_location_ids = set(loc['location'].atum_location_id for loc in api_locations)
                    
                    # Calculate differences
                    to_add = api_location_ids - existing_location_ids
                    to_remove = existing_location_ids - api_location_ids
                    
                    self.stdout.write(f'  API locations: {len(api_location_ids)}, Existing: {len(existing_location_ids)}')
                    self.stdout.write(f'  Locations to add: {len(to_add)}, Locations to remove: {len(to_remove)}')
                    
                    if not dry_run:
                        with transaction.atomic():
                            # Remove excess locations
                            if to_remove:
                                removed = existing_inventories.filter(
                                    location__atum_location_id__in=to_remove
                                ).delete()[0]
                                self.stdout.write(f'  Removed {removed} excess inventory records')
                            
                            # Add missing locations
                            for loc_data in api_locations:
                                if loc_data['location'].atum_location_id in to_add:
                                    # Convert quantity to int if it's a string
                                    try:
                                        quantity = int(loc_data['quantity']) if loc_data['quantity'] is not None else 0
                                    except (ValueError, TypeError):
                                        quantity = 0
                                    
                                    # Create new inventory record
                                    ProductInventory.objects.create(
                                        product=product,
                                        location=loc_data['location'],
                                        quantity=quantity,
                                        is_available=quantity > 0,
                                        notes=f'Synced from ATUM inventory {loc_data["inventory_id"]} at {loc_data["location_name"]} [ATUM]'
                                    )
                                    self.stdout.write(f'  Added inventory record for location: {loc_data["location_name"]}')
                            
                            if to_add or to_remove:
                                updated += 1
                    
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'Error processing product {product.name}: {str(e)}')
                    )
                    errors += 1
                
                # Sleep between API calls to avoid rate limiting
                time.sleep(sleep_time)
            
            self.stdout.write(f'Completed batch {i//batch_size + 1}. Processed {processed}/{total_products} products so far.')
        
        self.stdout.write(self.style.SUCCESS(
            f'ATUM location restoration completed: {processed} products processed, {updated} products updated, {errors} errors'
        ))
