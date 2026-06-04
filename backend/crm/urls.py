from django.urls import path, include, re_path
from rest_framework.routers import DefaultRouter
from . import views
from .views import sync_products_manual, sync_fresh_products_manual, sync_comprehensive_products_manual, sync_customers_manual, sync_subscriptions_manual, sync_inventory_locations_manual, sync_brands_manual, sync_atum_inventory_manual, sync_atum_locations_manual, sync_product_prices_manual, sync_product_variations_manual, sync_product_skus_manual
from .auth_views import CookieTokenView, AuthCheckView, RefreshTokenView, LogoutView, UserInfoView
from .views_inventory import InventoryLocationViewSet, ProductInventoryViewSet, get_product_inventory, update_product_inventory, get_product_atum_locations
from .product_categories import get_product_categories, get_hierarchical_categories
from .views_category_sync import sync_product_categories_manual
from .views_subscriptions import get_woocommerce_subscriptions, get_customer_woocommerce_subscriptions, update_subscription_payment_method, update_subscription_dates, get_subscription_counts, get_subscription_payment_profiles, get_subscription_related_orders
from .views_memberships import get_customer_woocommerce_memberships, manage_membership
from .views_membership_actions import pause_membership, resume_membership, cancel_membership, delete_membership, get_membership_status
from .views_woocommerce_orders import get_woocommerce_orders, get_woocommerce_order, get_woocommerce_subscription, get_failed_orders, retry_failed_order
from .views_refunds import process_refund, get_refund_history, get_order_refunds, check_transaction_status
from .views_woocommerce_refunds import get_woocommerce_order_refunds, get_woocommerce_order_refund
from .views_woocommerce_points import get_woocommerce_customer_points, redeem_customer_points, get_woocommerce_customer_points_history, adjust_customer_points
from .views_fractional_points import get_fractional_points_balance, redeem_fractional_points, adjust_fractional_points, get_points_transactions
from .views_shipment_tracking import (
    get_shipment_trackings, 
    get_shipment_tracking, 
    create_shipment_tracking, 
    delete_shipment_tracking,
    get_shipment_tracking_providers,
    get_product_name_by_woo_id,
    resolve_product,
    update_order_shipping_address,
    resend_tracking_email,
    wc_tracking_email_webhook
)
from .views_credited_services import credit_webhook, get_product_credits, get_all_product_credits, credit_webhook_with_variation
from .views_webhooks import atum_inventory_webhook, inventory_location_webhook, product_stock_webhook
from .webhook_enhanced import woocommerce_product_webhook_enhanced
from .webhooks_order import woocommerce_order_webhook, woocommerce_order_webhook_test
from .webhooks_subscription import woocommerce_subscription_webhook, woocommerce_subscription_webhook_test
from .webhooks_ghl import ghl_membership_webhook, ghl_contact_update_webhook
from .views_bundle import get_bundle_product, batch_product_details, batch_variation_lookup
from .views_credit_service_points import (
    process_order_completion_webhook, 
    get_customer_credit_points, 
    get_product_credit_points,
    manual_points_adjustment,
    check_product_eligibility,
    check_variation_credit_eligibility,
    get_service_types,
    create_service_type,
    update_service_type,
    delete_service_type,
    get_customer_credit_transaction_history
)
from .views_service_types import bulk_upload_service_types, download_service_types_template
from .views_appointments import get_customer_appointments, get_appointment_detail
from .views_membership_direct import (
    get_customer_memberships, 
    get_membership_details, 
    manage_membership, 
    test_membership_connection,
    get_customer_subscription_products,
    get_customer_subscriptions,
    manage_subscription,
    get_subscription_details
)
from .views_customer_notes import customer_notes_list, customer_note_detail, acknowledge_important_notes, get_unacknowledged_important_notes
from .views_order_notes import order_notes, delete_order_note, subscription_notes, delete_subscription_note
from .views_saved_carts import SavedCartViewSet
from .views_receipts import send_receipt, send_refund_receipt, send_unpaid_invoice
from .views_invoice_payment import get_invoice_details, process_invoice_payment
from .views_membership_settings import get_membership_email_settings, update_membership_email_settings
from .views_atum_config import (
    get_atum_locations, 
    get_atum_api_locations, 
    create_atum_location, 
    sync_atum_locations, 
    atum_location_mappings
)
from .views_webhook_logs import WebhookLogViewSet
from .views_ghl_orders import get_ghl_orders, get_ghl_order_detail, sync_ghl_orders
from .views_ghl_integration import (
    get_ghl_sync_stats,
    sync_missing_ghl_contact_ids,
    verify_existing_ghl_contact_ids,
    sync_priority_ghl_contact_ids,
    search_ghl_contact,
    ghl_integrity_check,
    test_ghl_connection,
    get_recent_ghl_activity,
    get_ghl_performance_metrics,
    sync_contact_from_ghl
)
from .views_database_schema import get_database_schema, get_table_detail
from .views_product_editor import (
    get_product_full, update_product_full_sync,
    create_variation, update_variation, delete_variation, reorder_variations,
    get_product_attributes_list, get_attribute_terms_list, create_attribute_term,
    search_products_for_linking, sync_product_from_woocommerce
)
from .views_reports import (
    report_kpis, report_sales_over_time, report_sales_by_location,
    report_sales_by_cashier, report_sales_by_payment_method, report_products,
    report_categories, report_customers, report_refunds, report_payments,
    report_subscriptions, report_discounts, report_hourly_heatmap,
    report_reconciliation, report_team_performance, report_liabilities,
)
from .views_failed_order_queue import list_failed_orders, retry_failed_order, resolve_failed_order

