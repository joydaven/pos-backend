from django.db import models
from django.utils import timezone
from crm.models import POSOrder

class ShippingCarrier(models.Model):
    """Model for shipping carriers available in ShipStation"""
    code = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class ShippingService(models.Model):
    """Model for shipping services offered by carriers"""
    carrier = models.ForeignKey(ShippingCarrier, on_delete=models.CASCADE, related_name='services')
    code = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    domestic = models.BooleanField(default=True)
    international = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ('carrier', 'code')
    
    def __str__(self):
        return f"{self.carrier.name} - {self.name}"

class ShipmentPackage(models.Model):
    """Model for predefined package types"""
    code = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=100)
    carrier = models.ForeignKey(ShippingCarrier, on_delete=models.CASCADE, related_name='packages', null=True, blank=True)
    length = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    width = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    height = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    
    def __str__(self):
        return self.name

class Shipment(models.Model):
    """Model for shipments created in ShipStation"""
    STATUS_CHOICES = (
        ('awaiting_shipment', 'Awaiting Shipment'),
        ('shipped', 'Shipped'),
        ('cancelled', 'Cancelled'),
        ('on_hold', 'On Hold'),
    )
    
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name='shipments')
    shipstation_order_id = models.CharField(max_length=50, blank=True, null=True)
    shipstation_shipment_id = models.CharField(max_length=50, blank=True, null=True)
    carrier = models.ForeignKey(ShippingCarrier, on_delete=models.SET_NULL, null=True, related_name='shipments')
    service = models.ForeignKey(ShippingService, on_delete=models.SET_NULL, null=True, related_name='shipments')
    package = models.ForeignKey(ShipmentPackage, on_delete=models.SET_NULL, null=True, related_name='shipments')
    tracking_number = models.CharField(max_length=100, blank=True, null=True)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    insurance_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='awaiting_shipment')
    label_url = models.URLField(blank=True, null=True)
    ship_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Shipment {self.id} for Order {self.order.order_number}"
    
    def save(self, *args, **kwargs):
        if not self.ship_date and self.status == 'shipped':
            self.ship_date = timezone.now().date()
        super().save(*args, **kwargs)

class ShippingRate(models.Model):
    """Model for storing shipping rate quotes"""
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name='shipping_rates')
    carrier = models.ForeignKey(ShippingCarrier, on_delete=models.CASCADE, related_name='rates')
    service = models.ForeignKey(ShippingService, on_delete=models.CASCADE, related_name='rates')
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_days = models.PositiveIntegerField(null=True, blank=True)
    is_guaranteed = models.BooleanField(default=False)
    shipstation_rate_id = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('order', 'carrier', 'service')
    
    def __str__(self):
        return f"{self.carrier.name} {self.service.name}: ${self.rate}"

class ShippingWebhookEvent(models.Model):
    """Model for storing ShipStation webhook events"""
    EVENT_TYPES = (
        ('ORDER_NOTIFY', 'Order Notification'),
        ('ITEM_ORDER_NOTIFY', 'Item Order Notification'),
        ('SHIP_NOTIFY', 'Ship Notification'),
        ('ITEM_SHIP_NOTIFY', 'Item Ship Notification'),
    )
    
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES)
    resource_url = models.URLField()
    resource_type = models.CharField(max_length=50)
    resource_id = models.CharField(max_length=50)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.event_type} - {self.resource_id}"
