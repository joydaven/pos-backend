"""
GoHighLevel API integration utilities for direct API calls to GHL.
"""
import requests
import logging
from django.conf import settings
from core.secrets import get_secret

logger = logging.getLogger(__name__)

# GHL API constants
GHL_API_BASE_URL = 'https://services.leadconnectorhq.com'
GHL_API_TOKEN = get_secret('GHL_API_TOKEN', '')
GHL_API_ORDERS_TOKEN = get_secret('GHL_API_ORDERS_TOKEN', '')
GHL_API_VERSION = '2021-07-28'
GHL_DEFAULT_LOCATION_ID = get_secret('GHL_DEFAULT_LOCATION_ID', '')


def search_ghl_contact_by_email(email, location_id=None):
    """
    Search for a GHL contact by email address.
    
    Args:
        email (str): Email address to search for
        location_id (str): GHL location ID (optional, uses default if not provided)
        
    Returns:
        dict: Contact data if found, None otherwise
    """
    if not email:
        logger.warning("Cannot search GHL without email address")
        return None
    
    # Use provided location_id or fall back to default
    target_location_id = location_id or GHL_DEFAULT_LOCATION_ID
    
    logger.info(f"DEBUG: Email received in search function: '{email}'")
    logger.info(f"DEBUG: Email repr: {repr(email)}")
    logger.info(f"DEBUG: Using location ID: {target_location_id}")
    logger.info(f"Searching GHL for contact with email: {email}")
    
    try:
        headers = {
            'Authorization': f'Bearer {GHL_API_TOKEN}',
            'Version': GHL_API_VERSION,
            'Content-Type': 'application/json'
        }
        
        request_body = {
            "locationId": target_location_id,
            "query": email,
            "page": 1,
            "pageLimit": 20
        }
        
        response = requests.post(
            f"{GHL_API_BASE_URL}/contacts/search",
            json=request_body,
            headers=headers
        )
        
        response.raise_for_status()  # Raise exception for non-200 responses
        
        data = response.json()
        logger.info(f"GHL API response received: {len(data.get('contacts', [])) if data else 0} contacts found")
        
        # Find the contact with the exact matching email
        if data and 'contacts' in data and data['contacts']:
            logger.info(f"DEBUG: Checking {len(data['contacts'])} contacts for exact email match")
            for i, contact in enumerate(data['contacts']):
                contact_email = contact.get('email', 'NO_EMAIL')
                logger.info(f"DEBUG: Contact {i+1}: email='{contact_email}', searching for='{email}'")
                if contact.get('email') and contact['email'].lower() == email.lower():
                    logger.info(f"Found exact match for email {email} in GHL - Contact ID: {contact.get('id')}")
                    return contact
            
            logger.info(f"No exact email match found in GHL for {email}")
        else:
            logger.info(f"No contacts found in GHL for email {email}")
        
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling GHL API: {e}")
        return None


def get_ghl_contact_id_by_email(email, location_id=None):
    """
    Get the GHL contact ID for a given email.
    
    Args:
        email (str): Email address to search for
        location_id (str): GHL location ID (optional, uses default if not provided)
        
    Returns:
        str: GHL contact ID if found, None otherwise
    """
    contact = search_ghl_contact_by_email(email, location_id)
    if contact and 'id' in contact:
        return contact['id']
    return None


