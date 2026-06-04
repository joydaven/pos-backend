from django.core.management.base import BaseCommand
from crm.woocommerce import WooCommerceAPI
from crm.views import process_product
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync products from WooCommerce with enhanced product type support'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limit the number of products to sync (0 for all)'
        )
        parser.add_argument(
            '--page',
            type=int,
            default=1,
            help='Start from this page'
        )
        parser.add_argument(
            '--per-page',
            type=int,
            default=100,
            help='Number of products per page'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        page = options['page']
        per_page = options['per_page']
        
        self.stdout.write(self.style.SUCCESS(f'Starting WooCommerce product sync (limit={limit}, page={page}, per_page={per_page})'))
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get first page to determine total
        first_page = wc.get_products(page=page, per_page=per_page)
        if not first_page or 'total' not in first_page:
            self.stdout.write(self.style.ERROR('Failed to get product count from WooCommerce'))
            return
            
        total_products = first_page['total']
        total_pages = first_page.get('total_pages', 1)
        
        self.stdout.write(self.style.SUCCESS(f'Found {total_products} products across {total_pages} pages'))
        
        # Set limit if specified
        if limit > 0 and limit < total_products:
            self.stdout.write(self.style.SUCCESS(f'Limiting to {limit} products'))
            total_products = limit
        
        processed_count = 0
        
        # Process first page results
        if first_page.get('data'):
            for product in first_page['data']:
                try:
                    product_obj = process_product(product, wc)
                    processed_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'[{processed_count}/{total_products}] Processed product {product["id"]}: {product["name"]} '
                        f'(Type: {product_obj.product_type})'
                    ))
                    
                    # Check if we've reached the limit
                    if limit > 0 and processed_count >= limit:
                        self.stdout.write(self.style.SUCCESS(f'Reached limit of {limit} products'))
                        return
                        
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'Error processing product {product["id"]}: {str(e)}'))
                    logger.error(f'Error processing product {product["id"]}: {str(e)}')
        
        # Process remaining pages
        current_page = page + 1
        
        while current_page <= total_pages and processed_count < total_products:
            try:
                self.stdout.write(self.style.SUCCESS(f'Processing page {current_page}/{total_pages}'))
                result = wc.get_products(page=current_page, per_page=per_page)
                
                if result and result.get('data'):
                    for product in result['data']:
                        try:
                            product_obj = process_product(product, wc)
                            processed_count += 1
                            self.stdout.write(self.style.SUCCESS(
                                f'[{processed_count}/{total_products}] Processed product {product["id"]}: {product["name"]} '
                                f'(Type: {product_obj.product_type})'
                            ))
                            
                            # Check if we've reached the limit
                            if limit > 0 and processed_count >= limit:
                                self.stdout.write(self.style.SUCCESS(f'Reached limit of {limit} products'))
                                return
                                
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f'Error processing product {product["id"]}: {str(e)}'))
                            logger.error(f'Error processing product {product["id"]}: {str(e)}')
                            continue
                            
                current_page += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error processing page {current_page}: {str(e)}'))
                logger.error(f'Error processing page {current_page}: {str(e)}')
                current_page += 1
        
        self.stdout.write(self.style.SUCCESS(f'Completed product sync. Processed {processed_count} products.'))
