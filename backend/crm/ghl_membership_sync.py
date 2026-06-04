"""
GoHighLevel Membership Lifecycle Sync
Handles syncing membership purchases, renewals, cancellations, and status changes to GHL custom fields
"""

import logging
import json
from datetime import datetime, timedelta
from django.utils import timezone
from .ghl_api import (
    get_ghl_contact_id_by_email,
    update_ghl_contact_custom_field,
    add_ghl_note
)
from .models import Contact, POSSetting

logger = logging.getLogger(__name__)

# GHL Custom Field IDs for membership data
GHL_MEMBERSHIP_FIELDS = {
    'current_membership_fee': 'OyVBTEnlEqOegqJrZsSa',
    'last_charge': 'UoTdiQYnpfKKbjgLFBHc', 
    'next_charge': 'QskqTssDzni04NWUSVcL',
    'amount_charged': 'UaEc2uBD1bmVI5PvOSaO',
    'membership_reactivation': 'jJDeaSlYekDJOg61DLF4',
    'member_status': 'INuPiX0s0uXhTq9qKB3x',
    'membership_activation': '5fBv7GStdh5lqn43PcDT',
    'cancellation_date': '0E7M3ojNYot4Bs83l3Zo',
    'expiry_date': 'VWVAd2d9aE74u9OcCeiU',
}

GHL_MEMBERSHIP_FIELD_IDS_TO_NAMES = {v: k for k, v in GHL_MEMBERSHIP_FIELDS.items()}


def get_ghl_membership_fields(contact_data):
    """
    Extract membership custom field values from a GHL contact's customFields array.
    
    Args:
        contact_data (dict): Full GHL contact dict (as returned by get_ghl_contact_by_id)
        
    Returns:
        dict: e.g. {'member_status': 'Active Member', 'next_charge': '03/15/2026', ...}
              Only includes fields that have values.
    """
    custom_fields = contact_data.get('customFields', [])
    if not custom_fields:
        return {}
    
    membership_fields = {}
    for field in custom_fields:
        field_id = field.get('id', '')
        field_value = field.get('value', '')
        
        if field_id in GHL_MEMBERSHIP_FIELD_IDS_TO_NAMES and field_value:
            field_name = GHL_MEMBERSHIP_FIELD_IDS_TO_NAMES[field_id]
            membership_fields[field_name] = field_value
    
    logger.info(f"Extracted {len(membership_fields)} GHL membership fields: {membership_fields}")
    return membership_fields


def is_membership_product(product_name, product_categories=None):
    """
    Determine if a product is a membership product based on name and categories
    """
    membership_keywords = [
        'membership', 'member', 'subscription', 'monthly', 'annual',
        'studio membership', 'doctors studio', 'clinic membership'
    ]
    
    # Check product name
    product_name_lower = product_name.lower()
    for keyword in membership_keywords:
        if keyword in product_name_lower:
            logger.info(f"✅ Detected membership product by name: {product_name} (keyword: {keyword})")
            return True
    
    # Check categories if provided
    if product_categories:
        for category in product_categories:
            category_lower = category.lower()
            for keyword in membership_keywords:
                if keyword in category_lower:
                    logger.info(f"✅ Detected membership product by category: {product_name} (category: {category}, keyword: {keyword})")
                    return True
    
    return False

def calculate_next_billing_date(billing_interval, billing_period, start_date=None):
    """
    Calculate next billing date based on billing schedule
    """
    base_date = start_date or timezone.now()
    
    if billing_period == 'day':
        return base_date + timedelta(days=billing_interval)
    elif billing_period == 'week':
        return base_date + timedelta(weeks=billing_interval)
    elif billing_period == 'month':
        # More accurate month calculation
        import calendar
        year = base_date.year
        month = base_date.month + billing_interval
        
        # Handle year overflow
        while month > 12:
            year += 1
            month -= 12
        
        # Handle day overflow (e.g., Jan 31 + 1 month = Feb 28/29)
        day = min(base_date.day, calendar.monthrange(year, month)[1])
        
        return base_date.replace(year=year, month=month, day=day)
    elif billing_period == 'year':
        return base_date.replace(year=base_date.year + billing_interval)
    else:
        # Default to monthly
        logger.warning(f"Unknown billing period: {billing_period}, defaulting to monthly")
        return base_date + timedelta(days=30)

