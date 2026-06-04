from django.core.management.base import BaseCommand
from crm.views_membership_direct import membership_manager
from crm.models import Contact, POSOrder, POSOrderItem
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Test membership API functionality'

    def handle(self, *args, **options):
        customer_email = "reah@doctorsstudio.com"
        customer_uuid = "158be6d7-ab57-45ef-9ef0-be0142eab409"
        
        self.stdout.write(f"🔍 Testing Membership API for: {customer_email}")
        self.stdout.write(f"🔍 Customer UUID: {customer_uuid}")
        self.stdout.write("=" * 80)
        
        try:
            # Test 1: Check if customer exists in local database
            self.stdout.write("1. Checking customer in local database...")
            try:
                customer = Contact.objects.get(email=customer_email)
                self.stdout.write(f"   ✅ Customer found: {customer.id} - {customer.first_name} {customer.last_name}")
                self.stdout.write(f"   Email: {customer.email}")
                self.stdout.write(f"   WooCommerce ID: {customer.woo_customer_id}")
            except Contact.DoesNotExist:
                self.stdout.write(f"   ❌ Customer not found with email: {customer_email}")
                return
            
            # Test 2: Check POS orders for this customer
            self.stdout.write(f"\n2. Checking POS orders for customer...")
            pos_orders = POSOrder.objects.filter(contact=customer).order_by('-created_at')
            self.stdout.write(f"   Found {len(pos_orders)} POS orders")
            
            for i, order in enumerate(pos_orders[:5]):  # Show first 5 orders
                self.stdout.write(f"   Order {i+1}: {order.id}")
                self.stdout.write(f"     Created: {order.created_at}")
                self.stdout.write(f"     Status: {order.status}")
                self.stdout.write(f"     Total: ${order.total}")
                
                # Check order items
                items = order.items.all()
                self.stdout.write(f"     Items ({len(items)}):")
                for item in items:
                    self.stdout.write(f"       - {item.name} (Qty: {item.quantity}, Price: ${item.subtotal})")
                    
                    # Check if it's a membership product
                    is_membership = any(keyword in item.name.lower() for keyword in ['membership', 'member', 'subscription'])
                    if is_membership:
                        self.stdout.write(f"         ⭐ MEMBERSHIP PRODUCT FOUND!")
                self.stdout.write("")
            
            # Test 3: Test the get_pos_membership_products method
            self.stdout.write("3. Testing get_pos_membership_products method...")
            try:
                pos_membership_products = membership_manager.get_pos_membership_products(customer_email)
                self.stdout.write(f"   Found {len(pos_membership_products)} POS membership products")
                
                for i, product in enumerate(pos_membership_products):
                    self.stdout.write(f"   Product {i+1}:")
                    self.stdout.write(f"     Name: {product['product_name']}")
                    self.stdout.write(f"     Subscription ID: {product['subscription_id']}")
                    self.stdout.write(f"     Product ID: {product['product_id']}")
                    self.stdout.write(f"     Quantity: {product['quantity']}")
                    self.stdout.write(f"     Total: ${product['line_total']}")
                    self.stdout.write(f"     Status: {product['subscription_status']}")
                    self.stdout.write(f"     Start Date: {product['start_date']}")
                    self.stdout.write("")
            except Exception as e:
                self.stdout.write(f"   ❌ Error in get_pos_membership_products: {str(e)}")
                import traceback
                traceback.print_exc()
            
            # Test 4: Test the main get_customer_subscription_products method
            self.stdout.write("4. Testing get_customer_subscription_products method...")
            try:
                subscription_products = membership_manager.get_customer_subscription_products(customer_email)
                self.stdout.write(f"   Found {len(subscription_products)} total membership products")
                
                for i, product in enumerate(subscription_products):
                    self.stdout.write(f"   Product {i+1}:")
                    self.stdout.write(f"     Name: {product['product_name']}")
                    self.stdout.write(f"     Subscription ID: {product['subscription_id']}")
                    self.stdout.write(f"     Product ID: {product['product_id']}")
                    self.stdout.write(f"     Quantity: {product['quantity']}")
                    self.stdout.write(f"     Total: ${product['line_total']}")
                    self.stdout.write(f"     Status: {product['subscription_status']}")
                    self.stdout.write(f"     Start Date: {product['start_date']}")
                    self.stdout.write(f"     Type: {'POS Order' if product['subscription_id'].startswith('pos-') else 'WooCommerce Subscription'}")
                    self.stdout.write("")
            except Exception as e:
                self.stdout.write(f"   ❌ Error in get_customer_subscription_products: {str(e)}")
                import traceback
                traceback.print_exc()
            
            # Test 5: Test WooCommerce subscriptions (should be empty based on our findings)
            self.stdout.write("5. Testing WooCommerce subscriptions...")
            try:
                woo_subscriptions = membership_manager.get_customer_subscriptions(customer_email)
                self.stdout.write(f"   Found {len(woo_subscriptions)} WooCommerce subscriptions")
                
                if len(woo_subscriptions) > 0:
                    for i, sub in enumerate(woo_subscriptions):
                        self.stdout.write(f"   Subscription {i+1}: {sub['subscription_id']} - {sub['status']}")
                else:
                    self.stdout.write("   (No WooCommerce subscriptions found - this is expected)")
            except Exception as e:
                self.stdout.write(f"   ❌ Error in get_customer_subscriptions: {str(e)}")
                import traceback
                traceback.print_exc()
            
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write("✅ Test completed successfully!")
            
        except Exception as e:
            self.stdout.write(f"❌ Test failed with error: {str(e)}")
            import traceback
            traceback.print_exc()
