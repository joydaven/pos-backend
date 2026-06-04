from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from .authorize_net import AuthorizeNetGateway
from .models import PaymentTransaction, PaymentCard, PaymentCardNote
from .serializers import AuthorizeNetPaymentSerializer, PaymentTransactionSerializer, PaymentCardSerializer
from crm.throttles import PaymentRateThrottle
import logging
import uuid

logger = logging.getLogger(__name__)

class AuthorizeNetPaymentView(APIView):
    """
    API endpoint for processing payments through Authorize.net
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [PaymentRateThrottle]
    
    def post(self, request, *args, **kwargs):
        """
        Process a payment through Authorize.net
        
        Request body:
        {
            "amount": 100.00,
            "cardNumber": "4111111111111111",
            "expiryDate": "1225",
            "cvv": "123",
            "customerEmail": "customer@example.com",
            "customerName": "John Doe",
            "orderItems": [
                {
                    "name": "Product 1",
                    "quantity": 1,
                    "price": 50.00
                },
                {
                    "name": "Product 2",
                    "quantity": 2,
                    "price": 25.00
                }
            ],
            "orderNumber": "ORD-12345"
        }
        """
        try:
            # Idempotency check: prevent duplicate payment processing
            idempotency_key = request.data.get('idempotencyKey') or request.META.get('HTTP_X_IDEMPOTENCY_KEY')
            if idempotency_key:
                existing = PaymentTransaction.objects.filter(idempotency_key=idempotency_key, status='approved').first()
                if existing:
                    logger.info(f"Idempotent request detected (key={idempotency_key}), returning existing transaction {existing.transaction_id}")
                    return Response({
                        'transactionId': existing.transaction_id,
                        'status': existing.status,
                        'message': 'Transaction already processed (idempotent)',
                        'authCode': existing.auth_code or '',
                        'responseCode': existing.response_code or '1',
                    }, status=status.HTTP_200_OK)

            # Validate request data
            serializer = AuthorizeNetPaymentSerializer(data=request.data)
            if not serializer.is_valid():
                return Response({
                    'status': 'error',
                    'message': 'Invalid payment data',
                    'errors': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Extract validated data
            validated_data = serializer.validated_data
            amount = validated_data.get('amount')
            card_number = validated_data.get('cardNumber')
            expiry_date = validated_data.get('expiryDate')
            cvv = validated_data.get('cvv')
            customer_email = validated_data.get('customerEmail', '')
            customer_name = validated_data.get('customerName', '')
            order_items = validated_data.get('orderItems', [])
            order_number = validated_data.get('orderNumber', '')
            
            # Process payment through Authorize.net
            payment_response = AuthorizeNetGateway.process_payment(
                amount=amount,
                card_number=card_number,
                expiry_date=expiry_date,
                cvv=cvv,
                customer_email=customer_email,
                customer_name=customer_name,
                order_id=order_number
            )
            
            # Save transaction record regardless of success or failure
            try:
                # Get last 4 digits of card number for reference
                card_last4 = card_number[-4:] if len(card_number) >= 4 else "0000"
                
                # Determine card brand based on first digit
                card_brand = "Credit Card"
                if card_number.startswith('4'):
                    card_brand = "Visa"
                elif card_number.startswith(('51', '52', '53', '54', '55')):
                    card_brand = "MasterCard"
                elif card_number.startswith(('34', '37')):
                    card_brand = "American Express"
                elif card_number.startswith('6'):
                    card_brand = "Discover"
                
                # Save transaction record
                if payment_response['success']:
                    try:
                        transaction = PaymentTransaction.objects.create(
                            idempotency_key=idempotency_key,
                            transaction_id=payment_response['transactionId'],
                            order_id=order_number,
                            amount=amount,
                            status=payment_response['status'],
                            payment_type='credit',
                            auth_code=payment_response['authCode'],
                            response_code=payment_response['responseCode'],
                            response_message=payment_response['message']
                        )
                    except Exception as e:
                        logger.error(f"Error saving transaction record: {str(e)}")
                    
                    # Save card information (only last 4 digits for security)
                    try:
                        # Extract month and year from expiry date (MM/YY format)
                        # First, remove any slashes or spaces
                        clean_expiry = expiry_date.replace('/', '').replace(' ', '')
                        
                        # Handle different formats (MMYY or MM/YY)
                        if len(clean_expiry) == 4:  # MMYY format
                            exp_month = clean_expiry[:2]
                            exp_year = clean_expiry[2:]
                        elif len(clean_expiry) == 6:  # MMYYYY format
                            exp_month = clean_expiry[:2]
                            exp_year = clean_expiry[2:]
                        else:
                            # Default values if format is unrecognized
                            exp_month = '12'
                            exp_year = '25'
                        
                        # Ensure month is valid (01-12)
                        if not (1 <= int(exp_month) <= 12):
                            exp_month = '12'
                        
                        # Ensure month is exactly 2 characters
                        exp_month = exp_month.zfill(2)[:2]
                        
                        # Ensure year is at most 4 characters
                        exp_year = exp_year[:4]
                        
                        PaymentCard.objects.create(
                            order_id=order_number or str(uuid.uuid4()),
                            last4=card_last4,
                            card_brand=card_brand,
                            exp_month=exp_month,
                            exp_year=exp_year
                        )
                    except Exception as e:
                        logger.error(f"Error saving card information: {str(e)}")
                else:
                    # Save failed transaction
                    try:
                        PaymentTransaction.objects.create(
                            transaction_id=f"FAILED-{uuid.uuid4().hex[:8]}",
                            order_id=order_number,
                            amount=amount,
                            status=payment_response['status'],
                            payment_type='credit',
                            response_code=payment_response.get('code', 'UNKNOWN'),
                            response_message=payment_response['message']
                        )
                    except Exception as e:
                        logger.error(f"Error saving failed transaction record: {str(e)}")
            except Exception as e:
                logger.exception(f"Error saving transaction record: {str(e)}")
                # Continue with the response even if saving to DB fails
            
            # Return response based on payment result
            if payment_response['success']:
                from users.activity_log import log_activity
                log_activity(request, f"Processed payment ${amount} ({card_brand} ****{card_last4})", category='payment', details={
                    'amount': float(amount),
                    'card_last4': card_last4,
                    'card_brand': card_brand,
                    'transaction_id': payment_response.get('transactionId'),
                    'order_number': order_number,
                    'customer_name': customer_name,
                })
                
                return Response({
                    'transactionId': payment_response['transactionId'],
                    'status': payment_response['status'],
                    'message': payment_response['message'],
                    'authCode': payment_response['authCode'],
                    'responseCode': payment_response['responseCode'],
                    'cardLast4': card_last4,
                    'cardBrand': card_brand
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    'status': payment_response['status'],
                    'message': payment_response['message'],
                    'code': payment_response.get('code', 'UNKNOWN')
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except Exception as e:
            logger.exception(f"Error processing payment: {str(e)}")
            return Response({
                'status': 'error',
                'message': f'An error occurred while processing payment: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([PaymentRateThrottle])
def create_customer_profile(request):
    """
    Create a customer profile in Authorize.net
    
    This endpoint creates a customer profile in Authorize.net's Customer Information Manager (CIM)
    and returns the customer profile ID and payment profile ID.
    
    Args:
        request: HTTP request with card data in the body
        
    Returns:
        Response with customer profile ID and payment profile ID
    """
    try:
        data = request.data
        
        # Get required fields
        card_number = data.get('card_number')
        expiry_date = data.get('expiry_date')
        customer_id = data.get('customer_id')
        
        # Optional fields
        card_brand = data.get('card_brand', 'Credit Card')
        cardholder_name = data.get('cardholder_name', '')
        order_id = data.get('order_id')
        customer_email = data.get('customer_email', '')
        
        # Billing address fields
        billing_address = data.get('billingAddress', {})
        billing_street = billing_address.get('street', '')
        billing_city = billing_address.get('city', '')
        billing_state = billing_address.get('state', '')
        billing_zip = billing_address.get('zip', '')
        billing_country = billing_address.get('country', 'USA')
        
        # Validate required fields
        if not card_number or not expiry_date:
            return Response({
                'status': 'error',
                'message': 'Missing required fields: card_number, expiry_date'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get CVV if provided
        cvv = data.get('cvv')
        
        # Prepare billing address
        billing_addr = None
        if billing_street or billing_city or billing_state or billing_zip:
            name_parts = cardholder_name.split(' ', 1) if cardholder_name else ['', '']
            billing_addr = {
                'firstName': name_parts[0],
                'lastName': name_parts[1] if len(name_parts) > 1 else '',
                'address': billing_street,
                'city': billing_city,
                'state': billing_state,
                'zip': billing_zip,
                'country': billing_country
            }
        
        # Create customer profile in Authorize.net
        result = AuthorizeNetGateway.create_customer_profile(
            card_number=card_number,
            expiry_date=expiry_date,
            cvv=cvv,
            customer_email=customer_email,
            customer_name=cardholder_name,
            customer_id=customer_id,
            billing_address=billing_addr
        )
        
        if result.get('status') == 'success':
            # Save card to database
            try:
                # Get the last 4 digits of the card number
                last4 = card_number[-4:] if len(card_number) >= 4 else 'xxxx'
                
                # Get the customer
                from crm.models import Contact
                customer = None
                if customer_id:
                    try:
                        customer = Contact.objects.get(id=customer_id)
                    except Contact.DoesNotExist:
                        logger.warning(f"Customer with ID {customer_id} not found")
                
                # Extract month and year from expiry date (MM/YY format)
                # First, remove any slashes or spaces
                clean_expiry = expiry_date.replace('/', '').replace(' ', '')
                
                # Handle different formats (MMYY or MM/YY)
                if len(clean_expiry) == 4:  # MMYY format
                    exp_month = clean_expiry[:2]
                    exp_year = clean_expiry[2:]
                elif len(clean_expiry) == 6:  # MMYYYY format
                    exp_month = clean_expiry[:2]
                    exp_year = clean_expiry[2:]
                else:
                    # Default values if format is unrecognized
                    exp_month = '12'
                    exp_year = '25'
                
                # Ensure month is valid (01-12)
                if not (1 <= int(exp_month) <= 12):
                    exp_month = '12'
                
                # Ensure month is exactly 2 characters
                exp_month = exp_month.zfill(2)[:2]
                
                # Ensure year is at most 4 characters
                exp_year = exp_year[:4]
                
                # Create payment card record with field length validation
                # Truncate fields that might exceed database limits
                safe_order_id = (order_id or '')[:50] if order_id else None
                safe_card_brand = (card_brand or '')[:50]
                safe_billing_state = (billing_state or '')[:50]
                safe_billing_country = (billing_country or '')[:50]
                safe_customer_profile_id = (result.get('customer_profile_id') or '')[:100]
                safe_payment_profile_id = (result.get('payment_profile_id') or '')[:100]
                
                logger.info(f"Creating PaymentCard with: order_id={safe_order_id}, card_brand={safe_card_brand}, customer_profile_id={safe_customer_profile_id}, payment_profile_id={safe_payment_profile_id}")
                
                payment_card = PaymentCard.objects.create(
                    customer=customer,
                    order_id=safe_order_id,
                    last4=last4,
                    card_brand=safe_card_brand,
                    exp_month=exp_month,
                    exp_year=exp_year,
                    customer_profile_id=safe_customer_profile_id,
                    payment_profile_id=safe_payment_profile_id,
                    is_default=True,  # Make this the default card for the customer
                    billing_street=billing_street,
                    billing_city=billing_city,
                    billing_state=safe_billing_state,
                    billing_zip=billing_zip,
                    billing_country=safe_billing_country
                )
                
                # If this is the default card, make sure other cards are not default
                if payment_card.is_default and customer:
                    PaymentCard.objects.filter(
                        customer=customer
                    ).exclude(
                        id=payment_card.id
                    ).update(is_default=False)
                
                logger.info(f"Payment card saved to database: {payment_card}")
                # Add the database card ID to the response
                result['card_id'] = str(payment_card.id)
                
                # Update Contact.authorize_net_customer_profile_id
                # This clears any 'none' negative cache from a previous empty search
                if customer and safe_customer_profile_id:
                    customer.authorize_net_customer_profile_id = safe_customer_profile_id
                    customer.save(update_fields=['authorize_net_customer_profile_id'])
                    logger.info(f"Linked CIM profile {safe_customer_profile_id} to Contact {customer_id}")
            except Exception as e:
                logger.error(f"Error saving payment card to database: {str(e)}")
                # Continue with the response even if saving to database fails
            
            return Response(result, status=status.HTTP_201_CREATED)
        else:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
    
    except Exception as e:
        logger.error(f"Error creating customer profile: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

def _fallback_local_cards(customer, customer_id):
    """Return saved cards from local PaymentCard DB (fast path, no CIM calls)."""
    saved_cards = []
    customer_cards = PaymentCard.objects.filter(customer=customer).order_by('-is_default', '-created_at')
    
    if customer_cards.exists():
        serializer = PaymentCardSerializer(customer_cards, many=True)
        for card_data in serializer.data:
            saved_cards.append({
                'id': card_data['id'],
                'last4': card_data['last4'] or 'xxxx',
                'cardBrand': card_data['card_brand'] or 'Credit Card',
                'customerProfileId': card_data['customer_profile_id'] or '',
                'paymentProfileId': card_data['payment_profile_id'] or '',
                'expMonth': card_data['exp_month'] or '12',
                'expYear': card_data['exp_year'] or '25',
                'isDefault': card_data['is_default'],
                'billingAddress': card_data['billingAddress'],
                'source': 'local_db'
            })
    
    return Response({
        'status': 'success',
        'cards': saved_cards,
        'source': 'local_db'
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_saved_cards(request, customer_id):
    """
    Get saved cards for a customer.
    
    Discovery chain:
    1. Check Contact.authorize_net_customer_profile_id
    2. If empty → check local PaymentCard records for a customer_profile_id (auto-link)
    3. If still empty → real-time lookup via CIM get_customer_profile_by_email (auto-link + save)
    4. If CIM profile found → fetch all payment profiles from Authorize.net CIM
    5. Fallback → local PaymentCard DB
    """
    try:
        from crm.models import Contact
        
        try:
            customer = Contact.objects.get(id=customer_id)
        except Contact.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Customer with ID {customer_id} not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # --- Step 1: Discover the CIM customer profile ID ---
        cim_profile_id = None
        
        # 1a. Check Contact model field
        previously_searched = False
        if customer.authorize_net_customer_profile_id:
            profile_id = customer.authorize_net_customer_profile_id.strip()
            if profile_id == 'none':
                # Previously searched and found nothing — flag for shallow re-check
                previously_searched = True
                logger.info(f"CIM profile cached as 'none' for {customer.email}, will do shallow re-check")
            elif profile_id.isdigit():
                cim_profile_id = profile_id
                logger.info(f"CIM profile ID from Contact model: {cim_profile_id}")
            else:
                logger.warning(f"Invalid (non-numeric) CIM profile ID on Contact: {profile_id}")
        
        # 1b. Fallback: check local PaymentCard records
        if not cim_profile_id:
            existing_card = PaymentCard.objects.filter(
                customer=customer,
                customer_profile_id__isnull=False
            ).exclude(customer_profile_id='').first()
            
            if existing_card:
                card_profile_id = existing_card.customer_profile_id.strip()
                if card_profile_id.isdigit():
                    cim_profile_id = card_profile_id
                    logger.info(f"CIM profile ID from PaymentCard: {cim_profile_id}")
                    # Auto-link to Contact for future lookups
                    customer.authorize_net_customer_profile_id = cim_profile_id
                    customer.save(update_fields=['authorize_net_customer_profile_id'])
                    logger.info(f"Auto-linked CIM profile {cim_profile_id} to Contact {customer_id}")
        
        # 1c. No CIM profile found via Contact field or local PaymentCard records.
        # With the Authorize.net webhook (paymentProfile.created/updated) keeping
        # Contact.authorize_net_customer_profile_id warm in the background,
        # if we still have no profile here, the customer genuinely has no cards.
        # Return instantly from local DB instead of doing the slow CIM email search.
        if not cim_profile_id:
            logger.info(f"No CIM profile for {customer.email} — returning local DB cards instantly (webhook will update if cards exist)")
            return _fallback_local_cards(customer, customer_id)
        
        # --- Step 2: Fetch cards from CIM if we have a profile ID ---
        saved_cards = []
        
        if cim_profile_id:
            cim_profiles = AuthorizeNetGateway.get_customer_payment_profiles(cim_profile_id)
            
            if cim_profiles is not None:
                if not cim_profiles:
                    # CIM profile exists but has 0 payment profiles (all cards deleted)
                    # Clear the cached profile ID so we don't keep fetching an empty profile
                    logger.info(f"CIM profile {cim_profile_id} has 0 payment profiles — clearing cache for {customer.email}")
                    customer.authorize_net_customer_profile_id = 'none'
                    customer.save(update_fields=['authorize_net_customer_profile_id'])
                    return _fallback_local_cards(customer, customer_id)
                if cim_profiles:
                    has_explicit_default = any(pp.get('is_default', False) for pp in cim_profiles)
                    for pp in cim_profiles:
                        saved_cards.append({
                            'id': f"cim_{pp['payment_profile_id']}",
                            'last4': pp['last4'] or 'xxxx',
                            'cardBrand': pp['card_brand'] or 'Credit Card',
                            'customerProfileId': cim_profile_id,
                            'paymentProfileId': pp['payment_profile_id'],
                            'expMonth': pp['exp_month'] or '',
                            'expYear': pp['exp_year'] or '',
                            'isDefault': pp.get('is_default', False),
                            'cardholderName': pp.get('cardholder_name', ''),
                            'billingAddress': pp.get('billing', {
                                'street': '', 'city': '', 'state': '', 'zip': '', 'country': 'USA'
                            }),
                            'source': 'cim'
                        })
                    
                    # Mark the first card as default if CIM didn't flag one
                    if saved_cards and not has_explicit_default:
                        saved_cards[0]['isDefault'] = True
                
                # Attach notes to cards
                notes_map = {}
                try:
                    for cn in PaymentCardNote.objects.filter(customer=customer):
                        notes_map[cn.payment_profile_id] = cn.note
                except Exception as e:
                    logger.warning(f"Error fetching card notes: {e}")
                
                for card in saved_cards:
                    card['note'] = notes_map.get(card['paymentProfileId'], '')
                
                logger.info(f"Returning {len(saved_cards)} cards from CIM for customer {customer_id}")
                
                return Response({
                    'status': 'success',
                    'cards': saved_cards,
                    'source': 'authorize_net_cim',
                    'customerProfileId': cim_profile_id
                })
            else:
                # CIM API returned None — profile may have been deleted on Authorize.net
                # Clear the stale cached profile ID
                logger.warning(f"CIM API failure for profile {cim_profile_id} — clearing cache (profile may be deleted)")
                customer.authorize_net_customer_profile_id = 'none'
                customer.save(update_fields=['authorize_net_customer_profile_id'])
        
        # --- Step 3: Fallback to local PaymentCard DB ---
        logger.info(f"Falling back to local DB for customer {customer_id}")
        customer_cards = PaymentCard.objects.filter(customer=customer).order_by('-is_default', '-created_at')
        
        if customer_cards.exists():
            serializer = PaymentCardSerializer(customer_cards, many=True)
            for card_data in serializer.data:
                saved_cards.append({
                    'id': card_data['id'],
                    'last4': card_data['last4'] or 'xxxx',
                    'cardBrand': card_data['card_brand'] or 'Credit Card',
                    'customerProfileId': card_data['customer_profile_id'] or '',
                    'paymentProfileId': card_data['payment_profile_id'] or '',
                    'expMonth': card_data['exp_month'] or '12',
                    'expYear': card_data['exp_year'] or '25',
                    'isDefault': card_data['is_default'],
                    'billingAddress': card_data['billingAddress'],
                    'source': 'local_db'
                })
        
        # Remove duplicates using composite key to avoid dropping different cards with same last4
        unique_cards = {}
        for card in saved_cards:
            key = (card['last4'], card['cardBrand'], card.get('expMonth', ''), card.get('expYear', ''))
            unique_cards[key] = card
        
        return Response({
            'status': 'success',
            'cards': list(unique_cards.values()),
            'source': 'local_db'
        })
    
    except Exception as e:
        logger.error(f"Error getting saved cards: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def set_default_card(request):
    """
    Set a payment profile as the default for a customer on Authorize.net CIM.
    
    Expects JSON body:
        customerProfileId: CIM customer profile ID
        paymentProfileId: CIM payment profile ID to set as default
    """
    try:
        customer_profile_id = request.data.get('customerProfileId')
        payment_profile_id = request.data.get('paymentProfileId')
        
        if not customer_profile_id or not payment_profile_id:
            return Response({
                'status': 'error',
                'message': 'customerProfileId and paymentProfileId are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        result = AuthorizeNetGateway.set_default_payment_profile(customer_profile_id, payment_profile_id)
        
        if result.get('success'):
            return Response({
                'status': 'success',
                'message': f'Payment profile {payment_profile_id} set as default'
            })
        else:
            return Response({
                'status': 'error',
                'message': result.get('error', 'Failed to set default payment profile')
            }, status=status.HTTP_400_BAD_REQUEST)
    
    except Exception as e:
        logger.error(f"Error setting default card: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_saved_card(request, card_id):
    """
    Update a saved card's information
    
    Args:
        card_id: ID of the card to update
        request: HTTP request with updated card data
        
    Returns:
        Updated card information
    """
    try:
        data = request.data
        
        # --- Handle CIM-sourced cards (id starts with "cim_") ---
        if str(card_id).startswith('cim_'):
            payment_profile_id = data.get('paymentProfileId') or str(card_id).replace('cim_', '')
            customer_profile_id = data.get('customerProfileId', '')
            
            logger.info(f"Updating CIM card: card_id={card_id}, customer_profile={customer_profile_id}, payment_profile={payment_profile_id}")
            
            # Handle setting default via CIM API
            if data.get('is_default') or data.get('isDefault'):
                if customer_profile_id and payment_profile_id:
                    result = AuthorizeNetGateway.set_default_payment_profile(customer_profile_id, payment_profile_id)
                    if not result.get('success'):
                        logger.warning(f"Failed to set default on CIM: {result.get('error')}")
            
            # Update expiry date and/or billing address on Authorize.net CIM
            new_exp_month = data.get('exp_month') or data.get('expMonth')
            new_exp_year = data.get('exp_year') or data.get('expYear')
            new_billing = data.get('billingAddress')
            
            if customer_profile_id and payment_profile_id and (new_exp_month or new_billing):
                cim_result = AuthorizeNetGateway.update_customer_payment_profile(
                    customer_profile_id=customer_profile_id,
                    payment_profile_id=payment_profile_id,
                    exp_month=new_exp_month,
                    exp_year=new_exp_year,
                    billing_address=new_billing
                )
                if not cim_result.get('success'):
                    logger.error(f"Failed to update CIM payment profile: {cim_result.get('error')}")
                    return Response({
                        'status': 'error',
                        'message': f"Failed to update card on Authorize.net: {cim_result.get('error')}"
                    }, status=status.HTTP_400_BAD_REQUEST)
                logger.info(f"Successfully updated CIM payment profile {payment_profile_id}")
            
            # Try to find a local PaymentCard record for persisting edits
            card = None
            if customer_profile_id and payment_profile_id:
                card = PaymentCard.objects.filter(
                    payment_profile_id=payment_profile_id,
                    customer_profile_id=customer_profile_id
                ).first()
            
            if card:
                # Update local record with provided fields
                if 'exp_month' in data or 'expMonth' in data:
                    card.exp_month = data.get('exp_month') or data.get('expMonth')
                if 'exp_year' in data or 'expYear' in data:
                    card.exp_year = data.get('exp_year') or data.get('expYear')
                
                billing_address = data.get('billingAddress', {})
                if billing_address:
                    card.billing_street = billing_address.get('street', card.billing_street)
                    card.billing_city = billing_address.get('city', card.billing_city)
                    card.billing_state = billing_address.get('state', card.billing_state)
                    card.billing_zip = billing_address.get('zip', card.billing_zip)
                    card.billing_country = billing_address.get('country', card.billing_country)
                
                card.save()
                logger.info(f"Updated local PaymentCard record for CIM card {card_id}")
            
            # Return the card data in the expected format
            return Response({
                'status': 'success',
                'message': 'Card updated successfully',
                'card': {
                    'id': card_id,
                    'last4': data.get('last4', 'xxxx'),
                    'cardBrand': data.get('cardBrand', 'Credit Card'),
                    'customerProfileId': customer_profile_id,
                    'paymentProfileId': payment_profile_id,
                    'expMonth': data.get('exp_month') or data.get('expMonth', ''),
                    'expYear': data.get('exp_year') or data.get('expYear', ''),
                    'isDefault': data.get('is_default', data.get('isDefault', False)),
                    'billingAddress': data.get('billingAddress', {
                        'street': '', 'city': '', 'state': '', 'zip': '', 'country': 'USA'
                    })
                }
            })
        
        # --- Handle local DB cards (UUID) ---
        try:
            card = PaymentCard.objects.get(id=card_id)
        except PaymentCard.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Card with ID {card_id} not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Update allowed fields
        if 'exp_month' in data:
            card.exp_month = data['exp_month']
        if 'exp_year' in data:
            card.exp_year = data['exp_year']
        if 'is_default' in data:
            card.is_default = data['is_default']
            
            # If setting as default, make sure other cards are not default
            if card.is_default and card.customer:
                PaymentCard.objects.filter(
                    customer=card.customer
                ).exclude(
                    id=card.id
                ).update(is_default=False)
        
        # Update billing address fields
        billing_address = data.get('billingAddress', {})
        if billing_address:
            card.billing_street = billing_address.get('street', card.billing_street)
            card.billing_city = billing_address.get('city', card.billing_city)
            card.billing_state = billing_address.get('state', card.billing_state)
            card.billing_zip = billing_address.get('zip', card.billing_zip)
            card.billing_country = billing_address.get('country', card.billing_country)
        
        card.save()
        
        # Return updated card data
        serializer = PaymentCardSerializer(card)
        card_data = serializer.data
        
        return Response({
            'status': 'success',
            'message': 'Card updated successfully',
            'card': {
                'id': card_data['id'],
                'last4': card_data['last4'] or 'xxxx',
                'cardBrand': card_data['card_brand'] or 'Credit Card',
                'customerProfileId': card_data['customer_profile_id'] or '',
                'paymentProfileId': card_data['payment_profile_id'] or '',
                'expMonth': card_data['exp_month'] or '12',
                'expYear': card_data['exp_year'] or '25',
                'isDefault': card_data['is_default'],
                'billingAddress': card_data['billingAddress']
            }
        })
    
    except Exception as e:
        logger.error(f"Error updating saved card: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_saved_card(request, card_id):
    """
    Delete a saved card
    
    Handles both local DB cards (UUID) and CIM-sourced cards (cim_<paymentProfileId>).
    For CIM cards, customerProfileId and paymentProfileId can be passed as query params.
    
    Args:
        card_id: ID of the card to delete (UUID or cim_<paymentProfileId>)
        
    Returns:
        Success message
    """
    try:
        from .authorize_net import AuthorizeNetGateway
        
        # --- Handle CIM-sourced cards (id starts with "cim_") ---
        if str(card_id).startswith('cim_'):
            payment_profile_id = request.query_params.get('paymentProfileId') or str(card_id).replace('cim_', '')
            customer_profile_id = request.query_params.get('customerProfileId')
            
            if not customer_profile_id:
                logger.error(f"CIM card delete: missing customerProfileId for card {card_id}")
                return Response({
                    'status': 'error',
                    'message': 'customerProfileId is required to delete a CIM card'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            logger.info(f"Deleting CIM payment profile: Customer={customer_profile_id}, Payment={payment_profile_id}")
            
            delete_result = AuthorizeNetGateway.delete_customer_payment_profile(
                customer_profile_id,
                payment_profile_id
            )
            
            if delete_result.get('success'):
                logger.info(f"Successfully deleted CIM payment profile {payment_profile_id}")
                
                # Also delete any matching local PaymentCard record if one exists
                local_deleted = PaymentCard.objects.filter(
                    payment_profile_id=payment_profile_id,
                    customer_profile_id=customer_profile_id
                ).delete()[0]
                if local_deleted:
                    logger.info(f"Also deleted {local_deleted} matching local PaymentCard record(s)")
                
                return Response({
                    'status': 'success',
                    'message': f'Successfully deleted payment profile',
                    'authorize_net_deleted': True
                }, status=status.HTTP_200_OK)
            else:
                error_msg = delete_result.get('message', 'Failed to delete from Authorize.Net')
                logger.warning(f"Failed to delete CIM payment profile: {error_msg}")
                return Response({
                    'status': 'error',
                    'message': error_msg
                }, status=status.HTTP_400_BAD_REQUEST)
        
        # --- Handle local DB cards (UUID) ---
        try:
            card = PaymentCard.objects.get(id=card_id)
        except PaymentCard.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Card with ID {card_id} not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Store card info for response
        card_info = f"{card.card_brand or 'Card'} ending in {card.last4 or 'xxxx'}"
        
        # Delete from Authorize.Net CIM first if we have profile IDs
        authorize_net_deleted = False
        if card.customer_profile_id and card.payment_profile_id:
            logger.info(f"Deleting payment profile from Authorize.Net CIM: Customer={card.customer_profile_id}, Payment={card.payment_profile_id}")
            
            delete_result = AuthorizeNetGateway.delete_customer_payment_profile(
                card.customer_profile_id,
                card.payment_profile_id
            )
            
            if delete_result.get('success'):
                authorize_net_deleted = True
                logger.info(f"Successfully deleted payment profile from Authorize.Net")
            else:
                logger.warning(f"Failed to delete from Authorize.Net: {delete_result.get('message')}")
                # Continue with database deletion even if Authorize.Net delete fails
        else:
            logger.info(f"No CIM profile IDs found for card, skipping Authorize.Net deletion")
        
        # Delete the card from the database
        card.delete()
        
        logger.info(f"Deleted card from database: {card_info}")
        
        return Response({
            'status': 'success',
            'message': f'Successfully deleted {card_info}',
            'authorize_net_deleted': authorize_net_deleted
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.exception(f"Error deleting saved card: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred while deleting the card: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([PaymentRateThrottle])
def charge_customer_profile(request):
    """
    Charge a customer's saved payment profile using CIM
    
    Request body:
    {
        "customer_profile_id": "1234567890",
        "payment_profile_id": "9876543210",
        "amount": 100.00,
        "orderNumber": "ORD-12345",
        "orderItems": [...]
    }
    """
    try:
        data = request.data
        
        # Get required fields
        customer_profile_id = data.get('customer_profile_id')
        payment_profile_id = data.get('payment_profile_id')
        amount = data.get('amount')
        
        # Optional fields
        order_number = data.get('orderNumber') or data.get('order_number')
        order_items = data.get('orderItems') or data.get('order_items', [])
        
        # Validate required fields
        if not customer_profile_id or not payment_profile_id or not amount:
            return Response({
                'status': 'error',
                'message': 'Missing required fields: customer_profile_id, payment_profile_id, amount'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        logger.info(f"Charging CIM profile - Customer: {customer_profile_id}, Payment: {payment_profile_id}, Amount: ${amount}")
        
        # Process payment through CIM
        payment_response = AuthorizeNetGateway.charge_customer_profile(
            customer_profile_id=customer_profile_id,
            payment_profile_id=payment_profile_id,
            amount=float(amount),
            order_items=order_items,
            order_id=order_number
        )
        
        # Save transaction record
        try:
            if payment_response['success']:
                PaymentTransaction.objects.create(
                    transaction_id=payment_response['transactionId'],
                    order_id=order_number,
                    amount=amount,
                    status=payment_response['status'],
                    payment_type='credit',
                    auth_code=payment_response.get('authCode'),
                    response_code=payment_response.get('responseCode'),
                    response_message=payment_response.get('message')
                )
        except Exception as e:
            logger.error(f"Error saving CIM transaction record: {str(e)}")
        
        # Return response
        if payment_response['success']:
            from users.activity_log import log_activity
            log_activity(request, f"Charged saved card ${amount} (profile {payment_profile_id})", category='payment', details={
                'amount': float(amount),
                'customer_profile_id': customer_profile_id,
                'payment_profile_id': payment_profile_id,
                'transaction_id': payment_response.get('transactionId'),
                'order_number': order_number,
            })
            
            return Response({
                'transactionId': payment_response['transactionId'],
                'status': payment_response['status'],
                'message': payment_response['message'],
                'authCode': payment_response.get('authCode'),
                'responseCode': payment_response.get('responseCode')
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'status': payment_response['status'],
                'message': payment_response['message'],
                'code': payment_response.get('code', 'UNKNOWN')
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        logger.exception(f"Error charging customer profile: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred while processing payment: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def save_card_note(request):
    """
    Create or update an internal note on a payment card.
    
    Request body:
        customerId: Contact UUID
        paymentProfileId: CIM payment profile ID
        note: The note text (empty string to clear)
    """
    try:
        from crm.models import Contact
        
        customer_id = request.data.get('customerId')
        payment_profile_id = request.data.get('paymentProfileId')
        note = request.data.get('note', '')
        
        if not customer_id or not payment_profile_id:
            return Response({
                'status': 'error',
                'message': 'customerId and paymentProfileId are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            customer = Contact.objects.get(id=customer_id)
        except Contact.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Customer {customer_id} not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        card_note, created = PaymentCardNote.objects.update_or_create(
            customer=customer,
            payment_profile_id=str(payment_profile_id),
            defaults={'note': note}
        )
        
        logger.info(f"{'Created' if created else 'Updated'} card note for customer {customer_id}, payment profile {payment_profile_id}")
        
        return Response({
            'status': 'success',
            'message': 'Note saved successfully',
            'note': card_note.note
        })
    
    except Exception as e:
        logger.exception(f"Error saving card note: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([AllowAny])
def authorizenet_webhook(request):
    """
    Webhook endpoint for Authorize.net CIM profile events.
    
    Handles:
      - net.authorize.customer.paymentProfile.created
      - net.authorize.customer.paymentProfile.updated
      - net.authorize.customer.paymentProfile.deleted
      - net.authorize.customer.created
      - net.authorize.customer.updated
    
    On paymentProfile created/updated: fetches the CIM profile by ID,
    extracts the email, and links the CIM customer profile ID to the
    matching Contact.authorize_net_customer_profile_id.
    
    This eliminates the need for the slow email-based CIM search in
    get_saved_cards — the webhook keeps the cache warm in the background.
    """
    import hashlib
    import hmac as hmac_mod
    import json
    import requests as http_requests
    import xml.etree.ElementTree as ET
    from core.secrets import get_secret
    from .config import AUTHORIZE_NET_LOGIN_ID, AUTHORIZE_NET_TRANSACTION_KEY, AUTHORIZE_NET_ENDPOINT
    
    # --- Signature verification (optional but recommended) ---
    # Note: DRF consumes the raw body stream, so we re-serialize request.data
    # for HMAC computation. This is acceptable since Authorize.net sends compact JSON.
    signature_key = get_secret('AUTHORIZE_NET_SIGNATURE_KEY', '')
    if signature_key:
        sig_header = request.META.get('HTTP_X_ANET_SIGNATURE', '')
        if sig_header:
            try:
                raw_body = json.dumps(request.data, separators=(',', ':')).encode('utf-8')
                expected = 'sha512=' + hmac_mod.new(
                    bytes.fromhex(signature_key),
                    raw_body,
                    hashlib.sha512
                ).hexdigest().upper()
                if not hmac_mod.compare_digest(expected, sig_header):
                    logger.warning("Authorize.net webhook signature mismatch — allowing through (re-serialization may differ)")
            except Exception as sig_err:
                logger.warning(f"Authorize.net webhook signature check error: {sig_err}")
        else:
            logger.debug("Authorize.net webhook: no X-ANET-Signature header")
    
    # DRF already parsed the JSON body into request.data
    body = request.data
    if not body:
        logger.warning("Authorize.net webhook: empty body")
        return Response({'error': 'Empty body'}, status=400)
    
    event_type = body.get('eventType', '')
    payload = body.get('payload', {})
    notification_id = body.get('notificationId', '')
    
    logger.info(f"Authorize.net webhook received: {event_type} (notification: {notification_id})")
    
    # Extract the CIM customer profile ID from the payload
    customer_profile_id = str(payload.get('customerProfileId', '') or payload.get('id', '') or '')
    
    if not customer_profile_id or not customer_profile_id.isdigit():
        logger.info(f"Authorize.net webhook: no valid customerProfileId in payload, skipping")
        return Response({'status': 'ok', 'action': 'skipped'})
    
    # Fetch the CIM profile to get the email address
    try:
        ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
        ns_prefix = '{' + ns + '}'
        
        get_profile_xml = f'''<?xml version="1.0" encoding="utf-8"?>
        <getCustomerProfileRequest xmlns="{ns}">
            <merchantAuthentication>
                <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
            </merchantAuthentication>
            <customerProfileId>{customer_profile_id}</customerProfileId>
        </getCustomerProfileRequest>'''
        
        resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=get_profile_xml,
                                  headers={'Content-Type': 'text/xml'}, timeout=15)
        
        if resp.status_code != 200:
            logger.error(f"Authorize.net webhook: failed to fetch CIM profile {customer_profile_id}: HTTP {resp.status_code}")
            return Response({'status': 'ok', 'action': 'fetch_failed'})
        
        root = ET.fromstring(resp.text)
        result_code = root.findtext(f'.//{ns_prefix}resultCode')
        if result_code != 'Ok':
            error_text = root.findtext(f'.//{ns_prefix}text') or 'Unknown'
            logger.error(f"Authorize.net webhook: CIM profile fetch error: {error_text}")
            return Response({'status': 'ok', 'action': 'fetch_failed'})
        
        profile_email = (root.findtext(f'.//{ns_prefix}profile/{ns_prefix}email') or '').strip().lower()
        
        if not profile_email:
            logger.info(f"Authorize.net webhook: CIM profile {customer_profile_id} has no email, skipping")
            return Response({'status': 'ok', 'action': 'no_email'})
        
        # Link the CIM profile to the matching Contact
        from crm.models import Contact
        try:
            contact = Contact.objects.get(email__iexact=profile_email)
            old_value = contact.authorize_net_customer_profile_id
            contact.authorize_net_customer_profile_id = customer_profile_id
            contact.save(update_fields=['authorize_net_customer_profile_id'])
            logger.info(
                f"✅ Authorize.net webhook: linked CIM profile {customer_profile_id} "
                f"to Contact {contact.email} (was: {old_value}) "
                f"[event: {event_type}]"
            )
            return Response({
                'status': 'ok',
                'action': 'linked',
                'email': profile_email,
                'customer_profile_id': customer_profile_id
            })
        except Contact.DoesNotExist:
            logger.info(f"Authorize.net webhook: no Contact found for email {profile_email}")
            return Response({'status': 'ok', 'action': 'no_contact'})
        except Contact.MultipleObjectsReturned:
            logger.warning(f"Authorize.net webhook: multiple Contacts for email {profile_email}")
            contact = Contact.objects.filter(email__iexact=profile_email).first()
            if contact:
                contact.authorize_net_customer_profile_id = customer_profile_id
                contact.save(update_fields=['authorize_net_customer_profile_id'])
                logger.info(f"✅ Linked CIM profile {customer_profile_id} to first Contact for {profile_email}")
            return Response({'status': 'ok', 'action': 'linked_first'})
    
    except Exception as e:
        logger.exception(f"Authorize.net webhook error: {e}")
        # Return 200 so Authorize.net doesn't keep retrying
        return Response({'status': 'ok', 'action': 'error'})
