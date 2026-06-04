"""
Backfill is_active_member on Contact records by checking WooCommerce memberships API.
Run once after deploying the migration, then webhooks + lazy-update keep it current.

Usage:
    python manage.py backfill_active_members
    python manage.py backfill_active_members --dry-run
"""
import logging
import time
from django.core.management.base import BaseCommand
from crm.models import Contact
from crm.woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Backfill is_active_member flag on Contact records from WooCommerce memberships'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print what would change without writing')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        wc = WooCommerceAPI()

        contacts_with_woo = Contact.objects.filter(
            woo_customer_id__isnull=False
        ).values_list('id', 'woo_customer_id', 'email', 'first_name', 'last_name', 'is_active_member')

        total = contacts_with_woo.count()
        self.stdout.write(f"Checking {total} contacts with WooCommerce IDs...")

        set_true = []
        set_false = []
        errors = []

        for i, (pk, woo_id, email, first, last, current_flag) in enumerate(contacts_with_woo):
            name = f"{first} {last}".strip() or email
            try:
                memberships = wc.get_memberships(customer_id=woo_id) or []
                has_active = any(
                    m.get('status') in ('active', 'wcm-active')
                    for m in memberships
                )

                if has_active and not current_flag:
                    set_true.append(pk)
                    self.stdout.write(f"  [{i+1}/{total}] {name} (woo:{woo_id}) -> SET active member")
                elif not has_active and current_flag:
                    set_false.append(pk)
                    self.stdout.write(f"  [{i+1}/{total}] {name} (woo:{woo_id}) -> CLEAR active member")
                else:
                    status_label = "active" if current_flag else "not active"
                    if (i + 1) % 50 == 0:
                        self.stdout.write(f"  [{i+1}/{total}] {name} — already correct ({status_label})")

            except Exception as e:
                errors.append((name, woo_id, str(e)))
                self.stderr.write(f"  [{i+1}/{total}] ERROR {name} (woo:{woo_id}): {e}")

            # Rate limiting: WooCommerce API typically allows ~10 req/s
            if (i + 1) % 10 == 0:
                time.sleep(1)

        if not dry_run:
            if set_true:
                Contact.objects.filter(id__in=set_true).update(is_active_member=True)
            if set_false:
                Contact.objects.filter(id__in=set_false).update(is_active_member=False)

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"\n{prefix}Done. "
            f"Set active: {len(set_true)}, Cleared: {len(set_false)}, "
            f"Errors: {len(errors)}, Total checked: {total}"
        ))

        if errors:
            self.stdout.write("\nErrors:")
            for name, woo_id, err in errors:
                self.stdout.write(f"  - {name} (woo:{woo_id}): {err}")
