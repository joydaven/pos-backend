"""
GoHighLevel Inbound Webhook Handlers
Handles membership field change events fired by GHL automations.

Flow: GHL member_status changes -> GHL Workflow fires webhook here
      -> Backend updates WooCommerce subscription status + dates
      -> Loop prevention ensures WC webhook doesn't re-sync back to GHL

Security: Requires Bearer token matching GHL_INBOUND_WEBHOOK_SECRET in .env
"""

import json
import logging
import os
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .models import WebhookLog

logger = logging.getLogger(__name__)

# GHL member_status values -> WooCommerce subscription statuses
GHL_STATUS_TO_WC_STATUS = {
    'Active Member': 'active',
    'Past Member': 'cancelled',
    'Non-Member': 'cancelled',
    'Pending Cancel': 'pending-cancel',
    'Paused': 'on-hold',
    'Cancelled': 'cancelled',
}

LOOP_PREVENTION_WINDOW_SECONDS = 120


def verify_webhook_token(request):
    """
    Verify the Bearer token from the GHL webhook request.
    Returns True if valid, False otherwise.
    """
    expected_token = getattr(settings, 'GHL_INBOUND_WEBHOOK_SECRET', '')
    if not expected_token:
        logger.warning("GHL_INBOUND_WEBHOOK_SECRET not configured — rejecting all inbound GHL webhooks")
        return False

    auth_header = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth_header.startswith('Bearer '):
        return False

    provided_token = auth_header[7:]  # Strip "Bearer "
    return provided_token == expected_token


def is_ghl_sync_locked(subscription_id):
    """
    Check if a recent outbound WC -> GHL sync happened for this subscription.
    If yes, skip inbound processing to prevent loops.
    """
    cutoff = timezone.now() - timezone.timedelta(seconds=LOOP_PREVENTION_WINDOW_SECONDS)
    recent = WebhookLog.objects.filter(
        webhook_type='subscription',
        status='success',
        order_id=subscription_id,
        received_at__gte=cutoff,
    ).exists()

    if recent:
        logger.info(
            f"Loop prevention: skipping GHL inbound for subscription {subscription_id} "
            f"(outbound WC->GHL sync within {LOOP_PREVENTION_WINDOW_SECONDS}s)"
        )
    return recent


def is_duplicate_ghl_webhook(email):
    """
    Check if a recent inbound GHL webhook already processed for this email.
    Prevents duplicate processing from rapid GHL automation retries.
    """
    cutoff = timezone.now() - timezone.timedelta(seconds=LOOP_PREVENTION_WINDOW_SECONDS)
    return WebhookLog.objects.filter(
        webhook_type='ghl_membership',
        status='success',
        action_taken__icontains=email,
        received_at__gte=cutoff,
    ).exists()


