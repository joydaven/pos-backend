"""
Fresh Product Sync Command

This command performs a comprehensive sync of products from WooCommerce:
1. Syncs all products from WooCommerce (including bundles)
2. Removes products that no longer exist in WooCommerce
3. Updates bundle data with fresh WooCommerce information
4. Ensures parent-child relationships are correct
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from crm.models import Product, ProductBundle, ProductVariation, ProductSubscription
from crm.views import process_product
from crm.woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Perform a fresh sync of products from WooCommerce with cleanup'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limit number of products to sync (0 for all)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        
        self.stdout.write(self.style.SUCCESS(f'🚀 Starting Fresh Product Sync (dry_run={dry_run}, limit={limit})'))
        self.stdout.write('=' * 60)
        
        # Initialize WooCommerce API
        wc = WooCommerceAPI()
        
        # Step 1: Get all WooCommerce product IDs
        self.stdout.write(self.style.SUCCESS('📊 Step 1: Fetching WooCommerce product IDs...'))
        woo_product_ids = set()
        page = 1
        per_page = 100
        
        while True:
            try:
                result = wc.get_products(page=page, per_page=per_page)
                if not result or not result.get('data'):
                    break
                    
                for product in result['data']:
                    woo_product_ids.add(str(product['id']))
                    
                self.stdout.write(f'  Page {page}: Found {len(result["data"])} products')
                
                # Check if we have more pages
                if page >= result.get('total_pages', 1):
                    break
                    
                page += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error fetching page {page}: {str(e)}'))
                break
        
        self.stdout.write(self.style.SUCCESS(f'✅ Found {len(woo_product_ids)} products in WooCommerce'))
        
        # Step 2: Identify orphaned products in our database
        self.stdout.write(self.style.SUCCESS('🔍 Step 2: Identifying orphaned products...'))
        
        # Get all product IDs from our database
        db_products = Product.objects.all().values('id', 'woo_product_id', 'name')
        orphaned_products = []
        
        for db_product in db_products:
            woo_id = str(db_product['woo_product_id'])
            if woo_id not in woo_product_ids:
                orphaned_products.append(db_product)
        
        self.stdout.write(f'📊 Found {len(orphaned_products)} orphaned products in database')
        
        if orphaned_products:
            self.stdout.write('🗑️  Orphaned products to be removed:')
            for product in orphaned_products[:10]:  # Show first 10
                self.stdout.write(f'  - {product["name"]} (WooCommerce ID: {product["woo_product_id"]})')
            if len(orphaned_products) > 10:
                self.stdout.write(f'  ... and {len(orphaned_products) - 10} more')
        
        # Step 3: Remove orphaned products
        if orphaned_products and not dry_run:
            self.stdout.write(self.style.WARNING('🗑️  Step 3: Removing orphaned products...'))
            
            for product in orphaned_products:
                try:
                    product_obj = Product.objects.get(id=product['id'])
                    
                    # Remove related data first
                    ProductVariation.objects.filter(product=product_obj).delete()
                    ProductBundle.objects.filter(product=product_obj).delete()
                    ProductSubscription.objects.filter(product=product_obj).delete()
                    
                    # Remove the product
                    product_obj.delete()
                    
                    self.stdout.write(f'  ✅ Removed: {product["name"]}')
                    
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  ❌ Error removing {product["name"]}: {str(e)}'))
            
            self.stdout.write(self.style.SUCCESS(f'✅ Removed {len(orphaned_products)} orphaned products'))
        elif orphaned_products and dry_run:
            self.stdout.write(self.style.WARNING(f'🔍 DRY RUN: Would remove {len(orphaned_products)} orphaned products'))
        else:
            self.stdout.write(self.style.SUCCESS('✅ No orphaned products found'))
        
        # Step 4: Sync fresh products from WooCommerce
        self.stdout.write(self.style.SUCCESS('🔄 Step 4: Syncing fresh products from WooCommerce...'))
        
        processed_count = 0
        page = 1
        
        while True:
            try:
                result = wc.get_products(page=page, per_page=per_page)
                if not result or not result.get('data'):
                    break
                
                self.stdout.write(f'📄 Processing page {page}/{result.get("total_pages", 1)}...')
                
                for product in result['data']:
                    try:
                        if not dry_run:
                            product_obj = process_product(product, wc)
                            processed_count += 1
                            
                            self.stdout.write(
                                f'  [{processed_count}] ✅ {product["name"]} (Type: {product_obj.product_type})'
                            )
                        else:
                            processed_count += 1
                            self.stdout.write(
                                f'  [{processed_count}] 🔍 Would sync: {product["name"]} (Type: {product.get("type", "simple")})'
                            )
                        
                        # Check limit
                        if limit > 0 and processed_count >= limit:
                            self.stdout.write(self.style.SUCCESS(f'✅ Reached limit of {limit} products'))
                            return
                            
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'  ❌ Error processing {product["name"]}: {str(e)}'))
                        continue
                
                # Check if we have more pages
                if page >= result.get('total_pages', 1):
                    break
                    
                page += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error processing page {page}: {str(e)}'))
                break
        
        # Step 5: Summary
        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS('📊 FRESH SYNC SUMMARY:'))
        self.stdout.write(f'  🔍 WooCommerce products found: {len(woo_product_ids)}')
        self.stdout.write(f'  🗑️  Orphaned products {"would be " if dry_run else ""}removed: {len(orphaned_products)}')
        self.stdout.write(f'  🔄 Products {"would be " if dry_run else ""}synced: {processed_count}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 This was a DRY RUN - no changes were made'))
            self.stdout.write('    Run without --dry-run to perform actual sync')
        else:
            self.stdout.write(self.style.SUCCESS('✅ Fresh sync completed successfully!'))
            self.stdout.write('    Your database now contains only fresh WooCommerce data')
