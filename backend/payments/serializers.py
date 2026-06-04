"""
Serializers for the payments app
"""
from rest_framework import serializers
from .models import PaymentTransaction, PaymentCard

class PaymentCardSerializer(serializers.ModelSerializer):
    """
    Serializer for the PaymentCard model
    """
    billingAddress = serializers.SerializerMethodField()
    
    class Meta:
        model = PaymentCard
        fields = [
            'id', 'order_id', 'last4', 'card_brand', 'exp_month', 'exp_year', 
            'customer_profile_id', 'payment_profile_id', 'is_default', 'created_at',
            'billing_street', 'billing_city', 'billing_state', 'billing_zip', 'billing_country',
            'billingAddress'
        ]
        read_only_fields = ['id', 'created_at']
    
    def get_billingAddress(self, obj):
        """
        Return billing address as a structured object for frontend compatibility
        """
        return {
            'street': obj.billing_street or '',
            'city': obj.billing_city or '',
            'state': obj.billing_state or '',
            'zip': obj.billing_zip or '',
            'country': obj.billing_country or 'USA'
        }

class PaymentTransactionSerializer(serializers.ModelSerializer):
    """
    Serializer for the PaymentTransaction model
    """
    class Meta:
        model = PaymentTransaction
        fields = [
            'id', 'transaction_id', 'order_id', 'amount', 'status', 
            'payment_type', 'auth_code', 'response_code', 'response_message',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

class AuthorizeNetPaymentSerializer(serializers.Serializer):
    """
    Serializer for Authorize.net payment requests
    """
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    cardNumber = serializers.CharField(max_length=20)
    expiryDate = serializers.CharField(max_length=4)
    cvv = serializers.CharField(max_length=4)
    customerEmail = serializers.EmailField(required=False, allow_blank=True)
    customerName = serializers.CharField(required=False, allow_blank=True)
    orderNumber = serializers.CharField(required=False, allow_blank=True)
    orderItems = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_null=True
    )
    
    def validate_expiryDate(self, value):
        """
        Validate that the expiry date is in MMYY format
        """
        if len(value) != 4:
            raise serializers.ValidationError("Expiry date must be in MMYY format")
        
        try:
            month = int(value[:2])
            year = int(value[2:])
            
            if month < 1 or month > 12:
                raise serializers.ValidationError("Invalid month in expiry date")
                
        except ValueError:
            raise serializers.ValidationError("Expiry date must contain only digits")
            
        return value
    
    def validate_cardNumber(self, value):
        """
        Validate that the card number contains only digits
        """
        # Remove any spaces from the card number
        value = value.replace(" ", "")
        
        if not value.isdigit():
            raise serializers.ValidationError("Card number must contain only digits")
            
        return value
    
    def validate_cvv(self, value):
        """
        Validate that the CVV contains only digits
        """
        if not value.isdigit():
            raise serializers.ValidationError("CVV must contain only digits")
            
        return value
