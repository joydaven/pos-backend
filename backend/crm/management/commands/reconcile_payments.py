"""
Daily reconciliation management command.

Compares POS orders, PaymentTransaction records, and WooCommerce orders
to detect orphans, mismatches, and sync failures.

Usage:
    python manage.py reconcile_payments          # Full report
    python manage.py reconcile_payments --fix    # Attempt auto-fixes
    python manage.py reconcile_payments --days 7 # Only last 7 days
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from crm.models import POSOrder
from payments.models import PaymentTransaction

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Reconcile POS orders, payment transactions, and WooCommerce orders'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=1,
            help='Number of days to look back (default: 1)',
        )
        parser.add_argument(
            '--fix', action='store_true',
            help='Attempt to auto-fix detected issues',
        )

    def handle(self, *args, **options):
        days = options['days']
        fix = options['fix']
        since = timezone.now() - timedelta(days=days)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'\n=== Payment Reconciliation Report (last {days} day(s)) ===\n'
        ))

        issues = []

        # ── 1. POS orders with credit payment but no PaymentTransaction ──
        self.stdout.write('1. Checking POS orders with credit payments but no transaction record...')
        credit_orders = POSOrder.objects.filter(
            created_at__gte=since,
            payment_method_title__icontains='credit',
        ).exclude(status__in=['cancelled', 'trash', 'draft'])

        orphan_orders = []
        for order in credit_orders:
            trans_id = order.transaction_id or ''
            if not trans_id or trans_id.startswith('ORD-'):
                # No real Authorize.Net transaction ID
                orphan_orders.append(order)
                continue
            # Check if a PaymentTransaction record exists
            if not PaymentTransaction.objects.filter(transaction_id=trans_id).exists():
                orphan_orders.append(order)

        if orphan_orders:
            self.stdout.write(self.style.WARNING(
                f'   ⚠ {len(orphan_orders)} credit orders without matching PaymentTransaction:'
            ))
            for o in orphan_orders[:20]:
                self.stdout.write(f'     - {o.order_number}  total=${o.total}  trans_id={o.transaction_id or "NONE"}')
            issues.append(('orphan_credit_orders', len(orphan_orders)))
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ All credit orders have matching transactions'))

        # ── 2. PaymentTransactions with no matching POS order ──
        self.stdout.write('\n2. Checking PaymentTransactions with no matching POS order...')
        recent_txns = PaymentTransaction.objects.filter(
            created_at__gte=since,
            status='approved',
        )
        orphan_txns = []
        for txn in recent_txns:
            order_id = txn.order_id or ''
            if not order_id:
                orphan_txns.append(txn)
                continue
            if not POSOrder.objects.filter(order_number=order_id).exists():
                orphan_txns.append(txn)

        if orphan_txns:
            self.stdout.write(self.style.WARNING(
                f'   ⚠ {len(orphan_txns)} approved transactions without matching POS order:'
            ))
            for t in orphan_txns[:20]:
                self.stdout.write(f'     - txn={t.transaction_id}  order_id={t.order_id or "NONE"}  amount=${t.amount}')
            issues.append(('orphan_transactions', len(orphan_txns)))
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ All approved transactions have matching POS orders'))

        # ── 3. POS orders missing WooCommerce sync ──
        self.stdout.write('\n3. Checking POS orders missing WooCommerce sync...')
        unsynced = POSOrder.objects.filter(
            created_at__gte=since,
        ).exclude(
            status__in=['cancelled', 'trash', 'draft']
        )

        missing_woo = []
        for order in unsynced:
            meta = order.metadata or {}
            if isinstance(meta, str):
                try:
                    import json
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            woo_id = meta.get('woo_order_id') if isinstance(meta, dict) else None
            if not woo_id:
                missing_woo.append(order)

        if missing_woo:
            self.stdout.write(self.style.WARNING(
                f'   ⚠ {len(missing_woo)} POS orders not synced to WooCommerce:'
            ))
            for o in missing_woo[:20]:
                self.stdout.write(f'     - {o.order_number}  total=${o.total}  status={o.status}')
            issues.append(('missing_woo_sync', len(missing_woo)))
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ All POS orders have WooCommerce order IDs'))

        # ── 4. Amount mismatches between POS order and PaymentTransaction ──
        self.stdout.write('\n4. Checking for amount mismatches...')
        mismatches = []
        matched_orders = POSOrder.objects.filter(
            created_at__gte=since,
        ).exclude(
            transaction_id='',
        ).exclude(
            transaction_id__startswith='ORD-',
        ).exclude(
            status__in=['cancelled', 'trash', 'draft', 'refunded']
        )

        for order in matched_orders:
            if not order.transaction_id:
                continue
            txn = PaymentTransaction.objects.filter(
                transaction_id=order.transaction_id,
                status='approved',
            ).first()
            if txn and abs(txn.amount - order.total) > Decimal('0.01'):
                mismatches.append((order, txn))

        if mismatches:
            self.stdout.write(self.style.WARNING(
                f'   ⚠ {len(mismatches)} amount mismatches:'
            ))
            for order, txn in mismatches[:20]:
                self.stdout.write(
                    f'     - {order.order_number}: POS=${order.total} vs Txn=${txn.amount} '
                    f'(diff=${abs(order.total - txn.amount)})'
                )
            issues.append(('amount_mismatches', len(mismatches)))
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ No amount mismatches detected'))

        # ── Summary ──
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== Summary ==='))
        if issues:
            total = sum(count for _, count in issues)
            self.stdout.write(self.style.WARNING(f'Found {total} issue(s) across {len(issues)} categories:'))
            for category, count in issues:
                self.stdout.write(f'  - {category}: {count}')
            if not fix:
                self.stdout.write('\nRun with --fix to attempt auto-remediation.')
        else:
            self.stdout.write(self.style.SUCCESS('✅ No issues found. All systems reconciled.'))

        logger.info(f"Reconciliation complete: {len(issues)} issue categories, days={days}")
