from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from crm.models import InventoryLocation, ProductInventory, Product
from crm.woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync ATUM inventory locations and product inventory data from WooCommerce'

    def add_arguments(self, parser):
        parser.add_argument(
            '--locations-only',
            action='store_true',
            help='Sync only ATUM locations without product inventory data',
        )
        parser.add_argument(
            '--products-only',
            action='store_true',
            help='Sync only product inventory data without locations',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without making changes',
        )
        parser.add_argument(
            '--product-id',
            type=int,
            help='Sync inventory for a specific product ID only',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting ATUM inventory sync...'))
        
        # Initialize WooCommerce API
        try:
            wc_api = WooCommerceAPI()
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Failed to initialize WooCommerce API: {str(e)}')
            )
            return

        dry_run = options['dry_run']
        locations_only = options['locations_only']
        products_only = options['products_only']
        product_id = options['product_id']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        try:
            if not products_only:
                self.sync_atum_locations(wc_api, dry_run)
            
            if not locations_only:
                self.sync_product_inventory(wc_api, dry_run, product_id)
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error during sync: {str(e)}')
            )
            logger.error(f'ATUM sync error: {str(e)}')

    def sync_atum_locations(self, wc_api, dry_run=False):
        """Sync ATUM inventory locations from WooCommerce"""
        self.stdout.write('Syncing ATUM locations...')
        
        try:
            # Get all ATUM locations from inventory records
            atum_locations = wc_api.get_atum_locations_from_inventories()
            
            if not atum_locations:
                self.stdout.write(self.style.WARNING('No ATUM locations found in WooCommerce'))
                return

            locations_created = 0
            locations_updated = 0
            
            for location_data in atum_locations:
                atum_id = location_data.get('id')
                name = location_data.get('name', '')
                slug = location_data.get('slug', '')
                description = location_data.get('description', '')
                parent_id = location_data.get('parent', 0)
                count = location_data.get('count', 0)
                barcode = location_data.get('barcode', '')
                
                if not atum_id or not name:
                    self.stdout.write(
                        self.style.WARNING(f'Skipping location with missing ID or name: {location_data}')
                    )
                    continue

                # Generate a unique code from slug or name
                code = slug.upper() if slug else name.upper().replace(' ', '-')
                
                if dry_run:
                    self.stdout.write(f'Would sync location: {name} (ATUM ID: {atum_id})')
                    continue

                try:
                    with transaction.atomic():
                        # Try to find existing location by ATUM ID
                        location, created = InventoryLocation.objects.get_or_create(
                            atum_location_id=atum_id,
                            defaults={
                                'name': name,
                                'code': code,
                                'slug': slug,
                                'description': description,
                                'parent_location_id': parent_id if parent_id > 0 else None,
                                'product_count': count,
                                'barcode': barcode,
                                'is_active': True,
                                'is_default': False,
                                'address': ''
                            }
                        )
                        
                        if created:
                            locations_created += 1
                            self.stdout.write(
                                self.style.SUCCESS(f'Created location: {name} (ATUM ID: {atum_id})')
                            )
                        else:
                            # Update existing location
                            location.name = name
                            location.slug = slug
                            location.description = description
                            location.parent_location_id = parent_id if parent_id > 0 else None
                            location.product_count = count
                            location.barcode = barcode
                            location.is_active = True
                            location.save()
                            
                            locations_updated += 1
                            self.stdout.write(f'Updated location: {name} (ATUM ID: {atum_id})')
                            
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'Error syncing location {name}: {str(e)}')
                    )
                    logger.error(f'Error syncing ATUM location {atum_id}: {str(e)}')

            if not dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'ATUM locations sync completed: {locations_created} created, {locations_updated} updated'
                    )
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error syncing ATUM locations: {str(e)}')
            )
            raise

    def sync_product_inventory(self, wc_api, dry_run=False, product_id=None):
        """Sync product inventory data with ATUM locations"""
        self.stdout.write('Syncing product inventory with ATUM locations...')
        
        try:
            # Get products from our database
            if product_id:
                products = Product.objects.filter(woo_product_id=product_id)
                if not products.exists():
                    self.stdout.write(
                        self.style.WARNING(f'Product with WooCommerce ID {product_id} not found locally')
                    )
                    return
            else:
                products = Product.objects.all()
            
            total_products = products.count()
            
            if total_products == 0:
                self.stdout.write(self.style.WARNING('No products found in database'))
                return

            inventory_created = 0
            inventory_updated = 0
            processed = 0
            
            self.stdout.write(f'Processing {total_products} products...')
            
            for product in products:
                processed += 1
                
                if processed % 100 == 0:
                    self.stdout.write(f'Processed {processed}/{total_products} products...')
                
                try:
                    # Get ATUM inventory data for this product
                    product_inventories = wc_api.get_product_inventories(product.woo_product_id)
                    
                    if not product_inventories:
                        continue
                    
                    for inventory_data in product_inventories:
                        inventory_id = inventory_data.get('id')
                        location_name = inventory_data.get('name', '')
                        quantity = inventory_data.get('meta_data', {}).get('stock_quantity', 0)
                        
                        # Convert quantity to int if it's a string
                        try:
                            quantity = int(quantity) if quantity is not None else 0
                        except (ValueError, TypeError):
                            quantity = 0
                        
                        if not location_name:
                            continue
                        
                        # Find the corresponding InventoryLocation by name
                        # Handle location name mappings for common mismatches
                        location_name_mappings = {
                            'Boca Clinic': 'Boca Raton Clinic',
                            'Jupiter Clinic': 'Jupiter Clinic',
                            'Chicago Clinic': 'Chicago Clinic',
                            'Vendor Fulfillment': 'Vendor Fulfillment',
                            'Dropship': 'Dropship',
                        }
                        
                        # Use mapped name if available, otherwise use original
                        mapped_location_name = location_name_mappings.get(location_name, location_name)
                        
                        try:
                            # Only sync inventory for locations that have a valid ATUM location ID
                            inventory_location = InventoryLocation.objects.get(
                                name=mapped_location_name,
                                atum_location_id__isnull=False
                            )
                        except InventoryLocation.DoesNotExist:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'ATUM location "{location_name}" not found in database for product {product.woo_product_id}'
                                )
                            )
                            continue
                        
                        if dry_run:
                            self.stdout.write(
                                f'Would sync inventory: Product {product.woo_product_id} at location {inventory_location.name} (qty: {quantity})'
                            )
                            continue
                            
                        try:
                            with transaction.atomic():
                                # Create or update inventory record
                                inventory, created = ProductInventory.objects.update_or_create(
                                    product=product,
                                    location=inventory_location,
                                    defaults={
                                        'quantity': quantity,
                                        'notes': f'Synced from ATUM inventory {inventory_id} at {mapped_location_name} [ATUM]'
                                    }
                                )
                                
                                if created:
                                    inventory_created += 1
                                else:
                                    # Update existing inventory
                                    inventory.quantity = quantity
                                    inventory.is_available = quantity > 0
                                    inventory.notes = f'Synced from ATUM inventory {inventory_id} at {mapped_location_name} [ATUM]'
                                    inventory.save()
                                    
                                    inventory_updated += 1
                                    
                        except Exception as e:
                            self.stdout.write(
                                self.style.ERROR(
                                    f'Error syncing inventory for product {product.woo_product_id} at location {location_name}: {str(e)}'
                                )
                            )
                            logger.error(f'Error syncing product inventory: {str(e)}')
                            
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Error getting ATUM data for product {product.woo_product_id}: {str(e)}'
                        )
                    )
                    continue

            if not dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Product inventory sync completed: {inventory_created} created, {inventory_updated} updated'
                    )
                )

        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error syncing product inventory: {str(e)}')
            )
            raise
