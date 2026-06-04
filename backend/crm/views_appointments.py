"""
Views for handling GHL appointments data
"""
import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)

def get_ghl_db_connection():
    """
    Create a connection to the GHL database using environment variables
    """
    try:
        from core.secrets import get_ghl_db_config
        ghl_cfg = get_ghl_db_config()
        connection = psycopg2.connect(
            host=ghl_cfg['host'],
            port=ghl_cfg['port'],
            database=ghl_cfg['database'],
            user=ghl_cfg['user'],
            password=ghl_cfg['password']
        )
        return connection
    except Exception as e:
        logger.error(f"Failed to connect to GHL database: {e}")
        raise

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_appointments(request, customer_id):
    """
    Fetch appointments for a customer from the GHL database
    
    Args:
        customer_id: UUID of the customer in the main CRM database
    
    Returns:
        JSON response with appointments data
    """
    try:
        # First, get the customer's email from the main database
        from .models import Contact
        
        try:
            contact = Contact.objects.get(id=customer_id)
            customer_email = contact.email
            
            if not customer_email:
                return Response({
                    'status': 'success',
                    'data': [],
                    'message': 'No email found for this customer'
                }, status=status.HTTP_200_OK)
                
        except Contact.DoesNotExist:
            return Response({
                'status': 'error',
                'message': 'Customer not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Connect to GHL database and fetch appointments
        ghl_conn = get_ghl_db_connection()
        
        try:
            with ghl_conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Query to get appointments for the contact using email
                query = """
                    SELECT 
                        a.id,
                        a.external_id,
                        a.title,
                        a.start_time,
                        a.end_time,
                        a.status,
                        a.notes,
                        a.location,
                        a.provider,
                        a.service,
                        a.source,
                        a.created_at,
                        a.updated_at,
                        c.first_name,
                        c.last_name,
                        c.email
                    FROM crm_appointment a
                    INNER JOIN crm_contact c ON a.contact_id = c.id
                    WHERE c.email = %s
                    ORDER BY a.start_time DESC
                """
                
                cursor.execute(query, (customer_email,))
                appointments = cursor.fetchall()
                
                # Convert to list of dictionaries for JSON serialization
                appointments_list = []
                for appointment in appointments:
                    appointment_dict = dict(appointment)
                    
                    # Format datetime fields for frontend
                    if appointment_dict.get('start_time'):
                        appointment_dict['start_time'] = appointment_dict['start_time'].isoformat()
                    if appointment_dict.get('end_time'):
                        appointment_dict['end_time'] = appointment_dict['end_time'].isoformat()
                    if appointment_dict.get('created_at'):
                        appointment_dict['created_at'] = appointment_dict['created_at'].isoformat()
                    if appointment_dict.get('updated_at'):
                        appointment_dict['updated_at'] = appointment_dict['updated_at'].isoformat()
                    
                    appointments_list.append(appointment_dict)
                
                logger.info(f"Found {len(appointments_list)} appointments for email: {customer_email}")
                
                return Response({
                    'status': 'success',
                    'data': appointments_list,
                    'customer_id': customer_id,
                    'customer_email': customer_email,
                    'count': len(appointments_list)
                }, status=status.HTTP_200_OK)
                
        finally:
            ghl_conn.close()
            
    except Exception as e:
        logger.error(f"Error fetching appointments for customer {customer_id}: {e}")
        return Response({
            'status': 'error',
            'message': f'Failed to fetch appointments: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_appointment_detail(request, appointment_id):
    """
    Fetch detailed information for a specific appointment
    
    Args:
        appointment_id: ID of the appointment in the GHL database
    
    Returns:
        JSON response with appointment details
    """
    try:
        # Connect to GHL database and fetch appointment details
        ghl_conn = get_ghl_db_connection()
        
        try:
            with ghl_conn.cursor(cursor_factory=RealDictCursor) as cursor:
                query = """
                    SELECT 
                        a.*,
                        c.first_name,
                        c.last_name,
                        c.email,
                        c.phone
                    FROM crm_appointment a
                    INNER JOIN crm_contact c ON a.contact_id = c.id
                    WHERE a.id = %s
                """
                
                cursor.execute(query, (appointment_id,))
                appointment = cursor.fetchone()
                
                if not appointment:
                    return Response({
                        'status': 'error',
                        'message': 'Appointment not found'
                    }, status=status.HTTP_404_NOT_FOUND)
                
                # Convert to dictionary and format datetime fields
                appointment_dict = dict(appointment)
                
                if appointment_dict.get('start_time'):
                    appointment_dict['start_time'] = appointment_dict['start_time'].isoformat()
                if appointment_dict.get('end_time'):
                    appointment_dict['end_time'] = appointment_dict['end_time'].isoformat()
                if appointment_dict.get('created_at'):
                    appointment_dict['created_at'] = appointment_dict['created_at'].isoformat()
                if appointment_dict.get('updated_at'):
                    appointment_dict['updated_at'] = appointment_dict['updated_at'].isoformat()
                
                return Response({
                    'status': 'success',
                    'data': appointment_dict
                }, status=status.HTTP_200_OK)
                
        finally:
            ghl_conn.close()
            
    except Exception as e:
        logger.error(f"Error fetching appointment {appointment_id}: {e}")
        return Response({
            'status': 'error',
            'message': f'Failed to fetch appointment: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
