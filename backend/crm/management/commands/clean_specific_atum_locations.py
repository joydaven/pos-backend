from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import Product, ProductInventory
import logging
import time

class Command(BaseCommand):
    help = 'Cleans up excess ATUM inventory locations, keeping only the original specific locations for each product'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Run in dry-run mode without making changes')
        parser.add_argument('--product-id', type=str, help='Process a specific product by ID')
        parser.add_argument('--batch-size', type=int, default=50, help='Number of products to process in each batch')
        parser.add_argument('--keep-zero-quantity', action='store_true', help='Keep zero quantity locations if needed')
        parser.add_argument('--max-locations', type=int, default=4, help='Maximum number of ATUM locations to keep per product')
        parser.add_argument('--min-locations', type=int, default=2, help='Minimum number of ATUM locations to keep per product')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        product_id = options.get('product_id')
        batch_size = options.get('batch_size', 50)
        keep_zero_quantity = options.get('keep_zero_quantity', False)
        max_locations = options.get('max_locations', 4)
        min_locations = options.get('min_locations', 2)
        
        self.stdout.write('Starting cleanup of excess ATUM locations...')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('Running in DRY RUN mode - no changes will be made'))
        
        # Get all products with inventory
        if product_id:
            try:
                products = Product.objects.filter(id=product_id)
                if not products.exists():
                    self.stdout.write(self.style.ERROR(f'Product with ID {product_id} not found'))
                    return
                self.stdout.write(f'Processing specific product: {products[0].name}')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error finding product: {e}'))
                return
        else:
            # Get all products that have inventory records
            products = Product.objects.filter(
                inventory_locations__isnull=False
            ).distinct()
            self.stdout.write(f'Found {products.count()} products with inventory records')

        # Process products in batches
        total_products = products.count()
        batch_count = (total_products + batch_size - 1) // batch_size  # Ceiling division
        
        for batch_index in range(batch_count):
            start_idx = batch_index * batch_size
            end_idx = min((batch_index + 1) * batch_size, total_products)
            
            self.stdout.write(f'Processing batch {batch_index + 1} of {batch_count} ({end_idx - start_idx} products)')
            
            batch_products = products[start_idx:end_idx]
            for product in batch_products:
                self.process_product(product, dry_run, keep_zero_quantity, max_locations, min_locations)
                
        self.stdout.write('Excess ATUM location cleanup completed')

    def process_product(self, product, dry_run, keep_zero_quantity, max_locations, min_locations):
        self.stdout.write(f'Processing product: {product.name}')
        
        # Get all inventory records for this product with ATUM location IDs
        atum_inventories = ProductInventory.objects.filter(
            product=product,
            location__atum_location_id__isnull=False,
            notes__contains='Synced from ATUM inventory'
        )
        
        atum_count = atum_inventories.count()
        self.stdout.write(f'  Found {atum_count} ATUM inventory locations')
        
        # If the product has more than max_locations ATUM locations, clean up excess locations
        if atum_count > max_locations:
            self.stdout.write(f'  Product has {atum_count} ATUM locations, cleaning up excess locations')
            
            # First, get all non-zero quantity inventories
            non_zero_inventories = atum_inventories.filter(quantity__gt=0).order_by('-quantity')
            non_zero_count = non_zero_inventories.count()
            
            self.stdout.write(f'  Found {non_zero_count} locations with non-zero quantity')
            
            # Determine how many locations to keep based on non-zero quantity
            # Keep at least min_locations, but no more than max_locations
            locations_to_keep = max(min(non_zero_count, max_locations), min_locations)
            
            # If we have more non-zero locations than we want to keep, keep only the top ones by quantity
            if non_zero_count > locations_to_keep:
                to_keep = list(non_zero_inventories.order_by('-quantity')[:locations_to_keep].values_list('id', flat=True))
                self.stdout.write(f'  Keeping top {locations_to_keep} locations by quantity')
            else:
                # Keep all non-zero locations
                to_keep = list(non_zero_inventories.values_list('id', flat=True))
                
                # If we have fewer non-zero locations than min_locations, keep some zero-quantity locations
                if non_zero_count < locations_to_keep and not keep_zero_quantity:
                    # Prioritize common locations: Vendor Fulfillment, Jupiter Clinic, Chicago Clinic, Boca Raton Clinic
                    priority_locations = [1, 2, 3, 5]  # ATUM location IDs
                    
                    # Get zero quantity inventories
                    zero_inventories = atum_inventories.filter(quantity=0)
                    
                    # Create a list to store prioritized inventories
                    prioritized_inventories = []
                    
                    # First add priority locations
                    for priority_id in priority_locations:
                        priority_items = zero_inventories.filter(location__atum_location_id=priority_id)
                        prioritized_inventories.extend(list(priority_items))
                    
                    # Then add remaining locations
                    remaining = zero_inventories.exclude(location__atum_location_id__in=priority_locations)
                    prioritized_inventories.extend(list(remaining))
                    
                    # Convert to a list that we can slice
                    zero_inventories = prioritized_inventories
                    
                    # Add enough zero-quantity locations to reach min_locations
                    needed = locations_to_keep - non_zero_count
                    additional_to_keep = [item.id for item in zero_inventories[:needed]]
                    to_keep.extend(additional_to_keep)
                    
                    self.stdout.write(f'  Keeping {len(additional_to_keep)} additional zero-quantity locations')
            
            # Remove excess locations
            to_remove = atum_inventories.exclude(id__in=to_keep)
            remove_count = to_remove.count()
            
            if remove_count > 0:
                self.stdout.write(f'  Removing {remove_count} excess ATUM locations')
                
                if not dry_run:
                    with transaction.atomic():
                        to_remove.delete()
                    self.stdout.write(f'  Removed {remove_count} excess locations')
            else:
                self.stdout.write('  No excess locations to remove')
        else:
            self.stdout.write(f'  Product has {atum_count} ATUM locations, no cleanup needed')
