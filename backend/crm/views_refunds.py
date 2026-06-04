from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
import time
from .models import POSOrder, POSOrderItem, POSOrderRefund, POSOrderRefundItem
from django.db.models import Q, Sum
from .woocommerce import WooCommerceAPI
import logging
import uuid
import time
from django.utils import timezone
from django.db import transaction
import json
from datetime import timedelta

# Authorize.Net refund eligibility window (180 days)
# Simulate the refund window change it to 0
AUTHORIZE_NET_REFUND_WINDOW_DAYS = 180

logger = logging.getLogger(__name__)


def _get_friendly_authorize_error(error_code, error_text):
    """
    Translate Authorize.Net error codes into human-friendly messages.
    Returns actionable guidance so staff knows what to do next.
    """
    error_code_str = str(error_code)
    
    friendly_messages = {
        # Transaction cannot be found / expired
        '16': (
            'The original transaction cannot be found in Authorize.Net. '
            'It may have been archived (older than 180 days) or the transaction ID is incorrect. '
            'Please issue a store credit or check instead.'
        ),
        # The referenced transaction does not meet the criteria for issuing a credit
        '54': (
            'Authorize.Net rejected the refund: the original transaction does not meet refund criteria. '
            'This typically means the transaction is too old (over 180 days), already fully refunded, '
            'or was voided. Please issue a store credit, check, or other alternate refund method.'
        ),
        # Card number is required / invalid
        '6': (
            'The credit card number associated with this transaction is invalid or missing. '
            'The card may have been replaced or closed. '
            'Please issue a store credit or check instead.'
        ),
        # Transaction has already been voided
        '310': (
            'This transaction has already been voided and cannot be refunded. '
            'No charge was made to the customer\'s card.'
        ),
        # Original transaction has already been fully refunded
        '311': (
            'This transaction has already been fully refunded. '
            'No additional refund can be processed to the original payment method.'
        ),
    }
    
    if error_code_str in friendly_messages:
        return friendly_messages[error_code_str]
    
    # Default: include the raw error but add guidance
    return (
        f'Authorize.Net declined the refund: {error_text} (code: {error_code_str}). '
        f'If the refund cannot be processed to the original card, '
        f'please issue a store credit, check, or other alternate refund method.'
    )


