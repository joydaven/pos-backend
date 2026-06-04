from django.core.management.base import BaseCommand
from crm.woocommerce import WooCommerceAPI
from crm.models import Contact, Order
from datetime import datetime
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

BETA_EMAIL_DOMAIN = '@doctorsstudio.com'


class Command(BaseCommand):
    help = '''Sync orders from WooCommerce for beta testers only (@doctorsstudio.com emails)
    
Examples:
    # Analyze beta users and their orders
    python manage.py sync_beta_orders --analyze
    
    # Sync all orders for beta users
    python manage.py sync_beta_orders --sync-all
    
    # Sync orders for a specific beta user email
    python manage.py sync_beta_orders --email "user@doctorsstudio.com"
    
    # Dry run (preview without making changes)
    python manage.py sync_beta_orders --sync-all --dry-run
    
    # Limit number of orders to sync
    python manage.py sync_beta_orders --sync-all --limit 50
    '''

    def add_arguments(self, parser):
        parser.add_argument(
            '--analyze',
            action='store_true',
            help='Analyze beta users and their order counts'
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Sync orders for a specific beta user email'
        )
        parser.add_argument(
            '--sync-all',
            action='store_true',
            help='Sync all orders for all beta users'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without making them'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit number of orders to sync per user'
        )

    def handle(self, *args, **options):
        self.wc_api = WooCommerceAPI()
        self.dry_run = options['dry_run']
        self.limit = options.get('limit')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("🔍 DRY RUN MODE - No changes will be made\n"))
        
        if options['email']:
            if not options['email'].endswith(BETA_EMAIL_DOMAIN):
                self.stdout.write(self.style.ERROR(f"Email must end with {BETA_EMAIL_DOMAIN}"))
                return
            self.sync_orders_for_email(options['email'])
        elif options['sync_all']:
            self.sync_all_beta_orders()
        elif options['analyze']:
            self.analyze_beta_users()
        else:
            self.stdout.write("No action specified. Running analysis...\n")
            self.analyze_beta_users()
            self.stdout.write("\nFor more options, run: python manage.py sync_beta_orders --help")

    def get_beta_contacts(self):
        """Get all contacts with @doctorsstudio.com emails"""
        return Contact.objects.filter(email__iendswith=BETA_EMAIL_DOMAIN)

    def analyze_beta_users(self):
        """Analyze beta users and their order status"""
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("📊 BETA USER ANALYSIS")
        self.stdout.write("=" * 60 + "\n")
        
        beta_contacts = self.get_beta_contacts()
        self.stdout.write(f"Found {beta_contacts.count()} beta users ({BETA_EMAIL_DOMAIN})\n")
        
        if beta_contacts.count() == 0:
            self.stdout.write(self.style.WARNING("No beta users found in local database."))
            self.stdout.write("You may need to sync customers first: python manage.py sync_customers")
            return
        
        total_local_orders = 0
        total_woo_orders = 0
        
        for contact in beta_contacts:
            local_orders = Order.objects.filter(contact=contact).count()
            total_local_orders += local_orders
            
            # Check WooCommerce for orders
            woo_orders = self._get_orders_for_customer(contact.woo_customer_id) if contact.woo_customer_id else []
            woo_order_count = len(woo_orders)
            total_woo_orders += woo_order_count
            
            status_icon = "✅" if local_orders == woo_order_count else "⚠️"
            self.stdout.write(
                f"  {status_icon} {contact.email}: "
                f"Local={local_orders}, WooCommerce={woo_order_count}"
            )
        
        self.stdout.write("\n" + "-" * 60)
        self.stdout.write(f"📈 Total Local Orders: {total_local_orders}")
        self.stdout.write(f"📈 Total WooCommerce Orders: {total_woo_orders}")
        self.stdout.write(f"📈 Orders to Sync: {total_woo_orders - total_local_orders}")

    def _get_orders_for_customer(self, woo_customer_id):
        """Fetch all orders for a customer from WooCommerce"""
        if not woo_customer_id:
            return []
        
        try:
            all_orders = []
            page = 1
            per_page = 100
            
            while True:
                response = self.wc_api.wcapi.get("orders", params={
                    "customer": woo_customer_id,
                    "per_page": per_page,
                    "page": page
                })
                
                if response.status_code != 200:
                    logger.error(f"Failed to get orders for customer {woo_customer_id}: {response.status_code}")
                    break
                
                orders = response.json()
                if not orders:
                    break
                
                all_orders.extend(orders)
                
                if len(orders) < per_page:
                    break
                
                page += 1
                
                if self.limit and len(all_orders) >= self.limit:
                    all_orders = all_orders[:self.limit]
                    break
            
            return all_orders
        except Exception as e:
            logger.error(f"Error fetching orders for customer {woo_customer_id}: {e}")
            return []

    def sync_orders_for_email(self, email):
        """Sync orders for a specific beta user email"""
        self.stdout.write(f"\n🔄 Syncing orders for: {email}\n")
        
        try:
            contact = Contact.objects.get(email__iexact=email)
        except Contact.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Contact not found: {email}"))
            self.stdout.write("You may need to sync customers first: python manage.py sync_customers")
            return
        
        if not contact.woo_customer_id:
            self.stdout.write(self.style.ERROR(f"Contact has no WooCommerce ID: {email}"))
            return
        
        self._sync_orders_for_contact(contact)

    def sync_all_beta_orders(self):
        """Sync orders for all beta users"""
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("🔄 SYNCING ORDERS FOR ALL BETA USERS")
        self.stdout.write("=" * 60 + "\n")
        
        beta_contacts = self.get_beta_contacts()
        
        if beta_contacts.count() == 0:
            self.stdout.write(self.style.WARNING("No beta users found."))
            return
        
        total_synced = 0
        total_skipped = 0
        total_errors = 0
        
        for contact in beta_contacts:
            result = self._sync_orders_for_contact(contact)
            total_synced += result['synced']
            total_skipped += result['skipped']
            total_errors += result['errors']
        
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("📊 SYNC SUMMARY")
        self.stdout.write("=" * 60)
        self.stdout.write(f"✅ Orders Synced: {total_synced}")
        self.stdout.write(f"⏭️  Orders Skipped (already exist): {total_skipped}")
        self.stdout.write(f"❌ Errors: {total_errors}")

    def _sync_orders_for_contact(self, contact):
        """Sync orders for a single contact"""
        result = {'synced': 0, 'skipped': 0, 'errors': 0}
        
        self.stdout.write(f"\n📧 {contact.email}")
        
        if not contact.woo_customer_id:
            self.stdout.write(self.style.WARNING("  ⚠️  No WooCommerce ID - skipping"))
            return result
        
        woo_orders = self._get_orders_for_customer(contact.woo_customer_id)
        self.stdout.write(f"  Found {len(woo_orders)} orders in WooCommerce")
        
        for woo_order in woo_orders:
            woo_order_id = str(woo_order.get('id'))
            
            # Check if order already exists
            if Order.objects.filter(woo_order_id=woo_order_id).exists():
                result['skipped'] += 1
                continue
            
            if self.dry_run:
                self.stdout.write(f"  [DRY RUN] Would sync order #{woo_order_id}")
                result['synced'] += 1
                continue
            
            try:
                # Parse order date
                order_date_str = woo_order.get('date_created')
                if order_date_str:
                    order_date = datetime.fromisoformat(order_date_str.replace('Z', '+00:00'))
                else:
                    order_date = timezone.now()
                
                # Create order record
                Order.objects.create(
                    contact=contact,
                    woo_order_id=woo_order_id,
                    order_date=order_date,
                    total_amount=woo_order.get('total', 0),
                    status=woo_order.get('status', 'unknown')
                )
                
                result['synced'] += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  ✅ Synced order #{woo_order_id} - ${woo_order.get('total')} ({woo_order.get('status')})"
                ))
            except Exception as e:
                result['errors'] += 1
                self.stdout.write(self.style.ERROR(f"  ❌ Error syncing order #{woo_order_id}: {e}"))
        
        return result
