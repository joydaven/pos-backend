"""
Check for products in WooCommerce that haven't been synced to our database
and sync them with all their variations, bundles, and other related data.

Usage:
    python manage.py check_and_sync_missing_products
    python manage.py check_and_sync_missing_products --dry-run
    python manage.py check_and_sync_missing_products --sync-missing (skip prompt)
    python manage.py check_and_sync_missing_products --verbose
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from crm.models import (
    Product, ProductSimple, ProductVariation, ProductBundle,
    ProductGrouped, ProductSubscription, ProductInventory, InventoryLocation
)
from crm.woocommerce import WooCommerceAPI
from itertools import chain
import logging
import re
import html

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for products in WooCommerce that are missing from database and optionally sync them'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without making changes'
        )
        parser.add_argument(
            '--sync-missing',
            action='store_true',
            help='Automatically sync missing products without prompting'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed logging output'
        )
        parser.add_argument(
            '--product-id',
            type=int,
            help='Check and sync specific WooCommerce product ID'
        )
        parser.add_argument(
            '--fix-product',
            type=int,
            help='Fix an already-synced product (re-sync with comprehensive fixes)'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        sync_missing = options['sync_missing']
        verbose = options['verbose']
        specific_product_id = options.get('product_id')
        fix_product_id = options.get('fix_product')

        if verbose:
            logger.setLevel(logging.DEBUG)

        self.stdout.write(self.style.SUCCESS('\n' + '='*80))
        self.stdout.write(self.style.SUCCESS('WOOCOMMERCE PRODUCT SYNC CHECKER'))
        self.stdout.write(self.style.SUCCESS('='*80 + '\n'))
        
        # Handle fix-product mode
        if fix_product_id:
            return self._fix_existing_product(fix_product_id, verbose, dry_run)

        # Initialize WooCommerce API
        try:
            wc_api = WooCommerceAPI()
            self.stdout.write(self.style.SUCCESS('✓ Connected to WooCommerce API'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to connect to WooCommerce API: {e}'))
            return

        # Get all products from database
        db_product_ids = set(Product.objects.values_list('woo_product_id', flat=True))
        self.stdout.write(f'\n📊 Database has {len(db_product_ids)} products')

        # Get all products from WooCommerce
        try:
            if specific_product_id:
                wc_products = [wc_api.wcapi.get(f'products/{specific_product_id}').json()]
                self.stdout.write(f'\n🔍 Checking specific product ID: {specific_product_id}')
            else:
                self.stdout.write('\n🔄 Fetching all products from WooCommerce...')
                # get_all_products() returns a generator that yields pages (lists) of products
                # We need to flatten it into a single list of products
                wc_products = list(chain.from_iterable(wc_api.get_all_products()))
                self.stdout.write(f'✓ WooCommerce has {len(wc_products)} products')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to fetch WooCommerce products: {e}'))
            return

        # Find missing products
        missing_products = []
        for wc_product in wc_products:
            wc_id = wc_product.get('id')
            if wc_id not in db_product_ids:
                missing_products.append(wc_product)

        # Report findings
        self.stdout.write(f'\n📋 COMPARISON RESULTS:')
        self.stdout.write(f'   • Products in WooCommerce: {len(wc_products)}')
        self.stdout.write(f'   • Products in Database: {len(db_product_ids)}')
        self.stdout.write(f'   • Missing Products: {len(missing_products)}')

        if not missing_products:
            self.stdout.write(self.style.SUCCESS('\n✓ All WooCommerce products are synced!'))
            return

        # Interactive sync loop
        remaining_products = missing_products.copy()
        synced_count = 0
        failed_count = 0
        
        while remaining_products:
            # Display remaining missing products with keys
            self.stdout.write(f'\n⚠️  MISSING PRODUCTS ({len(remaining_products)}):')
            self.stdout.write('-' * 80)

            product_map = {}  # Map keys to products
            for idx, product in enumerate(remaining_products, 1):
                product_type = product.get('type', 'unknown')
                product_id = product.get('id')
                product_name = product.get('name', 'Unknown')
                status = product.get('status', 'unknown')
                
                product_map[str(idx)] = product
                
                # Check for subscription metadata
                subscription_data = self._extract_subscription_metadata(product)
                
                # Check for variations
                variation_count = 0
                if product_type in ['variable', 'variable-subscription']:
                    try:
                        variations = wc_api.wcapi.get(f'products/{product_id}/variations', params={'per_page': 100}).json()
                        variation_count = len(variations) if isinstance(variations, list) else 0
                    except:
                        variation_count = 0

                # Check for bundle data
                is_bundle = product_type == 'bundle'
                bundled_items_count = 0
                if is_bundle:
                    bundled_items = product.get('bundled_items', [])
                    bundled_items_count = len(bundled_items) if bundled_items else 0

                self.stdout.write(f'\n   [{idx}] ID: {product_id}')
                self.stdout.write(f'       Name: {product_name}')
                self.stdout.write(f'       Type: {product_type}')
                self.stdout.write(f'       Status: {status}')
                
                if variation_count > 0:
                    self.stdout.write(f'       Variations: {variation_count}')
                if bundled_items_count > 0:
                    self.stdout.write(f'       Bundle Items: {bundled_items_count}')
                
                # Display subscription info if present
                if subscription_data['has_subscription']:
                    plugin_type = subscription_data.get('plugin_type', 'unknown')
                    if plugin_type == 'wcsatt':
                        schemes = subscription_data.get('subscription_schemes', {})
                        discount = subscription_data.get('subscription_discount', '')
                        self.stdout.write(f'       Subscription: WCSATT ({len(schemes)} schemes, {discount} discount)')
                    elif plugin_type == 'wcs':
                        period = subscription_data.get('subscription_period', 'month')
                        interval = subscription_data.get('subscription_interval', 1)
                        self.stdout.write(f'       Subscription: WCS (every {interval} {period})')
                
                self.stdout.write('   ' + '-' * 76)

            # Skip interactive prompts if flags are set
            if dry_run:
                self.stdout.write(self.style.WARNING(f'\n[DRY RUN] Would sync {len(remaining_products)} products'))
                break
                
            if sync_missing:
                # Sync all remaining products
                break

            # Ask if user wants to sync specific product
            self.stdout.write(f'\n❓ Do you want to sync a specific product? (y/n): ', ending='')
            response = input().strip().lower()
            
            if response in ['y', 'yes']:
                # Ask for product number
                self.stdout.write(f'   Enter product number [1-{len(remaining_products)}] or "q" to quit: ', ending='')
                choice = input().strip().lower()
                
                if choice == 'q':
                    self.stdout.write(self.style.WARNING('\n⏭️  Exiting sync. No more changes made.'))
                    break
                
                if choice in product_map:
                    # Sync the selected product
                    selected_product = product_map[choice]
                    product_id = selected_product.get('id')
                    product_name = selected_product.get('name', 'Unknown')
                    
                    try:
                        self.stdout.write(f'\n🔄 Syncing: {product_name} (ID: {product_id})...')
                        self._sync_product(selected_product, wc_api, verbose)
                        synced_count += 1
                        self.stdout.write(self.style.SUCCESS(f'✓ Synced successfully!\n'))
                        
                        # Remove from remaining list
                        remaining_products.remove(selected_product)
                        
                    except Exception as e:
                        failed_count += 1
                        self.stdout.write(self.style.ERROR(f'✗ Failed: {str(e)}\n'))
                        if verbose:
                            logger.exception(f"Error syncing product {product_id}")
                else:
                    self.stdout.write(self.style.ERROR(f'\n✗ Invalid choice. Please enter a number between 1 and {len(remaining_products)}.\n'))
                    
                # Continue loop to show updated list
                continue
            else:
                # Ask if they want to sync all remaining
                self.stdout.write(f'\n❓ Do you want to sync all remaining {len(remaining_products)} products? (y/n): ', ending='')
                response = input().strip().lower()
                
                if response in ['y', 'yes']:
                    break  # Exit loop to sync all remaining
                else:
                    self.stdout.write(self.style.WARNING('\n⏭️  Skipping sync. No more changes made.'))
                    self.stdout.write('\n' + '='*80 + '\n')
                    return

        # Sync remaining products (if any) after exiting the loop
        if remaining_products and not dry_run:
            self.stdout.write(f'\n🔄 SYNCING REMAINING {len(remaining_products)} PRODUCTS...\n')

            for product in remaining_products:
                product_id = product.get('id')
                product_name = product.get('name', 'Unknown')
                
                try:
                    self.stdout.write(f'   Syncing: {product_name} (ID: {product_id})...')
                    self._sync_product(product, wc_api, verbose)
                    synced_count += 1
                    self.stdout.write(self.style.SUCCESS(f'   ✓ Synced successfully'))
                except Exception as e:
                    failed_count += 1
                    self.stdout.write(self.style.ERROR(f'   ✗ Failed: {str(e)}'))
                    if verbose:
                        logger.exception(f'Error syncing product {product_id}')

            self.stdout.write(f'\n📊 SYNC RESULTS:')
            self.stdout.write(self.style.SUCCESS(f'   • Successfully synced: {synced_count}'))
            if failed_count > 0:
                self.stdout.write(self.style.ERROR(f'   • Failed: {failed_count}'))

        elif dry_run:
            self.stdout.write(self.style.WARNING('\n⚠️  DRY RUN - No changes made'))
            self.stdout.write('   Use without --dry-run to enable syncing')

        self.stdout.write('\n' + '='*80 + '\n')

    def _sync_product(self, wc_product, wc_api, verbose=False):
        """
        Sync a single product from WooCommerce to database with all related data
        Includes comprehensive fixes for subscriptions, pricing, and ATUM locations
        """
        product_id = wc_product.get('id')
        product_type = wc_product.get('type', 'simple')
        product_name = wc_product.get('name', 'Unknown')

        with transaction.atomic():
            # Create main Product record
            product = self._create_product_record(wc_product)
            
            # Sync based on product type
            if product_type == 'simple':
                self._sync_simple_product(product, wc_product, verbose)
            
            elif product_type in ['variable', 'variable-subscription']:
                self._sync_variable_product(product, wc_product, wc_api, verbose)
            
            elif product_type == 'bundle':
                self._sync_bundle_product(product, wc_product, verbose)
            
            elif product_type == 'grouped':
                self._sync_grouped_product(product, wc_product, verbose)
            
            elif product_type in ['subscription', 'variable_subscription']:
                self._sync_subscription_product(product, wc_product, verbose)
            
            # Apply comprehensive fixes after basic sync
            if verbose:
                self.stdout.write(f'   Applying comprehensive fixes...')
            
            # Fix subscription schemes (if applicable)
            self._sync_comprehensive_subscription(product, wc_product, verbose)
            
            # Fix pricing
            self._sync_comprehensive_prices(product, wc_product, verbose)
            
            # Sync ATUM locations
            self._sync_atum_locations(product, wc_api, verbose)

            if verbose:
                logger.info(f"Successfully synced {product_type} product with comprehensive fixes: {product_name} (ID: {product_id})")

        return product

    def _create_product_record(self, wc_product):
        """Create main Product record"""
        from decimal import Decimal
        
        product_data = {
            'woo_product_id': wc_product['id'],
            'name': wc_product.get('name', ''),
            'description': wc_product.get('description', ''),
            'status': wc_product.get('status', 'publish'),
            'stock_status': wc_product.get('stock_status', 'instock'),
            'product_type': wc_product.get('type', 'simple'),
            'categories': self._serialize_categories(wc_product.get('categories', [])),
            'images': self._serialize_images(wc_product.get('images', [])),
        }

        # Handle pricing
        price_str = wc_product.get('price', '0')
        regular_price_str = wc_product.get('regular_price', '0')
        sale_price_str = wc_product.get('sale_price', '0')

        try:
            product_data['price'] = Decimal(price_str) if price_str else None
        except:
            product_data['price'] = None

        try:
            product_data['regular_price'] = Decimal(regular_price_str) if regular_price_str else None
        except:
            product_data['regular_price'] = None

        try:
            product_data['sale_price'] = Decimal(sale_price_str) if sale_price_str else None
        except:
            product_data['sale_price'] = None

        # Handle stock quantity
        stock_qty = wc_product.get('stock_quantity')
        product_data['stock_quantity'] = stock_qty if stock_qty is not None else None

        product, created = Product.objects.update_or_create(
            woo_product_id=wc_product['id'],
            defaults=product_data
        )

        return product
    
    def _serialize_categories(self, categories):
        """
        Properly serialize categories to avoid [object Object] issues.
        Matches webhook behavior: extracts only NAME strings from category dicts.
        """
        if not categories:
            return []
        
        serialized = []
        for cat in categories:
            if isinstance(cat, dict):
                # Extract only the NAME - webhook behavior at views.py line 8885
                serialized.append(cat.get('name', ''))
            else:
                serialized.append(cat)
        return serialized
    
    def _serialize_images(self, images):
        """
        Properly serialize images.
        Matches webhook behavior: extracts only SRC strings from image dicts.
        """
        if not images:
            return []
        
        serialized = []
        for img in images:
            if isinstance(img, dict):
                # Extract only the SRC - webhook behavior at views.py line 8911
                serialized.append(img.get('src', ''))
            else:
                serialized.append(img)
        return serialized

    def _extract_subscription_metadata(self, wc_product):
        """Extract subscription metadata from WooCommerce product data."""
        subscription_data = {
            'has_subscription': False,
            'subscription_price': None,
            'subscription_period': None,
            'subscription_interval': None,
            'subscription_sign_up_fee': None,
            'subscription_trial_period': None,
            'subscription_trial_length': None,
            'subscription_limit': None,
            'subscription_length': None,
            'one_time_shipping': None,
            'subscription_schemes': {},
            'subscription_discount': None,
            'plugin_type': None,  # 'wcs' or 'wcsatt'
            'meta_data': []
        }
        
        # Extract metadata
        meta_data = wc_product.get('meta_data', [])
        if isinstance(meta_data, list):
            for meta in meta_data:
                if isinstance(meta, dict) and 'key' in meta and 'value' in meta:
                    key = meta.get('key')
                    value = meta.get('value')
                    
                    # Check for WooCommerce Subscriptions (standard) fields
                    if '_subscription_' in key or '_wcs_' in key:
                        subscription_data['meta_data'].append(meta)
                        subscription_data['has_subscription'] = True
                        subscription_data['plugin_type'] = 'wcs'
                        
                        # Map specific keys
                        if key == '_subscription_price':
                            subscription_data['subscription_price'] = value
                        elif key == '_subscription_period':
                            subscription_data['subscription_period'] = value
                        elif key == '_subscription_period_interval':
                            try:
                                subscription_data['subscription_interval'] = int(value) if value else 1
                            except (ValueError, TypeError):
                                subscription_data['subscription_interval'] = 1
                        elif key == '_subscription_sign_up_fee':
                            subscription_data['subscription_sign_up_fee'] = value
                        elif key == '_subscription_trial_period':
                            subscription_data['subscription_trial_period'] = value
                        elif key == '_subscription_trial_length':
                            try:
                                subscription_data['subscription_trial_length'] = int(value) if value else None
                            except (ValueError, TypeError):
                                subscription_data['subscription_trial_length'] = None
                    
                    # Check for WCSATT fields
                    elif '_wcsatt_' in key or key == '_satt_data':
                        subscription_data['meta_data'].append(meta)
                        
                        if key == '_satt_data' and isinstance(value, dict):
                            subscription_schemes = value.get('subscription_schemes', {})
                            if subscription_schemes:
                                subscription_data['has_subscription'] = True
                                subscription_data['plugin_type'] = 'wcsatt'
                                subscription_data['subscription_schemes'] = subscription_schemes
                                
                                # Extract discount from price_html
                                price_html = wc_product.get('price_html', '')
                                if 'wcsatt-sub-discount' in price_html:
                                    discount_match = re.search(r'wcsatt-sub-discount[^>]*>([^<]+)', price_html)
                                    if discount_match:
                                        discount_text = html.unescape(discount_match.group(1))
                                        subscription_data['subscription_discount'] = discount_text
                                
                                # Set first scheme as default
                                first_scheme = next(iter(subscription_schemes.items()), None)
                                if first_scheme:
                                    scheme_key, scheme_data = first_scheme
                                    if scheme_key and '_' in scheme_key:
                                        try:
                                            interval_str, period = scheme_key.split('_', 1)
                                            interval = int(interval_str)
                                            
                                            subscription_data['subscription_period'] = period
                                            subscription_data['subscription_interval'] = interval
                                            
                                            # Calculate price with discount
                                            original_price = wc_product.get('price')
                                            if original_price and subscription_data['subscription_discount']:
                                                try:
                                                    discount_percent = float(subscription_data['subscription_discount'].replace('%', '').strip())
                                                    discounted_price = float(original_price) * (1 - discount_percent / 100)
                                                    subscription_data['subscription_price'] = str(round(discounted_price, 2))
                                                except (ValueError, TypeError):
                                                    subscription_data['subscription_price'] = original_price
                                            else:
                                                subscription_data['subscription_price'] = original_price
                                        except (ValueError, TypeError):
                                            pass
        
        # Check if WCSATT is disabled
        is_wcsatt_disabled = any(
            meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
            for meta in meta_data
        )
        if is_wcsatt_disabled:
            subscription_data['has_subscription'] = False
            subscription_data['plugin_type'] = 'wcsatt_disabled'
        
        return subscription_data

    def _sync_simple_product(self, product, wc_product, verbose=False):
        """Sync simple product details with subscription metadata"""
        from decimal import Decimal

        # Extract subscription metadata first
        subscription_data = self._extract_subscription_metadata(wc_product)

        simple_data = {
            'product': product,
            'sku': wc_product.get('sku', ''),
            'is_virtual': wc_product.get('virtual', False),
            'is_downloadable': wc_product.get('downloadable', False),
            'woo_data': wc_product,
        }

        # Handle dimensions
        dimensions = wc_product.get('dimensions', {})
        try:
            simple_data['weight'] = Decimal(wc_product.get('weight', '0')) if wc_product.get('weight') else None
            simple_data['length'] = Decimal(dimensions.get('length', '0')) if dimensions.get('length') else None
            simple_data['width'] = Decimal(dimensions.get('width', '0')) if dimensions.get('width') else None
            simple_data['height'] = Decimal(dimensions.get('height', '0')) if dimensions.get('height') else None
        except:
            pass

        # Create/update ProductSimple
        simple_product, created = ProductSimple.objects.update_or_create(
            product=product,
            defaults=simple_data
        )

        # Add subscription metadata to woo_data if present
        if subscription_data['has_subscription']:
            if not isinstance(simple_product.woo_data, dict):
                simple_product.woo_data = {}
            
            simple_product.woo_data['subscription_metadata'] = subscription_data
            simple_product.woo_data['last_subscription_sync'] = timezone.now().isoformat()
            simple_product.save(update_fields=['woo_data'])
            
            # Create/update ProductSubscription record
            if subscription_data['subscription_price'] or subscription_data['subscription_period']:
                ProductSubscription.objects.update_or_create(
                    product=product,
                    defaults={
                        'price': subscription_data['subscription_price'],
                        'period': subscription_data['subscription_period'] or 'month',
                        'interval': subscription_data['subscription_interval'] or 1,
                        'trial_period': subscription_data['subscription_trial_period'],
                        'trial_length': subscription_data['subscription_trial_length'],
                        'sign_up_fee': subscription_data['subscription_sign_up_fee'],
                        'woo_data': wc_product
                    }
                )
                
                if verbose:
                    plugin_type = subscription_data.get('plugin_type', 'unknown')
                    logger.debug(f"Synced {plugin_type} subscription metadata for: {product.name}")

        if verbose:
            logger.debug(f"Synced simple product details for: {product.name}")

    def _sync_variable_product(self, product, wc_product, wc_api, verbose=False):
        """Sync variable product with all variations and subscription metadata"""
        from decimal import Decimal

        # Create ProductSimple record for parent
        simple_data = {
            'product': product,
            'sku': wc_product.get('sku', ''),
            'is_virtual': wc_product.get('virtual', False),
            'is_downloadable': wc_product.get('downloadable', False),
            'woo_data': wc_product,
        }
        ProductSimple.objects.update_or_create(
            product=product,
            defaults=simple_data
        )

        # Fetch and sync variations
        try:
            variations = wc_api.wcapi.get(f'products/{wc_product["id"]}/variations', params={'per_page': 100}).json()
            
            if isinstance(variations, list):
                for variation in variations:
                    # Extract subscription metadata for this variation
                    variation_subscription_data = self._extract_subscription_metadata(variation)
                    
                    variation_data = {
                        'product': product,
                        'woo_variation_id': variation['id'],
                        'sku': variation.get('sku', ''),
                        'attributes': variation.get('attributes', []),
                        'menu_order': variation.get('menu_order', 0),
                        'woo_data': variation,
                    }

                    # Handle pricing
                    try:
                        variation_data['price'] = Decimal(variation.get('price', '0')) if variation.get('price') else None
                        variation_data['regular_price'] = Decimal(variation.get('regular_price', '0')) if variation.get('regular_price') else None
                        variation_data['sale_price'] = Decimal(variation.get('sale_price', '0')) if variation.get('sale_price') else None
                    except:
                        pass

                    # Handle stock
                    stock_qty = variation.get('stock_quantity')
                    variation_data['stock_quantity'] = stock_qty if stock_qty is not None else None

                    # Create/update variation
                    variation_obj, created = ProductVariation.objects.update_or_create(
                        product=product,
                        woo_variation_id=variation['id'],
                        defaults=variation_data
                    )
                    
                    # Add subscription metadata to variation woo_data if present
                    if variation_subscription_data['has_subscription']:
                        if not isinstance(variation_obj.woo_data, dict):
                            variation_obj.woo_data = {}
                        
                        variation_obj.woo_data['subscription_metadata'] = variation_subscription_data
                        variation_obj.woo_data['last_subscription_sync'] = timezone.now().isoformat()
                        variation_obj.save(update_fields=['woo_data'])
                        
                        if verbose:
                            plugin_type = variation_subscription_data.get('plugin_type', 'unknown')
                            logger.debug(f"Synced {plugin_type} subscription metadata for variation: {variation['id']}")

                if verbose:
                    logger.debug(f"Synced {len(variations)} variations for: {product.name}")

        except Exception as e:
            logger.error(f"Failed to sync variations for product {wc_product['id']}: {e}")

    def _sync_bundle_product(self, product, wc_product, verbose=False):
        """Sync bundle product with bundled items"""
        bundle_data = {
            'product': product,
            'bundled_products': wc_product.get('bundled_items', []),
            'woo_data': wc_product,
        }

        # Handle min/max quantities
        bundle_data['min_quantity'] = wc_product.get('min_quantity', 1)
        bundle_data['max_quantity'] = wc_product.get('max_quantity', None)

        ProductBundle.objects.update_or_create(
            product=product,
            defaults=bundle_data
        )

        if verbose:
            logger.debug(f"Synced bundle product with {len(bundle_data['bundled_products'])} items: {product.name}")

    def _sync_grouped_product(self, product, wc_product, verbose=False):
        """Sync grouped product"""
        grouped_data = {
            'product': product,
            'grouped_products': wc_product.get('grouped_products', []),
            'woo_data': wc_product,
        }

        ProductGrouped.objects.update_or_create(
            product=product,
            defaults=grouped_data
        )

        if verbose:
            logger.debug(f"Synced grouped product: {product.name}")

    def _sync_subscription_product(self, product, wc_product, verbose=False):
        """Sync subscription product details"""
        from decimal import Decimal

        subscription_data = {
            'product': product,
            'woo_data': wc_product,
        }

        # Extract subscription metadata
        meta_data = wc_product.get('meta_data', [])
        for meta in meta_data:
            key = meta.get('key', '')
            value = meta.get('value', '')

            if key == '_subscription_price':
                try:
                    subscription_data['price'] = Decimal(value) if value else None
                except:
                    pass
            elif key == '_subscription_period':
                subscription_data['period'] = value
            elif key == '_subscription_period_interval':
                try:
                    subscription_data['interval'] = int(value) if value else 1
                except:
                    subscription_data['interval'] = 1
            elif key == '_subscription_trial_period':
                subscription_data['trial_period'] = value
            elif key == '_subscription_trial_length':
                try:
                    subscription_data['trial_length'] = int(value) if value else None
                except:
                    pass
            elif key == '_subscription_sign_up_fee':
                try:
                    subscription_data['sign_up_fee'] = Decimal(value) if value else None
                except:
                    pass

        ProductSubscription.objects.update_or_create(
            product=product,
            defaults=subscription_data
        )

        if verbose:
            logger.debug(f"Synced subscription product: {product.name}")
    
    def _fix_existing_product(self, product_id, verbose, dry_run):
        """Fix an already-synced product with comprehensive updates"""
        self.stdout.write(f'\n🔧 FIX PRODUCT MODE - Product ID: {product_id}\n')
        
        # Find the product in database
        try:
            product = Product.objects.get(woo_product_id=product_id)
            self.stdout.write(f'✓ Found product in database: {product.name}')
        except Product.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'✗ Product {product_id} not found in database'))
            return
        
        # Initialize WooCommerce API
        try:
            wc_api = WooCommerceAPI()
            self.stdout.write(f'✓ Connected to WooCommerce API')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to connect to WooCommerce API: {e}'))
            return
        
        # Fetch fresh product data from WooCommerce
        try:
            wc_product = wc_api.wcapi.get(f'products/{product_id}').json()
            self.stdout.write(f'✓ Fetched fresh data from WooCommerce')
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Failed to fetch product from WooCommerce: {e}'))
            return
        
        # Show what will be fixed
        self.stdout.write(f'\n📋 ISSUES TO FIX:')
        self.stdout.write(f'   1. Category serialization ([object Object] → proper JSON)')
        self.stdout.write(f'   2. Subscription plans (1 scheme → 5 comprehensive schemes)')
        self.stdout.write(f'   3. Pricing (incorrect sale status → correct pricing)')
        self.stdout.write(f'   4. ATUM locations (missing → fully synced)')
        
        if dry_run:
            self.stdout.write(self.style.WARNING(f'\n[DRY RUN] Would fix these issues'))
            return
        
        # Ask for confirmation
        self.stdout.write(f'\n❓ Proceed with fixing product "{product.name}"? (y/n): ', ending='')
        response = input().strip().lower()
        
        if response not in ['y', 'yes']:
            self.stdout.write(self.style.WARNING('⏭️  Skipping fix. No changes made.'))
            return
        
        # Apply comprehensive fixes
        try:
            self.stdout.write(f'\n🔄 Applying comprehensive fixes...\n')
            
            # 1. Fix categories and images
            self.stdout.write(f'   1/4 Fixing category/image serialization...')
            product.categories = self._serialize_categories(wc_product.get('categories', []))
            product.images = self._serialize_images(wc_product.get('images', []))
            product.save(update_fields=['categories', 'images'])
            self.stdout.write(self.style.SUCCESS(f'       ✓ Fixed'))
            
            # 2. Fix subscription plans (5 schemes)
            self.stdout.write(f'   2/4 Fixing subscription plans...')
            self._sync_comprehensive_subscription(product, wc_product, verbose)
            self.stdout.write(self.style.SUCCESS(f'       ✓ Fixed'))
            
            # 3. Fix pricing
            self.stdout.write(f'   3/4 Fixing pricing...')
            self._sync_comprehensive_prices(product, wc_product, verbose)
            self.stdout.write(self.style.SUCCESS(f'       ✓ Fixed'))
            
            # 4. Fix ATUM locations
            self.stdout.write(f'   4/4 Syncing ATUM locations...')
            self._sync_atum_locations(product, wc_api, verbose)
            self.stdout.write(self.style.SUCCESS(f'       ✓ Fixed'))
            
            self.stdout.write(self.style.SUCCESS(f'\n✅ Product "{product.name}" has been comprehensively fixed!'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Failed to fix product: {str(e)}'))
            if verbose:
                logger.exception(f"Error fixing product {product_id}")
    
    def _sync_comprehensive_subscription(self, product, wc_product, verbose=False):
        """Sync comprehensive subscription data with 5 schemes (like sync_woo_subscriptions.py)"""
        from decimal import Decimal
        
        # Check if product has subscription data
        meta_data = wc_product.get('meta_data', [])
        has_wcsatt = any('_wcsatt' in meta.get('key', '') for meta in meta_data)
        
        if not has_wcsatt:
            if verbose:
                self.stdout.write(f'       ℹ️  No WCSATT subscription data found')
            return
        
        # Find ProductSimple record
        try:
            simple_product = ProductSimple.objects.get(product=product)
        except ProductSimple.DoesNotExist:
            if verbose:
                self.stdout.write(f'       ⚠️  No ProductSimple record found')
            return
        
        # Get base price
        base_price = float(product.price) if product.price else 0
        if base_price == 0:
            base_price = float(wc_product.get('price', 0))
            if base_price == 0:
                base_price = float(wc_product.get('regular_price', 0))
        
        if base_price == 0:
            if verbose:
                self.stdout.write(f'       ⚠️  No valid price found for subscription calculation')
            return
        
        # Calculate discounted price (5% discount)
        discount = 5
        discounted_price = base_price * (1 - discount / 100)
        
        if verbose:
            self.stdout.write(f'       💰 Base: ${base_price:.2f} → Subscription: ${discounted_price:.2f}')
        
        # Ensure woo_data is dict
        woo_data = simple_product.woo_data or {}
        if isinstance(woo_data, str):
            import json
            woo_data = json.loads(woo_data)
        
        # Update meta_data with _wcsatt_schemes
        woo_meta_data = woo_data.get('meta_data', [])
        
        # Remove existing _wcsatt_schemes
        woo_meta_data = [meta for meta in woo_meta_data if meta.get('key') != '_wcsatt_schemes']
        
        # Create 5 comprehensive subscription schemes
        wcsatt_schemes = [
            {
                "position": "0",
                "subscription_price": f"{discounted_price:.2f}",
                "subscription_length": "0",
                "subscription_period": "week",
                "subscription_discount": discount,
                "subscription_sale_price": "",
                "subscription_regular_price": f"{base_price:.2f}",
                "subscription_pricing_method": "inherit",
                "subscription_period_interval": "2",
                "subscription_payment_sync_date": 0
            },
            {
                "position": "1",
                "subscription_price": f"{discounted_price:.2f}",
                "subscription_length": "0",
                "subscription_period": "week",
                "subscription_discount": discount,
                "subscription_sale_price": "",
                "subscription_regular_price": f"{base_price:.2f}",
                "subscription_pricing_method": "inherit",
                "subscription_period_interval": "4",
                "subscription_payment_sync_date": 0
            },
            {
                "position": "2",
                "subscription_price": f"{discounted_price:.2f}",
                "subscription_length": "0",
                "subscription_period": "week",
                "subscription_discount": discount,
                "subscription_sale_price": "",
                "subscription_regular_price": f"{base_price:.2f}",
                "subscription_pricing_method": "inherit",
                "subscription_period_interval": "6",
                "subscription_payment_sync_date": 0
            },
            {
                "position": "3",
                "subscription_price": f"{discounted_price:.2f}",
                "subscription_length": "0",
                "subscription_period": "month",
                "subscription_discount": discount,
                "subscription_sale_price": "",
                "subscription_regular_price": f"{base_price:.2f}",
                "subscription_pricing_method": "inherit",
                "subscription_period_interval": "2",
                "subscription_payment_sync_date": 0
            },
            {
                "position": "4",
                "subscription_price": f"{discounted_price:.2f}",
                "subscription_length": "0",
                "subscription_period": "month",
                "subscription_discount": discount,
                "subscription_sale_price": "",
                "subscription_regular_price": f"{base_price:.2f}",
                "subscription_pricing_method": "inherit",
                "subscription_period_interval": "3",
                "subscription_payment_sync_date": 0
            }
        ]
        
        # Add the new schemes
        woo_meta_data.append({
            "key": "_wcsatt_schemes",
            "value": wcsatt_schemes
        })
        
        # Update woo_data
        woo_data['meta_data'] = woo_meta_data
        woo_data['price'] = f"{base_price:.2f}"
        woo_data['regular_price'] = f"{base_price:.2f}"
        simple_product.woo_data = woo_data
        simple_product.save(update_fields=['woo_data'])
        
        if verbose:
            self.stdout.write(f'       ✅ Created 5 subscription schemes')
    
    def _sync_comprehensive_prices(self, product, wc_product, verbose=False):
        """
        Sync comprehensive pricing using direct Cloud SQL database query.
        Matches sync_woo_prices.py behavior to bypass WooCommerce API cache.
        """
        from decimal import Decimal, InvalidOperation
        import pymysql
        
        def safe_decimal(value):
            """Safely convert value to Decimal - matches webhook logic"""
            if value is None or value == '' or value == 0:
                return None
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return None
        
        try:
            from core.secrets import get_woo_db_config
            woo_cfg = get_woo_db_config()
            
            if not all([woo_cfg['db_host'], woo_cfg['db_name'], woo_cfg['db_username'], woo_cfg['db_password']]):
                if verbose:
                    self.stdout.write(f'       ⚠️  Missing DB credentials, using API fallback')
                wc_price = safe_decimal(wc_product.get('price'))
                wc_regular = safe_decimal(wc_product.get('regular_price'))
                wc_sale = safe_decimal(wc_product.get('sale_price'))
            else:
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
                
                try:
                    cursor = connection.cursor()
                    
                    query = """
                        SELECT 
                            COALESCE(MAX(pm_price.meta_value), '') as price,
                            COALESCE(MAX(pm_regular.meta_value), '') as regular_price,
                            COALESCE(MAX(pm_sale.meta_value), '') as sale_price
                        FROM wp_posts p
                        LEFT JOIN wp_postmeta pm_price ON p.ID = pm_price.post_id AND pm_price.meta_key = '_price'
                        LEFT JOIN wp_postmeta pm_regular ON p.ID = pm_regular.post_id AND pm_regular.meta_key = '_regular_price'  
                        LEFT JOIN wp_postmeta pm_sale ON p.ID = pm_sale.post_id AND pm_sale.meta_key = '_sale_price'
                        WHERE p.ID = %s
                        GROUP BY p.ID
                    """
                    
                    cursor.execute(query, (product.woo_product_id,))
                    db_product = cursor.fetchone()
                    
                    if db_product:
                        wc_price = safe_decimal(db_product['price'])
                        wc_regular = safe_decimal(db_product['regular_price'])
                        wc_sale = safe_decimal(db_product['sale_price'])
                        
                        if verbose:
                            self.stdout.write(f'       📊 DB data (cache-free): price={db_product["price"]}, regular={db_product["regular_price"]}, sale="{db_product["sale_price"]}"')
                            self.stdout.write(f'       📊 Converted: price={wc_price}, regular={wc_regular}, sale={wc_sale}')
                    else:
                        if verbose:
                            self.stdout.write(f'       ⚠️  Product not found in database')
                        wc_price = safe_decimal(wc_product.get('price'))
                        wc_regular = safe_decimal(wc_product.get('regular_price'))
                        wc_sale = safe_decimal(wc_product.get('sale_price'))
                finally:
                    connection.close()
            
            # Update Product model (webhook behavior at views.py line 8142-8161)
            changes = []
            if product.regular_price != wc_regular:
                changes.append(f'regular_price: {product.regular_price} → {wc_regular}')
                product.regular_price = wc_regular
            
            if product.sale_price != wc_sale:
                changes.append(f'sale_price: {product.sale_price} → {wc_sale}')
                product.sale_price = wc_sale
            
            if product.price != wc_price:
                changes.append(f'price: {product.price} → {wc_price}')
                product.price = wc_price
            
            if changes:
                product.save(update_fields=['price', 'regular_price', 'sale_price'])
                if verbose:
                    self.stdout.write(f'       💰 Updated prices: {", ".join(changes)}')
                    status = 'on sale' if wc_sale else 'regular'
                    self.stdout.write(f'       💰 Current: ${wc_price} ({status})')
            else:
                if verbose:
                    self.stdout.write(f'       ℹ️  Prices already up to date')
            
            # Also update ProductSimple woo_data pricing if it exists
            simple_product = ProductSimple.objects.filter(product=product).first()
            if simple_product and simple_product.woo_data:
                woo_data = simple_product.woo_data
                if isinstance(woo_data, dict):
                    # Update pricing fields in woo_data
                    woo_data['price'] = str(wc_price) if wc_price else ''
                    woo_data['regular_price'] = str(wc_regular) if wc_regular else ''
                    
                    # CRITICAL: Remove sale_price completely if None/empty (not on sale)
                    # Frontend checks for existence of sale_price field, not just truthy value
                    if wc_sale and wc_sale > 0:
                        woo_data['sale_price'] = str(wc_sale)
                    else:
                        # Remove the key entirely, don't set to empty string
                        woo_data.pop('sale_price', None)
                        # Also check meta_data for sale_price and remove it
                        if 'meta_data' in woo_data and isinstance(woo_data['meta_data'], list):
                            woo_data['meta_data'] = [
                                meta for meta in woo_data['meta_data'] 
                                if meta.get('key') != '_sale_price'
                            ]
                    
                    simple_product.woo_data = woo_data
                    simple_product.save(update_fields=['woo_data'])
                    
                    if verbose:
                        self.stdout.write(f'       ✅ Updated woo_data pricing (sale_price removed: {not wc_sale})')
                    
        except Exception as e:
            if verbose:
                self.stdout.write(f'       ⚠️  Error syncing prices: {str(e)}')
            logger.error(f"Error in comprehensive price sync: {str(e)}")
    
    def _sync_atum_locations(self, product, wc_api, verbose=False):
        """
        Sync ATUM inventory locations using the same function as webhook.
        Matches webhook behavior at views.py line 9125
        """
        try:
            # Use the same ATUM sync function that webhooks use
            from crm.atum_webhook_sync import sync_product_atum_inventory
            
            # Sync ATUM inventory for this product (webhook behavior at views.py line 9125)
            atum_sync_status = sync_product_atum_inventory(product, dry_run=False)
            
            if atum_sync_status and atum_sync_status.get('success', False):
                # Check if any changes were made
                changes_made = (
                    atum_sync_status.get('product_updated', False) or
                    atum_sync_status.get('variations_updated', 0) > 0 or
                    atum_sync_status.get('locations_added', 0) > 0 or
                    atum_sync_status.get('locations_removed', 0) > 0
                )
                
                if changes_made:
                    if verbose:
                        locations_added = atum_sync_status.get('locations_added', 0)
                        locations_removed = atum_sync_status.get('locations_removed', 0)
                        variations_updated = atum_sync_status.get('variations_updated', 0)
                        self.stdout.write(f'       ✅ ATUM sync: +{locations_added} -{locations_removed} locations, {variations_updated} variations')
                else:
                    if verbose:
                        self.stdout.write(f'       ℹ️  ATUM locations already up to date')
            else:
                if verbose:
                    self.stdout.write(f'       ⚠️  ATUM sync failed or returned no data')
                    
        except ImportError as e:
            if verbose:
                self.stdout.write(f'       ⚠️  ATUM sync module not available: {str(e)}')
        except Exception as e:
            if verbose:
                self.stdout.write(f'       ⚠️  Error syncing ATUM locations: {str(e)}')
            logger.error(f"Error in ATUM location sync: {str(e)}")
