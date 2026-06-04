from django.core.management.base import BaseCommand
from django.db import transaction
from crm.models import Product, ProductVariation, ProductSubscription
from crm.woocommerce import WooCommerceAPI
from decimal import Decimal, InvalidOperation
import logging
import time

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync product prices from WooCommerce to local database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--product-id',
            type=int,
            help='Sync prices for a specific product ID only'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=50,
            help='Number of products to process per batch (default: 50)'
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.verbose = options['verbose']
        self.batch_size = options['batch_size']
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        try:
            wc_api = WooCommerceAPI()
            
            if options['product_id']:
                self.sync_single_product(wc_api, options['product_id'])
            else:
                self.sync_all_products(wc_api)
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error during price sync: {str(e)}'))
            logger.error(f'Price sync error: {str(e)}', exc_info=True)

    def sync_single_product(self, wc_api, product_id):
        """Sync prices for a single product"""
        try:
            product = Product.objects.get(woo_product_id=product_id)
            self.stdout.write(f'Syncing prices for product: {product.name} (ID: {product_id})')
            
            updated = self.sync_product_prices(wc_api, product)
            
            if updated:
                self.stdout.write(self.style.SUCCESS(f'✓ Updated prices for {product.name}'))
            else:
                self.stdout.write(f'- No price changes needed for {product.name}')
                
        except Product.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Product with WooCommerce ID {product_id} not found in database'))

    def sync_all_products(self, wc_api):
        """Sync prices for all products"""
        products = Product.objects.all().order_by('woo_product_id')
        total_products = products.count()
        updated_count = 0
        error_count = 0
        
        self.stdout.write(f'Starting price sync for {total_products} products...')
        
        for i, product in enumerate(products, 1):
            try:
                if self.verbose:
                    self.stdout.write(f'[{i}/{total_products}] Processing: {product.name}')
                
                updated = self.sync_product_prices(wc_api, product)
                
                if updated:
                    updated_count += 1
                    if self.verbose:
                        self.stdout.write(self.style.SUCCESS(f'  ✓ Updated prices'))
                
                # Add small delay to avoid overwhelming the API
                if i % self.batch_size == 0:
                    time.sleep(1)
                    self.stdout.write(f'Processed {i}/{total_products} products...')
                    
            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(f'  ✗ Error processing {product.name}: {str(e)}'))
                logger.error(f'Error syncing prices for product {product.woo_product_id}: {str(e)}')
        
        self.stdout.write(self.style.SUCCESS(f'\nPrice sync completed!'))
        self.stdout.write(f'Total products: {total_products}')
        self.stdout.write(f'Updated: {updated_count}')
        self.stdout.write(f'Errors: {error_count}')

    def sync_product_prices(self, wc_api, product):
        """Sync prices for a specific product and its variations"""
        updated = False
        
        try:
            # Get fresh product data from WooCommerce
            wc_response = wc_api.get_product(product.woo_product_id)
            
            if not wc_response or wc_response.get('status') == 'error' or not wc_response.get('data'):
                if self.verbose:
                    error_msg = wc_response.get('error', 'Unknown error') if wc_response else 'No response'
                    self.stdout.write(f'  - Product {product.woo_product_id} not found in WooCommerce: {error_msg}')
                return False
            
            # Extract the actual product data
            wc_product = wc_response['data']
            
            # Update main product prices
            main_updated = self.update_main_product_prices(product, wc_product)
            if main_updated:
                updated = True
            
            # Update variation prices if it's a variable product
            if product.product_type in ['variable', 'variable_subscription']:
                variations_updated = self.update_variation_prices(wc_api, product, wc_product)
                if variations_updated:
                    updated = True
            
            # Update subscription prices if it's a subscription product
            if product.product_type in ['subscription', 'variable_subscription']:
                subscription_updated = self.update_subscription_prices(product, wc_product)
                if subscription_updated:
                    updated = True
            
            return updated
            
        except Exception as e:
            logger.error(f'Error syncing prices for product {product.woo_product_id}: {str(e)}')
            raise

    def update_main_product_prices(self, product, wc_product):
        """Update main product price fields"""
        updated = False
        changes = []
        
        # Extract price data from WooCommerce
        wc_price = self.safe_decimal(wc_product.get('price'))
        wc_regular_price = self.safe_decimal(wc_product.get('regular_price'))
        wc_sale_price = self.safe_decimal(wc_product.get('sale_price'))
        
        # Debug logging - show raw WooCommerce data
        if self.verbose:
            self.stdout.write(f'  Product Type: {wc_product.get("type", "unknown")}')
            self.stdout.write(f'  WooCommerce raw data:')
            self.stdout.write(f'    price: {repr(wc_product.get("price"))} -> {wc_price}')
            self.stdout.write(f'    regular_price: {repr(wc_product.get("regular_price"))} -> {wc_regular_price}')
            self.stdout.write(f'    sale_price: {repr(wc_product.get("sale_price"))} -> {wc_sale_price}')
            self.stdout.write(f'  Current database values:')
            self.stdout.write(f'    price: {product.price}')
            self.stdout.write(f'    regular_price: {product.regular_price}')
            self.stdout.write(f'    sale_price: {product.sale_price}')
            
            # For variable products, check if we need to get price from variations
            if wc_product.get('type') == 'variable' and all(p is None for p in [wc_price, wc_regular_price, wc_sale_price]):
                self.stdout.write(f'  ⚠️  Variable product with no main prices - checking variations...')
        
        # Check for price changes
        if product.price != wc_price:
            changes.append(f'price: {product.price} → {wc_price}')
            if not self.dry_run:
                product.price = wc_price
                updated = True
        
        if product.regular_price != wc_regular_price:
            changes.append(f'regular_price: {product.regular_price} → {wc_regular_price}')
            if not self.dry_run:
                product.regular_price = wc_regular_price
                updated = True
        
        if product.sale_price != wc_sale_price:
            changes.append(f'sale_price: {product.sale_price} → {wc_sale_price}')
            if not self.dry_run:
                product.sale_price = wc_sale_price
                updated = True
        
        if updated and not self.dry_run:
            product.save(update_fields=['price', 'regular_price', 'sale_price', 'updated_at'])
        
        if changes:
            if self.verbose:
                self.stdout.write(f'  Main product price changes: {", ".join(changes)}')
            # Return True even in dry run mode to indicate changes would be made
            return True
        elif self.verbose:
            self.stdout.write(f'  No price changes detected')
        
        return updated

    def update_variation_prices(self, wc_api, product, wc_product):
        """Update variation prices for variable products"""
        updated = False
        
        try:
            # Get variations from WooCommerce
            wc_variations = wc_api.get_product_variations(product.woo_product_id)
            
            if not wc_variations:
                return False
            
            for wc_variation in wc_variations:
                variation_id = wc_variation.get('id')
                
                try:
                    variation = ProductVariation.objects.get(
                        product=product,
                        woo_variation_id=variation_id
                    )
                    
                    variation_updated = self.update_variation_price_fields(variation, wc_variation)
                    if variation_updated:
                        updated = True
                        
                except ProductVariation.DoesNotExist:
                    if self.verbose:
                        self.stdout.write(f'  - Variation {variation_id} not found in database')
                    continue
            
            return updated
            
        except Exception as e:
            logger.error(f'Error updating variation prices for product {product.woo_product_id}: {str(e)}')
            return False

    def update_variation_price_fields(self, variation, wc_variation):
        """Update individual variation price fields"""
        updated = False
        changes = []
        
        # Extract variation price data
        wc_price = self.safe_decimal(wc_variation.get('price'))
        wc_regular_price = self.safe_decimal(wc_variation.get('regular_price'))
        wc_sale_price = self.safe_decimal(wc_variation.get('sale_price'))
        
        # Check for price changes
        if variation.price != wc_price:
            changes.append(f'price: {variation.price} → {wc_price}')
            if not self.dry_run:
                variation.price = wc_price
                updated = True
        
        if variation.regular_price != wc_regular_price:
            changes.append(f'regular_price: {variation.regular_price} → {wc_regular_price}')
            if not self.dry_run:
                variation.regular_price = wc_regular_price
                updated = True
        
        if variation.sale_price != wc_sale_price:
            changes.append(f'sale_price: {variation.sale_price} → {wc_sale_price}')
            if not self.dry_run:
                variation.sale_price = wc_sale_price
                updated = True
        
        if updated and not self.dry_run:
            variation.save(update_fields=['price', 'regular_price', 'sale_price', 'updated_at'])
        
        if changes and self.verbose:
            variation_name = f"Variation {variation.woo_variation_id}"
            if variation.attributes:
                attr_str = ", ".join([f"{attr.get('name', '')}: {attr.get('option', '')}" 
                                    for attr in variation.attributes if attr.get('name')])
                if attr_str:
                    variation_name += f" ({attr_str})"
            self.stdout.write(f'    {variation_name}: {", ".join(changes)}')
        
        return updated

    def update_subscription_prices(self, product, wc_product):
        """Update subscription price fields"""
        updated = False
        
        try:
            subscription = ProductSubscription.objects.get(product=product)
            
            # Extract subscription price data
            wc_subscription_price = self.safe_decimal(wc_product.get('subscription_price', wc_product.get('price')))
            wc_sign_up_fee = self.safe_decimal(wc_product.get('subscription_sign_up_fee'))
            
            changes = []
            
            # Check for subscription price changes
            if subscription.price != wc_subscription_price:
                changes.append(f'subscription_price: {subscription.price} → {wc_subscription_price}')
                if not self.dry_run:
                    subscription.price = wc_subscription_price
                    updated = True
            
            if subscription.sign_up_fee != wc_sign_up_fee:
                changes.append(f'sign_up_fee: {subscription.sign_up_fee} → {wc_sign_up_fee}')
                if not self.dry_run:
                    subscription.sign_up_fee = wc_sign_up_fee
                    updated = True
            
            if updated and not self.dry_run:
                subscription.save(update_fields=['price', 'sign_up_fee', 'updated_at'])
            
            if changes and self.verbose:
                self.stdout.write(f'  Subscription price changes: {", ".join(changes)}')
            
            return updated
            
        except ProductSubscription.DoesNotExist:
            return False
        except Exception as e:
            logger.error(f'Error updating subscription prices for product {product.woo_product_id}: {str(e)}')
            return False

    def safe_decimal(self, value):
        """Safely convert a value to Decimal, handling empty strings and None"""
        if value is None or value == '' or value == 0:
            return None
        
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
