"""
WooCommerce Membership Management API Views
Handles membership CRUD operations with WooCommerce integration
"""

import json
import logging
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import Contact
from .woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_memberships(request, customer_id):
    """Get all memberships for a customer from WooCommerce"""
    try:
        # Get customer from database
        customer = Contact.objects.get(id=customer_id)
        
        if not customer.woo_customer_id:
            return Response({
                'error': 'Customer does not have a WooCommerce ID',
                'memberships': []
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get memberships for customer
        memberships = wc_api.get_customer_memberships(customer.woo_customer_id)
        
        return Response({
            'customer_id': customer_id,
            'woo_customer_id': customer.woo_customer_id,
            'memberships': memberships
        })
        
    except Contact.DoesNotExist:
        return Response({
            'error': 'Customer not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error fetching customer memberships: {str(e)}")
        return Response({
            'error': f'Failed to fetch memberships: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_membership_details(request, customer_id, membership_id):
    """Get detailed information for a specific membership"""
    try:
        # Get customer from database
        customer = Contact.objects.get(id=customer_id)
        
        if not customer.woo_customer_id:
            return Response({
                'error': 'Customer does not have a WooCommerce ID'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get membership details
        membership = wc_api.get_membership_details(membership_id)
        
        # Get membership plan details
        plan_details = wc_api.get_membership_plan(membership.get('plan_id')) if membership.get('plan_id') else None
        
        # Get membership notes
        notes = wc_api.get_membership_notes(membership_id)
        
        # Get recent activity/orders related to membership
        recent_activity = wc_api.get_membership_activity(membership_id)
        
        return Response({
            'membership': membership,
            'plan_details': plan_details,
            'notes': notes,
            'recent_activity': recent_activity
        })
        
    except Contact.DoesNotExist:
        return Response({
            'error': 'Customer not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error fetching membership details: {str(e)}")
        return Response({
            'error': f'Failed to fetch membership details: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_membership_status(request, customer_id, membership_id):
    """Update membership status (cancel, pause, resume)"""
    try:
        # Get customer from database
        customer = Contact.objects.get(id=customer_id)
        
        if not customer.woo_customer_id:
            return Response({
                'error': 'Customer does not have a WooCommerce ID'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get action from request
        action = request.data.get('action')  # 'cancel', 'pause', 'resume'
        note = request.data.get('note', '')
        
        if action not in ['cancel', 'pause', 'resume']:
            return Response({
                'error': 'Invalid action. Must be cancel, pause, or resume'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Update membership status
        result = wc_api.update_membership_status(membership_id, action, note)
        
        # Log the action
        logger.info(f"Membership {membership_id} {action}ed for customer {customer_id} by user {request.user.id}")
        
        return Response({
            'success': True,
            'action': action,
            'membership_id': membership_id,
            'result': result
        })
        
    except Contact.DoesNotExist:
        return Response({
            'error': 'Customer not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error updating membership status: {str(e)}")
        return Response({
            'error': f'Failed to update membership status: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_membership_note(request, customer_id, membership_id):
    """Add a note to a membership"""
    try:
        # Get customer from database
        customer = Contact.objects.get(id=customer_id)
        
        if not customer.woo_customer_id:
            return Response({
                'error': 'Customer does not have a WooCommerce ID'
            }, status=status.HTTP_404_NOT_FOUND)
        
        note_content = request.data.get('note', '').strip()
        if not note_content:
            return Response({
                'error': 'Note content is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Add note to membership
        note = wc_api.add_membership_note(membership_id, note_content, request.user.username)
        
        return Response({
            'success': True,
            'note': note
        })
        
    except Contact.DoesNotExist:
        return Response({
            'error': 'Customer not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error adding membership note: {str(e)}")
        return Response({
            'error': f'Failed to add membership note: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_membership_plans(request):
    """Get all available membership plans"""
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get all membership plans
        plans = wc_api.get_membership_plans()
        
        return Response({
            'plans': plans
        })
        
    except Exception as e:
        logger.error(f"Error fetching membership plans: {str(e)}")
        return Response({
            'error': f'Failed to fetch membership plans: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_customer_membership(request, customer_id):
    """Add a new membership plan to a customer"""
    try:
        # Get customer from database
        customer = Contact.objects.get(id=customer_id)
        
        if not customer.woo_customer_id:
            return Response({
                'error': 'Customer does not have a WooCommerce ID'
            }, status=status.HTTP_404_NOT_FOUND)
        
        plan_id = request.data.get('plan_id')
        if not plan_id:
            return Response({
                'error': 'Plan ID is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Add membership to customer
        membership = wc_api.add_customer_membership(customer.woo_customer_id, plan_id)
        
        # Log the action
        logger.info(f"Membership plan {plan_id} added to customer {customer_id} by user {request.user.id}")
        
        return Response({
            'success': True,
            'membership': membership
        })
        
    except Contact.DoesNotExist:
        return Response({
            'error': 'Customer not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error adding customer membership: {str(e)}")
        return Response({
            'error': f'Failed to add membership: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_membership_billing(request, customer_id, membership_id):
    """Update membership billing address"""
    try:
        # Get customer from database
        customer = Contact.objects.get(id=customer_id)
        
        if not customer.woo_customer_id:
            return Response({
                'error': 'Customer does not have a WooCommerce ID'
            }, status=status.HTTP_404_NOT_FOUND)
        
        billing_data = request.data.get('billing', {})
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Update customer billing address in WooCommerce
        result = wc_api.update_customer_billing(customer.woo_customer_id, billing_data)
        
        # Also update local customer record
        if billing_data:
            customer.billing_address = billing_data.get('address_1', customer.billing_address)
            customer.billing_city = billing_data.get('city', customer.billing_city)
            customer.billing_state = billing_data.get('state', customer.billing_state)
            customer.billing_postcode = billing_data.get('postcode', customer.billing_postcode)
            customer.billing_country = billing_data.get('country', customer.billing_country)
            customer.save()
        
        return Response({
            'success': True,
            'result': result
        })
        
    except Contact.DoesNotExist:
        return Response({
            'error': 'Customer not found'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error updating membership billing: {str(e)}")
        return Response({
            'error': f'Failed to update billing address: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
