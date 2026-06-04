"""
Management command to test order synchronization between WooCommerce and POS orders
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from crm.models import POSOrder
from crm.woocommerce import WooCommerceAPI
from crm.webhooks_order import find_corresponding_pos_order
import json

class Command(BaseCommand):
    help = 'Test order synchronization between WooCommerce and POS orders'

    def add_arguments(self, parser):
        parser.add_argument(
            '--woo-order-id',
            type=str,
            help='WooCommerce order ID to test sync with'
        )
        parser.add_argument(
            '--pos-order-id',
            type=str,
            help='POS order ID to test sync with'
        )
        parser.add_argument(
            '--test-webhook',
            action='store_true',
            help='Test webhook functionality with sample data'
        )
        parser.add_argument(
            '--find-linked-orders',
            action='store_true',
            help='Find all POS orders with WooCommerce order IDs'
        )

    def handle(self, *args, **options):
        wc_api = WooCommerceAPI()
        
        if options['find_linked_orders']:
            self.find_linked_orders()
        elif options['woo_order_id']:
            self.test_woo_to_pos_sync(options['woo_order_id'], wc_api)
        elif options['pos_order_id']:
            self.test_pos_to_woo_sync(options['pos_order_id'])
        elif options['test_webhook']:
            self.test_webhook_functionality()
        else:
            self.show_usage()

    def find_linked_orders(self):
        """Find all POS orders that have WooCommerce order IDs"""
        self.stdout.write(self.style.HTTP_INFO("🔍 Finding linked orders..."))
        
        # Find POS orders with WooCommerce order IDs in metadata
        pos_orders_with_woo_ids = POSOrder.objects.exclude(metadata__isnull=True).exclude(metadata={})
        
        linked_orders = []
        for order in pos_orders_with_woo_ids:
            if isinstance(order.metadata, dict) and order.metadata.get('woo_order_id'):
                linked_orders.append({
                    'pos_id': order.id,
                    'pos_order_number': order.order_number,
                    'pos_status': order.status,
                    'woo_order_id': order.metadata['woo_order_id'],
                    'transaction_id': order.transaction_id,
                    'created_at': order.created_at
                })
        
        if linked_orders:
            self.stdout.write(self.style.SUCCESS(f"✅ Found {len(linked_orders)} linked orders:"))
            for order in linked_orders[:10]:  # Show first 10
                self.stdout.write(f"  • POS: {order['pos_id']} (#{order['pos_order_number']}) → WooCommerce: {order['woo_order_id']}")
                self.stdout.write(f"    Status: {order['pos_status']}, Transaction: {order['transaction_id']}")
        else:
            self.stdout.write(self.style.WARNING("⚠️ No linked orders found"))

    def test_woo_to_pos_sync(self, woo_order_id, wc_api):
        """Test finding POS order from WooCommerce order"""
        self.stdout.write(self.style.HTTP_INFO(f"🔍 Testing WooCommerce to POS sync for order {woo_order_id}..."))
        
        try:
            # Get WooCommerce order data
            woo_order = wc_api.get_order(woo_order_id)
            if not woo_order:
                self.stdout.write(self.style.ERROR(f"❌ WooCommerce order {woo_order_id} not found"))
                return
            
            self.stdout.write(f"📦 WooCommerce Order: {woo_order_id}")
            self.stdout.write(f"   Status: {woo_order.get('status')}")
            self.stdout.write(f"   Number: {woo_order.get('number')}")
            self.stdout.write(f"   Transaction ID: {woo_order.get('transaction_id')}")
            
            # Check meta_data for POS transaction ID
            meta_data = woo_order.get('meta_data', [])
            pos_transaction_id = None
            for meta in meta_data:
                if meta.get('key') == '_pos_transaction_id':
                    pos_transaction_id = meta.get('value')
                    break
            
            if pos_transaction_id:
                self.stdout.write(f"   POS Transaction ID: {pos_transaction_id}")
            
            # Find corresponding POS order
            pos_order = find_corresponding_pos_order(woo_order)
            
            if pos_order:
                self.stdout.write(self.style.SUCCESS(f"✅ Found corresponding POS order: {pos_order.id}"))
                self.stdout.write(f"   POS Order Number: {pos_order.order_number}")
                self.stdout.write(f"   POS Status: {pos_order.status}")
                self.stdout.write(f"   POS Transaction ID: {pos_order.transaction_id}")
                
                # Check if statuses match
                status_mapping = {
                    'pending': 'pending',
                    'processing': 'processing',
                    'completed': 'completed',
                    'cancelled': 'cancelled',
                    'refunded': 'refunded',
                    'on-hold': 'processing',
                    'failed': 'cancelled'
                }
                
                expected_pos_status = status_mapping.get(woo_order.get('status'))
                if pos_order.status == expected_pos_status:
                    self.stdout.write(self.style.SUCCESS("✅ Status sync is correct"))
                else:
                    self.stdout.write(self.style.WARNING(f"⚠️ Status mismatch: WooCommerce={woo_order.get('status')} → Expected POS={expected_pos_status}, Actual POS={pos_order.status}"))
            else:
                self.stdout.write(self.style.WARNING("⚠️ No corresponding POS order found"))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {e}"))

    def test_pos_to_woo_sync(self, pos_order_id):
        """Test finding WooCommerce order from POS order"""
        self.stdout.write(self.style.HTTP_INFO(f"🔍 Testing POS to WooCommerce sync for order {pos_order_id}..."))
        
        try:
            pos_order = POSOrder.objects.get(id=pos_order_id)
            
            self.stdout.write(f"📦 POS Order: {pos_order.id}")
            self.stdout.write(f"   Order Number: {pos_order.order_number}")
            self.stdout.write(f"   Status: {pos_order.status}")
            self.stdout.write(f"   Transaction ID: {pos_order.transaction_id}")
            
            # Check if order has WooCommerce order ID in metadata
            woo_order_id = None
            if isinstance(pos_order.metadata, dict):
                woo_order_id = pos_order.metadata.get('woo_order_id')
            
            if woo_order_id:
                self.stdout.write(f"   WooCommerce Order ID: {woo_order_id}")
                
                # Try to get WooCommerce order
                wc_api = WooCommerceAPI()
                woo_order = wc_api.get_order(woo_order_id)
                
                if woo_order:
                    self.stdout.write(self.style.SUCCESS(f"✅ Found corresponding WooCommerce order: {woo_order_id}"))
                    self.stdout.write(f"   WooCommerce Status: {woo_order.get('status')}")
                    
                    # Check status sync
                    status_mapping = {
                        'pending': 'pending',
                        'processing': 'processing',
                        'completed': 'completed',
                        'cancelled': 'cancelled',
                        'refunded': 'refunded'
                    }
                    
                    expected_woo_status = status_mapping.get(pos_order.status)
                    if woo_order.get('status') == expected_woo_status:
                        self.stdout.write(self.style.SUCCESS("✅ Status sync is correct"))
                    else:
                        self.stdout.write(self.style.WARNING(f"⚠️ Status mismatch: POS={pos_order.status} → Expected WooCommerce={expected_woo_status}, Actual WooCommerce={woo_order.get('status')}"))
                else:
                    self.stdout.write(self.style.WARNING(f"⚠️ WooCommerce order {woo_order_id} not found"))
            else:
                self.stdout.write(self.style.WARNING("⚠️ No WooCommerce order ID found in POS order metadata"))
                
        except POSOrder.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"❌ POS order {pos_order_id} not found"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error: {e}"))

    def test_webhook_functionality(self):
        """Test webhook functionality with sample data"""
        self.stdout.write(self.style.HTTP_INFO("🧪 Testing webhook functionality..."))
        
        # Find a POS order with WooCommerce ID for testing
        pos_orders_with_woo_ids = POSOrder.objects.exclude(metadata__isnull=True).exclude(metadata={})
        
        test_order = None
        for order in pos_orders_with_woo_ids:
            if isinstance(order.metadata, dict) and order.metadata.get('woo_order_id'):
                test_order = order
                break
        
        if not test_order:
            self.stdout.write(self.style.WARNING("⚠️ No POS order with WooCommerce ID found for testing"))
            return
        
        woo_order_id = test_order.metadata['woo_order_id']
        
        # Create sample webhook data
        sample_webhook_data = {
            "id": int(woo_order_id),
            "number": f"WC-{woo_order_id}",
            "status": "cancelled",  # Test cancellation
            "transaction_id": test_order.transaction_id,
            "meta_data": [
                {
                    "key": "_pos_transaction_id",
                    "value": test_order.transaction_id
                }
            ]
        }
        
        self.stdout.write(f"📦 Testing with order: POS {test_order.id} ↔ WooCommerce {woo_order_id}")
        self.stdout.write(f"   Current POS status: {test_order.status}")
        self.stdout.write(f"   Testing webhook with status: cancelled")
        
        # Test find_corresponding_pos_order function
        found_pos_order = find_corresponding_pos_order(sample_webhook_data)
        
        if found_pos_order and found_pos_order.id == test_order.id:
            self.stdout.write(self.style.SUCCESS("✅ Webhook would find correct POS order"))
            self.stdout.write(f"   Found POS order: {found_pos_order.id}")
        else:
            self.stdout.write(self.style.WARNING("⚠️ Webhook might not find correct POS order"))

    def show_usage(self):
        """Show usage examples"""
        self.stdout.write(self.style.HTTP_INFO("📋 Usage Examples:"))
        self.stdout.write("  python3 manage.py test_order_sync --find-linked-orders")
        self.stdout.write("  python3 manage.py test_order_sync --woo-order-id 12345")
        self.stdout.write("  python3 manage.py test_order_sync --pos-order-id abc-123-def")
        self.stdout.write("  python3 manage.py test_order_sync --test-webhook")
