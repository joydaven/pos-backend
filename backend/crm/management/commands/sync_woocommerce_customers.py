from django.core.management.base import BaseCommand
from crm.woocommerce import WooCommerceAPI
from crm.views import process_customer
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync customers from WooCommerce'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limit the number of customers to sync (0 for all)'
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
            help='Number of customers per page'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        page = options['page']
        per_page = options['per_page']
        
        self.stdout.write(self.style.SUCCESS(f'Starting WooCommerce customer sync (limit={limit}, page={page}, per_page={per_page})'))
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Get all customers using the API method (including all user roles)
        self.stdout.write(self.style.SUCCESS('Fetching customers from WooCommerce (including all user roles)...'))
        customers = wc.get_all_customers(include_all_roles=True)
        
        if not customers:
            self.stdout.write(self.style.ERROR('No customers found or failed to fetch customers from WooCommerce'))
            return
            
        total_customers = len(customers)
        self.stdout.write(self.style.SUCCESS(f'Found {total_customers} customers to sync'))
        
        # Set limit if specified
        if limit > 0 and limit < total_customers:
            self.stdout.write(self.style.SUCCESS(f'Limiting to {limit} customers'))
            customers = customers[:limit]
            total_customers = limit
        
        processed_count = 0
        
        # Process customers
        for customer in customers:
            try:
                process_customer(customer)
                processed_count += 1
                self.stdout.write(self.style.SUCCESS(
                    f'[{processed_count}/{total_customers}] Processed customer {customer["id"]}: {customer["first_name"]} {customer["last_name"]} ({customer["email"]})'
                ))
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error processing customer {customer["id"]}: {str(e)}'))
                logger.error(f'Error processing customer {customer["id"]}: {str(e)}')
        
        self.stdout.write(self.style.SUCCESS(f'Completed customer sync. Processed {processed_count} customers.'))
