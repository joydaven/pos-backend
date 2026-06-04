from django.core.management.base import BaseCommand
from crm.models import Product, POSOrderItem
from crm.woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Clean up duplicate products created from POS orders'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes'
        )
        parser.add_argument(
            '--delete-duplicates',
            action='store_true',
            help='Delete duplicate products from WooCommerce (use with caution)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        delete_duplicates = options['delete_duplicates']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('Running in dry-run mode. No changes will be made.'))
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get all products
        self.stdout.write(self.style.SUCCESS('Fetching all products from database...'))
        products = Product.objects.all()
        self.stdout.write(self.style.SUCCESS(f'Found {products.count()} products in database'))
        
        # Group products by name
        product_groups = {}
        for product in products:
            if product.name not in product_groups:
                product_groups[product.name] = []
            product_groups[product.name].append(product)
        
        # Find duplicate products (same name, different IDs)
        duplicates = {name: products for name, products in product_groups.items() if len(products) > 1}
        self.stdout.write(self.style.SUCCESS(f'Found {len(duplicates)} product names with duplicates'))
        
        # Process each group of duplicates
        for name, duplicate_products in duplicates.items():
            self.stdout.write(self.style.SUCCESS(f'Processing duplicates for product: {name}'))
            self.stdout.write(self.style.SUCCESS(f'  Found {len(duplicate_products)} duplicate products'))
            
            # Find the original product (non-POS created)
            original_product = None
            pos_created_products = []
            
            for product in duplicate_products:
                try:
                    woo_product = wc.get_product(product.woo_product_id)
                    if woo_product and 'description' in woo_product:
                        description = woo_product.get('description', '').strip()
                        if description.startswith('<p>Product created from POS order ORD-'):
                            pos_created_products.append(product)
                            self.stdout.write(self.style.SUCCESS(f'  Product {product.woo_product_id}: POS-created'))
                        else:
                            if original_product is None:
                                original_product = product
                                self.stdout.write(self.style.SUCCESS(f'  Product {product.woo_product_id}: Original product'))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  Error checking product {product.woo_product_id}: {str(e)}'))
            
            # If we didn't find a non-POS product, use the oldest one as original
            if original_product is None and duplicate_products:
                # Sort by WooCommerce ID (lower is older)
                duplicate_products.sort(key=lambda p: p.woo_product_id)
                original_product = duplicate_products[0]
                pos_created_products = duplicate_products[1:]
                self.stdout.write(self.style.SUCCESS(f'  Using oldest product {original_product.woo_product_id} as original'))
            
            if original_product and pos_created_products:
                self.stdout.write(self.style.SUCCESS(f'  Original product: {original_product.woo_product_id}'))
                self.stdout.write(self.style.SUCCESS(f'  POS-created duplicates: {[p.woo_product_id for p in pos_created_products]}'))
                
                # Update order items to reference the original product
                for pos_product in pos_created_products:
                    order_items = POSOrderItem.objects.filter(product_id=str(pos_product.id))
                    self.stdout.write(self.style.SUCCESS(f'  Product {pos_product.woo_product_id} has {order_items.count()} order items'))
                    
                    if not dry_run:
                        order_items.update(product_id=str(original_product.id))
                        self.stdout.write(self.style.SUCCESS(f'  Updated {order_items.count()} order items to reference original product {original_product.id}'))
                    else:
                        self.stdout.write(self.style.SUCCESS(f'  Would update {order_items.count()} order items to reference original product {original_product.id}'))
                    
                    # Delete the duplicate product from database
                    if not dry_run:
                        # Delete from WooCommerce if requested
                        if delete_duplicates:
                            try:
                                response = wc.wcapi.delete(f"products/{pos_product.woo_product_id}", params={"force": True})
                                if response.ok:
                                    self.stdout.write(self.style.SUCCESS(f'  Deleted product {pos_product.woo_product_id} from WooCommerce'))
                                else:
                                    self.stdout.write(self.style.ERROR(f'  Failed to delete product {pos_product.woo_product_id} from WooCommerce: {response.status_code}'))
                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f'  Error deleting product {pos_product.woo_product_id} from WooCommerce: {str(e)}'))
                        
                        # Delete from database
                        pos_product.delete()
                        self.stdout.write(self.style.SUCCESS(f'  Deleted product {pos_product.woo_product_id} from database'))
                    else:
                        if delete_duplicates:
                            self.stdout.write(self.style.SUCCESS(f'  Would delete product {pos_product.woo_product_id} from WooCommerce'))
                        self.stdout.write(self.style.SUCCESS(f'  Would delete product {pos_product.woo_product_id} from database'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run completed. No changes were made.'))
            self.stdout.write(self.style.WARNING('Run without --dry-run to apply changes.'))
        else:
            self.stdout.write(self.style.SUCCESS('Duplicate cleanup completed successfully.'))
