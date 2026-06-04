from rest_framework import serializers
from .models import ShippingCarrier, ShippingService, Shipment, ShippingRate, ShipmentPackage, ShippingWebhookEvent

class ShippingCarrierSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingCarrier
        fields = ['code', 'name', 'account_number', 'is_active', 'created_at', 'updated_at']


class ShippingServiceSerializer(serializers.ModelSerializer):
    carrier_name = serializers.ReadOnlyField(source='carrier.name')
    
    class Meta:
        model = ShippingService
        fields = ['id', 'carrier', 'carrier_name', 'code', 'name', 'domestic', 'international', 'is_active']


class ShipmentPackageSerializer(serializers.ModelSerializer):
    carrier_name = serializers.ReadOnlyField(source='carrier.name')
    
    class Meta:
        model = ShipmentPackage
        fields = ['code', 'name', 'carrier', 'carrier_name', 'length', 'width', 'height', 'is_active']


class ShippingRateSerializer(serializers.ModelSerializer):
    carrier_name = serializers.ReadOnlyField(source='carrier.name')
    service_name = serializers.ReadOnlyField(source='service.name')
    
    class Meta:
        model = ShippingRate
        fields = ['id', 'order', 'carrier', 'carrier_name', 'service', 'service_name', 
                  'rate', 'delivery_days', 'is_guaranteed', 'shipstation_rate_id', 'created_at']


class ShipmentSerializer(serializers.ModelSerializer):
    carrier_name = serializers.ReadOnlyField(source='carrier.name')
    service_name = serializers.ReadOnlyField(source='service.name')
    package_name = serializers.ReadOnlyField(source='package.name')
    order_number = serializers.ReadOnlyField(source='order.order_number')
    
    class Meta:
        model = Shipment
        fields = ['id', 'order', 'order_number', 'shipstation_order_id', 'shipstation_shipment_id',
                  'carrier', 'carrier_name', 'service', 'service_name', 'package', 'package_name',
                  'tracking_number', 'shipping_cost', 'insurance_cost', 'status', 'label_url',
                  'ship_date', 'created_at', 'updated_at']


class ShipmentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ['order', 'carrier', 'service', 'package']
        

class ShippingRateRequestSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    carrier_code = serializers.CharField(required=False, allow_null=True)
    service_code = serializers.CharField(required=False, allow_null=True)
    package_code = serializers.CharField(required=False, allow_null=True)


class CreateLabelSerializer(serializers.Serializer):
    shipment_id = serializers.IntegerField()
    rate_id = serializers.CharField(required=False, allow_null=True)
    service_code = serializers.CharField(required=False, allow_null=True)
    carrier_code = serializers.CharField(required=False, allow_null=True)


class TrackShipmentSerializer(serializers.Serializer):
    tracking_number = serializers.CharField()
    carrier_code = serializers.CharField()


class VoidLabelSerializer(serializers.Serializer):
    shipment_id = serializers.IntegerField()


class ShippingWebhookEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingWebhookEvent
        fields = ['id', 'event_type', 'resource_url', 'resource_type', 
                  'resource_id', 'payload', 'processed', 'created_at']
