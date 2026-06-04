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
        variations_processed = 0
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
                    
                    # Check if this is a variable product
                    is_variable = product.product_type == 'variable'
                    
                    if is_variable:
                        # For variable products, process each variation
                        variations = ProductVariation.objects.filter(product=product)
                        self.stdout.write(f'Found {variations.count()} variations for variable product: {product.name}')
                        
                        for variation in variations:
                            self.stdout.write(f'  Processing variation: {variation.woo_variation_id}')
                            variations_processed += 1
                            variation_inventories = wc_api.get_product_inventories(variation.woo_variation_id)
                            
                            if not variation_inventories:
                                self.stdout.write(self.style.WARNING(f'  No ATUM inventory found for variation: {variation.woo_variation_id}'))
                                
                                # Remove any existing ATUM inventory records for this variation
                                existing_variation_inventories = ProductInventory.objects.filter(
                                    product=product,
                                    variation=variation,
                                    location__atum_location_id__isnull=False,
                                    notes__contains='Synced from ATUM inventory'
                                )
                                
                                if existing_variation_inventories.exists():
                                    count = existing_variation_inventories.count()
                                    if not dry_run:
                                        with transaction.atomic():
                                            deleted_count = existing_variation_inventories.delete()[0]
                                            self.stdout.write(f'    Removed {deleted_count} orphaned ATUM inventory records for variation {variation.woo_variation_id}')
                                            updated += 1
                                    else:
                                        self.stdout.write(f'    Would remove {count} orphaned ATUM inventory records for variation {variation.woo_variation_id} (dry run)')
                                continue
                            
                            # Check if API data is stale (all records are truly inactive/removed)
                            # Data is only considered stale if ALL records have:
                            # - No stock management (manage_stock = false) AND
                            # - Zero/null quantities AND  
                            # - Not marked as in stock
                            all_stale = True
                            for api_inv in variation_inventories:
                                meta_data = api_inv.get('meta_data', {})
                                stock_quantity = meta_data.get('stock_quantity')
                                manage_stock = meta_data.get('manage_stock', False)
                                stock_status = meta_data.get('stock_status', '')
                                
                                # Consider record active if ANY of these conditions are true:
                                # 1. Stock is actively managed (even if quantity is 0)
                                # 2. Stock status is 'instock' (even if not managed)
                                # 3. Has positive stock quantity
                                if (manage_stock or 
                                    stock_status == 'instock' or 
                                    (stock_quantity and int(stock_quantity) > 0)):
                                    all_stale = False
                                    break
                            
                            if all_stale:
                                self.stdout.write(self.style.WARNING(f'  API returned stale data for variation {variation.woo_variation_id} (all locations inactive: no stock management, no quantities, not in stock)'))
                                
                                # Remove existing ATUM inventory records as they're based on stale data
                                existing_variation_inventories = ProductInventory.objects.filter(
                                    product=product,
                                    variation=variation,
                                    location__atum_location_id__isnull=False,
                                    notes__contains='Synced from ATUM inventory'
                                )
                                
                                if existing_variation_inventories.exists():
                                    count = existing_variation_inventories.count()
                                    if not dry_run:
                                        with transaction.atomic():
                                            deleted_count = existing_variation_inventories.delete()[0]
                                            self.stdout.write(f'    Removed {deleted_count} stale ATUM inventory records for variation {variation.woo_variation_id}')
                                            updated += 1
                                    else:
                                        self.stdout.write(f'    Would remove {count} stale ATUM inventory records for variation {variation.woo_variation_id} (dry run)')
                                continue
                            
                            # Process variation inventory locations
                            variation_updated = self._process_variation_inventory(product, variation, variation_inventories, dry_run)
                            if variation_updated:
                                updated += 1
                        
                        # Skip the regular product inventory processing for variable products
                        continue
                    else:
                        # Get product inventories from ATUM API for simple products
                        product_inventories = wc_api.get_product_inventories(product.woo_product_id)
                    
                    if not product_inventories:
                        self.stdout.write(self.style.WARNING(f'No ATUM inventory found for product: {product.name}'))
                        
                        # Remove any existing ATUM inventory records for this product
                        existing_atum_inventories = ProductInventory.objects.filter(
                            product=product,
                            variation__isnull=True,  # Only product-level inventory
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
                    
                    # Process product inventory locations
                    product_updated = self._process_product_inventory(product, product_inventories, dry_run)
                    if product_updated:
                        updated += 1
                
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'Error processing product {product.name}: {str(e)}')
                    )
                    errors += 1
                
                # Sleep between API calls to avoid rate limiting
                time.sleep(sleep_time)
            
            self.stdout.write(f'Completed batch {i//batch_size + 1}. Processed {processed}/{total_products} products so far.')
        
        self.stdout.write(
            self.style.SUCCESS(
                f'ATUM location restoration completed: {processed} products processed, {updated} products updated, {variations_processed} variations processed, {errors} errors'
            )
        )

    def _process_product_inventory(self, product, product_inventories, dry_run):
        """
        Process ATUM inventory for a simple product
        """
        # Handle location name mappings for common mismatches
        location_name_mappings = {
            'Jupiter Inventory': 'Jupiter Inventory',
            'Boca Inventory': 'Boca Inventory',
            'Boca Clinic': 'Boca Raton Clinic',
            'Jupiter Clinic': 'Jupiter Clinic',
            'Chicago Clinic': 'Chicago Clinic',
            'Vendor Fulfillment': 'Vendor Fulfillment',
            'Dropship': 'Dropship',
            'Clinic Inventory': 'Clinic Inventory',
            'Clinic Inventory.': 'Clinic Inventory',  # Handle period at end
            'Main Inventory': 'Main Inventory',
            'Clinical Inventory': 'Clinical Inventory',
        }
        
        # Get existing inventory records for this product (excluding variations)
        existing_inventories = ProductInventory.objects.filter(
            product=product,
            variation__isnull=True,  # Only product-level inventory
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
                
                # Add missing locations and update existing ones with proper stock handling
                for loc_data in api_locations:
                    # Enhanced quantity handling with proper null handling for unmanaged stock
                    raw_quantity = loc_data['quantity']
                    
                    # Check if this location is managed or unmanaged
                    # If quantity is None or empty string, this indicates unmanaged stock
                    if raw_quantity is None or raw_quantity == '' or raw_quantity == 'null':
                        quantity = None  # Unmanaged stock
                        self.stdout.write(f'  Location {loc_data["location_name"]} is UNMANAGED (quantity: {raw_quantity})')
                    else:
                        try:
                            quantity = int(raw_quantity)
                            self.stdout.write(f'  Location {loc_data["location_name"]} is MANAGED (quantity: {quantity})')
                        except (ValueError, TypeError):
                            quantity = None  # Treat invalid values as unmanaged
                            self.stdout.write(f'  Invalid quantity value for {loc_data["location_name"]}: {raw_quantity} - treating as unmanaged')
                    
                    # Create or update inventory record for ALL locations (new and existing)
                    # Handle potential duplicates by cleaning them up first
                    existing_records = ProductInventory.objects.filter(
                        product=product,
                        location=loc_data['location'],
                        variation=None,  # Explicitly set to None for product-level inventory
                    )
                    
                    if existing_records.count() > 1:
                        # Clean up duplicates - keep the first one, delete the rest
                        self.stdout.write(f'  WARNING: Found {existing_records.count()} duplicate inventory records for {loc_data["location_name"]} - cleaning up')
                        primary_record = existing_records.first()
                        duplicates = existing_records.exclude(id=primary_record.id)
                        duplicate_count = duplicates.count()
                        duplicates.delete()
                        self.stdout.write(f'  Removed {duplicate_count} duplicate records, kept primary record ID {primary_record.id}')
                        inventory = primary_record
                        created = False
                    elif existing_records.exists():
                        inventory = existing_records.first()
                        created = False
                    else:
                        inventory = ProductInventory.objects.create(
                            product=product,
                            location=loc_data['location'],
                            variation=None,
                            quantity=quantity,
                            notes=f'[ATUM] Synced from ATUM inventory',
                        )
                        created = True
                    
                    # Always update quantity and notes for existing records
                    if not created:
                        # Only update if quantity has changed to avoid unnecessary saves
                        # Handle comparison between None and 0 properly
                        needs_update = (
                            (inventory.quantity is None and quantity is not None) or
                            (inventory.quantity is not None and quantity is None) or
                            (inventory.quantity != quantity)
                        )
                        
                        if needs_update:
                            old_qty_display = 'unmanaged' if inventory.quantity is None else str(inventory.quantity)
                            new_qty_display = 'unmanaged' if quantity is None else str(quantity)
                            self.stdout.write(f'  Updating quantity for {loc_data["location_name"]}: {old_qty_display} → {new_qty_display}')
                            inventory.quantity = quantity
                            inventory.notes = f'[ATUM] Synced from ATUM inventory'
                            inventory.save()
                    
                    # Log the action taken
                    if loc_data['location'].atum_location_id in to_add:
                        qty_display = 'unmanaged' if quantity is None else str(quantity)
                        self.stdout.write(f'  Added inventory record for location: {loc_data["location_name"]} (qty: {qty_display})')
                    elif not created:
                        qty_display = 'unmanaged' if quantity is None else str(quantity)
                        self.stdout.write(f'  Updated inventory record for location: {loc_data["location_name"]} (qty: {qty_display})')
                
                # Track if any quantities were updated (locations that existed but had quantity changes)
                quantities_updated = len(api_locations) > 0 and len(to_add) < len(api_locations)
                
                if to_add or to_remove or quantities_updated:
                    return True  # Indicate that updates were made
        
        return False

    def _process_variation_inventory(self, product, variation, variation_inventories, dry_run):
        """
        Process ATUM inventory for a specific product variation
        """
        # Handle location name mappings for common mismatches
        location_name_mappings = {
            'Jupiter Inventory': 'Jupiter Inventory',
            'Boca Inventory': 'Boca Inventory',
            'Jupiter Clinic': 'Jupiter Clinic',
            'Chicago Clinic': 'Chicago Clinic',
            'Vendor Fulfillment': 'Vendor Fulfillment',
            'Dropship': 'Dropship',
            'Clinic Inventory': 'Clinic Inventory',
            'Clinic Inventory.': 'Clinic Inventory',  # Handle period at end
            'Main Inventory': 'Main Inventory',
            'Clinical Inventory': 'Clinical Inventory',
        }
        
        # Get existing inventory records for this variation
        existing_inventories = ProductInventory.objects.filter(
            product=product,
            variation=variation,
            location__atum_location_id__isnull=False,
            notes__contains='Synced from ATUM inventory'
        )
        
        # Create a set of existing ATUM location IDs for this variation
        existing_location_ids = set(existing_inventories.values_list('location__atum_location_id', flat=True))
        
        # Create a set of ATUM location IDs from API data
        api_locations = []
        for inventory_data in variation_inventories:
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
                    self.style.WARNING(f'    ATUM location "{mapped_location_name}" not found in database')
                )
        
        api_location_ids = set(loc['location'].atum_location_id for loc in api_locations)
        
        # Calculate differences
        to_add = api_location_ids - existing_location_ids
        to_remove = existing_location_ids - api_location_ids
        
        self.stdout.write(f'    Variation {variation.woo_variation_id}: API locations: {len(api_location_ids)}, Existing: {len(existing_location_ids)}')
        self.stdout.write(f'    Locations to add: {len(to_add)}, Locations to remove: {len(to_remove)}')
        
        if not dry_run:
            with transaction.atomic():
                # Remove excess locations for this variation
                if to_remove:
                    removed = existing_inventories.filter(
                        location__atum_location_id__in=to_remove
                    ).delete()[0]
                    self.stdout.write(f'    Removed {removed} excess inventory records for variation {variation.woo_variation_id}')
                
                # Add missing locations and update existing ones for this variation with proper stock handling
                for loc_data in api_locations:
                    # Enhanced quantity handling with proper null handling for unmanaged stock
                    raw_quantity = loc_data['quantity']
                    
                    # Extract ATUM inventory metadata for variation (if available)
                    meta_data = {}
                    if 'meta_data' in loc_data:
                        meta_data = loc_data['meta_data']
                    
                    manage_stock = meta_data.get('manage_stock', False) if meta_data else False
                    
                    # Check if this location is managed or unmanaged
                    # Use manage_stock flag as primary indicator, fallback to quantity analysis
                    if not manage_stock and meta_data:
                        quantity = None  # Unmanaged stock
                        self.stdout.write(f'    Variation {variation.woo_variation_id} at {loc_data["location_name"]} is UNMANAGED (manage_stock=False)')
                    elif raw_quantity is None or raw_quantity == '' or raw_quantity == 'null':
                        quantity = None  # Unmanaged stock (no quantity data)
                        self.stdout.write(f'    Variation {variation.woo_variation_id} at {loc_data["location_name"]} is UNMANAGED (quantity: {raw_quantity})')
                    else:
                        try:
                            quantity = int(raw_quantity)
                            self.stdout.write(f'    Variation {variation.woo_variation_id} at {loc_data["location_name"]} is MANAGED (quantity: {quantity})')
                        except (ValueError, TypeError):
                            quantity = None  # Treat invalid values as unmanaged
                            self.stdout.write(f'    Invalid quantity value for variation {variation.woo_variation_id} at {loc_data["location_name"]}: {raw_quantity} - treating as unmanaged')
                    
                    # Create or update inventory record for ALL locations (new and existing)
                    # Handle potential duplicates by cleaning them up first
                    existing_records = ProductInventory.objects.filter(
                        product=product,
                        variation=variation,
                        location=loc_data['location'],
                    )
                    
                    if existing_records.count() > 1:
                        # Clean up duplicates - keep the first one, delete the rest
                        self.stdout.write(f'    WARNING: Found {existing_records.count()} duplicate inventory records for variation {variation.woo_variation_id} at {loc_data["location_name"]} - cleaning up')
                        primary_record = existing_records.first()
                        duplicates = existing_records.exclude(id=primary_record.id)
                        duplicate_count = duplicates.count()
                        duplicates.delete()
                        self.stdout.write(f'    Removed {duplicate_count} duplicate records, kept primary record ID {primary_record.id}')
                        inventory = primary_record
                        created = False
                    elif existing_records.exists():
                        inventory = existing_records.first()
                        created = False
                    else:
                        inventory = ProductInventory.objects.create(
                            product=product,
                            variation=variation,
                            location=loc_data['location'],
                            quantity=quantity,
                            notes=f'[ATUM] Synced from ATUM inventory for variation {variation.woo_variation_id}',
                        )
                        created = True
                    
                    # Always update quantity and notes for existing records
                    if not created:
                        # Only update if quantity has changed to avoid unnecessary saves
                        # Handle comparison between None and 0 properly
                        needs_update = (
                            (inventory.quantity is None and quantity is not None) or
                            (inventory.quantity is not None and quantity is None) or
                            (inventory.quantity != quantity)
                        )
                        
                        if needs_update:
                            old_qty_display = 'unmanaged' if inventory.quantity is None else str(inventory.quantity)
                            new_qty_display = 'unmanaged' if quantity is None else str(quantity)
                            self.stdout.write(f'    Updating quantity for variation {variation.woo_variation_id} at {loc_data["location_name"]}: {old_qty_display} → {new_qty_display}')
                            inventory.quantity = quantity
                            inventory.notes = f'[ATUM] Synced from ATUM inventory for variation {variation.woo_variation_id}'
                            inventory.save()
                    
                    # Log the action taken
                    if loc_data['location'].atum_location_id in to_add:
                        qty_display = 'unmanaged' if quantity is None else str(quantity)
                        self.stdout.write(f'    Added inventory record for variation {variation.woo_variation_id} at location: {loc_data["location_name"]} (qty: {qty_display})')
                    elif not created:
                        qty_display = 'unmanaged' if quantity is None else str(quantity)
                        self.stdout.write(f'    Updated inventory record for variation {variation.woo_variation_id} at location: {loc_data["location_name"]} (qty: {qty_display})')
                
                # Track if any quantities were updated (locations that existed but had quantity changes)
                quantities_updated = len(api_locations) > 0 and len(to_add) < len(api_locations)
                
                if to_add or to_remove or quantities_updated:
                    return True  # Indicate that updates were made
        
        return False
