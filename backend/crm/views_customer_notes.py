"""
Customer Notes API Views
Handles CRUD operations for customer general notes
"""

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator
from django.db import transaction
import logging

from .models import Contact, CustomerGeneralNote
from .serializers import CustomerGeneralNoteSerializer

logger = logging.getLogger(__name__)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def customer_notes_list(request, customer_id):
    """
    GET: List all notes for a customer
    POST: Create a new note for a customer
    """
    try:
        customer = get_object_or_404(Contact, id=customer_id)
        
        if request.method == 'GET':
            # Get all notes for this customer
            notes = CustomerGeneralNote.objects.filter(customer=customer).select_related('created_by')
            
            # Pagination
            page = request.GET.get('page', 1)
            page_size = request.GET.get('page_size', 20)
            
            paginator = Paginator(notes, page_size)
            page_obj = paginator.get_page(page)
            
            serializer = CustomerGeneralNoteSerializer(page_obj, many=True)
            
            return Response({
                'results': serializer.data,
                'count': paginator.count,
                'num_pages': paginator.num_pages,
                'current_page': page_obj.number,
                'has_next': page_obj.has_next(),
                'has_previous': page_obj.has_previous(),
            })
        
        elif request.method == 'POST':
            # Create a new note
            data = request.data.copy()
            data['customer'] = customer.id
            data['created_by'] = request.user.id
            
            serializer = CustomerGeneralNoteSerializer(data=data)
            if serializer.is_valid():
                with transaction.atomic():
                    note = serializer.save()
                    logger.info(f"Created note {note.id} for customer {customer.id} by user {request.user.id}")
                
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                logger.error(f"Failed to create note for customer {customer.id}: {serializer.errors}")
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    except Contact.DoesNotExist:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in customer_notes_list: {str(e)}")
        return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def customer_note_detail(request, customer_id, note_id):
    """
    GET: Retrieve a specific note
    PUT: Update a specific note (only by creator)
    DELETE: Delete a specific note (only by creator)
    """
    try:
        customer = get_object_or_404(Contact, id=customer_id)
        note = get_object_or_404(CustomerGeneralNote, id=note_id, customer=customer)
        
        if request.method == 'GET':
            serializer = CustomerGeneralNoteSerializer(note)
            return Response(serializer.data)
        
        elif request.method == 'PUT':
            # Only allow the creator to update the note
            if note.created_by != request.user:
                return Response({'error': 'You can only edit your own notes'}, status=status.HTTP_403_FORBIDDEN)
            
            data = request.data.copy()
            data['customer'] = customer.id
            data['created_by'] = request.user.id
            
            serializer = CustomerGeneralNoteSerializer(note, data=data, partial=True)
            if serializer.is_valid():
                with transaction.atomic():
                    updated_note = serializer.save()
                    logger.info(f"Updated note {note.id} for customer {customer.id} by user {request.user.id}")
                
                return Response(serializer.data)
            else:
                logger.error(f"Failed to update note {note.id}: {serializer.errors}")
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        elif request.method == 'DELETE':
            # Only allow the creator to delete the note
            if note.created_by != request.user:
                return Response({'error': 'You can only delete your own notes'}, status=status.HTTP_403_FORBIDDEN)
            
            with transaction.atomic():
                note_id_str = str(note.id)
                note.delete()
                logger.info(f"Deleted note {note_id_str} for customer {customer.id} by user {request.user.id}")
            
            return Response({'message': 'Note deleted successfully'}, status=status.HTTP_204_NO_CONTENT)
    
    except Contact.DoesNotExist:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    except CustomerGeneralNote.DoesNotExist:
        return Response({'error': 'Note not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in customer_note_detail: {str(e)}")
        return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def acknowledge_important_notes(request, customer_id):
    """
    Mark important notes as acknowledged for a customer
    This updates the customer's last_important_note_acknowledged_at timestamp
    """
    try:
        customer = get_object_or_404(Contact, id=customer_id)
        
        # Update the customer's last acknowledged timestamp
        from django.utils import timezone
        customer.last_important_note_acknowledged_at = timezone.now()
        customer.save(update_fields=['last_important_note_acknowledged_at'])
        
        logger.info(f"User {request.user.id} acknowledged important notes for customer {customer.id}")
        
        return Response({
            'message': 'Important notes acknowledged successfully',
            'acknowledged_at': customer.last_important_note_acknowledged_at
        }, status=status.HTTP_200_OK)
    
    except Contact.DoesNotExist:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in acknowledge_important_notes: {str(e)}")
        return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_unacknowledged_important_notes(request, customer_id):
    """
    Get important notes that haven't been acknowledged yet
    Returns notes that are marked as important and created after the last acknowledgment
    """
    try:
        customer = get_object_or_404(Contact, id=customer_id)
        
        # Get the last acknowledgment timestamp
        last_ack = customer.last_important_note_acknowledged_at
        
        # Filter for important notes created after the last acknowledgment
        query = CustomerGeneralNote.objects.filter(
            customer=customer,
            is_important=True
        ).select_related('created_by')
        
        if last_ack:
            query = query.filter(created_at__gt=last_ack)
        
        notes = query.order_by('-created_at')
        serializer = CustomerGeneralNoteSerializer(notes, many=True)
        
        return Response({
            'notes': serializer.data,
            'count': notes.count(),
            'last_acknowledged_at': last_ack
        }, status=status.HTTP_200_OK)
    
    except Contact.DoesNotExist:
        return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"Error in get_unacknowledged_important_notes: {str(e)}")
        return Response({'error': 'Internal server error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
