"""
Management command to sync brands from product data.

This command extracts brand information from all product types
(ProductSimple, ProductVariation, ProductBundle) and populates
the Brand table for fast filtering.

Usage:
    python manage.py sync_brands
    python manage.py sync_brands --verbose
    python manage.py sync_brands --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from crm.models import Brand
import logging


class Command(BaseCommand):
    help = 'Sync brands from product woo_data to Brand table for fast filtering'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without making changes',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output during sync',
        )

    def handle(self, *args, **options):
        """Execute the brand sync command."""
        
        # Set up logging
        if options['verbose']:
            logging.basicConfig(level=logging.INFO)
        
        self.stdout.write(
            self.style.SUCCESS('🔄 Starting brand sync from product data...')
        )
        
        try:
            if options['dry_run']:
                self.stdout.write(
                    self.style.WARNING('🧪 DRY RUN MODE - No changes will be made')
                )
                stats = self._dry_run_sync()
            else:
                with transaction.atomic():
                    stats = Brand.sync_from_products()
            
            # Display results
            self._display_sync_results(stats, options['dry_run'])
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Brand sync failed: {str(e)}')
            )
            raise CommandError(f'Brand sync failed: {str(e)}')

    def _dry_run_sync(self):
        """
        Perform a dry run to show what would be synced.
        
        Returns:
            dict: Statistics about what would be synced
        """
        from crm.models import ProductSimple, ProductVariation, ProductBundle
        from collections import defaultdict
        
        brand_counts = defaultdict(int)
        
        # Process ProductSimple
        simple_products = ProductSimple.objects.exclude(woo_data__isnull=True)
        for product in simple_products:
            brands = Brand._extract_brands_from_woo_data(product.woo_data)
            for brand in brands:
                brand_counts[brand] += 1
        
        # Process ProductVariation
        variation_products = ProductVariation.objects.exclude(woo_data__isnull=True)
        for product in variation_products:
            brands = Brand._extract_brands_from_woo_data(product.woo_data)
            for brand in brands:
                brand_counts[brand] += 1
        
        # Process ProductBundle
        bundle_products = ProductBundle.objects.exclude(woo_data__isnull=True)
        for product in bundle_products:
            brands = Brand._extract_brands_from_woo_data(product.woo_data)
            for brand in brands:
                brand_counts[brand] += 1
        
        # Check existing brands
        existing_brands = set(Brand.objects.values_list('name', flat=True))
        new_brands = set(brand_counts.keys()) - existing_brands
        updated_brands = set(brand_counts.keys()) & existing_brands
        deactivated_brands = existing_brands - set(brand_counts.keys())
        
        return {
            'total_brands_found': len(brand_counts),
            'brands_created': len(new_brands),
            'brands_updated': len(updated_brands),
            'brands_deactivated': len(deactivated_brands),
            'new_brands': sorted(new_brands),
            'updated_brands': sorted(updated_brands),
            'deactivated_brands': sorted(deactivated_brands),
            'brand_counts': dict(brand_counts)
        }

    def _display_sync_results(self, stats, is_dry_run=False):
        """
        Display the results of the sync operation.
        
        Args:
            stats (dict): Statistics from the sync operation
            is_dry_run (bool): Whether this was a dry run
        """
        action_verb = "Would be" if is_dry_run else "Were"
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('📊 Brand Sync Results:'))
        self.stdout.write(f'  • Total brands found: {stats["total_brands_found"]}')
        self.stdout.write(f'  • Brands {action_verb.lower()} created: {stats["brands_created"]}')
        self.stdout.write(f'  • Brands {action_verb.lower()} updated: {stats["brands_updated"]}')
        self.stdout.write(f'  • Brands {action_verb.lower()} deactivated: {stats["brands_deactivated"]}')
        
        if is_dry_run and 'new_brands' in stats:
            if stats['new_brands']:
                self.stdout.write('')
                self.stdout.write(self.style.WARNING('🆕 New brands that would be created:'))
                for brand in stats['new_brands'][:10]:  # Show first 10
                    count = stats['brand_counts'].get(brand, 0)
                    self.stdout.write(f'  • {brand} ({count} products)')
                if len(stats['new_brands']) > 10:
                    self.stdout.write(f'  ... and {len(stats["new_brands"]) - 10} more')
            
            if stats['deactivated_brands']:
                self.stdout.write('')
                self.stdout.write(self.style.WARNING('⚠️  Brands that would be deactivated:'))
                for brand in stats['deactivated_brands'][:10]:  # Show first 10
                    self.stdout.write(f'  • {brand}')
                if len(stats['deactivated_brands']) > 10:
                    self.stdout.write(f'  ... and {len(stats["deactivated_brands"]) - 10} more')
        
        if not is_dry_run:
            self.stdout.write('')
            self.stdout.write(
                self.style.SUCCESS('✅ Brand sync completed successfully!')
            )
            if 'sync_completed_at' in stats:
                self.stdout.write(f'   Completed at: {stats["sync_completed_at"]}')
        else:
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING('💡 Run without --dry-run to apply these changes')
            )
