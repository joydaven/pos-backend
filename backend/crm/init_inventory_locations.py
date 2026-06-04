from django.core.management.base import BaseCommand
from crm.models_inventory import InventoryLocation, ProductInventory
from crm.models import Product

class Command(BaseCommand):
    help = 'Initialize inventory locations and assign products to them'

    def handle(self, *args, **options):
        # Create default inventory locations if they don't exist
        clinic_inventory, created = InventoryLocation.objects.get_or_create(
            code='clinic',
            defaults={
                'name': 'Clinic Inventory',
                'description': 'Fulfill from clinic\'s local inventory',
                'is_default': True,
                'is_active': True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created inventory location: {clinic_inventory.name}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Inventory location already exists: {clinic_inventory.name}'))

        warehouse, created = InventoryLocation.objects.get_or_create(
            code='warehouse',
            defaults={
                'name': 'Warehouse',
                'description': 'Ship from central warehouse',
                'is_default': False,
                'is_active': True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created inventory location: {warehouse.name}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Inventory location already exists: {warehouse.name}'))

        # Assign all products to both locations with default values
        products = Product.objects.all()
        self.stdout.write(self.style.SUCCESS(f'Found {products.count()} products to assign to inventory locations'))

        for product in products:
            # Assign to clinic inventory
            clinic_inventory_item, created = ProductInventory.objects.get_or_create(
                product=product,
                location=clinic_inventory,
                defaults={
                    'quantity': product.stock_quantity or 0,
                    'reorder_level': 5,
                    'reorder_quantity': 10,
                    'is_available': product.stock_status == 'instock'

                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Assigned product {product.name} to {clinic_inventory.name}'))

            # Assign to warehouse
            warehouse_inventory_item, created = ProductInventory.objects.get_or_create(
                product=product,
                location=warehouse,
                defaults={
                    'quantity': product.stock_quantity or 0,
                    'reorder_level': 10,
                    'reorder_quantity': 20,
                    'is_available': product.stock_status == 'instock'
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Assigned product {product.name} to {warehouse.name}'))

        self.stdout.write(self.style.SUCCESS('Inventory locations initialized successfully'))

import logging
logger = logging.getLogger(__name__)

warehouse, created = InventoryLocation.objects.get_or_create(
    code='warehouse',
    defaults={
        'name': 'Warehouse',
        'description': 'Ship from central warehouse',
        'is_default': False,
        'is_active': True
    }
)
if created:
    logger.info(f'Created inventory location: {warehouse.name}')
    self.stdout.write(self.style.SUCCESS(f'Created inventory location: {warehouse.name}'))
else:
    logger.info(f'Inventory location already exists: {warehouse.name}')
    self.stdout.write(self.style.SUCCESS(f'Inventory location already exists: {warehouse.name}'))

# Assign all products to both locations with default values
products = Product.objects.all()
logger.info(f'Found {products.count()} products to assign to inventory locations')
self.stdout.write(self.style.SUCCESS(f'Found {products.count()} products to assign to inventory locations'))

for product in products:
    # Assign to clinic inventory
    clinic_inventory_item, created = ProductInventory.objects.get_or_create(
        product=product,
        location=clinic_inventory,
        defaults={
            'quantity': product.stock_quantity or 0,
            'reorder_level': 5,
            'reorder_quantity': 10,
            'is_available': product.stock_status == 'instock'
            
        }
    )
    if created:
        logger.info(f'Assigned product {product.name} to {clinic_inventory.name}')
        self.stdout.write(self.style.SUCCESS(f'Assigned product {product.name} to {clinic_inventory.name}'))

    # Assign to warehouse
    warehouse_inventory_item, created = ProductInventory.objects.get_or_create(
        product=product,
        location=warehouse,
        defaults={
            'quantity': product.stock_quantity or 0,
            'reorder_level': 10,
            'reorder_quantity': 20,
            'is_available': product.stock_status == 'instock'
        }
    )
    if created:
        logger.info(f'Assigned product {product.name} to {warehouse.name}')
        self.stdout.write(self.style.SUCCESS(f'Assigned product {product.name} to {warehouse.name}'))

logger.info('Inventory locations initialized successfully')
self.stdout.write(self.style.SUCCESS('Inventory locations initialized successfully'))