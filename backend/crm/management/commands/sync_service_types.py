"""
Django management command to sync service types from Google Sheets or CSV
into the woo_service_types PostgreSQL table.

Usage:
    python manage.py sync_service_types --csv /path/to/file.csv
    python manage.py sync_service_types --sheet SHEET_ID
    python manage.py sync_service_types --dry-run --csv /path/to/file.csv
    python manage.py sync_service_types --delete --csv /path/to/file.csv
"""

import re
import csv
from typing import Tuple, Optional, List
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from crm.models import WooServiceTypes


class Command(BaseCommand):
    help = 'Sync service types from Google Sheets or CSV into woo_service_types table'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            type=str,
            help='Path to CSV file with service types data'
        )
        parser.add_argument(
            '--sheet',
            type=str,
            help='Google Sheets ID to import from (requires credentials)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be imported without saving to database'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed processing information'
        )
        parser.add_argument(
            '--delete',
            action='store_true',
            help='Delete service types matching names in CSV (requires confirmation)'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        verbose = options.get('verbose', False)
        delete_mode = options.get('delete', False)
        csv_path = options.get('csv')
        sheet_id = options.get('sheet')

        if not csv_path and not sheet_id:
            self.stdout.write(self.style.ERROR(
                'You must provide either --csv or --sheet option'
            ))
            return

        # Get data from source
        if csv_path:
            rows = self.read_from_csv(csv_path)
        else:
            rows = self.read_from_google_sheets(sheet_id)

        if not rows:
            self.stdout.write(self.style.ERROR('No data found to import'))
            return

        # DELETE MODE
        if delete_mode:
            self.handle_delete_mode(rows, dry_run, verbose)
            return

        # IMPORT MODE
        self.stdout.write(f'\nProcessing {len(rows)} service type entries...\n')
        
        stats = {
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0
        }

        with transaction.atomic():
            for idx, row in enumerate(rows, 1):
                try:
                    result = self.process_service_type(
                        row,
                        dry_run=dry_run,
                        verbose=verbose,
                        index=idx
                    )
                    stats[result] += 1
                except Exception as e:
                    stats['errors'] += 1
                    self.stdout.write(self.style.ERROR(
                        f'Error processing row {idx}: {str(e)}'
                    ))

            if dry_run:
                self.stdout.write(self.style.WARNING(
                    '\n⚠️  DRY RUN - No changes were saved to database\n'
                ))

        # Print summary
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('SYNC COMPLETED'))
        self.stdout.write('='*60)
        self.stdout.write(f'✅ Created:  {stats["created"]}')
        self.stdout.write(f'🔄 Updated:  {stats["updated"]}')
        self.stdout.write(f'⏭️  Skipped:  {stats["skipped"]}')
        self.stdout.write(f'❌ Errors:   {stats["errors"]}')
        self.stdout.write('='*60 + '\n')

    def read_from_csv(self, csv_path: str) -> List[dict]:
        """Read service types from CSV file"""
        rows = []
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Expecting columns: post_title, _sku
                    if 'post_title' in row:
                        rows.append({
                            'name': row['post_title'],
                            'sku': row.get('_sku', '')
                        })
            self.stdout.write(self.style.SUCCESS(
                f'✓ Read {len(rows)} rows from CSV: {csv_path}'
            ))
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(
                f'CSV file not found: {csv_path}'
            ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f'Error reading CSV: {str(e)}'
            ))
        return rows

    def read_from_google_sheets(self, sheet_id: str) -> List[dict]:
        """Read service types from Google Sheets"""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            
            # You'll need to add your credentials file path
            SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']
            SERVICE_ACCOUNT_FILE = 'path/to/credentials.json'
            
            credentials = service_account.Credentials.from_service_account_file(
                SERVICE_ACCOUNT_FILE, scopes=SCOPES
            )
            service = build('sheets', 'v4', credentials=credentials)
            
            # Read data from sheet
            sheet = service.spreadsheets()
            result = sheet.values().get(
                spreadsheetId=sheet_id,
                range='A:B'  # Columns A (post_title) and B (_sku)
            ).execute()
            
            values = result.get('values', [])
            
            if not values:
                return []
            
            # Skip header row
            rows = []
            for row in values[1:]:
                if len(row) >= 1:
                    rows.append({
                        'name': row[0],
                        'sku': row[1] if len(row) > 1 else ''
                    })
            
            self.stdout.write(self.style.SUCCESS(
                f'✓ Read {len(rows)} rows from Google Sheets'
            ))
            return rows
            
        except ImportError:
            self.stdout.write(self.style.ERROR(
                'Google Sheets support requires: pip install google-auth google-api-python-client'
            ))
            return []
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f'Error reading Google Sheets: {str(e)}'
            ))
            return []

    def parse_service_name(self, raw_name: str) -> Tuple[str, Optional[List[int]]]:
        """
        Parse service name and extract base name and series array
        
        Examples:
            "LAB: Baseline/Annual" -> ("LAB: Baseline/Annual", None)
            "LAB: Baseline/Annual - 1 series" -> ("LAB: Baseline/Annual", [1])
            "LAB: Baseline/Annual - Single" -> ("LAB: Baseline/Annual", [1])
            "LAB: Cancer Panel - 3 series" -> ("LAB: Cancer Panel", [3])
        """
        if " - " not in raw_name:
            return raw_name.strip(), None

        base, suffix = raw_name.split(" - ", 1)
        suffix = suffix.lower().strip()

        # Handle "Single" -> [1]
        if suffix == "single":
            return base.strip(), [1]

        # Handle "X series" -> [X]
        match = re.match(r"(\d+)\s+series", suffix)
        if match:
            return base.strip(), [int(match.group(1))]

        # Fallback - couldn't parse series
        return base.strip(), None

    def process_service_type(
        self,
        row: dict,
        dry_run: bool = False,
        verbose: bool = False,
        index: int = 0
    ) -> str:
        """
        Process a single service type entry
        
        Returns: 'created', 'updated', or 'skipped'
        """
        raw_name = row.get('name', '').strip()
        sku = row.get('sku', '').strip()

        if not raw_name:
            if verbose:
                self.stdout.write(f'  [{index}] Skipped - empty name')
            return 'skipped'

        # Parse name and series
        base_name, series = self.parse_service_name(raw_name)

        if verbose or dry_run:
            series_display = series if series else 'None'
            self.stdout.write(
                f'  [{index}] "{raw_name}" -> base: "{base_name}", series: {series_display}'
            )

        if dry_run:
            # Check if it exists
            existing = WooServiceTypes.objects.filter(name=base_name).first()
            if existing:
                return 'updated'
            return 'created'

        # Create or update the service type
        service_type, created = WooServiceTypes.objects.update_or_create(
            name=base_name,
            defaults={
                'series': series,
                'updated_at': timezone.now(),
            }
        )

        if verbose:
            action = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(
                f'    ✓ {action}: {service_type.name} (series: {service_type.series})'
            ))

        return 'created' if created else 'updated'

    def handle_delete_mode(self, rows: List[dict], dry_run: bool, verbose: bool):
        """
        Delete mode: Remove service types matching names in CSV
        """
        # Parse all base names from CSV
        base_names = set()
        for row in rows:
            raw_name = row.get('name', '').strip()
            if raw_name:
                base_name, _ = self.parse_service_name(raw_name)
                base_names.add(base_name)

        if not base_names:
            self.stdout.write(self.style.ERROR('No service names found to delete'))
            return

        # Find matching records in database
        matching_records = WooServiceTypes.objects.filter(name__in=base_names)
        count = matching_records.count()

        if count == 0:
            self.stdout.write(self.style.WARNING(
                f'No matching service types found in database for deletion'
            ))
            return

        # Show what will be deleted
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.WARNING('DELETE MODE'))
        self.stdout.write('='*60)
        self.stdout.write(f'Found {count} service type(s) to delete:\n')
        
        for record in matching_records:
            series_display = record.series if record.series else 'None'
            self.stdout.write(
                f'  • {record.name} (series: {series_display}, id: {record.id})'
            )

        if dry_run:
            self.stdout.write('\n' + self.style.WARNING(
                '⚠️  DRY RUN - No records were deleted\n'
            ))
            self.stdout.write('='*60)
            self.stdout.write(f'Would delete: {count} record(s)')
            self.stdout.write('='*60 + '\n')
            return

        # Confirmation prompt (only in non-dry-run mode)
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.ERROR('⚠️  WARNING: This will permanently delete these records!'))
        self.stdout.write('='*60)
        
        confirm = input('\nType "DELETE" to confirm: ')
        
        if confirm != 'DELETE':
            self.stdout.write(self.style.WARNING('\nDeletion cancelled.'))
            return

        # Perform deletion
        deleted_count, _ = matching_records.delete()
        
        self.stdout.write('\n' + '='*60)
        self.stdout.write(self.style.SUCCESS('DELETION COMPLETED'))
        self.stdout.write('='*60)
        self.stdout.write(self.style.SUCCESS(f'✓ Deleted {deleted_count} service type(s)'))
        self.stdout.write('='*60 + '\n')
        
        if verbose:
            self.stdout.write('\nDeleted service types:')
            for name in base_names:
                self.stdout.write(f'  • {name}')
