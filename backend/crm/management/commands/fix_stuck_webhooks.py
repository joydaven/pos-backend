"""
Management command to fix webhook records stuck in 'processing' status.

This command safely identifies and updates webhook records that have been
stuck in 'processing' status for more than a specified time period.
"""

import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from crm.models import WebhookLog

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fix webhook records stuck in processing status'

    def add_arguments(self, parser):
        parser.add_argument(
            '--minutes',
            type=int,
            default=30,
            help='Consider webhooks stuck if processing for more than this many minutes (default: 30)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip confirmation prompt'
        )

    def handle(self, *args, **options):
        minutes_threshold = options['minutes']
        dry_run = options['dry_run']
        force = options['force']
        
        # Calculate the cutoff time
        cutoff_time = timezone.now() - timedelta(minutes=minutes_threshold)
        
        # Find stuck webhooks
        stuck_webhooks = WebhookLog.objects.filter(
            status='processing',
            received_at__lt=cutoff_time
        ).order_by('received_at')
        
        count = stuck_webhooks.count()
        
        if count == 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ No webhook records found stuck in processing status for more than {minutes_threshold} minutes."
                )
            )
            return
        
        self.stdout.write(
            self.style.WARNING(
                f"Found {count} webhook records stuck in 'processing' status for more than {minutes_threshold} minutes:"
            )
        )
        
        # Show details of stuck webhooks
        for webhook in stuck_webhooks[:10]:  # Show first 10
            time_stuck = timezone.now() - webhook.received_at
            self.stdout.write(
                f"  - ID: {webhook.id} | Type: {webhook.webhook_type} | "
                f"Received: {webhook.received_at} | Stuck for: {time_stuck}"
            )
        
        if count > 10:
            self.stdout.write(f"  ... and {count - 10} more")
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "\n🔍 DRY RUN: No changes will be made. Use --force to actually update records."
                )
            )
            return
        
        # Confirmation prompt
        if not force:
            confirm = input(f"\nDo you want to mark these {count} webhook records as 'failed'? (y/N): ")
            if confirm.lower() not in ['y', 'yes']:
                self.stdout.write("Operation cancelled.")
                return
        
        # Update the stuck webhooks
        updated_count = 0
        failed_count = 0
        
        for webhook in stuck_webhooks:
            try:
                time_stuck = timezone.now() - webhook.received_at
                error_message = (
                    f"Webhook processing timed out after {time_stuck}. "
                    f"Marked as failed by fix_stuck_webhooks command."
                )
                
                webhook.mark_failed(error_message)
                updated_count += 1
                
                logger.info(f"Fixed stuck webhook {webhook.id} (stuck for {time_stuck})")
                
            except Exception as e:
                failed_count += 1
                logger.error(f"Failed to update webhook {webhook.id}: {str(e)}")
                self.stdout.write(
                    self.style.ERROR(f"Failed to update webhook {webhook.id}: {str(e)}")
                )
        
        # Summary
        if updated_count > 0:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✅ Successfully updated {updated_count} stuck webhook records to 'failed' status."
                )
            )
        
        if failed_count > 0:
            self.stdout.write(
                self.style.ERROR(
                    f"❌ Failed to update {failed_count} webhook records."
                )
            )
        
        # Show current status summary
        self.stdout.write("\n📊 Current webhook status summary:")
        from django.db import models
        status_counts = WebhookLog.objects.values('status').annotate(
            count=models.Count('id')
        ).order_by('status')
        
        for status_info in status_counts:
            status = status_info['status']
            count = status_info['count']
            
            if status == 'processing':
                style = self.style.WARNING
            elif status == 'failed':
                style = self.style.ERROR
            elif status == 'success':
                style = self.style.SUCCESS
            else:
                style = self.style.HTTP_INFO
                
            self.stdout.write(style(f"  {status}: {count}"))
