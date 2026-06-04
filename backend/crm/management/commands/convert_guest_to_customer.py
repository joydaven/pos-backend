from django.core.management.base import BaseCommand
from crm.woocommerce import WooCommerceAPI
from crm.views import process_customer
from crm.models import Contact
from datetime import datetime
import logging
import secrets
import string

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = '''Convert guest checkout customers to registered WooCommerce customers
    
Examples:
    # Check if guest can be converted (dry run)
    python3 manage.py convert_guest_to_customer --email "guest@email.com" --dry-run
    
    # Convert guest to customer
    python3 manage.py convert_guest_to_customer --email "guest@email.com"
    '''

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            required=True,
            help='Email address of the guest customer'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview conversion without creating account'
        )

    def handle(self, *args, **options):
        email = options['email']
        dry_run = options['dry_run']
        
        self.stdout.write("=" * 70)
        self.stdout.write(f"CONVERT GUEST TO CUSTOMER {'(DRY RUN)' if dry_run else ''}")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Email: {email}\n")
        
        wc_api = WooCommerceAPI()
        
        # Step 1: Find guest orders
        self.stdout.write("Step 1: Searching for guest orders...")
        guest_orders = self.find_guest_orders(wc_api, email)
        
        if not guest_orders:
            self.stdout.write(self.style.ERROR(f"❌ No guest orders found for: {email}"))
            return
        
        self.stdout.write(self.style.SUCCESS(f"✅ Found {len(guest_orders)} guest orders\n"))
        
        # Display orders
        self.stdout.write("📦 Guest Order Details:")
        for i, order in enumerate(guest_orders[:5], 1):
            self.stdout.write(f"   {i}. Order #{order['id']} - ${order['total']} - {order['date_created'][:10]}")
            self.stdout.write(f"      Name: {order['billing']['first_name']} {order['billing']['last_name']}")
        
        if len(guest_orders) > 5:
            self.stdout.write(f"   ... and {len(guest_orders) - 5} more orders")
        
        self.stdout.write("")
        
        # Step 2: Check if customer already exists
        self.stdout.write("Step 2: Checking if customer account already exists...")
        try:
            response = wc_api.wcapi.get("customers", params={"email": email})
            existing_customers = response.json() if response.status_code == 200 else []
            
            if existing_customers and len(existing_customers) > 0:
                customer = existing_customers[0]
                self.stdout.write(self.style.WARNING(f"⚠️  Customer account already exists!"))
                self.stdout.write(f"   Customer ID: {customer.get('id')}")
                self.stdout.write(f"   Username: {customer.get('username')}")
                self.stdout.write(f"   Name: {customer.get('first_name')} {customer.get('last_name')}")
                self.stdout.write("\n✅ Account already set up - you can sync to POS using:")
                self.stdout.write(f"   python3 manage.py sync_customers --email \"{email}\"")
                return
            
            self.stdout.write(self.style.SUCCESS("✅ No existing customer account found - ready to create\n"))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error checking for existing customer: {e}"))
            return
        
        # Step 3: Extract customer data from first order
        self.stdout.write("Step 3: Extracting customer data from orders...")
        customer_data = self.extract_customer_data(guest_orders)
        
        self.stdout.write(f"   Name: {customer_data['first_name']} {customer_data['last_name']}")
        self.stdout.write(f"   Email: {customer_data['email']}")
        self.stdout.write(f"   Phone: {customer_data['billing']['phone']}")
        self.stdout.write(f"   City: {customer_data['billing']['city']}")
        self.stdout.write("")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n🔄 DRY RUN - No changes made"))
            self.stdout.write("Remove --dry-run flag to create customer account")
            return
        
        # Step 4: Create WooCommerce customer
        self.stdout.write("Step 4: Creating WooCommerce customer account...")
        
        # Generate secure password
        password = self.generate_password()
        customer_data['password'] = password
        
        self.stdout.write(f"   Generated password: {password}")
        self.stdout.write(self.style.WARNING("   ⚠️  Save this password - it won't be shown again!"))
        
        try:
            response = wc_api.wcapi.post("customers", customer_data)
            
            if response.status_code == 201:
                new_customer = response.json()
                self.stdout.write(self.style.SUCCESS("✅ Created WooCommerce customer account!"))
                self.stdout.write(f"   Customer ID: {new_customer.get('id')}")
                self.stdout.write(f"   Username: {new_customer.get('username')}")
                self.stdout.write(f"   Email: {new_customer.get('email')}")
                self.stdout.write("")
                
                # Step 5: Sync to POS
                self.stdout.write("Step 5: Syncing to POS database...")
                try:
                    contact = process_customer(new_customer)
                    self.stdout.write(self.style.SUCCESS("✅ Synced to POS database!"))
                    self.stdout.write(f"   Local Contact ID: {contact.id}")
                    if contact.ghl_contact_id:
                        self.stdout.write(f"   GHL Contact ID: {contact.ghl_contact_id}")
                    self.stdout.write("")
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"❌ Error syncing to POS: {e}"))
                    logger.error(f"Error syncing customer to POS: {e}")
                
                # Info about guest orders
                self.stdout.write("=" * 70)
                self.stdout.write("IMPORTANT: Guest Order Linking")
                self.stdout.write("=" * 70)
                self.stdout.write(self.style.WARNING(f"⚠️  The {len(guest_orders)} guest orders are still marked as guest orders."))
                self.stdout.write("   WooCommerce API doesn't support bulk order customer reassignment.")
                self.stdout.write("")
                self.stdout.write("   Options:")
                self.stdout.write("   1. Orders will remain as guest orders (recommended)")
                self.stdout.write("   2. Future orders will use the new customer account")
                self.stdout.write("   3. Manually link in WooCommerce admin if needed")
                self.stdout.write("")
                
                # Success summary
                self.stdout.write("=" * 70)
                self.stdout.write(self.style.SUCCESS("✅ CONVERSION COMPLETE!"))
                self.stdout.write("=" * 70)
                self.stdout.write("Customer can now log in with:")
                self.stdout.write(f"  Email: {email}")
                self.stdout.write(f"  Password: {password}")
                self.stdout.write("")
                
            else:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get('message', 'Unknown error')
                self.stdout.write(self.style.ERROR(f"❌ Failed to create customer: {error_msg}"))
                logger.error(f"WooCommerce customer creation failed: {response.status_code} - {response.text}")
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error creating customer: {e}"))
            logger.error(f"Exception creating WooCommerce customer: {e}")
    
    def find_guest_orders(self, wc_api, email):
        """Find all orders for a guest email (customer_id = 0)"""
        guest_orders = []
        page = 1
        
        while page <= 10:  # Limit to 10 pages (1000 orders)
            try:
                response = wc_api.wcapi.get("orders", params={
                    "per_page": 100,
                    "page": page,
                    "orderby": "date",
                    "order": "desc"
                })
                
                if response.status_code != 200:
                    break
                
                orders = response.json()
                if not orders:
                    break
                
                # Filter for guest orders with matching email
                for order in orders:
                    if order.get('customer_id') == 0:
                        billing_email = order.get('billing', {}).get('email', '').lower()
                        if billing_email == email.lower():
                            guest_orders.append(order)
                
                page += 1
                
            except Exception as e:
                logger.error(f"Error fetching orders page {page}: {e}")
                break
        
        return guest_orders
    
    def extract_customer_data(self, guest_orders):
        """Extract customer data from the most recent order"""
        # Use first order (most recent)
        order = guest_orders[0]
        billing = order.get('billing', {})
        shipping = order.get('shipping', {})
        
        # Create username from email
        email = billing.get('email', '')
        username = email.split('@')[0].replace('.', '_').replace('+', '_')
        
        customer_data = {
            'email': email,
            'username': username,
            'first_name': billing.get('first_name', ''),
            'last_name': billing.get('last_name', ''),
            'billing': {
                'first_name': billing.get('first_name', ''),
                'last_name': billing.get('last_name', ''),
                'company': billing.get('company', ''),
                'address_1': billing.get('address_1', ''),
                'address_2': billing.get('address_2', ''),
                'city': billing.get('city', ''),
                'state': billing.get('state', ''),
                'postcode': billing.get('postcode', ''),
                'country': billing.get('country', 'US'),
                'email': email,
                'phone': billing.get('phone', '')
            },
            'shipping': {
                'first_name': shipping.get('first_name', '') or billing.get('first_name', ''),
                'last_name': shipping.get('last_name', '') or billing.get('last_name', ''),
                'company': shipping.get('company', ''),
                'address_1': shipping.get('address_1', '') or billing.get('address_1', ''),
                'address_2': shipping.get('address_2', ''),
                'city': shipping.get('city', '') or billing.get('city', ''),
                'state': shipping.get('state', '') or billing.get('state', ''),
                'postcode': shipping.get('postcode', '') or billing.get('postcode', ''),
                'country': shipping.get('country', '') or billing.get('country', 'US')
            }
        }
        
        return customer_data
    
    def generate_password(self, length=12):
        """Generate a secure random password"""
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for i in range(length))
        return password
