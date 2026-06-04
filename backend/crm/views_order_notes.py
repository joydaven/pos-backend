"""
Order Notes API Views
Handles CRUD operations for WooCommerce order notes
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
import logging

from .woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def order_notes(request, order_id):
    """
    GET: List all notes for a WooCommerce order
    POST: Add a new note to a WooCommerce order
    """
    try:
        wc = WooCommerceAPI()
        
        if request.method == 'GET':
            # Fetch order notes from WooCommerce
            # type=any includes both system notes AND user-added notes
            logger.info(f"Fetching notes for WooCommerce order {order_id}")
            response = wc.wcapi.get(f"orders/{order_id}/notes", params={'type': 'any', 'per_page': 100})
            
            if not response.ok:
                logger.error(f"Failed to get order notes. Status: {response.status_code}, Response: {response.text}")
                return Response(
                    {'error': f'Failed to fetch order notes: {response.status_code}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            notes = response.json()
            logger.info(f"Retrieved {len(notes)} notes for order {order_id}")
            
            return Response({
                'notes': notes,
                'count': len(notes)
            })
        
        elif request.method == 'POST':
            # Add a new note to the order
            note_content = request.data.get('note', '').strip()
            customer_note = request.data.get('customer_note', False)
            added_by = request.user.get_full_name() or request.user.username or 'POS User'
            
            if not note_content:
                return Response(
                    {'error': 'Note content is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Prepend the user who added the note
            full_note = f"[{added_by}] {note_content}"
            
            logger.info(f"Adding note to WooCommerce order {order_id}: {full_note[:100]}...")
            
            result = wc.add_order_note(order_id, {
                'note': full_note,
                'customer_note': customer_note,
                'added_by_user': True
            })
            
            if result.get('status') == 'success':
                logger.info(f"Successfully added note to order {order_id}")
                return Response({
                    'message': 'Note added successfully',
                    'note': result.get('data')
                }, status=status.HTTP_201_CREATED)
            else:
                logger.error(f"Failed to add note: {result.get('message')}")
                return Response(
                    {'error': result.get('message', 'Failed to add note')},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
    
    except Exception as e:
        logger.error(f"Error in order_notes view: {str(e)}")
        return Response(
            {'error': f'Internal server error: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_order_note(request, order_id, note_id):
    """
    DELETE: Remove a specific note from a WooCommerce order
    """
    try:
        wc = WooCommerceAPI()
        
        logger.info(f"Deleting note {note_id} from WooCommerce order {order_id}")
        response = wc.wcapi.delete(f"orders/{order_id}/notes/{note_id}", params={"force": True})
        
        if response.ok:
            logger.info(f"Successfully deleted note {note_id} from order {order_id}")
            return Response({'message': 'Note deleted successfully'}, status=status.HTTP_200_OK)
        else:
            logger.error(f"Failed to delete note. Status: {response.status_code}, Response: {response.text}")
            return Response(
                {'error': f'Failed to delete note: {response.status_code}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    except Exception as e:
        logger.error(f"Error deleting order note: {str(e)}")
        return Response(
            {'error': f'Internal server error: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================
# SUBSCRIPTION NOTES ENDPOINTS
# ============================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def subscription_notes(request, subscription_id):
    """
    GET: List all notes for a WooCommerce subscription
    POST: Add a new note to a WooCommerce subscription
    """
    try:
        wc = WooCommerceAPI()
        
        if request.method == 'GET':
            logger.info(f"Fetching notes for WooCommerce subscription {subscription_id}")
            response = wc.wcapi.get(f"subscriptions/{subscription_id}/notes", params={'type': 'any', 'per_page': 100})
            
            if not response.ok:
                logger.error(f"Failed to get subscription notes. Status: {response.status_code}, Response: {response.text}")
                return Response(
                    {'error': f'Failed to fetch subscription notes: {response.status_code}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            notes = response.json()
            logger.info(f"Retrieved {len(notes)} notes for subscription {subscription_id}")
            
            return Response({
                'notes': notes,
                'count': len(notes)
            })
        
        elif request.method == 'POST':
            note_content = request.data.get('note', '').strip()
            customer_note = request.data.get('customer_note', False)
            also_add_to_parent = request.data.get('also_add_to_parent', False)
            parent_order_id = request.data.get('parent_order_id')
            added_by = request.user.get_full_name() or request.user.username or 'POS User'
            
            if not note_content:
                return Response(
                    {'error': 'Note content is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            full_note = f"[{added_by}] {note_content}"
            
            logger.info(f"Adding note to WooCommerce subscription {subscription_id}: {full_note[:100]}...")
            
            # Add note to subscription
            note_payload = {
                'note': full_note,
                'customer_note': customer_note,
                'added_by_user': True
            }
            
            response = wc.wcapi.post(f"subscriptions/{subscription_id}/notes", note_payload)
            
            if not response.ok:
                logger.error(f"Failed to add subscription note. Status: {response.status_code}, Response: {response.text}")
                return Response(
                    {'error': f'Failed to add subscription note: {response.text}'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            sub_note = response.json()
            logger.info(f"Successfully added note to subscription {subscription_id}")
            
            # Optionally also add to parent order
            parent_note = None
            if also_add_to_parent and parent_order_id:
                logger.info(f"Also adding note to parent order {parent_order_id}")
                parent_result = wc.add_order_note(parent_order_id, {
                    'note': f"{full_note} (from subscription #{subscription_id})",
                    'customer_note': customer_note,
                    'added_by_user': True
                })
                if parent_result.get('status') == 'success':
                    parent_note = parent_result.get('data')
                    logger.info(f"Successfully added note to parent order {parent_order_id}")
            
            return Response({
                'message': 'Note added successfully',
                'subscription_note': sub_note,
                'parent_note': parent_note
            }, status=status.HTTP_201_CREATED)
    
    except Exception as e:
        logger.error(f"Error in subscription_notes view: {str(e)}")
        return Response(
            {'error': f'Internal server error: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_subscription_note(request, subscription_id, note_id):
    """
    DELETE: Remove a specific note from a WooCommerce subscription
    """
    try:
        wc = WooCommerceAPI()
        
        logger.info(f"Deleting note {note_id} from WooCommerce subscription {subscription_id}")
        response = wc.wcapi.delete(f"subscriptions/{subscription_id}/notes/{note_id}", params={"force": True})
        
        if response.ok:
            logger.info(f"Successfully deleted note {note_id} from subscription {subscription_id}")
            return Response({'message': 'Note deleted successfully'}, status=status.HTTP_200_OK)
        else:
            logger.error(f"Failed to delete subscription note. Status: {response.status_code}, Response: {response.text}")
            return Response(
                {'error': f'Failed to delete note: {response.status_code}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    except Exception as e:
        logger.error(f"Error deleting subscription note: {str(e)}")
        return Response(
            {'error': f'Internal server error: {str(e)}'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
