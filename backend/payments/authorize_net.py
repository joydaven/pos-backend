"""
Authorize.net payment gateway integration
"""
import logging
from decimal import Decimal
from authorizenet import apicontractsv1
from authorizenet.apicontrollers import createTransactionController
from .config import (
    AUTHORIZE_NET_LOGIN_ID,
    AUTHORIZE_NET_TRANSACTION_KEY,
    AUTHORIZE_NET_ENDPOINT,
    AUTHORIZE_NET_SANDBOX,
    AUTHORIZE_NET_CARD_VALIDATION_UPON_ADD

)
import uuid
from authorizenet.apicontrollers import createCustomerProfileController, createCustomerPaymentProfileController

# User-friendly error messages for Authorize.Net error codes
AUTHNET_ERROR_MESSAGES = {
    'E00027': 'Card could not be validated. If you just corrected your card details, please wait 1-2 minutes and try again.',
    'E00003': 'Invalid card information - Please check your card number and try again.',
    'E00004': 'Invalid expiration date - Please check the expiration date and try again.',
    'E00005': 'Invalid transaction amount - Please contact support.',
    'E00007': 'Card information is invalid - Please check all card details.',
    'E00013': 'Card number is invalid - Please check the card number.',
    'E00014': 'Card type is not accepted - This card type is not supported.',
    'E00039': 'Duplicate card - This card is already saved to your account.',
    'E00040': 'Customer profile not found - Please contact support.',
    'E00041': 'Payment profile not found - Please try adding the card again.',
    '2': 'Card declined - Please try a different payment method.',
    '3': 'Card declined - Invalid card number.',
    '4': 'Card declined - Card requires pickup.',
    '5': 'Card declined - Please contact your bank.',
    '6': 'Card declined - General error. Please try again.',
    '7': 'Card declined - Card requires pickup (special condition).',
    '8': 'Card approved for partial amount - Please use a different card.',
    '27': 'Card declined - Address verification failed.',
    '28': 'Card declined - Please try a different card.',
    '44': 'Card declined - CVV mismatch.',
    '45': 'Card declined - Address and CVV mismatch.',
    '65': 'Card declined - CVV required but not provided.',
    '127': 'Card declined - Invalid CVV.',
    '141': 'Card declined - Invalid card number.',
    '145': 'Card declined - Invalid expiration date.',
    '165': 'Card declined - Security code mismatch.',
    '250': 'Card declined - Fraud detection triggered.',
    '251': 'Card declined - Blocked by merchant settings.',
    '254': 'Card declined - Transaction not permitted.',
}

logger = logging.getLogger(__name__)

def get_friendly_error_message(error_code, original_message):
    """
    Convert Authorize.Net error codes to user-friendly messages
    
    Args:
        error_code: Error code from Authorize.Net (string or int)
        original_message: Original error message from Authorize.Net
        
    Returns:
        User-friendly error message
    """
    error_code_str = str(error_code)
    
    # Return friendly message if we have one
    if error_code_str in AUTHNET_ERROR_MESSAGES:
        return AUTHNET_ERROR_MESSAGES[error_code_str]
    
    # Default fallback for unknown errors
    if 'decline' in original_message.lower():
        return f"Card declined - {original_message}"
    elif 'invalid' in original_message.lower():
        return f"Invalid card information - {original_message}"
    else:
        return original_message

# Authorize.Net test card numbers
# Source: https://developer.authorize.net/hello_world/testing_guide/
TEST_CARD_NUMBERS = {
    # Visa
    '4111111111111111',
    '4012888888881881',
    '4012888818888',
    '4007000000027',
    '4007000000000027',
    '4222222222222',

    # Mastercard
    '5424000000000015',
    '5555555555554444',
    '2223000010309703',
    '2223000010309711',
    '5424000000000015',
    
    # American Express
    '378282246310005',
    '371449635398431',
    '370000000000002',
    '3700000000000002',
    '378200000000005',
    '3782822463100005',
    
    # Discover
    '6011111111111117',
    '6011000990139424',
    '6011000000000012',
    '6011000000012',
    
    # JCB
    '3530111333300000',
    '3566002020360505',
    '3088000000000017',
    '3088000000017',
    
    # Diners Club / Carte Blanche
    '36259600000004',
    '38520000023237',
    '3800000000006',
    '380000000000006',
    '3800000000000006',
    
    # China UnionPay
    '6221499053360818',
    '6262320002000067',
    '6284480000000008',
}

