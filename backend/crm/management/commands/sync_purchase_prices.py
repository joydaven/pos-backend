from django.core.management.base import BaseCommand
from crm.models import Product, ProductVariation
from core.secrets import get_woo_db_config
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
import pymysql
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync purchase_price from wp_atum_product_data table in WooCommerce MariaDB to local POS database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--product-id',
            type=int,
            help='Sync purchase_price for a specific WooCommerce product ID only'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output for every product'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.verbose = options['verbose']

        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        try:
            with self.woo_db_connection() as conn:
                if options['product_id']:
                    self.sync_single_product(conn, options['product_id'])
                else:
                    self.sync_all_products(conn)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error during purchase_price sync: {str(e)}'))
            logger.error(f'Purchase price sync error: {str(e)}', exc_info=True)

    @contextmanager
    def woo_db_connection(self):
        """Direct connection to WooCommerce Cloud SQL database."""
        woo_cfg = get_woo_db_config()
        conn = None
        try:
            self.stdout.write(f'Connecting to WooCommerce DB ({woo_cfg["db_host"]}:{woo_cfg["db_port"]})...')

            conn = pymysql.connect(
                host=woo_cfg['db_host'],
                port=int(woo_cfg['db_port']),
                user=woo_cfg['db_username'],
                password=woo_cfg['db_password'],
                database=woo_cfg['db_name'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=15,
            )
            self.stdout.write(self.style.SUCCESS('Database connection established'))
            yield conn
        finally:
            if conn:
                conn.close()

    def fetch_atum_purchase_prices(self, cursor, woo_ids):
        """Fetch purchase_price from wp_atum_product_data for given product IDs."""
        if not woo_ids:
            return {}
        CHUNK = 500
        results = {}
        for start in range(0, len(woo_ids), CHUNK):
            chunk = woo_ids[start:start + CHUNK]
            placeholders = ','.join(['%s'] * len(chunk))
            query = f"""
                SELECT product_id, purchase_price
                FROM wp_atum_product_data
                WHERE product_id IN ({placeholders})
            """
            cursor.execute(query, chunk)
            for row in cursor.fetchall():
                results[row['product_id']] = row['purchase_price']
        return results

    def sync_single_product(self, conn, product_id):
        """Sync purchase_price for a single product and its variations"""
        try:
            product = Product.objects.get(woo_product_id=product_id)
            self.stdout.write(f'Syncing purchase_price for: {product.name} (WooCommerce ID: {product_id})')

            cursor = conn.cursor()

            # Sync parent product
            atum_prices = self.fetch_atum_purchase_prices(cursor, [product_id])
            raw_price = atum_prices.get(product_id)
            purchase_price = self.safe_decimal(raw_price)

            if purchase_price is None:
                self.stdout.write(f'  - No purchase_price in wp_atum_product_data for product {product_id}')
                self.stdout.write(f'- No purchase_price change for {product.name}')
            elif product.purchase_price != purchase_price:
                old_value = product.purchase_price
                self.stdout.write(f'  purchase_price: {old_value} → {purchase_price}')
                if not self.dry_run:
                    product.purchase_price = purchase_price
                    product.save(update_fields=['purchase_price', 'updated_at'])
                self.stdout.write(self.style.SUCCESS(f'✓ Updated purchase_price for {product.name}'))
            else:
                self.stdout.write(f'  purchase_price already correct: {purchase_price}')

            # Sync variations if any
            variations = list(ProductVariation.objects.filter(product=product))
            if variations:
                var_woo_ids = [v.woo_variation_id for v in variations]
                var_atum_prices = self.fetch_atum_purchase_prices(cursor, var_woo_ids)
                for variation in variations:
                    raw_var_price = var_atum_prices.get(variation.woo_variation_id)
                    var_purchase_price = self.safe_decimal(raw_var_price)
                    if var_purchase_price is not None and variation.purchase_price != var_purchase_price:
                        old_val = variation.purchase_price
                        if not self.dry_run:
                            variation.purchase_price = var_purchase_price
                            variation.save(update_fields=['purchase_price', 'updated_at'])
                        self.stdout.write(self.style.SUCCESS(
                            f'  ✓ Variation {variation.woo_variation_id}: {old_val} → {var_purchase_price}'
                        ))

        except Product.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Product with WooCommerce ID {product_id} not found in database'))

    def sync_all_products(self, conn):
        """Sync purchase_price for all products in bulk"""
        products = list(Product.objects.all().order_by('woo_product_id'))
        total_products = len(products)
        woo_ids = [p.woo_product_id for p in products]

        self.stdout.write(f'\nFetching purchase prices from wp_atum_product_data for {total_products} products...')
        cursor = conn.cursor()
        atum_prices = self.fetch_atum_purchase_prices(cursor, woo_ids)
        self.stdout.write(self.style.SUCCESS(f'Found {len(atum_prices)} products with ATUM data\n'))

        updated_count = 0
        skipped_count = 0
        no_price_count = 0
        error_count = 0

        for i, product in enumerate(products, 1):
            try:
                raw_price = atum_prices.get(product.woo_product_id)
                purchase_price = self.safe_decimal(raw_price)

                if purchase_price is None:
                    no_price_count += 1
                    if self.verbose:
                        self.stdout.write(f'  [{i}/{total_products}] {product.name} — no purchase_price in ATUM')
                    continue

                if product.purchase_price != purchase_price:
                    old_value = product.purchase_price
                    if not self.dry_run:
                        product.purchase_price = purchase_price
                        product.save(update_fields=['purchase_price', 'updated_at'])
                    updated_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ [{i}/{total_products}] {product.name}: {old_value} → {purchase_price}'
                    ))
                else:
                    skipped_count += 1
                    if self.verbose:
                        self.stdout.write(f'  [{i}/{total_products}] {product.name} — unchanged: {purchase_price}')

            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(
                    f'  ✗ [{i}/{total_products}] Error: {product.name} (WC ID: {product.woo_product_id}): {str(e)}'
                ))
                logger.error(f'Error syncing purchase_price for product {product.woo_product_id}: {str(e)}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Purchase price sync completed (products)!'))
        self.stdout.write(f'  Total products:       {total_products}')
        self.stdout.write(f'  ATUM records found:   {len(atum_prices)}')
        self.stdout.write(f'  Updated:              {updated_count}')
        self.stdout.write(f'  Unchanged:            {skipped_count}')
        self.stdout.write(f'  No price in ATUM:     {no_price_count}')
        self.stdout.write(f'  Errors:               {error_count}')

        # Also sync purchase prices for variations
        self.sync_all_variations(conn)

    def sync_all_variations(self, conn):
        """Sync purchase_price for all product variations from ATUM."""
        variations = list(ProductVariation.objects.all().select_related('product'))
        total_variations = len(variations)
        woo_ids = [v.woo_variation_id for v in variations]

        self.stdout.write(f'\nFetching purchase prices for {total_variations} variations...')
        cursor = conn.cursor()
        atum_prices = self.fetch_atum_purchase_prices(cursor, woo_ids)
        self.stdout.write(self.style.SUCCESS(f'Found {len(atum_prices)} variations with ATUM data\n'))

        updated_count = 0
        skipped_count = 0
        no_price_count = 0
        error_count = 0

        for i, variation in enumerate(variations, 1):
            try:
                raw_price = atum_prices.get(variation.woo_variation_id)
                purchase_price = self.safe_decimal(raw_price)

                if purchase_price is None:
                    no_price_count += 1
                    if self.verbose:
                        self.stdout.write(f'  [{i}/{total_variations}] Variation {variation.woo_variation_id} of {variation.product.name} — no purchase_price')
                    continue

                if variation.purchase_price != purchase_price:
                    old_value = variation.purchase_price
                    if not self.dry_run:
                        variation.purchase_price = purchase_price
                        variation.save(update_fields=['purchase_price', 'updated_at'])
                    updated_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  ✓ [{i}/{total_variations}] Variation {variation.woo_variation_id} of {variation.product.name}: {old_value} → {purchase_price}'
                    ))
                else:
                    skipped_count += 1
                    if self.verbose:
                        self.stdout.write(f'  [{i}/{total_variations}] Variation {variation.woo_variation_id} — unchanged: {purchase_price}')

            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(
                    f'  ✗ [{i}/{total_variations}] Error: Variation {variation.woo_variation_id}: {str(e)}'
                ))
                logger.error(f'Error syncing purchase_price for variation {variation.woo_variation_id}: {str(e)}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Purchase price sync completed (variations)!'))
        self.stdout.write(f'  Total variations:     {total_variations}')
        self.stdout.write(f'  ATUM records found:   {len(atum_prices)}')
        self.stdout.write(f'  Updated:              {updated_count}')
        self.stdout.write(f'  Unchanged:            {skipped_count}')
        self.stdout.write(f'  No price in ATUM:     {no_price_count}')
        self.stdout.write(f'  Errors:               {error_count}')

    def safe_decimal(self, value):
        """Safely convert a value to Decimal, handling empty strings and None"""
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