def sync_membership_purchase_to_ghl(customer_email, membership_data, location_id=None):
    """
    Sync initial membership purchase data to GHL custom fields
    
    Args:
        customer_email (str): Customer email address
        membership_data (dict): Membership purchase information
            - product_name: Name of membership product
            - amount_paid: Amount charged for membership
            - billing_interval: Billing interval (1, 2, 3, etc.)
            - billing_period: Billing period ('month', 'week', 'year')
            - start_date: Membership start date
            - subscription_id: WooCommerce subscription ID (if applicable)
            - order_id: Order ID
        location_id (str): GHL location ID (optional, uses default if not provided)
    
    Returns:
        dict: Sync results
    """
    logger.info(f"🎉 Starting GHL membership purchase sync for {customer_email}")
    
    results = {
        'success': False,
        'fields_updated': [],
        'errors': [],
        'ghl_contact_id': None
    }
    
    try:
        # Get GHL contact ID with location_id
        ghl_contact_id = get_ghl_contact_id_by_email(customer_email, location_id)
        if not ghl_contact_id:
            error_msg = f"GHL contact not found for email: {customer_email} in location: {location_id}"
            logger.error(error_msg)
            results['errors'].append(error_msg)
            return results
        
        results['ghl_contact_id'] = ghl_contact_id
        logger.info(f"✅ Found GHL contact: {ghl_contact_id}")
        
        # Prepare membership field updates
        field_updates = {}
        
        # Set member status to Active Member
        field_updates['member_status'] = 'Active Member'
        
        # Set membership activation date (purchase date)
        activation_date = membership_data.get('start_date', timezone.now())
        if isinstance(activation_date, str):
            try:
                activation_date = datetime.fromisoformat(activation_date.replace('Z', '+00:00'))
            except:
                activation_date = timezone.now()
        field_updates['membership_activation'] = activation_date.strftime('%m/%d/%Y')
        
        # Set last charge date (today)
        field_updates['last_charge'] = timezone.now().strftime('%m/%d/%Y')
        
        # Set amount charged
        amount_paid = float(membership_data.get('amount_paid', 0))
        field_updates['amount_charged'] = f"${amount_paid:.2f}"
        
        # Set current membership fee
        field_updates['current_membership_fee'] = f"${amount_paid:.2f}"
        
        # Calculate and set next charge date
        billing_interval = int(membership_data.get('billing_interval', 1))
        billing_period = membership_data.get('billing_period', 'month')
        next_charge = calculate_next_billing_date(billing_interval, billing_period, activation_date)
        field_updates['next_charge'] = next_charge.strftime('%m/%d/%Y')
        
        # Update each field in GHL
        for field_name, field_value in field_updates.items():
            field_id = GHL_MEMBERSHIP_FIELDS.get(field_name)
            if field_id:
                success = update_ghl_contact_custom_field(
                    ghl_contact_id, 
                    field_id, 
                    field_value
                )
                
                if success:
                    results['fields_updated'].append(f"{field_name}: {field_value}")
                    logger.info(f"✅ Updated {field_name} to {field_value}")
                else:
                    error_msg = f"Failed to update {field_name}"
                    results['errors'].append(error_msg)
                    logger.error(error_msg)
        
        # Add note to GHL contact timeline
        product_name = membership_data.get('product_name', 'Membership')
        subscription_id = membership_data.get('subscription_id', 'N/A')
        order_id = membership_data.get('order_id', 'N/A')
        
        # Check if onboarding emails are enabled
        send_onboarding_email = POSSetting.get_setting('membership_onboarding_email_enabled', True)
        
        if send_onboarding_email:
            note_body = f"""🎉 MEMBERSHIP PURCHASE COMPLETED

Product: {product_name}
Amount Paid: ${amount_paid:.2f}
Billing: Every {billing_interval} {billing_period}(s)
Start Date: {activation_date.strftime('%m/%d/%Y')}
Next Charge: {next_charge.strftime('%m/%d/%Y')}
Order ID: {order_id}
Subscription ID: {subscription_id}

Membership status has been activated and billing schedule updated."""
            
            note_success = add_ghl_note(ghl_contact_id, note_body)
            if note_success:
                logger.info("✅ Added membership purchase note to GHL (onboarding email enabled)")
            else:
                results['errors'].append("Failed to add purchase note")
        else:
            logger.info("⏭️ Skipped membership purchase note (onboarding email disabled)")
        
        # Mark as successful if at least one field was updated
        results['success'] = len(results['fields_updated']) > 0
        
        logger.info(f"🎯 GHL membership purchase sync completed for {customer_email}: {len(results['fields_updated'])} fields updated")
        
    except Exception as e:
        error_msg = f"Error syncing membership purchase to GHL: {str(e)}"
        logger.error(error_msg)
        results['errors'].append(error_msg)
    
    return results

