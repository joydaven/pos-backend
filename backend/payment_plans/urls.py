from django.urls import path
from . import views

app_name = 'payment_plans'

urlpatterns = [
    # Templates
    path('templates/', views.template_list, name='template_list'),
    path('templates/<uuid:template_id>/', views.template_detail, name='template_detail'),

    # Plans
    path('', views.plan_list, name='plan_list'),
    path('<uuid:plan_id>/', views.plan_detail, name='plan_detail'),
    path('<uuid:plan_id>/cancel/', views.plan_cancel, name='plan_cancel'),
    path('<uuid:plan_id>/restructure/', views.plan_restructure, name='plan_restructure'),
    path('<uuid:plan_id>/pay-early/', views.plan_pay_early, name='plan_pay_early'),
    path('by-order/<uuid:order_id>/', views.plan_by_order, name='plan_by_order'),

    # Installments
    path('installments/<uuid:installment_id>/', views.installment_update, name='installment_update'),
    path('installments/<uuid:installment_id>/mark-paid/', views.installment_mark_paid, name='installment_mark_paid'),
    path('installments/<uuid:installment_id>/charge/', views.installment_charge, name='installment_charge'),
    path('installments/<uuid:installment_id>/send-receipt/', views.installment_send_receipt, name='installment_send_receipt'),

    # Settings
    path('settings/', views.payment_plan_settings, name='payment_plan_settings'),
]
