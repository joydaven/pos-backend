from rest_framework import serializers
from .models import Product, InventoryLocation, ProductInventory

class InventoryLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryLocation
        fields = '__all__'

class ProductInventorySerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    
    class Meta:
        model = ProductInventory
        fields = '__all__'
        
class ProductWithInventorySerializer(serializers.ModelSerializer):
    inventory_locations = ProductInventorySerializer(many=True, read_only=True)
    
    class Meta:
        model = Product
        fields = ['id', 'woo_product_id', 'name', 'price', 'stock_status', 'stock_quantity', 'inventory_locations']