def sync_membership_renewal_to_ghl(customer_email, renewal_data):
    """
    Sync membership renewal/recurring billing to GHL
    """
    logger.info(f"🔄 Starting GHL membership renewal sync for {customer_email}")
    
    results = {
        'success': False,
        'fields_updated': [],
        'errors': []
    }
    
    try:
        ghl_contact_id = get_ghl_contact_id_by_email(customer_email)
        if not ghl_contact_id:
            results['errors'].append(f"GHL contact not found for email: {customer_email}")
            return results
        
        # Update billing fields for renewal
        renewal_date = timezone.now()
        amount_charged = float(renewal_data.get('amount_charged', 0))
        billing_interval = int(renewal_data.get('billing_interval', 1))
        billing_period = renewal_data.get('billing_period', 'month')
        
        # Calculate next billing date
        next_charge = calculate_next_billing_date(billing_interval, billing_period, renewal_date)
        
        field_updates = {
            'last_charge': renewal_date.strftime('%m/%d/%Y'),
            'next_charge': next_charge.strftime('%m/%d/%Y'),
            'amount_charged': f"${amount_charged:.2f}",
            'member_status': 'Active Member'  # Ensure status stays active
        }
        
        # Update each field
        for field_name, field_value in field_updates.items():
            field_id = GHL_MEMBERSHIP_FIELDS.get(field_name)
            if field_id:
                success = update_ghl_contact_custom_field(ghl_contact_id, field_id, field_value)
                if success:
                    results['fields_updated'].append(f"{field_name}: {field_value}")
                else:
                    results['errors'].append(f"Failed to update {field_name}")
        
        # Add renewal note
        subscription_id = renewal_data.get('subscription_id', 'N/A')
        order_id = renewal_data.get('order_id', 'N/A')
        
        note_body = f"""💳 MEMBERSHIP RENEWAL PROCESSED

Amount Charged: ${amount_charged:.2f}
Billing Date: {renewal_date.strftime('%m/%d/%Y')}
Next Charge: {next_charge.strftime('%m/%d/%Y')}
Order ID: {order_id}
Subscription ID: {subscription_id}

Membership has been successfully renewed."""
        
        note_success = add_ghl_note(ghl_contact_id, note_body)
        if note_success:
            logger.info("✅ Added membership renewal note to GHL")
        
        results['success'] = len(results['fields_updated']) > 0
        logger.info(f"🎯 GHL membership renewal sync completed: {len(results['fields_updated'])} fields updated")
        
    except Exception as e:
        results['errors'].append(f"Error syncing renewal: {str(e)}")
        logger.error(f"Error syncing membership renewal: {str(e)}")
    
    return results

