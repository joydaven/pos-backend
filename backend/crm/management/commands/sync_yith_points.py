from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from decimal import Decimal
from crm.models import Contact, CustomerPointsAccount, PointsTransaction
from crm.woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync points between local fractional DB and YITH (periodic task)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )
        parser.add_argument(
            '--customer-id',
            type=str,
            help='Sync only a specific customer by UUID',
        )
        parser.add_argument(
            '--direction',
            type=str,
            choices=['to-yith', 'from-yith', 'both'],
            default='both',
            help='Sync direction: to-yith, from-yith, or both',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        customer_id = options.get('customer_id')
        direction = options['direction']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        wc_api = WooCommerceAPI()
        
        # Get customers to sync
        if customer_id:
            customers = Contact.objects.filter(
                id=customer_id,
                woo_customer_id__isnull=False
            )
        else:
            # Sync all customers with WooCommerce IDs and points accounts
            customers = Contact.objects.filter(
                woo_customer_id__isnull=False,
                points_account__isnull=False
            )
        
        total_customers = customers.count()
        self.stdout.write(f'Found {total_customers} customers to sync')
        
        synced_to_yith = 0
        synced_from_yith = 0
        conflicts_resolved = 0
        errors = 0
        
        for customer in customers:
            try:
                # Get local points account
                try:
                    local_account = customer.points_account
                except CustomerPointsAccount.DoesNotExist:
                    self.stdout.write(f'  ⏩ Skipping {customer.first_name} {customer.last_name} - no local account')
                    continue
                
                # Get YITH points
                yith_points_data = wc_api.get_customer_points(customer.woo_customer_id)
                if not yith_points_data:
                    self.stdout.write(f'  ⚠️  Could not fetch YITH points for {customer.first_name} {customer.last_name}')
                    errors += 1
                    continue
                
                yith_points = yith_points_data.get('points_to_redeem', 0)
                local_points = float(local_account.points_balance)
                local_synced = local_account.yith_synced_points
                
                self.stdout.write(f'\n  {customer.first_name} {customer.last_name}:')
                self.stdout.write(f'    Local: {local_points:.2f} | YITH: {yith_points} | Last Synced: {local_synced}')
                
                # Detect conflicts: YITH changed outside of POS
                if yith_points != local_synced and direction in ['from-yith', 'both']:
                    difference = yith_points - local_synced
                    self.stdout.write(self.style.WARNING(f'    ⚠️  CONFLICT: YITH changed by {difference} points'))
                    
                    if not dry_run:
                        # Add difference to local balance (admin adjustment in WP)
                        local_account.add_points(
                            amount=Decimal(str(abs(difference))),
                            description=f'YITH sync adjustment: {difference} points from external source',
                            transaction_type='sync'
                        )
                        conflicts_resolved += 1
                        self.stdout.write(self.style.SUCCESS(f'    ✅ Adjusted local balance by {difference} points'))
                
                # Sync local to YITH if needed
                if direction in ['to-yith', 'both']:
                    integer_points = int(local_account.points_balance)
                    
                    if integer_points != yith_points:
                        self.stdout.write(f'    🔄 Syncing to YITH: {integer_points} points')
                        
                        if not dry_run:
                            sync_result = wc_api.set_customer_points(customer.woo_customer_id, integer_points)
                            
                            if sync_result.get('success'):
                                local_account.yith_synced_points = integer_points
                                local_account.yith_last_sync = timezone.now()
                                local_account.sync_needed = False
                                local_account.save()
                                synced_to_yith += 1
                                self.stdout.write(self.style.SUCCESS(f'    ✅ Synced to YITH: {integer_points} points'))
                            else:
                                self.stdout.write(self.style.ERROR(f'    ❌ Failed to sync to YITH: {sync_result.get("error")}'))
                                errors += 1
                    else:
                        # Update sync metadata even if values match
                        if not dry_run and local_account.sync_needed:
                            local_account.yith_synced_points = integer_points
                            local_account.yith_last_sync = timezone.now()
                            local_account.sync_needed = False
                            local_account.save()
                        self.stdout.write('    ✓ Already in sync')
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'  ❌ Error syncing {customer.first_name} {customer.last_name}: {str(e)}'
                    )
                )
                logger.error(f'Sync error for customer {customer.id}: {str(e)}', exc_info=True)
                errors += 1
        
        # Summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS(f'Sync Summary:'))
        self.stdout.write(f'  Total customers: {total_customers}')
        if direction in ['to-yith', 'both']:
            self.stdout.write(self.style.SUCCESS(f'  ✅ Synced to YITH: {synced_to_yith}'))
        if direction in ['from-yith', 'both']:
            self.stdout.write(self.style.SUCCESS(f'  ✅ Conflicts resolved: {conflicts_resolved}'))
        self.stdout.write(self.style.ERROR(f'  ❌ Errors: {errors}'))
        
        if dry_run:
            self.stdout.write(self.style.WARNING('\nDRY RUN COMPLETE - No changes were made'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ Sync complete!'))
