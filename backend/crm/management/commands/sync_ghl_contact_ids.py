"""
Django management command to sync GHL contact IDs for all contacts in the database.

This command searches for contacts in GoHighLevel by email address and updates
the local database with the corresponding GHL contact ID. This is crucial for
GHL integrations and credit service point syncing.

Usage:
    python manage.py sync_ghl_contact_ids [options]

Examples:
    # Dry run to see what would be updated
    python manage.py sync_ghl_contact_ids --dry-run

    # Process only 50 contacts
    python manage.py sync_ghl_contact_ids --limit 50

    # Process specific email
    python manage.py sync_ghl_contact_ids --email "customer@example.com"

    # Process only contacts with missing GHL IDs
    python manage.py sync_ghl_contact_ids --missing-only

    # Process only contacts with credit points (priority)
    python manage.py sync_ghl_contact_ids --priority-only

    # Force update existing GHL IDs
    python manage.py sync_ghl_contact_ids --force-update

    # Verbose output
    python manage.py sync_ghl_contact_ids --verbose
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, models
from django.utils import timezone
from crm.models import Contact, CreditServicePoints
from crm.ghl_api import search_ghl_contact_by_email, get_ghl_contact_id_by_email, apply_ghl_contact_fields
import time
import logging
from django.db.models import Q

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync GHL contact IDs for all contacts by searching GHL via email'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Maximum number of contacts to process',
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Process only specific email address',
        )
        parser.add_argument(
            '--missing-only',
            action='store_true',
            help='Only sync contacts that have no GHL contact ID',
        )
        parser.add_argument(
            '--verify-existing',
            action='store_true',
            help='Verify and update existing GHL contact IDs (check if they are current)',
        )
        parser.add_argument(
            '--priority-only',
            action='store_true',
            help='Only process contacts with credit points (priority)',
        )
        parser.add_argument(
            '--force-update',
            action='store_true',
            help='Update existing GHL contact IDs (re-sync)',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Enable verbose output',
        )
        parser.add_argument(
            '--delay',
            type=float,
            default=0.5,
            help='Delay between API requests in seconds (default: 0.5)',
        )

    def handle(self, *args, **options):
        missing_only = options['missing_only']
        priority_only = options['priority_only']
        verify_existing = options['verify_existing']
        limit = options['limit']
        delay = options['delay']
        dry_run = options['dry_run']
        self.verbosity = options['verbosity']
        self.verbose = options['verbose']

        # Setup logging level
        if self.verbose:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        operation_name = "GHL Contact ID Verification" if verify_existing else "GHL Contact ID Sync"
        self.stdout.write(
            self.style.SUCCESS(f'🔍 Starting {operation_name}')
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING('🧪 DRY RUN MODE - No changes will be saved')
            )

        self.stdout.write(f"Limit: {limit if limit else 'No limit'}")
        self.stdout.write(f"Delay: {delay}s between requests")
        self.stdout.write(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")

        # Build queryset based on options
        queryset = self.build_queryset(options)
        
        if not queryset.exists():
            self.stdout.write(
                self.style.WARNING('No contacts found matching criteria')
            )
            return

        total_contacts = queryset.count()
        action = "verify" if verify_existing else "process"
        self.stdout.write(f"Found {total_contacts} contacts to {action}")

        # Process contacts
        results = self.process_contacts(queryset, options)
        
        # Display summary
        self.display_summary(results, total_contacts, options)

    def build_queryset(self, options):
        """Build the queryset based on command options."""
        queryset = Contact.objects.all()

        # Filter by specific email
        if options['email']:
            queryset = queryset.filter(email__iexact=options['email'])
            
        # Filter by missing GHL IDs
        elif options['missing_only']:
            queryset = queryset.filter(
                ghl_contact_id__isnull=True
            ).exclude(
                ghl_contact_id__exact=''
            )
            
        # Filter by priority (contacts with credit points)
        elif options['priority_only']:
            queryset = queryset.filter(
                credit_points__points__gt=0
            ).distinct()
            
            # Also filter by missing GHL IDs unless force update
            if not options['force_update']:
                queryset = queryset.filter(
                    ghl_contact_id__isnull=True
                ).exclude(
                    ghl_contact_id__exact=''
                )
                
        # Verify existing GHL contact IDs (re-sync to check if they're current)
        elif options['verify_existing']:
            queryset = queryset.filter(
                ghl_contact_id__isnull=False
            ).exclude(ghl_contact_id__exact='').exclude(email__isnull=True).exclude(email__exact='')
            
        # Default: missing GHL IDs unless force update
        elif not options['force_update']:
            queryset = queryset.filter(
                ghl_contact_id__isnull=True
            ).exclude(
                ghl_contact_id__exact=''
            )

        # Order by priority: customers with credit points first
        queryset = queryset.annotate(
            has_credits=models.Exists(
                CreditServicePoints.objects.filter(
                    customer=models.OuterRef('pk'),
                    points__gt=0
                )
            )
        ).order_by('-has_credits', 'email')

        # Apply limit after ordering
        if options['limit']:
            queryset = queryset[:options['limit']]

        return queryset

    def update_credit_service_points_ghl_id(self, contact, old_ghl_id, new_ghl_id, dry_run=False):
        """
        Update stale contact_ghl_id values in CreditServicePoints records for this contact.
        
        Args:
            contact: Contact object
            old_ghl_id: Previous GHL ID (can be None)
            new_ghl_id: New GHL ID
            dry_run: If True, don't save changes
            
        Returns:
            int: Number of records updated
        """
        try:
            # Find CreditServicePoints records for this customer with stale GHL IDs
            # If old_ghl_id equals new_ghl_id (force-update case), find ANY record not matching new_ghl_id
            if old_ghl_id == new_ghl_id:
                stale_records = CreditServicePoints.objects.filter(
                    customer=contact
                ).exclude(
                    contact_ghl_id=new_ghl_id
                )
            else:
                # Normal case: find records with NULL or old_ghl_id
                stale_records = CreditServicePoints.objects.filter(
                    customer=contact
                ).filter(
                    Q(contact_ghl_id__isnull=True) | Q(contact_ghl_id=old_ghl_id)
                )
            
            count = stale_records.count()
            
            if count > 0:
                if self.verbose:
                    self.stdout.write(f"     Found {count} CreditServicePoints record(s) with stale GHL ID")
                
                if not dry_run:
                    updated = stale_records.update(contact_ghl_id=new_ghl_id)
                    if self.verbose:
                        self.stdout.write(f"     ✅ Updated {updated} CreditServicePoints record(s)")
                    return updated
                else:
                    if self.verbose:
                        self.stdout.write(f"     Would update {count} CreditServicePoints record(s)")
                    return count
            
            return 0
            
        except Exception as e:
            logger.error(f"Error updating CreditServicePoints for {contact.email}: {str(e)}")
            if self.verbose:
                self.stdout.write(
                    self.style.WARNING(f"     ⚠️  Failed to update CreditServicePoints: {str(e)}")
                )
            return 0

    def process_contacts(self, queryset, options):
        """Process the contacts and sync GHL contact IDs."""
        results = {
            'processed': 0,
            'found': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'error_details': []
        }

        for contact in queryset:
            try:
                result = self.process_single_contact(contact, options)
                results['processed'] += 1
                
                if result['status'] == 'found':
                    results['found'] += 1
                    if result['updated']:
                        results['updated'] += 1
                elif result['status'] == 'skipped':
                    results['skipped'] += 1
                elif result['status'] == 'error':
                    results['errors'] += 1
                    results['error_details'].append(result['error'])

                # Rate limiting
                if options['delay'] > 0:
                    time.sleep(options['delay'])

            except Exception as e:
                results['errors'] += 1
                results['processed'] += 1
                error_msg = f"Unexpected error processing {contact.email}: {str(e)}"
                results['error_details'].append(error_msg)
                
                if self.verbose:
                    self.stdout.write(
                        self.style.ERROR(f'❌ {error_msg}')
                    )

        return results

    def process_single_contact(self, contact, options):
        """Process a single contact."""
        result = {
            'status': 'unknown',
            'updated': False,
            'error': None
        }

        if self.verbose:
            self.stdout.write(f'🔍 Processing: {contact.email}')

        # Skip if contact already has GHL ID (unless force update or verification)
        if contact.ghl_contact_id and not options['force_update'] and not options['verify_existing']:
            if self.verbose:
                self.stdout.write(f"⏭️  Skipping {contact.email} - already has GHL ID: {contact.ghl_contact_id}")
            result['status'] = 'skipped'
            return result

        # For verification, we always check even if contact has GHL ID
        if options['verify_existing'] and self.verbose:
            self.stdout.write(f"🔍 Verifying GHL ID for {contact.email} (current: {contact.ghl_contact_id})")

        # Search for contact in GHL
        try:
            ghl_contact_data = search_ghl_contact_by_email(contact.email)
            
            if ghl_contact_data and 'id' in ghl_contact_data:
                ghl_contact_id = ghl_contact_data['id']
                
                # Check if this is a verification run and ID has changed
                if options['verify_existing'] and contact.ghl_contact_id:
                    if contact.ghl_contact_id != ghl_contact_id:
                        old_id = contact.ghl_contact_id
                        if not options['dry_run']:
                            contact.ghl_contact_id = ghl_contact_id
                            contact.ghl_last_sync = timezone.now()
                            contact.save()
                            self.stdout.write(f"  🔄 Updated ID: {contact.email}")
                            self.stdout.write(f"     Old: {old_id}")
                            self.stdout.write(f"     New: {ghl_contact_id}")
                            
                            # Update CreditServicePoints records with stale GHL IDs
                            self.update_credit_service_points_ghl_id(
                                contact, old_id, ghl_contact_id, dry_run=False
                            )
                        else:
                            self.stdout.write(f"  🔄 Would update ID: {contact.email}")
                            self.stdout.write(f"     Old: {contact.ghl_contact_id}")
                            self.stdout.write(f"     New: {ghl_contact_id}")
                            
                            # Show what would be updated in CreditServicePoints
                            self.update_credit_service_points_ghl_id(
                                contact, old_id, ghl_contact_id, dry_run=True
                            )
                        result['status'] = 'found'
                        result['updated'] = True
                    else:
                        # ID is still current, just update sync timestamp
                        if not options['dry_run']:
                            contact.ghl_last_sync = timezone.now()
                            contact.save()
                        self.stdout.write(f"  ✅ Verified current: {contact.email} → {ghl_contact_id}")
                        result['status'] = 'found'
                else:
                    # New GHL ID found for contact without one
                    old_id = contact.ghl_contact_id
                    if not options['dry_run']:
                        contact.ghl_contact_id = ghl_contact_id
                        contact.ghl_last_sync = timezone.now()
                        contact.save()
                        self.stdout.write(f"  ✅ Updated: {contact.email} → {ghl_contact_id}")
                        
                        # Update CreditServicePoints records with stale/missing GHL IDs
                        self.update_credit_service_points_ghl_id(
                            contact, old_id, ghl_contact_id, dry_run=False
                        )
                    else:
                        self.stdout.write(f"  ✅ Would update: {contact.email} → {ghl_contact_id}")
                        
                        # Show what would be updated in CreditServicePoints
                        self.update_credit_service_points_ghl_id(
                            contact, old_id, ghl_contact_id, dry_run=True
                        )
                    result['status'] = 'found'
                    result['updated'] = True
                    
                if isinstance(ghl_contact_data, dict):
                    contact.ghl_data = ghl_contact_data
                
                save_fields = ['ghl_contact_id', 'ghl_last_sync', 'ghl_data']
                
                # Apply GHL fields to POS contact (GHL is source of truth)
                if not options['dry_run']:
                    ghl_field_updates = apply_ghl_contact_fields(contact, ghl_contact_data, save=False)
                    if ghl_field_updates:
                        save_fields.extend(ghl_field_updates)
                        self.stdout.write(f"  📋 GHL→POS fields updated: {ghl_field_updates}")
                    
                contact.save(update_fields=save_fields)
                
            else:
                # No GHL contact found - update sync timestamp to mark as attempted
                if not options['dry_run']:
                    contact.ghl_last_sync = timezone.now()
                    contact.save()
                
                if options['verify_existing']:
                    self.stdout.write(f"  ⚠️  Contact not found in GHL (may have been deleted): {contact.email}")
                else:
                    self.stdout.write(f"  ❌ Not found: {contact.email}")
                result['status'] = 'found'  # Found = searched, but no result
                
        except Exception as e:
            error_msg = f"Error searching GHL for {contact.email}: {str(e)}"
            result['status'] = 'error'
            result['error'] = error_msg
            
            if self.verbose:
                self.stdout.write(
                    self.style.ERROR(f'❌ {error_msg}')
                )

        return result

    def display_summary(self, results, total_contacts, options):
        """Display the summary of results."""
        self.stdout.write('\n' + '='*60)
        operation_type = "verified" if options['verify_existing'] else "synced"
        self.stdout.write(self.style.SUCCESS(f'Successfully {operation_type} GHL contact IDs'))
        self.stdout.write(f"\n📊 Summary:")
        self.stdout.write(f"Total Processed: {results['processed']}/{total_contacts}")
        self.stdout.write(f"GHL IDs Found: {results['found']}")
        self.stdout.write(f"Records Updated: {results['updated']}")
        self.stdout.write(f"Errors: {results['errors']}")
        
        if results['found'] > 0:
            success_rate = (results['found'] / results['processed']) * 100
            self.stdout.write(f"Success Rate: {success_rate:.1f}%")
        
        if options['verify_existing']:
            verified_count = results['processed'] - results['updated']
            self.stdout.write(f"IDs Verified as Current: {verified_count}")
            self.stdout.write(f"IDs Updated (Changed): {results['updated']}")
        
        if options['dry_run']:
            self.stdout.write(f"\n🔍 DRY RUN - No changes were made to the database")
        
        operation_type = "verification" if options['verify_existing'] else "sync"
        self.stdout.write(f"\n✅ GHL Contact ID {operation_type} completed!")