@csrf_exempt
@require_http_methods(["POST"])
def ghl_membership_webhook(request):
    """
    Inbound webhook from GHL automation.
    Triggered when membership custom fields change on a GHL contact.
    Secured with Bearer token (GHL_INBOUND_WEBHOOK_SECRET).

    Expected payload (configure in GHL Workflow "Send Webhook" action):
    {
        "contact_id": "GHL_CONTACT_ID",
        "email": "customer@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "member_status": "Active Member",
        "membership_activation": "01/15/2026",
        "cancellation_date": "02/10/2026",
        "expiry_date": "03/15/2026",
        "current_membership_fee": "$499.00",
        "last_charge": "01/15/2026",
        "next_charge": "02/15/2026",
        "amount_charged": "$499.00",
        "membership_reactivation": "01/20/2026"
    }
    """
    webhook_log = WebhookLog.objects.create(
        webhook_type='ghl_membership',
        status='received',
        source_ip=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        request_body=request.body.decode('utf-8') if request.body else '',
    )

    # --- AUTH CHECK ---
    if not verify_webhook_token(request):
        webhook_log.mark_failed("Unauthorized: invalid or missing Bearer token")
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        webhook_log.status = 'processing'
        webhook_log.save()

        payload = json.loads(request.body)
        email = payload.get('email', '').strip().lower()
        ghl_contact_id = payload.get('contact_id', '')
        new_member_status = payload.get('member_status', '')

        # Extract all membership fields from payload
        membership_fields = {
            'member_status': new_member_status,
            'membership_activation': payload.get('membership_activation', ''),
            'cancellation_date': payload.get('cancellation_date', ''),
            'expiry_date': payload.get('expiry_date', ''),
            'current_membership_fee': payload.get('current_membership_fee', ''),
            'last_charge': payload.get('last_charge', ''),
            'next_charge': payload.get('next_charge', ''),
            'amount_charged': payload.get('amount_charged', ''),
            'membership_reactivation': payload.get('membership_reactivation', ''),
        }

        # Filter out empty values for logging
        received_fields = {k: v for k, v in membership_fields.items() if v}

        logger.info(
            f"GHL Membership webhook received: email={email}, "
            f"contact_id={ghl_contact_id}, fields={received_fields}"
        )

        if not email:
            webhook_log.mark_failed("Missing email in GHL webhook payload")
            return JsonResponse({'error': 'Missing email'}, status=400)

        if not new_member_status:
            webhook_log.mark_failed("Missing member_status in GHL webhook payload")
            return JsonResponse({'error': 'Missing member_status'}, status=400)

        # Duplicate check
        if is_duplicate_ghl_webhook(email):
            webhook_log.mark_skipped(f"Duplicate GHL webhook for {email} within lock window")
            return JsonResponse({'success': True, 'skipped': True, 'reason': 'duplicate'})

        # Map GHL status to WooCommerce status
        wc_status = GHL_STATUS_TO_WC_STATUS.get(new_member_status)
        if not wc_status:
            msg = f"Unknown GHL member_status: '{new_member_status}'"
            logger.warning(msg)
            webhook_log.mark_skipped(msg)
            return JsonResponse({'success': True, 'skipped': True, 'reason': msg})

        # Update is_active_member on Contact
        try:
            from .models import Contact
            is_active = (new_member_status == 'Active Member')
            Contact.objects.filter(email=email).exclude(
                is_active_member=is_active
            ).update(is_active_member=is_active)
            logger.info(f"Updated is_active_member={is_active} for {email} via GHL webhook")
        except Exception as e:
            logger.warning(f"Could not update is_active_member for {email}: {e}")

        # Find and update the customer's WooCommerce subscription
        result = update_woocommerce_subscription_from_ghl(
            email, wc_status, new_member_status, membership_fields
        )

        webhook_log.action_taken = f"{email} -> WC:{wc_status}"
        processing_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)

        if result.get('success'):
            webhook_log.mark_success(
                response_message=(
                    f"Updated subscription {result.get('subscription_id')} to {wc_status} | "
                    f"fields: {list(received_fields.keys())}"
                ),
                processing_time_ms=processing_ms,
            )
        elif result.get('skipped'):
            webhook_log.mark_skipped(result.get('reason', 'Skipped'))
        else:
            webhook_log.mark_failed(
                error_message=result.get('error', 'Unknown error'),
                processing_time_ms=processing_ms,
            )

        return JsonResponse(result)

    except json.JSONDecodeError:
        webhook_log.mark_failed("Invalid JSON")
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"GHL membership webhook error: {e}")
        webhook_log.mark_failed(str(e))
        return JsonResponse({'error': str(e)}, status=500)


