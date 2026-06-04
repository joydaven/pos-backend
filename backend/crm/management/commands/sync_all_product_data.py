"""
Management command to sync all product subscription metadata and pricing data from WooCommerce.

This command:
1. Fetches fresh data from WooCommerce for all products with cache-busting
2. Compares POS database vs WooCommerce data for subscription metadata and pricing
3. Updates outdated records in the POS system
4. Provides detailed reporting on sync status

Usage:
    python manage.py sync_all_product_data [--dry-run] [--product-id ID] [--batch-size N]
"""

import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import transaction
from django.db.models import Q

from crm.models import Product, ProductSimple, ProductVariation, ProductSubscription
from crm.woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Sync all product subscription metadata and pricing from WooCommerce"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--product-id',
            type=str,
            help='Sync specific product by WooCommerce ID',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of products to process in each batch (default: 50)',
        )
        parser.add_argument(
            '--subscription-only',
            action='store_true',
            help='Only sync subscription metadata, skip pricing',
        )
        parser.add_argument(
            '--pricing-only',
            action='store_true',
            help='Only sync pricing data, skip subscription metadata',
        )
        parser.add_argument(
            '--force-update',
            action='store_true',
            help='Force update all products regardless of last sync time',
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.batch_size = options['batch_size']
        self.product_id = options['product_id']
        self.subscription_only = options['subscription_only']
        self.pricing_only = options['pricing_only']
        self.force_update = options['force_update']
        
        # Initialize counters
        self.stats = {
            'total_processed': 0,
            'subscription_updates': 0,
            'pricing_updates': 0,
            'no_changes': 0,
            'errors': 0,
            'skipped': 0,
            'stale_data_detected': 0
        }
        
        self.stdout.write(self.style.SUCCESS('🚀 Starting Product Data Sync'))
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('📋 DRY RUN MODE - No changes will be made'))
        
        try:
            # Initialize WooCommerce API with cache busting
            self.wc_api = WooCommerceAPI()
            
            # Get products to sync
            products = self.get_products_to_sync()
            
            if not products:
                self.stdout.write(self.style.WARNING('No products found to sync'))
                return
            
            self.stdout.write(f'📦 Found {len(products)} products to sync')
            
            # Process products in batches
            self.process_products_in_batches(products)
            
            # Print final stats
            self.print_final_stats()
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Error during sync: {str(e)}'))
            raise CommandError(f'Sync failed: {str(e)}')

    def get_products_to_sync(self) -> List[Product]:
        """Get list of products that need syncing."""
        if self.product_id:
            # Sync specific product
            try:
                return [Product.objects.get(woo_product_id=self.product_id)]
            except Product.DoesNotExist:
                raise CommandError(f'Product with WooCommerce ID {self.product_id} not found')
        
        # Get all products with WooCommerce IDs
        queryset = Product.objects.filter(
            woo_product_id__isnull=False
        ).order_by('id')
        
        if not self.force_update:
            # Only sync products that haven't been synced recently (last 24 hours)
            cutoff = timezone.now() - timezone.timedelta(hours=24)
            
            # Filter products that need subscription sync
            subscription_filter = Q()
            if not self.pricing_only:
                subscription_filter = Q(
                    Q(productsimple__woo_data__last_subscription_sync__lt=cutoff.isoformat()) |
                    Q(productsimple__woo_data__last_subscription_sync__isnull=True) |
                    Q(productsimple__woo_data__isnull=True)
                )
            
            # Filter products that need pricing sync
            pricing_filter = Q()
            if not self.subscription_only:
                pricing_filter = Q(
                    Q(updated_at__lt=cutoff) |
                    Q(updated_at__isnull=True)
                )
            
            if subscription_filter and pricing_filter:
                queryset = queryset.filter(subscription_filter | pricing_filter)
            elif subscription_filter:
                queryset = queryset.filter(subscription_filter)
            elif pricing_filter:
                queryset = queryset.filter(pricing_filter)
        
        return list(queryset)

    def process_products_in_batches(self, products: List[Product]):
        """Process products in batches to avoid memory issues."""
        total = len(products)
        processed = 0
        
        for i in range(0, total, self.batch_size):
            batch = products[i:i + self.batch_size]
            
            self.stdout.write(f'📦 Processing batch {i//self.batch_size + 1} ({len(batch)} products)')
            
            for product in batch:
                try:
                    self.sync_product_data(product)
                    processed += 1
                    
                    if processed % 10 == 0:
                        self.stdout.write(f'✅ Processed {processed}/{total} products')
                        
                except Exception as e:
                    self.stats['errors'] += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f'❌ Error syncing product {product.woo_product_id} ({product.name}): {str(e)}'
                        )
                    )
                    continue
            
            # Small delay between batches to avoid overwhelming the API
            time.sleep(1)

    def sync_product_data(self, product: Product):
        """Sync subscription metadata and pricing for a single product."""
        self.stats['total_processed'] += 1
        changes_made = False
        
        try:
            # Get fresh product data from WooCommerce with cache-busting
            wc_product_data = self.get_fresh_woocommerce_data(product.woo_product_id)
            
            if not wc_product_data:
                self.stats['skipped'] += 1
                self.stdout.write(
                    self.style.WARNING(f'⚠️ Skipping {product.name} - could not fetch WooCommerce data')
                )
                return
            
            # Sync subscription metadata
            if not self.pricing_only:
                subscription_changes = self.sync_subscription_metadata(product, wc_product_data)
                if subscription_changes:
                    changes_made = True
                    self.stats['subscription_updates'] += 1
            
            # Sync pricing data
            if not self.subscription_only:
                pricing_changes = self.sync_pricing_data(product, wc_product_data)
                if pricing_changes:
                    changes_made = True
                    self.stats['pricing_updates'] += 1
            
            if not changes_made:
                self.stats['no_changes'] += 1
                
        except Exception as e:
            self.stats['errors'] += 1
            raise e

    def get_fresh_woocommerce_data(self, woo_product_id: str) -> dict:
        """Get fresh product data from WooCommerce with cache busting."""
        try:
            # Method 1: Try with cache busting parameter (if API supports it)
            cache_bust = int(time.time())
            
            # First attempt: try to use cache busting parameter
            try:
                response = self.wc_api.get_product(woo_product_id, params={'_cb': cache_bust, '_t': cache_bust})
                if response and response.get('status') == 'success' and response.get('data'):
                    self.stdout.write(f'📡 Fresh data fetched for product {woo_product_id} (with cache busting)')
                    return response['data']
            except Exception as cache_bust_error:
                # Cache busting with params failed, try without
                self.stdout.write(f'⚠️ Cache busting failed for {woo_product_id}, trying without: {str(cache_bust_error)}')
            
            # Method 2: Fallback to regular API call with delay to avoid cache
            time.sleep(0.5)  # Small delay to avoid hitting cached data
            response = self.wc_api.get_product(woo_product_id)
            
            if response and response.get('status') == 'success' and response.get('data'):
                wc_data = response['data']
                # Debug: Show what we fetched vs expecting fresh data
                self.stdout.write(f'📡 Data fetched for product {woo_product_id} (fallback method)')
                self.stdout.write(f'   WC Data: price={wc_data.get("price")}, regular_price={wc_data.get("regular_price")}, sale_price="{wc_data.get("sale_price")}"')
                return wc_data
            else:
                logger.warning(f"Failed to fetch product {woo_product_id}: {response}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching product {woo_product_id}: {str(e)}")
            return None

    def sync_subscription_metadata(self, product: Product, wc_data: dict) -> bool:
        """Sync subscription metadata for a product."""
        from crm.views import extract_subscription_metadata, sync_product_subscription_metadata
        
        try:
            # Extract subscription data from WooCommerce
            subscription_data = extract_subscription_metadata(wc_data)
            
            # Check if subscription metadata needs updating
            needs_update = self.check_subscription_metadata_outdated(product, subscription_data)
            
            if not needs_update:
                return False
            
            if self.dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f'📅 [DRY RUN] Would update subscription metadata for {product.name}'
                    )
                )
                return True
            
            # Perform the sync
            with transaction.atomic():
                sync_result = sync_product_subscription_metadata(product, wc_data)
                
                if sync_result and sync_result.get('success'):
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'📅 Updated subscription metadata for {product.name}'
                        )
                    )
                    return True
                else:
                    logger.error(f"Subscription sync failed for {product.name}: {sync_result}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error syncing subscription metadata for {product.name}: {str(e)}")
            return False

    def sync_pricing_data(self, product: Product, wc_data: dict) -> bool:
        """Sync pricing data for a product."""
        try:
            # Extract pricing from WooCommerce data
            wc_price = self.safe_decimal(wc_data.get('price'))
            wc_regular_price = self.safe_decimal(wc_data.get('regular_price'))
            wc_sale_price = self.safe_decimal(wc_data.get('sale_price'))
            
            # Debug: Show price comparison
            self.stdout.write(f'💰 Price comparison for {product.name}:')
            self.stdout.write(f'   Current in DB: price={product.price}, regular={product.regular_price}, sale={product.sale_price}')
            self.stdout.write(f'   Fresh from WC: price={wc_price}, regular={wc_regular_price}, sale={wc_sale_price}')
            self.stdout.write(f'   Raw WC data: price="{wc_data.get("price")}", regular_price="{wc_data.get("regular_price")}", sale_price="{wc_data.get("sale_price")}"')
            
            # CRITICAL: Check if WooCommerce data appears to be stale
            # If our DB has more recent data than WC API, it's likely cached/stale
            if self.is_woocommerce_data_stale(product, wc_data):
                self.stdout.write(
                    self.style.WARNING(
                        f'⚠️ STALE DATA DETECTED for {product.name} - WooCommerce API returned cached data. Skipping pricing update.'
                    )
                )
                self.stdout.write('   📅 Recommendation: Data was recently updated via webhook. API cache needs time to refresh.')
                self.stats['stale_data_detected'] += 1
                return False
            
            # Check if pricing needs updating
            pricing_changes = self.get_pricing_changes(product, wc_price, wc_regular_price, wc_sale_price)
            
            if not pricing_changes:
                return False
            
            if self.dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f'💰 [DRY RUN] Would update pricing for {product.name}: {", ".join(pricing_changes)}'
                    )
                )
                return True
            
            # Update pricing
            with transaction.atomic():
                if product.price != wc_price:
                    product.price = wc_price
                if product.regular_price != wc_regular_price:
                    product.regular_price = wc_regular_price
                if product.sale_price != wc_sale_price:
                    product.sale_price = wc_sale_price
                
                product.save(update_fields=['price', 'regular_price', 'sale_price', 'updated_at'])
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'💰 Updated pricing for {product.name}: {", ".join(pricing_changes)}'
                    )
                )
                
                # Also sync variations if this is a variable product
                if product.product_type in ['variable', 'variable_subscription']:
                    self.sync_variation_pricing(product, wc_data)
                
                return True
                
        except Exception as e:
            logger.error(f"Error syncing pricing for {product.name}: {str(e)}")
            return False

    def sync_variation_pricing(self, product: Product, wc_data: dict):
        """Sync pricing for product variations."""
        try:
            variations_data = self.wc_api.get_product_variations(product.woo_product_id)
            
            if not variations_data:
                return
            
            for wc_variation in variations_data:
                variation_id = wc_variation.get('id')
                
                try:
                    local_variation = ProductVariation.objects.get(
                        product=product,
                        woo_variation_id=variation_id
                    )
                    
                    # Extract variation pricing
                    wc_price = self.safe_decimal(wc_variation.get('price'))
                    wc_regular_price = self.safe_decimal(wc_variation.get('regular_price'))
                    wc_sale_price = self.safe_decimal(wc_variation.get('sale_price'))
                    
                    # Check for changes
                    changes = []
                    if local_variation.price != wc_price:
                        changes.append(f'price: {local_variation.price} → {wc_price}')
                        local_variation.price = wc_price
                    if local_variation.regular_price != wc_regular_price:
                        changes.append(f'regular_price: {local_variation.regular_price} → {wc_regular_price}')
                        local_variation.regular_price = wc_regular_price
                    if local_variation.sale_price != wc_sale_price:
                        changes.append(f'sale_price: {local_variation.sale_price} → {wc_sale_price}')
                        local_variation.sale_price = wc_sale_price
                    
                    if changes and not self.dry_run:
                        local_variation.save(update_fields=['price', 'regular_price', 'sale_price'])
                        self.stdout.write(f'  📦 Updated variation {variation_id}: {", ".join(changes)}')
                        
                except ProductVariation.DoesNotExist:
                    continue
                    
        except Exception as e:
            logger.error(f"Error syncing variation pricing for {product.name}: {str(e)}")

    def check_subscription_metadata_outdated(self, product: Product, new_subscription_data: dict) -> bool:
        """Check if subscription metadata needs updating."""
        try:
            # Get current subscription metadata from ProductSimple
            simple = ProductSimple.objects.filter(product=product).first()
            
            if not simple or not simple.woo_data:
                return new_subscription_data.get('has_subscription', False)
            
            current_metadata = simple.woo_data.get('subscription_metadata', {})
            
            # Compare key subscription fields
            key_fields = [
                'has_subscription', 'subscription_price', 'subscription_period',
                'subscription_interval', 'subscription_discount', 'plugin_type'
            ]
            
            for field in key_fields:
                if current_metadata.get(field) != new_subscription_data.get(field):
                    return True
            
            # Check subscription schemes for WCSATT
            current_schemes = current_metadata.get('subscription_schemes', {})
            new_schemes = new_subscription_data.get('subscription_schemes', {})
            
            if current_schemes != new_schemes:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking subscription metadata for {product.name}: {str(e)}")
            return True  # Update if we can't determine

    def get_pricing_changes(self, product: Product, wc_price: Decimal, wc_regular_price: Decimal, wc_sale_price: Decimal) -> List[str]:
        """Get list of pricing changes needed."""
        changes = []
        
        if product.price != wc_price:
            changes.append(f'price: {product.price} → {wc_price}')
        if product.regular_price != wc_regular_price:
            changes.append(f'regular_price: {product.regular_price} → {wc_regular_price}')
        if product.sale_price != wc_sale_price:
            changes.append(f'sale_price: {product.sale_price} → {wc_sale_price}')
        
        return changes

    def is_woocommerce_data_stale(self, product: Product, wc_data: dict) -> bool:
        """Detect if WooCommerce API returned stale/cached data."""
        try:
            # Check if product was recently updated (within last hour)
            # If so, and WC data differs significantly, it's likely cached
            if product.updated_at:
                time_since_update = timezone.now() - product.updated_at
                recently_updated = time_since_update.total_seconds() < 3600  # 1 hour
                
                if recently_updated:
                    # If recently updated via webhook, and API shows different data,
                    # it's likely the API is returning cached data
                    wc_price = self.safe_decimal(wc_data.get('price'))
                    wc_sale_price = self.safe_decimal(wc_data.get('sale_price'))
                    
                    # Check for suspicious patterns that indicate stale data:
                    # 1. WC API shows sale price when DB has None (sale was removed)
                    # 2. WC API price differs from DB price when recently updated
                    
                    if (product.sale_price is None and wc_sale_price is not None):
                        self.stdout.write(f'   🔍 Stale indicator: DB has no sale price, but WC API shows sale_price={wc_sale_price}')
                        return True
                    
                    if (product.price != wc_price and wc_price is not None):
                        self.stdout.write(f'   🔍 Stale indicator: DB price={product.price} != WC price={wc_price} (recently updated)')
                        return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error checking for stale data: {str(e)}")
            return False  # If we can't determine, proceed with update

    def safe_decimal(self, value) -> Decimal:
        """Safely convert value to Decimal - same logic as webhook sync."""
        if value is None or value == '' or value == 0:
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def print_final_stats(self):
        """Print final statistics."""
        self.stdout.write(self.style.SUCCESS('\n📊 Sync Complete!'))
        self.stdout.write(f'Total Processed: {self.stats["total_processed"]}')
        self.stdout.write(f'Subscription Updates: {self.stats["subscription_updates"]}')
        self.stdout.write(f'Pricing Updates: {self.stats["pricing_updates"]}')
        self.stdout.write(f'No Changes Needed: {self.stats["no_changes"]}')
        self.stdout.write(f'Skipped: {self.stats["skipped"]}')
        
        if self.stats['stale_data_detected'] > 0:
            self.stdout.write(self.style.WARNING(f'Stale Data Detected: {self.stats["stale_data_detected"]}'))
        
        if self.stats['errors'] > 0:
            self.stdout.write(self.style.ERROR(f'Errors: {self.stats["errors"]}'))
        else:
            self.stdout.write(self.style.SUCCESS('Errors: 0'))
