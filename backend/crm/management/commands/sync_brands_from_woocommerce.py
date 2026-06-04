"""
Management command to sync brands directly from WooCommerce product attributes.

This command fetches all brand terms from WooCommerce's product attributes API
and populates the Brand table, ensuring all WooCommerce brands are available
in the POS filter even if their products haven't been synced yet.

Usage:
    python manage.py sync_brands_from_woocommerce
    python manage.py sync_brands_from_woocommerce --verbose
    python manage.py sync_brands_from_woocommerce --dry-run
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from crm.models import Brand
from crm.woocommerce import WooCommerceAPI
import logging
from collections import defaultdict


class Command(BaseCommand):
    help = 'Sync brands directly from WooCommerce product attributes API'

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
        """Execute the WooCommerce brand sync command."""
        
        # Set up logging
        if options['verbose']:
            logging.basicConfig(level=logging.INFO)
        
        self.stdout.write(
            self.style.SUCCESS('🔄 Starting brand sync from WooCommerce API...')
        )
        
        try:
            if options['dry_run']:
                self.stdout.write(
                    self.style.WARNING('🧪 DRY RUN MODE - No changes will be made')
                )
                stats = self._dry_run_sync()
            else:
                with transaction.atomic():
                    stats = self._sync_brands_from_woocommerce()
            
            # Display results
            self._display_sync_results(stats, options['dry_run'])
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ WooCommerce brand sync failed: {str(e)}')
            )
            raise CommandError(f'WooCommerce brand sync failed: {str(e)}')

    def _sync_brands_from_woocommerce(self):
        """
        Sync brands from WooCommerce product attributes API.
        
        Returns:
            dict: Statistics about the sync operation
        """
        logger = logging.getLogger(__name__)
        
        try:
            # Initialize WooCommerce API
            wc_api = WooCommerceAPI()
            
            # Get all brands from WooCommerce
            wc_brands = wc_api.get_all_brands_from_woocommerce()
            
            if not wc_brands:
                logger.warning("No brands found in WooCommerce")
                return {
                    'total_brands_found': 0,
                    'brands_created': 0,
                    'brands_updated': 0,
                    'brands_deactivated': 0,
                    'sync_completed_at': timezone.now()
                }
            
            # Track statistics
            sync_time = timezone.now()
            brands_created = 0
            brands_updated = 0
            brands_deactivated = 0
            
            # Create or update brands from WooCommerce
            wc_brand_names = set()
            for brand_name in wc_brands:
                if not brand_name or not brand_name.strip():
                    continue
                    
                brand_name = brand_name.strip()
                wc_brand_names.add(brand_name)
                slug = slugify(brand_name)
                
                brand, created = Brand.objects.get_or_create(
                    name=brand_name,
                    defaults={
                        'slug': slug,
                        'product_count': 0,  # Will be updated by regular product sync
                        'is_active': True,
                        'last_synced_at': sync_time
                    }
                )
                
                if created:
                    brands_created += 1
                    logger.info(f"Created new brand from WooCommerce: {brand_name}")
                else:
                    # Update existing brand
                    brand.is_active = True
                    brand.last_synced_at = sync_time
                    brand.save()
                    brands_updated += 1
            
            # Optionally deactivate brands that no longer exist in WooCommerce
            # (commented out to preserve local brands that might not be in WooCommerce yet)
            # for brand in Brand.objects.filter(is_active=True):
            #     if brand.name not in wc_brand_names:
            #         brand.is_active = False
            #         brand.last_synced_at = sync_time
            #         brand.save()
            #         brands_deactivated += 1
            #         logger.info(f"Deactivated brand not found in WooCommerce: {brand.name}")
            
            stats = {
                'total_brands_found': len(wc_brands),
                'brands_created': brands_created,
                'brands_updated': brands_updated,
                'brands_deactivated': brands_deactivated,
                'sync_completed_at': sync_time,
                'wc_brands': wc_brands
            }
            
            logger.info(f"WooCommerce brand sync completed: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Error during WooCommerce brand sync: {str(e)}")
            raise

    def _dry_run_sync(self):
        """
        Perform a dry run to show what would be synced from WooCommerce.
        
        Returns:
            dict: Statistics about what would be synced
        """
        try:
            # Initialize WooCommerce API
            wc_api = WooCommerceAPI()
            
            # Get all brands from WooCommerce
            wc_brands = wc_api.get_all_brands_from_woocommerce()
            
            if not wc_brands:
                return {
                    'total_brands_found': 0,
                    'brands_created': 0,
                    'brands_updated': 0,
                    'brands_deactivated': 0,
                    'new_brands': [],
                    'updated_brands': [],
                    'deactivated_brands': []
                }
            
            # Check existing brands
            existing_brands = set(Brand.objects.values_list('name', flat=True))
            wc_brand_names = set(brand.strip() for brand in wc_brands if brand and brand.strip())
            
            new_brands = wc_brand_names - existing_brands
            updated_brands = wc_brand_names & existing_brands
            # Don't deactivate brands in dry run for WooCommerce sync
            deactivated_brands = set()
            
            return {
                'total_brands_found': len(wc_brands),
                'brands_created': len(new_brands),
                'brands_updated': len(updated_brands),
                'brands_deactivated': len(deactivated_brands),
                'new_brands': sorted(new_brands),
                'updated_brands': sorted(updated_brands),
                'deactivated_brands': sorted(deactivated_brands),
                'wc_brands': wc_brands
            }
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error during dry run: {str(e)}')
            )
            raise

    def _display_sync_results(self, stats, is_dry_run=False):
        """
        Display the results of the sync operation.
        
        Args:
            stats (dict): Statistics from the sync operation
            is_dry_run (bool): Whether this was a dry run
        """
        action_verb = "Would be" if is_dry_run else "Were"
        
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('📊 WooCommerce Brand Sync Results:'))
        self.stdout.write(f'  • Total brands found in WooCommerce: {stats["total_brands_found"]}')
        self.stdout.write(f'  • Brands {action_verb.lower()} created: {stats["brands_created"]}')
        self.stdout.write(f'  • Brands {action_verb.lower()} updated: {stats["brands_updated"]}')
        self.stdout.write(f'  • Brands {action_verb.lower()} deactivated: {stats["brands_deactivated"]}')
        
        if is_dry_run and 'new_brands' in stats:
            if stats['new_brands']:
                self.stdout.write('')
                self.stdout.write(self.style.WARNING('🆕 New brands that would be created from WooCommerce:'))
                for brand in stats['new_brands'][:20]:  # Show first 20
                    self.stdout.write(f'  • {brand}')
                if len(stats['new_brands']) > 20:
                    self.stdout.write(f'  ... and {len(stats["new_brands"]) - 20} more')
            
            if stats.get('wc_brands'):
                self.stdout.write('')
                self.stdout.write(self.style.SUCCESS('🏷️  All WooCommerce brands found:'))
                for brand in sorted(stats['wc_brands'])[:30]:  # Show first 30
                    self.stdout.write(f'  • {brand}')
                if len(stats['wc_brands']) > 30:
                    self.stdout.write(f'  ... and {len(stats["wc_brands"]) - 30} more')
        
        if not is_dry_run:
            self.stdout.write('')
            self.stdout.write(
                self.style.SUCCESS('✅ WooCommerce brand sync completed successfully!')
            )
            if 'sync_completed_at' in stats:
                self.stdout.write(f'   Completed at: {stats["sync_completed_at"]}')
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING('💡 Run "python manage.py sync_brands" to update product counts')
            )
        else:
            self.stdout.write('')
            self.stdout.write(
                self.style.WARNING('💡 Run without --dry-run to apply these changes')
            )
