from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
from crm.models import Contact, CustomerPointsAccount, PointsTransaction
from crm.woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Migrate existing YITH points to local fractional points system'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--customer-id',
            type=str,
            help='Migrate only a specific customer by UUID',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        customer_id = options.get('customer_id')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        wc_api = WooCommerceAPI()
        
        # Get customers to migrate
        if customer_id:
            customers = Contact.objects.filter(id=customer_id, woo_customer_id__isnull=False)
        else:
            customers = Contact.objects.filter(woo_customer_id__isnull=False)
        
        total_customers = customers.count()
        self.stdout.write(f'Found {total_customers} customers with WooCommerce IDs')
        
        migrated = 0
        skipped = 0
        errors = 0
        
        for customer in customers:
            try:
                # Check if account already exists
                existing_account = CustomerPointsAccount.objects.filter(customer=customer).first()
                if existing_account:
                    self.stdout.write(f'  ⏩ Skipping {customer.first_name} {customer.last_name} - account already exists')
                    skipped += 1
                    continue
                
                # Get YITH points
                points_data = wc_api.get_customer_points(customer.woo_customer_id)
                
                if not points_data:
                    self.stdout.write(f'  ⚠️  No points data for {customer.first_name} {customer.last_name}')
                    skipped += 1
                    continue
                
                yith_points = points_data.get('points_to_redeem', 0)
                
                if yith_points == 0:
                    self.stdout.write(f'  ⏩ Skipping {customer.first_name} {customer.last_name} - 0 points')
                    skipped += 1
                    continue
                
                if not dry_run:
                    with transaction.atomic():
                        # Create points account
                        account = CustomerPointsAccount.objects.create(
                            customer=customer,
                            points_balance=Decimal(str(yith_points)),
                            yith_synced_points=yith_points,
                            sync_needed=False
                        )
                        
                        # Create initial transaction
                        PointsTransaction.objects.create(
                            customer=customer,
                            account=account,
                            transaction_type='sync',
                            amount=Decimal(str(yith_points)),
                            balance_after=Decimal(str(yith_points)),
                            description=f'Initial migration from YITH Points & Rewards',
                            metadata={
                                'source': 'yith',
                                'migration': True,
                                'yith_points_collected': points_data.get('points_collected', 0),
                                'yith_rank': points_data.get('rank', ''),
                            }
                        )
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  ✅ Migrated {customer.first_name} {customer.last_name}: {yith_points} points'
                    )
                )
                migrated += 1
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'  ❌ Error migrating {customer.first_name} {customer.last_name}: {str(e)}'
                    )
                )
                logger.error(f'Migration error for customer {customer.id}: {str(e)}', exc_info=True)
                errors += 1
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'Migration Summary:'))
        self.stdout.write(f'  Total customers: {total_customers}')
        self.stdout.write(self.style.SUCCESS(f'  ✅ Migrated: {migrated}'))
        self.stdout.write(self.style.WARNING(f'  ⏩ Skipped: {skipped}'))
        self.stdout.write(self.style.ERROR(f'  ❌ Errors: {errors}'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN COMPLETE - No changes were made'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ Migration complete!'))
