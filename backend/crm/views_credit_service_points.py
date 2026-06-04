from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone

from .models import CreditServicePoints, Contact, Product, WooServiceTypes, WooCreditLog, WooCreditedService
from .serializers import ContactSerializer, ProductSerializer

import logging
logger = logging.getLogger(__name__)


class CreditServicePointsSerializer:
    """Simple serializer for CreditServicePoints"""
    
    @staticmethod
    def serialize(credit_points):
        return {
            'id': str(credit_points.id),
            'customer_id': str(credit_points.customer.id),
            'product_id': str(credit_points.product.id),
            'customer_name': f"{credit_points.customer.first_name} {credit_points.customer.last_name}",
            'product_name': credit_points.product.name,
            'points': credit_points.points,
            'contact_ghl_id': credit_points.contact_ghl_id,
            'contact_woo_id': credit_points.contact_woo_id,
            'created_at': credit_points.created_at.isoformat() if credit_points.created_at else None,
            'updated_at': credit_points.updated_at.isoformat() if credit_points.updated_at else None,
        }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def process_order_completion_webhook(request):
    """
    Webhook endpoint for processing order completion with new credit service points logic.
    
    POST /api/credit-service-points/order-completion/
    
    Request body:
    {
        "order_id": "uuid-string",
        "customer_id": "uuid-string", 
        "items": [
            {
                "product_id": "uuid-string",
                "quantity": 1,
                "series_count": 12,  // Number of series purchased
                "product_type": "variable"
            }
        ]
    }
    """
    try:
        order_data = request.data
        logger.info(f"Processing order completion webhook: {order_data}")
        
        # Validate required fields
        if not order_data.get('customer_id'):
            return Response({
                'status': 'error',
                'message': 'customer_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not order_data.get('items'):
            return Response({
                'status': 'error', 
                'message': 'items array is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Process the order using the model method
        results = CreditServicePoints.process_order_completion(order_data)
        
        return Response({
            'status': 'success',
            'message': 'Order processed successfully',
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Error in order completion webhook: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_credit_points(request, customer_id):
    """
    Get all credit service points for a specific customer.
    
    GET /api/credit-service-points/customer/{customer_id}/
    """
    try:
        # Get the customer
        customer = get_object_or_404(Contact, id=customer_id)
        
        # Check if CreditServicePoints table exists
        try:
            # Get all credit points for the customer
            credit_points = CreditServicePoints.objects.filter(customer=customer).select_related('product')
            
            # Serialize the data
            serialized_points = [CreditServicePointsSerializer.serialize(cp) for cp in credit_points]
            
            return Response({
                'status': 'success',
                'customer': {
                    'id': str(customer.id),
                    'name': f"{customer.first_name} {customer.last_name}",
                    'email': customer.email,
                    'woo_customer_id': customer.woo_customer_id,
                    'ghl_contact_id': customer.ghl_contact_id
                },
                'data': serialized_points,
                'total_products_with_points': len(serialized_points)
            })
            
        except Exception as model_error:
            # If the table doesn't exist yet, return empty data
            logger.warning(f"CreditServicePoints table may not exist yet: {str(model_error)}")
            return Response({
                'status': 'success',
                'customer': {
                    'id': str(customer.id),
                    'name': f"{customer.first_name} {customer.last_name}",
                    'email': customer.email,
                    'woo_customer_id': customer.woo_customer_id,
                    'ghl_contact_id': customer.ghl_contact_id
                },
                'data': [],
                'total_products_with_points': 0,
                'message': 'Credit service points table not migrated yet. Please run migration.'
            })
        
    except Exception as e:
        logger.error(f"Error getting customer credit points: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_credit_points(request, customer_id, product_id):
    """
    Get credit service points for a specific customer and product.
    
    GET /api/credit-service-points/customer/{customer_id}/product/{product_id}/
    """
    try:
        # Get the customer and product
        customer = get_object_or_404(Contact, id=customer_id)
        product = get_object_or_404(Product, id=product_id)
        
        # Check if product is eligible for credit points
        if not CreditServicePoints.is_variable_product_with_series(product):
            return Response({
                'status': 'success',
                'message': 'Product is not eligible for credit service points',
                'customer': {
                    'id': str(customer.id),
                    'name': f"{customer.first_name} {customer.last_name}"
                },
                'product': {
                    'id': str(product.id),
                    'name': product.name,
                    'product_type': product.product_type
                },
                'points': 0,
                'eligible': False
            })
        
        # Get credit points for the customer and product
        credit_points = CreditServicePoints.objects.filter(
            customer=customer,
            product=product
        ).first()
        
        points_count = credit_points.points if credit_points else 0
        
        return Response({
            'status': 'success',
            'customer': {
                'id': str(customer.id),
                'name': f"{customer.first_name} {customer.last_name}"
            },
            'product': {
                'id': str(product.id),
                'name': product.name,
                'product_type': product.product_type
            },
            'points': points_count,
            'eligible': True,
            'credit_record': CreditServicePointsSerializer.serialize(credit_points) if credit_points else None
        })
        
    except Exception as e:
        logger.error(f"Error getting product credit points: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def manual_points_adjustment(request):
    """
    Manually adjust credit service points for a customer and product.
    
    POST /api/credit-service-points/adjust/
    
    Request body:
    {
        "customer_id": "uuid-string",
        "product_id": "uuid-string",
        "action": "add" or "subtract",
        "points": 5,
        "reason": "Manual adjustment reason",
        "source": "POS" or "MANUAL" (optional, defaults to "MANUAL")
    }
    """
    try:
        data = request.data
        
        # Validate required fields
        required_fields = ['customer_id', 'product_id', 'action', 'points']
        for field in required_fields:
            if not data.get(field):
                return Response({
                    'status': 'error',
                    'message': f'{field} is required'
                }, status=status.HTTP_400_BAD_REQUEST)
        
        customer_id = data['customer_id']
        product_id = data['product_id']
        action = data['action']
        points = int(data['points'])
        reason = data.get('reason', 'Manual adjustment')
        source = data.get('source', 'MANUAL').upper()  # Default to MANUAL, frontend can override to POS
        
        # Validate source
        if source not in ['POS', 'MANUAL', 'WEB']:
            source = 'MANUAL'
        
        if action not in ['add', 'subtract']:
            return Response({
                'status': 'error',
                'message': 'action must be "add" or "subtract"'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if points <= 0:
            return Response({
                'status': 'error',
                'message': 'points must be greater than 0'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get customer and product
        customer = get_object_or_404(Contact, id=customer_id)
        product = get_object_or_404(Product, id=product_id)
        
        # Check if product is eligible
        if not CreditServicePoints.is_variable_product_with_series(product):
            return Response({
                'status': 'error',
                'message': 'Product is not eligible for credit service points'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get or create credit points record
        with transaction.atomic():
            credit_points, created = CreditServicePoints.objects.get_or_create(
                customer=customer,
                product=product,
                defaults={
                    'contact_ghl_id': customer.ghl_contact_id,
                    'contact_woo_id': customer.woo_customer_id,
                    'points': 0
                }
            )
            
            old_points = credit_points.points
            
            # Use proper action names based on source
            # POS source: "add" / "subtract" (these are POS operations, not manual)
            # MANUAL source: "manual_add" / "manual_subtract"
            if action == 'add':
                credit_points.points += points
                message = f"Added {points} points"
                points_change = points
                log_action = "add" if source == "POS" else "manual_add"
            else:  # subtract
                if credit_points.points < points:
                    return Response({
                        'status': 'error',
                        'message': f'Insufficient points. Available: {credit_points.points}, Requested: {points}'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                credit_points.points -= points
                message = f"Subtracted {points} points"
                points_change = -points
                log_action = "subtract" if source == "POS" else "manual_subtract"
            
            # Use proper order number based on source
            order_number = data.get('order_number', 'MANUAL' if source == 'MANUAL' else 'POS')
            
            credit_points.save()
            
            # Log the adjustment with correct source
            credit_points.log_credit_change(
                action=log_action,
                points_change=points_change,
                order_number=order_number,
                reason=reason,
                notes=f"{reason} | {source} adjustment | Previous: {old_points} | New: {credit_points.points}",
                source=source
            )
            
            logger.info(f"Manual points adjustment: {customer.email} - {product.name} - {action} {points} points - Reason: {reason}")
        
        # Fetch updated transaction history to return to frontend
        try:
            contact_woo_id = str(customer.woo_customer_id) if customer.woo_customer_id else str(customer.id)
            credited_services = WooCreditedService.objects.filter(contact_woo_id=contact_woo_id)
            service_ids = [cs.id for cs in credited_services]
            
            # Get latest transaction history
            recent_logs = WooCreditLog.objects.filter(
                woo_credited_service_id__in=service_ids
            ).order_by('-created_at')[:10]  # Get last 10 transactions
            
            transaction_history = []
            for log in recent_logs:
                cs = next((s for s in credited_services if s.id == log.woo_credited_service_id), None)
                # Use stored source field if available, fallback to derivation for legacy records
                if hasattr(log, 'source') and log.source:
                    log_source = log.source
                else:
                    log_source = "MANUAL" if (log.order_number == "MANUAL" or "manual" in log.action.lower()) else "POS"
                transaction_history.append({
                    'id': log.id,
                    'product_name': cs.product_name if cs else 'Unknown',
                    'action': log.action,
                    'order_number': log.order_number,
                    'points_change': log.points_change,
                    'updated_points': log.updated_points,
                    'reason': log.reason,
                    'notes': log.notes,
                    'source': log_source,
                    'date': log.created_at.isoformat() if log.created_at else None
                })
        except Exception as e:
            logger.warning(f"Failed to fetch transaction history: {str(e)}")
            transaction_history = []
        
        return Response({
            'status': 'success',
            'message': message,
            'customer': {
                'id': str(customer.id),
                'name': f"{customer.first_name} {customer.last_name}"
            },
            'product': {
                'id': str(product.id),
                'name': product.name
            },
            'old_points': old_points,
            'new_points': credit_points.points,
            'points_changed': points,
            'action': action,
            'reason': reason,
            'transaction_history': transaction_history,  # Include updated history
            'should_refresh': True  # Signal frontend to refresh
        })
        
    except Exception as e:
        logger.error(f"Error in manual points adjustment: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_product_eligibility(request, product_id):
    """
    Check if a product is eligible for credit service points.
    
    GET /api/credit-service-points/product/{product_id}/eligibility/
    """
    try:
        product = get_object_or_404(Product, id=product_id)
        
        is_eligible = CreditServicePoints.is_variable_product_with_series(product)
        
        return Response({
            'status': 'success',
            'product': {
                'id': str(product.id),
                'name': product.name,
                'product_type': product.product_type
            },
            'eligible': is_eligible,
            'reason': 'Variable product with series' if is_eligible else 'Not a variable product or no series'
        })
        
    except Exception as e:
        logger.error(f"Error checking product eligibility: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def check_variation_credit_eligibility(request):
    """
    Check if a specific product variation is eligible for credit services based on woo_service_types.
    
    POST /api/credit-service-points/check-variation-eligibility/
    
    Request body:
    {
        "product_id": "uuid-string",
        "variation_data": {
            "attributes": [
                {
                    "name": "Series",
                    "value": "6"
                }
            ]
        }
    }
    
    OR:
    
    {
        "product_name": "IV Ozone Therapy",
        "series_value": 6
    }
    """
    try:
        data = request.data
        
        # Method 1: Using product_id and variation_data
        if 'product_id' in data and 'variation_data' in data:
            product_id = data['product_id']
            variation_data = data['variation_data']
            
            product = get_object_or_404(Product, id=product_id)
            eligibility_result = CreditServicePoints.check_variation_eligibility_for_credits(product, variation_data)
            
            return Response({
                'status': 'success',
                'product': {
                    'id': str(product.id),
                    'name': product.name,
                    'product_type': product.product_type
                },
                **eligibility_result
            })
        
        # Method 2: Direct product name and series value
        elif 'product_name' in data and 'series_value' in data:
            product_name = data['product_name']
            series_value = int(data['series_value'])
            customer_id = data.get('customer_id')  # Optional customer ID for redemption check
            variation_type = data.get('variation_type', 'earning')  # NEW: redemption or earning
            
            # Get service type info
            from .models import WooServiceTypes
            service_type = WooServiceTypes.objects.filter(
                name__icontains=product_name
            ).first()
            
            if not service_type:
                service_type = WooServiceTypes.objects.filter(
                    name__iexact=product_name
                ).first()
            
            # 🎯 NEW LOGIC: Handle "Single" (redemption) vs "1 series" (earning) differently
            can_earn_credits = False
            can_redeem_credits = False
            customer_has_points = False
            
            if variation_type == 'redemption' and series_value == 1:
                # "Single" variation - REDEMPTION ONLY
                if service_type and customer_id:
                    try:
                        from uuid import UUID
                        customer_uuid = UUID(customer_id)
                        
                        # Check if customer has points
                        credited_services = CreditServicePoints.objects.filter(
                            customer_id=customer_uuid,
                            product__name__icontains=product_name
                        )
                        
                        for credit_service in credited_services:
                            if credit_service.points > 0:
                                can_redeem_credits = True
                                customer_has_points = True
                                break
                        
                        # Fallback: check legacy system
                        if not can_redeem_credits:
                            from .models import WooCreditedService
                            legacy_service = WooCreditedService.objects.filter(
                                contact_woo_id=customer_id,
                                product_name__icontains=product_name
                            ).first()
                            
                            if legacy_service and legacy_service.product_points > 0:
                                can_redeem_credits = True
                                customer_has_points = True
                                
                    except (ValueError, TypeError, Exception):
                        pass
            
            elif variation_type == 'earning' and series_value == 1:
                # "1 series" variation - ALWAYS EARNS (quantity-based)
                can_earn_credits = CreditServicePoints.is_product_series_eligible_for_credits(product_name, series_value)
            
            else:
                # Multi-series (3, 6, 12) - EARNS series_count credits
                can_earn_credits = CreditServicePoints.is_product_series_eligible_for_credits(product_name, series_value)
            
            # Overall eligibility
            is_eligible = can_earn_credits or can_redeem_credits
            
            # Determine the reason
            if variation_type == 'redemption' and can_redeem_credits:
                reason = f'Single series can redeem 1 credit on "{product_name}"'
            elif variation_type == 'redemption' and not can_redeem_credits:
                reason = f'Single series - no credits available for redemption on "{product_name}"'
            elif variation_type == 'earning' and series_value == 1 and can_earn_credits:
                reason = f'1 series can earn credits (quantity-based) on "{product_name}"'
            elif can_earn_credits:
                reason = f'{series_value} series can earn {series_value} credits on "{product_name}"'
            else:
                reason = f'Not eligible for credits on "{product_name}"'
            
            return Response({
                'status': 'success',
                'eligible': is_eligible,
                'can_earn_credits': can_earn_credits,
                'can_redeem_credits': can_redeem_credits,
                'customer_has_points': customer_has_points,
                'series_value': series_value,
                'variation_type': variation_type,
                'service_name': service_type.name if service_type else product_name,
                'available_series': service_type.series if service_type else [],
                'reason': reason
            })
        
        else:
            return Response({
                'status': 'error',
                'message': 'Either (product_id + variation_data) or (product_name + series_value) is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        logger.error(f"Error checking variation credit eligibility: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_service_types(request):
    """
    Get all available service types from woo_service_types table.
    
    GET /api/credit-service-points/service-types/
    """
    try:
        from .models import WooServiceTypes
        
        service_types = WooServiceTypes.objects.all().order_by('name')
        
        serialized_types = []
        for service_type in service_types:
            serialized_types.append({
                'id': service_type.id,
                'name': service_type.name,
                'default_index': service_type.default_index,
                'series': service_type.series,
                'created_at': service_type.created_at.isoformat() if service_type.created_at else None,
                'updated_at': service_type.updated_at.isoformat() if service_type.updated_at else None
            })
        
        return Response({
            'status': 'success',
            'service_types': serialized_types,
            'total_count': len(serialized_types)
        })
        
    except Exception as e:
        logger.error(f"Error getting service types: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_service_type(request):
    """
    Create a new service type.
    
    POST /api/credit-service-points/service-types/
    
    Request body:
    {
        "name": "IV Therapy: Custom Treatment",
        "default_index": 0,
        "series": [3, 6, 12]
    }
    """
    try:
        from .models import WooServiceTypes
        
        data = request.data
        
        # Validate required fields
        if not data.get('name'):
            return Response({
                'status': 'error',
                'message': 'name is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if service type with this name already exists
        if WooServiceTypes.objects.filter(name=data['name']).exists():
            return Response({
                'status': 'error',
                'message': f'Service type with name "{data["name"]}" already exists'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create new service type
        service_type = WooServiceTypes.objects.create(
            name=data['name'],
            default_index=data.get('default_index', 0),
            series=data.get('series', [])
        )
        
        logger.info(f"Created new service type: {service_type.name}")
        
        return Response({
            'status': 'success',
            'message': 'Service type created successfully',
            'service_type': {
                'id': service_type.id,
                'name': service_type.name,
                'default_index': service_type.default_index,
                'series': service_type.series,
                'created_at': service_type.created_at.isoformat() if service_type.created_at else None,
                'updated_at': service_type.updated_at.isoformat() if service_type.updated_at else None
            }
        })
        
    except Exception as e:
        logger.error(f"Error creating service type: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_service_type(request, service_type_id):
    """
    Update an existing service type.
    
    PUT /api/credit-service-points/service-types/{id}/
    
    Request body:
    {
        "name": "Updated Service Name",
        "default_index": 1,
        "series": [2, 4, 6, 12]
    }
    """
    try:
        from .models import WooServiceTypes
        
        # Get the service type
        try:
            service_type = WooServiceTypes.objects.get(id=service_type_id)
        except WooServiceTypes.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Service type with ID {service_type_id} not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        data = request.data
        
        # Validate required fields
        if not data.get('name'):
            return Response({
                'status': 'error',
                'message': 'name is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if another service type with this name already exists
        existing = WooServiceTypes.objects.filter(name=data['name']).exclude(id=service_type_id)
        if existing.exists():
            return Response({
                'status': 'error',
                'message': f'Another service type with name "{data["name"]}" already exists'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Update the service type
        old_name = service_type.name
        service_type.name = data['name']
        service_type.default_index = data.get('default_index', service_type.default_index)
        service_type.series = data.get('series', service_type.series)
        service_type.save()
        
        logger.info(f"Updated service type: {old_name} -> {service_type.name}")
        
        return Response({
            'status': 'success',
            'message': 'Service type updated successfully',
            'service_type': {
                'id': service_type.id,
                'name': service_type.name,
                'default_index': service_type.default_index,
                'series': service_type.series,
                'created_at': service_type.created_at.isoformat() if service_type.created_at else None,
                'updated_at': service_type.updated_at.isoformat() if service_type.updated_at else None
            }
        })
        
    except Exception as e:
        logger.error(f"Error updating service type: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_service_type(request, service_type_id):
    """
    Delete a service type.
    
    DELETE /api/credit-service-points/service-types/{id}/
    """
    try:
        from .models import WooServiceTypes
        
        # Get the service type
        try:
            service_type = WooServiceTypes.objects.get(id=service_type_id)
        except WooServiceTypes.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Service type with ID {service_type_id} not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        service_name = service_type.name
        service_type.delete()
        
        logger.info(f"Deleted service type: {service_name}")
        
        return Response({
            'status': 'success',
            'message': f'Service type "{service_name}" deleted successfully'
        })
        
    except Exception as e:
        logger.error(f"Error deleting service type: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_credit_transaction_history(request, customer_id):
    """
    Get credit transaction history for a customer.
    
    GET /api/credit-service-points/customer/{customer_id}/transaction-history/
    
    Returns all credit log entries for the customer's credited services with source indicators.
    """
    try:
        # Get the customer
        customer = get_object_or_404(Contact, id=customer_id)
        
        # Use woo_customer_id if available, otherwise use customer UUID
        # This ensures we can retrieve logs for customers without WooCommerce IDs
        contact_woo_id = str(customer.woo_customer_id) if customer.woo_customer_id else str(customer.id)
        
        # Get all WooCreditedService records for this customer
        # WooCreditLog.woo_credited_service_id references WooCreditedService.id
        credited_services = WooCreditedService.objects.filter(
            contact_woo_id=contact_woo_id
        )
        
        # Get service IDs and create product map
        service_ids = []
        product_map = {}  # Map woo_credited_service_id to product info
        
        for cs in credited_services:
            service_ids.append(cs.id)
            product_map[cs.id] = {
                'product_id': cs.product_id,
                'product_name': cs.product_name,
                'current_points': cs.product_points
            }
        
        # Get all credit logs for these services
        credit_logs = WooCreditLog.objects.filter(
            woo_credited_service_id__in=service_ids
        ).order_by('-created_at')
        
        # Serialize the logs
        serialized_logs = []
        for log in credit_logs:
            product_info = product_map.get(log.woo_credited_service_id, {})
            
            # Determine source: use stored source field if available, otherwise derive from action/order_number
            # This fallback handles legacy log entries created before the source field was added
            if hasattr(log, 'source') and log.source:
                source = log.source
            else:
                source = "MANUAL" if (log.order_number == "MANUAL" or "manual" in log.action.lower()) else "POS"
            
            # Clean notes - remove "Customer:" prefix if present
            clean_notes = log.notes if log.notes else ''
            if clean_notes and clean_notes.strip().startswith('Customer:'):
                clean_notes = clean_notes.replace('Customer:', '', 1).strip()
            
            serialized_logs.append({
                'id': log.id,
                'product_id': product_info.get('product_id', ''),
                'product_name': product_info.get('product_name', 'Unknown Service'),
                'action': log.action,
                'order_number': log.order_number,
                'points_change': log.points_change,
                'updated_points': log.updated_points,
                'reason': log.reason,
                'notes': clean_notes,
                'source': source,  # POS or MANUAL
                'created_at': log.created_at.isoformat() if log.created_at else None,
                'date': log.created_at.isoformat() if log.created_at else None
            })
        
        return Response({
            'status': 'success',
            'transaction_history': serialized_logs,
            'total_transactions': len(serialized_logs)
        })
        
    except Exception as e:
        logger.error(f"Error getting credit transaction history: {str(e)}")
        return Response({
            'status': 'error',
            'message': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
