"""
URL configuration for doctorsstudio project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from oauth2_provider.views import AuthorizationView, RevokeTokenView
from oauth2_provider.views.introspect import IntrospectTokenView
from crm.auth_views import CookieTokenView
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView


# OAuth2 provider endpoints
oauth2_endpoint_views = [
    path('authorize/', AuthorizationView.as_view(), name="authorize"),
    path('token/', CookieTokenView.as_view(), name="token"),  # Override with our cookie token view
    path('revoke-token/', RevokeTokenView.as_view(), name="revoke-token"),
    path('introspect/', IntrospectTokenView.as_view(), name="introspect"),
]

# Import the subscription, order, and points views
from crm.views_subscriptions import get_woocommerce_subscriptions, get_subscription_counts
from crm.views_woocommerce_orders import get_woocommerce_orders, get_woocommerce_order
from crm.views_woocommerce_points import get_woocommerce_customer_points

def health_check(request):
    """Lightweight health check endpoint for monitoring and load balancers."""
    from django.http import JsonResponse
    from django.db import connection
    try:
        connection.ensure_connection()
        return JsonResponse({'status': 'ok', 'db': 'connected'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'db': str(e)}, status=503)

urlpatterns = [
    path('api/health/', health_check, name='health_check'),
    path('drs-admin/', admin.site.urls),
    
    # API Documentation with drf-spectacular
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    
    # API Endpoints
    path('api/', include('crm.urls')),
    path('api/payments/', include('payments.urls')),  # Add payments URLs
    path('api/payment-plans/', include('payment_plans.urls')),  # Add payment plans URLs
    path('api/credit-bank/', include('credit_bank.urls')),  # Credit Bank URLs
    path('api/shipping/', include('shipping.urls')),  # Add shipping URLs
    path('api/users/', include('users.urls')),  # Add user management URLs
    path('api/auth/', include('auth_api.urls')),  # Add Google authentication URLs
    
    # OAuth2 Endpoints
    path('o/', include('oauth2_provider.urls', namespace='oauth2_provider')),  # Use standard OAuth2 endpoints
    path('o/custom/', include(oauth2_endpoint_views)),  # Keep custom endpoints as fallback
    
    # Authentication
    path('accounts/login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    
    # Add direct paths for WooCommerce API to match frontend requests
    path('woocommerce/subscriptions/counts/', get_subscription_counts, name='direct_subscription_counts'),
    path('woocommerce/subscriptions/', get_woocommerce_subscriptions, name='direct_woocommerce_subscriptions'),
    path('woocommerce/orders/', get_woocommerce_orders, name='direct_woocommerce_orders'),
    path('woocommerce/orders/<str:order_id>/', get_woocommerce_order, name='direct_woocommerce_order'),
    
    # Add direct path for WooCommerce points endpoint
    path('woocommerce/points/<int:customer_id>/', get_woocommerce_customer_points, name='direct_woocommerce_customer_points'),
]

# Serve static files in development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
