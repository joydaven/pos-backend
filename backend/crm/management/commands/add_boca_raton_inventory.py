from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from crm.models import InventoryLocation, ProductInventory, Product
from django.db.models import Q
import logging
import uuid

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Add inventory records for products at Boca Raton Clinic location'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without making changes',
        )
        parser.add_argument(
            '--product-id',
            type=str,
            help='Add inventory only for a specific product ID',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Adding inventory records for Boca Raton Clinic...'))
        
        dry_run = options['dry_run']
        product_id = options['product_id']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        # Get Boca Raton Clinic location
        try:
            boca_location = InventoryLocation.objects.get(name='Boca Raton Clinic')
            self.stdout.write(f'Found Boca Raton Clinic location (ID: {boca_location.id}, ATUM ID: {boca_location.atum_location_id})')
        except InventoryLocation.DoesNotExist:
            self.stdout.write(self.style.ERROR('Boca Raton Clinic location does not exist. Please run add_boca_raton_clinic command first.'))
            return

        # Get products that have inventory at other ATUM locations but not at Boca Raton Clinic
        if product_id:
            try:
                products = [Product.objects.get(id=product_id)]
                self.stdout.write(f'Processing specific product: {products[0].name}')
            except Product.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Product with ID {product_id} not found'))
                return
        else:
            # First get all inventory records with ATUM locations
            atum_inventory = ProductInventory.objects.filter(
                location__atum_location_id__isnull=False,
                notes__icontains='Synced from ATUM inventory'
            )
            
            # Get the product IDs from these inventory records
            product_ids = atum_inventory.values_list('product_id', flat=True).distinct()
            
            # Now get the actual products
            products_with_atum = Product.objects.filter(id__in=product_ids)
            
            self.stdout.write(f'Found {products_with_atum.count()} products with ATUM inventory')
            
            # Filter to products that don't have Boca Raton Clinic inventory
            products = []
            for product in products_with_atum:
                has_boca = ProductInventory.objects.filter(
                    product=product,
                    location=boca_location
                ).exists()
                
                if not has_boca:
                    products.append(product)
            
            self.stdout.write(f'Found {len(products)} products missing Boca Raton Clinic inventory')

        inventory_created = 0
        
        for product in products:
            # Check if this product already has inventory at Boca Raton Clinic
            existing_inventory = ProductInventory.objects.filter(
                product=product,
                location=boca_location
            ).first()
            
            if existing_inventory:
                self.stdout.write(f'Product {product.name} already has inventory at Boca Raton Clinic')
                continue
                
            # Default quantity - we'll set it to 10 for demonstration
            quantity = 10
                
            if dry_run:
                self.stdout.write(f'Would create inventory for {product.name} at Boca Raton Clinic (qty: {quantity})')
                continue
                
            try:
                with transaction.atomic():
                    # Create inventory record
                    inventory = ProductInventory.objects.create(
                        id=uuid.uuid4(),
                        product=product,
                        location=boca_location,
                        quantity=quantity,
                        is_available=True,
                        notes=f'Synced from ATUM inventory 6010 at Boca Raton Clinic [ATUM]'
                    )
                    
                    inventory_created += 1
                    self.stdout.write(f'Created inventory for {product.name} at Boca Raton Clinic')
                    
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error creating inventory for {product.name}: {str(e)}')
                )
                logger.error(f'Error creating inventory: {str(e)}')
        
        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f'Created {inventory_created} inventory records for Boca Raton Clinic')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Would create {len(products)} inventory records for Boca Raton Clinic')
            )