def sync_membership_cancellation_to_ghl(customer_email, cancellation_data):
    """
    Sync membership cancellation to GHL
    """
    logger.info(f"❌ Starting GHL membership cancellation sync for {customer_email}")
    
    results = {
        'success': False,
        'fields_updated': [],
        'errors': []
    }
    
    try:
        ghl_contact_id = get_ghl_contact_id_by_email(customer_email)
        if not ghl_contact_id:
            results['errors'].append(f"GHL contact not found for email: {customer_email}")
            return results
        
        # Determine if this is an expiration or a cancellation
        is_expired = 'expired' in cancellation_data.get('reason', '').lower()
        cancellation_now = timezone.now().strftime('%Y-%m-%d')
        
        # Update member status, set date fields, and clear next charge
        field_updates = {
            'member_status': 'Past Member' if is_expired else 'Cancelled',
            'next_charge': '',  # Clear next charge date
            'cancellation_date': cancellation_now,
        }
        
        if is_expired:
            field_updates['expiry_date'] = cancellation_now
        
        # Update fields
        for field_name, field_value in field_updates.items():
            field_id = GHL_MEMBERSHIP_FIELDS.get(field_name)
            if field_id:
                success = update_ghl_contact_custom_field(ghl_contact_id, field_id, field_value)
                if success:
                    results['fields_updated'].append(f"{field_name}: {field_value}")
                else:
                    results['errors'].append(f"Failed to update {field_name}")
        
        # Add cancellation note
        reason = cancellation_data.get('reason', 'Customer request')
        subscription_id = cancellation_data.get('subscription_id', 'N/A')
        
        # Check if cancellation emails are enabled
        send_cancellation_email = POSSetting.get_setting('membership_cancellation_email_enabled', True)
        
        if send_cancellation_email:
            note_body = f"""❌ MEMBERSHIP CANCELLED

Cancellation Date: {timezone.now().strftime('%m/%d/%Y')}
Reason: {reason}
Subscription ID: {subscription_id}

Membership has been cancelled and billing has been stopped."""
            
            note_success = add_ghl_note(ghl_contact_id, note_body)
            if note_success:
                logger.info("✅ Added membership cancellation note to GHL (cancellation email enabled)")
        else:
            logger.info("⏭️ Skipped membership cancellation note (cancellation email disabled)")
        
        results['success'] = len(results['fields_updated']) > 0
        logger.info(f"🎯 GHL membership cancellation sync completed: {len(results['fields_updated'])} fields updated")
        
    except Exception as e:
        results['errors'].append(f"Error syncing cancellation: {str(e)}")
        logger.error(f"Error syncing membership cancellation: {str(e)}")
    
    return results

def sync_membership_pause_to_ghl(customer_email, pause_data):
    """
    Sync membership pause/hold to GHL
    """
    logger.info(f"⏸️ Starting GHL membership pause sync for {customer_email}")
    
    results = {
        'success': False,
        'fields_updated': [],
        'errors': []
    }
    
    try:
        ghl_contact_id = get_ghl_contact_id_by_email(customer_email)
        if not ghl_contact_id:
            results['errors'].append(f"GHL contact not found for email: {customer_email}")
            return results
        
        # Update member status and clear next charge
        field_updates = {
            'member_status': 'Paused',
            'next_charge': ''  # Clear next charge date while paused
        }
        
        # Update fields
        for field_name, field_value in field_updates.items():
            field_id = GHL_MEMBERSHIP_FIELDS.get(field_name)
            if field_id:
                success = update_ghl_contact_custom_field(ghl_contact_id, field_id, field_value)
                if success:
                    results['fields_updated'].append(f"{field_name}: {field_value}")
                else:
                    results['errors'].append(f"Failed to update {field_name}")
        
        # Add pause note
        reason = pause_data.get('reason', 'Customer request')
        subscription_id = pause_data.get('subscription_id', 'N/A')
        
        note_body = f"""⏸️ MEMBERSHIP PAUSED

Pause Date: {timezone.now().strftime('%m/%d/%Y')}
Reason: {reason}
Subscription ID: {subscription_id}

Membership has been paused and billing has been suspended."""
        
        note_success = add_ghl_note(ghl_contact_id, note_body)
        if note_success:
            logger.info("✅ Added membership pause note to GHL")
        
        results['success'] = len(results['fields_updated']) > 0
        logger.info(f"🎯 GHL membership pause sync completed: {len(results['fields_updated'])} fields updated")
        
    except Exception as e:
        results['errors'].append(f"Error syncing pause: {str(e)}")
        logger.error(f"Error syncing membership pause: {str(e)}")
    
    return results

