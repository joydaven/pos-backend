from django.core.management.base import BaseCommand
from django.core.management import call_command
from crm.woocommerce import WooCommerceAPI
from crm.ghl_api import search_ghl_contact_by_email
from crm.models import Contact, Order, CustomerPointsAccount
from datetime import datetime
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

BETA_EMAIL_DOMAIN = '@doctorsstudio.com'


class Command(BaseCommand):
    help = '''Comprehensive sync for beta testers (@doctorsstudio.com emails)
    
This command syncs multiple data types for beta users only:
- Customers (from WooCommerce)
- Orders (from WooCommerce)
- GHL Contact IDs (from GoHighLevel)
- YITH Points (from WooCommerce)
- Subscriptions (from WooCommerce)

Examples:
    # Analyze what needs to be synced
    python manage.py sync_beta_data --analyze
    
    # Sync everything for beta users
    python manage.py sync_beta_data --sync-all
    
    # Sync specific data types only
    python manage.py sync_beta_data --customers --orders --ghl
    
    # Dry run (preview without making changes)
    python manage.py sync_beta_data --sync-all --dry-run
    '''

    def add_arguments(self, parser):
        parser.add_argument(
            '--analyze',
            action='store_true',
            help='Analyze what data needs to be synced for beta users'
        )
        parser.add_argument(
            '--sync-all',
            action='store_true',
            help='Sync all data types for beta users'
        )
        parser.add_argument(
            '--customers',
            action='store_true',
            help='Sync customers from WooCommerce'
        )
        parser.add_argument(
            '--orders',
            action='store_true',
            help='Sync orders from WooCommerce'
        )
        parser.add_argument(
            '--ghl',
            action='store_true',
            help='Sync GHL contact IDs'
        )
        parser.add_argument(
            '--points',
            action='store_true',
            help='Sync YITH points'
        )
        parser.add_argument(
            '--subscriptions',
            action='store_true',
            help='Sync subscriptions from WooCommerce'
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
            help='Limit number of items to sync per category'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.limit = options.get('limit')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("\n🔍 DRY RUN MODE - No changes will be made\n"))
        
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("🧪 BETA USER DATA SYNC")
        self.stdout.write(f"   Target: {BETA_EMAIL_DOMAIN} emails")
        self.stdout.write("=" * 70 + "\n")
        
        if options['analyze']:
            self.analyze_all()
        elif options['sync_all']:
            self.sync_customers()
            self.sync_orders()
            self.sync_ghl_contact_ids()
            self.sync_points()
            self.sync_subscriptions()
            self.print_final_summary()
        else:
            # Sync specific data types
            any_selected = False
            if options['customers']:
                self.sync_customers()
                any_selected = True
            if options['orders']:
                self.sync_orders()
                any_selected = True
            if options['ghl']:
                self.sync_ghl_contact_ids()
                any_selected = True
            if options['points']:
                self.sync_points()
                any_selected = True
            if options['subscriptions']:
                self.sync_subscriptions()
                any_selected = True
            
            if not any_selected:
                self.stdout.write("No action specified. Running analysis...\n")
                self.analyze_all()
                self.stdout.write("\nFor more options, run: python manage.py sync_beta_data --help")

    def get_beta_contacts(self):
        """Get all contacts with @doctorsstudio.com emails"""
        return Contact.objects.filter(email__iendswith=BETA_EMAIL_DOMAIN)

    def analyze_all(self):
        """Analyze all data types for beta users"""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("📊 ANALYSIS REPORT")
        self.stdout.write("-" * 70 + "\n")
        
        beta_contacts = self.get_beta_contacts()
        self.stdout.write(f"📧 Beta Users Found: {beta_contacts.count()}\n")
        
        if beta_contacts.count() == 0:
            self.stdout.write(self.style.WARNING("No beta users found in local database."))
            self.stdout.write("Checking WooCommerce for beta customers...\n")
            self._analyze_woo_beta_customers()
            return
        
        self._analyze_orders(beta_contacts)
        self._analyze_ghl(beta_contacts)
        self._analyze_points(beta_contacts)
        self._analyze_subscriptions(beta_contacts)
        
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("💡 RECOMMENDATIONS")
        self.stdout.write("-" * 70)
        self.stdout.write("Run: python manage.py sync_beta_data --sync-all")
        self.stdout.write("Or selectively: python manage.py sync_beta_data --orders --ghl")

    def _analyze_woo_beta_customers(self):
        """Check WooCommerce for beta customers"""
        try:
            wc_api = WooCommerceAPI()
            all_customers = wc_api.get_all_customers(include_all_roles=True)
            beta_customers = [c for c in all_customers if c.get('email', '').endswith(BETA_EMAIL_DOMAIN)]
            self.stdout.write(f"Found {len(beta_customers)} beta customers in WooCommerce:")
            for c in beta_customers[:10]:
                self.stdout.write(f"  - {c.get('email')}")
            if len(beta_customers) > 10:
                self.stdout.write(f"  ... and {len(beta_customers) - 10} more")
            self.stdout.write("\nRun: python manage.py sync_beta_data --customers")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error checking WooCommerce: {e}"))

    def _analyze_orders(self, beta_contacts):
        """Analyze order sync status"""
        self.stdout.write("\n📦 ORDERS")
        total_local = 0
        total_woo = 0
        
        wc_api = WooCommerceAPI()
        
        for contact in beta_contacts:
            local_count = Order.objects.filter(contact=contact).count()
            total_local += local_count
            
            if contact.woo_customer_id:
                try:
                    response = wc_api.wcapi.get("orders", params={
                        "customer": contact.woo_customer_id,
                        "per_page": 1
                    })
                    if response.status_code == 200:
                        # Get total from headers
                        woo_count = int(response.headers.get('X-WP-Total', 0))
                        total_woo += woo_count
                except:
                    pass
        
        self.stdout.write(f"   Local Orders: {total_local}")
        self.stdout.write(f"   WooCommerce Orders: {total_woo}")
        self.stdout.write(f"   To Sync: {max(0, total_woo - total_local)}")

    def _analyze_ghl(self, beta_contacts):
        """Analyze GHL sync status"""
        self.stdout.write("\n🔗 GHL CONTACT IDS")
        with_ghl = beta_contacts.filter(ghl_contact_id__isnull=False).count()
        without_ghl = beta_contacts.filter(ghl_contact_id__isnull=True).count()
        self.stdout.write(f"   With GHL ID: {with_ghl}")
        self.stdout.write(f"   Missing GHL ID: {without_ghl}")

    def _analyze_points(self, beta_contacts):
        """Analyze points sync status"""
        self.stdout.write("\n⭐ YITH POINTS")
        with_points = CustomerPointsAccount.objects.filter(
            customer__in=beta_contacts
        ).count()
        self.stdout.write(f"   With Points Account: {with_points}")
        self.stdout.write(f"   Without Points Account: {beta_contacts.count() - with_points}")

    def _analyze_subscriptions(self, beta_contacts):
        """Analyze subscription sync status"""
        self.stdout.write("\n🔄 SUBSCRIPTIONS")
        woo_ids = list(beta_contacts.filter(woo_customer_id__isnull=False).values_list('woo_customer_id', flat=True))
        self.stdout.write(f"   Beta users with WooCommerce ID: {len(woo_ids)}")
        self.stdout.write("   (Subscription details fetched on-demand from WooCommerce)")

    def sync_customers(self):
        """Sync beta customers from WooCommerce"""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("👥 SYNCING BETA CUSTOMERS")
        self.stdout.write("-" * 70 + "\n")
        
        try:
            wc_api = WooCommerceAPI()
            all_customers = wc_api.get_all_customers(include_all_roles=True)
            beta_customers = [c for c in all_customers if c.get('email', '').endswith(BETA_EMAIL_DOMAIN)]
            
            self.stdout.write(f"Found {len(beta_customers)} beta customers in WooCommerce\n")
            
            synced = 0
            skipped = 0
            
            for woo_customer in beta_customers:
                email = woo_customer.get('email', '').lower()
                woo_id = woo_customer.get('id')
                
                existing = Contact.objects.filter(email__iexact=email).first()
                
                if existing:
                    # Update WooCommerce ID if missing
                    if not existing.woo_customer_id and woo_id:
                        if not self.dry_run:
                            existing.woo_customer_id = woo_id
                            existing.save()
                        self.stdout.write(f"  ✅ Updated {email} with WooCommerce ID {woo_id}")
                        synced += 1
                    else:
                        skipped += 1
                else:
                    if self.dry_run:
                        self.stdout.write(f"  [DRY RUN] Would create: {email}")
                    else:
                        Contact.objects.create(
                            email=email,
                            first_name=woo_customer.get('first_name', ''),
                            last_name=woo_customer.get('last_name', ''),
                            woo_customer_id=woo_id,
                            phone=woo_customer.get('billing', {}).get('phone', ''),
                            primary_source='woo'
                        )
                        self.stdout.write(self.style.SUCCESS(f"  ✅ Created: {email}"))
                    synced += 1
            
            self.stdout.write(f"\n📊 Customers: {synced} synced, {skipped} skipped")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error syncing customers: {e}"))

    def sync_orders(self):
        """Sync orders for beta users"""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("📦 SYNCING BETA ORDERS")
        self.stdout.write("-" * 70 + "\n")
        
        # Use the dedicated beta orders command
        try:
            args = ['sync_beta_orders', '--sync-all']
            if self.dry_run:
                args.append('--dry-run')
            if self.limit:
                args.extend(['--limit', str(self.limit)])
            call_command(*args)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error syncing orders: {e}"))

    def sync_ghl_contact_ids(self):
        """Sync GHL contact IDs for beta users"""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("🔗 SYNCING GHL CONTACT IDS")
        self.stdout.write("-" * 70 + "\n")
        
        beta_contacts = self.get_beta_contacts().filter(ghl_contact_id__isnull=True)
        self.stdout.write(f"Found {beta_contacts.count()} beta users missing GHL IDs\n")
        
        synced = 0
        not_found = 0
        errors = 0
        
        for contact in beta_contacts:
            try:
                if self.dry_run:
                    self.stdout.write(f"  [DRY RUN] Would search GHL for: {contact.email}")
                    synced += 1
                    continue
                
                ghl_result = search_ghl_contact_by_email(contact.email)
                
                if ghl_result and ghl_result.get('id'):
                    contact.ghl_contact_id = ghl_result['id']
                    contact.ghl_last_sync = timezone.now()
                    contact.ghl_data = ghl_result
                    contact.save()
                    self.stdout.write(self.style.SUCCESS(
                        f"  ✅ {contact.email} → {ghl_result['id']}"
                    ))
                    synced += 1
                else:
                    self.stdout.write(f"  ⚠️  {contact.email} - Not found in GHL")
                    not_found += 1
                    
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ❌ {contact.email}: {e}"))
                errors += 1
        
        self.stdout.write(f"\n📊 GHL: {synced} synced, {not_found} not found, {errors} errors")

    def sync_points(self):
        """Sync YITH points for beta users"""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("⭐ SYNCING YITH POINTS")
        self.stdout.write("-" * 70 + "\n")
        
        beta_contacts = self.get_beta_contacts().filter(woo_customer_id__isnull=False)
        self.stdout.write(f"Found {beta_contacts.count()} beta users with WooCommerce IDs\n")
        
        try:
            wc_api = WooCommerceAPI()
            synced = 0
            errors = 0
            
            for contact in beta_contacts:
                try:
                    points_data = wc_api.get_customer_points(contact.woo_customer_id)
                    
                    if points_data:
                        points_balance = points_data.get('points_balance', 0)
                        
                        if self.dry_run:
                            self.stdout.write(f"  [DRY RUN] {contact.email}: {points_balance} points")
                        else:
                            # Create or update points account
                            account, created = CustomerPointsAccount.objects.get_or_create(
                                customer=contact,
                                defaults={'points_balance': points_balance}
                            )
                            if not created:
                                account.points_balance = points_balance
                                account.save()
                            
                            action = "Created" if created else "Updated"
                            self.stdout.write(self.style.SUCCESS(
                                f"  ✅ {contact.email}: {points_balance} points ({action})"
                            ))
                        synced += 1
                    else:
                        self.stdout.write(f"  ⚠️  {contact.email}: No points data")
                        
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  ❌ {contact.email}: {e}"))
                    errors += 1
            
            self.stdout.write(f"\n📊 Points: {synced} synced, {errors} errors")
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error syncing points: {e}"))

    def sync_subscriptions(self):
        """Note about subscriptions - they're fetched on-demand"""
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write("🔄 SUBSCRIPTIONS")
        self.stdout.write("-" * 70 + "\n")
        
        self.stdout.write("ℹ️  Subscriptions are fetched on-demand from WooCommerce API")
        self.stdout.write("   No local sync needed - data is always current from WooCommerce")
        
        beta_contacts = self.get_beta_contacts().filter(woo_customer_id__isnull=False)
        self.stdout.write(f"\n   {beta_contacts.count()} beta users can access subscription data")

    def print_final_summary(self):
        """Print final sync summary"""
        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("✅ BETA SYNC COMPLETE")
        self.stdout.write("=" * 70 + "\n")
        
        beta_contacts = self.get_beta_contacts()
        self.stdout.write(f"📧 Total Beta Users: {beta_contacts.count()}")
        self.stdout.write(f"🔗 With GHL ID: {beta_contacts.filter(ghl_contact_id__isnull=False).count()}")
        self.stdout.write(f"📦 Total Orders: {Order.objects.filter(contact__in=beta_contacts).count()}")
        
        points_accounts = CustomerPointsAccount.objects.filter(customer__in=beta_contacts).count()
        self.stdout.write(f"⭐ With Points Account: {points_accounts}")