router = DefaultRouter()
router.register(r'contacts', views.ContactViewSet, basename='contact')
router.register(r'orders', views.OrderViewSet, basename='order')
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'pos-orders', views.POSOrderViewSet, basename='pos-order')
router.register(r'pos-customers', views.POSCustomerViewSet, basename='pos-customer')
router.register(r'pos-locations', views.POSLocationViewSet, basename='pos-location')
router.register(r'inventory-locations', InventoryLocationViewSet, basename='inventory-location')
router.register(r'product-inventory', ProductInventoryViewSet, basename='product-inventory')
router.register(r'saved-carts', SavedCartViewSet, basename='saved-cart')
router.register(r'webhook-logs', WebhookLogViewSet, basename='webhook-log')

urlpatterns = [
    path('', views.api_root, name='api-root'),
    # Receipt email endpoints (must be before router to avoid pos-orders/ prefix capture)
    path('pos-orders/<int:order_id>/send-receipt/', send_receipt, name='send-receipt'),
    path('pos-orders/<int:order_id>/send-refund-receipt/', send_refund_receipt, name='send-refund-receipt'),
    path('pos-orders/send-invoice/', send_unpaid_invoice, name='send-unpaid-invoice'),
    # Product lookup endpoints must be before router include to avoid
    # products/<pk>/ capturing "resolve" and returning 405 for POST.
    path('products/by-woo-id/<int:woo_product_id>/', get_product_name_by_woo_id, name='get_product_name_by_woo_id'),
    path('products/resolve/', resolve_product, name='resolve_product'),
    path('sync/', views.sync_woocommerce_data, name='sync_woocommerce_data'),
    path('sync/status/', views.get_sync_status, name='get_sync_status'),
    path('sync/stop/', views.stop_sync, name='stop_sync'),
    re_path(r'^sync/products(?:/(?P<limit>\d+))?/$', views.sync_products, name='sync_products'),
    # Manual sync endpoints for frontend integration
    path('sync/products/', views.sync_products_manual, name='sync_products_manual'),
    path('sync/fresh-products/', views.sync_fresh_products_manual, name='sync_fresh_products_manual'),
    path('sync/comprehensive-products/', views.sync_comprehensive_products_manual, name='sync_comprehensive_products_manual'),
    path('sync/customers/', views.sync_customers_manual, name='sync_customers_manual'),
    path('sync/users/', views.sync_users_manual, name='sync_users_manual'),
    path('sync/subscriptions/', views.sync_subscriptions_manual, name='sync_subscriptions_manual'),
    path('sync/inventory-locations/', views.sync_inventory_locations_manual, name='sync_inventory_locations_manual'),
    path('sync/brands/', views.sync_brands_manual, name='sync_brands_manual'),
    path('sync/atum-inventory/', views.sync_atum_inventory_manual, name='sync_atum_inventory_manual'),
    path('sync/atum-locations/', views.sync_atum_locations_manual, name='sync_atum_locations_manual'),
    path('sync/product-prices/', views.sync_product_prices_manual, name='sync_product_prices_manual'),
    path('sync/product-variations/', views.sync_product_variations_manual, name='sync_product_variations_manual'),
    path('sync/product-skus/', views.sync_product_skus_manual, name='sync_product_skus_manual'),
    # Product update endpoint that syncs to both DB and WooCommerce
    path('products/<uuid:product_id>/update-sync/', views.update_product_sync, name='update_product_sync'),
    # Product Editor endpoints (full product data + enhanced sync)
    path('products/<uuid:product_id>/full/', get_product_full, name='get_product_full'),
    path('products/<uuid:product_id>/full-sync/', update_product_full_sync, name='update_product_full_sync'),
    path('products/<uuid:product_id>/sync-from-wc/', sync_product_from_woocommerce, name='sync_product_from_woocommerce'),
    path('products/<uuid:product_id>/variations/', create_variation, name='create_variation'),
    path('products/<uuid:product_id>/variations/<uuid:variation_id>/', update_variation, name='update_variation'),
    path('products/<uuid:product_id>/variations/<uuid:variation_id>/delete/', delete_variation, name='delete_variation'),
    path('products/<uuid:product_id>/variations/reorder/', reorder_variations, name='reorder_variations'),
    path('product-attributes/', get_product_attributes_list, name='get_product_attributes_list'),
    path('product-attributes/<int:attribute_id>/terms/', get_attribute_terms_list, name='get_attribute_terms_list'),
    path('product-attributes/<int:attribute_id>/terms/create/', create_attribute_term, name='create_attribute_term'),
    path('products/search-for-linking/', search_products_for_linking, name='search_products_for_linking'),
    # WooCommerce webhook endpoint for product updates
    path('webhooks/woocommerce/product/', views.woocommerce_product_webhook, name='woocommerce_product_webhook'),
    # Enhanced WooCommerce webhook endpoint for product updates with logging
    path('webhooks/woocommerce/product/enhanced/', woocommerce_product_webhook_enhanced, name='woocommerce_product_webhook_enhanced'),
    # WooCommerce webhook endpoint for customer updates
    path('webhooks/woocommerce/customer/', views.woocommerce_customer_webhook, name='woocommerce_customer_webhook'),
    # WooCommerce webhook test endpoint for debugging
    path('webhooks/woocommerce/test/', views.woocommerce_webhook_test, name='woocommerce_webhook_test'),
    # WooCommerce order webhook endpoints
    path('webhooks/woocommerce/order/', woocommerce_order_webhook, name='woocommerce_order_webhook'),
    path('webhooks/woocommerce/order/test/', woocommerce_order_webhook_test, name='woocommerce_order_webhook_test'),
    # WooCommerce subscription webhook endpoints
    path('webhooks/woocommerce/subscription/', woocommerce_subscription_webhook, name='woocommerce_subscription_webhook'),
    path('webhooks/woocommerce/subscription/test/', woocommerce_subscription_webhook_test, name='woocommerce_subscription_webhook_test'),
    # GHL Membership Sync endpoint
    path('sync-membership-ghl/', views.sync_membership_to_ghl, name='sync_membership_to_ghl'),
    # GHL inbound webhook endpoints (GHL automation -> Backend)
    path('webhooks/ghl/membership/', ghl_membership_webhook, name='ghl_membership_webhook'),
    path('webhooks/ghl/contact-update/', ghl_contact_update_webhook, name='ghl_contact_update_webhook'),
    # ATUM inventory webhook endpoints
    path('webhooks/atum/inventory/', atum_inventory_webhook, name='atum_inventory_webhook'),
    path('webhooks/atum/location/', inventory_location_webhook, name='inventory_location_webhook'),
    path('webhooks/woocommerce/stock/', product_stock_webhook, name='product_stock_webhook'),
    path('webhooks/woocommerce/tracking-email/', wc_tracking_email_webhook, name='wc_tracking_email_webhook'),
    path('all_customers/', views.get_all_customers, name='get_all_customers'),
    path('woocommerce/subscriptions/counts/', get_subscription_counts, name='subscription_counts'),
    path('woocommerce/subscriptions/', get_woocommerce_subscriptions, name='woocommerce_subscriptions'),
    # Add a path with page parameter for pagination
    path('woocommerce/subscriptions/page=<int:page>&per_page=<int:per_page>', get_woocommerce_subscriptions, name='woocommerce_subscriptions_paginated'),
    # Customer-specific WooCommerce subscriptions with payment method info (using WooCommerce customer ID)
    path('customers/<int:woo_customer_id>/woocommerce-subscriptions/', get_customer_woocommerce_subscriptions, name='customer_woocommerce_subscriptions'),
    # Get subscription payment profiles from Authorize.net CIM
    path('subscriptions/<int:subscription_id>/payment-profiles/', get_subscription_payment_profiles, name='get_subscription_payment_profiles'),
    # Update subscription payment method
    path('subscriptions/<int:subscription_id>/update-payment-method/', update_subscription_payment_method, name='update_subscription_payment_method'),
    # Update subscription dates (start_date, next_payment_date, end_date)
    path('subscriptions/<int:subscription_id>/update-dates/', update_subscription_dates, name='update_subscription_dates'),
    # Subscription related orders (parent + renewals) — server-side filtering
    path('subscriptions/<int:subscription_id>/related-orders/', get_subscription_related_orders, name='subscription_related_orders'),
    # WooCommerce memberships endpoints (API-based)
    path('customers/<int:woo_customer_id>/woocommerce-memberships/', get_customer_woocommerce_memberships, name='customer_woocommerce_memberships'),
    path('memberships/<int:membership_id>/manage/', manage_membership, name='manage_woocommerce_membership'),
    
    # New individual membership action endpoints
    path('memberships/<int:membership_id>/status/', get_membership_status, name='get_membership_status'),
    path('memberships/<int:membership_id>/pause/', pause_membership, name='pause_membership'),
    path('memberships/<int:membership_id>/resume/', resume_membership, name='resume_membership'), 
    path('memberships/<int:membership_id>/cancel/', cancel_membership, name='cancel_membership'),
    path('memberships/<int:membership_id>/delete/', delete_membership, name='delete_membership'),
    path('all_products/', views.get_all_products, name='get_all_products'),
    path('product/<int:product_id>/', views.get_product_detail, name='get_product_detail'),
    # Product children endpoint - supports both UUIDs and credited-service-{uuid} format
    path('product/<str:product_id>/children/', views.get_product_children, name='get_product_children'),
    # Batch endpoints for bundle optimization (replaces N individual WooCommerce API calls)
    path('batch-product-details/', batch_product_details, name='batch_product_details'),
    path('batch-variation-lookup/', batch_variation_lookup, name='batch_variation_lookup'),
    # Inventory endpoints
    path('product/<uuid:product_id>/inventory/', get_product_inventory, name='get_product_inventory'),
    path('product/<uuid:product_id>/inventory/<uuid:location_id>/', update_product_inventory, name='update_product_inventory'),
    # ATUM locations endpoint - supports both UUIDs and credited-service-{uuid} format
    path('product/<str:product_id>/atum-locations/', get_product_atum_locations, name='get_product_atum_locations'),
    # Authentication endpoints
    path('auth/token/', CookieTokenView.as_view(), name='token'),
    path('auth/check/', AuthCheckView.as_view(), name='auth_check'),
    path('auth/refresh/', RefreshTokenView.as_view(), name='refresh_token'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/user/', UserInfoView.as_view(), name='user_info'),
    # POS Settings endpoints
    path('pos-settings/', views.get_pos_settings, name='pos_settings'),
    path('pos-settings/update/', views.update_pos_settings, name='update_pos_settings'),
    path('pos/create-woocommerce-order/', views.create_woocommerce_order, name='create_woocommerce_order'),
    path('create-woocommerce-order/', views.create_woocommerce_order, name='create_woocommerce_order_alt'),
    # Failed order queue (orders that failed after payment was captured)
    path('failed-order-queue/', list_failed_orders, name='failed_order_queue_list'),
    path('failed-order-queue/<uuid:queue_id>/retry/', retry_failed_order, name='failed_order_queue_retry'),
    path('failed-order-queue/<uuid:queue_id>/resolve/', resolve_failed_order, name='failed_order_queue_resolve'),
    # Subscription products sync endpoint
    path('sync-subscription-products/', views.sync_subscription_products, name='sync_subscription_products'),
    # Subscription creation endpoint
    path('create-subscription/', views.create_subscription, name='create_subscription'),
    # Variation lookup endpoint
    path('get-variation-id-by-attributes/', views.get_variation_id_by_attributes, name='get_variation_id_by_attributes'),
    # Product categories endpoints
    path('product-categories/', get_product_categories, name='get_product_categories'),
    path('hierarchical-categories/', get_hierarchical_categories, name='get_hierarchical_categories'),
    path('sync-product-categories/', sync_product_categories_manual, name='sync_product_categories_manual'),
    path('product-brands/', views.get_product_brands, name='get_product_brands'),
    
    # Bundle product endpoint that uses local database
    path('bundle-product/<int:woo_product_id>/', get_bundle_product, name='get_bundle_product'),
    # WooCommerce orders endpoints
    path('woocommerce/orders/failed/', get_failed_orders, name='failed_orders'),
    path('woocommerce/orders/<int:order_id>/retry-payment/', retry_failed_order, name='retry_failed_order'),
    path('woocommerce/orders/', get_woocommerce_orders, name='woocommerce_orders'),
    path('woocommerce/orders/<int:order_id>/', get_woocommerce_order, name='woocommerce_order'),
    # Duplicate endpoints without int type constraint for order_id to handle string IDs
    path('woocommerce/orders/<str:order_id>/', get_woocommerce_order, name='woocommerce_order_detail_str'),
    # WooCommerce order status update endpoint
    path('woocommerce-orders/<int:order_id>/update_status/', views.update_woocommerce_order_status, name='update_woocommerce_order_status'),
    # Refund endpoints
    path('process-refund/', process_refund, name='process_refund'),
    path('refund-history/', get_refund_history, name='refund_history'),
    path('order-refunds/<str:order_id>/', get_order_refunds, name='order_refunds'),
    path('check-transaction-status/<str:order_id>/', check_transaction_status, name='check_transaction_status'),
    # WooCommerce refund endpoints
    path('woocommerce/orders/<int:order_id>/refunds/', get_woocommerce_order_refunds, name='woocommerce_order_refunds'),
    path('woocommerce/orders/<int:order_id>/refunds/<int:refund_id>/', get_woocommerce_order_refund, name='woocommerce_order_refund'),
    
    # WooCommerce order notes endpoints
    path('woocommerce/orders/<int:order_id>/notes/', order_notes, name='order_notes'),
    path('woocommerce/orders/<int:order_id>/notes/<int:note_id>/', delete_order_note, name='delete_order_note'),
    
    # Single WooCommerce subscription detail (via WC Subscriptions REST API)
    path('woocommerce/subscriptions/<int:subscription_id>/', get_woocommerce_subscription, name='woocommerce_subscription_detail'),
    # WooCommerce subscription notes endpoints
    path('woocommerce/subscriptions/<int:subscription_id>/notes/', subscription_notes, name='subscription_notes'),
    path('woocommerce/subscriptions/<int:subscription_id>/notes/<int:note_id>/', delete_subscription_note, name='delete_subscription_note'),
    
    # Shipment Tracking endpoints
    path('woocommerce/orders/<int:order_id>/shipment-trackings/', get_shipment_trackings, name='get_shipment_trackings'),
    path('woocommerce/orders/<int:order_id>/shipment-trackings/create/', create_shipment_tracking, name='create_shipment_tracking'),
    path('woocommerce/orders/<int:order_id>/shipment-trackings/providers/', get_shipment_tracking_providers, name='get_shipment_tracking_providers'),
    path('woocommerce/orders/<int:order_id>/shipment-trackings/<str:tracking_id>/', get_shipment_tracking, name='get_shipment_tracking'),
    path('woocommerce/orders/<int:order_id>/shipment-trackings/<str:tracking_id>/delete/', delete_shipment_tracking, name='delete_shipment_tracking'),
    path('woocommerce/orders/<int:order_id>/resend-tracking-email/', resend_tracking_email, name='resend_tracking_email'),
    
    # WooCommerce order shipping address update
    path('woocommerce/orders/<int:order_id>/update-shipping/', update_order_shipping_address, name='update_order_shipping_address'),
    
    # WooCommerce points endpoints
    path('woocommerce/customers/<int:customer_id>/points/', get_woocommerce_customer_points, name='woocommerce_customer_points'),
    path('woocommerce/customers/<int:customer_id>/points/redeem/', redeem_customer_points, name='redeem_customer_points'),
    path('woocommerce/customers/<int:customer_id>/points/history/', get_woocommerce_customer_points_history, name='woocommerce_customer_points_history'),
    path('woocommerce/customers/<int:customer_id>/points/adjust/', adjust_customer_points, name='adjust_customer_points'),
    
    # Fractional points endpoints (new decimal-precision system)
    path('customers/<uuid:customer_id>/fractional-points/', get_fractional_points_balance, name='get_fractional_points_balance'),
    path('customers/<uuid:customer_id>/fractional-points/redeem/', redeem_fractional_points, name='redeem_fractional_points'),
    path('customers/<uuid:customer_id>/fractional-points/adjust/', adjust_fractional_points, name='adjust_fractional_points'),
    path('customers/<uuid:customer_id>/fractional-points/transactions/', get_points_transactions, name='get_points_transactions'),
    
    # Credited Services endpoints (legacy)
    path('store-manager/credits/webhook', credit_webhook, name='credit_webhook'),
    path('store-manager/credits/webhook-with-variation', credit_webhook_with_variation, name='credit_webhook_with_variation'),
    path('store-manager/<str:woo_commerce_contact_id>/<str:product_id>', get_product_credits, name='get_product_credits'),
    path('store-manager/<str:woo_commerce_contact_id>', get_all_product_credits, name='get_all_product_credits'),
    
    # New Credit Service Points endpoints
    path('credit-service-points/order-completion/', process_order_completion_webhook, name='credit_service_points_order_completion'),
    path('credit-service-points/customer/<uuid:customer_id>/', get_customer_credit_points, name='get_customer_credit_points'),
    path('credit-service-points/customer/<uuid:customer_id>/product/<uuid:product_id>/', get_product_credit_points, name='get_product_credit_points'),
    path('credit-service-points/customer/<uuid:customer_id>/transaction-history/', get_customer_credit_transaction_history, name='get_customer_credit_transaction_history'),
    path('credit-service-points/adjust/', manual_points_adjustment, name='manual_points_adjustment'),
    path('credit-service-points/product/<uuid:product_id>/eligibility/', check_product_eligibility, name='check_product_eligibility'),
    path('credit-service-points/check-variation-eligibility/', check_variation_credit_eligibility, name='check_variation_credit_eligibility'),
    path('credit-service-points/service-types/', get_service_types, name='get_service_types'),
    path('credit-service-points/service-types/create/', create_service_type, name='create_service_type'),
    path('credit-service-points/service-types/<int:service_type_id>/', update_service_type, name='update_service_type'),
    path('credit-service-points/service-types/<int:service_type_id>/delete/', delete_service_type, name='delete_service_type'),
    path('credit-service-points/service-types/bulk-upload/', bulk_upload_service_types, name='bulk_upload_service_types'),
    path('credit-service-points/service-types/download-template/', download_service_types_template, name='download_service_types_template'),
    
    # Dual customer creation endpoint (database + WooCommerce)
    path('create-customer-with-woocommerce/', views.create_customer_with_woocommerce, name='create_customer_with_woocommerce'),
    path('customers/<uuid:customer_id>/sync-woo-link/', views.sync_customer_woo_link, name='sync_customer_woo_link'),
    
    # Appointments endpoints
    path('customers/<uuid:customer_id>/appointments/', get_customer_appointments, name='get_customer_appointments'),
    path('appointments/<uuid:appointment_id>/', get_appointment_detail, name='get_appointment_detail'),
    
    # Customer Notes endpoints
    path('customers/<uuid:customer_id>/notes/', customer_notes_list, name='customer_notes_list'),
    path('customers/<uuid:customer_id>/notes/<uuid:note_id>/', customer_note_detail, name='customer_note_detail'),
    path('customers/<uuid:customer_id>/notes/important/acknowledge/', acknowledge_important_notes, name='acknowledge_important_notes'),
    path('customers/<uuid:customer_id>/notes/important/unacknowledged/', get_unacknowledged_important_notes, name='get_unacknowledged_important_notes'),
    
    # ATUM Configuration endpoints
    path('atum-config/locations/', get_atum_locations, name='get_atum_locations'),
    path('atum-config/api-locations/', get_atum_api_locations, name='get_atum_api_locations'),
    path('atum-config/create-location/', create_atum_location, name='create_atum_location'),
    path('atum-config/sync/', sync_atum_locations, name='sync_atum_locations'),
    path('atum-config/mappings/', atum_location_mappings, name='atum_location_mappings'),
    
    # Membership Management endpoints (Direct WooCommerce DB access)
    path('customers/<uuid:customer_id>/memberships/', get_customer_memberships, name='get_customer_memberships'),
    path('memberships/<int:membership_id>/', get_membership_details, name='get_membership_details'),
    path('memberships/<int:membership_id>/manage/', manage_membership, name='manage_membership'),
    path('membership/test-connection/', test_membership_connection, name='test_membership_connection'),
    path('membership/email-settings/', get_membership_email_settings, name='get_membership_email_settings'),
    path('membership/email-settings/update/', update_membership_email_settings, name='update_membership_email_settings'),
    
    # Subscription Management endpoints (Direct WooCommerce DB access)
    path('customers/<uuid:customer_id>/subscription-products/', get_customer_subscription_products, name='get_customer_subscription_products'),
    path('customers/<uuid:customer_id>/subscriptions/', get_customer_subscriptions, name='get_customer_subscriptions'),
    path('subscriptions/<int:subscription_id>/', get_subscription_details, name='get_subscription_details'),
    path('subscriptions/<int:subscription_id>/manage/', manage_subscription, name='manage_subscription'),
    
    # GoHighLevel Orders endpoints
    path('ghl/orders/', get_ghl_orders, name='ghl_orders'),
    path('ghl/orders/sync/', sync_ghl_orders, name='ghl_orders_sync'),
    path('ghl/orders/<str:order_id>/', get_ghl_order_detail, name='ghl_order_detail'),

    # GoHighLevel Integration endpoints
    path('ghl/sync-stats/', get_ghl_sync_stats, name='get_ghl_sync_stats'),
    path('ghl/sync-missing-contact-ids/', sync_missing_ghl_contact_ids, name='sync_missing_ghl_contact_ids'),
    path('ghl/verify-existing-contact-ids/', verify_existing_ghl_contact_ids, name='verify_existing_ghl_contact_ids'),
    path('ghl/sync-priority-contact-ids/', sync_priority_ghl_contact_ids, name='sync_priority_ghl_contact_ids'),
    path('ghl/search-contact/', search_ghl_contact, name='search_ghl_contact'),
    path('ghl/integrity-check/', ghl_integrity_check, name='ghl_integrity_check'),
    path('ghl/test-connection/', test_ghl_connection, name='test_ghl_connection'),
    path('ghl/recent-activity/', get_recent_ghl_activity, name='get_recent_ghl_activity'),
    path('ghl/performance-metrics/', get_ghl_performance_metrics, name='get_ghl_performance_metrics'),
    path('ghl/sync-contact/<uuid:customer_id>/', sync_contact_from_ghl, name='sync_contact_from_ghl'),
    
    # Database Schema endpoints
    path('database/schema/', get_database_schema, name='get_database_schema'),
    path('database/tables/<str:table_name>/', get_table_detail, name='get_table_detail'),
    
    # Public invoice payment endpoints (no auth — token-validated)
    path('invoice/pay/<str:token>/', get_invoice_details, name='invoice_payment_get'),
    path('invoice/pay/<str:token>/process/', process_invoice_payment, name='invoice_payment_process'),

    # Reporting endpoints
    path('reports/kpis/', report_kpis, name='report_kpis'),
    path('reports/sales-over-time/', report_sales_over_time, name='report_sales_over_time'),
    path('reports/sales-by-location/', report_sales_by_location, name='report_sales_by_location'),
    path('reports/sales-by-cashier/', report_sales_by_cashier, name='report_sales_by_cashier'),
    path('reports/sales-by-payment-method/', report_sales_by_payment_method, name='report_sales_by_payment_method'),
    path('reports/products/', report_products, name='report_products'),
    path('reports/categories/', report_categories, name='report_categories'),
    path('reports/customers/', report_customers, name='report_customers'),
    path('reports/refunds/', report_refunds, name='report_refunds'),
    path('reports/payments/', report_payments, name='report_payments'),
    path('reports/subscriptions/', report_subscriptions, name='report_subscriptions'),
    path('reports/discounts/', report_discounts, name='report_discounts'),
    path('reports/hourly-heatmap/', report_hourly_heatmap, name='report_hourly_heatmap'),
    path('reports/reconciliation/', report_reconciliation, name='report_reconciliation'),
    path('reports/team-performance/', report_team_performance, name='report_team_performance'),
    path('reports/liabilities/', report_liabilities, name='report_liabilities'),

    # Router include goes after explicit routes to prevent path shadowing.
    path('', include(router.urls)),
]
