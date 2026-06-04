from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ShippingCarrierViewSet,
    ShippingServiceViewSet,
    ShipmentViewSet,
    ShippingRateViewSet,
    ShipmentPackageViewSet,
    ShippingWebhookViewSet
)

router = DefaultRouter()
router.register(r'carriers', ShippingCarrierViewSet)
router.register(r'services', ShippingServiceViewSet)
router.register(r'shipments', ShipmentViewSet)
router.register(r'rates', ShippingRateViewSet)
router.register(r'packages', ShipmentPackageViewSet)
router.register(r'webhooks', ShippingWebhookViewSet)

urlpatterns = [
    path('', include(router.urls)),
    # Public webhook endpoint (no authentication required)
    path('webhook/', ShippingWebhookViewSet.as_view({'post': 'webhook'}), name='shipstation-webhook'),
]
