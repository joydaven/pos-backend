"""
Management command to clean up stale pending-payment POS orders.

Orders with status ``pending-payment`` that are older than the configured
threshold (default 24 hours) are likely the result of abandoned checkouts
or browser crashes after the order record was created but before payment
was captured.  This command marks them as ``cancelled`` so they don't
pollute order listings.

Usage:
    python manage.py cleanup_pending_orders              # dry-run by default
    python manage.py cleanup_pending_orders --apply      # actually cancel stale orders
    python manage.py cleanup_pending_orders --hours 12   # use a 12-hour threshold
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import POSOrder


class Command(BaseCommand):
    help = "Cancel POS orders stuck in pending-payment status beyond a configurable age threshold."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Age threshold in hours (default: 24). Orders older than this are candidates.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually cancel the stale orders. Without this flag the command performs a dry-run.",
        )

    def handle(self, *args, **options):
        hours = options["hours"]
        apply = options["apply"]
        cutoff = timezone.now() - timedelta(hours=hours)

        stale_qs = POSOrder.objects.filter(
            status="pending-payment",
            created_at__lt=cutoff,
        )
        count = stale_qs.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No stale pending-payment orders found."))
            return

        if apply:
            updated = stale_qs.update(status="cancelled")
            self.stdout.write(
                self.style.SUCCESS(f"Cancelled {updated} stale pending-payment order(s) older than {hours}h.")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY-RUN] Found {count} stale pending-payment order(s) older than {hours}h. "
                    "Re-run with --apply to cancel them."
                )
            )
            for order in stale_qs[:20]:
                self.stdout.write(f"  - {order.order_number}  created {order.created_at}")
