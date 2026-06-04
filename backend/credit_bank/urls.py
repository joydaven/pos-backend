from django.urls import path
from . import views

urlpatterns = [
    path('<uuid:customer_id>/', views.get_credit_bank_balance, name='credit_bank_balance'),
    path('<uuid:customer_id>/add/', views.add_credits, name='credit_bank_add'),
    path('<uuid:customer_id>/redeem/', views.redeem_credits, name='credit_bank_redeem'),
    path('<uuid:customer_id>/adjust/', views.adjust_credits, name='credit_bank_adjust'),
    path('<uuid:customer_id>/transactions/', views.get_credit_bank_transactions, name='credit_bank_transactions'),
]
