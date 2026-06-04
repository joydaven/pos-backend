from django.core.management.base import BaseCommand
import pymysql


class Command(BaseCommand):
    help = 'Check ATUM inventory IDs for test product'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=' * 80))
        self.stdout.write(self.style.SUCCESS('CHECKING ATUM INVENTORY IDs'))
        self.stdout.write(self.style.SUCCESS('=' * 80))
        
        from core.secrets import get_woo_db_config
        woo_cfg = get_woo_db_config()
        
        connection = None
        
        try:
            connection = pymysql.connect(
                host=woo_cfg['db_host'],
                port=int(woo_cfg['db_port']),
                user=woo_cfg['db_username'],
                password=woo_cfg['db_password'],
                database=woo_cfg['db_name'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10
            )
            self.stdout.write('✅ Database connection established\n')
            
            with connection.cursor() as cursor:
                # Get ATUM inventory records for test product
                self.stdout.write(self.style.WARNING('ATUM Inventory Records for Product 287351:'))
                self.stdout.write('-' * 80)
                
                cursor.execute("""
                    SELECT 
                        id,
                        name,
                        product_id,
                        inventory_id,
                        atum_stock_status,
                        stock_quantity,
                        inbound_stock,
                        stock_on_hold,
                        sold_today,
                        sales_last_days,
                        out_stock_threshold,
                        inheritable
                    FROM wp_atum_inventories 
                    WHERE product_id = 287351 
                    ORDER BY name
                """)
                
                records = cursor.fetchall()
                
                if records:
                    for record in records:
                        self.stdout.write(f"\n📦 Record ID: {record['id']}")
                        self.stdout.write(f"   Name: {record['name']}")
                        self.stdout.write(f"   Product ID: {record['product_id']}")
                        self.stdout.write(f"   Inventory ID: {record.get('inventory_id', 'N/A')}")
                        self.stdout.write(f"   Stock Status: {record['atum_stock_status']}")
                        self.stdout.write(f"   Stock Quantity: {record['stock_quantity']}")
                else:
                    self.stdout.write(self.style.ERROR('❌ No records found for product 287351'))
                
                # Check wp_atum_locations table
                self.stdout.write('\n')
                self.stdout.write(self.style.WARNING('ATUM Locations (Master List):'))
                self.stdout.write('-' * 80)
                
                cursor.execute("""
                    SELECT 
                        id,
                        name,
                        slug,
                        type
                    FROM wp_atum_locations 
                    WHERE name LIKE '%Jupiter%' OR name LIKE '%Boca%'
                    ORDER BY name
                """)
                
                locations = cursor.fetchall()
                
                if locations:
                    for loc in locations:
                        self.stdout.write(f"\n�� Location ID: {loc['id']}")
                        self.stdout.write(f"   Name: {loc['name']}")
                        self.stdout.write(f"   Slug: {loc['slug']}")
                else:
                    self.stdout.write(self.style.ERROR('❌ No Jupiter or Boca locations found'))
                
                # Check recent inventory order records
                self.stdout.write('\n')
                self.stdout.write(self.style.WARNING('Recent ATUM Inventory Orders for Product 287351:'))
                self.stdout.write('-' * 80)
                
                cursor.execute("""
                    SELECT 
                        io.id,
                        io.order_id,
                        io.order_item_id,
                        io.inventory_id,
                        io.type,
                        io.quantity,
                        oi.order_item_name,
                        p.post_date
                    FROM wp_atum_inventory_orders io
                    LEFT JOIN wp_woocommerce_order_items oi ON io.order_item_id = oi.order_item_id
                    LEFT JOIN wp_posts p ON io.order_id = p.ID
                    WHERE oi.order_item_name LIKE '%DS TEST%'
                    ORDER BY p.post_date DESC
                    LIMIT 10
                """)
                
                orders = cursor.fetchall()
                
                if orders:
                    for order in orders:
                        self.stdout.write(f"\n📋 Order ID: {order['order_id']}")
                        self.stdout.write(f"   Order Item ID: {order['order_item_id']}")
                        self.stdout.write(f"   Inventory ID (wp_atum_inventories.id): {order['inventory_id']}")
                        self.stdout.write(f"   Quantity: {order['quantity']}")
                        self.stdout.write(f"   Date: {order['post_date']}")
                else:
                    self.stdout.write(self.style.ERROR('❌ No inventory order records found'))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error: {str(e)}'))
            import traceback
            self.stdout.write(traceback.format_exc())
        finally:
            if connection:
                connection.close()
                self.stdout.write('\n✅ Database connection closed')
        
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(self.style.SUCCESS('CHECK COMPLETE'))
        self.stdout.write('=' * 80)