def sync_membership_reactivation_to_ghl(customer_email, reactivation_data):
    """
    Sync membership reactivation/resume to GHL
    """
    logger.info(f"▶️ Starting GHL membership reactivation sync for {customer_email}")
    
    results = {
        'success': False,
        'fields_updated': [],
        'errors': []
    }
    
    try:
        ghl_contact_id = get_ghl_contact_id_by_email(customer_email)
        if not ghl_contact_id:
            results['errors'].append(f"GHL contact not found for email: {customer_email}")
            return results
        
        # Calculate next billing date from reactivation
        billing_interval = int(reactivation_data.get('billing_interval', 1))
        billing_period = reactivation_data.get('billing_period', 'month')
        reactivation_date = timezone.now()
        next_charge = calculate_next_billing_date(billing_interval, billing_period, reactivation_date)
        
        # Update member status and billing info
        field_updates = {
            'member_status': 'Active Member',
            'membership_reactivation': reactivation_date.strftime('%m/%d/%Y'),
            'next_charge': next_charge.strftime('%m/%d/%Y')
        }
        
        # Update fields
        for field_name, field_value in field_updates.items():
            field_id = GHL_MEMBERSHIP_FIELDS.get(field_name)
            if field_id:
                success = update_ghl_contact_custom_field(ghl_contact_id, field_id, field_value)
                if success:
                    results['fields_updated'].append(f"{field_name}: {field_value}")
                else:
                    results['errors'].append(f"Failed to update {field_name}")
        
        # Add reactivation note
        subscription_id = reactivation_data.get('subscription_id', 'N/A')
        
        note_body = f"""▶️ MEMBERSHIP REACTIVATED

Reactivation Date: {reactivation_date.strftime('%m/%d/%Y')}
Next Charge: {next_charge.strftime('%m/%d/%Y')}
Subscription ID: {subscription_id}

Membership has been reactivated and billing has resumed."""
        
        note_success = add_ghl_note(ghl_contact_id, note_body)
        if note_success:
            logger.info("✅ Added membership reactivation note to GHL")
        
        results['success'] = len(results['fields_updated']) > 0
        logger.info(f"🎯 GHL membership reactivation sync completed: {len(results['fields_updated'])} fields updated")
        
    except Exception as e:
        results['errors'].append(f"Error syncing reactivation: {str(e)}")
        logger.error(f"Error syncing membership reactivation: {str(e)}")
    
    return results

def extract_customer_email_from_order_data(order_data):
    """
    Extract customer email from order data (various formats)
    """
    # Try different possible locations for email
    email = None
    
    # Direct email field
    if 'email' in order_data:
        email = order_data['email']
    
    # Customer object
    elif 'customer' in order_data and order_data['customer']:
        customer = order_data['customer']
        if isinstance(customer, dict):
            email = customer.get('email')
        else:
            # Customer might be a string email
            email = customer if '@' in str(customer) else None
    
    # Billing info
    elif 'billing' in order_data and order_data['billing']:
        email = order_data['billing'].get('email')
    
    # Customer email field
    elif 'customer_email' in order_data:
        email = order_data['customer_email']
    
    return email

def extract_billing_schedule_from_order_data(order_data):
    """
    Extract billing schedule information from order/subscription data
    """
    billing_schedule = {
        'billing_interval': 1,
        'billing_period': 'month'
    }
    
    # Check for subscription metadata
    if 'subscription_schedule' in order_data:
        schedule = order_data['subscription_schedule']
        billing_schedule['billing_interval'] = schedule.get('interval', 1)
        billing_schedule['billing_period'] = schedule.get('period', 'month')
    
    # Check line items for subscription info
    elif 'line_items' in order_data:
        for item in order_data['line_items']:
            if 'meta_data' in item:
                for meta in item['meta_data']:
                    if meta.get('key') == '_subscription_period':
                        billing_schedule['billing_period'] = meta.get('value', 'month')
                    elif meta.get('key') == '_subscription_period_interval':
                        billing_schedule['billing_interval'] = int(meta.get('value', 1))
    
    return billing_schedule
