import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import InventoryLocation, ProductInventory

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Fix duplicate location names with different ATUM IDs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        # Define the canonical location mappings
        canonical_locations = {
            'Chicago Clinic': {'atum_id': 4, 'variants': ['Chicago Clininc']},
            'Vendor Fulfillment': {'atum_id': 1, 'variants': ['Vendor Fullfillment', 'Vendor Fullfilment']},
        }
        
        self.stdout.write(self.style.NOTICE('Fixing duplicate location names...'))
        
        for canonical_name, config in canonical_locations.items():
            canonical_atum_id = config['atum_id']
            variant_names = config['variants']
            
            # Find the canonical location
            try:
                canonical_location = InventoryLocation.objects.get(
                    name=canonical_name,
                    atum_location_id=canonical_atum_id
                )
                self.stdout.write(f"Found canonical location: {canonical_name} (ID: {canonical_location.id}, ATUM ID: {canonical_atum_id})")
            except InventoryLocation.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Canonical location {canonical_name} with ATUM ID {canonical_atum_id} not found!"))
                continue
            except InventoryLocation.MultipleObjectsReturned:
                self.stdout.write(self.style.ERROR(f"Multiple canonical locations found for {canonical_name} with ATUM ID {canonical_atum_id}!"))
                continue
            
            # Process each variant
            for variant_name in variant_names:
                variant_locations = InventoryLocation.objects.filter(name=variant_name)
                
                if not variant_locations.exists():
                    self.stdout.write(f"No variant locations found for {variant_name}")
                    continue
                
                self.stdout.write(f"Found {variant_locations.count()} variant locations for {variant_name}")
                
                for variant in variant_locations:
                    self.stdout.write(f"Processing variant: {variant.name} (ID: {variant.id}, ATUM ID: {variant.atum_location_id})")
                    
                    if not dry_run:
                        try:
                            with transaction.atomic():
                                # Find all inventory records using this variant location
                                inventory_records = ProductInventory.objects.filter(location=variant)
                                
                                for inventory in inventory_records:
                                    # Check if there's already an inventory record for this product at the canonical location
                                    existing = ProductInventory.objects.filter(
                                        product=inventory.product,
                                        location=canonical_location
                                    ).first()
                                    
                                    if existing:
                                        # If there's an existing record, update its quantity and notes
                                        existing.quantity += inventory.quantity
                                        if "[ATUM]" not in existing.notes and "[ATUM]" in inventory.notes:
                                            existing.notes = inventory.notes
                                        existing.save()
                                        self.stdout.write(f"Updated existing inventory for '{inventory.product.name}' at '{canonical_location.name}'")
                                        # Delete the duplicate inventory record
                                        inventory.delete()
                                    else:
                                        # If no existing record, just update the location reference
                                        inventory.location = canonical_location
                                        inventory.save()
                                        self.stdout.write(f"Moved inventory for '{inventory.product.name}' to '{canonical_location.name}'")
                                
                                # After moving all inventory, delete the variant location
                                variant.delete()
                                self.stdout.write(self.style.SUCCESS(f"Deleted variant location '{variant.name}'"))
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error processing variant location '{variant.name}': {str(e)}"))
        
        # Summary
        if dry_run:
            self.stdout.write(self.style.NOTICE("\nDRY RUN COMPLETED - No changes were made"))
        else:
            self.stdout.write(self.style.SUCCESS("\nFIX COMPLETED"))
            
        # Verify the results
        self.stdout.write(self.style.NOTICE("\nCurrent ATUM Location Status:"))
        for location in InventoryLocation.objects.all():
            self.stdout.write(f"Location: {location.name}, ATUM ID: {location.atum_location_id}")