def process_pos_payment_refund(order, refund_amount, refund_reason="", frontend_transaction_id="", frontend_card_last4=""):
    """
    Process actual payment refund via POS Authorize.Net gateway
    
    Args:
        order: POSOrder instance
        refund_amount: Amount to refund
        refund_reason: Reason for refund
        frontend_transaction_id: Transaction ID passed from frontend (optional)
        frontend_card_last4: Card last 4 digits passed from frontend (optional)
        
    Returns:
        dict with 'success' and 'message' keys
    """
    try:
        import os
        from authorizenet import apicontractsv1
        from authorizenet.apicontrollers import createTransactionController
        
        # Get POS Authorize.Net credentials from centralized secret management
        from core.secrets import get_secret
        api_login_id = get_secret('AUTHORIZE_NET_LOGIN_ID')
        transaction_key = get_secret('AUTHORIZE_NET_TRANSACTION_KEY')
        sandbox_mode = get_secret('AUTHORIZE_NET_SANDBOX', 'False').lower() == 'true'
        
        if not api_login_id or not transaction_key:
            return {
                'success': False,
                'message': 'POS Authorize.Net credentials not configured'
            }
        
        # PROACTIVE CHECK: Authorize.Net 180-day refund window
        # After 180 days, card data is archived and refunds cannot be returned to the original card.
        order_date = order.created_at
        if order_date:
            days_since_order = (timezone.now() - order_date).days
            if days_since_order >= AUTHORIZE_NET_REFUND_WINDOW_DAYS:
                logger.warning(
                    f"⏰ Refund blocked: Order {order.order_number} is {days_since_order} days old "
                    f"(limit: {AUTHORIZE_NET_REFUND_WINDOW_DAYS} days). "
                    f"Authorize.Net archives card data after 180 days."
                )
                return {
                    'success': False,
                    'message': (
                        f'This order is {days_since_order} days old and exceeds the 180-day refund window. '
                        f'Authorize.Net archives card data after 180 days, so refunds can no longer be '
                        f'returned to the original payment method. '
                        f'Please issue a store credit, check, or other alternate refund method.'
                    ),
                    'error_code': 'REFUND_WINDOW_EXPIRED',
                    'days_since_order': days_since_order,
                    'refund_window_days': AUTHORIZE_NET_REFUND_WINDOW_DAYS
                }
            else:
                days_remaining = AUTHORIZE_NET_REFUND_WINDOW_DAYS - days_since_order
                logger.info(f"✅ Order {order.order_number} is {days_since_order} days old ({days_remaining} days remaining in refund window)")
        
        # Get original transaction ID
        logger.info(f"🔍 Searching for transaction ID for order {order.order_number}")
        
        # PRIORITY 1: Use transaction ID from frontend (from WooCommerce order data)
        original_trans_id = frontend_transaction_id if frontend_transaction_id and not frontend_transaction_id.startswith('ORD-') else None
        if original_trans_id:
            logger.info(f"✅ Priority 1 - Using transaction_id from frontend: {original_trans_id}")
        else:
            logger.info(f"📋 Order metadata keys: {list(order.metadata.keys()) if order.metadata else 'None'}")
            
            # PRIORITY 2: Check POS order metadata
            original_trans_id = order.metadata.get('transaction_id') if order.metadata else None
            logger.info(f"Priority 2 - transaction_id from POS metadata: {original_trans_id}")
        
        # If it's an ORD-xxx or not found, use fallback methods
        if not original_trans_id or original_trans_id.startswith('ORD-'):
            logger.info(f"Primary transaction_id is order number or missing, checking fallback methods")
            
            # PRIORITY 2: Check other transaction ID fields in POS metadata
            if order.metadata:
                original_trans_id = order.metadata.get('authorize_net_trans_id') or \
                                  order.metadata.get('auth_net_transaction_id')
                logger.info(f"Priority 2 - Other POS metadata fields: {original_trans_id}")
            
            # PRIORITY 3: Check WooCommerce order metadata (if order is synced to WooCommerce)
            if (not original_trans_id or original_trans_id.startswith('ORD-')) and order.metadata and order.metadata.get('woo_order_id'):
                try:
                    from .woocommerce import WooCommerceAPI
                    wc = WooCommerceAPI()
                    woo_order_id = order.metadata.get('woo_order_id')
                    logger.info(f"Priority 3 - Fetching WooCommerce order {woo_order_id} for transaction ID")
                    
                    woo_order = wc.get_order(woo_order_id)
                    if woo_order:
                        # Check WooCommerce transaction_id field
                        woo_trans_id = woo_order.get('transaction_id', '')
                        if woo_trans_id and not woo_trans_id.startswith('ORD-'):
                            original_trans_id = woo_trans_id
                            logger.info(f"✅ Found transaction ID in WooCommerce order field: {woo_trans_id}")
                        else:
                            # Check WooCommerce metadata
                            woo_meta = woo_order.get('meta_data', [])
                            for meta in woo_meta:
                                if meta.get('key') == '_pos_transaction_id':
                                    trans_id = meta.get('value', '')
                                    if trans_id and not trans_id.startswith('ORD-'):
                                        original_trans_id = trans_id
                                        logger.info(f"✅ Found transaction ID in WooCommerce metadata: {trans_id}")
                                        break
                except Exception as woo_e:
                    logger.warning(f"Failed to fetch WooCommerce order for transaction ID: {str(woo_e)}")
            
            # PRIORITY 4 (FALLBACK): For old orders, parse _pos_payment_method metadata
            if (not original_trans_id or original_trans_id.startswith('ORD-')) and order.metadata:
                pos_payment_method = order.metadata.get('_pos_payment_method')
                if pos_payment_method:
                    try:
                        payment_data = json.loads(pos_payment_method) if isinstance(pos_payment_method, str) else pos_payment_method
                        
                        # Check if it's a split payment
                        if payment_data.get('type') == 'split':
                            split_payments = payment_data.get('splitPayments', [])
                            # Find the credit card payment
                            for split in split_payments:
                                method = split.get('method', {})
                                if method.get('type') == 'credit':
                                    # Extract transaction ID from credit card payment
                                    trans_id = method.get('id')
                                    if trans_id and not trans_id.startswith('ORD-'):
                                        original_trans_id = trans_id
                                        logger.info(f"✅ FALLBACK: Found credit card transaction ID in split payment metadata: {trans_id}")
                                        break
                        elif payment_data.get('type') == 'credit':
                            # Non-split credit card payment - transaction ID is at top level
                            trans_id = payment_data.get('id')
                            if trans_id and not trans_id.startswith('ORD-'):
                                original_trans_id = trans_id
                                logger.info(f"✅ FALLBACK: Found credit card transaction ID in payment metadata: {trans_id}")
                    except (json.JSONDecodeError, TypeError, AttributeError) as e:
                        logger.warning(f"Failed to parse _pos_payment_method metadata: {str(e)}")
        
        if not original_trans_id:
            logger.warning(f"No Authorize.Net transaction ID found for order {order.order_number}")
            return {
                'success': False,
                'message': 'No payment transaction ID found for this order (likely cash/manual payment)'
            }
        
        # Don't process if transaction ID is still an order number
        if original_trans_id.startswith('ORD-'):
            logger.warning(f"Transaction ID is order number, not Authorize.Net transaction: {original_trans_id}")
            return {
                'success': False,
                'message': 'No valid Authorize.Net transaction ID found (only order number available)'
            }
        
        # Set up merchant authentication
        merchantAuth = apicontractsv1.merchantAuthenticationType()
        merchantAuth.name = api_login_id
        merchantAuth.transactionKey = transaction_key
        
        # Step 1: Check transaction status to determine if we should VOID or REFUND
        logger.info(f"Checking transaction status for {original_trans_id}")
        
        from authorizenet.apicontrollers import getTransactionDetailsController
        
        getTransactionDetailsRequest = apicontractsv1.getTransactionDetailsRequest()
        getTransactionDetailsRequest.merchantAuthentication = merchantAuth
        getTransactionDetailsRequest.transId = original_trans_id
        
        getTransactionDetailsController_instance = getTransactionDetailsController(getTransactionDetailsRequest)
        
        if sandbox_mode:
            getTransactionDetailsController_instance.setenvironment('https://apitest.authorize.net/xml/v1/request.api')
        else:
            getTransactionDetailsController_instance.setenvironment('https://api.authorize.net/xml/v1/request.api')
        
        getTransactionDetailsController_instance.execute()
        
        details_response = getTransactionDetailsController_instance.getresponse()
        
        # Determine if transaction is settled
        is_settled = False
        transaction_status = "unknown"
        
        if details_response and details_response.messages.resultCode == "Ok":
            transaction_status = details_response.transaction.transactionStatus
            logger.info(f"Transaction status: {transaction_status}")
            
            # settledSuccessfully means we should REFUND
            # authorizedPendingCapture, capturedPendingSettlement means we should VOID
            if transaction_status == "settledSuccessfully":
                is_settled = True
        else:
            logger.warning(f"Could not get transaction details, assuming settled and attempting refund")
            is_settled = True  # Default to refund if we can't check
        
        # Step 2: Use VOID or REFUND based on settlement status
        if is_settled:
            logger.info(f"Transaction is SETTLED - using REFUND for ${refund_amount}")
        else:
            logger.info(f"Transaction is NOT SETTLED (status: {transaction_status}) - using VOID")
        
        # Step 3: Execute VOID or REFUND
        if not is_settled:
            # VOID - for unsettled transactions
            logger.info(f"Executing VOID for transaction {original_trans_id}")
            
            transactionRequest = apicontractsv1.transactionRequestType()
            transactionRequest.transactionType = "voidTransaction"
            transactionRequest.refTransId = original_trans_id
            
            createTransactionRequest = apicontractsv1.createTransactionRequest()
            createTransactionRequest.merchantAuthentication = merchantAuth
            createTransactionRequest.transactionRequest = transactionRequest
            
            createTransactionController_instance = createTransactionController(createTransactionRequest)
            
            if sandbox_mode:
                createTransactionController_instance.setenvironment('https://apitest.authorize.net/xml/v1/request.api')
            else:
                createTransactionController_instance.setenvironment('https://api.authorize.net/xml/v1/request.api')
            
            createTransactionController_instance.execute()
            response = createTransactionController_instance.getresponse()
            
        else:
            # REFUND - for settled transactions
            logger.info(f"Executing REFUND for transaction {original_trans_id}, amount ${refund_amount}")
            
            # Get card last 4 digits - prioritize frontend, then fallback to metadata
            card_last4 = frontend_card_last4 if frontend_card_last4 else None
            
            if card_last4:
                logger.info(f"✅ Using card last 4 from frontend: {card_last4}")
            else:
                logger.info(f"Card last 4 not provided by frontend, checking order metadata...")
                
                if order.metadata:
                    # Check for card last 4 in various metadata fields
                    card_last4 = order.metadata.get('_card_last4') or order.metadata.get('card_last4')
                    
                    # If not found, try parsing from _pos_payment_method
                    if not card_last4:
                        pos_payment_method = order.metadata.get('_pos_payment_method')
                        if pos_payment_method:
                            try:
                                payment_data = json.loads(pos_payment_method) if isinstance(pos_payment_method, str) else pos_payment_method
                                
                                # For split payments, get from credit card method
                                if payment_data.get('type') == 'split':
                                    for split in payment_data.get('splitPayments', []):
                                        method = split.get('method', {})
                                        if method.get('type') == 'credit':
                                            card_last4 = method.get('last4')
                                            break
                                # For non-split credit payments
                                elif payment_data.get('type') == 'credit':
                                    card_last4 = payment_data.get('last4')
                            except:
                                pass
                
                # FALLBACK: Check order.payment_method JSON (POS orders store card info here)
                if not card_last4 and hasattr(order, 'payment_method') and order.payment_method:
                    try:
                        pm = json.loads(order.payment_method) if isinstance(order.payment_method, str) else order.payment_method
                        if isinstance(pm, dict):
                            if pm.get('type') == 'split':
                                for split in pm.get('splitPayments', []):
                                    method = split.get('method', {})
                                    if method.get('type') == 'credit':
                                        card_last4 = method.get('last4')
                                        if card_last4:
                                            logger.info(f"✅ Found card last 4 from order.payment_method (split): {card_last4}")
                                        break
                            elif pm.get('type') == 'credit':
                                card_last4 = pm.get('last4')
                                if card_last4:
                                    logger.info(f"✅ Found card last 4 from order.payment_method (credit): {card_last4}")
                    except (json.JSONDecodeError, TypeError, AttributeError) as e:
                        logger.warning(f"Failed to parse order.payment_method for card last4: {e}")

                if not card_last4:
                    logger.warning(f"Card last 4 digits not found for order {order.order_number}")
                    return {
                        'success': False,
                        'message': 'Card information required for refund (last 4 digits not found)'
                    }
                
                logger.info(f"Using card last 4 from metadata: {card_last4}")
            
            # Create payment info with card last 4 (required by Authorize.Net for refunds)
            creditCard = apicontractsv1.creditCardType()
            creditCard.cardNumber = f"XXXX{card_last4}"  # Masked card number with last 4
            creditCard.expirationDate = "XXXX"  # Expiration not needed for refunds
            
            payment = apicontractsv1.paymentType()
            payment.creditCard = creditCard
            
            # Create the payment transaction request
            transactionRequest = apicontractsv1.transactionRequestType()
            transactionRequest.transactionType = "refundTransaction"
            transactionRequest.amount = refund_amount
            transactionRequest.payment = payment  # Include payment info
            transactionRequest.refTransId = original_trans_id
            
            # Create the API request
            createTransactionRequest = apicontractsv1.createTransactionRequest()
            createTransactionRequest.merchantAuthentication = merchantAuth
            createTransactionRequest.transactionRequest = transactionRequest
            
            # Execute the request
            createTransactionController_instance = createTransactionController(createTransactionRequest)
            
            if sandbox_mode:
                createTransactionController_instance.setenvironment('https://apitest.authorize.net/xml/v1/request.api')
            else:
                createTransactionController_instance.setenvironment('https://api.authorize.net/xml/v1/request.api')
            
            createTransactionController_instance.execute()
            response = createTransactionController_instance.getresponse()
        
        # Process response (same for both VOID and REFUND)
        if response is not None:
            if response.messages.resultCode == "Ok":
                if hasattr(response.transactionResponse, 'messages'):
                    trans_id = response.transactionResponse.transId
                    action_type = "VOID" if not is_settled else "REFUND"
                    logger.info(f"✅ Authorize.Net {action_type} successful. Trans ID: {trans_id}")
                    return {
                        'success': True,
                        'message': f'{action_type} processed successfully. Transaction ID: {trans_id}',
                        'transaction_id': trans_id,
                        'action_type': action_type
                    }
                else:
                    if hasattr(response.transactionResponse, 'errors'):
                        error_code = response.transactionResponse.errors.error[0].errorCode
                        error_text = response.transactionResponse.errors.error[0].errorText
                        logger.error(f"❌ Authorize.Net refund error: {error_code} - {error_text}")
                        return {
                            'success': False,
                            'message': _get_friendly_authorize_error(error_code, error_text),
                            'error_code': str(error_code),
                            'raw_error': str(error_text)
                        }
            else:
                if hasattr(response, 'transactionResponse') and hasattr(response.transactionResponse, 'errors'):
                    error_code = response.transactionResponse.errors.error[0].errorCode
                    error_text = response.transactionResponse.errors.error[0].errorText
                    logger.error(f"❌ Authorize.Net refund failed: {error_code} - {error_text}")
                    return {
                        'success': False,
                        'message': _get_friendly_authorize_error(error_code, error_text),
                        'error_code': str(error_code),
                        'raw_error': str(error_text)
                    }
                else:
                    error_code = response.messages.message[0]['code'].text
                    error_text = response.messages.message[0]['text'].text
                    logger.error(f"❌ Authorize.Net API error: {error_code} - {error_text}")
                    return {
                        'success': False,
                        'message': _get_friendly_authorize_error(error_code, error_text),
                        'error_code': str(error_code),
                        'raw_error': str(error_text)
                    }
        else:
            logger.error("❌ No response from Authorize.Net")
            return {
                'success': False,
                'message': 'No response from Authorize.Net payment gateway'
            }
            
    except ImportError:
        logger.error("Authorize.Net SDK not installed")
        return {
            'success': False,
            'message': 'Authorize.Net SDK not installed (run: pip install authorizenet)'
        }
    except Exception as e:
        logger.error(f"Error processing POS payment refund: {str(e)}")
        return {
            'success': False,
            'message': f'Payment refund error: {str(e)}'
        }

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_transaction_status(request, order_id):
    """
    Check the Authorize.net transaction status for an order.
    Returns payment method details and settlement status (settled/unsettled).
    Used by the refund UI to determine if partial refund or void-only is available.
    """
    try:
        import os
        from authorizenet import apicontractsv1
        from authorizenet.apicontrollers import getTransactionDetailsController

        # Find the POS order — try multiple lookup strategies
        order = None
        woo_order_data = None  # WooCommerce order data (fallback for non-POS orders)
        
        # Strategy 1: POS order by order_number (ORD-XXXXX format)
        try:
            order = POSOrder.objects.get(order_number=order_id)
        except POSOrder.DoesNotExist:
            pass
        
        # Strategy 2: POS order by UUID id
        if not order:
            try:
                order = POSOrder.objects.get(id=order_id)
            except (POSOrder.DoesNotExist, ValueError, Exception):
                pass
        
        # Strategy 3: POS order by WooCommerce order ID stored in metadata
        if not order and order_id.isdigit():
            try:
                order = POSOrder.objects.filter(
                    metadata__woo_order_id=int(order_id)
                ).first() or POSOrder.objects.filter(
                    metadata__woo_order_id=order_id
                ).first()
            except Exception:
                pass
        
        # Strategy 4: Fetch directly from WooCommerce API (for non-POS orders)
        if not order and order_id.isdigit():
            try:
                wc = WooCommerceAPI()
                woo_order_data = wc.get_order(int(order_id))
                if not woo_order_data:
                    return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)
                logger.info(f"Fetched WooCommerce order {order_id} for transaction status check")
            except Exception as e:
                logger.warning(f"Failed to fetch WooCommerce order {order_id}: {e}")
                return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)
        
        if not order and not woo_order_data:
            return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)

        # Extract payment method info
        payment_method = 'Unknown'
        payment_type = 'unknown'
        card_brand = ''
        card_last4 = ''
        transaction_id = ''
        result = {
            'payment_method': 'Unknown',
            'payment_type': 'unknown',
            'transaction_id': '',
            'settlement_status': 'unknown',
            'can_partial_refund': True,
            'is_credit_card': False,
        }

        if order and order.metadata:
            # --- POS order path: extract from _pos_payment_method metadata ---
            pos_pm = order.metadata.get('_pos_payment_method')
            if pos_pm:
                try:
                    pm_data = json.loads(pos_pm) if isinstance(pos_pm, str) else pos_pm
                    payment_type = pm_data.get('type', 'unknown')

                    if payment_type == 'split':
                        splits = pm_data.get('splitPayments', [])
                        methods = []
                        for s in splits:
                            m = s.get('method', {})
                            amt = s.get('amount', 0)
                            if m.get('type') == 'credit':
                                card_brand = m.get('brand', 'Card')
                                card_last4 = m.get('last4', '')
                                transaction_id = m.get('id', '')
                                methods.append({'type': 'credit', 'name': m.get('name', f'{card_brand} ending in {card_last4}'), 'amount': float(amt), 'last4': card_last4, 'brand': card_brand})
                            else:
                                methods.append({'type': m.get('type', 'other'), 'name': m.get('name', m.get('type', 'Other')), 'amount': float(amt)})
                        result.update({
                            'payment_method': 'Split Payment',
                            'payment_type': 'split',
                            'split_methods': methods,
                            'transaction_id': transaction_id,
                            'is_credit_card': bool(transaction_id),
                        })
                    elif payment_type == 'credit':
                        card_brand = pm_data.get('brand', 'Card')
                        card_last4 = pm_data.get('last4', '')
                        transaction_id = pm_data.get('id', '')
                        result.update({
                            'payment_method': pm_data.get('name', f'{card_brand} ending in {card_last4}'),
                            'payment_type': 'credit',
                            'card_brand': card_brand,
                            'card_last4': card_last4,
                            'transaction_id': transaction_id,
                            'is_credit_card': True,
                        })
                    else:
                        result.update({
                            'payment_method': pm_data.get('name', payment_type.title()),
                            'payment_type': payment_type,
                            'settlement_status': 'not_applicable',
                            'is_credit_card': False,
                        })
                except (json.JSONDecodeError, TypeError):
                    pass
            
            # Override payment method for $0 credited service redemption orders
            if order.total is not None and float(order.total) == 0:
                order_items = order.items.all()
                if order_items.exists() and all(
                    (item.metadata if isinstance(item.metadata, dict) else {}).get('isCreditedService', False)
                    for item in order_items
                ):
                    result.update({
                        'payment_method': 'Credited Services',
                        'payment_type': 'credited_services',
                        'is_credit_card': False,
                        'settlement_status': 'not_applicable',
                        'transaction_id': '',
                    })
                    return Response(result)
            
            # Try other POS metadata fields for transaction ID
            if not transaction_id:
                transaction_id = order.metadata.get('transaction_id', '') or ''
                if transaction_id.startswith('ORD-'):
                    transaction_id = ''
                if not transaction_id:
                    transaction_id = order.metadata.get('authorize_net_trans_id', '') or order.metadata.get('auth_net_transaction_id', '') or ''

        # --- WooCommerce order path: extract from WooCommerce API data ---
        # Either we have a WooCommerce-only order, or we need to supplement POS data
        woo_id_to_fetch = None
        if woo_order_data:
            woo_id_to_fetch = None  # Already have it
        elif order and order.metadata and order.metadata.get('woo_order_id'):
            woo_id_to_fetch = order.metadata['woo_order_id']
        
        if woo_id_to_fetch and not woo_order_data:
            try:
                wc = WooCommerceAPI()
                woo_order_data = wc.get_order(woo_id_to_fetch)
            except Exception as e:
                logger.warning(f"Failed to fetch WooCommerce order {woo_id_to_fetch}: {e}")
        
        if woo_order_data:
            # Get payment method from WooCommerce
            woo_pm = woo_order_data.get('payment_method', '')
            woo_pm_title = woo_order_data.get('payment_method_title', '')
            woo_tid = woo_order_data.get('transaction_id', '')
            
            if result['payment_method'] == 'Unknown' and woo_pm_title:
                is_cc = 'authorize' in woo_pm.lower() or 'credit' in woo_pm_title.lower() or 'card' in woo_pm_title.lower()
                result.update({
                    'payment_method': woo_pm_title,
                    'payment_type': 'credit' if is_cc else woo_pm or 'other',
                    'is_credit_card': is_cc,
                })
            
            if not transaction_id and woo_tid and not woo_tid.startswith('ORD-'):
                transaction_id = woo_tid
            
            # Check WooCommerce meta_data for additional info
            woo_meta = woo_order_data.get('meta_data', [])
            if isinstance(woo_meta, list):
                for meta in woo_meta:
                    key = meta.get('key', '')
                    val = meta.get('value', '')
                    if not transaction_id and key == '_pos_transaction_id' and val and not str(val).startswith('ORD-'):
                        transaction_id = str(val)
                    # Check for POS payment method in WooCommerce meta
                    if key == '_pos_payment_method' and result['payment_method'] == woo_pm_title:
                        try:
                            pm_data = json.loads(val) if isinstance(val, str) else val
                            pt = pm_data.get('type', '')
                            if pt == 'credit':
                                card_brand = pm_data.get('brand', 'Card')
                                card_last4 = pm_data.get('last4', '')
                                if not transaction_id:
                                    tid = pm_data.get('id', '')
                                    if tid and not tid.startswith('ORD-'):
                                        transaction_id = tid
                                result.update({
                                    'payment_method': pm_data.get('name', f'{card_brand} ending in {card_last4}'),
                                    'payment_type': 'credit',
                                    'card_brand': card_brand,
                                    'card_last4': card_last4,
                                    'is_credit_card': True,
                                })
                            elif pt == 'split':
                                splits = pm_data.get('splitPayments', [])
                                methods = []
                                for s in splits:
                                    m = s.get('method', {})
                                    amt = s.get('amount', 0)
                                    if m.get('type') == 'credit':
                                        card_brand = m.get('brand', 'Card')
                                        card_last4 = m.get('last4', '')
                                        if not transaction_id:
                                            tid = m.get('id', '')
                                            if tid and not tid.startswith('ORD-'):
                                                transaction_id = tid
                                        methods.append({'type': 'credit', 'name': m.get('name', f'{card_brand} ending in {card_last4}'), 'amount': float(amt), 'last4': card_last4, 'brand': card_brand})
                                    else:
                                        methods.append({'type': m.get('type', 'other'), 'name': m.get('name', m.get('type', 'Other')), 'amount': float(amt)})
                                result.update({
                                    'payment_method': 'Split Payment',
                                    'payment_type': 'split',
                                    'split_methods': methods,
                                    'is_credit_card': bool(transaction_id),
                                })
                            elif pt in ('cash', 'check', 'wire_transfer', 'care_credit', 'outside_pos'):
                                result.update({
                                    'payment_method': pm_data.get('name', pt.replace('_', ' ').title()),
                                    'payment_type': pt,
                                    'settlement_status': 'not_applicable',
                                    'is_credit_card': False,
                                })
                        except (json.JSONDecodeError, TypeError):
                            pass

        result['transaction_id'] = transaction_id

        # If we have a credit card transaction ID, check settlement status with Authorize.net
        if transaction_id and result.get('is_credit_card', False):
            from core.secrets import get_secret
            api_login_id = get_secret('AUTHORIZE_NET_LOGIN_ID')
            transaction_key_env = get_secret('AUTHORIZE_NET_TRANSACTION_KEY')
            sandbox_mode = get_secret('AUTHORIZE_NET_SANDBOX', 'False').lower() == 'true'

            if api_login_id and transaction_key_env:
                try:
                    merchantAuth = apicontractsv1.merchantAuthenticationType()
                    merchantAuth.name = api_login_id
                    merchantAuth.transactionKey = transaction_key_env

                    req = apicontractsv1.getTransactionDetailsRequest()
                    req.merchantAuthentication = merchantAuth
                    req.transId = transaction_id

                    ctrl = getTransactionDetailsController(req)
                    endpoint = 'https://apitest.authorize.net/xml/v1/request.api' if sandbox_mode else 'https://api.authorize.net/xml/v1/request.api'
                    ctrl.setenvironment(endpoint)
                    ctrl.execute()

                    resp = ctrl.getresponse()

                    if resp and resp.messages.resultCode == "Ok":
                        tx_status = str(resp.transaction.transactionStatus)
                        tx_amount = float(str(resp.transaction.settleAmount)) if hasattr(resp.transaction, 'settleAmount') else None

                        if tx_status == 'settledSuccessfully':
                            result['settlement_status'] = 'settled'
                            result['can_partial_refund'] = True
                        elif tx_status in ('authorizedPendingCapture', 'capturedPendingSettlement', 'FDSPendingReview', 'FDSAuthorizedPendingReview'):
                            result['settlement_status'] = 'unsettled'
                            result['can_partial_refund'] = False
                        elif tx_status in ('voided', 'expired', 'declined', 'failedReview'):
                            result['settlement_status'] = tx_status
                            result['can_partial_refund'] = False
                        elif tx_status == 'refundSettledSuccessfully':
                            result['settlement_status'] = 'already_refunded'
                            result['can_partial_refund'] = False
                        else:
                            result['settlement_status'] = tx_status
                            result['can_partial_refund'] = True

                        result['transaction_status_raw'] = tx_status
                        if tx_amount:
                            result['transaction_amount'] = tx_amount

                        logger.info(f"Transaction {transaction_id} status: {tx_status}")
                    else:
                        logger.warning(f"Could not get transaction details for {transaction_id}")
                        result['settlement_status'] = 'check_failed'
                        result['can_partial_refund'] = True
                except Exception as e:
                    logger.warning(f"Error checking transaction status: {e}")
                    result['settlement_status'] = 'check_failed'
                    result['can_partial_refund'] = True

        return Response(result)

    except Exception as e:
        logger.exception(f"Error checking transaction status: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _get_refund_method_display(payment_info):
    """Map payment_info type to a human-readable refund destination label."""
    if not payment_info:
        return 'Cash'
    ptype = payment_info.get('type', '')

    def _card_display(info):
        """Build card display from brand + last4 (e.g. 'Visa ****1234')."""
        name = info.get('name', '')
        if name and ('****' in name or 'ending' in name):
            return name
        brand = info.get('brand', '')
        last4 = info.get('last4', '')
        if brand and last4:
            return f'{brand} ****{last4}'
        if last4:
            return f'Card ****{last4}'
        return None

    # For split payments, prefer credit card method if present, else largest amount
    if ptype == 'split':
        splits = payment_info.get('splitPayments', [])
        for sp in splits:
            m = sp.get('method', {})
            if m.get('type') in ('credit', 'debit'):
                return _card_display(m) or 'Credit Card'
        # No credit card in split — use largest amount method
        if splits:
            largest = max(splits, key=lambda s: float(s.get('amount', 0)))
            return _get_refund_method_display(largest.get('method', {}))
        return 'Cash'

    # For credit/debit, prefer card brand + last4 over generic label
    if ptype in ('credit', 'debit'):
        return _card_display(payment_info) or 'Credit Card'

    METHOD_MAP = {
        'care_credit': 'Care Credit',
        'wire_transfer': 'Wire Transfer',
        'check': 'Check',
        'credited_services': 'Credited Services',
        'outside_pos': 'Outside POS',
        'points': 'Points',
        'cash': 'Cash',
    }
    return METHOD_MAP.get(ptype, ptype.replace('_', ' ').title() if ptype else 'Cash')


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def process_refund(request):
    """
    Process a refund for a POS order
    
    This endpoint handles both WooCommerce refunds and local POS refunds:
    1. For WooCommerce orders, it creates a refund in WooCommerce via API
    2. For all orders, it creates a refund record in the local database
    3. It moves the refunded items from POSOrderItem to POSOrderRefundItem
    
    Request body should include:
    - order_id: ID of the order to refund
    - items: Array of items to refund with id, refundQuantity, and optional custom_amount
      - custom_amount: Optional partial dollar amount to refund (overrides price * qty)
    - reason: Reason for the refund
    - amount: Total refund amount
    - created_by: Email of the user creating the refund
    - transaction_id: Authorize.Net transaction ID (optional, from frontend)
    """
    try:
        # Get request data
        data = request.data
        order_id = data.get('order_id')
        items_to_refund = data.get('items', [])
        refund_reason = data.get('reason', '')
        refund_amount = float(data.get('amount', 0))
        created_by = data.get('created_by', request.user.email)
        frontend_transaction_id = data.get('transaction_id', '')  # Transaction ID from frontend
        frontend_card_last4 = data.get('card_last4', '')  # Card last 4 from frontend
        payment_info = data.get('payment_info')  # Full payment breakdown from frontend
        refund_as_points = data.get('refund_as_points', False)  # Refund as YITH points instead of money
        
        logger.info(f"💳 Payment info received: {payment_info}")
        logger.info(f"🎯 Refund as points: {refund_as_points}")
        
        # Validate required fields
        if not order_id:
            return Response({"error": "Order ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not items_to_refund:
            return Response({"error": "At least one item must be refunded"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Allow $0 refund (e.g. credited service redemption orders that need credit restoration)
        # Only block negative refund amounts
        if refund_amount < 0:
            return Response({"error": "Refund amount cannot be negative"}, status=status.HTTP_400_BAD_REQUEST)
        
        if refund_amount == 0:
            logger.info(f"✅ Allowing $0 refund for order {order_id} (credited service redemption or zero-dollar order)")
        
        # Detect credited service refund for downstream logic (skip WooCommerce refund, etc.)
        is_credited_service_refund = False
        if payment_info and isinstance(payment_info, dict):
            pm_type = payment_info.get('type', '')
            pm_name = payment_info.get('name', '')
            if pm_type == 'credited_services' or 'credited' in str(pm_name).lower():
                is_credited_service_refund = True
        
        # Get the order from the database
        try:
            # Debug the incoming order_id
            logger.info(f"Processing refund for order_id: {order_id}")
            
            # IMPORTANT: Never try to convert a non-UUID string to UUID
            # First, check if the order_id is a valid UUID format
            is_valid_uuid = True
            try:
                # Just validate the format, don't actually use this uuid object
                uuid_obj = uuid.UUID(str(order_id))
            except (ValueError, TypeError):
                # Not a valid UUID format, so must be an order_number
                is_valid_uuid = False
                logger.info(f"Order ID {order_id} is not a valid UUID format, treating as order_number")
            
            # Log all existing order numbers for debugging
            all_orders = POSOrder.objects.all()
            logger.info(f"Available order numbers in database: {[o.order_number for o in all_orders[:10]]}")
            logger.info(f"Total orders in database: {all_orders.count()}")
            
            # Log some WooCommerce order IDs for debugging
            # Find orders with woo_order_id in metadata
            woo_orders = []
            for o in POSOrder.objects.all()[:100]:  # Limit to first 100 for performance
                if o.metadata and 'woo_order_id' in o.metadata:
                    woo_orders.append(o)
            woo_orders = woo_orders[:10]  # Take first 10
            logger.info(f"Sample WooCommerce order IDs: {[o.metadata.get('woo_order_id') for o in woo_orders if o.metadata]}")
            
            # Search strategy based on ID format
            if is_valid_uuid:
                # If it's a valid UUID format, try UUID first
                try:
                    order = POSOrder.objects.get(id=order_id)
                    logger.info(f"Found order by UUID: {order.order_number}")
                except POSOrder.DoesNotExist:
                    # If not found by UUID, try order_number as fallback
                    # Try multiple formats for order_number lookup
                    order = POSOrder.objects.filter(order_number=order_id).first()
                    if not order:
                        # Try as integer
                        try:
                            order = POSOrder.objects.filter(order_number=str(int(order_id))).first()
                        except (ValueError, TypeError):
                            pass
                    
                    if not order:
                        logger.error(f"Order not found with id or order_number: {order_id}")
                        return Response({"error": f"Order with ID {order_id} not found"}, status=status.HTTP_404_NOT_FOUND)
            else:
                # First try order_number (local POS order number)
                order = POSOrder.objects.filter(order_number=order_id).first()
                
                # If not found, try as WooCommerce order ID stored in metadata
                if not order:
                    try:
                        # Try to convert to integer for WooCommerce ID lookup
                        woo_id = int(order_id)
                        # WooCommerce ID is stored in metadata as woo_order_id
                        # Use efficient database query instead of iterating all orders
                        order = POSOrder.objects.filter(
                            metadata__woo_order_id=woo_id
                        ).first()
                        
                        if order:
                            logger.info(f"Found order by WooCommerce ID in metadata {woo_id}: {order.order_number}")
                        else:
                            logger.info(f"No POS order found with woo_order_id {woo_id}, will attempt direct WooCommerce refund")
                    except (ValueError, TypeError):
                        logger.info(f"Could not convert {order_id} to integer for WooCommerce ID lookup")
                
                # If not found, try as integer order_number
                if not order:
                    try:
                        order = POSOrder.objects.filter(order_number=str(int(order_id))).first()
                        logger.info(f"Found order after integer conversion: {order.order_number if order else None}")
                    except (ValueError, TypeError):
                        pass
                
                # If still not found, try with/without leading zeros
                if not order:
                    # Try with leading zeros removed
                    try:
                        stripped_id = order_id.lstrip('0')
                        if stripped_id != order_id:
                            order = POSOrder.objects.filter(order_number=stripped_id).first()
                            logger.info(f"Found order after stripping zeros: {order.order_number if order else None}")
                    except (AttributeError, TypeError):
                        pass
                
                if not order:
                    # Check if this might be a pure WooCommerce order (not in POS database)
                    try:
                        woo_id = int(order_id)
                        logger.info(f"Order not found in POS database, attempting direct WooCommerce refund for order {woo_id}")
                        
                        # Initialize WooCommerce API (already imported at top of file)
                        wc = WooCommerceAPI()
                        
                        # Get the WooCommerce order to verify it exists
                        woo_order = wc.get_order(woo_id)
                        if not woo_order:
                            return Response({"error": f"Order with ID {order_id} not found in POS or WooCommerce."}, status=status.HTTP_404_NOT_FOUND)
                        
                        # Process pure WooCommerce refund with Authorize.net payment refund
                        logger.info(f"Processing direct WooCommerce refund for order {woo_id}")
                        
                        # ── Extract Authorize.net transaction ID from WooCommerce order ──
                        woo_trans_id = woo_order.get('transaction_id', '')
                        if not woo_trans_id:
                            # Check meta_data for transaction ID
                            for meta in woo_order.get('meta_data', []):
                                if meta.get('key') in ['_transaction_id', '_payment_transaction_id'] and meta.get('value'):
                                    woo_trans_id = meta['value']
                                    break
                        
                        # Also use frontend-provided transaction_id if available
                        if frontend_transaction_id and not frontend_transaction_id.startswith('ORD-'):
                            woo_trans_id = frontend_transaction_id
                            logger.info(f"Using frontend-provided transaction_id: {woo_trans_id}")
                        
                        # ── Extract card last4 from WooCommerce order ──
                        woo_card_last4 = frontend_card_last4 or ''
                        if not woo_card_last4:
                            # Try payment_method_title (e.g. "Credit Card (Visa ending in 4310)")
                            import re
                            pmt_title = woo_order.get('payment_method_title', '')
                            last4_match = re.search(r'(\d{4})\s*\)?$', pmt_title)
                            if last4_match:
                                woo_card_last4 = last4_match.group(1)
                            else:
                                # Try meta_data for card info
                                for meta in woo_order.get('meta_data', []):
                                    if meta.get('key') in ['_authnet_cc_last4',
                                                           '_wc_authorize_net_cim_credit_card_last_four',
                                                           '_card_last4', 'card_last4'] and meta.get('value'):
                                        woo_card_last4 = str(meta['value'])[-4:] if len(str(meta['value'])) >= 4 else str(meta['value'])
                                        break
                        
                        logger.info(f"Pure WooCommerce refund - transaction_id: {woo_trans_id}, card_last4: {woo_card_last4}")
                        
                        # ── Process Authorize.net payment refund ──
                        gateway_refund_success = False
                        gateway_refund_message = ""
                        gateway_refund_data = {}
                        
                        woo_payment_method = woo_order.get('payment_method', '')
                        is_authorize_net = 'authorize_net' in woo_payment_method or woo_trans_id
                        
                        if is_authorize_net and woo_trans_id and not woo_trans_id.startswith('ORD-'):
                            logger.info(f"Processing Authorize.net refund for WooCommerce order {woo_id}, trans_id: {woo_trans_id}")
                            
                            # Build a minimal object that process_pos_payment_refund can use
                            # It needs: order_number, created_at, metadata
                            class _WooOrderProxy:
                                """Minimal proxy so process_pos_payment_refund can work with a WooCommerce order."""
                                def __init__(self, woo_order_data):
                                    self.order_number = str(woo_order_data.get('id', ''))
                                    # Parse created_at for the 180-day window check
                                    from django.utils import timezone as tz
                                    from datetime import datetime
                                    date_str = woo_order_data.get('date_created_gmt') or woo_order_data.get('date_created', '')
                                    try:
                                        self.created_at = tz.make_aware(datetime.strptime(date_str[:19], '%Y-%m-%dT%H:%M:%S'))
                                    except Exception:
                                        self.created_at = tz.now()
                                    self.metadata = {}
                            
                            proxy_order = _WooOrderProxy(woo_order)
                            
                            gateway_result = process_pos_payment_refund(
                                proxy_order,
                                float(refund_amount),
                                refund_reason,
                                frontend_transaction_id=woo_trans_id,
                                frontend_card_last4=woo_card_last4
                            )
                            
                            gateway_refund_success = gateway_result.get('success', False)
                            gateway_refund_message = gateway_result.get('message', '')
                            gateway_refund_data = gateway_result
                            logger.info(f"Authorize.net refund result: success={gateway_refund_success}, message={gateway_refund_message}")
                        else:
                            logger.warning(f"No valid Authorize.net transaction ID for WooCommerce order {woo_id} (payment_method: {woo_payment_method}, trans_id: {woo_trans_id})")
                            gateway_refund_message = "No Authorize.net transaction ID found on this order"
                        
                        # ── Create WooCommerce refund record ──
                        formatted_amount = "{:.2f}".format(float(refund_amount))
                        
                        refund_data = {
                            "amount": formatted_amount,
                            "reason": refund_reason,
                            "api_refund": False
                        }
                        
                        response = wc.wcapi.post(f"orders/{woo_id}/refunds", refund_data)
                        
                        if response.status_code in [200, 201]:
                            woo_refund_response = response.json()
                            woo_refund_id = woo_refund_response.get('id')
                            logger.info(f"WooCommerce refund created successfully: {woo_refund_id}")
                            
                            # Update WooCommerce order status for full refunds
                            order_total = float(woo_order.get('total', 0))
                            if float(refund_amount) >= order_total:
                                status_update_response = wc.update_order_status(woo_id, 'refunded')
                                logger.info(f"WooCommerce order status update response: {status_update_response}")
                            
                            # ── Create POS refund tracking records for per-item tracking ──
                            # Without this, get_order_refunds returns nothing for pure WooCommerce orders
                            # and per-item caps are never applied, allowing repeated refunds on the same items.
                            try:
                                from decimal import Decimal
                                # Find or create a shadow POS order for this WooCommerce order
                                # Use explicit filter + create (get_or_create with JSONField lookups is unreliable)
                                pos_order = (
                                    POSOrder.objects.filter(metadata__woo_order_id=woo_id).first()
                                    or POSOrder.objects.filter(metadata__woo_order_id=str(woo_id)).first()
                                )
                                _created = False
                                if not pos_order:
                                    # Parse WooCommerce order dates
                                    from django.utils import timezone as tz
                                    from datetime import datetime as _dt
                                    woo_date_str = woo_order.get('date_created_gmt') or woo_order.get('date_created', '')
                                    try:
                                        order_created = tz.make_aware(_dt.strptime(woo_date_str[:19], '%Y-%m-%dT%H:%M:%S'))
                                    except Exception:
                                        order_created = tz.now()
                                    
                                    pos_order = POSOrder.objects.create(
                                        order_number=str(woo_id),
                                        total=Decimal(str(woo_order.get('total', 0))),
                                        status=woo_order.get('status', 'completed'),
                                        payment_method={'title': woo_order.get('payment_method_title', 'Unknown')},
                                        payment_method_title=woo_order.get('payment_method_title', 'Unknown'),
                                        metadata={'woo_order_id': woo_id, 'source': 'woo_refund_shadow'},
                                        created_at=order_created,
                                        updated_at=tz.now(),
                                    )
                                    _created = True
                                if _created:
                                    logger.info(f"Created shadow POS order {pos_order.order_number} for WooCommerce order {woo_id}")
                                    # Create shadow order items from WooCommerce line items
                                    for li in woo_order.get('line_items', []):
                                        li_price = Decimal(str(li.get('price', 0)))
                                        li_qty = li.get('quantity', 1)
                                        POSOrderItem.objects.create(
                                            order=pos_order,
                                            product_id=str(li.get('product_id', '')),
                                            name=li.get('name', 'Unknown'),
                                            price=li_price,
                                            quantity=li_qty,
                                            subtotal=li_price * li_qty,
                                        )
                                
                                # Create refund record
                                current_ts = int(time.time())
                                pos_refund = POSOrderRefund.objects.create(
                                    order=pos_order,
                                    refund_number=f"REF-WOO-{woo_id}-{current_ts}",
                                    refund_amount=Decimal(str(refund_amount)),
                                    refund_reason=refund_reason,
                                    created_by=created_by,
                                    status='completed',
                                    payment_method={'title': woo_order.get('payment_method_title', 'Unknown')},
                                    payment_method_title=woo_order.get('payment_method_title', 'Unknown'),
                                    metadata={'woo_refund_id': woo_refund_id, 'source': 'woo_direct'},
                                )
                                
                                # Create refund item records
                                for item_data in items_to_refund:
                                    item_name = item_data.get('name', 'Unknown')
                                    item_qty = int(item_data.get('refundQuantity', 0))
                                    item_custom_amt = item_data.get('custom_amount')
                                    item_price = float(item_data.get('price', 0))
                                    item_subtotal = float(item_custom_amt) if item_custom_amt is not None else item_price * item_qty
                                    
                                    if item_qty <= 0:
                                        continue
                                    
                                    # Try to find the matching shadow order item
                                    shadow_order_item = POSOrderItem.objects.filter(
                                        order=pos_order, name__iexact=item_name.strip()
                                    ).first()
                                    
                                    POSOrderRefundItem.objects.create(
                                        refund=pos_refund,
                                        order_item=shadow_order_item,
                                        product_id=item_data.get('product_id', str(item_data.get('id', ''))),
                                        name=item_name,
                                        price=Decimal(str(item_price)),
                                        quantity=item_qty,
                                        subtotal=Decimal(str(item_subtotal)),
                                        refund_reason=refund_reason,
                                    )
                                
                                logger.info(f"Created POS refund tracking records for WooCommerce order {woo_id}: refund={pos_refund.refund_number}")
                            except Exception as track_err:
                                # Don't fail the refund if tracking records fail — just log
                                logger.error(f"Failed to create POS refund tracking records for WooCommerce order {woo_id}: {track_err}")
                            
                            # Determine overall status based on gateway result
                            if gateway_refund_success:
                                overall_status = "success"
                                overall_message = "Refund processed successfully — payment returned to original card"
                            elif is_authorize_net and woo_trans_id:
                                overall_status = "partial_success"
                                overall_message = (
                                    f"WooCommerce refund record created, but payment gateway refund FAILED. "
                                    f"{gateway_refund_message}. "
                                    f"The customer has NOT been refunded to their card."
                                )
                            else:
                                overall_status = "success"
                                overall_message = "WooCommerce refund record created (no gateway payment to refund)"
                            
                            # Auto-send refund receipt email to customer (pure WooCommerce order path)
                            refund_email_sent = False
                            customer_email = woo_order.get('billing', {}).get('email', '')
                            if customer_email:
                                try:
                                    from .views_receipts import (
                                        _build_refund_receipt_html, _build_pdf_refund_receipt_html,
                                        _html_to_pdf, _enrich_order_subscription_frequency
                                    )
                                    _enrich_order_subscription_frequency(woo_order)

                                    # Inject refund method display
                                    woo_order['_refund_method_display'] = _get_refund_method_display(
                                        {'type': woo_order.get('payment_method', ''), 'name': woo_order.get('payment_method_title', '')}
                                    ) if woo_order.get('payment_method') else 'Cash'

                                    # Inject the actual refunded items so the receipt only shows refunded items
                                    if items_to_refund:
                                        woo_order['_refunded_items'] = [
                                            {
                                                'name': it.get('name', 'Unknown'),
                                                'quantity': int(it.get('refundQuantity', 0)),
                                                'total': str(float(it.get('custom_amount', 0)) if it.get('custom_amount') is not None else float(it.get('price', 0)) * int(it.get('refundQuantity', 0))),
                                                'sku': '',
                                                'product_id': it.get('product_id'),
                                                'meta_data': [],
                                            }
                                            for it in items_to_refund if int(it.get('refundQuantity', 0)) > 0
                                        ]
                                        woo_order['_refunded_item_names'] = [
                                            it.get('name', '').lower().strip() for it in items_to_refund if int(it.get('refundQuantity', 0)) > 0
                                        ]
                                        logger.info(f"📋 Pure WooCommerce refund: Injected _refunded_items: {woo_order['_refunded_item_names']}")

                                    html_body, _, _ = _build_refund_receipt_html(woo_order)
                                    pdf_html = _build_pdf_refund_receipt_html(woo_order)
                                    pdf_bytes = _html_to_pdf(pdf_html)

                                    woo_number = woo_order.get('number', woo_id)
                                    subject = f'Your Doctors Studio Refund Receipt — Order #{woo_number}'
                                    text_content = f'Refund receipt for Order #{woo_number}'

                                    from .email_sender import send_pos_email
                                    attachments = [(f'Refund_Receipt_Order_{woo_number}.pdf', pdf_bytes, 'application/pdf')] if pdf_bytes else None
                                    refund_email_sent = send_pos_email(
                                        subject=subject,
                                        text_content=text_content,
                                        html_content=html_body,
                                        to_email=customer_email,
                                        email_type='auto_refund_receipt',
                                        attachments=attachments,
                                        related_order=str(woo_number),
                                    )
                                    if refund_email_sent:
                                        logger.info(f"📧 ✅ Auto refund receipt sent to {customer_email} for pure WooCommerce order {woo_id}")
                                    else:
                                        logger.warning(f"📧 ❌ Failed to send auto refund receipt for pure WooCommerce order {woo_id}")
                                except Exception as email_err:
                                    logger.error(f"📧 Error auto-sending refund receipt for pure WooCommerce order {woo_id}: {email_err}")
                            else:
                                logger.info(f"📧 No customer email on WooCommerce order {woo_id}, skipping auto refund receipt")

                            return Response({
                                "status": overall_status,
                                "message": overall_message,
                                "woo_refund_id": woo_refund_id,
                                "woo_order_id": woo_id,
                                "refund_amount": float(refund_amount),
                                "reason": refund_reason,
                                "order_status": "refunded" if float(refund_amount) >= order_total else woo_order.get('status', 'completed'),
                                "note": "This was a pure WooCommerce order (not in POS database)",
                                "payment_gateway_refund": gateway_refund_success,
                                "gateway_details": gateway_refund_data if gateway_refund_data else None,
                                "gateway_refund_failed": is_authorize_net and woo_trans_id and not gateway_refund_success,
                                "refund_email_sent": refund_email_sent,
                            }, status=status.HTTP_201_CREATED)
                        else:
                            logger.error(f"Failed to create WooCommerce refund: {response.text}")
                            return Response({"error": f"Failed to create WooCommerce refund: {response.text}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                    except (ValueError, TypeError):
                        # Not a numeric ID, can't be a WooCommerce order
                        logger.error(f"Order not found with order_number or woo_order_id: {order_id} (type: {type(order_id)})")
                        return Response({"error": f"Order with ID {order_id} not found. Please check if this is a valid order number or WooCommerce order ID."}, status=status.HTTP_404_NOT_FOUND)
                    except Exception as woo_error:
                        logger.error(f"Error processing direct WooCommerce refund: {str(woo_error)}")
                        return Response({"error": f"Error processing WooCommerce refund: {str(woo_error)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
                woo_id = order.metadata.get('woo_order_id', 'None') if order.metadata else 'None'
                logger.info(f"Found order: {order.order_number} (WooCommerce ID in metadata: {woo_id})")

        except Exception as e:
            logger.error(f"Error finding order: {str(e)}")
            return Response({"error": f"Error finding order: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Fallback credited service detection from the order itself
        if not is_credited_service_refund and refund_amount == 0:
            # Check order payment_method_title
            pmt_title = getattr(order, 'payment_method_title', '') or ''
            if 'credited' in pmt_title.lower():
                is_credited_service_refund = True
                logger.info(f"✅ Detected credited service order from payment_method_title: {pmt_title}")
            else:
                # Check if all order items are credited services
                order_items = order.items.all()
                if order_items.exists() and all(
                    (item.metadata if isinstance(item.metadata, dict) else {}).get('isCreditedService', False)
                    for item in order_items
                ):
                    is_credited_service_refund = True
                    logger.info(f"✅ Detected credited service order from item metadata")
            
            if is_credited_service_refund:
                logger.info(f"✅ Allowing $0 refund for credited service order {order_id} (detected after order fetch)")
        
        # Check if the order has a WooCommerce ID in metadata
        woo_order_id = order.metadata.get('woo_order_id') if order.metadata else None
        
        # Process the refund in a transaction to ensure data consistency
        with transaction.atomic():
            # Create a new refund record
            # Generate a unique refund number based on order number and current timestamp
            current_timestamp = int(time.time())
            refund_number = f"REF-{order.order_number.replace('ORD-', '')}-{current_timestamp}"
            
            refund = POSOrderRefund.objects.create(
                order=order,
                refund_number=refund_number,
                refund_amount=refund_amount,
                refund_reason=refund_reason,  # Changed from reason to refund_reason
                created_by=created_by,
                status='completed',
                # refund_date is not needed as created_at is auto_now_add
                payment_method=order.payment_method,
                # Store woo_refund_id in metadata instead
                metadata={'woo_refund_id': None}  # Will be updated if WooCommerce refund is successful
            )
            
            # Process each refund item
            processed_items = []
            
            for item_data in items_to_refund:
                item_id = item_data.get('id')
                refund_quantity = int(item_data.get('refundQuantity', 0))
                custom_amount = item_data.get('custom_amount')  # Optional custom partial dollar refund amount
                
                if not item_id or refund_quantity <= 0:
                    continue
                
                try:
                    # Log the item ID we're trying to find
                    logger.info(f"Looking for order item with ID: {item_id} for order {order.order_number}")
                    
                    # First check if the item_id is a valid UUID
                    is_valid_uuid = False
                    try:
                        # Try to convert to UUID to check validity
                        uuid_obj = uuid.UUID(str(item_id))
                        is_valid_uuid = True
                    except (ValueError, TypeError):
                        is_valid_uuid = False
                        logger.info(f"Item ID {item_id} is not a valid UUID, will try alternative lookup methods")
                    
                    # Try to get the order item by ID if it's a valid UUID
                    if is_valid_uuid:
                        try:
                            order_item = POSOrderItem.objects.get(id=uuid_obj)
                            logger.info(f"Found order item by UUID: {item_id}")
                        except POSOrderItem.DoesNotExist:
                            logger.info(f"Item not found by UUID: {item_id}, trying by product_id or code")
                            order_item = None
                    else:
                        order_item = None
                    
                    # If not found by UUID, try other methods
                    if not order_item:
                        # Try to find by product_id or code (using the item_id from frontend)
                        order_item = POSOrderItem.objects.filter(order=order, product_id=str(item_id)).first() or \
                                    POSOrderItem.objects.filter(order=order, code=str(item_id)).first()
                        
                        if order_item:
                            logger.info(f"Found order item by product_id or code: {item_id}")
                    
                    # Try by item name (frontend sends WooCommerce line_item_id which doesn't match POS IDs)
                    if not order_item:
                        item_name = item_data.get('name', '')
                        if item_name:
                            order_item = POSOrderItem.objects.filter(
                                order=order, name__iexact=item_name.strip()
                            ).first()
                            if order_item:
                                logger.info(f"Found order item by name: '{item_name}'")
                    
                    # Try by WooCommerce product_id sent from frontend
                    if not order_item:
                        woo_product_id = item_data.get('product_id', '')
                        if woo_product_id:
                            order_item = POSOrderItem.objects.filter(
                                order=order, product_id=str(woo_product_id)
                            ).first()
                            if order_item:
                                logger.info(f"Found order item by frontend product_id: {woo_product_id}")
                        
                        # If still not found, try by position in the order's items
                        if not order_item:
                            logger.info(f"Trying to find item by position in order items: {item_id}")
                            order_items = POSOrderItem.objects.filter(order=order)
                            logger.info(f"Order has {len(order_items)} items")
                            
                            # Log the first few items for debugging
                            for i, oi in enumerate(order_items[:5]):
                                logger.info(f"Item {i}: ID={oi.id}, product_id={oi.product_id}, name={oi.name}")
                            
                            # Try to match by position
                            for i, oi in enumerate(order_items):
                                if str(i) == str(item_id) or str(i+1) == str(item_id):
                                    order_item = oi
                                    logger.info(f"Found order item by position {i}: {oi.id}")
                                    break
                        
                        if not order_item:
                            # Last resort: just get the first item if there's only one
                            if len(order_items) == 1:
                                order_item = order_items[0]
                                logger.info(f"Using the only item in order as fallback: {order_item.id}")
                            else:
                                logger.error(f"Could not find item with ID {item_id} for order {order.order_number}")
                                raise POSOrderItem.DoesNotExist(f"Item with ID {item_id} not found")
                    
                    # Calculate refund amount for this item
                    # Use custom_amount if provided (partial dollar refund), otherwise use price * qty
                    if custom_amount is not None:
                        item_refund_amount = float(custom_amount)
                        logger.info(f"Using custom refund amount ${item_refund_amount} for item {order_item.name} (full price would be ${order_item.price * refund_quantity})")
                    else:
                        item_refund_amount = order_item.price * refund_quantity
                    
                    # Create a refund item record
                    customer_name = None
                    customer_email = None
                    if order.contact:
                        customer_name = f"{order.contact.first_name} {order.contact.last_name}".strip()
                        customer_email = order.contact.email
                    
                    refund_item = POSOrderRefundItem.objects.create(
                        refund=refund,
                        order_item=order_item,  # Changed from original_item to order_item
                        customer=order.contact,  # Set the customer from the order's contact
                        customer_name=customer_name,  # Store customer name directly
                        customer_email=customer_email,  # Store customer email directly
                        product_id=order_item.product_id,
                        name=order_item.name,
                        price=order_item.price,
                        quantity=refund_quantity,
                        subtotal=item_refund_amount,  # Changed from refund_amount to subtotal
                        refund_reason=refund_reason  # Store the refund reason on each item
                    )
                    
                    # Add to processed items (for matching with WooCommerce order items later)
                    processed_items.append({
                        "id": str(refund_item.id),
                        "name": refund_item.name,
                        "quantity": refund_quantity,
                        "price": float(refund_item.price),
                        "refund_amount": float(refund_item.subtotal)  # Changed from refund_amount to subtotal
                    })
                    
                except POSOrderItem.DoesNotExist:
                    logger.error(f"Order item with ID {item_id} not found")
                    continue
            
            # ── Credit service points reversal on refund ──
            try:
                from .models import CreditServicePoints, WooCreditLog, WooCreditedService
                
                # Scenario B: Refund of a $0 credited service REDEMPTION order
                # → Restore redeemed credits back to customer
                for item_data in items_to_refund:
                    try:
                        ri_id = item_data.get('id', '')
                        ri_qty = int(item_data.get('refundQuantity', 0))
                        if ri_qty <= 0:
                            continue
                        
                        # Find the order item to check its metadata
                        oi = None
                        try:
                            oi = POSOrderItem.objects.get(id=uuid.UUID(str(ri_id)), order=order)
                        except (POSOrderItem.DoesNotExist, ValueError):
                            item_name = item_data.get('name', '')
                            if item_name:
                                oi = POSOrderItem.objects.filter(order=order, name__iexact=item_name.strip()).first()
                        
                        if not oi or not oi.metadata:
                            continue
                        
                        meta = oi.metadata if isinstance(oi.metadata, dict) else {}
                        if not meta.get('isCreditedService', False):
                            continue
                        
                        cs_id = meta.get('creditedServiceId', '')
                        if not cs_id:
                            continue
                        
                        try:
                            credit_points = CreditServicePoints.objects.get(id=cs_id)
                            credit_points.points += ri_qty
                            credit_points.save()
                            
                            credit_points.log_credit_change(
                                action="refund_restore",
                                points_change=ri_qty,
                                order_number=order.order_number or 'UNKNOWN',
                                reason=f"Refund: credit restored for {oi.name}",
                                notes=f"Refund restored {ri_qty} credit(s) | Order: {order.order_number}",
                                source="POS"
                            )
                            logger.info(f"✅ Restored {ri_qty} credit(s) for {oi.name} (CreditServicePoints ID: {cs_id})")
                        except CreditServicePoints.DoesNotExist:
                            logger.warning(f"⚠️ CreditServicePoints not found for ID {cs_id}, skipping credit restore")
                    except Exception as cs_err:
                        logger.error(f"Error restoring credited service credit: {str(cs_err)}")
                
                # Scenario A: Refund of a PURCHASE order that originally earned credits
                # → Subtract the earned credits from customer's balance
                try:
                    credit_logs = WooCreditLog.objects.filter(
                        order_number=order.order_number,
                        action='add'
                    )
                    for log_entry in credit_logs:
                        try:
                            wcs = WooCreditedService.objects.get(id=log_entry.woo_credited_service_id)
                            # Find the CreditServicePoints record for this customer+product
                            csp = CreditServicePoints.objects.filter(
                                contact_woo_id=int(wcs.contact_woo_id) if wcs.contact_woo_id.isdigit() else None,
                                product__woo_product_id=int(wcs.product_id) if wcs.product_id.isdigit() else None,
                            ).first()
                            
                            if not csp:
                                # Try by customer UUID
                                csp = CreditServicePoints.objects.filter(
                                    customer__id=wcs.contact_woo_id if not wcs.contact_woo_id.isdigit() else None,
                                ).first()
                            
                            if csp:
                                points_to_subtract = log_entry.points_change
                                csp.points = max(0, csp.points - points_to_subtract)
                                csp.save()
                                
                                csp.log_credit_change(
                                    action="refund_reverse",
                                    points_change=-points_to_subtract,
                                    order_number=order.order_number or 'UNKNOWN',
                                    reason=f"Refund: credits reversed for purchase",
                                    notes=f"Reversed {points_to_subtract} credit(s) earned from order {order.order_number}",
                                    source="POS"
                                )
                                logger.info(f"✅ Reversed {points_to_subtract} earned credit(s) for order {order.order_number}")
                            else:
                                logger.warning(f"⚠️ CreditServicePoints not found for WooCreditedService ID {log_entry.woo_credited_service_id}")
                        except WooCreditedService.DoesNotExist:
                            logger.warning(f"⚠️ WooCreditedService not found for ID {log_entry.woo_credited_service_id}")
                        except Exception as rev_err:
                            logger.error(f"Error reversing earned credits: {str(rev_err)}")
                except Exception as log_err:
                    logger.error(f"Error querying credit logs for reversal: {str(log_err)}")
                    
            except Exception as credit_err:
                logger.error(f"Error in credit service points reversal: {str(credit_err)}")
            
            # If this is a WooCommerce order, create a refund in WooCommerce
            woo_refund_id = None
            woo_refund_response = None
            
            # Skip WooCommerce refund creation for $0 orders (WooCommerce rejects $0 refund amounts)
            skip_woo_refund = False
            if woo_order_id and refund_amount == 0:
                logger.info(f"⏭️ Skipping WooCommerce refund creation for $0 order {order_id} (WooCommerce rejects $0 refunds)")
                skip_woo_refund = True
                
                # Still update WooCommerce order status to 'refunded' for full $0 refunds
                try:
                    wc_status = WooCommerceAPI()
                    logger.info(f"Updating WooCommerce order {woo_order_id} status to 'refunded' (skipped $0 refund creation)")
                    status_resp = wc_status.update_order_status(woo_order_id, 'refunded')
                    if status_resp.get('status') == 'success':
                        logger.info(f"✅ Successfully updated WooCommerce order {woo_order_id} status to 'refunded'")
                        refund.metadata['woo_status_updated'] = True
                        refund.save()
                    else:
                        logger.error(f"Failed to update WooCommerce order status: {status_resp.get('message')}")
                        refund.metadata['woo_status_update_error'] = status_resp.get('message')
                        refund.save()
                except Exception as e:
                    logger.error(f"Error updating WooCommerce order status for $0 refund: {str(e)}")
                    refund.metadata['woo_status_update_error'] = str(e)
                    refund.save()
            
            if woo_order_id and not skip_woo_refund:
                try:
                    # Initialize WooCommerce API
                    wc = WooCommerceAPI()
                    
                    # First, fetch the WooCommerce order to get proper line item IDs
                    logger.info(f"Fetching WooCommerce order {woo_order_id} to get line item IDs")
                    woo_order = wc.get_order(woo_order_id)
                    
                    if not woo_order:
                        logger.error(f"Could not fetch WooCommerce order {woo_order_id}")
                        raise Exception(f"WooCommerce order {woo_order_id} not found")
                    
                    # Determine if any item has a custom (partial dollar) refund amount
                    has_partial_dollar_refund = any(
                        item_data.get('custom_amount') is not None 
                        for item_data in items_to_refund
                    )
                    order_total_val = float(order.total) if order.total is not None else 0.0
                    # Calculate cumulative refunded amount (prior refunds + this one)
                    prior_refunds_total = POSOrderRefund.objects.filter(order=order).exclude(id=refund.id).aggregate(
                        total=Sum('refund_amount'))['total'] or 0
                    cumulative_refunded = float(prior_refunds_total) + float(refund_amount)
                    is_full_amount_refund = cumulative_refunded >= order_total_val
                    
                    formatted_amount = str(float(refund_amount))
                    
                    if has_partial_dollar_refund and not is_full_amount_refund:
                        # PARTIAL DOLLAR REFUND: Only send amount, no line items
                        # WooCommerce misinterprets refund_total on line items for partial amounts
                        logger.info(f"Partial dollar refund detected. Sending WooCommerce refund with amount only (no line items): ${formatted_amount}")
                        refund_data = {
                            "amount": formatted_amount,
                            "reason": refund_reason or "Partial refund processed via POS",
                            "api_refund": False,
                        }
                    else:
                        # FULL ITEM REFUND: Include line items for proper WooCommerce tracking
                        woo_line_items = []
                        for woo_item in woo_order.get('line_items', []):
                            woo_line_item_id = woo_item.get('id')
                            woo_item_name = woo_item.get('name', '')
                            
                            for refund_item in processed_items:
                                if refund_item['name'].lower() == woo_item_name.lower():
                                    refund_qty = refund_item['quantity']
                                    refund_total = refund_item['refund_amount']
                                    
                                    woo_line_items.append({
                                        "id": int(woo_line_item_id),
                                        "quantity": int(refund_qty),
                                        "refund_total": float(refund_total)
                                    })
                                    logger.info(f"Matched refund item '{refund_item['name']}' to WooCommerce line item {woo_line_item_id} with refund_total: {float(refund_total)}")
                                    break
                        
                        logger.info(f"Full refund. WooCommerce line items: {json.dumps(woo_line_items, indent=2)}")
                        refund_data = {
                            "amount": formatted_amount,
                            "reason": refund_reason or "Refund processed via POS",
                            "api_refund": False,
                            "line_items": woo_line_items
                        }
                    
                    # Check if order has a valid payment gateway
                    payment_method = woo_order.get('payment_method', '')
                    payment_method_title = woo_order.get('payment_method_title', '')
                    logger.info(f"Order payment method: {payment_method} ({payment_method_title})")
                    
                    logger.info(f"WooCommerce refund data: {json.dumps(refund_data, indent=2)}")
                    
                    # Create the refund in WooCommerce (record-keeping only)
                    logger.info(f"Creating WooCommerce refund for order {woo_order_id} with api_refund=False (POS handles payment refund)")
                    response = wc.wcapi.post(f"orders/{woo_order_id}/refunds", refund_data)
                    
                    if response.status_code in [200, 201]:
                        woo_refund_response = response.json()
                        woo_refund_id = woo_refund_response.get('id')
                        logger.info(f"WooCommerce refund created successfully: {woo_refund_id}")
                        
                        # Log full refund response for debugging (includes gateway details)
                        logger.info(f"Full WooCommerce refund response: {json.dumps(woo_refund_response, indent=2)}")
                        
                        # WooCommerce refund created (record-keeping only)
                        # Now process actual payment refund via POS Authorize.Net
                        pos_refund_success = False
                        pos_refund_message = ""
                        
                        try:
                            # Calculate refund amounts based on payment method
                            credit_card_amount = 0
                            manual_refund_amount = 0
                            manual_refund_methods = []
                            
                            if payment_info:
                                payment_type = payment_info.get('type', '')
                                
                                # Scenario 1 & 2: Regular or Split with credit card
                                if payment_type == 'split':
                                    split_payments = payment_info.get('splitPayments', [])
                                    for split in split_payments:
                                        method = split.get('method', {})
                                        amount = float(split.get('amount', 0))
                                        
                                        if method.get('type') == 'credit':
                                            credit_card_amount = amount
                                        else:
                                            # Cash, points, etc - manual refund
                                            manual_refund_amount += amount
                                            manual_refund_methods.append({
                                                'type': method.get('type', 'unknown'),
                                                'name': method.get('name', 'Unknown'),
                                                'amount': amount
                                            })
                                elif payment_type == 'credit':
                                    # Scenario 2: Credit card only
                                    credit_card_amount = refund_amount
                                else:
                                    # Scenario 3: No credit card (cash, points, etc)
                                    manual_refund_amount = refund_amount
                                    manual_refund_methods.append({
                                        'type': payment_type,
                                        'name': payment_info.get('name', payment_type),
                                        'amount': refund_amount
                                    })
                            else:
                                # No payment info - assume credit card for backward compatibility
                                credit_card_amount = refund_amount
                            
                            logger.info(f"💳 Refund breakdown - Credit card: ${credit_card_amount}, Manual: ${manual_refund_amount}")
                            logger.info(f"📝 Manual refund methods: {manual_refund_methods}")
                            
                            # Check if refund should be as points instead of money
                            points_added = 0
                            if refund_as_points:
                                # Skip payment gateway refund and add YITH points instead
                                logger.info(f"🎯 Processing refund as YITH points for ${refund_amount}")
                                
                                # Get customer WooCommerce ID from order
                                customer_woo_id = None
                                if order.contact and hasattr(order.contact, 'woo_customer_id'):
                                    customer_woo_id = order.contact.woo_customer_id
                                
                                if customer_woo_id:
                                    try:
                                        # Import helper function from views_woocommerce_points
                                        from .views_woocommerce_points import perform_points_adjustment
                                        
                                        # Call helper function directly (no request object needed)
                                        points_result = perform_points_adjustment(
                                            customer_woo_id,
                                            int(refund_amount),  # 1 dollar = 1 point
                                            f'Refund for order {order.order_number}: {refund_reason}'
                                        )
                                        
                                        if points_result.get('success'):
                                            points_added = int(refund_amount)
                                            pos_refund_success = True
                                            pos_refund_message = f"Refunded ${refund_amount} as {points_added} YITH points"
                                            logger.info(f"✅ {pos_refund_message}")
                                        else:
                                            error_msg = points_result.get('error', 'Unknown error')
                                            pos_refund_success = False
                                            pos_refund_message = f"Failed to add YITH points: {error_msg}"
                                            logger.error(f"❌ {pos_refund_message}")
                                    except Exception as points_error:
                                        pos_refund_success = False
                                        pos_refund_message = f"Error adding YITH points: {str(points_error)}"
                                        logger.error(f"❌ {pos_refund_message}")
                                else:
                                    pos_refund_success = False
                                    pos_refund_message = "Customer does not have WooCommerce ID for points refund"
                                    logger.error(f"❌ {pos_refund_message}")
                            else:
                                # Process POS payment gateway refund (only for credit card portion)
                                if credit_card_amount > 0:
                                    logger.info(f"Processing Authorize.Net refund for ${credit_card_amount}")
                                    pos_refund_result = process_pos_payment_refund(order, credit_card_amount, refund_reason, frontend_transaction_id, frontend_card_last4)
                                    
                                    if pos_refund_result.get('success'):
                                        pos_refund_success = True
                                        pos_refund_message = pos_refund_result.get('message', 'Refund processed successfully')
                                        action_type = pos_refund_result.get('action_type', 'REFUND')
                                        logger.info(f"✅ Authorize.Net {action_type} successful: {pos_refund_message}")
                                    else:
                                        pos_refund_success = False
                                        pos_refund_message = pos_refund_result.get('message', 'Unknown error')
                                        pos_refund_error_code = pos_refund_result.get('error_code', '')
                                        logger.error(f"❌ Authorize.Net refund failed: {pos_refund_message}")
                                else:
                                    # No credit card payment - skip gateway refund
                                    pos_refund_success = True  # Consider successful since no gateway refund needed
                                    pos_refund_message = "No credit card payment to refund via gateway"
                                    logger.info(f"ℹ️ {pos_refund_message}")
                                
                        except Exception as pos_refund_error:
                            pos_refund_message = f"Error processing POS payment refund: {str(pos_refund_error)}"
                            logger.error(pos_refund_message)
                        
                        # Update the refund record with WooCommerce refund ID and POS gateway info
                        refund.metadata['woo_refund_id'] = woo_refund_id
                        refund.metadata['refunded_payment'] = pos_refund_success  # POS gateway refund status
                        refund.metadata['refund_as_points'] = refund_as_points  # Track if refunded as points
                        refund.metadata['points_added'] = points_added  # Track points added
                        refund.metadata['gateway_response'] = {
                            'refunded_payment': pos_refund_success,
                            'gateway': 'yith_points' if refund_as_points else 'pos_authorize_net',
                            'message': pos_refund_message,
                            'error_code': pos_refund_error_code if 'pos_refund_error_code' in locals() else '',
                            'woocommerce_record_only': True,
                            'refund_as_points': refund_as_points,
                            'points_added': points_added
                        }
                        refund.save()
                        
                        logger.info(f"POS payment gateway refund status: {pos_refund_success}")
                        
                        # Determine if this is a full or partial refund
                        is_full_refund = float(refund_amount) >= float(order.total)
                        woo_target_status = 'refunded' if is_full_refund else 'completed'
                        
                        # Only update WooCommerce order status to 'refunded' for FULL refunds
                        if is_full_refund and order.status in ['completed', 'processing']:
                            logger.info(f"Full refund detected (${refund_amount} >= ${order.total}). Updating WooCommerce order {woo_order_id} status to 'refunded'")
                            try:
                                status_update_response = wc.update_order_status(woo_order_id, 'refunded')
                                
                                if status_update_response.get('status') == 'success':
                                    logger.info(f"Successfully updated WooCommerce order {woo_order_id} status to 'refunded'")
                                    refund.metadata['woo_status_updated'] = True
                                    refund.save()
                                else:
                                    logger.error(f"Failed to update WooCommerce order status: {status_update_response.get('message')}")
                                    refund.metadata['woo_status_update_error'] = status_update_response.get('message')
                                    refund.save()
                            except Exception as status_e:
                                logger.error(f"Error updating WooCommerce order status: {str(status_e)}")
                                refund.metadata['woo_status_update_error'] = str(status_e)
                                refund.save()
                        else:
                            logger.info(f"Partial refund detected (${refund_amount} < ${order.total}). Keeping WooCommerce order {woo_order_id} status as-is.")
                    else:
                        # WooCommerce refund creation failed
                        error_msg = f"Failed to create WooCommerce refund: {response.text}"
                        logger.error(error_msg)
                        refund.metadata['woo_refund_error'] = error_msg
                        refund.save()
                        # Continue with local refund even if WooCommerce refund fails
                
                except Exception as e:
                    error_msg = f"Error creating WooCommerce refund: {str(e)}"
                    logger.error(error_msg)
                    refund.metadata['woo_refund_error'] = error_msg
                    refund.save()
                    # Continue with local refund even if WooCommerce refund fails
            
            # Update the order status based on whether this is a full or partial refund
            order_total_val = float(order.total) if order.total is not None else 0.0
            # Calculate cumulative refunded amount (all refunds including this one)
            prior_refunds_total = POSOrderRefund.objects.filter(order=order).exclude(id=refund.id).aggregate(
                total=Sum('refund_amount'))['total'] or 0
            cumulative_refunded = float(prior_refunds_total) + float(refund_amount)
            is_full_refund = cumulative_refunded >= order_total_val
            if order.status in ['completed', 'processing']:
                if is_full_refund:
                    logger.info(f"Full refund (${refund_amount} >= ${order.total}). Updating order {order.order_number} status to 'refunded'")
                    order.status = 'refunded'
                    order.save()
                else:
                    logger.info(f"Partial refund (${refund_amount} < ${order.total}). Keeping order {order.order_number} status as '{order.status}'")
            else:
                logger.info(f"Order {order.order_number} status is {order.status}, not updating")
                
            # Determine overall refund status based on gateway result
            # Per Chris: "Make sure we are acting upon authorize.net feedback and not just posting refund status without being sure"
            gateway_response_data = refund.metadata.get('gateway_response', {})
            gateway_refund_failed = gateway_response_data.get('refunded_payment') == False and not refund_as_points
            gateway_error_code = gateway_response_data.get('error_code', '')
            
            if gateway_refund_failed and 'credit_card_amount' in locals() and credit_card_amount > 0:
                # Gateway refund FAILED - report accurately
                overall_status = "partial_success"
                overall_message = (
                    f"WooCommerce refund record created, but payment gateway refund FAILED. "
                    f"{gateway_response_data.get('message', 'Unknown gateway error')}. "
                    f"The customer has NOT been refunded to their card."
                )
            else:
                overall_status = "success"
                overall_message = "Refund processed successfully"
            
            # Auto-send refund receipt email to customer
            refund_email_sent = False
            customer_email = order.contact.email if order.contact else None
            if customer_email:
                try:
                    from django.core.mail import EmailMultiAlternatives, get_connection
                    from django.conf import settings as django_settings
                    from .views_receipts import (
                        _build_refund_receipt_html, _build_pdf_refund_receipt_html,
                        _html_to_pdf, _enrich_order_subscription_frequency
                    )
                    import os

                    # Try to build the rich refund receipt using WooCommerce order data
                    if woo_order_id:
                        wc_for_email = WooCommerceAPI()
                        woo_resp = wc_for_email.wcapi.get(f'orders/{woo_order_id}')
                        if woo_resp.status_code == 200:
                            order_data_for_email = woo_resp.json()
                            _enrich_order_subscription_frequency(order_data_for_email)

                            # Inject actual refund method based on payment type
                            # Check if this is a $0 credited service redemption order
                            all_items_credited = order.items.exists() and all(
                                (item.metadata if isinstance(item.metadata, dict) else {}).get('isCreditedService', False)
                                for item in order.items.all()
                            )
                            if refund_as_points:
                                order_data_for_email['_refund_method_display'] = 'Points'
                            elif all_items_credited and order.total is not None and float(order.total) == 0:
                                order_data_for_email['_refund_method_display'] = 'Credited Services'
                            elif payment_info:
                                order_data_for_email['_refund_method_display'] = _get_refund_method_display(payment_info)
                            else:
                                order_data_for_email['_refund_method_display'] = 'Cash'

                            # Inject the actual refunded items so templates don't show all order items
                            if 'processed_items' in locals() and processed_items:
                                order_data_for_email['_refunded_items'] = [
                                    {
                                        'name': pi['name'],
                                        'quantity': pi['quantity'],
                                        'total': str(pi['refund_amount']),
                                        'sku': '',
                                        'product_id': None,
                                        'meta_data': [],
                                    }
                                    for pi in processed_items
                                ]
                                # NUCLEAR FIX: Also inject a simple name list for bulletproof filtering
                                order_data_for_email['_refunded_item_names'] = [
                                    pi['name'].lower().strip() for pi in processed_items
                                ]
                                logger.info(f"📋 Injected _refunded_items + _refunded_item_names: {order_data_for_email['_refunded_item_names']}")
                            else:
                                logger.warning(f"📋 NO _refunded_items injected! processed_items exists={('processed_items' in locals())}, len={len(processed_items) if 'processed_items' in locals() else 'N/A'}")

                            # Log what _build_refund_receipt_html will see
                            logger.info(f"📋 order_data keys with underscore: {[k for k in order_data_for_email if k.startswith('_')]}")
                            logger.info(f"📋 line_items count: {len(order_data_for_email.get('line_items', []))}, refunds count: {len(order_data_for_email.get('refunds', []))}")

                            html_body, _, _ = _build_refund_receipt_html(order_data_for_email)
                            pdf_html = _build_pdf_refund_receipt_html(order_data_for_email)
                        else:
                            html_body = None
                    else:
                        html_body = None

                    if html_body:
                        pdf_bytes = _html_to_pdf(pdf_html)
                        woo_number = order_data_for_email.get('number', order.order_number)
                        subject = f'Your Doctors Studio Refund Receipt — Order #{woo_number}'
                        text_content = f'Refund receipt for Order #{woo_number}'

                        from .email_sender import send_pos_email
                        attachments = [(f'Refund_Receipt_Order_{woo_number}.pdf', pdf_bytes, 'application/pdf')] if pdf_bytes else None
                        refund_email_sent = send_pos_email(
                            subject=subject,
                            text_content=text_content,
                            html_content=html_body,
                            to_email=customer_email,
                            email_type='auto_refund_receipt',
                            attachments=attachments,
                            related_order=str(woo_number),
                        )
                    else:
                        logger.warning(f"📧 Could not build refund receipt HTML for order {order.order_number}")
                except Exception as email_err:
                    logger.error(f"📧 Error auto-sending refund receipt: {email_err}")
            else:
                logger.info(f"📧 No customer email for order {order.order_number}, skipping auto refund receipt")

            # Return the response with refund breakdown
            response_data = {
                "status": overall_status,
                "message": overall_message,
                "refund_id": str(refund.id),
                "order_id": str(order.id),
                "refund_amount": float(refund_amount),
                "items": processed_items,
                "woo_refund_id": woo_refund_id,
                "woo_order_id": woo_order_id,
                "created_at": refund.created_at.isoformat(),
                "created_by": created_by,
                "reason": refund_reason,
                "order_status": order.status,  # Include the updated order status in the response
                "is_partial_refund": not is_full_refund,  # True if refund amount < order total
                "order_total": float(order.total) if order.total is not None else 0.0,  # Original order total for frontend reference
                "customer": {
                    "id": str(order.contact.id) if order.contact else None,
                    "name": f"{order.contact.first_name} {order.contact.last_name}" if order.contact else "Unknown Customer",
                    "email": order.contact.email if order.contact else None
                },
                "woocommerce_refund_status": "success" if woo_refund_id else ("skipped" if skip_woo_refund else ("failed" if woo_order_id else "not_applicable")),
                "woocommerce_status_update": refund.metadata.get('woo_status_updated', False) if woo_order_id else None,
                "payment_gateway_refund": refund.metadata.get('refunded_payment', False),
                "gateway_details": gateway_response_data if gateway_response_data else None,
                "gateway_refund_failed": gateway_refund_failed,
                # Add refund breakdown
                "refund_breakdown": {
                    "total_amount": float(refund_amount),
                    "credit_card_refund": float(credit_card_amount) if 'credit_card_amount' in locals() else 0,
                    "manual_refund": float(manual_refund_amount) if 'manual_refund_amount' in locals() else 0,
                    "manual_refund_methods": manual_refund_methods if 'manual_refund_methods' in locals() else [],
                    "instructions": f"Please return ${manual_refund_amount:.2f} to customer manually" if 'manual_refund_amount' in locals() and manual_refund_amount > 0 else None
                },
                "points_refund": {
                    "refund_as_points": refund_as_points,
                    "points_added": points_added if 'points_added' in locals() else 0,
                    "message": f"Added {points_added} points to customer account" if refund_as_points and points_added > 0 else None
                } if refund_as_points else None,
                "errors": {
                    "woo_refund_error": refund.metadata.get('woo_refund_error'),
                    "woo_status_update_error": refund.metadata.get('woo_status_update_error')
                } if refund.metadata.get('woo_refund_error') or refund.metadata.get('woo_status_update_error') else None,
                "refund_email_sent": refund_email_sent
            }
            
            from users.activity_log import log_activity
            customer_name = f"{order.contact.first_name} {order.contact.last_name}" if order.contact else "Unknown"
            log_activity(request, f"Processed refund ${refund_amount:.2f} for Order {order.order_number}", category='refund', details={
                'order_number': order.order_number,
                'order_id': str(order.id),
                'refund_id': str(refund.id),
                'refund_amount': float(refund_amount),
                'reason': refund_reason,
                'customer': customer_name,
                'is_partial': not is_full_refund,
                'items_refunded': len(processed_items),
            })
            
            return Response(response_data, status=status.HTTP_201_CREATED)
    
    except Exception as e:
        logger.error(f"Error processing refund: {str(e)}")
        return Response({"error": f"Failed to process refund: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_refund_history(request):
    """
    Get refund history from POSOrderRefundItem table
    
    Query parameters:
    - order_id: Filter by order ID
    - customer_id: Filter by customer ID
    - search: Search by refund number, order number, or product name
    - page: Page number for pagination
    - page_size: Number of items per page
    """
    try:
        # Get query parameters
        order_id = request.query_params.get('order_id')
        customer_id = request.query_params.get('customer_id')
        search_term = request.query_params.get('search')
        
        # Start with all refund items
        queryset = POSOrderRefundItem.objects.all().order_by('-created_at')
        
        # Apply filters if provided
        if order_id:
            queryset = queryset.filter(refund__order__id=order_id)
        
        if customer_id:
            queryset = queryset.filter(customer_id=customer_id)
        
        if search_term:
            search_filter = (
                Q(refund__refund_number__icontains=search_term) |
                Q(refund__order__order_number__icontains=search_term) |
                Q(name__icontains=search_term) |
                Q(customer_name__icontains=search_term) |
                Q(customer_email__icontains=search_term)
            )
            # Also search by WooCommerce order ID stored in order metadata
            try:
                woo_id = int(search_term)
                search_filter = search_filter | Q(refund__order__metadata__woo_order_id=woo_id)
            except (ValueError, TypeError):
                pass
            queryset = queryset.filter(search_filter)
        
        # Set up pagination
        paginator = PageNumberPagination()
        paginator.page_size = int(request.query_params.get('page_size', 10))
        paginator.page_query_param = 'page'
        paginator.page_size_query_param = 'page_size'
        
        # Paginate the queryset
        paginated_queryset = paginator.paginate_queryset(queryset, request)
        
        # Format the response data
        results = []
        for item in paginated_queryset:
            refund = item.refund
            order = refund.order if refund else None
            
            # Get WooCommerce order ID from metadata if available
            woo_order_id = None
            if order and order.metadata:
                woo_order_id = order.metadata.get('woo_order_id')
            
            result = {
                'id': str(item.id),
                'refund_id': str(refund.id) if refund else None,
                'refund_number': refund.refund_number if refund else None,
                'order_id': str(order.id) if order else None,
                'order_number': order.order_number if order else None,
                'woo_order_id': woo_order_id,
                'product_name': item.name,  # Using the name field from the model
                'quantity': item.quantity,
                'price': float(item.price),
                'subtotal': float(item.subtotal),
                'refund_reason': item.refund_reason or refund.refund_reason if refund else '',  # Use item reason or fallback to refund reason
                'notes': item.notes,
                'created_at': item.created_at.isoformat(),
                'customer_id': str(item.customer.id) if item.customer else item.customer_id,
                'customer_name': item.customer_name,
                'customer_email': item.customer_email
            }
            
            results.append(result)
        
        # Return paginated response
        return paginator.get_paginated_response(results)
    
    except Exception as e:
        logger.error(f"Error fetching refund history: {str(e)}")
        return Response({"error": f"Failed to fetch refund history: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_order_refunds(request, order_id):
    """
    Get all refunds for a specific order from POS database.
    Accepts POS order_number (e.g. ORD-xxx), POS UUID, or WooCommerce order ID.
    """
    try:
        order = None
        # Try by order_number
        order = POSOrder.objects.filter(order_number__icontains=str(order_id)).first()
        # Try by UUID
        if not order:
            try:
                order = POSOrder.objects.get(id=order_id)
            except (POSOrder.DoesNotExist, ValueError, Exception):
                pass
        # Try by WooCommerce order ID in metadata
        if not order:
            order = POSOrder.objects.filter(metadata__woo_order_id=str(order_id)).first()
            if not order:
                order = POSOrder.objects.filter(metadata__woo_order_id=int(order_id)).first() if str(order_id).isdigit() else None

        if not order:
            return Response({"error": "Order not found", "refunds": []}, status=status.HTTP_404_NOT_FOUND)

        refunds = POSOrderRefund.objects.filter(order=order).order_by('-created_at')
        refund_list = []
        for r in refunds:
            items = []
            for item in r.items.all():
                items.append({
                    'name': item.name,
                    'product_id': item.product_id or '',
                    'quantity': item.quantity,
                    'price': float(item.price),
                    'subtotal': float(item.subtotal),
                })
            refund_list.append({
                'id': str(r.id),
                'refund_number': r.refund_number,
                'amount': float(r.refund_amount),
                'reason': r.refund_reason,
                'status': r.status,
                'created_at': r.created_at.isoformat(),
                'created_by': r.created_by,
                'woo_refund_id': r.metadata.get('woo_refund_id') if r.metadata else None,
                'items': items,
            })

        order_total = float(order.total) if order.total else 0
        total_refunded = sum(r['amount'] for r in refund_list)

        return Response({
            'order_id': str(order.id),
            'order_number': order.order_number,
            'order_total': order_total,
            'total_refunded': total_refunded,
            'remaining_balance': max(0, order_total - total_refunded),
            'refund_count': len(refund_list),
            'refunds': refund_list,
        })

    except Exception as e:
        logger.error(f"Error fetching order refunds: {str(e)}")
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
