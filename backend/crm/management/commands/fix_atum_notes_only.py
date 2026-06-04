import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import InventoryLocation, ProductInventory

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Fix ATUM inventory location sync issues by standardizing notes format without changing ATUM location IDs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--product-id',
            type=str,
            help='Specific product ID to fix (optional)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        product_id = options.get('product_id')
        
        # Define the correct ATUM location mappings
        atum_location_mappings = {
            'Boca Clinic': {'name': 'Boca Raton Clinic', 'atum_id': 3},
            'Boca Raton Clinic': {'name': 'Boca Raton Clinic', 'atum_id': 3},
            'Chicago Clinic': {'name': 'Chicago Clinic', 'atum_id': 4},
            'Jupiter Clinic': {'name': 'Jupiter Clinic', 'atum_id': 2},
            'Vendor Fulfillment': {'name': 'Vendor Fulfillment', 'atum_id': 1},
            'Dropship': {'name': 'Dropship', 'atum_id': 7},
        }
        
        # 1. Fix ProductInventory records - standardize notes format
        self.stdout.write(self.style.NOTICE('Fixing ProductInventory records...'))
        inventory_fixed = 0
        
        # Build query for product inventory records
        inventory_query = ProductInventory.objects.select_related('location', 'product')
        if product_id:
            inventory_query = inventory_query.filter(product_id=product_id)
        
        for inventory in inventory_query:
            location_name = inventory.location.name
            
            # Skip non-ATUM locations like Default Location and Main Warehouse
            if location_name not in atum_location_mappings and not any(name in location_name for name in atum_location_mappings):
                continue
                
            # Find the correct mapping for this location
            mapping = None
            for key, value in atum_location_mappings.items():
                if key in location_name:
                    mapping = value
                    break
            
            if not mapping:
                self.stdout.write(f"Could not find mapping for location: {location_name}")
                continue
                
            needs_update = False
            update_reasons = []
            
            # Check if notes contain "Synced from ATUM inventory"
            if "Synced from ATUM inventory" not in inventory.notes:
                update_reasons.append("Missing 'Synced from ATUM inventory' in notes")
                needs_update = True
                
            # Check if notes contain [ATUM] tag
            if "[ATUM]" not in inventory.notes:
                update_reasons.append("Missing [ATUM] tag in notes")
                needs_update = True
                
            if needs_update:
                self.stdout.write(f"Product '{inventory.product.name}' at '{location_name}' needs update: {', '.join(update_reasons)}")
                
                if not dry_run:
                    try:
                        with transaction.atomic():
                            # Extract inventory ID from existing notes if possible
                            inventory_id = None
                            import re
                            match = re.search(r'(\d+) at', inventory.notes)
                            if match:
                                inventory_id = match.group(1)
                            
                            # Create standardized note
                            if inventory_id:
                                inventory.notes = f"Synced from ATUM inventory {inventory_id} at {mapping['name']} [ATUM]"
                            else:
                                inventory.notes = f"Synced from ATUM inventory at {mapping['name']} [ATUM]"
                            
                            inventory.save()
                            inventory_fixed += 1
                            self.stdout.write(self.style.SUCCESS(f"Fixed inventory for '{inventory.product.name}' at '{mapping['name']}'"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error fixing inventory for '{inventory.product.name}': {str(e)}"))
        
        # Summary
        if dry_run:
            self.stdout.write(self.style.NOTICE(f"\nDRY RUN SUMMARY:"))
            self.stdout.write(f"Would fix {inventory_fixed} inventory records")
        else:
            self.stdout.write(self.style.SUCCESS(f"\nFIX COMPLETED:"))
            self.stdout.write(f"Fixed {inventory_fixed} inventory records")
