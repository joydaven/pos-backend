"""
Management command to sync ONLY subscription data from WooCommerce for simple and variable products.
Similar to sync_woo_prices but focused purely on subscription options.
"""

import logging
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from crm.models import Product, ProductVariation, ProductSubscription
from crm.woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync subscription data from WooCommerce - Simple & Variable products only'

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
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force update existing subscription data',
        )

    def handle(self, *args, **options):
        """Main command handler"""
        self.dry_run = options['dry_run']
        self.product_id = options.get('product_id')
        self.limit = options['limit']
        self.variable_only = options['variable_only']
        self.simple_only = options['simple_only']
        self.force = options['force']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN MODE - No changes will be made'))
        
        self.stdout.write(self.style.SUCCESS('📋 Starting WooCommerce Subscription Sync'))
        
        try:
            stats = self.sync_subscriptions()
            self.display_results(stats)
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Sync failed: {str(e)}'))
            logger.error(f"Subscription sync failed: {str(e)}")
            raise CommandError(f'Subscription sync failed: {str(e)}')

    def get_woocommerce_api(self):
        """Get WooCommerce API instance"""
        try:
            wc = WooCommerceAPI()
            self.stdout.write('🔗 WooCommerce API initialized')
            return wc
        except Exception as e:
            raise Exception(f"Failed to initialize WooCommerce API: {str(e)}")

    def sync_subscriptions(self):
        """Main subscription sync logic"""
        stats = {
            'simple_products_updated': 0,
            'variable_products_updated': 0,
            'variations_updated': 0,
            'simple_products_checked': 0,
            'variable_products_checked': 0,
            'variations_checked': 0,
            'subscriptions_created': 0,
            'subscriptions_updated': 0,
            'subscriptions_removed': 0,  # 🔧 NEW: Track cleanup operations
            'errors': 0,
            'skipped': 0
        }
        
        wc = self.get_woocommerce_api()
        
        # Fetch products from WooCommerce API
        self.stdout.write(f'📦 Fetching products from WooCommerce API...')
        
        if self.product_id:
            # Fetch specific product
            try:
                product_response = wc.wcapi.get(f"products/{self.product_id}")
                if product_response.status_code == 200:
                    products = [product_response.json()]
                else:
                    self.stdout.write(self.style.ERROR(f'❌ Product {self.product_id} not found in WooCommerce'))
                    return stats
            except Exception as e:
                raise Exception(f"Failed to fetch product {self.product_id}: {str(e)}")
        else:
            # Fetch multiple products
            products = []
            page = 1
            per_page = min(self.limit, 100)
            
            while len(products) < self.limit:
                try:
                    response = wc.wcapi.get("products", params={
                        "page": page,
                        "per_page": per_page,
                        "status": "publish"
                    })
                    
                    if response.status_code != 200:
                        raise Exception(f"WooCommerce API error: {response.status_code}")
                    
                    page_products = response.json()
                    if not page_products:
                        break
                    
                    products.extend(page_products)
                    self.stdout.write(f'   Fetched {len(page_products)} products from page {page}')
                    
                    if len(page_products) < per_page:
                        break
                        
                    page += 1
                    
                except Exception as e:
                    raise Exception(f"Failed to fetch products from page {page}: {str(e)}")
        
        # Limit to requested amount
        products = products[:self.limit]
        self.stdout.write(f'📋 Processing {len(products)} products for subscription data...')
        
        for woo_product in products:
            try:
                product_type = woo_product.get('type', 'simple')
                
                # Apply type filters
                if self.simple_only and product_type != 'simple':
                    continue
                if self.variable_only and product_type != 'variable':
                    continue
                
                if product_type == 'simple':
                    updated = self.sync_simple_product_subscription(woo_product, stats)
                    if updated:
                        stats['simple_products_updated'] += 1
                    stats['simple_products_checked'] += 1
                    
                elif product_type == 'variable':
                    variations_updated = self.sync_variable_product_subscription(wc, woo_product, stats)
                    if variations_updated > 0:
                        stats['variable_products_updated'] += 1
                    stats['variations_updated'] += variations_updated
                    stats['variable_products_checked'] += 1
                    
            except Exception as e:
                stats['errors'] += 1
                self.stdout.write(
                    self.style.ERROR(f'❌ Error processing product {woo_product.get("id", "Unknown")}: {str(e)}')
                )
                logger.error(f"Error processing product {woo_product.get('id')}: {str(e)}")
                
        return stats

    def sync_simple_product_subscription(self, woo_product, stats):
        """Sync simple product subscription data"""
        product_id = woo_product['id']
        product_name = woo_product.get('name', 'Unknown')
        
        try:
            # Find our local product
            product = Product.objects.get(woo_product_id=product_id)
            
            # Extract subscription data from WooCommerce meta
            subscription_data = self.extract_subscription_data(woo_product)
            
            # Debug: Show what data we found
            self.stdout.write(f'   🔍 Subscription data found: {subscription_data}')
            
            # 🧹 FIRST: Check for cleanup of disabled products (regardless of current subscription status)
            meta_data = woo_product.get('meta_data', [])
            is_wcsatt_disabled = any(
                meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
                for meta in meta_data
            )
            
            if is_wcsatt_disabled:
                # Check if this disabled product has existing subscriptions to remove
                existing_subscription = ProductSubscription.objects.filter(product=product).first()
                if existing_subscription:
                    self.stdout.write(f'🗑️ Product {product_name} is set to "Sell one-time only" but has existing subscription - removing')
                    if not self.dry_run:
                        # Remove _wcsatt_schemes from woo_data and delete subscription
                        self.remove_subscription_from_woo_data(product)
                        existing_subscription.delete()
                        stats['subscriptions_removed'] += 1
                        self.stdout.write(f'     ✅ Removed subscription and cleaned woo_data')
                    else:
                        stats['subscriptions_removed'] += 1  # Track in dry-run
                    return True
                else:
                    # Even if no ProductSubscription, check if woo_data has subscription schemes to clean
                    from crm.models import ProductSimple
                    simple_product = ProductSimple.objects.filter(product=product).first()
                    has_woo_data_schemes = False
                    
                    if simple_product and simple_product.woo_data:
                        meta_data = simple_product.woo_data.get('meta_data', [])
                        has_woo_data_schemes = any(meta.get('key') == '_wcsatt_schemes' for meta in meta_data)
                    
                    if has_woo_data_schemes:
                        self.stdout.write(f'🗑️ Product {product_name} is disabled but has subscription data in woo_data - cleaning')
                        if not self.dry_run:
                            self.remove_subscription_from_woo_data(product)
                            self.stdout.write(f'     ✅ Cleaned subscription data from woo_data')
                        stats['subscriptions_removed'] += 1
                        return True
                    else:
                        self.stdout.write(f'     ℹ️ Product {product_name} is disabled and has no subscription data - skipping')
                        return False
            
            if subscription_data['is_subscription']:
                self.stdout.write(f'📋 Processing subscription product: {product_name} (ID: {product_id})')
                
                # Check if ProductSubscription already exists
                existing_subscription = ProductSubscription.objects.filter(product=product).first()
                
                if existing_subscription and not self.force:
                    # Compare existing data
                    needs_update = self.needs_subscription_update(existing_subscription, subscription_data)
                    if not needs_update:
                        self.stdout.write(f'   ✓ Subscription data already up to date')
                        return False
                
                self.stdout.write(f'   📋 Updating subscription: {subscription_data["interval"]} {subscription_data["period"]}(s) at ${subscription_data["price"]}')
                
                if not self.dry_run:
                    if existing_subscription:
                        # Update existing subscription
                        self.update_subscription_object(existing_subscription, subscription_data, woo_product)
                        stats['subscriptions_updated'] += 1
                    else:
                        # Create new subscription
                        ProductSubscription.objects.create(
                            product=product,
                            price=Decimal(str(subscription_data['price'])) if subscription_data['price'] else None,
                            period=subscription_data['period'],
                            interval=subscription_data['interval'],
                            trial_period=subscription_data.get('trial_period'),
                            trial_length=subscription_data.get('trial_length'),
                            sign_up_fee=Decimal(str(subscription_data['sign_up_fee'])) if subscription_data.get('sign_up_fee') else None,
                            woo_data=woo_product
                        )
                        stats['subscriptions_created'] += 1
                    
                    # Also update the product's woo_data to include _wcsatt_schemes for frontend compatibility
                    self.update_product_woo_data_with_subscription(product, subscription_data)
                
                return True
            else:
                # 🧹 CLEANUP: Remove existing subscription for disabled products
                existing_subscription = ProductSubscription.objects.filter(product=product).first()
                if existing_subscription:
                    # Check if this product is explicitly disabled for subscriptions
                    meta_data = woo_product.get('meta_data', [])
                    is_wcsatt_disabled = any(
                        meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes'
                        for meta in meta_data
                    )
                    
                    if is_wcsatt_disabled:
                        self.stdout.write(f'🗑️ Product {product_name} is set to "Sell one-time only" but has existing subscription - removing')
                        if not self.dry_run:
                            # Also remove _wcsatt_schemes from woo_data
                            self.remove_subscription_from_woo_data(product)
                            existing_subscription.delete()
                            stats['subscriptions_removed'] += 1  # 🔧 Track removal
                            self.stdout.write(f'     ✅ Removed subscription and cleaned woo_data')
                        else:
                            stats['subscriptions_removed'] += 1  # Track in dry-run too
                        return True
                    else:
                        self.stdout.write(f'⚠️ Product {product_name} no longer has subscription data - keeping existing')
                
                return False
                
        except Product.DoesNotExist:
            self.stdout.write(f'⚠️ Product {product_id} not found in local database')
            return False

    def sync_variable_product_subscription(self, wc, woo_product, stats):
        """Sync variable product subscription data"""
        product_id = woo_product['id']
        product_name = woo_product.get('name', 'Unknown')
        variations_updated = 0
        
        try:
            # Find our local product
            product = Product.objects.get(woo_product_id=product_id)
            
            self.stdout.write(f'🔄 Processing variable product: {product_name} (ID: {product_id})')
            
            # Get variations from WooCommerce API
            try:
                variations_response = wc.wcapi.get(f"products/{product_id}/variations")
                if variations_response.status_code != 200:
                    self.stdout.write(f'   ❌ Failed to fetch variations: {variations_response.status_code}')
                    return 0
                
                wc_variations = variations_response.json()
                self.stdout.write(f'   Found {len(wc_variations)} variations')
                
            except Exception as e:
                self.stdout.write(f'   ❌ Error fetching variations: {str(e)}')
                return 0
            
            # Process each variation
            for wc_variation in wc_variations:
                variation_id = wc_variation['id']
                
                try:
                    # Find local variation
                    variation = ProductVariation.objects.get(
                        product=product,
                        woo_variation_id=variation_id
                    )
                    
                    # Extract subscription data from variation
                    subscription_data = self.extract_subscription_data(wc_variation)
                    
                    if subscription_data['is_subscription']:
                        self.stdout.write(f'     📋 Variation {variation_id} has subscription: {subscription_data["interval"]} {subscription_data["period"]}(s)')
                        
                        if not self.dry_run:
                            # Update the variation's woo_data with subscription schemes (like simple products)
                            self.update_variation_woo_data_with_subscription(variation, subscription_data)
                            
                            # For variations, we store subscription data in woo_data only
                            # No separate ProductSubscription record needed (model only supports one per product)
                        
                        variations_updated += 1
                        
                except ProductVariation.DoesNotExist:
                    self.stdout.write(f'     ⚠️ Variation {variation_id} not found in local database')
            
            # Update stats for variations processed
            stats['variations_checked'] += len(wc_variations)
            stats['variations_updated'] += variations_updated
            
            # Also check if parent product needs subscription data
            parent_subscription_data = self.extract_subscription_data(woo_product)
            if parent_subscription_data['is_subscription']:
                self.sync_simple_product_subscription(woo_product, stats)
                    
        except Product.DoesNotExist:
            self.stdout.write(f'⚠️ Variable product {product_id} not found in local database')
            
        return variations_updated

    def extract_subscription_data(self, product_data):
        """Extract subscription data from WooCommerce product/variation data"""
        subscription_data = {
            'is_subscription': False,
            'price': None,
            'period': 'month',
            'interval': 1,
            'trial_period': None,
            'trial_length': None,
            'sign_up_fee': None
        }
        
        # Debug: Show product type and meta data
        product_type = product_data.get('type', '')
        product_id = product_data.get('id', 'Unknown')
        self.stdout.write(f'     🔍 Product {product_id} type: {product_type}')
        
        # Check product type
        if product_type in ['subscription', 'variable-subscription']:
            subscription_data['is_subscription'] = True
            self.stdout.write(f'     ✅ Detected subscription by product type: {product_type}')
        
        # Check meta data for subscription fields
        meta_data = product_data.get('meta_data', [])
        subscription_meta_found = []
        wcsatt_schemes = []
        
        for meta in meta_data:
            key = meta.get('key', '')
            value = meta.get('value', '')
            
            # Debug: Show all subscription-related meta keys
            if 'subscription' in key.lower() or key.startswith('_wcs') or key.startswith('_recurring'):
                subscription_meta_found.append(f'{key}={value}')
            
            # Standard WooCommerce Subscriptions
            if key == '_subscription_price':
                subscription_data['is_subscription'] = True
                if value:
                    subscription_data['price'] = float(value)
            elif key == '_subscription_period':
                subscription_data['period'] = value or 'month'
            elif key == '_subscription_period_interval':
                subscription_data['interval'] = int(value) if value else 1
            elif key == '_subscription_trial_period':
                subscription_data['trial_period'] = value
            elif key == '_subscription_trial_length':
                subscription_data['trial_length'] = int(value) if value else None
            elif key == '_subscription_sign_up_fee':
                if value:
                    subscription_data['sign_up_fee'] = float(value)
            
            # WCSATT (All Products for Subscriptions) fields
            elif key == '_wcsatt_schemes':
                # This contains the subscription options/schemes
                if value and value != '':
                    subscription_data['is_subscription'] = True
                    wcsatt_schemes = value if isinstance(value, list) else []
                    self.stdout.write(f'     ✅ WCSATT schemes found: {wcsatt_schemes}')
            elif key == '_wcsatt_force_subscription' and value == 'yes':
                subscription_data['is_subscription'] = True
                self.stdout.write(f'     ✅ WCSATT force subscription: {value}')
            elif key == '_wcsatt_default_status' and value in ['subscription', 'one-time']:
                if value == 'subscription':
                    subscription_data['is_subscription'] = True
                self.stdout.write(f'     🔍 WCSATT default status: {value}')
        
        if subscription_meta_found:
            self.stdout.write(f'     🔍 Subscription meta found: {subscription_meta_found}')
        else:
            self.stdout.write(f'     ❌ No subscription meta data found')
        
        # Process WCSATT schemes if found
        if wcsatt_schemes and len(wcsatt_schemes) > 0:
            # Use the first scheme as default
            first_scheme = wcsatt_schemes[0] if isinstance(wcsatt_schemes, list) else wcsatt_schemes
            if isinstance(first_scheme, dict):
                # Extract period and interval from scheme
                scheme_period = first_scheme.get('subscription_period', 'month')
                scheme_interval = first_scheme.get('subscription_period_interval', 1)
                subscription_data['period'] = scheme_period
                subscription_data['interval'] = int(scheme_interval)
                self.stdout.write(f'     📋 Using WCSATT scheme: {scheme_interval} {scheme_period}(s)')
        
        # Check if product has WCSATT enabled but need to fetch schemes from API
        has_wcsatt = any('_wcsatt' in key for key, _ in [(meta.get('key', ''), meta.get('value', '')) for meta in meta_data])
        
        # 🔧 FIX: Check if WCSATT is explicitly disabled for this product
        wcsatt_disabled = False
        for meta in meta_data:
            if meta.get('key') == '_wcsatt_disabled' and meta.get('value') == 'yes':
                wcsatt_disabled = True
                self.stdout.write(f'     ❌ WCSATT disabled for this product (_wcsatt_disabled=yes) - skipping subscription')
                break
        
        if has_wcsatt and not subscription_data['is_subscription'] and not wcsatt_disabled:
            # This product has WCSATT installed but might have subscription options not in basic meta
            # This means it CAN be a subscription product - mark it for UI display
            subscription_data['is_subscription'] = True
            subscription_data['period'] = 'week'
            subscription_data['interval'] = 2  # Default subscription interval
            subscription_data['price'] = float(product_data.get('price', 0)) * 0.95  # 5% discount typical
            self.stdout.write(f'     ✅ WCSATT enabled - product CAN be subscription (default: 2 weeks, 5% off)')
        elif wcsatt_disabled:
            self.stdout.write(f'     ℹ️ Product set to "Sell one-time only" - no subscription processing')
        
        # Also check for subscription options in different formats
        if 'subscription_options' in product_data:
            self.stdout.write(f'     🔍 Direct subscription_options: {product_data["subscription_options"]}')
        
        return subscription_data

    def needs_subscription_update(self, existing_subscription, new_data):
        """Check if subscription data needs updating"""
        current_price = float(existing_subscription.price) if existing_subscription.price else None
        new_price = new_data.get('price')
        
        return (
            existing_subscription.period != new_data['period'] or
            existing_subscription.interval != new_data['interval'] or
            current_price != new_price or
            existing_subscription.trial_period != new_data.get('trial_period') or
            existing_subscription.trial_length != new_data.get('trial_length')
        )

    def update_subscription_object(self, subscription, subscription_data, woo_product):
        """Update existing ProductSubscription object"""
        subscription.price = Decimal(str(subscription_data['price'])) if subscription_data['price'] else None
        subscription.period = subscription_data['period']
        subscription.interval = subscription_data['interval']
        subscription.trial_period = subscription_data.get('trial_period')
        subscription.trial_length = subscription_data.get('trial_length')
        subscription.sign_up_fee = Decimal(str(subscription_data['sign_up_fee'])) if subscription_data.get('sign_up_fee') else None
        subscription.woo_data = woo_product
        subscription.save()
        
        # Also update the product's woo_data for frontend compatibility
        self.update_product_woo_data_with_subscription(subscription.product, {
            'price': float(subscription.price) if subscription.price else None,
            'period': subscription.period,
            'interval': subscription.interval,
            'trial_period': subscription.trial_period,
            'trial_length': subscription.trial_length,
            'sign_up_fee': float(subscription.sign_up_fee) if subscription.sign_up_fee else None
        })

    def update_product_woo_data_with_subscription(self, product, subscription_data):
        """Update product's woo_data to include subscription metadata for frontend compatibility"""
        try:
            # Find the corresponding ProductSimple record
            from crm.models import ProductSimple
            simple_product = ProductSimple.objects.filter(product=product).first()
            
            if simple_product and simple_product.woo_data:
                # Ensure woo_data is a dict
                woo_data = simple_product.woo_data
                if isinstance(woo_data, str):
                    import json
                    woo_data = json.loads(woo_data)
                
                # Update meta_data with _wcsatt_schemes
                meta_data = woo_data.get('meta_data', [])
                
                # Remove existing _wcsatt_schemes if any
                meta_data = [meta for meta in meta_data if meta.get('key') != '_wcsatt_schemes']
                
                # Create multiple _wcsatt_schemes entries with actual discounted prices
                discount = 5  # 5% discount
                
                # Get the base product price from the Product model (most accurate)
                base_price = float(simple_product.product.price) if simple_product.product.price else 0
                
                # Fallback to woo_data if Product price is not set
                if base_price == 0:
                    base_price = float(woo_data.get('price', 0))
                    if base_price == 0:
                        # Try to get from regular_price
                        base_price = float(woo_data.get('regular_price', 0))
                
                # Calculate discounted price
                discounted_price = base_price * (1 - discount / 100) if base_price > 0 else 0
                discounted_price_str = f"{discounted_price:.2f}"
                
                self.stdout.write(f'     💰 Base price: ${base_price:.2f} → Subscription price: ${discounted_price:.2f}')
                
                # 🔧 CRITICAL FIX: Populate subscription_price fields with actual calculated prices
                # Frontend reads subscription_price directly, not discount calculations
                wcsatt_schemes = [
                    {
                        "position": "0",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "week",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "2",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "1",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "week",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "4",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "2",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "week",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "6",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "3",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "month",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "2",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "4",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "month",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "3",
                        "subscription_payment_sync_date": 0
                    }
                ]
                
                # Add the new schemes
                meta_data.append({
                    "key": "_wcsatt_schemes",
                    "value": wcsatt_schemes
                })
                
                # Update woo_data and ensure price field exists for subscription calculation
                woo_data['meta_data'] = meta_data
                woo_data['price'] = str(base_price)  # Add price field for subscription calculations
                woo_data['regular_price'] = str(base_price)
                simple_product.woo_data = woo_data
                simple_product.save()
                
                self.stdout.write(f'     ✅ Updated product woo_data with _wcsatt_schemes for frontend')
                
        except Exception as e:
            self.stdout.write(f'     ⚠️ Error updating product woo_data: {str(e)}')

    def update_variation_woo_data_with_subscription(self, variation, subscription_data):
        """Update variation's woo_data to include subscription metadata for frontend compatibility"""
        try:
            # Ensure woo_data is a dict
            woo_data = variation.woo_data or {}
            if isinstance(woo_data, str):
                import json
                woo_data = json.loads(woo_data)
            
            # Get the base variation price from the variation model or woo_data
            base_price = float(variation.price) if variation.price else 0
            
            # Fallback to woo_data if variation price is not set
            if base_price == 0:
                base_price = float(woo_data.get('price', 0))
                if base_price == 0:
                    base_price = float(woo_data.get('regular_price', 0))
            
            if base_price > 0:
                # Update meta_data with _wcsatt_schemes
                meta_data = woo_data.get('meta_data', [])
                
                # Remove existing _wcsatt_schemes if any
                meta_data = [meta for meta in meta_data if meta.get('key') != '_wcsatt_schemes']
                
                # Create multiple _wcsatt_schemes entries with actual calculated prices
                discount = 5  # 5% discount
                discounted_price = base_price * (1 - discount / 100) if base_price > 0 else 0
                
                wcsatt_schemes = [
                    {
                        "position": "0",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "week",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "2",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "1",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "week",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "4",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "2",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "week",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "6",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "3",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "month",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "2",
                        "subscription_payment_sync_date": 0
                    },
                    {
                        "position": "4",
                        "subscription_price": str(discounted_price),  # 🔧 FIX: Add actual price
                        "subscription_length": "0",
                        "subscription_period": "month",
                        "subscription_discount": discount,
                        "subscription_sale_price": "",
                        "subscription_regular_price": str(base_price),  # 🔧 FIX: Add regular price
                        "subscription_pricing_method": "inherit",
                        "subscription_period_interval": "3",
                        "subscription_payment_sync_date": 0
                    }
                ]
                
                # Add the new schemes
                meta_data.append({
                    "key": "_wcsatt_schemes",
                    "value": wcsatt_schemes
                })
                
                # Update woo_data and ensure price field exists for subscription calculation
                woo_data['meta_data'] = meta_data
                woo_data['price'] = str(base_price)  # Add price field for subscription calculations
                woo_data['regular_price'] = str(base_price)
                variation.woo_data = woo_data
                variation.save()
                
                self.stdout.write(f'     ✅ Updated variation {variation.woo_variation_id} woo_data with _wcsatt_schemes')
                
        except Exception as e:
            self.stdout.write(f'     ⚠️ Error updating variation woo_data: {str(e)}')

    def remove_subscription_from_woo_data(self, product):
        """Remove subscription metadata from product's woo_data (for cleanup)"""
        try:
            # Find the corresponding ProductSimple record
            from crm.models import ProductSimple
            simple_product = ProductSimple.objects.filter(product=product).first()
            
            if simple_product and simple_product.woo_data:
                # Ensure woo_data is a dict
                woo_data = simple_product.woo_data
                if isinstance(woo_data, str):
                    import json
                    woo_data = json.loads(woo_data)
                
                # Remove subscription-related meta_data
                meta_data = woo_data.get('meta_data', [])
                
                # Remove _wcsatt_schemes and other subscription meta
                original_count = len(meta_data)
                meta_data = [meta for meta in meta_data if meta.get('key') not in [
                    '_wcsatt_schemes',
                    '_subscription_price',
                    '_subscription_period',
                    '_subscription_period_interval',
                    '_subscription_length',
                    '_subscription_trial_period',
                    '_subscription_trial_length',
                    '_subscription_sign_up_fee'
                ]]
                
                removed_count = original_count - len(meta_data)
                
                if removed_count > 0:
                    # Update woo_data
                    woo_data['meta_data'] = meta_data
                    simple_product.woo_data = woo_data
                    simple_product.save()
                    
                    self.stdout.write(f'     🗑️ Removed {removed_count} subscription meta fields from woo_data')
                else:
                    self.stdout.write(f'     ℹ️ No subscription meta fields found in woo_data to remove')
                    
        except Exception as e:
            self.stdout.write(f'     ⚠️ Error removing subscription from woo_data: {str(e)}')

    def display_results(self, stats):
        """Display sync results"""
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('📊 SUBSCRIPTION SYNC RESULTS'))
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
        
        # Subscriptions
        self.stdout.write(f'📋 Subscriptions:')
        self.stdout.write(f'   Created: {stats["subscriptions_created"]}')
        self.stdout.write(f'   Updated: {stats["subscriptions_updated"]}')
        self.stdout.write(f'   Removed: {stats["subscriptions_removed"]}')  # 🔧 NEW: Show cleanup count
        
        # Totals
        total_updated = (
            stats['simple_products_updated'] + 
            stats['variable_products_updated'] + 
            stats['variations_updated']
        )
        
        self.stdout.write(f'\n🎯 Total Updates: {total_updated}')
        self.stdout.write(f'❌ Errors: {stats["errors"]}')
        
        if total_updated > 0:
            self.stdout.write(self.style.SUCCESS(f'✅ Subscription sync completed successfully!'))
        else:
            self.stdout.write(self.style.WARNING('ℹ️ All subscription data was already up to date'))
