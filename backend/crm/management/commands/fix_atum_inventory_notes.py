import logging
from django.core.management.base import BaseCommand
from crm.models import ProductInventory, InventoryLocation, Product

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Fix inconsistent ATUM inventory location notes to ensure proper API filtering'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate the fixes without making actual changes',
        )
        parser.add_argument(
            '--product',
            type=str,
            help='Specific product ID to fix (optional)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        specific_product_id = options.get('product')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('Running in DRY RUN mode - no changes will be made'))
        
        # Get all inventory records with ATUM location IDs
        inventory_query = ProductInventory.objects.filter(
            location__atum_location_id__isnull=False
        )
        
        # Filter by specific product if provided
        if specific_product_id:
            try:
                product = Product.objects.get(id=specific_product_id)
                inventory_query = inventory_query.filter(product=product)
                self.stdout.write(f"Filtering for product: {product.name} (ID: {product.id})")
            except Product.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"Product with ID {specific_product_id} not found"))
                return
        
        # Get all inventory records with ATUM location IDs
        atum_inventories = inventory_query.select_related('product', 'location')
        
        self.stdout.write(f"Found {atum_inventories.count()} inventory records with ATUM location IDs")
        
        # Track statistics
        stats = {
            'missing_notes': 0,
            'incorrect_notes': 0,
            'already_correct': 0,
            'updated': 0
        }
        
        # Process each inventory record
        for inventory in atum_inventories:
            product_name = inventory.product.name
            location_name = inventory.location.name
            atum_location_id = inventory.location.atum_location_id
            
            # Check if notes field contains the required text
            has_atum_sync_note = inventory.notes and 'Synced from ATUM inventory' in inventory.notes
            
            if not inventory.notes:
                self.stdout.write(f"Missing notes for {product_name} at {location_name} (ATUM ID: {atum_location_id})")
                stats['missing_notes'] += 1
                new_notes = "Synced from ATUM inventory"
            elif not has_atum_sync_note:
                self.stdout.write(f"Incorrect notes for {product_name} at {location_name} (ATUM ID: {atum_location_id}): {inventory.notes}")
                stats['incorrect_notes'] += 1
                new_notes = f"{inventory.notes} - Synced from ATUM inventory"
            else:
                stats['already_correct'] += 1
                continue  # Skip if already correct
            
            # Update the notes if not in dry run mode
            if not dry_run:
                inventory.notes = new_notes
                inventory.save()
                stats['updated'] += 1
                self.stdout.write(self.style.SUCCESS(f"Updated notes for {product_name} at {location_name}"))
            else:
                self.stdout.write(f"Would update notes for {product_name} at {location_name} to: {new_notes}")
        
        # Print summary
        self.stdout.write("\n" + "="*50)
        self.stdout.write("SUMMARY:")
        self.stdout.write(f"Total inventory records with ATUM location IDs: {atum_inventories.count()}")
        self.stdout.write(f"Records with missing notes: {stats['missing_notes']}")
        self.stdout.write(f"Records with incorrect notes: {stats['incorrect_notes']}")
        self.stdout.write(f"Records already correct: {stats['already_correct']}")
        
        if not dry_run:
            self.stdout.write(f"Records updated: {stats['updated']}")
        else:
            self.stdout.write(f"Records that would be updated: {stats['missing_notes'] + stats['incorrect_notes']}")
        
        self.stdout.write("="*50)
        
        # Provide specific instructions for Copacalm Drops if it was processed
        if specific_product_id:
            try:
                product = Product.objects.get(id=specific_product_id)
                if "Copacalm" in product.name:
                    self.stdout.write(self.style.SUCCESS(f"\nFixed inventory notes for {product.name}"))
                    self.stdout.write("Please refresh the product page to see updated fulfillment locations")
            except Product.DoesNotExist:
                pass
