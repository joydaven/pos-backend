from django.core.management.base import BaseCommand
from crm.woocommerce import WooCommerceAPI
from crm.views import process_customer
from crm.models import Contact
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = '''Comprehensive customer sync tool for WooCommerce to POS
    
Examples:
    # Analyze sync status
    python3 manage.py sync_customers --analyze
    
    # Sync specific customer by email
    python3 manage.py sync_customers --email "waldo.tames@gmail.com"
    
    # Sync specific customer by WooCommerce ID
    python3 manage.py sync_customers --woo-id 12345
    
    # Sync all missing customers
    python3 manage.py sync_customers --sync-all
    
    # Dry run (preview without making changes)
    python3 manage.py sync_customers --sync-all --dry-run
    '''

    def add_arguments(self, parser):
        parser.add_argument(
            '--analyze',
            action='store_true',
            help='Analyze sync status (check how many customers need sync)'
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Sync specific customer by email'
        )
        parser.add_argument(
            '--woo-id',
            type=int,
            help='Sync specific customer by WooCommerce ID'
        )
        parser.add_argument(
            '--sync-all',
            action='store_true',
            help='Sync all missing customers'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without making them'
        )

    def handle(self, *args, **options):
        self.wc_api = WooCommerceAPI()
        
        # Execute based on arguments
        if options['email']:
            self.sync_by_email(options['email'], dry_run=options['dry_run'])
        elif options['woo_id']:
            self.sync_by_woo_id(options['woo_id'], dry_run=options['dry_run'])
        elif options['sync_all']:
            self.sync_all_missing(dry_run=options['dry_run'])
        elif options['analyze']:
            self.analyze_sync_status()
        else:
            # Default to analyze
            self.stdout.write("No action specified. Running analysis...\n")
            self.analyze_sync_status()
            self.stdout.write("\nFor more options, run: python3 manage.py sync_customers --help")
    
    def get_woocommerce_customers(self):
        """Fetch all customers from WooCommerce"""
        self.stdout.write("Fetching customers from WooCommerce...")
        
        try:
            customers = self.wc_api.get_all_customers(include_all_roles=True)
            self.stdout.write(self.style.SUCCESS(f"Found {len(customers)} customers in WooCommerce"))
            return customers
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching WooCommerce customers: {e}"))
            return []
    
    def get_local_customers(self):
        """Fetch all customers from local database"""
        self.stdout.write("Fetching customers from local database...")
        
        try:
            contacts = Contact.objects.all()
            self.stdout.write(self.style.SUCCESS(f"Found {contacts.count()} customers in local database"))
            return contacts
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching local customers: {e}"))
            return []
    
    def find_customer_by_email(self, email):
        """Find a customer in WooCommerce by email"""
        self.stdout.write(f"Searching for customer with email: {email}")
        
        try:
            # WooCommerce search by email
            customers = self.wc_api.wcapi.get("customers", params={"email": email}).json()
            
            if customers and len(customers) > 0:
                return customers[0]
            else:
                return None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error searching for customer: {e}"))
            return None
    
    def find_customer_by_woo_id(self, woo_id):
        """Find a customer in WooCommerce by WooCommerce ID"""
        self.stdout.write(f"Fetching customer with WooCommerce ID: {woo_id}")
        
        try:
            customer = self.wc_api.wcapi.get(f"customers/{woo_id}").json()
            
            if customer and 'id' in customer:
                return customer
            else:
                return None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching customer: {e}"))
            return None
    
    def analyze_sync_status(self):
        """Analyze sync status between WooCommerce and local database"""
        self.stdout.write("=" * 70)
        self.stdout.write("CUSTOMER SYNC ANALYSIS")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Analysis started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Fetch data
        woo_customers = self.get_woocommerce_customers()
        local_customers = self.get_local_customers()
        
        if not woo_customers:
            self.stdout.write(self.style.ERROR("Could not fetch WooCommerce customers. Exiting."))
            return None
        
        # Create lookup maps
        woo_customer_ids = set()
        woo_email_map = {}
        
        for customer in woo_customers:
            customer_id = customer.get('id')
            email = customer.get('email', '').lower().strip()
            
            if customer_id:
                woo_customer_ids.add(customer_id)
            if email:
                woo_email_map[email] = customer
        
        # Local customer IDs
        local_woo_ids = set()
        local_emails = set()
        
        for contact in local_customers:
            if contact.woo_customer_id:
                local_woo_ids.add(contact.woo_customer_id)
            if contact.email:
                local_emails.add(contact.email.lower().strip())
        
        # Calculate differences
        unsynced_ids = woo_customer_ids - local_woo_ids
        synced_ids = woo_customer_ids & local_woo_ids
        unsynced_customers = []
        
        for woo_id in unsynced_ids:
            for customer in woo_customers:
                if customer.get('id') == woo_id:
                    unsynced_customers.append(customer)
                    break
        
        # Display results
        sync_percentage = (len(synced_ids) / len(woo_customers) * 100) if woo_customers else 0
        
        self.stdout.write(f"📊 Total WooCommerce Customers: {len(woo_customers)}")
        self.stdout.write(f"📊 Total Local Database Customers: {len(local_customers)}")
        self.stdout.write(self.style.SUCCESS(f"✅ Synced Customers: {len(synced_ids)} ({sync_percentage:.1f}%)"))
        self.stdout.write(self.style.WARNING(f"❌ Unsynced Customers: {len(unsynced_ids)}"))
        self.stdout.write("")
        
        # Show unsynced customers
        if unsynced_customers:
            self.stdout.write("=" * 70)
            self.stdout.write("UNSYNCED CUSTOMERS (First 20)")
            self.stdout.write("=" * 70)
            
            for i, customer in enumerate(unsynced_customers[:20]):
                customer_id = customer.get('id')
                email = customer.get('email', 'No email')
                first_name = customer.get('first_name', '')
                last_name = customer.get('last_name', '')
                role = customer.get('role', 'customer')
                date_created = customer.get('date_created', '')
                
                name = f"{first_name} {last_name}".strip() or "No name"
                date_str = date_created[:10] if date_created else 'Unknown'
                
                self.stdout.write(f"{i+1:2}. ID: {customer_id:5} | {name:30} | {email:35} | {role:10} | {date_str}")
            
            if len(unsynced_customers) > 20:
                self.stdout.write(f"\n... and {len(unsynced_customers) - 20} more unsynced customers")
            self.stdout.write("")
        
        # Summary
        self.stdout.write("=" * 70)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 70)
        
        if len(unsynced_ids) == 0:
            self.stdout.write(self.style.SUCCESS("🎉 Perfect sync! All WooCommerce customers are in the local database!"))
        else:
            self.stdout.write(self.style.WARNING(f"⚠️  Action needed: {len(unsynced_ids)} customers need to be synced"))
            self.stdout.write("")
            self.stdout.write("📋 Next Steps:")
            self.stdout.write(f"  • To sync all missing customers: python3 manage.py sync_customers --sync-all")
            self.stdout.write(f"  • To sync specific customer: python3 manage.py sync_customers --email 'customer@email.com'")
            self.stdout.write(f"  • To preview changes: add --dry-run flag")
        
        self.stdout.write(f"\nAnalysis completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        return unsynced_customers
    
    def sync_customer(self, customer_data, dry_run=False):
        """Sync a single customer to local database"""
        customer_id = customer_data.get('id')
        email = customer_data.get('email', 'No email')
        first_name = customer_data.get('first_name', '')
        last_name = customer_data.get('last_name', '')
        name = f"{first_name} {last_name}".strip() or "No name"
        
        try:
            # Check if customer already exists
            existing = Contact.objects.filter(woo_customer_id=customer_id).first()
            
            if existing:
                self.stdout.write(f"ℹ️  Customer already exists: {name} ({email}) - WooID: {customer_id}")
                return {'status': 'exists', 'customer_id': customer_id}
            
            if dry_run:
                self.stdout.write(self.style.WARNING(f"🔄 Would sync: {name} ({email}) - WooID: {customer_id}"))
                return {'status': 'dry_run', 'customer_id': customer_id}
            
            # Process customer using existing function (includes GHL sync)
            new_contact = process_customer(customer_data)
            
            self.stdout.write(self.style.SUCCESS(f"✅ Synced: {name} ({email}) - WooID: {customer_id} → Local ID: {new_contact.id}"))
            
            # Show GHL sync status
            if new_contact.ghl_contact_id:
                self.stdout.write(f"   GHL Contact ID: {new_contact.ghl_contact_id}")
            
            return {'status': 'success', 'customer_id': customer_id, 'local_id': str(new_contact.id)}
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error syncing {name} ({email}): {e}"))
            logger.error(f"Error syncing customer {customer_id}: {e}")
            return {'status': 'error', 'customer_id': customer_id, 'error': str(e)}
    
    def sync_all_missing(self, dry_run=False):
        """Sync all missing customers from WooCommerce to local database"""
        self.stdout.write("=" * 70)
        self.stdout.write(f"SYNC ALL MISSING CUSTOMERS {'(DRY RUN)' if dry_run else ''}")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Get unsynced customers
        unsynced_customers = self.analyze_sync_status()
        
        if not unsynced_customers:
            self.stdout.write(self.style.SUCCESS("\n🎉 No customers need syncing!"))
            return
        
        self.stdout.write(f"\n{'=' * 70}")
        self.stdout.write(f"STARTING SYNC OF {len(unsynced_customers)} CUSTOMERS")
        self.stdout.write("=" * 70)
        self.stdout.write("")
        
        # Sync customers
        results = {
            'success': 0,
            'exists': 0,
            'error': 0,
            'dry_run': 0
        }
        
        for i, customer in enumerate(unsynced_customers, 1):
            self.stdout.write(f"[{i}/{len(unsynced_customers)}] ", ending="")
            result = self.sync_customer(customer, dry_run=dry_run)
            
            status = result.get('status')
            if status in results:
                results[status] += 1
        
        # Summary
        self.stdout.write(f"\n{'=' * 70}")
        self.stdout.write("SYNC SUMMARY")
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.SUCCESS(f"✅ Successfully synced: {results['success']}"))
        self.stdout.write(f"ℹ️  Already existed: {results['exists']}")
        self.stdout.write(self.style.ERROR(f"❌ Errors: {results['error']}"))
        if dry_run:
            self.stdout.write(self.style.WARNING(f"🔄 Would sync: {results['dry_run']}"))
        
        self.stdout.write(f"\nCompleted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n🔄 This was a DRY RUN - no changes were made"))
            self.stdout.write("Remove --dry-run flag to apply changes")
    
    def sync_by_email(self, email, dry_run=False):
        """Sync a specific customer by email"""
        self.stdout.write("=" * 70)
        self.stdout.write(f"SYNC CUSTOMER BY EMAIL {'(DRY RUN)' if dry_run else ''}")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Searching for: {email}\n")
        
        # Check if customer exists locally
        local_customer = Contact.objects.filter(email__iexact=email).first()
        
        if local_customer:
            self.stdout.write("ℹ️  Customer already exists in local database:")
            self.stdout.write(f"   Local ID: {local_customer.id}")
            self.stdout.write(f"   Name: {local_customer.first_name} {local_customer.last_name}")
            self.stdout.write(f"   Email: {local_customer.email}")
            self.stdout.write(f"   WooCommerce ID: {local_customer.woo_customer_id or 'Not set'}")
            
            if local_customer.woo_customer_id:
                self.stdout.write(self.style.SUCCESS("\n✅ Customer is already synced!"))
                return
        
        # Search WooCommerce
        woo_customer = self.find_customer_by_email(email)
        
        if not woo_customer:
            self.stdout.write(self.style.ERROR(f"❌ Customer not found in WooCommerce with email: {email}"))
            return
        
        # Display customer info
        woo_id = woo_customer.get('id')
        first_name = woo_customer.get('first_name', '')
        last_name = woo_customer.get('last_name', '')
        name = f"{first_name} {last_name}".strip() or "No name"
        role = woo_customer.get('role', 'customer')
        date_created = woo_customer.get('date_created', '')
        
        self.stdout.write(self.style.SUCCESS("✅ Found in WooCommerce:"))
        self.stdout.write(f"   WooCommerce ID: {woo_id}")
        self.stdout.write(f"   Name: {name}")
        self.stdout.write(f"   Email: {email}")
        self.stdout.write(f"   Role: {role}")
        self.stdout.write(f"   Created: {date_created[:10] if date_created else 'Unknown'}")
        self.stdout.write("")
        
        # Sync
        result = self.sync_customer(woo_customer, dry_run=dry_run)
        
        if result['status'] == 'success':
            self.stdout.write(self.style.SUCCESS("\n🎉 Customer synced successfully!"))
        elif result['status'] == 'dry_run':
            self.stdout.write(self.style.WARNING("\n🔄 Dry run complete - no changes made"))
        
    def sync_by_woo_id(self, woo_id, dry_run=False):
        """Sync a specific customer by WooCommerce ID"""
        self.stdout.write("=" * 70)
        self.stdout.write(f"SYNC CUSTOMER BY WOOCOMMERCE ID {'(DRY RUN)' if dry_run else ''}")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Fetching WooCommerce ID: {woo_id}\n")
        
        # Check if customer exists locally
        local_customer = Contact.objects.filter(woo_customer_id=woo_id).first()
        
        if local_customer:
            self.stdout.write(self.style.SUCCESS("✅ Customer already exists in local database:"))
            self.stdout.write(f"   Local ID: {local_customer.id}")
            self.stdout.write(f"   Name: {local_customer.first_name} {local_customer.last_name}")
            self.stdout.write(f"   Email: {local_customer.email}")
            self.stdout.write(f"   WooCommerce ID: {local_customer.woo_customer_id}")
            return
        
        # Fetch from WooCommerce
        woo_customer = self.find_customer_by_woo_id(woo_id)
        
        if not woo_customer:
            self.stdout.write(self.style.ERROR(f"❌ Customer not found in WooCommerce with ID: {woo_id}"))
            return
        
        # Display customer info
        email = woo_customer.get('email', 'No email')
        first_name = woo_customer.get('first_name', '')
        last_name = woo_customer.get('last_name', '')
        name = f"{first_name} {last_name}".strip() or "No name"
        role = woo_customer.get('role', 'customer')
        date_created = woo_customer.get('date_created', '')
        
        self.stdout.write(self.style.SUCCESS("✅ Found in WooCommerce:"))
        self.stdout.write(f"   WooCommerce ID: {woo_id}")
        self.stdout.write(f"   Name: {name}")
        self.stdout.write(f"   Email: {email}")
        self.stdout.write(f"   Role: {role}")
        self.stdout.write(f"   Created: {date_created[:10] if date_created else 'Unknown'}")
        self.stdout.write("")
        
        # Sync
        result = self.sync_customer(woo_customer, dry_run=dry_run)
        
        if result['status'] == 'success':
            self.stdout.write(self.style.SUCCESS("\n🎉 Customer synced successfully!"))
        elif result['status'] == 'dry_run':
            self.stdout.write(self.style.WARNING("\n🔄 Dry run complete - no changes made"))