def update_ghl_contact_custom_field(contact_id, field_name, field_value):
    """
    Update a custom field for a GHL contact.
    
    Args:
        contact_id (str): GHL contact ID
        field_name (str): Custom field name (e.g., 'credit_iv_ozone')
        field_value (int): Field value (credit points)
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not contact_id or not field_name:
        logger.warning("Cannot update GHL custom field without contact_id and field_name")
        return False
    
    logger.info(f"Updating GHL contact {contact_id} custom field '{field_name}' to {field_value}")
    
    try:
        headers = {
            'Authorization': f'Bearer {GHL_API_TOKEN}',
            'Version': GHL_API_VERSION,
            'Content-Type': 'application/json'
        }
        
        # Use the upsert contact endpoint to update custom fields
        # Try both formats - field_name as key AND as id
        request_body = {
            "customFields": [
                {
                    "id": field_name,  # Try using 'id' instead of 'key'
                    "value": str(field_value)  # Try using 'value' instead of 'field_value'
                }
            ]
        }
        
        logger.info(f"🔍 GHL API Request Details:")
        logger.info(f"   URL: {GHL_API_BASE_URL}/contacts/{contact_id}")
        logger.info(f"   Headers: {headers}")
        logger.info(f"   Request Body: {request_body}")
        
        response = requests.put(
            f"{GHL_API_BASE_URL}/contacts/{contact_id}",
            json=request_body,
            headers=headers
        )
        
        logger.info(f"🔍 GHL API Response Details:")
        logger.info(f"   Status Code: {response.status_code}")
        logger.info(f"   Response Headers: {dict(response.headers)}")
        logger.info(f"   Response Body: {response.text}")
        
        if response.status_code == 200:
            logger.info(f"Successfully updated GHL custom field '{field_name}' for contact {contact_id}")
            return True
        else:
            logger.error(f"Failed to update GHL custom field. Status: {response.status_code}, Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error updating GHL custom field: {e}")
        return False


def add_ghl_note(contact_id, note_body):
    """
    Add a note to a GHL contact.
    
    Args:
        contact_id (str): GHL contact ID
        note_body (str): Note content
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not contact_id or not note_body:
        logger.warning("Cannot add GHL note without contact_id and note_body")
        return False
    
    logger.info(f"Adding note to GHL contact {contact_id}: {note_body}")
    
    try:
        headers = {
            'Authorization': f'Bearer {GHL_API_TOKEN}',
            'Version': GHL_API_VERSION,
            'Content-Type': 'application/json'
        }
        
        request_body = {
            "body": note_body
        }
        
        response = requests.post(
            f"{GHL_API_BASE_URL}/contacts/{contact_id}/notes",
            json=request_body,
            headers=headers
        )
        
        if response.status_code in [200, 201]:
            logger.info(f"Successfully added note to GHL contact {contact_id}")
            return True
        else:
            logger.error(f"Failed to add GHL note. Status: {response.status_code}, Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Error adding GHL note: {e}")
        return False


def sync_all_credit_points_to_ghl(contact_id):
    """
    Sync all credit points for a contact to GHL using Notes API.
    
    Args:
        contact_id (str): GHL contact ID
        
    Returns:
        bool: True if successful, False otherwise
    """
    if not contact_id:
        logger.warning("Cannot sync to GHL without contact_id")
        return False
    
    try:
        from .models import CreditServicePoints, Contact
        
        # Get the contact and all their credit points
        contact = Contact.objects.filter(ghl_contact_id=contact_id).first()
        if not contact:
            logger.warning(f"No contact found with GHL ID {contact_id}")
            return False
        
        # Get all credit points for this contact
        credit_points = CreditServicePoints.objects.filter(
            customer=contact,
            points__gt=0
        ).select_related('product')
        
        if not credit_points.exists():
            logger.info(f"No credit points found for contact {contact.email}")
            return True
        
        logger.info(f"Syncing {credit_points.count()} credit services to GHL via Notes API")
        
        # Build comprehensive credit service summary
        credit_summary = []
        total_points = 0
        for cp in credit_points:
            credit_summary.append(f"• {cp.product.name}: {cp.points} points")
            total_points += cp.points
        
        # Create note content
        note_body = f"🏥 CREDIT SERVICE BALANCE UPDATE\n\n" + "\n".join(credit_summary) + f"\n\nTotal Credit Points: {total_points}"
        
        # Create custom field value
        field_value = " | ".join([f"{cp.product.name}: {cp.points} pts" for cp in credit_points])
        
        logger.info(f"Syncing credit summary to GHL note and custom field")
        
        # Add note to contact timeline
        note_success = add_ghl_note(contact_id, note_body)
        
        # Update custom field for searchability
        field_success = update_ghl_contact_custom_field(contact_id, "credit_service_log", field_value)
        
        if note_success and field_success:
            logger.info(f"✅ Successfully synced credit points via Notes API")
            return True
        else:
            logger.error(f"❌ Failed to sync credit points")
            return False
        
    except Exception as e:
        logger.error(f"Error syncing all credit points: {str(e)}")
        return False