def update_woocommerce_subscription_from_ghl(email, wc_status, ghl_status_label, membership_fields):
    """
    Find the customer's membership subscription in WooCommerce and update
    its status and dates based on GHL field changes.

    Args:
        email (str): Customer email (lowercase)
        wc_status (str): Target WooCommerce subscription status
        ghl_status_label (str): Original GHL member_status value (for logging)
        membership_fields (dict): All membership fields from GHL payload

    Returns:
        dict with 'success', 'subscription_id', 'error', etc.
    """
    from .woocommerce import WooCommerceAPI
    from .ghl_membership_sync import is_membership_product

    try:
        wc_api = WooCommerceAPI()

        # Search WooCommerce subscriptions by customer email
        search_result = wc_api.get_subscriptions(
            page=1, per_page=20,
            status='any', search=email
        )

        if search_result['status'] != 'success' or not search_result['data']:
            return {'success': False, 'error': f'No WooCommerce subscriptions found for {email}'}

        # Find the membership subscription
        target_subscription = None
        for sub in search_result['data']:
            billing_email = sub.get('billing', {}).get('email', '').lower()
            if billing_email != email:
                continue

            line_items = sub.get('line_items', [])
            has_membership = any(
                is_membership_product(item.get('name', ''))
                for item in line_items
            )
            if not has_membership:
                continue

            current_status = sub.get('status', '')
            if current_status != wc_status:
                target_subscription = sub
                break

        if not target_subscription:
            return {
                'success': False,
                'error': (
                    f'No membership subscription found for {email} '
                    f'that needs status change to {wc_status}'
                ),
            }

        subscription_id = target_subscription['id']

        # Loop prevention: check if a recent WC->GHL sync just happened
        if is_ghl_sync_locked(subscription_id):
            return {
                'success': True,
                'skipped': True,
                'reason': 'Loop prevention: recent WC->GHL sync detected',
                'subscription_id': subscription_id,
            }

        # Build WooCommerce update payload
        update_data = {
            'status': wc_status,
            'meta_data': [
                {'key': '_ghl_initiated', 'value': str(timezone.now().isoformat())}
            ],
        }

        # If next_charge date was provided, update WC next_payment_date
        next_charge = membership_fields.get('next_charge', '')
        if next_charge:
            wc_date = parse_ghl_date_to_wc(next_charge)
            if wc_date:
                update_data['next_payment_date'] = wc_date
                logger.info(f"Including next_payment_date update: {wc_date}")

        # If cancellation/expiry, clear next payment and set end date
        if wc_status in ('cancelled', 'pending-cancel'):
            expiry_date = membership_fields.get('expiry_date', '')
            cancel_date = membership_fields.get('cancellation_date', '')
            end_date = expiry_date or cancel_date
            if end_date:
                wc_end = parse_ghl_date_to_wc(end_date)
                if wc_end:
                    update_data['end_date'] = wc_end

        response = wc_api.wcapi.put(f"subscriptions/{subscription_id}", update_data)

        if response.ok:
            changes = [f"status={wc_status}"]
            if 'next_payment_date' in update_data:
                changes.append(f"next_payment={update_data['next_payment_date']}")
            if 'end_date' in update_data:
                changes.append(f"end_date={update_data['end_date']}")

            logger.info(
                f"Updated WC subscription {subscription_id}: {', '.join(changes)} "
                f"(from GHL: '{ghl_status_label}')"
            )
            return {
                'success': True,
                'subscription_id': subscription_id,
                'new_status': wc_status,
                'ghl_status': ghl_status_label,
                'changes': changes,
            }
        else:
            error = f"WC API error {response.status_code}: {response.text[:500]}"
            logger.error(error)
            return {'success': False, 'error': error, 'subscription_id': subscription_id}

    except Exception as e:
        logger.error(f"Error updating WC subscription from GHL: {e}")
        return {'success': False, 'error': str(e)}


def parse_ghl_date_to_wc(date_str):
    """
    Convert GHL date format (MM/DD/YYYY) to WooCommerce format (YYYY-MM-DDT00:00:00).
    Returns None if parsing fails.
    """
    from datetime import datetime
    if not date_str or not date_str.strip():
        return None
    try:
        parsed = datetime.strptime(date_str.strip(), '%m/%d/%Y')
        return parsed.strftime('%Y-%m-%dT00:00:00')
    except ValueError:
        try:
            parsed = datetime.strptime(date_str.strip(), '%Y-%m-%d')
            return parsed.strftime('%Y-%m-%dT00:00:00')
        except ValueError:
            logger.warning(f"Could not parse GHL date: '{date_str}'")
            return None


# ---------------------------------------------------------------------------
# GHL Contact Update Webhook
# Triggered by GHL Workflow when a contact's profile fields change.
# ---------------------------------------------------------------------------

