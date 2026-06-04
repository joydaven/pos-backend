from rest_framework import serializers
from .models import WooCreditedService, Contact, Product


class WooCreditedServiceSerializer(serializers.ModelSerializer):
    """
    Serializer for the WooCreditedService model.
    """
    class Meta:
        model = WooCreditedService
        fields = '__all__'


class CreditedServiceResponseSerializer(serializers.ModelSerializer):
    """
    Serializer for returning credited service information with formatted fields.
    """
    product_id = serializers.CharField()
    product_name = serializers.CharField()
    available_credits = serializers.IntegerField(source='product_points')

    class Meta:
        model = WooCreditedService
        fields = ('product_id', 'product_name', 'available_credits')


class ContactInfoSerializer(serializers.ModelSerializer):
    """
    Serializer for returning contact information in credited service responses.
    """
    id = serializers.UUIDField()
    ghl_id = serializers.CharField(source='ghl_contact_id')
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField()

    class Meta:
        model = Contact
        fields = ('id', 'ghl_id', 'first_name', 'last_name', 'email', 'phone')


class CreditWebhookSerializer(serializers.Serializer):
    """
    Serializer for the credit webhook endpoint that adds or subtracts credits.
    """
    contact_woo_id = serializers.CharField(required=True)
    product_id = serializers.CharField(required=True)
    action = serializers.ChoiceField(choices=['add', 'subtract'], required=True)
    points = serializers.IntegerField(required=True, min_value=1)
    contact_info = serializers.DictField(required=False)
