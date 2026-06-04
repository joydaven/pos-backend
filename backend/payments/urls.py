"""
URL patterns for the payments app
"""
from django.urls import path
from .views import AuthorizeNetPaymentView, create_customer_profile, get_saved_cards, update_saved_card, delete_saved_card, charge_customer_profile, set_default_card, save_card_note, authorizenet_webhook

app_name = 'payments'

urlpatterns = [
    path('authorize-net/', AuthorizeNetPaymentView.as_view(), name='authorize_net_payment'),
    path('authorize-net/charge-profile/', charge_customer_profile, name='charge_customer_profile'),
    path('authorize-net/webhook/', authorizenet_webhook, name='authorizenet_webhook'),
    path('create-customer-profile/', create_customer_profile, name='create_customer_profile'),
    path('saved-cards/set-default/', set_default_card, name='set_default_card'),
    path('saved-cards/note/', save_card_note, name='save_card_note'),
    path('saved-cards/<str:customer_id>/', get_saved_cards, name='get_saved_cards'),
    path('saved-cards/<str:card_id>/update/', update_saved_card, name='update_saved_card'),
    path('saved-cards/<str:card_id>/delete/', delete_saved_card, name='delete_saved_card'),
]
