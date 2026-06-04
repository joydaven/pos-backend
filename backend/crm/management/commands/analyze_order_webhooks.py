"""
Management command to analyze webhook logs for a specific WooCommerce order
Shows the complete webhook history and helps identify creation vs update events
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from crm.models import WebhookLog, POSOrder
import json


class Command(BaseCommand):
    help = 'Analyze webhook logs for a specific WooCommerce order ID'

    def add_arguments(self, parser):
        parser.add_argument(
            'order_id',
            type=str,
            help='WooCommerce order ID to analyze'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show full request body for each webhook'
        )

    def handle(self, *args, **options):
        order_id = options['order_id']
        verbose = options.get('verbose', False)
        
        self.stdout.write(self.style.HTTP_INFO(f"\n{'='*80}"))
        self.stdout.write(self.style.HTTP_INFO(f"WEBHOOK ANALYSIS FOR ORDER: {order_id}"))
        self.stdout.write(self.style.HTTP_INFO(f"{'='*80}\n"))
        
        # Find all webhook logs that contain this order ID
        webhooks = WebhookLog.objects.filter(
            webhook_type='order'
        ).order_by('received_at')
        
        # Filter by order ID in request body
        matching_webhooks = []
        for webhook in webhooks:
            try:
                if webhook.request_body:
                    body = json.loads(webhook.request_body)
                    if str(body.get('id')) == str(order_id):
                        matching_webhooks.append(webhook)
            except:
                continue
        
        if not matching_webhooks:
            self.stdout.write(self.style.ERROR(f"❌ No webhook logs found for order {order_id}\n"))
            return
        
        self.stdout.write(self.style.SUCCESS(f"✅ Found {len(matching_webhooks)} webhook(s) for order {order_id}\n"))
        
        # Check if POS order exists
        pos_orders = POSOrder.objects.filter(metadata__woo_order_id=str(order_id))
        if pos_orders.exists():
            pos_order = pos_orders.first()
            self.stdout.write(self.style.SUCCESS(f"📦 POS Order: {pos_order.id}"))
            self.stdout.write(f"   Order Number: {pos_order.order_number}")
            self.stdout.write(f"   Status: {pos_order.status}")
            self.stdout.write(f"   Created: {pos_order.created_at}")
            self.stdout.write(f"   Total: ${pos_order.total}\n")
        else:
            self.stdout.write(self.style.WARNING(f"⚠️  No POS order found with woo_order_id={order_id}\n"))
        
        # Analyze each webhook
        self.stdout.write(self.style.HTTP_INFO(f"\n{'─'*80}"))
        self.stdout.write(self.style.HTTP_INFO("WEBHOOK SEQUENCE:"))
        self.stdout.write(self.style.HTTP_INFO(f"{'─'*80}\n"))
        
        for idx, webhook in enumerate(matching_webhooks, 1):
            # Parse webhook data
            try:
                body = json.loads(webhook.request_body)
                woo_status = body.get('status', 'unknown')
                created_via = body.get('created_via', 'unknown')
                date_created = body.get('date_created', 'unknown')
                date_modified = body.get('date_modified', 'unknown')
            except:
                woo_status = 'parse_error'
                created_via = 'parse_error'
                date_created = 'unknown'
                date_modified = 'unknown'
            
            # Determine webhook event type
            is_creation = self._is_creation_webhook(webhook)
            event_type = "🆕 ORDER CREATION" if is_creation else "🔄 ORDER UPDATE"
            
            # Color based on status
            if webhook.status == 'success':
                status_color = self.style.SUCCESS
            elif webhook.status == 'skipped':
                status_color = self.style.WARNING
            else:
                status_color = self.style.ERROR
            
            # Print webhook details
            self.stdout.write(f"\n{self.style.HTTP_INFO(f'WEBHOOK #{idx}:')} {event_type}")
            self.stdout.write(f"{'─'*80}")
            self.stdout.write(f"Webhook Log ID:    {webhook.id}")
            self.stdout.write(f"Received At:       {webhook.received_at}")
            self.stdout.write(f"Processing Time:   {webhook.processing_time_ms}ms")
            self.stdout.write(status_color(f"Status:            {webhook.status.upper()}"))
            self.stdout.write(f"Action Taken:      {webhook.action_taken or 'N/A'}")
            self.stdout.write(f"Updated Fields:    {webhook.updated_fields or 'N/A'}")
            
            if webhook.error_message:
                self.stdout.write(self.style.ERROR(f"Error:             {webhook.error_message}"))
            
            self.stdout.write(f"\nWooCommerce Data:")
            self.stdout.write(f"  Order ID:        {order_id}")
            self.stdout.write(f"  Status:          {woo_status}")
            self.stdout.write(f"  Created Via:     {created_via}")
            self.stdout.write(f"  Date Created:    {date_created}")
            self.stdout.write(f"  Date Modified:   {date_modified}")
            
            # Show timing analysis
            if idx > 1:
                time_diff = (webhook.received_at - matching_webhooks[idx-2].received_at).total_seconds()
                self.stdout.write(f"\n⏱️  Time since previous webhook: {time_diff:.1f} seconds")
            
            if verbose:
                self.stdout.write(f"\n📄 Full Request Body:")
                try:
                    body_formatted = json.dumps(json.loads(webhook.request_body), indent=2)
                    self.stdout.write(body_formatted[:1000] + "..." if len(body_formatted) > 1000 else body_formatted)
                except:
                    self.stdout.write(webhook.request_body[:500])
        
        # Summary
        self.stdout.write(self.style.HTTP_INFO(f"\n{'='*80}"))
        self.stdout.write(self.style.HTTP_INFO("SUMMARY:"))
        self.stdout.write(self.style.HTTP_INFO(f"{'='*80}\n"))
        
        creation_webhooks = [w for w in matching_webhooks if self._is_creation_webhook(w)]
        update_webhooks = [w for w in matching_webhooks if not self._is_creation_webhook(w)]
        
        self.stdout.write(f"Total Webhooks:    {len(matching_webhooks)}")
        self.stdout.write(f"  Creation Events: {len(creation_webhooks)}")
        self.stdout.write(f"  Update Events:   {len(update_webhooks)}")
        self.stdout.write(f"\nStatus Breakdown:")
        self.stdout.write(f"  Success:         {len([w for w in matching_webhooks if w.status == 'success'])}")
        self.stdout.write(f"  Skipped:         {len([w for w in matching_webhooks if w.status == 'skipped'])}")
        self.stdout.write(f"  Failed:          {len([w for w in matching_webhooks if w.status == 'failed'])}")
        
        # Timing analysis
        if len(matching_webhooks) > 1:
            first_webhook = matching_webhooks[0]
            last_webhook = matching_webhooks[-1]
            total_duration = (last_webhook.received_at - first_webhook.received_at).total_seconds()
            self.stdout.write(f"\n⏱️  Total webhook span: {total_duration:.1f} seconds ({total_duration/3600:.1f} hours)")
        
        self.stdout.write("\n")

    def _is_creation_webhook(self, webhook):
        """
        Determine if a webhook represents order creation
        by checking the action taken
        """
        if not webhook.action_taken:
            return False
        
        action_lower = webhook.action_taken.lower()
        
        # Keywords that indicate creation
        creation_keywords = [
            'created pos order',
            'pos order created',
            'created new pos',
            'new pos order'
        ]
        
        return any(keyword in action_lower for keyword in creation_keywords)