@csrf_exempt
@require_http_methods(["POST"])
def ghl_contact_update_webhook(request):
    """
    Inbound webhook from GHL ContactUpdate event.
    Triggered when a contact's profile fields change in GHL.
    Updates the corresponding POS Contact with the latest GHL data.

    GHL is the source of truth — fields are always overwritten.

    Actual GHL ContactUpdate payload structure:
    {
        "type": "ContactUpdate",
        "id": "ASFbC9HzNyaB0ufnrlNd",       // GHL contact ID (NOT "contact_id")
        "locationId": "pgfekl6sKgofVPSuYOJo",
        "firstName": "Jay",
        "lastName": "Developer",
        "email": "jay@doctorsstudio.com",
        "phone": "+109999999999",
        "address1": "Street Address",          // NOTE: "address1" not "address"
        "city": "City",
        "state": "State",
        "postalCode": "Postal Code",
        "country": "US",
        "tags": ["customer", "ds-member-cancel"],
        "customFields": [{"id": "abc123", "value": "some value"}],
        "dateOfBirth": "1983-02-02T00:00:00.000Z",
        "source": "...",
        "assignedTo": "...",
        ...
    }
    """
    from .models import Contact
    from .ghl_api import apply_ghl_contact_fields

    webhook_log = WebhookLog.objects.create(
        webhook_type='ghl_contact_update',
        status='received',
        source_ip=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        request_body=request.body.decode('utf-8') if request.body else '',
    )

    # --- AUTH CHECK ---
    if not verify_webhook_token(request):
        webhook_log.mark_failed("Unauthorized: invalid or missing Bearer token")
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        webhook_log.status = 'processing'
        webhook_log.save()

        payload = json.loads(request.body)

        # GHL ContactUpdate uses "id" for the contact ID (not "contact_id")
        ghl_contact_id = (payload.get('id') or payload.get('contact_id') or '').strip()
        email = (payload.get('email') or '').strip().lower()

        if not ghl_contact_id and not email:
            webhook_log.mark_failed("Missing both id and email in GHL ContactUpdate payload")
            return JsonResponse({'error': 'Missing id or email'}, status=400)

        logger.info(
            f"GHL contact update webhook: contact_id={ghl_contact_id}, email={email}"
        )

        # Duplicate check
        if email and is_duplicate_ghl_webhook(email):
            webhook_log.mark_skipped(f"Duplicate GHL contact update for {email} within lock window")
            return JsonResponse({'success': True, 'skipped': True, 'reason': 'duplicate'})

        # Look up the local Contact — prefer ghl_contact_id, fall back to email
        contact = None
        if ghl_contact_id:
            contact = Contact.objects.filter(ghl_contact_id=ghl_contact_id).first()
        if not contact and email:
            contact = Contact.objects.filter(email__iexact=email).first()

        if not contact:
            msg = f"No local contact found for ghl_contact_id={ghl_contact_id} / email={email}"
            logger.info(msg)
            webhook_log.mark_skipped(msg)
            return JsonResponse({'success': True, 'skipped': True, 'reason': msg})

        # Ensure ghl_contact_id is stored if we matched by email
        if ghl_contact_id and not contact.ghl_contact_id:
            contact.ghl_contact_id = ghl_contact_id
            contact.save(update_fields=['ghl_contact_id'])

        # Store the raw payload as ghl_data
        contact.ghl_data = payload
        contact.ghl_last_sync = timezone.now()
        contact.save(update_fields=['ghl_data', 'ghl_last_sync'])

        # Apply GHL fields → POS contact (source of truth overwrite)
        updated_fields = apply_ghl_contact_fields(contact, payload)

        processing_ms = int(
            (timezone.now() - webhook_log.received_at).total_seconds() * 1000
        )
        webhook_log.action_taken = f"{email} fields updated: {updated_fields}"
        webhook_log.mark_success(
            response_message=f"Updated {len(updated_fields)} fields for {contact.email}",
            processing_time_ms=processing_ms,
        )

        logger.info(
            f"✅ GHL contact update applied for {contact.email}: {updated_fields}"
        )

        return JsonResponse({
            'success': True,
            'contact_id': str(contact.id),
            'email': contact.email,
            'updated_fields': updated_fields,
        })

    except json.JSONDecodeError:
        webhook_log.mark_failed("Invalid JSON")
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"GHL contact update webhook error: {e}")
        webhook_log.mark_failed(str(e))
        return JsonResponse({'error': str(e)}, status=500)
