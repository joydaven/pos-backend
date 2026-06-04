"""
Views for the Failed Order Queue — lists, retries, and resolves orders
that failed a post-payment step (e.g. WooCommerce order creation).
"""

import logging
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import FailedOrderQueue, POSOrder
from .slack_notifications import notify_order_queue_resolved

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_failed_orders(request):
    """
    GET /api/failed-order-queue/
    Returns queued/retrying entries.  Pass ?status=all to see everything.
    """
    qs = FailedOrderQueue.objects.select_related('pos_order', 'resolved_by')

    status_filter = request.query_params.get('status', 'active')
    if status_filter == 'active':
        qs = qs.filter(status__in=['queued', 'retrying'])
    elif status_filter != 'all':
        qs = qs.filter(status=status_filter)

    entries = []
    for entry in qs[:100]:
        entries.append({
            'id': str(entry.id),
            'order_number': entry.order_number,
            'pos_order_id': str(entry.pos_order_id) if entry.pos_order_id else None,
            'failed_step': entry.failed_step,
            'failed_step_display': entry.get_failed_step_display(),
            'error_message': entry.error_message,
            'payment_taken': entry.payment_taken,
            'payment_amount': str(entry.payment_amount) if entry.payment_amount else None,
            'payment_method': entry.payment_method,
            'transaction_id': entry.transaction_id,
            'status': entry.status,
            'retry_count': entry.retry_count,
            'max_retries': entry.max_retries,
            'slack_notified': entry.slack_notified,
            'created_at': entry.created_at.isoformat() if entry.created_at else None,
            'last_retry_at': entry.last_retry_at.isoformat() if entry.last_retry_at else None,
            'resolved_at': entry.resolved_at.isoformat() if entry.resolved_at else None,
            'resolved_by': (entry.resolved_by.get_full_name() or entry.resolved_by.username)
                           if entry.resolved_by else None,
        })

    return Response({
        'count': len(entries),
        'results': entries,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def retry_failed_order(request, queue_id):
    """
    POST /api/failed-order-queue/<uuid>/retry/
    Retry the failed step for a queued order.
    """
    try:
        entry = FailedOrderQueue.objects.select_related('pos_order').get(id=queue_id)
    except FailedOrderQueue.DoesNotExist:
        return Response({'error': 'Queue entry not found'}, status=status.HTTP_404_NOT_FOUND)

    if entry.status in ('resolved', 'failed'):
        return Response({
            'error': f'Cannot retry — entry is already {entry.status}',
        }, status=status.HTTP_400_BAD_REQUEST)

    if entry.retry_count >= entry.max_retries:
        entry.status = 'failed'
        entry.save(update_fields=['status', 'updated_at'])
        return Response({
            'error': 'Max retries reached. Mark as resolved manually or contact support.',
        }, status=status.HTTP_400_BAD_REQUEST)

    entry.status = 'retrying'
    entry.retry_count += 1
    entry.last_retry_at = timezone.now()
    entry.save(update_fields=['status', 'retry_count', 'last_retry_at', 'updated_at'])

    if entry.failed_step == 'woocommerce_order':
        result = _retry_woocommerce_order(entry, request)
    else:
        result = {'success': False, 'message': f'Retry not implemented for step: {entry.failed_step}'}

    if result.get('success'):
        entry.status = 'resolved'
        entry.resolved_at = timezone.now()
        entry.resolved_by = request.user if request.user.is_authenticated else None
        entry.save(update_fields=['status', 'resolved_at', 'resolved_by', 'updated_at'])

        notify_order_queue_resolved(
            order_number=entry.order_number,
            failed_step=entry.failed_step,
            resolved_by=request.user.get_full_name() or request.user.username if request.user.is_authenticated else 'system',
            method='manual-retry',
        )

        return Response({
            'status': 'resolved',
            'message': f'Order {entry.order_number} successfully retried and resolved.',
            'details': result,
        })
    else:
        entry.status = 'queued'
        entry.error_message = result.get('message', entry.error_message)
        entry.save(update_fields=['status', 'error_message', 'updated_at'])
        return Response({
            'status': 'retry_failed',
            'message': result.get('message', 'Retry failed'),
            'retry_count': entry.retry_count,
            'max_retries': entry.max_retries,
        }, status=status.HTTP_502_BAD_GATEWAY)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resolve_failed_order(request, queue_id):
    """
    POST /api/failed-order-queue/<uuid>/resolve/
    Manually mark a queued entry as resolved (e.g. after fixing the issue out-of-band).
    """
    try:
        entry = FailedOrderQueue.objects.get(id=queue_id)
    except FailedOrderQueue.DoesNotExist:
        return Response({'error': 'Queue entry not found'}, status=status.HTTP_404_NOT_FOUND)

    entry.status = 'resolved'
    entry.resolved_at = timezone.now()
    entry.resolved_by = request.user if request.user.is_authenticated else None
    notes = request.data.get('notes', '')
    if notes:
        entry.error_message = f"{entry.error_message}\n\n[RESOLVED] {notes}"
    entry.save(update_fields=['status', 'resolved_at', 'resolved_by', 'error_message', 'updated_at'])

    notify_order_queue_resolved(
        order_number=entry.order_number,
        failed_step=entry.failed_step,
        resolved_by=request.user.get_full_name() or request.user.username if request.user.is_authenticated else 'system',
        method='manual-resolve',
    )

    return Response({
        'status': 'resolved',
        'message': f'Order {entry.order_number} manually resolved.',
    })


def _retry_woocommerce_order(entry, original_request):
    """
    Replay the create-woocommerce-order call using the stored payload.
    """
    from .views import create_woocommerce_order
    from rest_framework.test import APIRequestFactory

    try:
        payload = entry.request_payload
        if not payload or not payload.get('order_id'):
            return {'success': False, 'message': 'No stored payload for retry'}

        factory = APIRequestFactory()
        fake_request = factory.post(
            '/api/create-woocommerce-order/',
            data=payload,
            format='json',
        )
        fake_request.user = original_request.user

        response = create_woocommerce_order(fake_request)

        if hasattr(response, 'data'):
            resp_data = response.data
        else:
            resp_data = {}

        if resp_data.get('status') == 'success':
            return {
                'success': True,
                'woo_order_id': resp_data.get('woo_order_id'),
                'message': resp_data.get('message', 'WooCommerce order created'),
            }
        elif resp_data.get('status') == 'queued':
            return {
                'success': False,
                'message': resp_data.get('original_error', 'Still failing — re-queued'),
            }
        else:
            return {
                'success': False,
                'message': resp_data.get('message', 'WooCommerce order creation failed'),
            }
    except Exception as exc:
        logger.error(f"[QUEUE RETRY] Exception retrying order {entry.order_number}: {exc}")
        return {'success': False, 'message': str(exc)}
