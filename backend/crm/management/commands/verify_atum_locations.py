from django.core.management.base import BaseCommand
from crm.models import Product, ProductInventory
from collections import Counter


class Command(BaseCommand):
    help = 'Verify ATUM location counts across all products'

    def handle(self, *args, **options):
        self.stdout.write("Verifying ATUM location counts across all products...")
        
        # Get all products
        products = Product.objects.all()
        total_products = products.count()
        self.stdout.write(f"Total products in database: {total_products}")
        
        # Initialize counters
        location_counts = Counter()
        
        # Count ATUM locations for each product
        for p in products:
            count = ProductInventory.objects.filter(
                product=p, 
                location__atum_location_id__isnull=False, 
                notes__contains='Synced from ATUM inventory'
            ).count()
            location_counts[count] += 1
        
        # Print distribution
        self.stdout.write("\nATUM Location Count Distribution:")
        for count in sorted(location_counts.keys()):
            percentage = (location_counts[count] / total_products) * 100
            self.stdout.write(f"{count} locations: {location_counts[count]} products ({percentage:.1f}%)")
        
        # Print summary
        self.stdout.write("\nSummary:")
        two_locations = location_counts.get(2, 0)
        self.stdout.write(f"Products with 2 ATUM locations: {two_locations} ({(two_locations/total_products)*100:.1f}%)")
        self.stdout.write(f"Products with other than 2 ATUM locations: {total_products - two_locations} ({((total_products - two_locations)/total_products)*100:.1f}%)")
