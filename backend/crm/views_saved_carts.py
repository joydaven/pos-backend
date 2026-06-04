from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404
from .models import SavedCart
from .serializers import SavedCartSerializer
import logging

logger = logging.getLogger(__name__)

class SavedCartViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing saved carts
    """
    serializer_class = SavedCartSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Return all saved carts (shared across all staff)"""
        return SavedCart.objects.all()
    
    def perform_create(self, serializer):
        """Set the user when creating a saved cart"""
        serializer.save(user=self.request.user)
    
    def create(self, request, *args, **kwargs):
        """Create a new saved cart"""
        try:
            logger.info(f"Creating saved cart for user {request.user.username}")
            logger.info(f"Request data: {request.data}")
            
            # Validate required fields
            if not request.data.get('name'):
                return Response(
                    {'error': 'Cart name is required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if not request.data.get('cart_items'):
                return Response(
                    {'error': 'Cart items are required'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Customer is optional for auto-save functionality
            # if not request.data.get('customer_id'):
            #     return Response(
            #         {'error': 'Customer is required to save cart'}, 
            #         status=status.HTTP_400_BAD_REQUEST
            #     )
            
            # Check if cart name already exists (global uniqueness)
            if SavedCart.objects.filter(name=request.data.get('name')).exists():
                return Response(
                    {'error': 'A saved cart with this name already exists'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            
            logger.info(f"Successfully created saved cart: {serializer.data}")
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error creating saved cart: {str(e)}")
            return Response(
                {'error': f'Failed to save cart: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def list(self, request, *args, **kwargs):
        """List all saved carts for the current user"""
        try:
            queryset = self.get_queryset()
            serializer = self.get_serializer(queryset, many=True)
            
            logger.info(f"Retrieved {len(serializer.data)} saved carts for user {request.user.username}")
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Error retrieving saved carts: {str(e)}")
            return Response(
                {'error': f'Failed to retrieve saved carts: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific saved cart"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            
            logger.info(f"Retrieved saved cart {instance.name} for user {request.user.username}")
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Error retrieving saved cart: {str(e)}")
            return Response(
                {'error': f'Failed to retrieve saved cart: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def update(self, request, *args, **kwargs):
        """Update a saved cart"""
        try:
            partial = kwargs.pop('partial', False)
            instance = self.get_object()
            
            # Check if trying to update name to an existing name (global uniqueness)
            new_name = request.data.get('name')
            if new_name and new_name != instance.name:
                if SavedCart.objects.filter(name=new_name).exists():
                    return Response(
                        {'error': 'A saved cart with this name already exists'}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            serializer = self.get_serializer(instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            
            logger.info(f"Updated saved cart {instance.name} for user {request.user.username}")
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Error updating saved cart: {str(e)}")
            return Response(
                {'error': f'Failed to update saved cart: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def destroy(self, request, *args, **kwargs):
        """Delete a saved cart"""
        try:
            instance = self.get_object()
            cart_name = instance.name
            
            self.perform_destroy(instance)
            
            logger.info(f"Deleted saved cart {cart_name} for user {request.user.username}")
            return Response(
                {'message': f'Saved cart "{cart_name}" deleted successfully'}, 
                status=status.HTTP_204_NO_CONTENT
            )
            
        except Exception as e:
            logger.error(f"Error deleting saved cart: {str(e)}")
            return Response(
                {'error': f'Failed to delete saved cart: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def load_cart(self, request, pk=None):
        """Load a saved cart and return its items"""
        try:
            saved_cart = self.get_object()
            
            logger.info(f"Loading saved cart {saved_cart.name} for user {request.user.username}")
            
            # Serialize customer data with proper formatting
            from .serializers import SavedCartCustomerSerializer
            customer_data = SavedCartCustomerSerializer(saved_cart.customer).data if saved_cart.customer else None
            
            response_data = {
                'id': saved_cart.id,
                'name': saved_cart.name,
                'customer': customer_data,
                'cart_items': saved_cart.cart_items,
                'order_discount': saved_cart.order_discount,
                'order_discount_type': saved_cart.order_discount_type,
                'order_discount_reason': saved_cart.order_discount_reason,
                'created_at': saved_cart.created_at,
                'updated_at': saved_cart.updated_at
            }
            
            return Response(response_data, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"Error loading saved cart: {str(e)}")
            return Response(
                {'error': f'Failed to load saved cart: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