class AuthorizeNetGateway:
    """
    Gateway for processing payments through Authorize.net
    """
    
    @staticmethod
    def is_test_card(card_number):
        """
        Check if a card number is a test card
        Uses both exact matching and BIN pattern matching for Authorize.Net test cards
        
        Args:
            card_number (str): The credit card number to check
            
        Returns:
            bool: True if the card is a test card, False otherwise
        """
        # Remove spaces and dashes
        clean_card = card_number.replace(' ', '').replace('-', '')
        
        # Check exact match first
        if clean_card in TEST_CARD_NUMBERS:
            return True
        
        # Check common test card BIN patterns (first 6-8 digits)
        # Authorize.Net test cards often use specific BIN ranges
        test_patterns = [
            '411111',      # Visa test cards (4111 1111 1111 xxxx)
            '401288',      # Visa test cards (4012 8888 xxxx)
            '400700',      # Visa test cards (4007 0000 xxxx)
            '542400',      # Mastercard test cards (5424 0000 xxxx)
            '555555',      # Mastercard test cards (5555 5555 xxxx)
            '222300',      # Mastercard 2-series (2223 0000 xxxx)
            '370000',      # Amex test cards (3700 0000 xxxx)
            '378282',      # Amex test cards (3782 82xx xxxx)
            '371449',      # Amex test cards (3714 49xx xxxx)
            '378200',      # Amex test cards (3782 00xx xxxx)
            '601100',      # Discover test cards (6011 00xx xxxx)
            '601111',      # Discover test cards (6011 1111 xxxx)
            '353011',      # JCB test cards (3530 11xx xxxx)
            '356600',      # JCB test cards (3566 00xx xxxx)
            '308800',      # JCB test cards (3088 00xx xxxx)
            '362596',      # Diners Club test cards
            '385200',      # Diners Club test cards
            '380000',      # Diners Club test cards (3800 0000 xxxx)
            '622149',      # China UnionPay test cards
            '626232',      # China UnionPay test cards
            '628448',      # China UnionPay test cards
        ]
        
        # Check if card starts with any test pattern
        for pattern in test_patterns:
            if clean_card.startswith(pattern):
                logger.info(f"Test card detected by BIN pattern: {pattern}")
                return True
        
        return False
    
    @staticmethod
    def get_merchant_auth():
        """
        Create and return a merchant authentication object
        """
        # Check if credentials are configured
        if not AUTHORIZE_NET_LOGIN_ID or not AUTHORIZE_NET_TRANSACTION_KEY:
            logger.warning("Authorize.net credentials not configured - using test mode")
            
        merchant_auth = apicontractsv1.merchantAuthenticationType()
        merchant_auth.name = AUTHORIZE_NET_LOGIN_ID or "test_login"
        merchant_auth.transactionKey = AUTHORIZE_NET_TRANSACTION_KEY or "test_key"
        return merchant_auth
    
    @staticmethod
    def _xml_escape(value):
        """Escape special XML characters in a string value"""
        if value is None:
            return ''
        from xml.sax.saxutils import escape
        return escape(str(value))
    
    @classmethod
    def process_payment(cls, amount, card_number, expiry_date, cvv, 
                       customer_email=None, customer_name=None, order_items=None,
                       billing_address=None, order_id=None):
        """
        Process a payment through Authorize.net
        
        Args:
            amount (float): The amount to charge
            card_number (str): The credit card number
            expiry_date (str): The expiry date in MMYY format
            cvv (str): The card verification value
            customer_email (str, optional): Customer's email address
            customer_name (str, optional): Customer's full name
            order_items (list, optional): List of items in the order
            billing_address (dict, optional): Customer's billing address
            order_id (str, optional): Unique identifier for the order
            
        Returns:
            dict: Response containing transaction details
        """
        try:
            # Remove any spaces from the card number
            card_number = card_number.replace(" ", "")
            
            # Check if test card is being used in production
            # TEMPORARILY DISABLED FOR TESTING
            # if not AUTHORIZE_NET_SANDBOX and cls.is_test_card(card_number):
            #     logger.warning(f"Test card rejected in production mode: {card_number[-4:]}")
            #     return {
            #         'success': False,
            #         'status': 'declined',
            #         'message': 'Test credit cards are not allowed in production mode. Please use a real credit card.',
            #         'code': 'TEST_CARD_NOT_ALLOWED'
            #     }
            
            # Create merchant authentication
            merchant_auth = cls.get_merchant_auth()
            
            # Create credit card object
            credit_card = apicontractsv1.creditCardType()
            credit_card.cardNumber = card_number
            credit_card.expirationDate = expiry_date
            credit_card.cardCode = cvv
            
            # Create payment object
            payment = apicontractsv1.paymentType()
            payment.creditCard = credit_card
            
            # Create order object
            order = apicontractsv1.orderType()
            order.invoiceNumber = order_id or f"INV-{int(Decimal(amount) * 100)}"
            order.description = "POS System Order"
            
            # Create line items if provided
            line_items = None
            if order_items:
                line_items = apicontractsv1.ArrayOfLineItem()
                for i, item in enumerate(order_items):
                    line_item = apicontractsv1.lineItemType()
                    line_item.itemId = str(i + 1)
                    line_item.name = item.get('name', 'Product')[:31]  # Max 31 chars
                    line_item.description = item.get('description', '')[:255]  # Max 255 chars
                    line_item.quantity = str(item.get('quantity', 1))
                    line_item.unitPrice = str(item.get('price', 0))
                    line_items.lineItem.append(line_item)
            
            # Create customer data
            customer_data = None
            if customer_email or customer_name:
                customer_data = apicontractsv1.customerDataType()
                customer_data.email = customer_email
                
                if customer_name:
                    # Split name into first and last if possible
                    name_parts = customer_name.split(' ', 1)
                    if len(name_parts) > 1:
                        customer_data.firstName = name_parts[0]
                        customer_data.lastName = name_parts[1]
                    else:
                        customer_data.firstName = customer_name
            
            # Create billing address if provided
            bill_to = None
            if billing_address:
                bill_to = apicontractsv1.customerAddressType()
                bill_to.firstName = billing_address.get('firstName', '')
                bill_to.lastName = billing_address.get('lastName', '')
                bill_to.address = billing_address.get('address', '')
                bill_to.city = billing_address.get('city', '')
                bill_to.state = billing_address.get('state', '')
                bill_to.zip = billing_address.get('zip', '')
                bill_to.country = billing_address.get('country', 'USA')
                bill_to.phoneNumber = billing_address.get('phoneNumber', '')
            
            # Create transaction request
            transaction_request = apicontractsv1.transactionRequestType()
            transaction_request.transactionType = "authCaptureTransaction"
            transaction_request.amount = str(amount)
            transaction_request.payment = payment
            transaction_request.order = order
            
            if line_items:
                transaction_request.lineItems = line_items
            
            if customer_data:
                transaction_request.customer = customer_data
            
            if bill_to:
                transaction_request.billTo = bill_to
            
            # Create transaction request
            create_transaction = apicontractsv1.createTransactionRequest()
            create_transaction.merchantAuthentication = merchant_auth
            create_transaction.refId = order_id or "REF_" + str(int(Decimal(amount) * 100))
            create_transaction.transactionRequest = transaction_request
            
            # Execute transaction
            controller = createTransactionController(create_transaction)
            controller.setenvironment(AUTHORIZE_NET_ENDPOINT)
            controller.execute()
            
            # Get response
            api_response = controller.getresponse()
            
            if api_response is not None:
                # Check if the API call itself succeeded
                if api_response.messages.resultCode == "Ok":
                    transaction = api_response.transactionResponse
                    
                    # CRITICAL: Check the transaction responseCode, not just resultCode
                    # responseCode "1" = Approved, "2" = Declined, "3" = Error, "4" = Held for Review
                    response_code = str(transaction.responseCode) if transaction.responseCode else "0"
                    
                    if response_code != "1":
                        # resultCode was "Ok" but transaction was NOT approved
                        error_message = "Transaction was not approved."
                        error_code = response_code
                        
                        # Extract specific error from transaction response
                        if hasattr(transaction, 'errors') and transaction.errors is not None:
                            error_message = str(transaction.errors.error[0].errorText)
                            error_code = str(transaction.errors.error[0].errorCode)
                        elif hasattr(transaction, 'messages') and transaction.messages is not None:
                            error_message = str(transaction.messages.message[0].description)
                        
                        friendly_message = get_friendly_error_message(error_code, error_message)
                        logger.error(f"Transaction NOT approved despite Ok resultCode - responseCode: {response_code}, error: {error_code} - {error_message}")
                        
                        return {
                            'success': False,
                            'transactionId': str(transaction.transId) if transaction.transId else None,
                            'status': 'declined',
                            'message': friendly_message,
                            'code': error_code,
                            'responseCode': response_code,
                        }
                    
                    # Generate fallback transaction ID if Authorize.net doesn't provide one
                    # This can happen in sandbox/test environments
                    transaction_id = transaction.transId or f"auth_net_{uuid.uuid4().hex[:12]}"
                    auth_code = transaction.authCode or f"AUTH_{uuid.uuid4().hex[:8].upper()}"
                    
                    # Log auth code for monitoring (NOCODE is normal for this account configuration)
                    if auth_code == "NOCODE" or auth_code.upper() == "NOCODE":
                        logger.info(f"ℹ️ Transaction approved with Auth: NOCODE, Transaction ID: {transaction_id}")
                    
                    logger.info(f"Transaction processed - ID: {transaction_id}, Auth: {auth_code}")
                    
                    return {
                        'success': True,
                        'transactionId': transaction_id,
                        'status': 'approved',
                        'message': 'Transaction approved',
                        'authCode': auth_code,
                        'responseCode': response_code,
                        'avsResultCode': transaction.avsResultCode,
                        'cvvResultCode': transaction.cvvResultCode,
                        'accountNumber': transaction.accountNumber,
                        'accountType': transaction.accountType,
                    }
                else:
                    # Transaction was declined or had an error
                    error_message = api_response.messages.message[0].text
                    error_code = api_response.messages.message[0].code
                    
                    # Check if there's a more specific error in the transaction response
                    if hasattr(api_response, 'transactionResponse') and api_response.transactionResponse is not None:
                        if hasattr(api_response.transactionResponse, 'errors') and api_response.transactionResponse.errors is not None:
                            error_message = api_response.transactionResponse.errors.error[0].errorText
                            error_code = api_response.transactionResponse.errors.error[0].errorCode
                    
                    friendly_message = get_friendly_error_message(str(error_code), str(error_message))
                    logger.error(f"Authorize.net error: {error_code} - {error_message}")
                    
                    return {
                        'success': False,
                        'status': 'declined',
                        'message': friendly_message,
                        'code': error_code
                    }
            else:
                logger.error("Null response from Authorize.net API")
                
                return {
                    'success': False,
                    'status': 'error',
                    'message': 'Failed to process payment. No response from payment gateway.',
                    'code': 'NULL_RESPONSE'
                }
                
        except Exception as e:
            logger.exception(f"Error processing Authorize.net payment: {str(e)}")
            
            return {
                'success': False,
                'status': 'error',
                'message': f'An error occurred while processing payment: {str(e)}',
                'code': 'EXCEPTION'
            }

    @classmethod
    def create_customer_profile(cls, card_number, expiry_date, cvv=None, customer_email=None, 
                              customer_name=None, customer_id=None, billing_address=None):
        """
        Create a customer profile and payment profile in Authorize.net CIM
        
        Args:
            card_number (str): The credit card number
            expiry_date (str): The expiry date in MMYY format
            cvv (str, optional): Card verification value
            customer_email (str, optional): Customer's email address
            customer_name (str, optional): Customer's full name
            customer_id (str, optional): Customer's ID in your system
            billing_address (dict, optional): Billing address information
            
        Returns:
            dict: Response containing profile details
        """
        logger.info(f"Creating CIM customer profile for {customer_name or 'Unknown'}")
        
        try:
            # Remove spaces from card number
            card_number = card_number.replace(' ', '')
            
            # Log card number check details
            logger.info(f"🔧 CARD VALIDATION - Sandbox: {AUTHORIZE_NET_SANDBOX}, Card last 4: {card_number[-4:]}, Is test card: {cls.is_test_card(card_number)}")
            
            # Check if test card is being used in production
            # TEMPORARILY DISABLED FOR TESTING
            # if not AUTHORIZE_NET_SANDBOX and cls.is_test_card(card_number):
            #     logger.warning(f"❌ Test card rejected in CIM profile creation - Card: {card_number[-4:]}")
            #     return {
            #         'success': False,
            #         'status': 'error',
            #         'message': 'Test credit cards are not allowed in production mode.'
            #     }
            
            # Create merchant authentication
            merchant_auth = cls.get_merchant_auth()
            
            # Create credit card object
            credit_card = apicontractsv1.creditCardType()
            credit_card.cardNumber = card_number
            credit_card.expirationDate = expiry_date
            if cvv:
                credit_card.cardCode = cvv
            
            # Create payment object
            payment = apicontractsv1.paymentType()
            payment.creditCard = credit_card
            
            # Create billing address if provided
            bill_to = None
            if billing_address:
                bill_to = apicontractsv1.customerAddressType()
                bill_to.firstName = billing_address.get('firstName', '')
                bill_to.lastName = billing_address.get('lastName', '')
                bill_to.address = billing_address.get('address', '')
                bill_to.city = billing_address.get('city', '')
                bill_to.state = billing_address.get('state', '')
                bill_to.zip = billing_address.get('zip', '')
                bill_to.country = billing_address.get('country', 'USA')
            
            # Create payment profile
            payment_profile = apicontractsv1.customerPaymentProfileType()
            payment_profile.payment = payment
            if bill_to:
                payment_profile.billTo = bill_to
            
            # Check if customer already has an Authorize.Net profile
            existing_profile_id = None
            if customer_id:
                try:
                    # First check Contact model for stored profile ID
                    from crm.models import Contact
                    try:
                        contact = Contact.objects.get(id=customer_id)
                        if contact.authorize_net_customer_profile_id:
                            # Validate that it's a numeric ID (Authorize.Net profile IDs are numeric)
                            # Ignore if it's a UUID (corrupted data)
                            profile_id = contact.authorize_net_customer_profile_id.strip()
                            if profile_id.isdigit():
                                existing_profile_id = profile_id
                                logger.info(f"Found existing customer profile ID on Contact model: {existing_profile_id}")
                            else:
                                logger.warning(f"Invalid (non-numeric) profile ID on Contact model: {profile_id}. Ignoring.")
                                # Clear the invalid value
                                contact.authorize_net_customer_profile_id = None
                                contact.save(update_fields=['authorize_net_customer_profile_id'])
                    except Contact.DoesNotExist:
                        logger.warning(f"Contact not found with id: {customer_id}")
                    
                    # Fallback: Check PaymentCard table
                    if not existing_profile_id:
                        from payments.models import PaymentCard
                        existing_card = PaymentCard.objects.filter(
                            customer__id=customer_id,
                            customer_profile_id__isnull=False
                        ).first()
                        
                        if existing_card and existing_card.customer_profile_id:
                            # Validate that it's a numeric ID
                            card_profile_id = existing_card.customer_profile_id.strip()
                            if card_profile_id.isdigit():
                                existing_profile_id = card_profile_id
                                logger.info(f"Found existing customer profile ID in PaymentCard: {existing_profile_id}")
                                
                                # Also save it to Contact model for future lookups
                                try:
                                    contact = Contact.objects.get(id=customer_id)
                                    contact.authorize_net_customer_profile_id = existing_profile_id
                                    contact.save(update_fields=['authorize_net_customer_profile_id'])
                                    logger.info(f"Saved profile ID to Contact model for future lookups")
                                except Contact.DoesNotExist:
                                    pass
                            else:
                                logger.warning(f"Invalid (non-numeric) profile ID in PaymentCard: {card_profile_id}. Ignoring and will create new profile.")
                    
                    if existing_profile_id:
                        logger.info("Adding new payment method to existing profile instead of creating new profile")
                        
                        # Add payment profile to existing customer profile
                        return cls.add_payment_profile_to_customer(
                            customer_profile_id=existing_profile_id,
                            card_number=card_number,
                            expiry_date=expiry_date,
                            cvv=cvv,
                            billing_address=billing_address
                        )
                except Exception as e:
                    logger.warning(f"Error checking for existing profile: {str(e)}")
            
            # No existing profile found - create new customer profile
            # Use raw XML instead of SDK to control element ordering
            # (SDK puts <payment> before <billTo> which violates Authorize.net schema for some card types like AmEx)
            import requests as http_requests
            import xml.etree.ElementTree as ET
            
            if customer_id:
                merchant_customer_id = customer_id[:20]
            else:
                merchant_customer_id = f"CUS_{uuid.uuid4().hex[:12]}"
            logger.info(f"Creating NEW customer profile with merchantCustomerId: {merchant_customer_id}")
            
            validation_mode = "testMode" if AUTHORIZE_NET_SANDBOX else AUTHORIZE_NET_CARD_VALIDATION_UPON_ADD
            
            bill_to_xml = ''
            if billing_address:
                bill_to_xml = f'''<billTo>
                        <firstName>{cls._xml_escape(billing_address.get('firstName', ''))}</firstName>
                        <lastName>{cls._xml_escape(billing_address.get('lastName', ''))}</lastName>
                        <address>{cls._xml_escape(billing_address.get('address', ''))}</address>
                        <city>{cls._xml_escape(billing_address.get('city', ''))}</city>
                        <state>{cls._xml_escape(billing_address.get('state', ''))}</state>
                        <zip>{cls._xml_escape(billing_address.get('zip', ''))}</zip>
                        <country>{cls._xml_escape(billing_address.get('country', 'USA'))}</country>
                    </billTo>'''
            
            card_code_xml = f'<cardCode>{cls._xml_escape(cvv)}</cardCode>' if cvv else ''
            
            ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
            create_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <createCustomerProfileRequest xmlns="{ns}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
                <profile>
                    <merchantCustomerId>{cls._xml_escape(merchant_customer_id)}</merchantCustomerId>
                    <email>{cls._xml_escape(customer_email or '')}</email>
                    <paymentProfiles>
                        {bill_to_xml}
                        <payment>
                            <creditCard>
                                <cardNumber>{cls._xml_escape(card_number)}</cardNumber>
                                <expirationDate>{cls._xml_escape(expiry_date)}</expirationDate>
                                {card_code_xml}
                            </creditCard>
                        </payment>
                    </paymentProfiles>
                </profile>
                <validationMode>{validation_mode}</validationMode>
            </createCustomerProfileRequest>'''
            
            resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=create_xml,
                                      headers={'Content-Type': 'text/xml'}, timeout=30)
            
            if resp.status_code != 200:
                logger.error(f"CIM createCustomerProfile HTTP error: {resp.status_code}")
                return {'success': False, 'status': 'error', 'message': f'Gateway HTTP error: {resp.status_code}'}
            
            ns_prefix = '{' + ns + '}'
            root = ET.fromstring(resp.text)
            result_code = root.findtext(f'.//{ns_prefix}resultCode')
            
            if result_code == 'Ok':
                customer_profile_id = root.findtext(f'.//{ns_prefix}customerProfileId') or ''
                payment_profile_id = ''
                pp_id_list = root.find(f'.//{ns_prefix}customerPaymentProfileIdList')
                if pp_id_list is not None:
                    numeric = pp_id_list.findtext(f'{ns_prefix}numericString')
                    if numeric:
                        payment_profile_id = numeric
                
                logger.info(f"CIM profile created - Profile ID: {customer_profile_id}, Payment Profile ID: {payment_profile_id}")
                
                if customer_id and customer_profile_id:
                    try:
                        from crm.models import Contact
                        contact = Contact.objects.get(id=customer_id)
                        contact.authorize_net_customer_profile_id = customer_profile_id
                        contact.save(update_fields=['authorize_net_customer_profile_id'])
                        logger.info(f"Saved customer profile ID to Contact model: {customer_profile_id}")
                    except Contact.DoesNotExist:
                        logger.warning(f"Could not save profile ID - Contact not found: {customer_id}")
                    except Exception as e:
                        logger.error(f"Error saving profile ID to Contact: {str(e)}")
                
                return {
                    'success': True, 'status': 'success',
                    'customer_profile_id': customer_profile_id,
                    'payment_profile_id': payment_profile_id,
                    'message': 'Customer profile created successfully'
                }
            else:
                error_details = []
                error_code = None
                for msg in root.findall(f'.//{ns_prefix}messages/{ns_prefix}message'):
                    code = msg.findtext(f'{ns_prefix}code') or 'N/A'
                    text = msg.findtext(f'{ns_prefix}text') or 'N/A'
                    error_details.append(f"[{code}] {text}")
                    if not error_code:
                        error_code = code
                
                validation_response_text = ''
                val_list = root.find(f'.//{ns_prefix}validationDirectResponseList')
                if val_list is not None:
                    val_str = val_list.findtext(f'{ns_prefix}string') or ''
                    if val_str:
                        validation_response_text = val_str
                        logger.info(f"Validation direct response: {val_str}")
                
                # Handle duplicate customer profile (E00039)
                if error_code == 'E00039':
                    logger.info(f"Duplicate customer profile detected for merchantCustomerId: {merchant_customer_id}")
                    try:
                        existing_profile_id = None
                        
                        if validation_response_text:
                            parts = validation_response_text.split(',')
                            if len(parts) > 12 and parts[12]:
                                existing_profile_id = parts[12]
                                logger.info(f"Found customer profile ID in validationDirectResponse: {existing_profile_id}")
                        
                        if not existing_profile_id:
                            import re
                            for error_text in error_details:
                                for pattern in [r'ID[:\s]+(\d+)', r'exists[:\s]+(\d+)', r'\b(\d{7,})\b']:
                                    match = re.search(pattern, error_text, re.IGNORECASE)
                                    if match:
                                        existing_profile_id = match.group(1)
                                        logger.info(f"Extracted profile ID from error: {existing_profile_id}")
                                        break
                                if existing_profile_id:
                                    break
                        
                        if not existing_profile_id:
                            from payments.models import PaymentCard
                            existing_card = PaymentCard.objects.filter(
                                customer_profile_id__isnull=False, customer__id=customer_id
                            ).first()
                            if existing_card and existing_card.customer_profile_id:
                                existing_profile_id = existing_card.customer_profile_id
                        
                        if existing_profile_id:
                            if customer_id:
                                try:
                                    from crm.models import Contact
                                    contact = Contact.objects.get(id=customer_id)
                                    if not contact.authorize_net_customer_profile_id:
                                        contact.authorize_net_customer_profile_id = existing_profile_id
                                        contact.save(update_fields=['authorize_net_customer_profile_id'])
                                except Exception:
                                    pass
                            return cls.add_payment_profile_to_customer(
                                customer_profile_id=existing_profile_id,
                                card_number=card_number, expiry_date=expiry_date,
                                cvv=cvv, billing_address=billing_address
                            )
                        else:
                            return {'success': False, 'status': 'error',
                                    'message': 'Unable to retrieve existing profile. Please contact support.'}
                    except Exception as e:
                        logger.error(f"Error handling duplicate profile: {str(e)}")
                        return {'success': False, 'status': 'error', 'message': f'Error handling duplicate profile: {str(e)}'}
                
                error_message = "; ".join(error_details) if error_details else "Unknown error from Authorize.Net"
                if validation_response_text:
                    error_message += f" | ValidationResponse: {validation_response_text}"
                logger.error(f"CIM profile creation failed - Result: {result_code}, Errors: {error_message}")
                logger.error(f"Raw response: {resp.text[:1000]}")
                
                friendly_message = get_friendly_error_message(error_code, error_details[0] if error_details else error_message)
                return {'success': False, 'status': 'error', 'message': friendly_message}
                
        except Exception as e:
            logger.exception(f"Error creating CIM profile: {str(e)}")
            return {'success': False, 'status': 'error', 'message': f'Failed to create customer profile: {str(e)}'}
    
    @classmethod
    def get_customer_profile_id_by_merchant_id(cls, merchant_customer_id):
        """
        Get customer profile ID from Authorize.Net by merchantCustomerId
        
        Args:
            merchant_customer_id (str): The merchant customer ID to search for
            
        Returns:
            str: Customer profile ID if found, None otherwise
        """
        try:
            logger.info(f"Querying Authorize.Net for profile with merchantCustomerId: {merchant_customer_id}")
            
            # Create merchant authentication
            merchant_auth = cls.get_merchant_auth()
            
            # Create request to get customer profile IDs
            get_profile_ids = apicontractsv1.getCustomerProfileIdsRequest()
            get_profile_ids.merchantAuthentication = merchant_auth
            
            # Execute the request
            from authorizenet.apicontrollers import getCustomerProfileIdsController
            controller = getCustomerProfileIdsController(get_profile_ids)
            controller.setenvironment(AUTHORIZE_NET_ENDPOINT)
            controller.execute()
            
            response = controller.getresponse()
            
            if response is not None and response.messages.resultCode == "Ok":
                # We got all profile IDs, but we need to query each one to find the matching merchantCustomerId
                # This is not efficient but Authorize.Net doesn't provide a direct lookup by merchantCustomerId
                # For now, we'll just return None and let the error message guide the user
                logger.warning("Cannot directly query by merchantCustomerId - API limitation")
                return None
            else:
                logger.error("Failed to query customer profile IDs from Authorize.Net")
                return None
                
        except Exception as e:
            logger.error(f"Error querying customer profile: {str(e)}")
            return None
    
    @classmethod
    def add_payment_profile_to_customer(cls, customer_profile_id, card_number, expiry_date, 
                                       cvv=None, billing_address=None):
        """
        Add a new payment profile to an existing customer profile
        Uses raw XML instead of SDK to control element ordering
        (SDK puts <payment> before <billTo> which violates Authorize.net schema for some card types)
        
        Args:
            customer_profile_id (str): Existing customer profile ID
            card_number (str): Credit card number
            expiry_date (str): Expiration date in MM/YY format
            cvv (str, optional): Card CVV code
            billing_address (dict, optional): Billing address information
            
        Returns:
            dict: Response containing new payment profile ID
        """
        logger.info(f"Adding payment profile to existing customer profile: {customer_profile_id}")
        
        try:
            import requests as http_requests
            import xml.etree.ElementTree as ET
            
            # Remove spaces from card number
            card_number = card_number.replace(' ', '')
            
            # Build billTo XML if billing address provided
            bill_to_xml = ''
            if billing_address:
                bill_to_xml = f'''<billTo>
                        <firstName>{cls._xml_escape(billing_address.get('firstName', ''))}</firstName>
                        <lastName>{cls._xml_escape(billing_address.get('lastName', ''))}</lastName>
                        <address>{cls._xml_escape(billing_address.get('address', ''))}</address>
                        <city>{cls._xml_escape(billing_address.get('city', ''))}</city>
                        <state>{cls._xml_escape(billing_address.get('state', ''))}</state>
                        <zip>{cls._xml_escape(billing_address.get('zip', ''))}</zip>
                        <country>{cls._xml_escape(billing_address.get('country', 'USA'))}</country>
                    </billTo>'''
            
            # Build cardCode XML if CVV provided
            card_code_xml = f'<cardCode>{cls._xml_escape(cvv)}</cardCode>' if cvv else ''
            
            validation_mode = AUTHORIZE_NET_CARD_VALIDATION_UPON_ADD
            
            ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
            # CRITICAL: billTo MUST come before payment in paymentProfile
            create_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <createCustomerPaymentProfileRequest xmlns="{ns}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
                <customerProfileId>{cls._xml_escape(customer_profile_id)}</customerProfileId>
                <paymentProfile>
                    {bill_to_xml}
                    <payment>
                        <creditCard>
                            <cardNumber>{cls._xml_escape(card_number)}</cardNumber>
                            <expirationDate>{cls._xml_escape(expiry_date)}</expirationDate>
                            {card_code_xml}
                        </creditCard>
                    </payment>
                </paymentProfile>
                <validationMode>{validation_mode}</validationMode>
            </createCustomerPaymentProfileRequest>'''
            
            resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=create_xml,
                                      headers={'Content-Type': 'text/xml'}, timeout=30)
            
            if resp.status_code != 200:
                logger.error(f"CIM createCustomerPaymentProfile HTTP error: {resp.status_code}")
                return {
                    'success': False,
                    'status': 'error',
                    'message': f'Gateway HTTP error: {resp.status_code}'
                }
            
            ns_prefix = '{' + ns + '}'
            root = ET.fromstring(resp.text)
            result_code = root.findtext(f'.//{ns_prefix}resultCode')
            
            if result_code == 'Ok':
                payment_profile_id = root.findtext(f'.//{ns_prefix}customerPaymentProfileId') or ''
                logger.info(f"Payment profile added successfully - Profile ID: {payment_profile_id}")
                
                return {
                    'success': True,
                    'status': 'success',
                    'customer_profile_id': customer_profile_id,
                    'payment_profile_id': payment_profile_id,
                    'message': 'Payment profile added to existing customer profile'
                }
            else:
                # Extract error details
                error_code = None
                error_message = 'Unknown error'
                for msg in root.findall(f'.//{ns_prefix}messages/{ns_prefix}message'):
                    code = msg.findtext(f'{ns_prefix}code') or 'UNKNOWN'
                    text = msg.findtext(f'{ns_prefix}text') or 'Unknown error'
                    if not error_code:
                        error_code = code
                        error_message = text
                
                # Extract validation direct response for detailed decline info
                val_resp = root.findtext(f'.//{ns_prefix}validationDirectResponse') or ''
                if val_resp:
                    logger.error(f"Validation direct response: {val_resp}")
                
                # Handle E00040 (stale/deleted profile) - clear the profile ID and ask user to retry
                if error_code == 'E00040':
                    logger.warning(f"CIM profile {customer_profile_id} not found on Authorize.net (stale). Clearing from Contact model.")
                    try:
                        from crm.models import Contact
                        stale_contacts = Contact.objects.filter(authorize_net_customer_profile_id=customer_profile_id)
                        updated = stale_contacts.update(authorize_net_customer_profile_id=None)
                        logger.info(f"Cleared stale CIM profile ID from {updated} Contact(s)")
                    except Exception as clear_err:
                        logger.error(f"Failed to clear stale profile ID: {clear_err}")
                    
                    return {
                        'success': False,
                        'status': 'error',
                        'message': 'Saved card profile was outdated and has been reset. Please try adding the card again.'
                    }
                
                friendly_message = get_friendly_error_message(error_code or 'UNKNOWN', error_message)
                
                logger.error(f"Failed to add payment profile: [{error_code}] {error_message}")
                if val_resp:
                    logger.error(f"Validation response details: {val_resp}")
                logger.error(f"Raw response: {resp.text[:1000]}")
                
                return {
                    'success': False,
                    'status': 'error',
                    'message': friendly_message
                }
                
        except Exception as e:
            logger.exception(f"Error adding payment profile: {str(e)}")
            return {
                'success': False,
                'status': 'error',
                'message': f'Failed to add payment profile: {str(e)}'
            }
    
    @classmethod
    def charge_customer_profile(cls, customer_profile_id, payment_profile_id, amount, 
                               order_items=None, order_id=None):
        """
        Charge a customer's saved payment profile using CIM
        
        Args:
            customer_profile_id (str): The customer profile ID from Authorize.Net
            payment_profile_id (str): The payment profile ID to charge
            amount (float): The amount to charge
            order_items (list, optional): List of items in the order
            order_id (str, optional): Unique identifier for the order
            
        Returns:
            dict: Response containing transaction details
        """
        from .config import AUTHORIZE_NET_SANDBOX, AUTHORIZE_NET_ENDPOINT
        logger.info(f"🔧 AUTHORIZE.NET MODE: SANDBOX={AUTHORIZE_NET_SANDBOX}, ENDPOINT={AUTHORIZE_NET_ENDPOINT}")
        logger.info(f"Charging CIM profile - Customer: {customer_profile_id}, Payment: {payment_profile_id}, Amount: ${amount}")
        
        try:
            # Create merchant authentication
            merchant_auth = cls.get_merchant_auth()
            
            # Create profile to charge
            profile_to_charge = apicontractsv1.customerProfilePaymentType()
            profile_to_charge.customerProfileId = customer_profile_id
            profile_to_charge.paymentProfile = apicontractsv1.paymentProfile()
            profile_to_charge.paymentProfile.paymentProfileId = payment_profile_id
            
            # Create order object
            order = apicontractsv1.orderType()
            order.invoiceNumber = order_id or f"INV-{int(Decimal(amount) * 100)}"
            order.description = "POS System Order"
            
            # Create line items if provided
            line_items = None
            if order_items:
                line_items = apicontractsv1.ArrayOfLineItem()
                for i, item in enumerate(order_items):
                    line_item = apicontractsv1.lineItemType()
                    line_item.itemId = str(i + 1)
                    line_item.name = item.get('name', 'Product')[:31]
                    line_item.description = item.get('description', '')[:255]
                    line_item.quantity = str(item.get('quantity', 1))
                    line_item.unitPrice = str(item.get('price', 0))
                    line_items.lineItem.append(line_item)
            
            # Create transaction request
            transaction_request = apicontractsv1.transactionRequestType()
            transaction_request.transactionType = "authCaptureTransaction"
            transaction_request.amount = str(amount)
            transaction_request.profile = profile_to_charge
            transaction_request.order = order
            
            if line_items:
                transaction_request.lineItems = line_items
            
            # Create transaction request
            create_transaction = apicontractsv1.createTransactionRequest()
            create_transaction.merchantAuthentication = merchant_auth
            create_transaction.refId = order_id or "REF_" + str(int(Decimal(amount) * 100))
            create_transaction.transactionRequest = transaction_request
            
            # Execute transaction
            controller = createTransactionController(create_transaction)
            controller.setenvironment(AUTHORIZE_NET_ENDPOINT)
            controller.execute()
            
            # Get response
            api_response = controller.getresponse()
            
            if api_response is not None:
                if api_response.messages.resultCode == "Ok":
                    transaction = api_response.transactionResponse
                    
                    transaction_id = str(transaction.transId) if transaction.transId else f"auth_net_{uuid.uuid4().hex[:12]}"
                    auth_code = str(transaction.authCode) if transaction.authCode else f"AUTH_{uuid.uuid4().hex[:8].upper()}"
                    response_code = str(transaction.responseCode) if transaction.responseCode else "0"
                    avs_code = str(transaction.avsResultCode) if transaction.avsResultCode else ""
                    cvv_code = str(transaction.cvvResultCode) if transaction.cvvResultCode else ""
                    account_num = str(transaction.accountNumber) if transaction.accountNumber else ""
                    account_type = str(transaction.accountType) if transaction.accountType else ""
                    
                    # CRITICAL: Check the transaction responseCode, not just resultCode
                    # responseCode "1" = Approved, "2" = Declined, "3" = Error, "4" = Held for Review
                    if response_code != "1":
                        error_message = "Transaction was not approved."
                        error_code = response_code
                        
                        if hasattr(transaction, 'errors') and transaction.errors is not None:
                            error_message = str(transaction.errors.error[0].errorText)
                            error_code = str(transaction.errors.error[0].errorCode)
                        elif hasattr(transaction, 'messages') and transaction.messages is not None:
                            error_message = str(transaction.messages.message[0].description)
                        
                        friendly_message = get_friendly_error_message(error_code, error_message)
                        logger.error(f"CIM transaction NOT approved despite Ok resultCode - responseCode: {response_code}, error: {error_code} - {error_message}")
                        
                        return {
                            'success': False,
                            'transactionId': transaction_id,
                            'status': 'declined',
                            'message': friendly_message,
                            'code': error_code,
                            'responseCode': response_code,
                        }
                    
                    # Log auth code for monitoring (NOCODE is normal for this account configuration)
                    if auth_code == "NOCODE" or auth_code.upper() == "NOCODE":
                        logger.info(f"ℹ️ CIM transaction approved with Auth: NOCODE, Transaction ID: {transaction_id}")
                    
                    logger.info(f"CIM transaction processed - ID: {transaction_id}, Auth: {auth_code}")
                    
                    return {
                        'success': True,
                        'transactionId': transaction_id,
                        'status': 'approved',
                        'message': 'Transaction approved',
                        'authCode': auth_code,
                        'responseCode': response_code,
                        'avsResultCode': avs_code,
                        'cvvResultCode': cvv_code,
                        'accountNumber': account_num,
                        'accountType': account_type,
                    }
                else:
                    error_message = str(api_response.messages.message[0].text) if api_response.messages.message[0].text else "Transaction declined"
                    error_code = str(api_response.messages.message[0].code) if api_response.messages.message[0].code else "DECLINED"
                    
                    if hasattr(api_response, 'transactionResponse') and api_response.transactionResponse is not None:
                        if hasattr(api_response.transactionResponse, 'errors') and api_response.transactionResponse.errors is not None:
                            error_message = str(api_response.transactionResponse.errors.error[0].errorText) if api_response.transactionResponse.errors.error[0].errorText else error_message
                            error_code = str(api_response.transactionResponse.errors.error[0].errorCode) if api_response.transactionResponse.errors.error[0].errorCode else error_code
                    
                    friendly_message = get_friendly_error_message(str(error_code), str(error_message))
                    logger.error(f"CIM transaction error: {error_code} - {error_message}")
                    
                    return {
                        'success': False,
                        'status': 'declined',
                        'message': friendly_message,
                        'code': error_code
                    }
            else:
                logger.error("Null response from Authorize.net CIM transaction")
                
                return {
                    'success': False,
                    'status': 'error',
                    'message': 'Failed to process payment. No response from payment gateway.',
                    'code': 'NULL_RESPONSE'
                }
                
        except Exception as e:
            logger.exception(f"Error processing CIM transaction: {str(e)}")
            
            return {
                'success': False,
                'status': 'error',
                'message': f'An error occurred while processing payment: {str(e)}',
                'code': 'EXCEPTION'
            }
    
    @classmethod
    def delete_customer_payment_profile(cls, customer_profile_id, payment_profile_id):
        """
        Delete a payment profile from Authorize.Net CIM
        
        Args:
            customer_profile_id (str): The customer profile ID
            payment_profile_id (str): The payment profile ID to delete
            
        Returns:
            dict: Response indicating success or failure
        """
        logger.info(f"Deleting CIM payment profile - Customer: {customer_profile_id}, Payment: {payment_profile_id}")
        
        try:
            from authorizenet.apicontrollers import deleteCustomerPaymentProfileController
            
            # Create merchant authentication
            merchant_auth = cls.get_merchant_auth()
            
            # Create the delete request
            delete_profile = apicontractsv1.deleteCustomerPaymentProfileRequest()
            delete_profile.merchantAuthentication = merchant_auth
            delete_profile.customerProfileId = str(customer_profile_id)
            delete_profile.customerPaymentProfileId = str(payment_profile_id)
            
            # Execute the request
            controller = deleteCustomerPaymentProfileController(delete_profile)
            controller.setenvironment(AUTHORIZE_NET_ENDPOINT)
            controller.execute()
            
            # Get response
            response = controller.getresponse()
            
            if response is not None:
                if response.messages.resultCode == "Ok":
                    logger.info(f"Successfully deleted payment profile {payment_profile_id} from Authorize.Net")
                    return {
                        'success': True,
                        'message': 'Payment profile deleted from Authorize.Net'
                    }
                else:
                    error_message = str(response.messages.message[0].text) if response.messages.message else "Unknown error"
                    logger.error(f"Failed to delete payment profile: {error_message}")
                    return {
                        'success': False,
                        'message': f'Failed to delete from Authorize.Net: {error_message}'
                    }
            else:
                logger.error("Null response from Authorize.Net delete API")
                return {
                    'success': False,
                    'message': 'No response from Authorize.Net'
                }
                
        except Exception as e:
            logger.exception(f"Error deleting payment profile from Authorize.Net: {str(e)}")
            return {
                'success': False,
                'message': f'Error deleting from Authorize.Net: {str(e)}'
            }

    @classmethod
    def get_customer_payment_profiles(cls, customer_profile_id):
        """
        Fetch all payment profiles for a customer from Authorize.net CIM.
        
        Uses raw HTTP/XML requests to bypass the buggy pyxb SDK parser which
        fails with ContentNondeterminismExceededError on newer API responses.
        
        Args:
            customer_profile_id (str): The CIM customer profile ID
            
        Returns:
            list[dict]: List of payment profile dicts with card details,
                        or empty list on failure.
        """
        logger.info(f"Fetching CIM payment profiles for customer profile: {customer_profile_id}")
        
        try:
            import requests as http_requests
            import xml.etree.ElementTree as ET
            
            ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
            ns_prefix = '{' + ns + '}'
            
            get_profile_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <getCustomerProfileRequest xmlns="{ns}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
                <customerProfileId>{customer_profile_id}</customerProfileId>
                <unmaskExpirationDate>true</unmaskExpirationDate>
            </getCustomerProfileRequest>'''
            
            resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=get_profile_xml,
                                      headers={'Content-Type': 'text/xml'}, timeout=15)
            
            if resp.status_code != 200:
                logger.error(f"CIM getCustomerProfile HTTP error: {resp.status_code}")
                return None
            
            root = ET.fromstring(resp.text)
            
            result_code = root.findtext(f'.//{ns_prefix}resultCode')
            if result_code != 'Ok':
                error_text = root.findtext(f'.//{ns_prefix}text') or 'Unknown error'
                logger.error(f"Failed to fetch CIM profile {customer_profile_id}: {error_text}")
                return None
            
            # Find all paymentProfiles elements under the profile element
            payment_profile_elems = root.findall(f'.//{ns_prefix}profile/{ns_prefix}paymentProfiles')
            
            if not payment_profile_elems:
                logger.info(f"No payment profiles found for CIM profile {customer_profile_id}")
                return []
            
            profiles = []
            for pp in payment_profile_elems:
                try:
                    payment_profile_id = pp.findtext(f'{ns_prefix}customerPaymentProfileId') or ''
                    
                    # Extract card info
                    last4 = ''
                    card_brand = 'Credit Card'
                    exp_month = ''
                    exp_year = ''
                    
                    cc = pp.find(f'.//{ns_prefix}creditCard')
                    if cc is not None:
                        # cardNumber comes back masked like XXXX1234
                        card_num = cc.findtext(f'{ns_prefix}cardNumber') or ''
                        last4 = card_num[-4:] if len(card_num) >= 4 else card_num
                        
                        # cardType e.g. "Visa", "MasterCard"
                        card_brand = cc.findtext(f'{ns_prefix}cardType') or 'Credit Card'
                        
                        # expirationDate comes back as XXXX or YYYY-MM
                        exp_date = cc.findtext(f'{ns_prefix}expirationDate') or ''
                        if '-' in exp_date:
                            parts = exp_date.split('-')
                            if len(parts) == 2:
                                exp_year = parts[0][-2:]  # last 2 digits of year
                                exp_month = parts[1].zfill(2)
                        elif len(exp_date) == 4 and exp_date != 'XXXX':
                            exp_month = exp_date[:2]
                            exp_year = exp_date[2:]
                    
                    # Extract billing address and cardholder name
                    billing = {}
                    cardholder_name = ''
                    bt = pp.find(f'{ns_prefix}billTo')
                    if bt is not None:
                        first = bt.findtext(f'{ns_prefix}firstName') or ''
                        last = bt.findtext(f'{ns_prefix}lastName') or ''
                        cardholder_name = f"{first} {last}".strip()
                        billing = {
                            'street': bt.findtext(f'{ns_prefix}address') or '',
                            'city': bt.findtext(f'{ns_prefix}city') or '',
                            'state': bt.findtext(f'{ns_prefix}state') or '',
                            'zip': bt.findtext(f'{ns_prefix}zip') or '',
                            'country': bt.findtext(f'{ns_prefix}country') or 'USA',
                        }
                    
                    # Check if this is the default payment profile
                    is_default = (pp.findtext(f'{ns_prefix}defaultPaymentProfile') or '').lower() == 'true'
                    
                    profiles.append({
                        'payment_profile_id': payment_profile_id,
                        'last4': last4,
                        'card_brand': card_brand,
                        'exp_month': exp_month,
                        'exp_year': exp_year,
                        'billing': billing,
                        'cardholder_name': cardholder_name,
                        'is_default': is_default,
                    })
                except Exception as inner_e:
                    logger.warning(f"Error parsing payment profile: {inner_e}")
                    continue
            
            logger.info(f"Found {len(profiles)} payment profiles for CIM profile {customer_profile_id}")
            return profiles
            
        except Exception as e:
            logger.exception(f"Error fetching CIM payment profiles: {str(e)}")
            return None

    @classmethod
    def charge_accept_js_nonce(cls, opaque_data_descriptor, opaque_data_value, amount,
                               order_id=None, customer_email=None, invoice_number=None):
        """
        Charge a payment using an Accept.js one-time-use nonce (opaqueData).
        The nonce was generated client-side by Accept.js — raw card data never touches our server.

        Args:
            opaque_data_descriptor (str): e.g. "COMMON.ACCEPT.INAPP.PAYMENT"
            opaque_data_value (str): The one-time nonce token from Accept.js
            amount (float): The amount to charge
            order_id (str, optional): Reference ID for the transaction
            customer_email (str, optional): Customer email for receipt
            invoice_number (str, optional): Invoice number for the transaction

        Returns:
            dict: Response containing transaction details
        """
        from .config import AUTHORIZE_NET_SANDBOX, AUTHORIZE_NET_ENDPOINT
        logger.info(f"Charging Accept.js nonce — Amount: ${amount}, Invoice: {invoice_number}")

        try:
            import requests as http_requests
            import xml.etree.ElementTree as ET

            ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
            ns_prefix = '{' + ns + '}'

            email_xml = f'<email>{cls._xml_escape(customer_email)}</email>' if customer_email else ''
            ref_id = cls._xml_escape(order_id or f'INV-{int(Decimal(amount) * 100)}')
            inv_num = cls._xml_escape(invoice_number or ref_id)

            charge_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <createTransactionRequest xmlns="{ns}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
                <refId>{ref_id}</refId>
                <transactionRequest>
                    <transactionType>authCaptureTransaction</transactionType>
                    <amount>{amount}</amount>
                    <payment>
                        <opaqueData>
                            <dataDescriptor>{cls._xml_escape(opaque_data_descriptor)}</dataDescriptor>
                            <dataValue>{cls._xml_escape(opaque_data_value)}</dataValue>
                        </opaqueData>
                    </payment>
                    <order>
                        <invoiceNumber>{inv_num}</invoiceNumber>
                        <description>Invoice Payment</description>
                    </order>
                    {f'<customer>{email_xml}</customer>' if email_xml else ''}
                </transactionRequest>
            </createTransactionRequest>'''

            resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=charge_xml,
                                      headers={'Content-Type': 'text/xml'}, timeout=30)

            if resp.status_code != 200:
                logger.error(f"Accept.js charge HTTP error: {resp.status_code}")
                return {
                    'success': False, 'status': 'error',
                    'message': f'Gateway HTTP error: {resp.status_code}',
                    'code': 'HTTP_ERROR'
                }

            root = ET.fromstring(resp.text)
            result_code = root.findtext(f'.//{ns_prefix}resultCode')

            # Extract transaction response fields
            trans_id = root.findtext(f'.//{ns_prefix}transactionResponse/{ns_prefix}transId') or ''
            response_code = root.findtext(f'.//{ns_prefix}transactionResponse/{ns_prefix}responseCode') or '0'
            auth_code = root.findtext(f'.//{ns_prefix}transactionResponse/{ns_prefix}authCode') or ''
            avs_code = root.findtext(f'.//{ns_prefix}transactionResponse/{ns_prefix}avsResultCode') or ''
            cvv_code = root.findtext(f'.//{ns_prefix}transactionResponse/{ns_prefix}cvvResultCode') or ''
            account_num = root.findtext(f'.//{ns_prefix}transactionResponse/{ns_prefix}accountNumber') or ''
            account_type = root.findtext(f'.//{ns_prefix}transactionResponse/{ns_prefix}accountType') or ''

            if result_code == 'Ok' and response_code == '1':
                logger.info(f"Accept.js charge approved — Trans ID: {trans_id}, Auth: {auth_code}")
                return {
                    'success': True,
                    'transactionId': trans_id,
                    'status': 'approved',
                    'message': 'Transaction approved',
                    'authCode': auth_code,
                    'responseCode': response_code,
                    'avsResultCode': avs_code,
                    'cvvResultCode': cvv_code,
                    'accountNumber': account_num,
                    'accountType': account_type,
                }

            # Extract error details
            error_code = response_code
            error_message = 'Transaction declined'
            # Check transactionResponse/errors first
            err_elem = root.find(f'.//{ns_prefix}transactionResponse/{ns_prefix}errors/{ns_prefix}error')
            if err_elem is not None:
                error_code = err_elem.findtext(f'{ns_prefix}errorCode') or error_code
                error_message = err_elem.findtext(f'{ns_prefix}errorText') or error_message
            else:
                # Fallback to top-level messages
                msg_elem = root.find(f'.//{ns_prefix}messages/{ns_prefix}message')
                if msg_elem is not None:
                    error_code = msg_elem.findtext(f'{ns_prefix}code') or error_code
                    error_message = msg_elem.findtext(f'{ns_prefix}text') or error_message

            friendly = get_friendly_error_message(str(error_code), str(error_message))
            logger.error(f"Accept.js charge failed: [{error_code}] {error_message}")
            return {
                'success': False,
                'transactionId': trans_id,
                'status': 'declined',
                'message': friendly,
                'code': str(error_code),
                'responseCode': response_code,
            }

        except Exception as e:
            logger.exception(f"Error processing Accept.js nonce charge: {str(e)}")
            return {
                'success': False, 'status': 'error',
                'message': f'Payment processing error: {str(e)}',
                'code': 'EXCEPTION'
            }

    @classmethod
    def set_default_payment_profile(cls, customer_profile_id, payment_profile_id):
        """
        Set a payment profile as the default for a customer in Authorize.net CIM.
        
        Uses updateCustomerPaymentProfile with defaultPaymentProfile=True.
        We must first fetch the existing profile data to preserve it during the update.
        
        Args:
            customer_profile_id (str): The CIM customer profile ID
            payment_profile_id (str): The payment profile ID to set as default
            
        Returns:
            dict: {'success': True} on success, {'success': False, 'error': '...'} on failure
        """
        logger.info(f"Setting default payment profile {payment_profile_id} for customer {customer_profile_id}")
        
        try:
            from authorizenet.apicontrollers import (
                getCustomerPaymentProfileController,
                updateCustomerPaymentProfileController
            )
            
            merchant_auth = cls.get_merchant_auth()
            
            # Step 1: Fetch the existing payment profile to preserve its data
            get_req = apicontractsv1.getCustomerPaymentProfileRequest()
            get_req.merchantAuthentication = merchant_auth
            get_req.customerProfileId = str(customer_profile_id)
            get_req.customerPaymentProfileId = str(payment_profile_id)
            get_req.unmaskExpirationDate = True
            
            get_ctrl = getCustomerPaymentProfileController(get_req)
            get_ctrl.setenvironment(AUTHORIZE_NET_ENDPOINT)
            get_ctrl.execute()
            get_resp = get_ctrl.getresponse()
            
            if get_resp is None or get_resp.messages.resultCode != "Ok":
                error_msg = "Failed to fetch payment profile"
                if get_resp and get_resp.messages and get_resp.messages.message:
                    try:
                        error_msg = str(get_resp.messages.message[0].text)
                    except Exception:
                        pass
                logger.error(f"Failed to fetch payment profile {payment_profile_id}: {error_msg}")
                return {'success': False, 'error': error_msg}
            
            existing_profile = get_resp.paymentProfile
            
            # Step 2: Update the profile with defaultPaymentProfile=True
            update_req = apicontractsv1.updateCustomerPaymentProfileRequest()
            update_req.merchantAuthentication = merchant_auth
            update_req.customerProfileId = str(customer_profile_id)
            
            # Build the payment profile update object
            profile_update = apicontractsv1.customerPaymentProfileExType()
            profile_update.customerPaymentProfileId = str(payment_profile_id)
            profile_update.defaultPaymentProfile = True
            
            # Reconstruct payment data from the fetched profile.
            # Authorize.net requires payment data on update even if we only
            # want to flip defaultPaymentProfile. It accepts its own masked
            # format (e.g. "XXXX1234") back. We must ensure the values are
            # valid masked strings — never bare "XXXX" which is too short.
            try:
                ep = existing_profile
                cc_obj = None
                if hasattr(ep, 'payment') and ep.payment is not None:
                    cc_src = getattr(ep.payment, 'creditCard', None)
                    if cc_src is not None:
                        card_num = str(cc_src.cardNumber) if cc_src.cardNumber else ''
                        exp_date = str(cc_src.expirationDate) if cc_src.expirationDate else ''
                        # Only set payment if we got valid masked data back
                        if len(card_num) >= 8 and len(exp_date) >= 4:
                            cc_obj = apicontractsv1.creditCardType()
                            cc_obj.cardNumber = card_num
                            cc_obj.expirationDate = exp_date
                
                if cc_obj is not None:
                    payment_obj = apicontractsv1.paymentType()
                    payment_obj.creditCard = cc_obj
                    profile_update.payment = payment_obj
                else:
                    logger.warning(f"Could not reconstruct payment data for profile {payment_profile_id}, sending update without payment block")
            except Exception as pay_err:
                logger.warning(f"Error reconstructing payment data: {pay_err}")
            
            # Reconstruct billing info similarly
            try:
                if hasattr(existing_profile, 'billTo') and existing_profile.billTo is not None:
                    bt_src = existing_profile.billTo
                    bill_to = apicontractsv1.customerAddressType()
                    bill_to.firstName = str(getattr(bt_src, 'firstName', '') or '')
                    bill_to.lastName = str(getattr(bt_src, 'lastName', '') or '')
                    bill_to.address = str(getattr(bt_src, 'address', '') or '')
                    bill_to.city = str(getattr(bt_src, 'city', '') or '')
                    bill_to.state = str(getattr(bt_src, 'state', '') or '')
                    bill_to.zip = str(getattr(bt_src, 'zip', '') or '')
                    bill_to.country = str(getattr(bt_src, 'country', 'USA') or 'USA')
                    profile_update.billTo = bill_to
            except Exception as bill_err:
                logger.warning(f"Error reconstructing billing data: {bill_err}")
            
            update_req.paymentProfile = profile_update
            update_req.validationMode = 'none'
            
            update_ctrl = updateCustomerPaymentProfileController(update_req)
            update_ctrl.setenvironment(AUTHORIZE_NET_ENDPOINT)
            update_ctrl.execute()
            update_resp = update_ctrl.getresponse()
            
            if update_resp is None or update_resp.messages.resultCode != "Ok":
                error_msg = "Failed to update payment profile"
                if update_resp and update_resp.messages and update_resp.messages.message:
                    try:
                        error_msg = str(update_resp.messages.message[0].text)
                    except Exception:
                        pass
                logger.error(f"Failed to set default payment profile {payment_profile_id}: {error_msg}")
                return {'success': False, 'error': error_msg}
            
            logger.info(f"Successfully set payment profile {payment_profile_id} as default for customer {customer_profile_id}")
            return {'success': True}
            
        except Exception as e:
            logger.exception(f"Error setting default payment profile: {str(e)}")
            return {'success': False, 'error': str(e)}

    @classmethod
    def update_customer_payment_profile(cls, customer_profile_id, payment_profile_id,
                                         exp_month=None, exp_year=None, billing_address=None):
        """
        Update a payment profile's expiry date and/or billing address on Authorize.net CIM.

        Uses raw HTTP/XML to bypass the buggy pyxb SDK parser.
        The API requires us to send back the masked card number even if we only
        want to change the expiry date, so we first fetch the existing profile.

        Args:
            customer_profile_id (str): CIM customer profile ID
            payment_profile_id (str): CIM payment profile ID to update
            exp_month (str, optional): New expiry month (MM)
            exp_year (str, optional): New expiry year (YYYY or YY)
            billing_address (dict, optional): {street, city, state, zip, country}

        Returns:
            dict: {'success': True} on success, {'success': False, 'error': '...'} on failure
        """
        logger.info(f"Updating CIM payment profile {payment_profile_id} for customer {customer_profile_id}")

        try:
            import requests as http_requests
            import xml.etree.ElementTree as ET

            ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
            ns_prefix = '{' + ns + '}'

            # Step 1: Fetch the existing payment profile to get the masked card number
            get_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <getCustomerPaymentProfileRequest xmlns="{ns}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
                <customerProfileId>{customer_profile_id}</customerProfileId>
                <customerPaymentProfileId>{payment_profile_id}</customerPaymentProfileId>
                <unmaskExpirationDate>true</unmaskExpirationDate>
            </getCustomerPaymentProfileRequest>'''

            get_resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=get_xml,
                                          headers={'Content-Type': 'text/xml'}, timeout=15)
            if get_resp.status_code != 200:
                logger.error(f"CIM getCustomerPaymentProfile HTTP error: {get_resp.status_code}")
                return {'success': False, 'error': f'HTTP {get_resp.status_code}'}

            get_root = ET.fromstring(get_resp.text)
            result_code = get_root.findtext(f'.//{ns_prefix}resultCode')
            if result_code != 'Ok':
                error_text = get_root.findtext(f'.//{ns_prefix}text') or 'Unknown error'
                logger.error(f"Failed to fetch payment profile {payment_profile_id}: {error_text}")
                return {'success': False, 'error': error_text}

            pp = get_root.find(f'.//{ns_prefix}paymentProfile')
            if pp is None:
                return {'success': False, 'error': 'paymentProfile element not found in response'}

            # Extract existing masked card number (e.g. XXXX1234)
            existing_card_num = ''
            existing_exp = ''
            cc = pp.find(f'.//{ns_prefix}creditCard')
            if cc is not None:
                existing_card_num = cc.findtext(f'{ns_prefix}cardNumber') or ''
                existing_exp = cc.findtext(f'{ns_prefix}expirationDate') or ''

            if not existing_card_num or len(existing_card_num) < 8:
                return {'success': False, 'error': 'Could not retrieve masked card number from CIM'}

            # Build new expiry date in YYYY-MM format
            if exp_month and exp_year:
                yr = exp_year
                if len(yr) == 2:
                    yr = f'20{yr}'
                new_exp = f'{yr}-{exp_month.zfill(2)}'
            else:
                new_exp = existing_exp

            # Extract existing billing info as defaults
            bt = pp.find(f'.//{ns_prefix}billTo')
            ex_first = ''
            ex_last = ''
            ex_street = ''
            ex_city = ''
            ex_state = ''
            ex_zip = ''
            ex_country = 'USA'
            if bt is not None:
                ex_first = bt.findtext(f'{ns_prefix}firstName') or ''
                ex_last = bt.findtext(f'{ns_prefix}lastName') or ''
                ex_street = bt.findtext(f'{ns_prefix}address') or ''
                ex_city = bt.findtext(f'{ns_prefix}city') or ''
                ex_state = bt.findtext(f'{ns_prefix}state') or ''
                ex_zip = bt.findtext(f'{ns_prefix}zip') or ''
                ex_country = bt.findtext(f'{ns_prefix}country') or 'USA'

            # Override with provided billing address
            b = billing_address or {}
            bill_street = b.get('street', ex_street) or ex_street
            bill_city = b.get('city', ex_city) or ex_city
            bill_state = b.get('state', ex_state) or ex_state
            bill_zip = b.get('zip', ex_zip) or ex_zip
            bill_country = b.get('country', ex_country) or ex_country

            # Escape XML special characters
            def esc(val):
                return (val or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

            # Step 2: Send the update request
            update_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <updateCustomerPaymentProfileRequest xmlns="{ns}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
                <customerProfileId>{customer_profile_id}</customerProfileId>
                <paymentProfile>
                    <billTo>
                        <firstName>{esc(ex_first)}</firstName>
                        <lastName>{esc(ex_last)}</lastName>
                        <address>{esc(bill_street)}</address>
                        <city>{esc(bill_city)}</city>
                        <state>{esc(bill_state)}</state>
                        <zip>{esc(bill_zip)}</zip>
                        <country>{esc(bill_country)}</country>
                    </billTo>
                    <payment>
                        <creditCard>
                            <cardNumber>{existing_card_num}</cardNumber>
                            <expirationDate>{new_exp}</expirationDate>
                        </creditCard>
                    </payment>
                    <customerPaymentProfileId>{payment_profile_id}</customerPaymentProfileId>
                </paymentProfile>
                <validationMode>none</validationMode>
            </updateCustomerPaymentProfileRequest>'''

            update_resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=update_xml,
                                              headers={'Content-Type': 'text/xml'}, timeout=15)
            if update_resp.status_code != 200:
                logger.error(f"CIM updateCustomerPaymentProfile HTTP error: {update_resp.status_code}")
                return {'success': False, 'error': f'HTTP {update_resp.status_code}'}

            update_root = ET.fromstring(update_resp.text)
            update_result = update_root.findtext(f'.//{ns_prefix}resultCode')
            if update_result != 'Ok':
                error_text = update_root.findtext(f'.//{ns_prefix}text') or 'Unknown error'
                logger.error(f"Failed to update payment profile {payment_profile_id}: {error_text}")
                return {'success': False, 'error': error_text}

            logger.info(f"Successfully updated CIM payment profile {payment_profile_id}")
            return {'success': True}

        except Exception as e:
            logger.exception(f"Error updating CIM payment profile: {str(e)}")
            return {'success': False, 'error': str(e)}

    @classmethod
    def get_customer_profile_by_email(cls, email, max_profiles=200):
        """
        Search Authorize.net CIM for a customer profile matching the given email.
        
        Uses raw HTTP/XML requests to bypass the buggy pyxb SDK parser which
        fails with ContentNondeterminismExceededError on newer API responses.
        Results are cached on the Contact model so this only runs once per customer.
        
        Args:
            email (str): Customer email to search for
            max_profiles (int): Max number of newest CIM profiles to check.
                Use 50 for a quick shallow search (~5-7s), 200 for a deep search (~30s).
            
        Returns:
            str or None: The customer profile ID if found, None otherwise
        """
        if not email:
            return None
            
        logger.info(f"Searching CIM for customer profile by email: {email}")
        
        try:
            import requests as http_requests
            import xml.etree.ElementTree as ET
            
            ns = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
            
            # Step 1: Get all customer profile IDs via raw XML
            get_ids_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <getCustomerProfileIdsRequest xmlns="{ns}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
            </getCustomerProfileIdsRequest>'''
            
            ids_resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=get_ids_xml,
                                          headers={'Content-Type': 'text/xml'}, timeout=30)
            
            if ids_resp.status_code != 200:
                logger.error(f"CIM getCustomerProfileIds HTTP error: {ids_resp.status_code}")
                return None
            
            ids_root = ET.fromstring(ids_resp.text)
            ns_prefix = '{' + ns + '}'
            
            # Check result code
            result_code = ids_root.findtext(f'.//{ns_prefix}resultCode')
            if result_code != 'Ok':
                logger.error(f"CIM getCustomerProfileIds failed: {result_code}")
                return None
            
            # Extract all numeric string IDs
            profile_ids = [elem.text for elem in ids_root.findall(f'.//{ns_prefix}numericString') if elem.text]
            logger.info(f"Found {len(profile_ids)} total CIM profiles to search")
            
            # Check newest profiles first (most likely to match)
            profile_ids = list(reversed(profile_ids))
            email_lower = email.strip().lower()
            
            # Cap the search to avoid hanging when no profile exists
            # Profiles are newest-first, so if not found in first N, it's very unlikely
            import time as _time
            MAX_PROFILES_TO_CHECK = max_profiles
            MAX_SEARCH_SECONDS = 30 if max_profiles > 50 else 15
            search_start = _time.time()
            
            # Step 2: Check each profile for matching email via raw XML
            for idx, pid in enumerate(profile_ids):
                # Bail out if we've checked enough or taken too long
                if idx >= MAX_PROFILES_TO_CHECK:
                    logger.info(f"CIM email search: hit {MAX_PROFILES_TO_CHECK} profile limit for {email}, stopping")
                    break
                elapsed = _time.time() - search_start
                if elapsed > MAX_SEARCH_SECONDS:
                    logger.info(f"CIM email search: hit {MAX_SEARCH_SECONDS}s time limit after {idx} profiles for {email}, stopping")
                    break
                if idx > 0 and idx % 100 == 0:
                    logger.info(f"CIM email search progress: checked {idx}/{len(profile_ids)} profiles for {email}")
                try:
                    get_profile_xml = f'''<?xml version="1.0" encoding="utf-8"?>
                    <getCustomerProfileRequest xmlns="{ns}">
                        <merchantAuthentication>
                            <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                            <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                        </merchantAuthentication>
                        <customerProfileId>{pid}</customerProfileId>
                    </getCustomerProfileRequest>'''
                    
                    profile_resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=get_profile_xml,
                                                      headers={'Content-Type': 'text/xml'}, timeout=10)
                    
                    if profile_resp.status_code != 200:
                        continue
                    
                    profile_root = ET.fromstring(profile_resp.text)
                    profile_result = profile_root.findtext(f'.//{ns_prefix}resultCode')
                    if profile_result != 'Ok':
                        continue
                    
                    profile_email = profile_root.findtext(f'.//{ns_prefix}profile/{ns_prefix}email') or ''
                    if profile_email.strip().lower() == email_lower:
                        logger.info(f"Found CIM profile {pid} matching email {email}")
                        return str(pid)
                except Exception as inner_e:
                    logger.warning(f"Error checking CIM profile {pid}: {inner_e}")
                    continue
            
            logger.info(f"No CIM profile found for email: {email}")
            return None
            
        except Exception as e:
            logger.exception(f"Error searching CIM by email: {str(e)}")
            return None
