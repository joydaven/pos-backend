from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone

from .models import WooCreditedService, Contact, Product, CreditServicePoints
from .serializers_credited_services import (
    WooCreditedServiceSerializer,
    CreditedServiceResponseSerializer,
    ContactInfoSerializer,
    CreditWebhookSerializer
)
from .ghl_api import get_ghl_contact_id_by_email

import logging
logger = logging.getLogger(__name__)


@api_view(['POST'])

@permission_classes([IsAuthenticated])
def credit_webhook(request):
    """
    API endpoint to add or subtract credits for a product and contact.
    
    POST /api/store-manager/credits/webhook
    
    Request body:
    {
        "contact_woo_id": "123456",
        "product_id": "3024",
        "action": "add" or "subtract",
        "points": 1,
        "contact_info": {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "phone": "+1234567890"
        }
    }
    """
    serializer = CreditWebhookSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response({
            'status': 'error',
            'message': 'Invalid request data',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    contact_woo_id = data['contact_woo_id']
    product_id = data['product_id']
    action = data['action']
    points = data['points']
    contact_info = data.get('contact_info', {})
    
    try:
        # Try to get the contact from the database
        contact = Contact.objects.filter(woo_customer_id=contact_woo_id).first()
        
        # Try to get the product from the database
        product = Product.objects.filter(woo_product_id=product_id).first()
        
        # Get the GHL contact ID if available
        ghl_contact_id = None
        if contact and contact.ghl_contact_id:
            # If we have a contact with GHL ID, use it
            ghl_contact_id = contact.ghl_contact_id
            logger.info(f"Found GHL contact ID {ghl_contact_id} for WooCommerce customer {contact_woo_id}")
        else:
            # If no contact or no GHL ID, try to find by email
            contact_email = contact.email if contact else contact_info.get('email')
            if contact_email:
                # Try to find a contact with matching email that has a GHL ID
                ghl_contact = Contact.objects.filter(
                    email=contact_email, 
                    ghl_contact_id__isnull=False
                ).first()
                
                if ghl_contact:
                    ghl_contact_id = ghl_contact.ghl_contact_id
                    logger.info(f"Found GHL contact ID {ghl_contact_id} by email {contact_email} in local database")
                else:
                    # If not found in local database, try to fetch from GHL API directly
                    logger.info(f"No GHL contact found in local database for email {contact_email}, trying GHL API...")
                    ghl_contact_id = get_ghl_contact_id_by_email(contact_email)
                    if ghl_contact_id:
                        logger.info(f"Found GHL contact ID {ghl_contact_id} for email {contact_email} via GHL API")
                        
                        # Update the Contact record if it exists but doesn't have a GHL ID
                        if contact and not contact.ghl_contact_id:
                            logger.info(f"Updating Contact record with GHL contact ID {ghl_contact_id}")
                            contact.ghl_contact_id = ghl_contact_id
                            contact.save(update_fields=['ghl_contact_id'])
                    else:
                        logger.info(f"No GHL contact found via API for email {contact_email}")
            else:
                logger.info(f"No email available to search for GHL contact")
        
        # Check if product is eligible for new credit service points system
        if product and CreditServicePoints.is_variable_product_with_series(product):
            logger.info(f"Product {product_id} is variable with series - checking specific eligibility")
            
            # For variable products, we need to check if the specific series is eligible
            # Since this is a webhook without variation info, we'll allow it but log a warning
            logger.warning(f"Credit webhook for variable product {product.name} without variation data - allowing credit but consider using variation-specific endpoint")
            
            # Use new CreditServicePoints system for variable products with series
            credit_points, created = CreditServicePoints.objects.get_or_create(
                customer=contact,
                product=product,
                defaults={
                    'contact_ghl_id': ghl_contact_id or '',
                    'contact_woo_id': contact_woo_id,
                    'points': 0
                }
            )
            
            # Add or subtract points
            with transaction.atomic():
                if action == 'add':
                    credit_points.points += points
                    message = f"Added {points} points to new credit system"
                else:  # subtract
                    if credit_points.points < points:
                        return Response({
                            'status': 'error',
                            'message': f'Insufficient points. Available: {credit_points.points}, Requested: {points}'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    credit_points.points -= points
                    message = f"Subtracted {points} points from new credit system"
                
                credit_points.save()
            
            return Response({
                'status': 'success',
                'message': message,
                'product_id': product_id,
                'product_name': product.name,
                'available_points': credit_points.points,
                'action': action,
                'points_changed': points,
                'system': 'new_credit_service_points'
            })
        
        else:
            logger.info(f"Product {product_id} is not variable with series - using legacy WooCreditedService system")
            
            # Use legacy system for non-variable products or products without series
            credited_service, created = WooCreditedService.objects.get_or_create(
                contact_woo_id=contact_woo_id,
                product_id=product_id,
                defaults={
                    'contact_fname': contact.first_name if contact else contact_info.get('first_name', ''),
                    'contact_lname': contact.last_name if contact else contact_info.get('last_name', ''),
                    'contact_email': contact.email if contact else contact_info.get('email', ''),
                    'contact_phone': contact.phone if contact else contact_info.get('phone', ''),
                    'contact_ghl_id': ghl_contact_id,  # Use the GHL ID we found
                    'product_name': product.name if product else '',
                    'product_points': 0
                }
            )
            
            # Update contact info if provided
            if contact_info:
                credited_service.contact_fname = contact_info.get('first_name', credited_service.contact_fname)
                credited_service.contact_lname = contact_info.get('last_name', credited_service.contact_lname)
                credited_service.contact_email = contact_info.get('email', credited_service.contact_email)
                credited_service.contact_phone = contact_info.get('phone', credited_service.contact_phone)
            
            # Always try to update the GHL contact ID if it's null
            if not credited_service.contact_ghl_id and ghl_contact_id:
                credited_service.contact_ghl_id = ghl_contact_id
                logger.info(f"Updated GHL contact ID to {ghl_contact_id} for credited service")
            
            # Update product name if available
            if product:
                credited_service.product_name = product.name
            
            # Add or subtract points
            with transaction.atomic():
                if action == 'add':
                    credited_service.product_points += points
                    message = f"Added {points} credits to legacy system"
                else:  # subtract
                    if credited_service.product_points < points:
                        return Response({
                            'status': 'error',
                            'message': f'Insufficient credits. Available: {credited_service.product_points}, Requested: {points}'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    credited_service.product_points -= points
                    message = f"Subtracted {points} credits from legacy system"
                
                credited_service.updated_at = timezone.now()
                credited_service.save()
        
            return Response({
                'status': 'success',
                'message': message,
                'product_id': product_id,
                'product_name': credited_service.product_name,
                'available_credits': credited_service.product_points,
                'action': action,
                'points_changed': points,
                'system': 'legacy_woo_credited_service'
            })
        
    except Exception as e:
        logger.error(f"Error in credit webhook: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])

@permission_classes([IsAuthenticated])
def get_product_credits(request, woo_commerce_contact_id, product_id):
    """
    API endpoint to get available credits for a specific product and contact.
    
    GET /api/store-manager/{woo_commerce_contact_id}/{product_id}
    """
    try:
        # Get the contact
        contact = get_object_or_404(Contact, woo_customer_id=woo_commerce_contact_id)
        
        # Get the credited service
        credited_service = WooCreditedService.objects.filter(
            contact_woo_id=woo_commerce_contact_id,
            product_id=product_id
        ).first()
        
        if not credited_service:
            # If no credited service found, return 0 credits
            product = Product.objects.filter(woo_product_id=product_id).first()
            product_name = product.name if product else f"Product {product_id}"
            
            return Response({
                'status': 'success',
                'product_id': product_id,
                'product_name': product_name,
                'available_credits': 0,
                'contact': ContactInfoSerializer(contact).data
            })
        
        # Return the credited service information
        return Response({
            'status': 'success',
            'product_id': credited_service.product_id,
            'product_name': credited_service.product_name,
            'available_credits': credited_service.product_points,
            'contact': ContactInfoSerializer(contact).data
        })
        
    except Exception as e:
        logger.error(f"Error getting product credits: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])

@permission_classes([IsAuthenticated])
def get_all_product_credits(request, woo_commerce_contact_id):
    """
    API endpoint to get all available credits for all products for a contact.
    
    GET /api/store-manager/{woo_commerce_contact_id}
    """
    try:
        # Get the contact
        contact = get_object_or_404(Contact, woo_customer_id=woo_commerce_contact_id)
        
        # Get all credited services for the contact
        credited_services = WooCreditedService.objects.filter(
            contact_woo_id=woo_commerce_contact_id
        )
        
        # Serialize the credited services
        serialized_services = CreditedServiceResponseSerializer(credited_services, many=True).data
        
        return Response({
            'status': 'success',
            'creditedServices': serialized_services,
            'contact': ContactInfoSerializer(contact).data
        })
        
    except Exception as e:
        logger.error(f"Error getting all product credits: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])

@permission_classes([IsAuthenticated])
def credit_webhook_with_variation(request):
    """
    Enhanced API endpoint to add or subtract credits for a product and contact with variation checking.
    
    POST /api/store-manager/credits/webhook-with-variation
    
    Request body:
    {
        "contact_woo_id": "123456",
        "product_id": "3024",
        "action": "add" or "subtract",
        "points": 1,
        "variation_data": {
            "attributes": [
                {
                    "name": "Series",
                    "value": "6"
                }
            ]
        },
        "contact_info": {
            "first_name": "John",
            "last_name": "Doe",
            "email": "john@example.com",
            "phone": "+1234567890"
        }
    }
    """
    serializer = CreditWebhookSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response({
            'status': 'error',
            'message': 'Invalid request data',
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)
    
    data = serializer.validated_data
    contact_woo_id = data['contact_woo_id']
    product_id = data['product_id']
    action = data['action']
    points = data['points']
    contact_info = data.get('contact_info', {})
    variation_data = request.data.get('variation_data')  # Get from raw request data
    
    try:
        # Try to get the contact from the database
        contact = Contact.objects.filter(woo_customer_id=contact_woo_id).first()
        
        # Try to get the product from the database
        product = Product.objects.filter(woo_product_id=product_id).first()
        
        if not product:
            return Response({
                'status': 'error',
                'message': f'Product with ID {product_id} not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Check if this is a variable product with series
        if CreditServicePoints.is_variable_product_with_series(product):
            logger.info(f"Product {product_id} is variable with series - checking variation eligibility")
            
            # Check variation eligibility if variation_data is provided
            if variation_data:
                eligibility_result = CreditServicePoints.check_variation_eligibility_for_credits(product, variation_data)
                
                if not eligibility_result['eligible']:
                    return Response({
                        'status': 'error',
                        'message': f'Variation not eligible for credits: {eligibility_result["reason"]}',
                        'eligibility_check': eligibility_result
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                logger.info(f"Variation eligibility check passed: {eligibility_result['reason']}")
            else:
                logger.warning(f"No variation data provided for variable product {product.name} - proceeding without eligibility check")
        
        # Get the GHL contact ID if available (same logic as original webhook)
        ghl_contact_id = None
        if contact and contact.ghl_contact_id:
            ghl_contact_id = contact.ghl_contact_id
            logger.info(f"Found GHL contact ID {ghl_contact_id} for WooCommerce customer {contact_woo_id}")
        else:
            # Try to find by email
            contact_email = contact.email if contact else contact_info.get('email')
            if contact_email:
                ghl_contact = Contact.objects.filter(
                    email=contact_email, 
                    ghl_contact_id__isnull=False
                ).first()
                
                if ghl_contact:
                    ghl_contact_id = ghl_contact.ghl_contact_id
                    logger.info(f"Found GHL contact ID {ghl_contact_id} by email {contact_email} in local database")
                else:
                    # Try GHL API
                    ghl_contact_id = get_ghl_contact_id_by_email(contact_email)
                    if ghl_contact_id:
                        logger.info(f"Found GHL contact ID {ghl_contact_id} for email {contact_email} via GHL API")
                        if contact and not contact.ghl_contact_id:
                            contact.ghl_contact_id = ghl_contact_id
                            contact.save(update_fields=['ghl_contact_id'])
        
        # Use new CreditServicePoints system for variable products with series
        if product and CreditServicePoints.is_variable_product_with_series(product):
            credit_points, created = CreditServicePoints.objects.get_or_create(
                customer=contact,
                product=product,
                defaults={
                    'contact_ghl_id': ghl_contact_id or '',
                    'contact_woo_id': contact_woo_id,
                    'points': 0
                }
            )
            
            # Add or subtract points
            with transaction.atomic():
                if action == 'add':
                    credit_points.points += points
                    message = f"Added {points} points to new credit system"
                else:  # subtract
                    if credit_points.points < points:
                        return Response({
                            'status': 'error',
                            'message': f'Insufficient points. Available: {credit_points.points}, Requested: {points}'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    credit_points.points -= points
                    message = f"Subtracted {points} points from new credit system"
                
                credit_points.save()
            
            response_data = {
                'status': 'success',
                'message': message,
                'product_id': product_id,
                'product_name': product.name,
                'available_points': credit_points.points,
                'action': action,
                'points_changed': points,
                'system': 'new_credit_service_points_with_variation_check'
            }
            
            # Add eligibility info if variation was checked
            if variation_data and 'eligibility_result' in locals():
                response_data['eligibility_check'] = eligibility_result
            
            return Response(response_data)
        
        else:
            # Use legacy system for non-variable products
            logger.info(f"Product {product_id} is not variable with series - using legacy WooCreditedService system")
            
            credited_service, created = WooCreditedService.objects.get_or_create(
                contact_woo_id=contact_woo_id,
                product_id=product_id,
                defaults={
                    'contact_fname': contact.first_name if contact else contact_info.get('first_name', ''),
                    'contact_lname': contact.last_name if contact else contact_info.get('last_name', ''),
                    'contact_email': contact.email if contact else contact_info.get('email', ''),
                    'contact_phone': contact.phone if contact else contact_info.get('phone', ''),
                    'contact_ghl_id': ghl_contact_id,
                    'product_name': product.name if product else '',
                    'product_points': 0
                }
            )
            
            # Update contact info if provided
            if contact_info:
                credited_service.contact_fname = contact_info.get('first_name', credited_service.contact_fname)
                credited_service.contact_lname = contact_info.get('last_name', credited_service.contact_lname)
                credited_service.contact_email = contact_info.get('email', credited_service.contact_email)
                credited_service.contact_phone = contact_info.get('phone', credited_service.contact_phone)
            
            if not credited_service.contact_ghl_id and ghl_contact_id:
                credited_service.contact_ghl_id = ghl_contact_id
            
            if product:
                credited_service.product_name = product.name
            
            # Add or subtract points
            with transaction.atomic():
                if action == 'add':
                    credited_service.product_points += points
                    message = f"Added {points} credits to legacy system"
                else:  # subtract
                    if credited_service.product_points < points:
                        return Response({
                            'status': 'error',
                            'message': f'Insufficient credits. Available: {credited_service.product_points}, Requested: {points}'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    credited_service.product_points -= points
                    message = f"Subtracted {points} credits from legacy system"
                
                credited_service.updated_at = timezone.now()
                credited_service.save()
        
            return Response({
                'status': 'success',
                'message': message,
                'product_id': product_id,
                'product_name': credited_service.product_name,
                'available_credits': credited_service.product_points,
                'action': action,
                'points_changed': points,
                'system': 'legacy_woo_credited_service'
            })
        
    except Exception as e:
        logger.error(f"Error in enhanced credit webhook: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
