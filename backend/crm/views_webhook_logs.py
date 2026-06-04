"""
Webhook logs API views for managing and displaying webhook activity
"""

from django.db.models import Count, Q, Avg
from django.utils import timezone
from datetime import timedelta
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination

from .models import WebhookLog
from .serializers import WebhookLogSerializer

import logging
logger = logging.getLogger(__name__)


class WebhookLogPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 200


class WebhookLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for webhook logs - read-only access with filtering and stats
    """
    queryset = WebhookLog.objects.all()
    serializer_class = WebhookLogSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = WebhookLogPagination
    
    def get_queryset(self):
        """Filter webhook logs based on query parameters"""
        queryset = WebhookLog.objects.all()
        
        # Filter by webhook type
        webhook_type = self.request.query_params.get('webhook_type')
        if webhook_type:
            queryset = queryset.filter(webhook_type=webhook_type)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        
        if date_from:
            try:
                from_date = timezone.datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                queryset = queryset.filter(received_at__gte=from_date)
            except ValueError:
                pass
        
        if date_to:
            try:
                to_date = timezone.datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                queryset = queryset.filter(received_at__lte=to_date)
            except ValueError:
                pass
        
        # Filter by WooCommerce product ID
        woo_product_id = self.request.query_params.get('woo_product_id')
        if woo_product_id:
            try:
                queryset = queryset.filter(woo_product_id=int(woo_product_id))
            except ValueError:
                pass
        
        # Filter by bundle products
        is_bundle_product = self.request.query_params.get('is_bundle_product')
        if is_bundle_product is not None:
            queryset = queryset.filter(is_bundle_product=is_bundle_product.lower() == 'true')
        
        # Filter by ATUM sync
        atum_sync_attempted = self.request.query_params.get('atum_sync_attempted')
        if atum_sync_attempted is not None:
            queryset = queryset.filter(atum_sync_attempted=atum_sync_attempted.lower() == 'true')
        
        return queryset.order_by('-received_at')
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get webhook logs statistics"""
        try:
            # Calculate date ranges
            now = timezone.now()
            last_24h = now - timedelta(hours=24)
            last_7d = now - timedelta(days=7)
            last_30d = now - timedelta(days=30)
            
            # Total logs count
            total_logs = WebhookLog.objects.count()
            
            # Logs by type
            by_type = dict(
                WebhookLog.objects.values('webhook_type')
                .annotate(count=Count('id'))
                .values_list('webhook_type', 'count')
            )
            
            # Logs by status
            by_status = dict(
                WebhookLog.objects.values('status')
                .annotate(count=Count('id'))
                .values_list('status', 'count')
            )
            
            # Recent activity
            recent_activity = {
                'last_24h': WebhookLog.objects.filter(received_at__gte=last_24h).count(),
                'last_7d': WebhookLog.objects.filter(received_at__gte=last_7d).count(),
                'last_30d': WebhookLog.objects.filter(received_at__gte=last_30d).count(),
            }
            
            # Success rate
            success_count = WebhookLog.objects.filter(status='success').count()
            success_rate = (success_count / total_logs * 100) if total_logs > 0 else 0
            
            # Average processing time
            avg_processing_time = WebhookLog.objects.filter(
                processing_time_ms__isnull=False
            ).aggregate(avg_time=Avg('processing_time_ms'))['avg_time']
            
            return Response({
                'total_logs': total_logs,
                'by_type': by_type,
                'by_status': by_status,
                'recent_activity': recent_activity,
                'success_rate': round(success_rate, 2),
                'avg_processing_time_ms': round(avg_processing_time, 2) if avg_processing_time else None
            })
            
        except Exception as e:
            logger.error(f"Error getting webhook logs stats: {str(e)}")
            return Response(
                {'error': 'Failed to get webhook logs statistics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def cleanup(self, request):
        """Delete webhook logs older than specified days"""
        try:
            days = request.data.get('days', 30)
            if not isinstance(days, int) or days < 1:
                return Response(
                    {'error': 'Days must be a positive integer'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            cutoff_date = timezone.now() - timedelta(days=days)
            deleted_count, _ = WebhookLog.objects.filter(received_at__lt=cutoff_date).delete()
            
            logger.info(f"Cleaned up {deleted_count} webhook logs older than {days} days")
            
            return Response({
                'deleted_count': deleted_count,
                'message': f'Successfully deleted {deleted_count} webhook logs older than {days} days'
            })
            
        except Exception as e:
            logger.error(f"Error cleaning up webhook logs: {str(e)}")
            return Response(
                {'error': 'Failed to cleanup webhook logs'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        """Retry a failed webhook (placeholder for future implementation)"""
        try:
            webhook_log = self.get_object()
            
            if webhook_log.status != 'failed':
                return Response(
                    {'error': 'Can only retry failed webhooks'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # TODO: Implement webhook retry logic based on webhook type
            # This would involve re-processing the original request_body
            
            return Response({
                'success': False,
                'message': 'Webhook retry functionality not yet implemented'
            })
            
        except Exception as e:
            logger.error(f"Error retrying webhook {pk}: {str(e)}")
            return Response(
                {'error': 'Failed to retry webhook'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def health(self, request):
        """Get webhook endpoint health status"""
        try:
            # Calculate health metrics for each webhook type
            webhook_types = ['product', 'customer', 'order', 'inventory', 'test']
            endpoints = []
            
            for webhook_type in webhook_types:
                # Get recent logs for this type (last 24 hours)
                recent_logs = WebhookLog.objects.filter(
                    webhook_type=webhook_type,
                    received_at__gte=timezone.now() - timedelta(hours=24)
                )
                
                total_recent = recent_logs.count()
                failed_recent = recent_logs.filter(status='failed').count()
                
                # Calculate error rate
                error_rate = (failed_recent / total_recent * 100) if total_recent > 0 else 0
                
                # Get last successful webhook
                last_success_log = WebhookLog.objects.filter(
                    webhook_type=webhook_type,
                    status='success'
                ).order_by('-received_at').first()
                
                # Determine health status
                if total_recent == 0:
                    health_status = 'warning'  # No recent activity
                elif error_rate > 50:
                    health_status = 'error'    # High error rate
                elif error_rate > 20:
                    health_status = 'warning'  # Moderate error rate
                else:
                    health_status = 'healthy'  # Low error rate
                
                endpoints.append({
                    'name': f'{webhook_type.title()} Webhook',
                    'url': f'/api/webhooks/woocommerce/{webhook_type}/',
                    'status': health_status,
                    'last_success': last_success_log.received_at.isoformat() if last_success_log else None,
                    'error_rate': round(error_rate, 2)
                })
            
            return Response({'endpoints': endpoints})
            
        except Exception as e:
            logger.error(f"Error getting webhook health: {str(e)}")
            return Response(
                {'error': 'Failed to get webhook health status'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
