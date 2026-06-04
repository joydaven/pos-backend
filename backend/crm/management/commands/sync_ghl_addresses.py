"""
Management command to bulk-sync home addresses from GoHighLevel for contacts
that have a ghl_contact_id but are missing their billing (home) address.

Usage:
    python manage.py sync_ghl_addresses              # Sync all missing
    python manage.py sync_ghl_addresses --dry-run     # Preview only
    python manage.py sync_ghl_addresses --all         # Re-sync ALL contacts (overwrite existing)
    python manage.py sync_ghl_addresses --limit 100   # Process only first 100
"""
import time
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from crm.models import Contact

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync home addresses from GoHighLevel for contacts missing address data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be updated without making changes',
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Re-sync ALL contacts with GHL IDs (overwrite existing addresses)',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limit number of contacts to process (0 = no limit)',
        )

    def handle(self, *args, **options):
        from crm.ghl_api import get_ghl_contact_by_id, apply_ghl_contact_fields

        dry_run = options['dry_run']
        sync_all = options['all']
        limit = options['limit']

        # Build queryset
        queryset = Contact.objects.exclude(
            ghl_contact_id__isnull=True
        ).exclude(
            ghl_contact_id=''
        )

        if not sync_all:
            # Only contacts missing home address
            queryset = queryset.filter(billing_address='')

        total = queryset.count()
        if limit > 0:
            queryset = queryset[:limit]
            process_count = min(limit, total)
        else:
            process_count = total

        self.stdout.write(f"\n{'[DRY RUN] ' if dry_run else ''}Syncing GHL addresses")
        self.stdout.write(f"  Total eligible: {total}")
        self.stdout.write(f"  Will process: {process_count}")
        self.stdout.write(f"  Mode: {'ALL (overwrite)' if sync_all else 'Missing only'}\n")

        updated = 0
        skipped = 0
        errors = 0
        no_address_in_ghl = 0

        for i, contact in enumerate(queryset.iterator(), 1):
            try:
                self.stdout.write(
                    f"[{i}/{process_count}] {contact.email} (GHL: {contact.ghl_contact_id})...",
                    ending=' '
                )

                # Fetch full contact from GHL by ID (direct, no search)
                ghl_data = get_ghl_contact_by_id(contact.ghl_contact_id)

                if not ghl_data:
                    self.stdout.write(self.style.WARNING('GHL contact not found'))
                    skipped += 1
                    continue

                # Check if GHL has address data (filter out literal 'undefined' strings)
                def clean_val(v):
                    v = (v or '').strip()
                    return '' if v.lower() in ('undefined', 'null', 'none', '\u200e') else v

                ghl_address = clean_val(ghl_data.get('address1') or ghl_data.get('address'))
                ghl_city = clean_val(ghl_data.get('city'))
                ghl_state = clean_val(ghl_data.get('state'))
                ghl_postal = clean_val(ghl_data.get('postalCode'))

                if not any([ghl_address, ghl_city, ghl_state, ghl_postal]):
                    self.stdout.write(self.style.WARNING('No address in GHL'))
                    no_address_in_ghl += 1
                    continue

                if dry_run:
                    self.stdout.write(self.style.SUCCESS(
                        f'Would set: {ghl_address}, {ghl_city}, {ghl_state} {ghl_postal}'
                    ))
                    updated += 1
                else:
                    # Apply GHL fields (address + any other profile fields)
                    changed_fields = apply_ghl_contact_fields(contact, ghl_data, save=True)
                    if changed_fields:
                        self.stdout.write(self.style.SUCCESS(f'Updated: {changed_fields}'))
                        updated += 1
                    else:
                        self.stdout.write('No changes needed')
                        skipped += 1

                # Rate limit: GHL API has limits, be gentle
                # ~2 requests/sec to stay well under typical 100/min limits
                time.sleep(0.5)

            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error: {e}'))
                errors += 1
                # Don't stop on individual errors
                continue

        self.stdout.write(f"\n{'[DRY RUN] ' if dry_run else ''}Summary:")
        self.stdout.write(f"  Updated: {updated}")
        self.stdout.write(f"  Skipped (no changes / not found): {skipped}")
        self.stdout.write(f"  No address in GHL: {no_address_in_ghl}")
        self.stdout.write(f"  Errors: {errors}")
        self.stdout.write(f"  Total processed: {updated + skipped + no_address_in_ghl + errors}")