def get_ghl_contact_by_id(contact_id):
    """
    Fetch a GHL contact by their GHL contact ID.
    Uses GET /contacts/{contactId} endpoint.
    
    Args:
        contact_id (str): GHL contact ID
        
    Returns:
        dict: Full contact data including customFields, or None on failure
    """
    if not contact_id:
        logger.warning("Cannot fetch GHL contact without contact_id")
        return None
    
    logger.info(f"Fetching GHL contact by ID: {contact_id}")
    
    try:
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {GHL_API_TOKEN}',
            'Version': GHL_API_VERSION,
        }
        
        response = requests.get(
            f"{GHL_API_BASE_URL}/contacts/{contact_id}",
            headers=headers
        )
        
        response.raise_for_status()
        data = response.json()
        
        contact = data.get('contact')
        if contact:
            logger.info(f"Fetched GHL contact: {contact.get('email')} (ID: {contact_id})")
            return contact
        
        logger.warning(f"No contact data in GHL response for ID: {contact_id}")
        return None
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching GHL contact {contact_id}: {e}")
        return None


def push_contact_to_ghl(contact):
    """
    Push POS Contact profile fields to GHL (reverse sync: POS → GHL).
    Uses PUT /contacts/{contactId} to update the GHL contact.

    Args:
        contact: crm.models.Contact instance (must have ghl_contact_id)

    Returns:
        dict: {'success': bool, 'updated_fields': list, 'error': str|None}
    """
    if not contact.ghl_contact_id:
        logger.info(f"POS→GHL skip: {contact.email} has no ghl_contact_id")
        return {'success': False, 'error': 'No ghl_contact_id', 'updated_fields': []}

    # Map POS Contact fields → GHL API field names
    # NOTE: billing_address_2 is a GHL *custom field* ("Street Address 2"),
    # not a standard API property. Sending 'address2' causes a 422 rejection.
    # It must be sent via the customFields array instead.
    GHL_STREET_ADDRESS_2_CUSTOM_FIELD_ID = 'mXxWyKmGVx5jb9hSbuMY'

    ghl_payload = {}
    field_map = {
        'first_name': 'firstName',
        'last_name': 'lastName',
        'phone': 'phone',
        'billing_address': 'address1',
        'billing_city': 'city',
        'billing_state': 'state',
        'billing_postcode': 'postalCode',
        'billing_country': 'country',
    }

    pushed_fields = []
    for pos_field, ghl_key in field_map.items():
        val = getattr(contact, pos_field, None)
        if val and str(val).strip():
            ghl_payload[ghl_key] = str(val).strip()
            pushed_fields.append(pos_field)

    # Push billing_address_2 as a GHL custom field
    addr2 = getattr(contact, 'billing_address_2', None)
    addr2_val = str(addr2).strip() if addr2 else ''
    ghl_payload['customFields'] = [
        {'id': GHL_STREET_ADDRESS_2_CUSTOM_FIELD_ID, 'field_value': addr2_val}
    ]
    if addr2_val:
        pushed_fields.append('billing_address_2')

    # Also push email (always include so GHL record stays linked)
    if contact.email:
        ghl_payload['email'] = contact.email

    if not ghl_payload:
        logger.info(f"POS→GHL skip: no fields to push for {contact.email}")
        return {'success': True, 'updated_fields': [], 'error': None}

    logger.info(
        f"POS→GHL push for {contact.email} (GHL ID: {contact.ghl_contact_id}): "
        f"fields={pushed_fields}"
    )

    try:
        headers = {
            'Authorization': f'Bearer {GHL_API_TOKEN}',
            'Version': GHL_API_VERSION,
            'Content-Type': 'application/json',
        }

        response = requests.put(
            f"{GHL_API_BASE_URL}/contacts/{contact.ghl_contact_id}",
            json=ghl_payload,
            headers=headers,
        )

        if response.status_code == 200:
            logger.info(f"✅ POS→GHL push success for {contact.email}: {pushed_fields}")
            return {'success': True, 'updated_fields': pushed_fields, 'error': None}
        else:
            error_msg = f"GHL API {response.status_code}: {response.text[:300]}"
            logger.error(f"POS→GHL push failed for {contact.email}: {error_msg}")
            return {'success': False, 'updated_fields': [], 'error': error_msg}

    except requests.exceptions.RequestException as e:
        logger.error(f"POS→GHL push error for {contact.email}: {e}")
        return {'success': False, 'updated_fields': [], 'error': str(e)}


