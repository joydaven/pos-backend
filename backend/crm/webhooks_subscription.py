"""
WooCommerce Subscription Webhook Handlers
Handles subscription-specific lifecycle events (renewal, cancellation, pause, etc.)
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .models import WebhookLog
from .utils.webhook_signature import verify_woocommerce_signature, check_webhook_duplicate

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def woocommerce_subscription_webhook(request):
    """
    WooCommerce Subscription Status Webhook Handler
    Handles subscription lifecycle events and syncs them with GHL
    """
    # Verify WooCommerce webhook HMAC signature
    valid, error_response = verify_woocommerce_signature(request)
    if not valid:
        return error_response

    # Deduplicate webhook deliveries
    is_dup, dup_response = check_webhook_duplicate(request)
    if is_dup:
        return dup_response

    # Create webhook log entry
    webhook_log = WebhookLog.objects.create(
        webhook_type='subscription',
        status='received',
        source_ip=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        request_body=request.body.decode('utf-8') if request.body else '',
        created_at=timezone.now()
    )
    
    try:
        webhook_log.status = 'processing'
        webhook_log.save()
        
        # Parse webhook data
        try:
            webhook_data = json.loads(request.body)
            logger.info(f"🔔 WooCommerce Subscription Webhook received: {webhook_data.get('id', 'Unknown')}")
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON in subscription webhook: {e}")
            webhook_log.status = 'failed'
            webhook_log.error_message = f"Invalid JSON: {e}"
            webhook_log.save()
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        
        # Extract subscription data
        subscription_id = webhook_data.get('id')
        subscription_status = webhook_data.get('status')
        
        if not subscription_id or not subscription_status:
            error_msg = f"Missing required fields: id={subscription_id}, status={subscription_status}"
            logger.error(f"❌ {error_msg}")
            webhook_log.status = 'failed'
            webhook_log.error_message = error_msg
            webhook_log.save()
            return JsonResponse({'error': error_msg}, status=400)
        
        # Log subscription details
        logger.info(f"📦 Processing subscription {subscription_id} status change to: {subscription_status}")
        
        # Update webhook log with subscription details
        webhook_log.order_id = subscription_id  # Reuse order_id field for subscription_id
        webhook_log.action_taken = f"Subscription status change to {subscription_status}"
        webhook_log.save()
        
        # Process subscription lifecycle event for GHL sync
        ghl_sync_result = process_subscription_lifecycle_event(webhook_data)
        
        if ghl_sync_result:
            logger.info(f"✅ Subscription GHL sync completed: {ghl_sync_result}")
            webhook_log.status = 'success'
            webhook_log.updated_fields = f"GHL sync: {ghl_sync_result}"
            webhook_log.processing_time_ms = int((timezone.now() - webhook_log.created_at).total_seconds() * 1000)
            webhook_log.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Subscription {subscription_id} status change processed and synced to GHL',
                'subscription_id': subscription_id,
                'status': subscription_status,
                'ghl_sync': ghl_sync_result
            })
        else:
            logger.info(f"ℹ️ No GHL sync needed for subscription {subscription_id}")
            webhook_log.status = 'skipped'
            webhook_log.action_taken = f"No GHL sync needed for status {subscription_status}"
            webhook_log.processing_time_ms = int((timezone.now() - webhook_log.created_at).total_seconds() * 1000)
            webhook_log.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Subscription {subscription_id} status change acknowledged (no sync needed)',
                'subscription_id': subscription_id,
                'status': subscription_status,
                'skipped': True
            })
            
    except Exception as e:
        logger.error(f"❌ Error processing subscription webhook: {e}")
        
        webhook_log.status = 'failed'
        webhook_log.error_message = str(e)
        webhook_log.processing_time_ms = int((timezone.now() - webhook_log.created_at).total_seconds() * 1000)
        webhook_log.save()
        
        return JsonResponse({'error': str(e)}, status=500)


def process_subscription_lifecycle_event(webhook_data):
    """
    Process subscription lifecycle events for GHL sync
    """
    from .ghl_membership_sync import (
        sync_membership_pause_to_ghl,
        sync_membership_reactivation_to_ghl,
        sync_membership_cancellation_to_ghl,
        extract_customer_email_from_order_data
    )
    
    try:
        subscription_status = webhook_data.get('status')
        subscription_id = webhook_data.get('id')
        
        # --- LOOP PREVENTION ---
        # If this status change was initiated by a GHL inbound webhook, skip GHL sync
        meta_data = webhook_data.get('meta_data', [])
        for meta in meta_data:
            if meta.get('key') == '_ghl_initiated':
                logger.info(
                    f"Loop prevention: subscription {subscription_id} status change "
                    f"was GHL-initiated, skipping GHL sync"
                )
                return None
        
        # Fallback: check WebhookLog for recent GHL inbound webhook
        from .models import WebhookLog
        cutoff = timezone.now() - timezone.timedelta(seconds=120)
        recent_ghl = WebhookLog.objects.filter(
            webhook_type='ghl_membership',
            status='success',
            received_at__gte=cutoff,
        ).filter(response_message__icontains=str(subscription_id))
        if recent_ghl.exists():
            logger.info(
                f"Loop prevention (DB check): recent GHL webhook found for "
                f"subscription {subscription_id}, skipping GHL sync"
            )
            return None
        # --- END LOOP PREVENTION ---
        
        # Extract customer email
        customer_email = extract_customer_email_from_order_data(webhook_data)
        if not customer_email:
            logger.warning(f"No customer email found in subscription {subscription_id}")
            return None
        
        logger.info(f"🎯 Processing subscription lifecycle event: {subscription_status} for {customer_email}")
        
        # Update is_active_member on Contact if this is a membership subscription
        from .ghl_membership_sync import is_membership_product
        line_items = webhook_data.get('line_items', [])
        has_membership_item = any(
            is_membership_product(item.get('name', ''))
            for item in line_items
        )
        if has_membership_item:
            is_active = subscription_status in ['wc-active', 'active']
            try:
                from .models import Contact
                Contact.objects.filter(email=customer_email).exclude(
                    is_active_member=is_active
                ).update(is_active_member=is_active)
                logger.info(f"Updated is_active_member={is_active} for {customer_email} via webhook")
            except Exception as e:
                logger.warning(f"Could not update is_active_member for {customer_email}: {e}")
        
        # Prepare common sync data
        sync_data = {
            'subscription_id': subscription_id,
            'billing_interval': extract_billing_interval(webhook_data),
            'billing_period': extract_billing_period(webhook_data),
            'reason': f'Subscription {subscription_status} via webhook'
        }
        
        # Process different subscription status changes
        if subscription_status in ['wc-active', 'active']:
            # Subscription activated/reactivated
            logger.info(f"▶️ Processing subscription activation for {subscription_id}")
            result = sync_membership_reactivation_to_ghl(customer_email, sync_data)
            return f"Activation sync: {result}"
            
        elif subscription_status in ['wc-on-hold', 'on-hold']:
            # Subscription paused/on hold
            logger.info(f"⏸️ Processing subscription pause for {subscription_id}")
            result = sync_membership_pause_to_ghl(customer_email, sync_data)
            return f"Pause sync: {result}"
            
        elif subscription_status in ['wc-cancelled', 'cancelled']:
            # Subscription cancelled
            logger.info(f"❌ Processing subscription cancellation for {subscription_id}")
            sync_data['reason'] = 'Subscription cancelled via webhook'
            result = sync_membership_cancellation_to_ghl(customer_email, sync_data)
            
            # Send cancellation email to customer
            _send_cancellation_email_from_webhook(customer_email, webhook_data, subscription_id, 'Subscription cancelled via webhook')
            
            return f"Cancellation sync: {result}"
            
        elif subscription_status in ['wc-expired', 'expired']:
            # Subscription expired (treat as cancellation)
            logger.info(f"⏰ Processing subscription expiration for {subscription_id}")
            sync_data['reason'] = 'Subscription expired'
            result = sync_membership_cancellation_to_ghl(customer_email, sync_data)
            
            # Send cancellation email to customer
            _send_cancellation_email_from_webhook(customer_email, webhook_data, subscription_id, 'Subscription expired')
            
            return f"Expiration sync: {result}"
        
        else:
            logger.info(f"ℹ️ No GHL sync action defined for subscription status: {subscription_status}")
            return None
        
    except Exception as e:
        logger.error(f"Error processing subscription lifecycle event: {str(e)}")
        return f"Error: {str(e)}"


def _send_cancellation_email_from_webhook(customer_email, webhook_data, subscription_id, reason):
    """
    Send cancellation email from webhook context.
    Extracts customer name, plan info, subscription details, line items, and billing
    from webhook data. Never raises — safe to call from webhook handlers.
    """
    try:
        from .models import POSSetting
        send_email = POSSetting.get_setting('membership_cancellation_email_enabled', True)
        if not send_email:
            logger.info(f"📧 Cancellation email disabled via settings, skipping for subscription {subscription_id}")
            return

        from .views_receipts import send_cancellation_email

        # Extract customer name from webhook billing data
        billing = webhook_data.get('billing', {})
        customer_name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()

        # Extract plan name from line items
        plan_name = 'Membership'
        line_items = webhook_data.get('line_items', [])
        if line_items:
            plan_name = line_items[0].get('name', 'Membership')

        cancellation_date = timezone.now().strftime('%b %d, %Y')

        # Extract subscription status
        sub_status = webhook_data.get('status', 'cancelled')
        # Normalize WC prefixed statuses
        if sub_status.startswith('wc-'):
            sub_status = sub_status[3:]

        # Extract subscription total and billing period
        sub_total = webhook_data.get('total', '')
        discount_total = float(webhook_data.get('discount_total', '0.00'))
        billing_period = extract_billing_period(webhook_data)
        billing_interval = extract_billing_interval(webhook_data)
        if sub_total:
            try:
                interval_str = f" every {billing_interval} " if billing_interval and int(billing_interval) > 1 else ' / '
                subscription_total = f"${float(sub_total):.2f}{interval_str}{billing_period}"
            except (ValueError, TypeError):
                subscription_total = sub_total
        else:
            subscription_total = ''

        # Extract last order date
        last_order_date = ''
        date_completed = webhook_data.get('date_completed', '') or webhook_data.get('date_paid', '')
        if date_completed:
            try:
                from datetime import datetime as dt
                parsed = dt.fromisoformat(date_completed.replace('Z', '+00:00'))
                last_order_date = parsed.strftime('%b %d, %Y')
            except Exception:
                last_order_date = str(date_completed)[:10]

        # Extract end of prepaid term
        end_date = ''
        schedule_end = webhook_data.get('schedule_end', '') or webhook_data.get('end_date', '')
        if not schedule_end:
            # Check meta_data for _schedule_end
            for meta in webhook_data.get('meta_data', []):
                if meta.get('key') in ('_schedule_end', 'schedule_end'):
                    schedule_end = meta.get('value', '')
                    break
        if schedule_end:
            try:
                from datetime import datetime as dt
                parsed = dt.fromisoformat(schedule_end.replace('Z', '+00:00'))
                end_date = parsed.strftime('%b %d, %Y')
            except Exception:
                end_date = str(schedule_end)[:10]

        # Extract parent order ID
        parent_order_id = webhook_data.get('parent_id', None)
        if not parent_order_id:
            for meta in webhook_data.get('meta_data', []):
                if meta.get('key') == '_order_id':
                    parent_order_id = meta.get('value')
                    break

        email_sent = send_cancellation_email(
            customer_email, customer_name, plan_name, subscription_id, cancellation_date, reason,
            status=sub_status, subscription_total=subscription_total, billing_period=billing_period,
            last_order_date=last_order_date, end_date=end_date, line_items=line_items,
            billing=billing, parent_order_id=parent_order_id, discount_total=discount_total,
        )
        logger.info(f"📧 Cancellation email {'sent' if email_sent else 'failed'} to {customer_email} for subscription {subscription_id}")
    except Exception as e:
        logger.error(f"📧 Error sending cancellation email from webhook: {e}")


def extract_billing_interval(webhook_data):
    """Extract billing interval from subscription webhook data"""
    # Check various places where billing interval might be stored
    billing_interval = webhook_data.get('billing_interval', 1)
    
    # Check in meta_data
    meta_data = webhook_data.get('meta_data', [])
    for meta in meta_data:
        if meta.get('key') in ['_billing_interval', 'billing_interval']:
            try:
                billing_interval = int(meta.get('value', 1))
                break
            except (ValueError, TypeError):
                pass
    
    return billing_interval


def extract_billing_period(webhook_data):
    """Extract billing period from subscription webhook data"""
    # Check various places where billing period might be stored
    billing_period = webhook_data.get('billing_period', 'month')
    
    # Check in meta_data
    meta_data = webhook_data.get('meta_data', [])
    for meta in meta_data:
        if meta.get('key') in ['_billing_period', 'billing_period']:
            billing_period = meta.get('value', 'month')
            break
    
    return billing_period


@csrf_exempt
@require_http_methods(["POST"])
def woocommerce_subscription_webhook_test(request):
    """
    Test endpoint for WooCommerce subscription webhooks
    """
    try:
        webhook_data = json.loads(request.body) if request.body else {}
        
        # Create test webhook log
        webhook_log = WebhookLog.objects.create(
            webhook_type='subscription_test',
            status='success',
            source_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            request_body=request.body.decode('utf-8') if request.body else '',
            action_taken='Test subscription webhook received',
            created_at=timezone.now()
        )
        
        logger.info(f"🧪 Test subscription webhook received: {webhook_data}")
        
        return JsonResponse({
            'success': True,
            'message': 'Subscription webhook test received successfully',
            'webhook_log_id': str(webhook_log.id),
            'received_data': webhook_data
        })
        
    except Exception as e:
        logger.error(f"❌ Error in test subscription webhook: {e}")
        return JsonResponse({'error': str(e)}, status=500)
