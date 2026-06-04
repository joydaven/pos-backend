from django.core.management.base import BaseCommand
from crm.models import InventoryLocation, ProductInventory, Product

class Command(BaseCommand):
    help = 'Check inventory location configuration in the database'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Checking inventory location configuration...'))
        
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
        for loc in non_atum_locations[:5]:  # Limit to 5 to avoid too much output
            self.stdout.write(f"ID: {loc.id}, Name: {loc.name}")
        if non_atum_locations.count() > 5:
            self.stdout.write(f"... and {non_atum_locations.count() - 5} more")

        # Check product inventory
        self.stdout.write(self.style.SUCCESS('\n=== PRODUCT INVENTORY ==='))
        total_inventory = ProductInventory.objects.count()
        self.stdout.write(f"Total product inventory records: {total_inventory}")
        
        # Check inventory records with ATUM locations
        atum_inventory = ProductInventory.objects.filter(location__atum_location_id__isnull=False)
        self.stdout.write(f"\nInventory records with ATUM locations: {atum_inventory.count()}")
        
        # Sample some ATUM inventory records
        self.stdout.write("\nSample ATUM inventory records:")
        for inv in atum_inventory[:5]:
            self.stdout.write(f"Product: {inv.product.name}, Location: {inv.location.name}, Quantity: {inv.quantity}")
            self.stdout.write(f"  Notes: {inv.notes}")
        
        # Check inventory records with non-ATUM locations
        non_atum_inventory = ProductInventory.objects.filter(location__atum_location_id__isnull=True)
        self.stdout.write(f"\nInventory records with non-ATUM locations: {non_atum_inventory.count()}")
        
        # Sample some non-ATUM inventory records
        self.stdout.write("\nSample non-ATUM inventory records:")
        for inv in non_atum_inventory[:5]:
            try:
                product_name = inv.product.name if inv.product else "Unknown Product"
                self.stdout.write(f"Product: {product_name}, Location: {inv.location.name}, Quantity: {inv.quantity}")
                self.stdout.write(f"  Notes: {inv.notes}")
            except Exception as e:
                self.stdout.write(f"Error with inventory record {inv.id}: {str(e)}")

        # Check sample product
        self.stdout.write(self.style.SUCCESS('\n=== SAMPLE PRODUCT INVENTORY CHECK ==='))
        # Try to find a product with both ATUM and non-ATUM inventory
        products_with_atum = Product.objects.filter(productinventory__location__atum_location_id__isnull=False).distinct()
        
        if products_with_atum.exists():
            sample_product = products_with_atum.first()
            self.stdout.write(f"Sample Product: {sample_product.name} (ID: {sample_product.id})")
            
            # Get all inventory for this product
            all_inventory = ProductInventory.objects.filter(product=sample_product)
            self.stdout.write(f"Total inventory records: {all_inventory.count()}")
            
            # Get ATUM inventory for this product
            atum_inventory = all_inventory.filter(location__atum_location_id__isnull=False)
            self.stdout.write(f"ATUM inventory records: {atum_inventory.count()}")
            for inv in atum_inventory:
                try:
                    self.stdout.write(f"  Location: {inv.location.name} (ATUM ID: {inv.location.atum_location_id}), Quantity: {inv.quantity}")
                    self.stdout.write(f"    Notes: {inv.notes}")
                except Exception as e:
                    self.stdout.write(f"Error with ATUM inventory record: {str(e)}")
            
            # Get non-ATUM inventory for this product
            non_atum_inventory = all_inventory.filter(location__atum_location_id__isnull=True)
            self.stdout.write(f"Non-ATUM inventory records: {non_atum_inventory.count()}")
            for inv in non_atum_inventory:
                try:
                    self.stdout.write(f"  Location: {inv.location.name}, Quantity: {inv.quantity}")
                    self.stdout.write(f"    Notes: {inv.notes}")
                except Exception as e:
                    self.stdout.write(f"Error with non-ATUM inventory record: {str(e)}")
        else:
            self.stdout.write("No products with ATUM inventory found")
