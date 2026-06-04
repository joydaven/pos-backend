from django.core.management.base import BaseCommand
from django.db import transaction, IntegrityError
from django.db.models import Count, Q
from crm.models import InventoryLocation, ProductInventory, Product
import logging
import uuid
import time

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Comprehensive fix for ATUM inventory location sync issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--product-id',
            type=str,
            help='Fix only a specific product ID',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of products to process in each batch',
        )
        parser.add_argument(
            '--skip-duplicates',
            action='store_true',
            help='Skip duplicate location cleanup',
        )
        parser.add_argument(
            '--skip-missing',
            action='store_true',
            help='Skip adding missing locations',
        )
        parser.add_argument(
            '--skip-notes',
            action='store_true',
            help='Skip standardizing notes format',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting comprehensive ATUM inventory fix...'))
        
        dry_run = options['dry_run']
        product_id = options['product_id']
        batch_size = options['batch_size']
        skip_duplicates = options['skip_duplicates']
        skip_missing = options['skip_missing']
        skip_notes = options['skip_notes']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        # Step 1: Ensure all standard ATUM locations exist with proper IDs
        self.ensure_atum_locations(dry_run)
        
        # Step 2: Fix duplicate locations if not skipped
        if not skip_duplicates:
            self.fix_duplicate_locations(dry_run)
        
        # Step 3: Process products in batches
        if product_id:
            try:
                products = [Product.objects.get(id=product_id)]
                self.stdout.write(f'Processing specific product: {products[0].name}')
            except Product.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Product with ID {product_id} not found'))
                return
                
            self.process_products(products, dry_run, skip_missing, skip_notes)
        else:
            # Get all products with any inventory
            products_with_inventory = Product.objects.filter(
                id__in=ProductInventory.objects.values_list('product_id', flat=True).distinct()
            )
            
            total_products = products_with_inventory.count()
            self.stdout.write(f'Found {total_products} products with inventory to process')
            
            # Process in batches
            for i in range(0, total_products, batch_size):
                batch = products_with_inventory[i:i+batch_size]
                self.stdout.write(f'Processing batch {i//batch_size + 1} ({len(batch)} products)')
                self.process_products(batch, dry_run, skip_missing, skip_notes)
                
                # Small delay between batches to prevent database overload
                if not dry_run and i + batch_size < total_products:
                    time.sleep(1)
        
        self.stdout.write(self.style.SUCCESS('Comprehensive ATUM inventory fix completed'))

    def ensure_atum_locations(self, dry_run):
        """Ensure all standard ATUM locations exist with proper IDs"""
        self.stdout.write('Ensuring all standard ATUM locations exist...')
        
        # Define standard ATUM locations with their expected IDs
        standard_locations = {
            'Vendor Fulfillment': 1,
            'Jupiter Clinic': 2,
            'Chicago Clinic': 3,
            'Boca Raton Clinic': 5,
            'Dropship': 7
        }
        
        for name, atum_id in standard_locations.items():
            # Check if location exists
            location = InventoryLocation.objects.filter(name=name).first()
            
            if not location:
                self.stdout.write(f'Location {name} does not exist')
                if not dry_run:
                    InventoryLocation.objects.create(
                        id=uuid.uuid4(),
                        name=name,
                        atum_location_id=atum_id,
                        code=f'ATUM-{atum_id}'
                    )
                    self.stdout.write(self.style.SUCCESS(f'Created location {name} with ATUM ID {atum_id}'))
                else:
                    self.stdout.write(f'Would create location {name} with ATUM ID {atum_id}')
            elif location.atum_location_id != atum_id:
                self.stdout.write(f'Location {name} has incorrect ATUM ID: {location.atum_location_id} (should be {atum_id})')
                if not dry_run:
                    location.atum_location_id = atum_id
                    location.code = f'ATUM-{atum_id}'
                    location.save()
                    self.stdout.write(self.style.SUCCESS(f'Updated location {name} with correct ATUM ID {atum_id}'))
                else:
                    self.stdout.write(f'Would update location {name} with correct ATUM ID {atum_id}')
            else:
                self.stdout.write(f'Location {name} already exists with correct ATUM ID {atum_id}')

    def fix_duplicate_locations(self, dry_run):
        """Fix duplicate locations with different names but same ATUM ID"""
        self.stdout.write('Checking for duplicate locations...')
        
        # Find locations with duplicate ATUM IDs
        duplicate_atum_ids = InventoryLocation.objects.filter(
            atum_location_id__isnull=False
        ).values('atum_location_id').annotate(
            count=Count('atum_location_id')
        ).filter(count__gt=1).values_list('atum_location_id', flat=True)
        
        for atum_id in duplicate_atum_ids:
            duplicates = InventoryLocation.objects.filter(atum_location_id=atum_id).order_by('name')
            
            if duplicates.count() <= 1:
                continue
                
            self.stdout.write(f'Found {duplicates.count()} locations with ATUM ID {atum_id}:')
            for loc in duplicates:
                self.stdout.write(f'  - {loc.name} (ID: {loc.id})')
            
            # Choose the canonical location (first alphabetically)
            canonical = duplicates.first()
            self.stdout.write(f'Using {canonical.name} as the canonical location')
            
            # Process each duplicate
            for duplicate in duplicates.exclude(id=canonical.id):
                # Count inventory records for this duplicate
                inventory_count = ProductInventory.objects.filter(location=duplicate).count()
                self.stdout.write(f'Location {duplicate.name} has {inventory_count} inventory records')
                
                if not dry_run:
                    try:
                        with transaction.atomic():
                            # Update inventory records to use canonical location
                            for inventory in ProductInventory.objects.filter(location=duplicate):
                                # Check if product already has inventory at canonical location
                                existing = ProductInventory.objects.filter(
                                    product=inventory.product,
                                    location=canonical
                                ).first()
                                
                                if existing:
                                    # Merge inventory (keep the higher quantity)
                                    if inventory.quantity > existing.quantity:
                                        existing.quantity = inventory.quantity
                                        existing.save()
                                    # Delete the duplicate inventory
                                    inventory.delete()
                                else:
                                    # Update to use canonical location
                                    inventory.location = canonical
                                    inventory.save()
                            
                            # Delete the duplicate location
                            duplicate.delete()
                            self.stdout.write(self.style.SUCCESS(
                                f'Merged {duplicate.name} into {canonical.name} and deleted duplicate'
                            ))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'Error merging locations: {str(e)}'))
                else:
                    self.stdout.write(f'Would merge {duplicate.name} into {canonical.name} and delete duplicate')

    def process_products(self, products, dry_run, skip_missing, skip_notes):
        """Process a batch of products to fix inventory issues"""
        # Get all ATUM locations
        atum_locations = InventoryLocation.objects.filter(atum_location_id__isnull=False)
        atum_location_ids = {loc.id: loc for loc in atum_locations}
        
        for product in products:
            self.stdout.write(f'Processing product: {product.name}')
            
            # Get current inventory for this product
            current_inventory = ProductInventory.objects.filter(product=product)
            current_locations = {inv.location_id: inv for inv in current_inventory}
            
            # Step 1: Standardize notes format if not skipped
            if not skip_notes:
                self.standardize_notes(product, current_inventory, dry_run)
            
            # Step 2: Add missing ATUM locations if not skipped
            if not skip_missing:
                self.add_missing_locations(product, current_locations, atum_location_ids, dry_run)

    def standardize_notes(self, product, inventories, dry_run):
        """Standardize notes format for all inventory records"""
        for inventory in inventories:
            if inventory.location.atum_location_id:
                # Check if notes need standardization
                needs_update = False
                
                if not inventory.notes:
                    needs_update = True
                    new_notes = f'Synced from ATUM inventory 6010 at {inventory.location.name} [ATUM]'
                elif 'Synced from ATUM inventory' in inventory.notes and '[ATUM]' not in inventory.notes:
                    needs_update = True
                    new_notes = f'{inventory.notes} [ATUM]'
                elif 'Synced from ATUM inventory' not in inventory.notes:
                    needs_update = True
                    new_notes = f'Synced from ATUM inventory 6010 at {inventory.location.name} [ATUM]'
                
                if needs_update:
                    if not dry_run:
                        inventory.notes = new_notes
                        inventory.save()
                        self.stdout.write(f'Updated notes for {product.name} at {inventory.location.name}')
                    else:
                        self.stdout.write(f'Would update notes for {product.name} at {inventory.location.name}')

    def add_missing_locations(self, product, current_locations, atum_location_ids, dry_run):
        """Add missing ATUM locations for a product"""
        # Check which ATUM locations are missing for this product
        missing_locations = []
        for loc_id, location in atum_location_ids.items():
            if loc_id not in current_locations:
                missing_locations.append(location)
        
        if missing_locations:
            self.stdout.write(f'Product {product.name} is missing {len(missing_locations)} ATUM locations:')
            for location in missing_locations:
                self.stdout.write(f'  - {location.name} (ATUM ID: {location.atum_location_id})')
                
                if not dry_run:
                    try:
                        # Create inventory record with default quantity
                        ProductInventory.objects.create(
                            id=uuid.uuid4(),
                            product=product,
                            location=location,
                            quantity=10,  # Default quantity
                            is_available=True,
                            notes=f'Synced from ATUM inventory 6010 at {location.name} [ATUM]'
                        )
                        self.stdout.write(self.style.SUCCESS(
                            f'Added inventory for {product.name} at {location.name}'
                        ))
                    except IntegrityError:
                        self.stdout.write(self.style.ERROR(
                            f'Error adding inventory for {product.name} at {location.name}: Duplicate record'
                        ))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(
                            f'Error adding inventory for {product.name} at {location.name}: {str(e)}'
                        ))
                else:
                    self.stdout.write(f'Would add inventory for {product.name} at {location.name}')
