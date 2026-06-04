from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from crm.models import POSOrder
from .models import (
    ShippingCarrier, 
    ShippingService, 
    Shipment, 
    ShippingRate, 
    ShipmentPackage,
    ShippingWebhookEvent
)
from .serializers import (
    ShippingCarrierSerializer,
    ShippingServiceSerializer,
    ShipmentSerializer,
    ShipmentCreateSerializer,
    ShippingRateSerializer,
    ShipmentPackageSerializer,
    ShippingRateRequestSerializer,
    CreateLabelSerializer,
    TrackShipmentSerializer,
    VoidLabelSerializer,
    ShippingWebhookEventSerializer
)
from .services import ShipStationService

import json
import logging
import hmac
import hashlib
import base64

logger = logging.getLogger(__name__)

class ShippingCarrierViewSet(viewsets.ModelViewSet):
    """
    API endpoint for shipping carriers
    """
    queryset = ShippingCarrier.objects.all()
    serializer_class = ShippingCarrierSerializer
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def sync(self, request):
        """Sync carriers from ShipStation"""
        try:
            service = ShipStationService()
            count = service.sync_carriers()
            return Response({'status': 'success', 'message': f'Synced {count} carriers'})
        except Exception as e:
            logger.error(f"Error syncing carriers: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ShippingServiceViewSet(viewsets.ModelViewSet):
    """
    API endpoint for shipping services
    """
    queryset = ShippingService.objects.all()
    serializer_class = ShippingServiceSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = ShippingService.objects.all()
        carrier = self.request.query_params.get('carrier', None)
        if carrier:
            queryset = queryset.filter(carrier__code=carrier)
        return queryset
    
    @action(detail=False, methods=['post'])
    def sync(self, request):
        """Sync services from ShipStation"""
        try:
            carrier = request.data.get('carrier', None)
            service = ShipStationService()
            count = service.sync_services(carrier_code=carrier)
            return Response({'status': 'success', 'message': f'Synced {count} services'})
        except Exception as e:
            logger.error(f"Error syncing services: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ShipmentPackageViewSet(viewsets.ModelViewSet):
    """
    API endpoint for shipment packages
    """
    queryset = ShipmentPackage.objects.all()
    serializer_class = ShipmentPackageSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = ShipmentPackage.objects.all()
        carrier = self.request.query_params.get('carrier', None)
        if carrier:
            queryset = queryset.filter(carrier__code=carrier)
        return queryset
    
    @action(detail=False, methods=['post'])
    def sync(self, request):
        """Sync packages from ShipStation"""
        try:
            carrier = request.data.get('carrier', None)
            service = ShipStationService()
            count = service.sync_packages(carrier_code=carrier)
            return Response({'status': 'success', 'message': f'Synced {count} packages'})
        except Exception as e:
            logger.error(f"Error syncing packages: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ShipmentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for shipments
    """
    queryset = Shipment.objects.all().order_by('-created_at')
    serializer_class = ShipmentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ShipmentCreateSerializer
        return ShipmentSerializer
    
    def get_queryset(self):
        queryset = Shipment.objects.all().order_by('-created_at')
        
        # Filter by order
        order_id = self.request.query_params.get('order', None)
        if order_id:
            queryset = queryset.filter(order_id=order_id)
        
        # Filter by status
        status = self.request.query_params.get('status', None)
        if status:
            queryset = queryset.filter(status=status)
            
        # Filter by tracking number
        tracking = self.request.query_params.get('tracking', None)
        if tracking:
            queryset = queryset.filter(tracking_number__icontains=tracking)
            
        return queryset
    
    @action(detail=False, methods=['post'])
    def create_order(self, request):
        """Create an order in ShipStation"""
        try:
            order_id = request.data.get('order_id')
            
            if not order_id:
                return Response(
                    {'status': 'error', 'message': 'Order ID is required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            try:
                order = POSOrder.objects.get(id=order_id)
            except POSOrder.DoesNotExist:
                return Response(
                    {'status': 'error', 'message': f'Order with ID {order_id} not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            service = ShipStationService()
            shipstation_order_id = service.create_order(order)
            
            return Response({
                'status': 'success',
                'message': 'Order created in ShipStation',
                'shipstation_order_id': shipstation_order_id
            })
            
        except Exception as e:
            logger.error(f"Error creating order in ShipStation: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def get_rates(self, request):
        """Get shipping rates for an order"""
        serializer = ShippingRateRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            order_id = serializer.validated_data['order_id']
            carrier_code = serializer.validated_data.get('carrier_code')
            service_code = serializer.validated_data.get('service_code')
            package_code = serializer.validated_data.get('package_code')
            
            try:
                order = POSOrder.objects.get(id=order_id)
            except POSOrder.DoesNotExist:
                return Response(
                    {'status': 'error', 'message': f'Order with ID {order_id} not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            service = ShipStationService()
            rates = service.get_rates(
                order, 
                carrier_code=carrier_code,
                service_code=service_code,
                package_code=package_code
            )
            
            serializer = ShippingRateSerializer(rates, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Error getting shipping rates: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def create_label(self, request):
        """Create a shipping label"""
        serializer = CreateLabelSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            shipment_id = serializer.validated_data['shipment_id']
            rate_id = serializer.validated_data.get('rate_id')
            service_code = serializer.validated_data.get('service_code')
            carrier_code = serializer.validated_data.get('carrier_code')
            
            service = ShipStationService()
            label_info = service.create_label(
                shipment_id, 
                rate_id=rate_id,
                service_code=service_code,
                carrier_code=carrier_code
            )
            
            return Response(label_info)
            
        except Exception as e:
            logger.error(f"Error creating shipping label: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def track(self, request):
        """Track a shipment"""
        serializer = TrackShipmentSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            tracking_number = serializer.validated_data['tracking_number']
            carrier_code = serializer.validated_data['carrier_code']
            
            service = ShipStationService()
            tracking_info = service.track_shipment(tracking_number, carrier_code)
            
            return Response(tracking_info)
            
        except Exception as e:
            logger.error(f"Error tracking shipment: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'])
    def void_label(self, request):
        """Void a shipping label"""
        serializer = VoidLabelSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            shipment_id = serializer.validated_data['shipment_id']
            
            service = ShipStationService()
            result = service.void_label(shipment_id)
            
            if result:
                return Response({'status': 'success', 'message': 'Label voided successfully'})
            else:
                return Response(
                    {'status': 'error', 'message': 'Failed to void label'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
        except Exception as e:
            logger.error(f"Error voiding label: {str(e)}")
            return Response(
                {'status': 'error', 'message': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ShippingRateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for shipping rates
    """
    queryset = ShippingRate.objects.all().order_by('rate')
    serializer_class = ShippingRateSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        queryset = ShippingRate.objects.all().order_by('rate')
        
        # Filter by order
        order_id = self.request.query_params.get('order', None)
        if order_id:
            queryset = queryset.filter(order_id=order_id)
            
        # Filter by carrier
        carrier = self.request.query_params.get('carrier', None)
        if carrier:
            queryset = queryset.filter(carrier__code=carrier)
            
        return queryset


@method_decorator(csrf_exempt, name='dispatch')
class ShippingWebhookViewSet(viewsets.ModelViewSet):
    """
    API endpoint for ShipStation webhooks
    """
    queryset = ShippingWebhookEvent.objects.all().order_by('-created_at')
    serializer_class = ShippingWebhookEventSerializer
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    @method_decorator(csrf_exempt)
    def webhook(self, request):
        """
        Handle ShipStation webhook events
        This endpoint is public and secured by HMAC verification
        """
        # Allow access without authentication for webhook endpoint
        self.permission_classes = [AllowAny]
        
        try:
            # Verify HMAC signature if provided
            signature = request.headers.get('X-ShipStation-Signature')
            if signature and hasattr(settings, 'SHIPSTATION_WEBHOOK_SECRET'):
                # Get request body as raw bytes
                body = request.body
                
                # Create HMAC signature
                secret = settings.SHIPSTATION_WEBHOOK_SECRET.encode()
                computed_hash = hmac.new(secret, body, hashlib.sha256).digest()
                computed_signature = base64.b64encode(computed_hash).decode()
                
                # Compare signatures
                if not hmac.compare_digest(signature, computed_signature):
                    logger.warning("Invalid webhook signature")
                    return HttpResponse(status=401)
            
            # Parse JSON payload
            payload = json.loads(request.body)
            
            # Extract event data
            resource_url = payload.get('resource_url')
            resource_type = payload.get('resource_type')
            event_type = payload.get('event')
            
            # Extract resource ID from URL
            resource_id = resource_url.split('/')[-1] if resource_url else None
            
            # Save webhook event
            event = ShippingWebhookEvent.objects.create(
                event_type=event_type,
                resource_url=resource_url,
                resource_type=resource_type,
                resource_id=resource_id,
                payload=payload,
                processed=False
            )
            
            # Process webhook event (can be moved to async task)
            # TODO: Implement webhook processing logic
            
            return HttpResponse(status=200)
            
        except Exception as e:
            logger.error(f"Error processing webhook: {str(e)}")
            return HttpResponse(status=500)
