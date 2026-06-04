"""
WooCommerce Order Webhook Handlers
Handles order status updates, cancellations, and synchronization with POS orders
"""

import json
import logging
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from .models import POSOrder, WebhookLog
from .serializers import POSOrderSerializer
from .utils.webhook_signature import verify_woocommerce_signature, check_webhook_duplicate
from .woocommerce import WooCommerceAPI
from .order_completion import validate_woocommerce_order_can_complete

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def woocommerce_order_webhook(request):
    """
    WooCommerce Order Status Webhook Handler
    Handles order status changes and syncs them with corresponding POS orders
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
        webhook_type='order',
        status='received',
        source_ip=request.META.get('REMOTE_ADDR'),
        user_agent=request.META.get('HTTP_USER_AGENT', ''),
        request_body=request.body.decode('utf-8') if request.body else ''
    )
    
    try:
        webhook_log.status = 'processing'
        webhook_log.save()
        
        # Parse webhook data
        try:
            # Handle empty request body (common for WooCommerce test requests)
            if not request.body:
                logger.info("🔔 Empty webhook request received (likely WooCommerce test/ping)")
                webhook_log.status = 'success'
                webhook_log.action_taken = "Empty test request"
                webhook_log.save()
                return JsonResponse({
                    'success': True, 
                    'message': 'Webhook endpoint is working - empty test request received'
                })
            
            # Try to parse as JSON first
            try:
                webhook_data = json.loads(request.body)
                logger.info(f"🔔 WooCommerce Order Webhook received (JSON): {webhook_data.get('id', 'Unknown')}")
            except json.JSONDecodeError:
                # If JSON parsing fails, check if it's form-encoded data
                body_str = request.body.decode('utf-8')
                if 'webhook_id=' in body_str:
                    logger.info("🔔 WooCommerce webhook test/ping received (form-encoded)")
                    webhook_log.status = 'success'
                    webhook_log.action_taken = "Form-encoded test"
                    webhook_log.save()
                    return JsonResponse({
                        'success': True,
                        'message': 'Webhook endpoint is working - form-encoded test received'
                    })
                else:
                    # Unknown format
                    logger.error(f"❌ Invalid webhook data format")
                    logger.error(f"Request body content: {request.body[:200]}...")  # Log first 200 chars
                    webhook_log.status = 'failed'
                    webhook_log.error_message = "Invalid data format (not JSON or form-encoded)"
                    webhook_log.save()
                    return JsonResponse({'error': 'Invalid data format'}, status=400)
                    
        except Exception as e:
            logger.error(f"❌ Error parsing webhook data: {e}")
            webhook_log.status = 'failed'
            webhook_log.error_message = str(e)
            webhook_log.save()
            return JsonResponse({'error': str(e)}, status=400)
        
        # Extract order data
        order_id = webhook_data.get('id')
        order_status = webhook_data.get('status')
        
        if not order_id or not order_status:
            error_msg = f"Missing required fields: id={order_id}, status={order_status}"
            logger.error(f"❌ {error_msg}")
            webhook_log.status = 'failed'
            webhook_log.error_message = error_msg
            webhook_log.save()
            return JsonResponse({'error': error_msg}, status=400)
        
        # Log order details
        logger.info(f"📦 Processing order {order_id} status change to: {order_status}")
        
        # Update webhook log with order details
        webhook_log.action_taken = f"Status change to {order_status}"[:50]
        webhook_log.save()
        
        # 🔥 NEW: Check for membership/subscription lifecycle events
        membership_sync_result = process_membership_lifecycle_event(webhook_data)
        if membership_sync_result:
            logger.info(f"🎯 Membership lifecycle event processed: {membership_sync_result}")
        
        # Find corresponding POS order
        pos_order = find_corresponding_pos_order(webhook_data)
        
        if pos_order:
            logger.info(f"🎯 Found corresponding POS order: {pos_order.id}")
            
            # Normalize WooCommerce status (remove 'wc-' prefix if present)
            new_pos_status = order_status.replace('wc-', '') if order_status else None
            
            # Only update if status changed and new status is valid
            if new_pos_status and pos_order.status != new_pos_status:
                if new_pos_status == 'completed':
                    wc_api = WooCommerceAPI()
                    completion_check = validate_woocommerce_order_can_complete(wc_api, webhook_data)
                    if not completion_check.get("eligible"):
                        message = (
                            f"Skipped completion sync for POS order {pos_order.id}: "
                            f"{completion_check.get('message')}"
                        )
                        logger.warning(message)
                        webhook_log.status = 'skipped'
                        webhook_log.action_taken = "Completion blocked: tracking incomplete"
                        webhook_log.error_message = completion_check.get("code", "TRACKING_COVERAGE_INCOMPLETE")
                        webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
                        webhook_log.save()
                        return JsonResponse(
                            {
                                "success": True,
                                "skipped": True,
                                "message": message,
                                "completion_validation": completion_check,
                            }
                        )

                old_status = pos_order.status
                pos_order.status = new_pos_status
                
                # Update completed_at timestamp if marking as completed
                if new_pos_status == 'completed' and old_status != 'completed':
                    pos_order.completed_at = timezone.now()
                
                pos_order.save()
                
                logger.info(f"✅ Updated POS order {pos_order.id}: {old_status} → {new_pos_status}")
                
                # Update webhook log with success details
                webhook_log.status = 'success'
                webhook_log.updated_fields = f"status: {old_status} → {new_pos_status}"
                webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
                webhook_log.save()
                
                return JsonResponse({
                    'success': True,
                    'message': f'POS order {pos_order.id} updated to {new_pos_status}',
                    'pos_order_id': str(pos_order.id),
                    'old_status': old_status,
                    'new_status': new_pos_status
                })
            else:
                message = f"No status update needed for POS order {pos_order.id} (current: {pos_order.status}, woo: {order_status})"
                logger.info(f"ℹ️ {message}")
                
                webhook_log.status = 'skipped'
                webhook_log.action_taken = f"POS order exists - no status change ({pos_order.status})"
                webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
                webhook_log.save()
                
                return JsonResponse({
                    'success': True,
                    'message': message,
                    'skipped': True
                })
        else:
            # No corresponding POS order found - check if we should create one
            logger.info(f"ℹ️ No corresponding POS order found for WooCommerce order {order_id}")
            
            # Check if this is a new order creation (not just a status update)
            if should_create_pos_order_from_woocommerce(webhook_data, order_status):
                logger.info(f"🔧 Creating new POS order from WooCommerce order {order_id}")
                
                try:
                    new_pos_order = create_pos_order_from_woocommerce(webhook_data)
                    
                    if new_pos_order:
                        logger.info(f"✅ Created new POS order {new_pos_order.id} from WooCommerce order {order_id}")
                        
                        # 📧 AUTO-SEND RECEIPT: For WooCommerce website orders (non-POS),
                        # send the branded Django POS receipt email to the customer.
                        # POS-originated orders are already filtered out by
                        # should_create_pos_order_from_woocommerce(), so this only fires
                        # for orders placed on the WooCommerce website.
                        try:
                            _send_woo_website_order_receipt(webhook_data, order_id)
                        except Exception as email_err:
                            logger.error(f"📧 ❌ Auto-receipt failed for WC order {order_id}: {email_err}")
                        
                        webhook_log.status = 'success'
                        webhook_log.action_taken = "Created POS order"
                        webhook_log.updated_fields = f"New POS order created with status: {new_pos_order.status}"
                        webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
                        webhook_log.save()
                        
                        return JsonResponse({
                            'success': True,
                            'message': f'Created new POS order {new_pos_order.id} from WooCommerce order {order_id}',
                            'pos_order_id': str(new_pos_order.id),
                            'pos_order_created': True,
                            'order_status': new_pos_order.status
                        })
                    else:
                        message = f"Failed to create POS order from WooCommerce order {order_id}"
                        logger.warning(f"⚠️ {message}")
                        
                        webhook_log.status = 'failed'
                        webhook_log.action_taken = "Failed to create POS order"
                        webhook_log.error_message = "POS order creation returned None"
                        webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
                        webhook_log.save()
                        
                        return JsonResponse({
                            'success': False,
                            'message': message,
                            'creation_failed': True
                        })
                        
                except Exception as e:
                    error_msg = f"Error creating POS order from WooCommerce order {order_id}: {str(e)}"
                    logger.error(f"❌ {error_msg}")
                    
                    webhook_log.status = 'failed'
                    webhook_log.action_taken = "Attempted to create POS order"
                    webhook_log.error_message = error_msg
                    webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
                    webhook_log.save()
                    
                    return JsonResponse({
                        'success': False,
                        'message': error_msg,
                        'creation_error': True
                    })
            else:
                message = f"Skipping POS order creation for WooCommerce order {order_id} (status: {order_status})"
                logger.info(f"ℹ️ {message}")
                
                # Detect reason for skipping
                created_via = webhook_data.get('created_via', '')
                meta_data = webhook_data.get('meta_data', [])
                has_pos_transaction = any(m.get('key') == '_pos_transaction_id' for m in meta_data)
                has_pos_created_via = any(m.get('key') == '_created_via' and str(m.get('value', '')).lower() in ('pos', 'ds pos') for m in meta_data)
                has_pos_order_id = any(m.get('key') == '_pos_order_id' and m.get('value') for m in meta_data)
                
                if has_pos_transaction or has_pos_created_via or has_pos_order_id or created_via in ['pos', 'POS', 'DS POS', 'ds pos']:
                    skip_reason = "POS-originated order (circular sync prevented)"
                else:
                    skip_reason = f"Invalid status for POS creation ({order_status})"
                
                webhook_log.status = 'skipped'
                webhook_log.action_taken = skip_reason
                webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
                webhook_log.save()
                
                return JsonResponse({
                    'success': True,
                    'message': message,
                    'no_pos_order': True,
                    'creation_skipped': True
                })
            
    except Exception as e:
        logger.error(f"❌ Error processing order webhook: {e}")
        
        webhook_log.status = 'failed'
        webhook_log.error_message = str(e)
        webhook_log.processing_time_ms = int((timezone.now() - webhook_log.received_at).total_seconds() * 1000)
        webhook_log.save()
        
        return JsonResponse({'error': str(e)}, status=500)


def process_membership_lifecycle_event(webhook_data):
    """
    Process membership/subscription lifecycle events for GHL sync
    """
    from .ghl_membership_sync import (
        is_membership_product,
        sync_membership_renewal_to_ghl,
        sync_membership_cancellation_to_ghl,
        extract_customer_email_from_order_data,
        extract_billing_schedule_from_order_data
    )
    
    try:
        order_status = webhook_data.get('status')
        order_id = webhook_data.get('id')
        
        # Extract customer email
        customer_email = extract_customer_email_from_order_data(webhook_data)
        if not customer_email:
            logger.warning(f"No customer email found in order {order_id}")
            return None
        
        # Check if this order contains membership products
        has_membership = False
        line_items = webhook_data.get('line_items', [])
        
        for item in line_items:
            product_name = item.get('name', '')
            if is_membership_product(product_name):
                has_membership = True
                logger.info(f"🎯 Found membership product in order {order_id}: {product_name}")
                break
        
        if not has_membership:
            return None
        
        # Process different lifecycle events
        if order_status == 'completed':
            # Check if this is a renewal (not initial purchase)
            if is_subscription_renewal(webhook_data):
                logger.info(f"🔄 Processing subscription renewal for order {order_id}")
                
                # Extract renewal data
                renewal_data = {
                    'amount_charged': float(webhook_data.get('total', 0)),
                    'order_id': order_id,
                    'subscription_id': extract_subscription_id_from_order(webhook_data)
                }
                
                # Add billing schedule info
                billing_schedule = extract_billing_schedule_from_order_data(webhook_data)
                renewal_data.update(billing_schedule)
                
                # Sync renewal to GHL
                result = sync_membership_renewal_to_ghl(customer_email, renewal_data)
                return f"Renewal sync: {result}"
        
        elif order_status == 'cancelled':
            logger.info(f"❌ Processing membership cancellation for order {order_id}")
            
            cancellation_data = {
                'reason': 'Order cancelled',
                'order_id': order_id,
                'subscription_id': extract_subscription_id_from_order(webhook_data)
            }
            
            # Sync cancellation to GHL
            result = sync_membership_cancellation_to_ghl(customer_email, cancellation_data)
            return f"Cancellation sync: {result}"
        
        return None
        
    except Exception as e:
        logger.error(f"Error processing membership lifecycle event: {str(e)}")
        return f"Error: {str(e)}"

def is_subscription_renewal(webhook_data):
    """
    Determine if this order is a subscription renewal vs initial purchase
    """
    # Check for subscription metadata indicating renewal
    meta_data = webhook_data.get('meta_data', [])
    for meta in meta_data:
        if meta.get('key') == '_subscription_renewal':
            return meta.get('value', False)
        elif meta.get('key') == 'is_vat_exempt' and meta.get('value') == 'yes':
            # Some renewals have this marker
            return True
    
    # Check for subscription ID in meta_data (renewals usually have this)
    subscription_id = extract_subscription_id_from_order(webhook_data)
    if subscription_id and subscription_id != 'N/A':
        # If we have a subscription ID, check if this is not the first order for that subscription
        # This is a heuristic - in production you might want to check against subscription creation date
        return True
    
    return False

def extract_subscription_id_from_order(webhook_data):
    """
    Extract subscription ID from order data
    """
    # Check meta_data for subscription ID
    meta_data = webhook_data.get('meta_data', [])
    for meta in meta_data:
        if meta.get('key') in ['_subscription_id', 'subscription_id']:
            return meta.get('value', 'N/A')
    
    # Check line items for subscription references
    line_items = webhook_data.get('line_items', [])
    for item in line_items:
        item_meta = item.get('meta_data', [])
        for meta in item_meta:
            if meta.get('key') in ['_subscription_id', 'subscription_id']:
                return meta.get('value', 'N/A')
    
    return 'N/A'


def find_corresponding_pos_order(webhook_data):
    """
    Find the corresponding POS order for a WooCommerce order
    Uses multiple strategies to find the matching order
    """
    order_id = webhook_data.get('id')
    
    try:
        meta_data = webhook_data.get('meta_data', [])
        
        # Strategy 0: Look for _pos_order_id (POS UUID) in WC order meta
        # This is the most reliable match — POS always sets this, even for cash orders
        for meta in meta_data:
            if meta.get('key') == '_pos_order_id' and meta.get('value'):
                try:
                    pos_order = POSOrder.objects.get(id=meta['value'])
                    logger.info(f"🔍 Found POS order by _pos_order_id: {meta['value']}")
                    return pos_order
                except POSOrder.DoesNotExist:
                    logger.info(f"🔍 _pos_order_id {meta['value']} not found in DB")
        
        # Strategy 1: Look for POS transaction ID in order metadata
        pos_transaction_id = None
        
        # Check meta_data for _pos_transaction_id
        for meta in meta_data:
            if meta.get('key') == '_pos_transaction_id':
                pos_transaction_id = meta.get('value')
                logger.info(f"🔍 Found POS transaction ID in meta_data: {pos_transaction_id}")
                break
        
        # Strategy 2: Check transaction_id field directly
        if not pos_transaction_id:
            pos_transaction_id = webhook_data.get('transaction_id')
            if pos_transaction_id:
                logger.info(f"🔍 Found POS transaction ID in transaction_id field: {pos_transaction_id}")
        
        if pos_transaction_id:
            # Search by transaction_id
            pos_orders = POSOrder.objects.filter(transaction_id=pos_transaction_id)
            if pos_orders.exists():
                return pos_orders.first()
        
        # Strategy 3: Look for WooCommerce order ID in POS order metadata
        pos_orders_with_woo_id = POSOrder.objects.filter(
            metadata__woo_order_id=str(order_id)
        )
        if pos_orders_with_woo_id.exists():
            logger.info(f"🔍 Found POS order by WooCommerce order ID in metadata: {order_id}")
            return pos_orders_with_woo_id.first()
        
        # Strategy 4: Search by order number if it matches a pattern
        order_number = webhook_data.get('number')
        if order_number:
            pos_orders_by_number = POSOrder.objects.filter(order_number=order_number)
            if pos_orders_by_number.exists():
                logger.info(f"🔍 Found POS order by order number: {order_number}")
                return pos_orders_by_number.first()
        
        logger.info(f"🔍 No corresponding POS order found for WooCommerce order {order_id}")
        return None
        
    except Exception as e:
        logger.error(f"❌ Error finding corresponding POS order: {e}")
        return None


@csrf_exempt
@require_http_methods(["POST"])
def woocommerce_order_webhook_test(request):
    """
    Test endpoint for WooCommerce order webhooks
    """
    try:
        # Handle empty request body gracefully
        webhook_data = json.loads(request.body) if request.body else {}
        
        # Determine if this is an empty test request
        test_type = "Empty test request" if not request.body else "Test order webhook received"
        
        # Create test webhook log
        webhook_log = WebhookLog.objects.create(
            webhook_type='test',
            status='success',
            source_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            request_body=request.body.decode('utf-8') if request.body else '',
            action_taken=test_type
        )
        
        logger.info(f"🧪 Test order webhook received: {webhook_data}")
        
        return JsonResponse({
            'success': True,
            'message': 'Order webhook test received successfully',
            'webhook_log_id': str(webhook_log.id),
            'received_data': webhook_data
        })
        
    except Exception as e:
        logger.error(f"❌ Error in test order webhook: {e}")
        return JsonResponse({'error': str(e)}, status=500)


def should_create_pos_order_from_woocommerce(webhook_data, order_status):
    """
    Determine if we should create a POS order from this WooCommerce order
    """
    # Only create POS orders for certain statuses
    valid_statuses = ['processing', 'completed', 'pending']
    
    if order_status not in valid_statuses:
        logger.info(f"🚫 Skipping POS order creation for status: {order_status}")
        return False
    
    # Check if this order was originally created by POS (avoid circular creation)
    meta_data = webhook_data.get('meta_data', [])
    for meta in meta_data:
        meta_key = meta.get('key', '')
        meta_value = meta.get('value', '')
        if meta_key == '_created_via' and str(meta_value).lower() in ('pos', 'ds pos'):
            logger.info(f"🚫 Skipping POS order creation for POS-originated order (_created_via={meta_value})")
            return False
        elif meta_key == '_pos_transaction_id':
            logger.info(f"🚫 Skipping POS order creation for order with POS transaction ID")
            return False
        elif meta_key == '_pos_order_id' and meta_value:
            logger.info(f"🚫 Skipping POS order creation for order with _pos_order_id={meta_value}")
            return False
    
    # Check if created_via field indicates POS origin
    created_via = webhook_data.get('created_via', '')
    if created_via in ['pos', 'POS', 'DS POS', 'ds pos']:
        logger.info(f"🚫 Skipping POS order creation for created_via: {created_via}")
        return False
    
    logger.info(f"✅ Should create POS order for WooCommerce order (status: {order_status})")
    return True


def create_pos_order_from_woocommerce(webhook_data):
    """
    Create a new POS order from WooCommerce order data
    """
    from .models import Contact, POSOrderItem
    from django.utils.dateparse import parse_datetime
    import uuid
    
    try:
        # Extract order data
        woo_order_id = webhook_data.get('id')
        order_number = f"WOO-{woo_order_id}"
        order_status = webhook_data.get('status', 'processing')
        total = float(webhook_data.get('total', 0))
        order_date = webhook_data.get('date_created', timezone.now().isoformat())
        
        # Parse the order date and make timezone-aware
        if isinstance(order_date, str):
            try:
                parsed_date = parse_datetime(order_date)
                if parsed_date:
                    # If parsed_date is naive, make it timezone-aware
                    if parsed_date.tzinfo is None:
                        parsed_date = timezone.make_aware(parsed_date)
                    order_date = parsed_date
                else:
                    order_date = timezone.now()
            except:
                order_date = timezone.now()
        else:
            order_date = timezone.now()
        
        logger.info(f"📦 Creating POS order {order_number} with total ${total}")
        
        # Find or create customer
        customer = None
        customer_data = webhook_data.get('billing', {}) or webhook_data.get('shipping', {})
        
        if customer_data:
            email = customer_data.get('email') or webhook_data.get('customer_email', '')
            first_name = customer_data.get('first_name', '')
            last_name = customer_data.get('last_name', '')
            
            if email:
                # Try to find existing customer by email first
                try:
                    customer = Contact.objects.get(email=email)
                    logger.info(f"👤 Found existing customer by email: {customer.email}")
                    
                    # Update WooCommerce customer ID if not set
                    woo_customer_id = webhook_data.get('customer_id')
                    if woo_customer_id and not customer.woo_customer_id:
                        customer.woo_customer_id = woo_customer_id
                        customer.save()
                        logger.info(f"👤 Updated customer {customer.email} with WooCommerce ID: {woo_customer_id}")
                        
                except Contact.DoesNotExist:
                    # Try to find by WooCommerce customer ID
                    woo_customer_id = webhook_data.get('customer_id')
                    if woo_customer_id:
                        try:
                            customer = Contact.objects.get(woo_customer_id=woo_customer_id)
                            logger.info(f"👤 Found existing customer by WooCommerce ID: {customer.email}")
                        except Contact.DoesNotExist:
                            # Create new customer
                            customer = Contact.objects.create(
                                email=email,
                                first_name=first_name,
                                last_name=last_name,
                                billing_address=customer_data.get('address_1', ''),
                                billing_city=customer_data.get('city', ''),
                                billing_state=customer_data.get('state', ''),
                                billing_postcode=customer_data.get('postcode', ''),
                                billing_country=customer_data.get('country', ''),
                                phone=customer_data.get('phone', ''),
                                woo_customer_id=woo_customer_id
                            )
                            logger.info(f"👤 Created new customer: {customer.email}")
                    else:
                        # Create customer without WooCommerce ID
                        customer = Contact.objects.create(
                            email=email,
                            first_name=first_name,
                            last_name=last_name,
                            billing_address=customer_data.get('address_1', ''),
                            billing_city=customer_data.get('city', ''),
                            billing_state=customer_data.get('state', ''),
                            billing_postcode=customer_data.get('postcode', ''),
                            billing_country=customer_data.get('country', '')
                        )
                        logger.info(f"👤 Created new customer without WooCommerce ID: {customer.email}")
        
        # Map WooCommerce status to POS status
        status_mapping = {
            'pending': 'pending',
            'processing': 'processing',
            'completed': 'completed',
            'cancelled': 'cancelled',
            'refunded': 'refunded',
            'on-hold': 'processing',
            'failed': 'cancelled'
        }
        pos_status = status_mapping.get(order_status, 'processing')
        
        # Extract payment method
        payment_method_title = webhook_data.get('payment_method_title', 'WooCommerce Order')
        
        # Create POS order
        pos_order = POSOrder.objects.create(
            order_number=order_number,
            contact=customer,
            total=total,
            status=pos_status,
            payment_method_title=payment_method_title,
            created_at=order_date,
            updated_at=timezone.now(),
            completed_at=timezone.now() if pos_status == 'completed' else None,
            notes=f"Created from WooCommerce order #{woo_order_id}",
            metadata={
                'woo_order_id': str(woo_order_id),
                'created_via': 'woocommerce_webhook',
                'source': 'woocommerce',
                'original_order_date': order_date.isoformat() if hasattr(order_date, 'isoformat') else str(order_date)
            }
        )
        
        logger.info(f"📦 Created POS order {pos_order.id} ({order_number})")
        
        # Create order items
        line_items = webhook_data.get('line_items', [])
        for item_data in line_items:
            try:
                create_pos_order_item_from_woocommerce(pos_order, item_data)
            except Exception as e:
                logger.error(f"❌ Error creating order item: {e}")
                # Continue with other items
        
        logger.info(f"✅ Successfully created POS order {pos_order.id} with {len(line_items)} items")
        return pos_order
        
    except Exception as e:
        logger.error(f"❌ Error creating POS order from WooCommerce data: {e}")
        return None


def create_pos_order_item_from_woocommerce(pos_order, item_data):
    """
    Create a POS order item from WooCommerce line item data
    """
    from .models import POSOrderItem, Product
    
    try:
        # Extract item data
        product_id = item_data.get('product_id')
        variation_id = item_data.get('variation_id', 0)
        name = item_data.get('name', 'Unknown Product')
        quantity = int(item_data.get('quantity', 1))
        price = float(item_data.get('price', 0))
        total = float(item_data.get('total', 0))
        
        logger.info(f"📝 Creating order item: {name} (qty: {quantity}, price: ${price})")
        
        # Try to find the product in our system
        product_uuid = None
        if product_id:
            try:
                # Try to find by WooCommerce product ID
                product = Product.objects.get(woo_product_id=product_id)
                product_uuid = str(product.id)  # Store UUID as string
                logger.info(f"🎯 Found matching product: {product.name}")
            except Product.DoesNotExist:
                logger.info(f"⚠️ Product not found in POS system (WooCommerce ID: {product_id})")
        
        # Create the order item
        order_item = POSOrderItem.objects.create(
            order=pos_order,
            product_id=product_uuid,  # UUID string, may be None if product not found
            name=name,
            quantity=quantity,
            price=price,
            subtotal=total,
            metadata={
                'woo_product_id': product_id,
                'woo_variation_id': variation_id,
                'source': 'woocommerce',
                'original_item_data': item_data  # Store original data for reference
            }
        )
        
        logger.info(f"✅ Created order item: {order_item.id}")
        return order_item
        
    except Exception as e:
        logger.error(f"❌ Error creating POS order item: {e}")
        raise


def _send_woo_website_order_receipt(webhook_data, woo_order_id):
    """
    Send the branded Django POS receipt email for a WooCommerce website order.

    This is called automatically when a non-POS order is received via webhook.
    Uses the same receipt template as POS orders (teal-branded HTML + PDF attachment).

    Dedup: skips if a receipt was already sent for this WooCommerce order ID
    (e.g. the POS frontend already triggered send_receipt for this order).
    """
    from django.core.cache import cache
    from .views_receipts import (
        _build_order_receipt_html,
        _build_pdf_receipt_html,
        _html_to_pdf,
        _enrich_order_subscription_frequency,
    )
    from .email_sender import send_pos_email

    # ── Skip POS-originated orders (they send their own receipt via the frontend) ──
    # This prevents duplicate emails caused by a race condition where the webhook
    # arrives before find_corresponding_pos_order() can find the POS order in the DB.
    created_via = webhook_data.get('created_via', '')
    if created_via in ('pos', 'POS', 'DS POS', 'ds pos'):
        logger.info(f"📧 ⏭️ Skipping receipt for POS-originated order {woo_order_id} (created_via={created_via})")
        return
    meta_data = webhook_data.get('meta_data', [])
    for meta in meta_data:
        key = meta.get('key', '')
        val = meta.get('value', '')
        if key == '_created_via' and str(val).lower() in ('pos', 'ds pos'):
            logger.info(f"📧 ⏭️ Skipping receipt for POS-originated order {woo_order_id} (_created_via={val})")
            return
        if key in ('_pos_transaction_id', '_pos_order_id') and val:
            logger.info(f"📧 ⏭️ Skipping receipt for POS-originated order {woo_order_id} ({key})")
            return

    # ── Extract customer email ──
    billing = webhook_data.get('billing', {})
    customer_email = billing.get('email', '')
    if not customer_email:
        logger.info(f"📧 ⏭️ No billing email on WC order {woo_order_id}, skipping receipt")
        return

    # ── Dedup: check if receipt was already sent (POS frontend or previous webhook) ──
    dedup_key = f"receipt_email_sent_{woo_order_id}"
    if cache.get(dedup_key):
        logger.info(f"📧 ⏭️ Dedup: receipt already sent for WC order {woo_order_id}, skipping")
        return
    cache.set(dedup_key, True, 600)  # 10 min window

    # ── Build receipt from webhook data (already full WC order JSON) ──
    order_number = webhook_data.get('number', woo_order_id)
    _enrich_order_subscription_frequency(webhook_data)
    html_content = _build_order_receipt_html(webhook_data)
    text_content = f'Your Doctors Studio Receipt — Order #{order_number}'
    subject = f'Your Doctors Studio Receipt — Order #{order_number}'

    # ── Generate PDF attachment ──
    pdf_html = _build_pdf_receipt_html(webhook_data, is_refund=False)
    pdf_bytes = _html_to_pdf(pdf_html)
    attachments = [(f'Receipt_Order_{order_number}.pdf', pdf_bytes, 'application/pdf')] if pdf_bytes else None

    if not pdf_bytes:
        logger.warning(f"📧 PDF generation failed for WC order #{order_number}, sending without attachment")

    # ── Send via unified email sender (uses woo_order_receipt toggle) ──
    sent = send_pos_email(
        subject=subject,
        text_content=text_content,
        html_content=html_content,
        to_email=customer_email,
        email_type='woo_order_receipt',
        attachments=attachments,
        related_order=str(order_number),
    )

    if sent:
        logger.info(f"📧 ✅ Auto-sent WooCommerce website order receipt #{order_number} to {customer_email}")
    else:
        logger.warning(f"📧 ❌ Failed to auto-send receipt for WC order #{order_number} to {customer_email}")
