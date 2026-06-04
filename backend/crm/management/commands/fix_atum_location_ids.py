import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import InventoryLocation, ProductInventory

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Fix ATUM inventory location IDs for locations with missing IDs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        # Define the correct ATUM location mappings
        atum_location_mappings = {
            'Boca Clinic': {'name': 'Boca Raton Clinic', 'atum_id': 3},
            'Boca Raton Clinic': {'name': 'Boca Raton Clinic', 'atum_id': 3},
            'Chicago Clinic': {'name': 'Chicago Clinic', 'atum_id': 4},
            'Jupiter Clinic': {'name': 'Jupiter Clinic', 'atum_id': 2},
            'Vendor Fulfillment': {'name': 'Vendor Fulfillment', 'atum_id': 1},
            'Dropship': {'name': 'Dropship', 'atum_id': 7},
        }
        
        # 1. First, check for duplicate locations with the same name but different ATUM IDs
        self.stdout.write(self.style.NOTICE('Checking for duplicate locations...'))
        
        for location_name, mapping in atum_location_mappings.items():
            locations = InventoryLocation.objects.filter(name=mapping['name'])
            
            if locations.count() > 1:
                self.stdout.write(self.style.WARNING(f"Found {locations.count()} locations with name '{mapping['name']}'"))
                
                # Find the location with the correct ATUM ID
                correct_location = locations.filter(atum_location_id=mapping['atum_id']).first()
                
                # If no location has the correct ID, use the first one
                if not correct_location:
                    correct_location = locations.first()
                    self.stdout.write(f"No location with correct ATUM ID found for '{mapping['name']}', using first one")
                
                # For all other locations, we'll need to merge their inventory to the correct one
                for duplicate in locations.exclude(id=correct_location.id):
                    self.stdout.write(f"Found duplicate: {duplicate.name} (ID: {duplicate.id}, ATUM ID: {duplicate.atum_location_id})")
                    
                    # Find all inventory records using this duplicate location
                    inventory_records = ProductInventory.objects.filter(location=duplicate)
                    
                    if not dry_run:
                        try:
                            with transaction.atomic():
                                for inventory in inventory_records:
                                    # Check if there's already an inventory record for this product at the correct location
                                    existing = ProductInventory.objects.filter(
                                        product=inventory.product,
                                        location=correct_location
                                    ).first()
                                    
                                    if existing:
                                        # If there's an existing record, update its quantity and notes
                                        existing.quantity += inventory.quantity
                                        if "[ATUM]" not in existing.notes and "[ATUM]" in inventory.notes:
                                            existing.notes = inventory.notes
                                        existing.save()
                                        self.stdout.write(f"Updated existing inventory for '{inventory.product.name}' at '{correct_location.name}'")
                                        # Delete the duplicate inventory record
                                        inventory.delete()
                                    else:
                                        # If no existing record, just update the location reference
                                        inventory.location = correct_location
                                        inventory.save()
                                        self.stdout.write(f"Moved inventory for '{inventory.product.name}' to '{correct_location.name}'")
                                
                                # After moving all inventory, delete the duplicate location
                                duplicate.delete()
                                self.stdout.write(self.style.SUCCESS(f"Deleted duplicate location '{duplicate.name}'"))
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error merging duplicate location '{duplicate.name}': {str(e)}"))
        
        # 2. Now update locations with missing ATUM IDs
        self.stdout.write(self.style.NOTICE('\nUpdating locations with missing ATUM IDs...'))
        
        for location_name, mapping in atum_location_mappings.items():
            try:
                # Find locations with this name but no ATUM ID
                locations = InventoryLocation.objects.filter(
                    name=mapping['name'],
                    atum_location_id__isnull=True
                )
                
                if locations.exists():
                    self.stdout.write(f"Found {locations.count()} locations named '{mapping['name']}' with missing ATUM ID")
                    
                    if not dry_run:
                        for location in locations:
                            try:
                                with transaction.atomic():
                                    # Check if another location already has this ATUM ID
                                    existing = InventoryLocation.objects.filter(
                                        atum_location_id=mapping['atum_id']
                                    ).first()
                                    
                                    if existing and existing.id != location.id:
                                        self.stdout.write(self.style.WARNING(
                                            f"Location '{existing.name}' already has ATUM ID {mapping['atum_id']}. "
                                            f"Will merge '{location.name}' into it."
                                        ))
                                        
                                        # Move all inventory from this location to the existing one
                                        inventory_records = ProductInventory.objects.filter(location=location)
                                        
                                        for inventory in inventory_records:
                                            # Check if there's already an inventory record for this product at the existing location
                                            existing_inventory = ProductInventory.objects.filter(
                                                product=inventory.product,
                                                location=existing
                                            ).first()
                                            
                                            if existing_inventory:
                                                # If there's an existing inventory, update its quantity and notes
                                                existing_inventory.quantity += inventory.quantity
                                                if "[ATUM]" not in existing_inventory.notes and "[ATUM]" in inventory.notes:
                                                    existing_inventory.notes = inventory.notes
                                                existing_inventory.save()
                                                self.stdout.write(f"Updated existing inventory for '{inventory.product.name}' at '{existing.name}'")
                                                # Delete the duplicate inventory record
                                                inventory.delete()
                                            else:
                                                # If no existing inventory, just update the location reference
                                                inventory.location = existing
                                                inventory.save()
                                                self.stdout.write(f"Moved inventory for '{inventory.product.name}' to '{existing.name}'")
                                        
                                        # After moving all inventory, delete the location
                                        location.delete()
                                        self.stdout.write(self.style.SUCCESS(f"Deleted location '{location.name}' after merging"))
                                    else:
                                        # No conflict, just update the ATUM ID
                                        location.atum_location_id = mapping['atum_id']
                                        location.save()
                                        self.stdout.write(self.style.SUCCESS(f"Updated location '{location.name}' with ATUM ID {mapping['atum_id']}"))
                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f"Error updating location '{location.name}': {str(e)}"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing location '{location_name}': {str(e)}"))
        
        # Summary
        if dry_run:
            self.stdout.write(self.style.NOTICE("\nDRY RUN COMPLETED - No changes were made"))
        else:
            self.stdout.write(self.style.SUCCESS("\nFIX COMPLETED"))
            
        # Verify the results
        self.stdout.write(self.style.NOTICE("\nCurrent ATUM Location Status:"))
        for location_name, mapping in atum_location_mappings.items():
            locations = InventoryLocation.objects.filter(name=mapping['name'])
            for location in locations:
                self.stdout.write(f"Location: {location.name}, ATUM ID: {location.atum_location_id}")
