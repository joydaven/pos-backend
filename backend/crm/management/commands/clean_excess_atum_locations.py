import uuid
from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import Product, ProductInventory, InventoryLocation
from django.db.models import Q


class Command(BaseCommand):
    help = 'Cleans up excess ATUM locations that were added to products but are not actually in ATUM'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without making changes')
        parser.add_argument('--product-id', type=str, help='Process only a specific product by ID')
        parser.add_argument('--batch-size', type=int, default=100, help='Number of products to process in each batch')
        parser.add_argument('--keep-zero-quantity', action='store_true', 
                           help='Keep locations with zero quantity (default: remove them)')

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        product_id = options.get('product_id')
        batch_size = options.get('batch_size', 100)
        keep_zero_quantity = options.get('keep_zero_quantity', False)

        self.stdout.write(self.style.SUCCESS('Starting cleanup of excess ATUM locations...'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

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
            
            batch_products = products[start_idx:end_idx]
            self.stdout.write(f'Processing batch {batch_index + 1} of {batch_count} ({len(batch_products)} products)')
            
            for product in batch_products:
                self.process_product(product, dry_run, keep_zero_quantity)
        
        self.stdout.write(self.style.SUCCESS('Excess ATUM location cleanup completed'))

    def process_product(self, product, dry_run, keep_zero_quantity):
        self.stdout.write(f'Processing product: {product.name}')
        
        # Get all inventory records for this product
        inventories = ProductInventory.objects.filter(product=product)
        
        # Identify ATUM inventory records (those with ATUM location ID and proper notes)
        atum_inventories = inventories.filter(
            location__atum_location_id__isnull=False,
            notes__icontains='Synced from ATUM inventory'
        )
        
        # Count how many we have
        atum_count = atum_inventories.count()
        self.stdout.write(f'  Found {atum_count} ATUM inventory locations')
        
        # If we have more than 4 ATUM locations, we need to clean up
        if atum_count > 4:
            self.stdout.write(f'  Product has {atum_count} ATUM locations, cleaning up excess locations')
            
            # First, keep locations with non-zero quantity
            non_zero_inventories = atum_inventories.filter(quantity__gt=0)
            non_zero_count = non_zero_inventories.count()
            
            self.stdout.write(f'  Found {non_zero_count} locations with non-zero quantity')
            
            # If we have 4 or fewer non-zero locations, keep those and remove the rest
            if non_zero_count <= 4:
                to_keep = list(non_zero_inventories.values_list('id', flat=True))
                
                # If we have fewer than 4 non-zero locations, keep some zero-quantity locations
                # to reach a total of 4 locations
                if non_zero_count < 4 and not keep_zero_quantity:
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
                    
                    # Add enough zero-quantity locations to reach 4 total
                    needed = 4 - non_zero_count
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
                            self.stdout.write(self.style.SUCCESS(f'  Removed {remove_count} excess locations'))
                    else:
                        self.stdout.write(f'  Would remove {remove_count} excess locations (dry run)')
                else:
                    self.stdout.write('  No excess locations to remove')
            else:
                # If we have more than 4 non-zero locations, keep only the 4 with highest quantity
                to_keep = list(non_zero_inventories.order_by('-quantity')[:4].values_list('id', flat=True))
                to_remove = atum_inventories.exclude(id__in=to_keep)
                remove_count = to_remove.count()
                
                if remove_count > 0:
                    self.stdout.write(f'  Removing {remove_count} excess ATUM locations, keeping top 4 by quantity')
                    
                    if not dry_run:
                        with transaction.atomic():
                            to_remove.delete()
                            self.stdout.write(self.style.SUCCESS(f'  Removed {remove_count} excess locations'))
                    else:
                        self.stdout.write(f'  Would remove {remove_count} excess locations (dry run)')
        else:
            self.stdout.write(f'  Product has {atum_count} ATUM locations, no cleanup needed')