def apply_ghl_contact_fields(contact, ghl_data, save=True):
    """
    Map GHL contact fields onto a local Contact model instance.
    GHL is the source of truth — always overwrite POS fields when GHL has a value.

    Args:
        contact: crm.models.Contact instance
        ghl_data (dict): Raw GHL contact dict (from search or get-by-id endpoint)
        save (bool): If True, call contact.save() with only the changed fields.

    Returns:
        list[str]: Names of Contact fields that were updated.
    """
    if not ghl_data or not isinstance(ghl_data, dict):
        return []

    # Filter out junk placeholder values from GHL ("undefined", "null", etc.)
    JUNK_VALUES = {'undefined', 'null', 'none', '\u200e', 'n/a'}
    def _is_real(val):
        return val and str(val).strip() and str(val).strip().lower() not in JUNK_VALUES

    updated_fields = []

    # --- core profile fields (always overwrite when GHL has a non-empty value) ---
    field_map = {
        'firstName': 'first_name',
        'lastName': 'last_name',
        'phone': 'phone',
        # Search endpoint uses 'address'; get-by-id uses 'address1'
        'city': 'billing_city',
        'state': 'billing_state',
        'postalCode': 'billing_postcode',
        'country': 'billing_country',
    }

    for ghl_key, model_field in field_map.items():
        ghl_val = ghl_data.get(ghl_key)
        if _is_real(ghl_val):
            clean_val = str(ghl_val).strip()
            if getattr(contact, model_field, None) != clean_val:
                setattr(contact, model_field, clean_val)
                updated_fields.append(model_field)

    # Handle address (search endpoint = 'address', get-by-id = 'address1')
    ghl_address = ghl_data.get('address') or ghl_data.get('address1')
    if _is_real(ghl_address):
        clean_addr = str(ghl_address).strip()
        if contact.billing_address != clean_addr:
            contact.billing_address = clean_addr
            updated_fields.append('billing_address')

    # Handle address line 2
    # GHL get-by-id returns 'address2'; GHL workflow webhooks flatten the custom
    # field by its display name "Street Address 2" as a top-level key.
    ghl_address2 = ghl_data.get('address2') or ghl_data.get('Street Address 2')
    if _is_real(ghl_address2):
        clean_addr2 = str(ghl_address2).strip()
        if contact.billing_address_2 != clean_addr2:
            contact.billing_address_2 = clean_addr2
            updated_fields.append('billing_address_2')

    # --- GHL-owned metadata fields (always overwrite) ---
    ghl_tags = ghl_data.get('tags')
    if isinstance(ghl_tags, list):
        if contact.ghl_tags != ghl_tags:
            contact.ghl_tags = ghl_tags
            updated_fields.append('ghl_tags')

    # customFields comes as [{id, value}, ...] — convert to {id: value} dict
    ghl_custom_fields = ghl_data.get('customFields')
    if isinstance(ghl_custom_fields, list):
        cf_dict = {}
        for cf in ghl_custom_fields:
            cf_id = cf.get('id')
            cf_val = cf.get('value')
            if cf_id:
                cf_dict[cf_id] = cf_val
        if contact.ghl_custom_fields != cf_dict:
            contact.ghl_custom_fields = cf_dict
            updated_fields.append('ghl_custom_fields')

    if updated_fields and save:
        contact.save(update_fields=updated_fields)
        logger.info(
            f"GHL→POS sync for {contact.email}: updated {updated_fields}"
        )
    elif updated_fields:
        logger.info(
            f"GHL→POS sync for {contact.email}: staged {updated_fields} (not saved yet)"
        )

    return updated_fields


