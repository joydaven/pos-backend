from django.core.management.base import BaseCommand
from crm.models import InventoryLocation, ProductInventory
from django.db.models import Count

class Command(BaseCommand):
    help = 'Check ATUM inventory locations in the database'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Checking ATUM inventory locations...'))
        
        # Check inventory locations
        self.stdout.write(self.style.SUCCESS('\n=== INVENTORY LOCATIONS ==='))
        total_locations = InventoryLocation.objects.count()
        self.stdout.write(f"Total locations: {total_locations}")
        
        # Check ATUM locations
        atum_locations = InventoryLocation.objects.filter(atum_location_id__isnull=False)
        self.stdout.write(f"\nATUM Locations ({atum_locations.count()}):")
        for loc in atum_locations:
            self.stdout.write(f"ID: {loc.id}, Name: {loc.name}, ATUM ID: {loc.atum_location_id}")
        
        # Check non-ATUM locations
        non_atum_locations = InventoryLocation.objects.filter(atum_location_id__isnull=True)
        self.stdout.write(f"\nNon-ATUM Locations ({non_atum_locations.count()}):")
        for loc in non_atum_locations:
            self.stdout.write(f"ID: {loc.id}, Name: {loc.name}")
        
        # Count inventory records by location type
        self.stdout.write(self.style.SUCCESS('\n=== INVENTORY RECORDS BY LOCATION TYPE ==='))
        atum_inventory_count = ProductInventory.objects.filter(location__atum_location_id__isnull=False).count()
        non_atum_inventory_count = ProductInventory.objects.filter(location__atum_location_id__isnull=True).count()
        self.stdout.write(f"Inventory records with ATUM locations: {atum_inventory_count}")
        self.stdout.write(f"Inventory records with non-ATUM locations: {non_atum_inventory_count}")
        
        # Check inventory records by location
        self.stdout.write(self.style.SUCCESS('\n=== INVENTORY RECORDS BY LOCATION ==='))
        location_counts = ProductInventory.objects.values('location__name', 'location__atum_location_id').annotate(
            count=Count('id')
        ).order_by('-count')
        
        for loc_data in location_counts:
            location_name = loc_data['location__name']
            atum_id = loc_data['location__atum_location_id']
            count = loc_data['count']
            
            if atum_id:
                self.stdout.write(f"ATUM Location: {location_name} (ATUM ID: {atum_id}) - {count} inventory records")
            else:
                self.stdout.write(f"Non-ATUM Location: {location_name} - {count} inventory records")
        
        # Check inventory notes for ATUM references
        self.stdout.write(self.style.SUCCESS('\n=== INVENTORY NOTES ANALYSIS ==='))
        atum_notes_count = ProductInventory.objects.filter(notes__icontains='ATUM').count()
        synced_notes_count = ProductInventory.objects.filter(notes__icontains='Synced from ATUM inventory').count()
        
        self.stdout.write(f"Inventory records with 'ATUM' in notes: {atum_notes_count}")
        self.stdout.write(f"Inventory records with 'Synced from ATUM inventory' in notes: {synced_notes_count}")
        
        # Sample some inventory notes
        self.stdout.write(self.style.SUCCESS('\n=== SAMPLE INVENTORY NOTES ==='))
        atum_inventory_samples = ProductInventory.objects.filter(
            location__atum_location_id__isnull=False
        ).order_by('?')[:5]
        
        self.stdout.write("ATUM location inventory notes samples:")
        for inv in atum_inventory_samples:
            try:
                self.stdout.write(f"Location: {inv.location.name} - Notes: {inv.notes}")
            except Exception as e:
                self.stdout.write(f"Error with inventory record: {str(e)}")
        
        non_atum_inventory_samples = ProductInventory.objects.filter(
            location__atum_location_id__isnull=True
        ).order_by('?')[:5]
        
        self.stdout.write("\nNon-ATUM location inventory notes samples:")
        for inv in non_atum_inventory_samples:
            try:
                self.stdout.write(f"Location: {inv.location.name} - Notes: {inv.notes}")
            except Exception as e:
                self.stdout.write(f"Error with inventory record: {str(e)}")
