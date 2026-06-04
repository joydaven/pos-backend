import logging
import pymysql
from contextlib import contextmanager
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from crm.models import Product, ProductVariation

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync product prices from WooCommerce database - Simple & Variable products'

    def add_arguments(self, parser):
        parser.add_argument(
            '--product-id',
            type=int,
            help='Sync specific product ID only',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=100,
            help='Maximum number of products to sync (default: 100)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--variable-only',
            action='store_true',
            help='Sync only variable products',
        )
        parser.add_argument(
            '--simple-only',
            action='store_true',
            help='Sync only simple products',
        )

    def handle(self, *args, **options):
        """Main command handler"""
        self.dry_run = options['dry_run']
        self.product_id = options.get('product_id')
        self.limit = options['limit']
        self.variable_only = options['variable_only']
        self.simple_only = options['simple_only']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made'))
        
        self.stdout.write(self.style.SUCCESS('💰 Starting WooCommerce Price Sync'))
        
        try:
            stats = self.sync_prices()
            self.display_results(stats)
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Sync failed: {str(e)}'))
            logger.error(f"Price sync failed: {str(e)}")
            raise CommandError(f'Price sync failed: {str(e)}')

    @contextmanager
    def get_woo_database_connection(self):
        """Direct connection to WooCommerce Cloud SQL database"""
        connection = None
        
        try:
            from core.secrets import get_woo_db_config
            woo_cfg = get_woo_db_config()
            
            self.stdout.write(f'🔗 Connecting to WooCommerce database: {woo_cfg["db_host"]}:{woo_cfg["db_port"]}')
            
            if not all([woo_cfg['db_host'], woo_cfg['db_name'], woo_cfg['db_username'], woo_cfg['db_password']]):
                raise ValueError("Missing required database connection credentials")
            
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
            
            self.stdout.write(self.style.SUCCESS('✅ Database connection established'))
            yield connection
            
        except Exception as e:
            logger.error(f"Database connection failed: {str(e)}")
            raise
        finally:
            if connection:
                connection.close()

    def sync_prices(self):
        """Main price sync logic"""
        stats = {
            'simple_products_updated': 0,
            'variable_products_updated': 0,
            'variations_updated': 0,
            'simple_products_checked': 0,
            'variable_products_checked': 0,
            'variations_checked': 0,
            'errors': 0,
            'skipped': 0
        }
        
        with self.get_woo_database_connection() as connection:
            cursor = connection.cursor()
            
            # Build query conditions
            conditions = []
            params = []
            
            if self.product_id:
                conditions.append("p.ID = %s")
                params.append(self.product_id)
            
            # Product type filtering - handle missing _product_type meta
            if self.simple_only:
                conditions.append("(pm_type.meta_value = 'simple' OR pm_type.meta_value IS NULL)")
            elif self.variable_only:
                conditions.append("pm_type.meta_value = 'variable'")
            else:
                # Include products with missing _product_type (default to simple) or explicit simple/variable
                conditions.append("(pm_type.meta_value IN ('simple', 'variable') OR pm_type.meta_value IS NULL)")
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            
            
            # Get products with current prices - handle missing product types gracefully
            # Use GROUP BY to eliminate duplicates from multiple meta joins
            query = f"""
                SELECT 
                    p.ID as product_id,
                    p.post_title as product_name,
                    CASE 
                        WHEN MAX(pm_type.meta_value) IS NOT NULL THEN MAX(pm_type.meta_value)
                        WHEN EXISTS(SELECT 1 FROM wp_posts v WHERE v.post_parent = p.ID AND v.post_type = 'product_variation') THEN 'variable'
                        ELSE 'simple'
                    END as product_type,
                    COALESCE(MAX(pm_price.meta_value), '') as price,
                    COALESCE(MAX(pm_regular.meta_value), '') as regular_price,
                    COALESCE(MAX(pm_sale.meta_value), '') as sale_price
                FROM wp_posts p
                LEFT JOIN wp_postmeta pm_type ON p.ID = pm_type.post_id AND pm_type.meta_key = '_product_type'
                LEFT JOIN wp_postmeta pm_price ON p.ID = pm_price.post_id AND pm_price.meta_key = '_price'
                LEFT JOIN wp_postmeta pm_regular ON p.ID = pm_regular.post_id AND pm_regular.meta_key = '_regular_price'  
                LEFT JOIN wp_postmeta pm_sale ON p.ID = pm_sale.post_id AND pm_sale.meta_key = '_sale_price'
                {where_clause}
                AND p.post_type = 'product'
                AND p.post_status = 'publish'
                GROUP BY p.ID, p.post_title
                ORDER BY p.ID
                LIMIT %s
            """
            
            params.append(self.limit)
            
            self.stdout.write(f'🔍 Fetching products with pricing data...')
            cursor.execute(query, params)
            products = cursor.fetchall()
            
            self.stdout.write(f'📦 Found {len(products)} products to process')
            
            for wc_product in products:
                try:
                    if wc_product['product_type'] == 'simple':
                        if self.sync_simple_product_price(wc_product):
                            stats['simple_products_updated'] += 1
                        stats['simple_products_checked'] += 1
                        
                    elif wc_product['product_type'] == 'variable':
                        parent_updated, variations_updated = self.sync_variable_product_price(
                            cursor, wc_product
                        )
                        if parent_updated:
                            stats['variable_products_updated'] += 1
                        stats['variations_updated'] += variations_updated
                        stats['variable_products_checked'] += 1
                        
                except Exception as e:
                    stats['errors'] += 1
                    self.stdout.write(
                        self.style.ERROR(f'❌ Error processing product {wc_product["product_id"]}: {str(e)}')
                    )
                    logger.error(f"Error processing product {wc_product['product_id']}: {str(e)}")
            
            return stats

    def sync_simple_product_price(self, wc_product):
        """Sync simple product price"""
        product_id = wc_product['product_id']
        
        try:
            # Find our local product
            product = Product.objects.get(woo_product_id=product_id)
            
            # Extract pricing data
            current_price = str(product.price) if product.price else ''
            current_regular = str(product.regular_price) if product.regular_price else ''
            current_sale = str(product.sale_price) if product.sale_price else ''
            
            wc_price = wc_product['price'] or ''
            wc_regular = wc_product['regular_price'] or ''
            wc_sale = wc_product['sale_price'] or ''
            
            # Check if any price changed
            price_changed = (
                current_price != wc_price or
                current_regular != wc_regular or
                current_sale != wc_sale
            )
            
            if price_changed:
                self.stdout.write(f'💰 Updating simple product: {product.name} (ID: {product_id})')
                self.stdout.write(f'   Price: ${current_price} → ${wc_price}')
                
                if wc_regular and wc_regular != current_regular:
                    self.stdout.write(f'   Regular: ${current_regular} → ${wc_regular}')
                    
                if wc_sale and wc_sale != current_sale:
                    self.stdout.write(f'   Sale: ${current_sale} → ${wc_sale}')
                
                if not self.dry_run:
                    # Update prices - clean formatting first
                    product.price = Decimal(str(wc_price).replace(',', '')) if wc_price else None
                    product.regular_price = Decimal(str(wc_regular).replace(',', '')) if wc_regular else None
                    product.sale_price = Decimal(str(wc_sale).replace(',', '')) if wc_sale else None
                    product.save(update_fields=['price', 'regular_price', 'sale_price'])
                    
                    # 🔧 FIX: Also restore woo_data pricing to fix $0.00 cart issues
                    self.restore_woo_data_pricing(product, wc_product)
                
                return True
            else:
                self.stdout.write(f'✓ Simple product up to date: {product.name}')
                return False
                
        except Product.DoesNotExist:
            self.stdout.write(f'⚠️ Product {product_id} not found in local database')
            return False
            
    def restore_woo_data_pricing(self, product, wc_product):
        """Restore pricing data in ProductSimple.woo_data to fix $0.00 cart issues"""
        try:
            from crm.models import ProductSimple
            simple_product = ProductSimple.objects.filter(product=product).first()
            
            if simple_product and simple_product.woo_data:
                woo_data = simple_product.woo_data
                if isinstance(woo_data, dict):
                    # Ensure basic pricing fields exist in woo_data
                    pricing_updated = False
                    
                    if not woo_data.get('price') and wc_product.get('price'):
                        woo_data['price'] = str(wc_product['price'])
                        pricing_updated = True
                        
                    if not woo_data.get('regular_price') and wc_product.get('regular_price'):
                        woo_data['regular_price'] = str(wc_product['regular_price'])
                        pricing_updated = True
                        
                    if wc_product.get('sale_price'):
                        woo_data['sale_price'] = str(wc_product['sale_price'])
                        pricing_updated = True
                    
                    if pricing_updated:
                        simple_product.woo_data = woo_data
                        simple_product.save(update_fields=['woo_data'])
                        self.stdout.write(f'     ✅ Restored woo_data pricing fields')
                        
        except Exception as e:
            self.stdout.write(f'     ⚠️ Error restoring woo_data pricing: {str(e)}')

    def sync_variable_product_price(self, cursor, wc_product):
        """Sync variable product and its variations"""
        product_id = wc_product['product_id']
        variations_updated = 0
        parent_updated = False
        
        try:
            # Find our local product
            product = Product.objects.get(woo_product_id=product_id)
            
            self.stdout.write(f'🔄 Processing variable product: {product.name} (ID: {product_id})')
            
            # Get all variations for this product
            variation_query = """
            SELECT 
                p.ID as variation_id,
                COALESCE(pm_price.meta_value, '') as price,
                COALESCE(pm_regular.meta_value, '') as regular_price,
                COALESCE(pm_sale.meta_value, '') as sale_price
            FROM wp_posts p
            LEFT JOIN wp_postmeta pm_price ON p.ID = pm_price.post_id AND pm_price.meta_key = '_price'
            LEFT JOIN wp_postmeta pm_regular ON p.ID = pm_regular.post_id AND pm_regular.meta_key = '_regular_price'
            LEFT JOIN wp_postmeta pm_sale ON p.ID = pm_sale.post_id AND pm_sale.meta_key = '_sale_price'
            WHERE p.post_parent = %s 
              AND p.post_type = 'product_variation'
              AND p.post_status = 'publish'
            ORDER BY p.menu_order, p.ID
            """
            
            cursor.execute(variation_query, (product_id,))
            wc_variations = cursor.fetchall()
            
            self.stdout.write(f'   Found {len(wc_variations)} variations')
            
            # Track variation prices for parent calculation
            variation_prices = []
            
            # Process each variation
            for wc_variation in wc_variations:
                variation_id = wc_variation['variation_id']
                
                try:
                    # Find local variation
                    variation = ProductVariation.objects.get(
                        product=product,
                        woo_variation_id=variation_id
                    )
                    
                    # Extract pricing data
                    current_price = str(variation.price) if variation.price else ''
                    current_regular = str(variation.regular_price) if variation.regular_price else ''
                    current_sale = str(variation.sale_price) if variation.sale_price else ''
                    
                    wc_price = wc_variation['price'] or ''
                    wc_regular = wc_variation['regular_price'] or ''
                    wc_sale = wc_variation['sale_price'] or ''
                    
                    # Check if any price changed
                    price_changed = (
                        current_price != wc_price or
                        current_regular != wc_regular or
                        current_sale != wc_sale
                    )
                    
                    if price_changed:
                        self.stdout.write(f'   💰 Updating variation {variation_id}: ${current_price} → ${wc_price}')
                        
                        if not self.dry_run:
                            variation.price = Decimal(str(wc_price).replace(',', '')) if wc_price else None
                            variation.regular_price = Decimal(str(wc_regular).replace(',', '')) if wc_regular else None
                            variation.sale_price = Decimal(str(wc_sale).replace(',', '')) if wc_sale else None
                            variation.save(update_fields=['price', 'regular_price', 'sale_price'])
                        
                        variations_updated += 1
                    
                    # Collect valid prices for parent calculation
                    if wc_price:
                        try:
                            price_float = float(wc_price)
                            if price_float > 0:
                                variation_prices.append(price_float)
                        except (ValueError, TypeError):
                            pass
                            
                except ProductVariation.DoesNotExist:
                    self.stdout.write(f'   ⚠️ Variation {variation_id} not found in local database')
            
            # Update parent product price based on variations
            if variation_prices:
                min_price = min(variation_prices)
                current_parent_price = float(product.price) if product.price else 0.0
                
                if abs(current_parent_price - min_price) > 0.01:
                    self.stdout.write(f'   💰 Updating parent price: ${current_parent_price:.2f} → ${min_price:.2f}')
                    
                    if not self.dry_run:
                        product.price = Decimal(str(min_price).replace(',', ''))
                        # Also update sale_price if it's higher than new price
                        if product.sale_price and float(product.sale_price) > min_price:
                            product.sale_price = Decimal(str(min_price).replace(',', ''))
                        product.save(update_fields=['price', 'sale_price'])
                    
                    parent_updated = True
                    
        except Product.DoesNotExist:
            self.stdout.write(f'⚠️ Variable product {product_id} not found in local database')
            
        return parent_updated, variations_updated

    def display_results(self, stats):
        """Display sync results"""
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('📊 PRICE SYNC RESULTS'))
        self.stdout.write('='*60)
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN - No actual changes made'))
        
        # Simple products
        self.stdout.write(f'📦 Simple Products:')
        self.stdout.write(f'   Checked: {stats["simple_products_checked"]}')
        self.stdout.write(f'   Updated: {stats["simple_products_updated"]}')
        
        # Variable products
        self.stdout.write(f'🔄 Variable Products:')
        self.stdout.write(f'   Checked: {stats["variable_products_checked"]}')  
        self.stdout.write(f'   Updated: {stats["variable_products_updated"]}')
        
        # Variations
        self.stdout.write(f'⚡ Variations:')
        self.stdout.write(f'   Checked: {stats["variations_checked"]}')
        self.stdout.write(f'   Updated: {stats["variations_updated"]}')
        
        # Totals
        total_updated = (
            stats['simple_products_updated'] + 
            stats['variable_products_updated'] + 
            stats['variations_updated']
        )
        
        self.stdout.write(f'\n🎯 Total Updates: {total_updated}')
        self.stdout.write(f'❌ Errors: {stats["errors"]}')
        
        if total_updated > 0:
            self.stdout.write(self.style.SUCCESS(f'✅ Price sync completed successfully!'))
        else:
            self.stdout.write(self.style.WARNING('ℹ️ All prices were already up to date'))
