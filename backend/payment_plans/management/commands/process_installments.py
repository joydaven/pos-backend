"""
Management command to process scheduled payment plan installments.

Runs daily (recommended: cron at 00:05 local time). It will:
  1. Mark overdue — any scheduled/pending installment whose due_date < today
     and has no card on file gets status='overdue'.
  2. Charge due — attempt CIM charge for scheduled installments due today
     (or past-due with a card on file).
  3. Retry failed — re-attempt failed installments (up to 3 retries) that
     still have a card on file.

Usage:
    python manage.py process_installments
    python manage.py process_installments --dry-run
"""
import logging
from datetime import date

from django.core.management.base import BaseCommand
from django.utils import timezone

from payment_plans.models import PaymentPlan, PaymentPlanInstallment

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


class Command(BaseCommand):
    help = 'Process scheduled payment plan installments (charge due, retry failed, mark overdue)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually charging or updating',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        today = date.today()

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"Payment Plan Installment Processor — {today}")
        self.stdout.write(f"{'='*60}\n")

        # ── Step 1: Mark overdue ─────────────────────────────────────
        overdue_count = self._mark_overdue(today, dry_run)

        # ── Step 2: Charge due installments (scheduled, due today or past) ──
        charge_success, charge_fail, charge_skip = self._charge_due(today, dry_run)

        # ── Step 3: Retry failed installments with retries remaining ──
        retry_success, retry_fail = self._retry_failed(today, dry_run)

        # ── Summary ──────────────────────────────────────────────────
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(self.style.SUCCESS("SUMMARY"))
        self.stdout.write(f"  Marked overdue:    {overdue_count}")
        self.stdout.write(f"  Charges attempted: {charge_success + charge_fail} ({charge_success} ok, {charge_fail} failed, {charge_skip} skipped)")
        self.stdout.write(f"  Retries attempted: {retry_success + retry_fail} ({retry_success} ok, {retry_fail} failed)")
        self.stdout.write(f"{'='*60}\n")

    # ─── Mark overdue ────────────────────────────────────────────────
    def _mark_overdue(self, today, dry_run):
        """
        Mark installments as overdue when:
        - status is scheduled/pending
        - due_date is in the past (before today)
        - plan has NO card on file (otherwise we'll try to charge them)
        """
        self.stdout.write("── Step 1: Marking overdue installments ──")

        # Find past-due installments on plans without a card
        past_due = PaymentPlanInstallment.objects.filter(
            status__in=['scheduled', 'pending'],
            due_date__lt=today,
            plan__status='active',
        ).select_related('plan')

        overdue_count = 0
        for inst in past_due:
            pm = inst.plan.payment_method
            has_card = pm and pm.get('customer_profile_id') and pm.get('payment_profile_id')

            if not has_card:
                if dry_run:
                    self.stdout.write(self.style.NOTICE(
                        f"  DRY RUN — Would mark overdue: [{inst.plan.plan_number}] "
                        f"#{inst.installment_number} ${inst.amount} due {inst.due_date}"
                    ))
                else:
                    inst.status = 'overdue'
                    inst.save(update_fields=['status', 'updated_at'])
                    self.stdout.write(self.style.WARNING(
                        f"  OVERDUE: [{inst.plan.plan_number}] "
                        f"#{inst.installment_number} ${inst.amount} due {inst.due_date} — no card on file"
                    ))
                overdue_count += 1

        if overdue_count == 0:
            self.stdout.write("  No installments to mark overdue.")
        return overdue_count

    # ─── Charge due installments ─────────────────────────────────────
    def _charge_due(self, today, dry_run):
        """Charge scheduled installments that are due today or past-due (with a card)."""
        self.stdout.write("\n── Step 2: Charging due installments ──")

        due_installments = PaymentPlanInstallment.objects.filter(
            status='scheduled',
            due_date__lte=today,
            plan__status='active',
        ).select_related('plan', 'plan__contact')

        count = due_installments.count()
        if count == 0:
            self.stdout.write("  No scheduled installments due.")
            return 0, 0, 0

        self.stdout.write(f"  Found {count} installment(s) to process.")
        return self._process_installments(due_installments, dry_run)

    # ─── Retry failed installments ───────────────────────────────────
    def _retry_failed(self, today, dry_run):
        """Retry failed installments that haven't exceeded MAX_RETRIES."""
        self.stdout.write("\n── Step 3: Retrying failed installments ──")

        failed_installments = PaymentPlanInstallment.objects.filter(
            status='failed',
            retry_count__lt=MAX_RETRIES,
            plan__status='active',
        ).select_related('plan', 'plan__contact')

        count = failed_installments.count()
        if count == 0:
            self.stdout.write("  No failed installments to retry.")
            return 0, 0

        self.stdout.write(f"  Found {count} failed installment(s) to retry.")
        success, fail, _ = self._process_installments(failed_installments, dry_run)

        # Mark installments that have exhausted retries as overdue
        exhausted = PaymentPlanInstallment.objects.filter(
            status='failed',
            retry_count__gte=MAX_RETRIES,
            plan__status='active',
        )
        for inst in exhausted:
            if not dry_run:
                inst.status = 'overdue'
                inst.failure_reason = f"{inst.failure_reason}\nMax retries ({MAX_RETRIES}) exhausted."
                inst.save(update_fields=['status', 'failure_reason', 'updated_at'])
                self.stdout.write(self.style.WARNING(
                    f"  OVERDUE (max retries): [{inst.plan.plan_number}] #{inst.installment_number}"
                ))

        return success, fail

    # ─── Shared charge logic ─────────────────────────────────────────
    def _process_installments(self, installments, dry_run):
        success_count = 0
        fail_count = 0
        skip_count = 0

        for inst in installments:
            plan = inst.plan

            # Per-installment card override takes priority, then plan-level card
            inst_card = inst.card_override if inst.card_override and inst.card_override.get('customer_profile_id') else None
            plan_card = plan.payment_method if plan.payment_method and plan.payment_method.get('customer_profile_id') else None
            pm = inst_card or plan_card

            card_source = 'installment override' if inst_card else 'plan default'

            self.stdout.write(
                f"  [{plan.plan_number}] Installment {inst.installment_number}: "
                f"${inst.amount} due {inst.due_date}"
            )

            if not pm or not pm.get('customer_profile_id') or not pm.get('payment_profile_id'):
                self.stdout.write(self.style.WARNING(
                    f"    SKIP — No saved card on plan or installment {plan.plan_number}"
                ))
                skip_count += 1
                continue

            if dry_run:
                self.stdout.write(self.style.NOTICE(
                    f"    DRY RUN — Would charge ${inst.amount} to "
                    f"{pm.get('brand', '?')} ****{pm.get('last4', '????')} ({card_source})"
                ))
                success_count += 1
                continue

            try:
                from payments.authorize_net import AuthorizeNetGateway

                inst.status = 'processing'
                inst.save(update_fields=['status', 'updated_at'])

                self.stdout.write(
                    f"    Charging {pm.get('brand', '?')} ****{pm.get('last4', '????')} ({card_source})"
                )

                result = AuthorizeNetGateway.charge_customer_profile(
                    customer_profile_id=pm['customer_profile_id'],
                    payment_profile_id=pm['payment_profile_id'],
                    amount=float(inst.amount),
                    order_id=f"{plan.plan_number}-{inst.installment_number}",
                )

                if result.get('status') == 'approved':
                    inst.mark_paid(
                        amount=inst.amount,
                        transaction_id=result.get('transaction_id', ''),
                        payment_method=pm,
                    )
                    success_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"    SUCCESS — Transaction {result.get('transaction_id', 'N/A')}"
                    ))

                    # Send receipt email
                    try:
                        email = None
                        if plan.contact and plan.contact.email:
                            email = plan.contact.email
                        if email:
                            from payment_plans.receipts import send_installment_receipt
                            send_installment_receipt(plan, inst, email)
                            self.stdout.write(f"    Receipt sent to {email}")
                    except Exception as e:
                        logger.error(f"Failed to send receipt for {plan.plan_number} installment {inst.installment_number}: {e}")

                else:
                    # If installment-level card failed, try plan-level card as fallback
                    if inst_card and plan_card and inst_card != plan_card:
                        self.stdout.write(self.style.WARNING(
                            f"    Installment card failed, trying plan default card..."
                        ))
                        fallback_result = AuthorizeNetGateway.charge_customer_profile(
                            customer_profile_id=plan_card['customer_profile_id'],
                            payment_profile_id=plan_card['payment_profile_id'],
                            amount=float(inst.amount),
                            order_id=f"{plan.plan_number}-{inst.installment_number}",
                        )
                        if fallback_result.get('status') == 'approved':
                            inst.mark_paid(
                                amount=inst.amount,
                                transaction_id=fallback_result.get('transaction_id', ''),
                                payment_method=plan_card,
                            )
                            success_count += 1
                            self.stdout.write(self.style.SUCCESS(
                                f"    SUCCESS (fallback card) — Transaction {fallback_result.get('transaction_id', 'N/A')}"
                            ))
                            try:
                                email = plan.contact.email if plan.contact and plan.contact.email else None
                                if email:
                                    from payment_plans.receipts import send_installment_receipt
                                    send_installment_receipt(plan, inst, email)
                                    self.stdout.write(f"    Receipt sent to {email}")
                            except Exception as e:
                                logger.error(f"Failed to send receipt: {e}")
                            continue

                    inst.status = 'failed'
                    inst.failure_reason = result.get('message', 'Charge failed')
                    inst.retry_count += 1
                    inst.save()
                    fail_count += 1
                    self.stdout.write(self.style.ERROR(
                        f"    FAILED — {inst.failure_reason}"
                    ))

            except Exception as e:
                inst.status = 'failed'
                inst.failure_reason = str(e)
                inst.retry_count += 1
                inst.save()
                fail_count += 1
                logger.error(f"Error processing installment {inst.id}: {e}")
                self.stdout.write(self.style.ERROR(f"    ERROR — {e}"))

        return success_count, fail_count, skip_count
