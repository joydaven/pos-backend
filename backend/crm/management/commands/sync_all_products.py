"""
Quick alias command for comprehensive product sync

Usage:
    python manage.py sync_all_products
    python manage.py sync_all_products --dry-run
    python manage.py sync_all_products --verbose
"""

from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
    help = 'Quick alias for comprehensive product sync (subscription + pricing)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed logging output'
        )

    def handle(self, *args, **options):
        """Alias to comprehensive sync command"""
        
        # Prepare arguments for the main command
        sync_args = []
        sync_options = {
            'dry_run': options.get('dry_run', False),
            'verbose': options.get('verbose', False),
            'batch_size': 50
        }
        
        self.stdout.write("🚀 Running comprehensive product sync (subscription + pricing)...")
        
        # Call the main comprehensive sync command
        call_command('sync_products_comprehensive', *sync_args, **sync_options)