def delete_ghl_contact(contact_id):
    """
    Delete a GHL contact by their GHL contact ID.
    Uses DELETE /contacts/{contactId} endpoint.

    Args:
        contact_id (str): GHL contact ID

    Returns:
        dict: {'success': bool, 'error': str|None}
    """
    if not contact_id:
        logger.warning("Cannot delete GHL contact without contact_id")
        return {'success': False, 'error': 'No contact_id provided'}

    logger.info(f"Deleting GHL contact: {contact_id}")

    try:
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {GHL_API_TOKEN}',
            'Version': GHL_API_VERSION,
        }

        response = requests.delete(
            f"{GHL_API_BASE_URL}/contacts/{contact_id}",
            headers=headers
        )

        if response.status_code in [200, 204]:
            logger.info(f"✅ Successfully deleted GHL contact {contact_id}")
            return {'success': True, 'error': None}
        else:
            error_msg = f"GHL API {response.status_code}: {response.text[:300]}"
            logger.error(f"Failed to delete GHL contact {contact_id}: {error_msg}")
            return {'success': False, 'error': error_msg}

    except requests.exceptions.RequestException as e:
        logger.error(f"Error deleting GHL contact {contact_id}: {e}")
        return {'success': False, 'error': str(e)}


def list_ghl_orders(limit=20, offset=0, contact_id=None, status=None,
                    start_at=None, end_at=None, search=None):
    """
    List orders from the GHL Payments API.

    Args:
        limit: Max results per page (default 20)
        offset: Pagination offset
        contact_id: Filter by GHL contact ID
        status: Filter by order status
        start_at: ISO date string for date range start
        end_at: ISO date string for date range end
        search: Search by order name

    Returns:
        dict with 'data' (list of orders) and 'totalCount', or None on failure
    """
    token = GHL_API_ORDERS_TOKEN or GHL_API_TOKEN
    try:
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
            'Version': GHL_API_VERSION,
        }

        params = {
            'altId': GHL_DEFAULT_LOCATION_ID,
            'altType': 'location',
            'limit': limit,
            'offset': offset,
        }

        if contact_id:
            params['contactId'] = contact_id
        if status:
            params['status'] = status
        if start_at:
            params['startAt'] = start_at
        if end_at:
            params['endAt'] = end_at
        if search:
            params['search'] = search

        response = requests.get(
            f"{GHL_API_BASE_URL}/payments/orders",
            headers=headers,
            params=params,
        )

        response.raise_for_status()
        data = response.json()
        logger.info(
            f"GHL Orders API: fetched {len(data.get('data', []))} orders "
            f"(total {data.get('totalCount', '?')}), offset={offset}"
        )
        return data

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching GHL orders: {e}")
        return None


def get_ghl_order_by_id(order_id):
    """
    Fetch a single GHL order by its ID.

    Args:
        order_id: GHL order _id

    Returns:
        dict: Order data, or None on failure
    """
    if not order_id:
        logger.warning("Cannot fetch GHL order without order_id")
        return None

    token = GHL_API_ORDERS_TOKEN or GHL_API_TOKEN
    try:
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {token}',
            'Version': GHL_API_VERSION,
        }

        response = requests.get(
            f"{GHL_API_BASE_URL}/payments/orders/{order_id}",
            headers=headers,
        )

        response.raise_for_status()
        return response.json()

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching GHL order {order_id}: {e}")
        return None
    except (ValueError, KeyError) as e:
        logger.error(f"Error parsing GHL order response for {order_id}: {e}")
        return None


def sync_credit_points_to_ghl(contact_id, service_name, points):
    """
    Sync credit points for a specific service by updating all credit points.
    
    Args:
        contact_id (str): GHL contact ID
        service_name (str): Service name (e.g., 'IV Ozone Therapy')
        points (int): Current points balance
        
    Returns:
        bool: True if successful, False otherwise
    """
    # Instead of syncing just one service, sync all services for this contact
    return sync_all_credit_points_to_ghl(contact_id)
