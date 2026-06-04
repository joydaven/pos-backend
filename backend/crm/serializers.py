import json
from rest_framework import serializers
from .models import (
    Contact, Order, Product, ProductSimple, ProductVariation, 
    ProductBundle, ProductSubscription, ProductGrouped, ProductInventory, 
    InventoryLocation, Brand, POSOrder, POSOrderItem, PaymentCard, POSLocation,
    WooCreditedService, WooServiceTypes, WooCreditLog, SavedCart, CustomerGeneralNote,
    WebhookLog
)
from .utils.encryption import encrypt_data, decrypt_data, mask_card_number

class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = '__all__'

class ContactWithLatestOrderSerializer(ContactSerializer):
    """
    Extended Contact serializer that includes the customer's latest order
    """
    latest_order = serializers.SerializerMethodField()
    
    class Meta(ContactSerializer.Meta):
        model = Contact
        fields = '__all__'
    
    def get_latest_order(self, obj):
        """
        Get the customer's most recent order from both POS orders and WooCommerce orders.
        Uses prefetched data when available to avoid N+1 queries.
        """
        from django.db.models import Q
        
        # Use prefetched data if available (set by POSCustomerViewSet.get_queryset)
        if hasattr(obj, '_prefetched_latest_pos_order'):
            prefetched = obj._prefetched_latest_pos_order
            latest_pos_order = prefetched[0] if prefetched else None
        else:
            latest_pos_order = obj.pos_orders.filter(
                ~Q(status='trash')
            ).order_by('-created_at').first()
        
        if hasattr(obj, '_prefetched_latest_woo_order'):
            prefetched = obj._prefetched_latest_woo_order
            latest_woo_order = prefetched[0] if prefetched else None
        else:
            latest_woo_order = obj.orders.order_by('-order_date').first()
        
        # Determine which is more recent
        latest_order = None
        order_data = None
        
        if latest_pos_order and latest_woo_order:
            # Compare dates and get the most recent
            if latest_pos_order.created_at > latest_woo_order.order_date:
                latest_order = latest_pos_order
                order_data = self._serialize_pos_order(latest_pos_order)
            else:
                latest_order = latest_woo_order
                order_data = self._serialize_woo_order(latest_woo_order)
        elif latest_pos_order:
            latest_order = latest_pos_order
            order_data = self._serialize_pos_order(latest_pos_order)
        elif latest_woo_order:
            latest_order = latest_woo_order
            order_data = self._serialize_woo_order(latest_woo_order)
        
        return order_data
    
    def _serialize_pos_order(self, order):
        """Serialize POS order data"""
        return {
            'id': str(order.id),
            'order_number': order.order_number,
            'total': str(order.total),
            'status': order.status,
            'date': order.created_at,
            'payment_method': order.payment_method_title or 'N/A',
            'source': 'pos',
            'type': 'POS Order'
        }
    
    def _serialize_woo_order(self, order):
        """Serialize WooCommerce order data"""
        return {
            'id': str(order.id),
            'order_number': order.woo_order_id,
            'total': str(order.total_amount),
            'status': order.status,
            'date': order.order_date,
            'payment_method': 'N/A',  # WooCommerce orders don't have payment method in this model
            'source': 'woocommerce',
            'type': 'WooCommerce Order'
        }

class ContactWithPointsSerializer(ContactSerializer):
    """
    Extended Contact serializer that includes WooCommerce points data
    """
    points = serializers.SerializerMethodField()
    points_value = serializers.SerializerMethodField()
    
    class Meta(ContactSerializer.Meta):
        # Since ContactSerializer.Meta.fields is '__all__', we need to handle it differently
        model = Contact
        fields = '__all__'  # This will include all fields from the model
    
    def get_points(self, obj):
        # Default to 0 if no points data available
        return self.context.get('points_data', {}).get('points', 0)
    
    def get_points_value(self, obj):
        # Default to 0 if no points value available
        return self.context.get('points_data', {}).get('points_value', 0)

class OrderSerializer(serializers.ModelSerializer):
    contact = ContactSerializer(read_only=True)

    class Meta:
        model = Order
        fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'

class ProductWithSKUSerializer(serializers.ModelSerializer):
    """Enhanced product serializer that includes SKU, brand, ATUM inventory data, and subscription options from related tables.
    
    Optimized to use prefetched related data from the ViewSet queryset instead of
    making individual DB queries per product. Expects the queryset to have:
        prefetch_related('simple_details', 'variations',
                         'bundles', 'subscriptions',
                         'inventory_locations__location', 'inventory_locations__variation')
    """
    sku = serializers.SerializerMethodField()
    brand = serializers.SerializerMethodField()
    inventory_locations = serializers.SerializerMethodField()
    variation_inventory_locations = serializers.SerializerMethodField()
    subscription_options = serializers.SerializerMethodField()
    divi_metadata = serializers.SerializerMethodField()
    subscription_metadata = serializers.SerializerMethodField()
    
    class Meta:
        model = Product
        exclude = ['woo_data', 'short_description', 'purchase_note', 'cross_sell_ids', 'upsell_ids', 'tags']

    # ── helpers to read from prefetch cache ──────────────────────────
    @staticmethod
    def _get_simple(obj):
        """Return the first ProductSimple from the prefetch cache."""
        try:
            cache = obj.simple_details.all()
            return cache[0] if cache else None
        except Exception:
            return None

    @staticmethod
    def _get_variations(obj):
        """Return all ProductVariation from the prefetch cache."""
        try:
            return list(obj.variations.all())
        except Exception:
            return []

    @staticmethod
    def _get_bundle(obj):
        """Return the first ProductBundle from the prefetch cache."""
        try:
            cache = obj.bundles.all()
            return cache[0] if cache else None
        except Exception:
            return None

    @staticmethod
    def _get_subscriptions(obj):
        """Return all ProductSubscription from the prefetch cache."""
        try:
            return list(obj.subscriptions.all())
        except Exception:
            return []

    @staticmethod
    def _get_inventory_records(obj):
        """Return all ProductInventory from the prefetch cache."""
        try:
            return list(obj.inventory_locations.all())
        except Exception:
            return []

    @staticmethod
    def _parse_woo_data(woo_data):
        import json
        if isinstance(woo_data, str):
            try:
                return json.loads(woo_data)
            except (json.JSONDecodeError, ValueError):
                return None
        return woo_data

    # ── field methods ────────────────────────────────────────────────
    def get_sku(self, obj):
        # Try ProductSimple
        simple = self._get_simple(obj)
        if simple and simple.sku:
            return simple.sku
        
        # Try first ProductVariation
        variations = self._get_variations(obj)
        if variations and variations[0].sku:
            return variations[0].sku
        
        # Try woo_data in ProductSimple
        if simple and simple.woo_data:
            woo_data = self._parse_woo_data(simple.woo_data)
            if woo_data and 'sku' in woo_data:
                return woo_data['sku']
        
        # Try ProductBundle
        if obj.product_type == 'bundle':
            bundle = self._get_bundle(obj)
            if bundle and bundle.woo_data:
                woo_data = self._parse_woo_data(bundle.woo_data)
                if woo_data and 'sku' in woo_data:
                    return woo_data['sku']
                if woo_data and 'product_data' in woo_data and isinstance(woo_data['product_data'], dict):
                    if 'sku' in woo_data['product_data']:
                        return woo_data['product_data']['sku']
        
        return ''
    
    def get_brand(self, obj):
        """Extract brand information from woo_data.attributes based on product type"""
        def extract_brand_from_woo_data(woo_data):
            try:
                woo_data = self._parse_woo_data(woo_data)
                if woo_data and 'attributes' in woo_data and isinstance(woo_data['attributes'], list):
                    for attr in woo_data['attributes']:
                        if (attr.get('name') == 'Brand' or attr.get('slug') == 'pa_brand') and attr.get('options'):
                            return attr['options'][0] if attr['options'] else None
            except Exception:
                pass
            return None
        
        simple = self._get_simple(obj)
        if simple and simple.woo_data:
            brand = extract_brand_from_woo_data(simple.woo_data)
            if brand:
                return brand
        
        variations = self._get_variations(obj)
        if variations and variations[0].woo_data:
            brand = extract_brand_from_woo_data(variations[0].woo_data)
            if brand:
                return brand
        
        bundle = self._get_bundle(obj)
        if bundle and bundle.woo_data:
            brand = extract_brand_from_woo_data(bundle.woo_data)
            if brand:
                return brand
        
        return None
    
    def get_inventory_locations(self, obj):
        """Get ATUM inventory locations for this product, handling variable products correctly"""
        try:
            all_records = self._get_inventory_records(obj)
            # Filter to ATUM records only
            atum_records = [
                r for r in all_records
                if r.location and r.location.atum_location_id is not None
                and r.notes and 'ATUM' in r.notes
            ]
            
            if obj.product_type in ['variable', 'variable_subscription']:
                # For variable products, only variation-level inventory
                variation_records = [r for r in atum_records if r.variation_id is not None]
                
                if not variation_records:
                    return []
                
                location_aggregates = {}
                for record in variation_records:
                    location_key = record.location.id
                    
                    if location_key not in location_aggregates:
                        location_aggregates[location_key] = {
                            'location': record.location,
                            'total_quantity': 0,
                            'managed_variations': 0,
                            'unmanaged_variations': 0,
                            'is_available': record.is_available,
                            'last_updated': record.last_updated,
                            'notes': record.notes
                        }
                    
                    if record.quantity is not None:
                        location_aggregates[location_key]['total_quantity'] += record.quantity
                        location_aggregates[location_key]['managed_variations'] += 1
                    else:
                        location_aggregates[location_key]['unmanaged_variations'] += 1
                    
                    if record.last_updated > location_aggregates[location_key]['last_updated']:
                        location_aggregates[location_key]['last_updated'] = record.last_updated
                
                locations = []
                for location_data in location_aggregates.values():
                    if location_data['managed_variations'] == 0:
                        stock_quantity = None
                    else:
                        stock_quantity = location_data['total_quantity']
                    
                    locations.append({
                        'id': str(location_data['location'].id),
                        'name': location_data['location'].name,
                        'code': location_data['location'].code,
                        'atum_location_id': location_data['location'].atum_location_id,
                        'stock_quantity': stock_quantity,
                        'is_available': location_data['is_available'],
                        'last_updated': location_data['last_updated'],
                        'notes': location_data['notes'],
                        'managed_variations': location_data['managed_variations'],
                        'unmanaged_variations': location_data['unmanaged_variations']
                    })
                
                return locations
            
            else:
                # For simple products, product-level inventory (variation=None)
                product_records = [r for r in atum_records if r.variation_id is None]
                
                locations = []
                for record in product_records:
                    locations.append({
                        'id': str(record.location.id),
                        'name': record.location.name,
                        'code': record.location.code,
                        'atum_location_id': record.location.atum_location_id,
                        'stock_quantity': record.quantity,
                        'is_available': record.is_available,
                        'last_updated': record.last_updated,
                        'notes': record.notes
                    })
                
                return locations
            
        except Exception as e:
            print(f"Error getting inventory locations for product {obj.id}: {e}")
            return []
    
    def get_variation_inventory_locations(self, obj):
        """Get variation-specific ATUM inventory locations for variable products"""
        try:
            if obj.product_type not in ['variable', 'variable_subscription']:
                return {}
            
            variations = self._get_variations(obj)
            if not variations:
                return {}
            
            all_records = self._get_inventory_records(obj)
            atum_records = [
                r for r in all_records
                if r.location and r.location.atum_location_id is not None
                and r.notes and 'ATUM' in r.notes
                and r.variation_id is not None
            ]
            
            # Group records by variation_id
            records_by_variation = {}
            for r in atum_records:
                records_by_variation.setdefault(r.variation_id, []).append(r)
            
            variation_inventory = {}
            for variation in variations:
                records = records_by_variation.get(variation.id, [])
                locations = []
                for record in records:
                    locations.append({
                        'id': str(record.location.id),
                        'name': record.location.name,
                        'code': record.location.code,
                        'atum_location_id': record.location.atum_location_id,
                        'stock_quantity': record.quantity,
                        'is_available': record.is_available,
                        'last_updated': record.last_updated,
                        'notes': record.notes
                    })
                
                if variation.woo_variation_id:
                    variation_inventory[str(variation.woo_variation_id)] = locations
            
            return variation_inventory
            
        except Exception as e:
            print(f"Error getting variation inventory locations for product {obj.id}: {e}")
            return {}
    
    def get_subscription_options(self, obj):
        """Extract subscription options from ProductSimple, ProductVariation, or ProductSubscription records"""
        subscription_options = []
        
        try:
            def extract_subscription_data(woo_data):
                woo_data = self._parse_woo_data(woo_data)
                subscription_metadata = woo_data.get('subscription_metadata', {}) if woo_data else {}
                
                if subscription_metadata and subscription_metadata.get('has_subscription', False):
                    option = {
                        'price': subscription_metadata.get('subscription_price'),
                        'period': subscription_metadata.get('subscription_period', 'month'),
                        'interval': subscription_metadata.get('subscription_interval', 1),
                        'sign_up_fee': subscription_metadata.get('subscription_sign_up_fee'),
                        'trial_period': subscription_metadata.get('subscription_trial_period'),
                        'trial_length': subscription_metadata.get('subscription_trial_length'),
                        'label': f"Subscribe and save - every {subscription_metadata.get('subscription_interval', 1)} {subscription_metadata.get('subscription_period', 'month')}(s)"
                    }
                    return {k: v for k, v in option.items() if v is not None}
                return None
            
            # Check ProductSubscription records (prefetched)
            for subscription_record in self._get_subscriptions(obj):
                option = {
                    'price': str(subscription_record.price) if subscription_record.price else None,
                    'period': subscription_record.period,
                    'interval': subscription_record.interval,
                    'sign_up_fee': str(subscription_record.sign_up_fee) if subscription_record.sign_up_fee else None,
                    'trial_period': subscription_record.trial_period,
                    'trial_length': subscription_record.trial_length,
                    'label': f"Subscribe and save - every {subscription_record.interval} {subscription_record.period}(s)"
                }
                subscription_options.append({k: v for k, v in option.items() if v is not None})
                break  # Only need the first one
            
            # Check ProductSimple for subscription metadata
            if obj.product_type in ['simple', 'subscription']:
                simple = self._get_simple(obj)
                if simple and simple.woo_data:
                    subscription_data = extract_subscription_data(simple.woo_data)
                    if subscription_data:
                        subscription_options.append(subscription_data)
            
            # Check ProductVariation for subscription metadata
            elif obj.product_type in ['variable', 'variable-subscription', 'variable_subscription']:
                # For variable products, prefer _wcsatt_schemes from meta_data (has all schemes)
                # over subscription_metadata (which only has 1 period/interval combo)
                schemes_extracted = False
                for variation in self._get_variations(obj):
                    if variation.woo_data:
                        woo_data = self._parse_woo_data(variation.woo_data)
                        if woo_data and 'meta_data' in woo_data:
                            for meta in woo_data.get('meta_data', []):
                                if meta.get('key') == '_wcsatt_schemes' and meta.get('value'):
                                    schemes = meta.get('value', [])
                                    for scheme in schemes:
                                        interval = scheme.get('subscription_period_interval', '')
                                        period = scheme.get('subscription_period', '')
                                        discount = scheme.get('subscription_discount', 0)
                                        scheme_price = scheme.get('subscription_price', '')
                                        
                                        if scheme_price:
                                            discounted_price = float(scheme_price)
                                        else:
                                            price = float(woo_data.get('price', 0))
                                            discounted_price = price * (1 - (float(discount) / 100)) if discount else price
                                        
                                        subscription_options.append({
                                            'id': f"sub_{interval}_{period}",
                                            'interval': str(interval),
                                            'period': period,
                                            'discount': int(discount) if isinstance(discount, (int, float)) else discount,
                                            'price': round(discounted_price, 2),
                                            'original_price': float(woo_data.get('price', 0)),
                                            'label': f"Subscribe and save - every {interval} {period}{'s' if int(interval) > 1 else ''}",
                                            'variation_id': variation.woo_variation_id,
                                            'variation_attributes': variation.attributes
                                        })
                                    schemes_extracted = True
                                    break  # Only need schemes from first variation (they're the same)
                    if schemes_extracted:
                        break
                
                # Fallback to subscription_metadata if no _wcsatt_schemes found
                if not schemes_extracted:
                    for variation in self._get_variations(obj):
                        if variation.woo_data:
                            subscription_data = extract_subscription_data(variation.woo_data)
                            if subscription_data:
                                subscription_data['variation_id'] = variation.woo_variation_id
                                subscription_data['variation_attributes'] = variation.attributes
                                subscription_options.append(subscription_data)
            
            # Remove duplicates based on period and interval (normalize interval to int for comparison)
            unique_options = []
            seen_combinations = set()
            for option in subscription_options:
                try:
                    interval_key = int(option.get('interval', 1))
                except (ValueError, TypeError):
                    interval_key = 1
                key = (option.get('period', 'month'), interval_key)
                if key not in seen_combinations:
                    unique_options.append(option)
                    seen_combinations.add(key)
            
            return unique_options
            
        except Exception as e:
            print(f"Error getting subscription options for product {obj.id}: {e}")
            return []
    
    def get_divi_metadata(self, obj):
        """Extract Divi metadata from ProductSimple.woo_data"""
        try:
            simple = self._get_simple(obj)
            if simple and simple.woo_data:
                woo_data = self._parse_woo_data(simple.woo_data)
                if woo_data and 'divi_metadata' in woo_data:
                    return woo_data['divi_metadata']
        except Exception as e:
            print(f"Error getting divi_metadata for product {obj.id}: {e}")
        return None
    
    def get_subscription_metadata(self, obj):
        """Extract subscription metadata from ProductSimple.woo_data or ProductVariation.woo_data for variable products"""
        try:
            # For variable products, prefer variation-level subscription_metadata
            # because ProductSimple may have stale/incorrect plugin_type (e.g. "divi")
            # while variations have the accurate WCSATT data
            if obj.product_type in ['variable', 'variable-subscription', 'variable_subscription']:
                variations = self._get_variations(obj)
                for variation in variations:
                    if variation.woo_data:
                        woo_data = self._parse_woo_data(variation.woo_data)
                        if woo_data and 'subscription_metadata' in woo_data:
                            return woo_data['subscription_metadata']
            
            # Fallback to ProductSimple for simple/subscription products
            simple = self._get_simple(obj)
            if simple and simple.woo_data:
                woo_data = self._parse_woo_data(simple.woo_data)
                if woo_data and 'subscription_metadata' in woo_data:
                    return woo_data['subscription_metadata']
        except Exception as e:
            print(f"Error getting subscription_metadata for product {obj.id}: {e}")
        return None

class ProductVariationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVariation
        fields = '__all__'

class ProductBundleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductBundle
        fields = '__all__'

class ProductSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductSubscription
        fields = '__all__'

class ProductGroupedSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductGrouped
        fields = '__all__'

class ProductSimpleSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductSimple
        fields = '__all__'

class POSOrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = POSOrderItem
        fields = '__all__'
        read_only_fields = ('id',)

class POSOrderItemCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating order items without requiring an order ID"""
    class Meta:
        model = POSOrderItem
        exclude = ('order',)
        read_only_fields = ('id',)
    
    def create(self, validated_data):
        """Override create to extract discount_reason from metadata JSON"""
        import json
        
        # Extract discount_reason from metadata if present
        metadata = validated_data.get('metadata', {})
        if isinstance(metadata, str):
            try:
                metadata_dict = json.loads(metadata)
                discount_reason = metadata_dict.get('discount_reason')
                if discount_reason:
                    validated_data['discount_reason'] = discount_reason
                    print(f"🆕 Extracted discount_reason from metadata: {discount_reason}")
            except json.JSONDecodeError:
                pass
        elif isinstance(metadata, dict):
            discount_reason = metadata.get('discount_reason')
            if discount_reason:
                validated_data['discount_reason'] = discount_reason
                print(f"🆕 Extracted discount_reason from metadata dict: {discount_reason}")
        
        return super().create(validated_data)

class PaymentCardSerializer(serializers.ModelSerializer):
    """Serializer for payment card information with encryption/decryption"""
    card_number = serializers.CharField(write_only=True, required=False)
    expiry_date = serializers.CharField(write_only=True, required=False)
    masked_card_number = serializers.SerializerMethodField()
    
    class Meta:
        model = PaymentCard
        fields = ['id', 'card_number', 'expiry_date', 'masked_card_number', 'last4', 'card_brand', 'cardholder_name']
        read_only_fields = ['id', 'masked_card_number', 'last4']
    
    def get_masked_card_number(self, obj):
        """Return the masked card number"""
        return f"**** **** **** {obj.last4}"
    
    def create(self, validated_data):
        """Create a new payment card with encrypted data"""
        card_number = validated_data.pop('card_number', None)
        expiry_date = validated_data.pop('expiry_date', None)
        
        # Get the last 4 digits before encrypting
        last4 = card_number[-4:] if card_number else ""
        
        # Create the payment card instance
        payment_card = PaymentCard.objects.create(
            encrypted_card_number=encrypt_data(card_number) if card_number else None,
            encrypted_expiry_date=encrypt_data(expiry_date) if expiry_date else None,
            last4=last4,
            **validated_data
        )
        
        return payment_card
    
    def to_representation(self, instance):
        """Override to representation to include masked card number"""
        representation = super().to_representation(instance)
        # Remove any None values
        return {k: v for k, v in representation.items() if v is not None}

class POSOrderSerializer(serializers.ModelSerializer):
    items = POSOrderItemSerializer(many=True, read_only=True)
    contact_details = ContactSerializer(source='contact', read_only=True)
    card_details = PaymentCardSerializer(read_only=True)
    shipping_info = serializers.JSONField(read_only=True)
    
    class Meta:
        model = POSOrder
        fields = '__all__'
        read_only_fields = ('id',)
    
    def to_representation(self, instance):
        """Override to automatically populate bundle_products from bundle items if empty"""
        representation = super().to_representation(instance)
        
        # If bundle_products is empty/null, reconstruct it from bundle items
        if not representation.get('bundle_products'):
            bundle_products = self._reconstruct_bundle_products_from_items(representation.get('items', []))
            if bundle_products:
                representation['bundle_products'] = bundle_products
        
        return representation
    
    def _reconstruct_bundle_products_from_items(self, items):
        """Reconstruct bundle products from individual bundle items"""
        if not items:
            return []
        
        bundle_groups = {}
        non_bundle_items = []
        
        for item in items:
            try:
                # Parse metadata
                metadata = item.get('metadata', '{}')
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                
                # Check if this is a bundle item
                if metadata.get('is_bundle_item') and metadata.get('bundle_parent_id'):
                    bundle_parent_id = metadata['bundle_parent_id']
                    
                    if bundle_parent_id not in bundle_groups:
                        bundle_groups[bundle_parent_id] = {
                            'items': [],
                            'bundle_name': metadata.get('bundle_parent_name', 'Bundle Product'),
                            'bundle_price': metadata.get('original_bundle_price', 0),
                            'bundle_quantity': metadata.get('bundle_quantity', 1)
                        }
                    
                    bundle_groups[bundle_parent_id]['items'].append(item)
                else:
                    non_bundle_items.append(item)
            except (json.JSONDecodeError, KeyError, TypeError):
                # If metadata parsing fails, treat as non-bundle item
                non_bundle_items.append(item)
        
        # Create bundle display items
        result = []
        
        # Add non-bundle items first
        result.extend(non_bundle_items)
        
        # Add reconstructed bundle items
        for bundle_parent_id, bundle_data in bundle_groups.items():
            bundle_item = {
                'id': f'bundle-{bundle_parent_id}',
                'name': f"{bundle_data['bundle_name']} (Bundle)",
                'quantity': bundle_data.get('bundle_quantity', 1),
                'price': str(bundle_data['bundle_price']),
                'subtotal': str(bundle_data['bundle_price']),
                'metadata': json.dumps({
                    'is_original_bundle': True,
                    'bundle_parent_id': bundle_parent_id,
                    'bundle_parent_name': bundle_data['bundle_name'],
                    'bundle_item_count': len(bundle_data['items']),
                    'original_bundle_price': bundle_data['bundle_price']
                })
            }
            result.append(bundle_item)
        
        return result if bundle_groups else []

class POSOrderCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating orders with nested items.
    Items are required on create but optional on PATCH so that payment status
    updates can be sent without re-submitting the full line-item list."""
    items = POSOrderItemCreateSerializer(many=True, required=False)
    card_details = PaymentCardSerializer(required=False, write_only=True)
    
    class Meta:
        model = POSOrder
        fields = '__all__'
        read_only_fields = ('id',)
        extra_kwargs = {
            'shipping_info': {'required': False, 'allow_null': True},
            'shipping_cost': {'required': False, 'default': 0}
        }

    def validate_items(self, value):
        if self.instance is None and not value:
            raise serializers.ValidationError("Items are required when creating an order.")
        return value
    
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        card_data = validated_data.pop('card_details', None)
        
        # Handle shipping_info if it's a string
        shipping_info = validated_data.get('shipping_info')
        if shipping_info and isinstance(shipping_info, str):
            try:
                import json
                validated_data['shipping_info'] = json.loads(shipping_info)
            except json.JSONDecodeError:
                pass
        
        # Extract payment method title from payment_method JSON
        payment_method = validated_data.get('payment_method')
        if payment_method:
            # If payment_method is a string, parse it
            if isinstance(payment_method, str):
                try:
                    import json
                    payment_method_obj = json.loads(payment_method)
                except json.JSONDecodeError:
                    payment_method_obj = {}
            else:
                payment_method_obj = payment_method
            
            # Extract transaction_id from payment method
            if 'id' in payment_method_obj and payment_method_obj['id']:
                validated_data['transaction_id'] = str(payment_method_obj['id'])
            
            # Set payment_method_title based on the payment method type
            if 'type' in payment_method_obj:
                payment_type = payment_method_obj['type']
                if payment_type == 'credit':
                    validated_data['payment_method_title'] = 'Credit Card'
                elif payment_type == 'cash':
                    validated_data['payment_method_title'] = 'Cash'
                elif payment_type == 'check':
                    validated_data['payment_method_title'] = 'Check'
                else:
                    # Capitalize the first letter of the payment type
                    validated_data['payment_method_title'] = payment_type.capitalize()
            else:
                # Default payment method title
                validated_data['payment_method_title'] = 'POS Payment'
        else:
            # Default payment method title if payment_method is not provided
            validated_data['payment_method_title'] = 'POS Payment'
        
        order = POSOrder.objects.create(**validated_data)
        
        # 🔥 ENHANCED DISCOUNT PROCESSING
        # Parse discount metadata and populate POSOrderItem discount fields
        for item_data in items_data:
            # Extract and parse discount metadata
            metadata = item_data.get('metadata', '{}')
            discount_data = {}
            
            if metadata:
                try:
                    import json
                    if isinstance(metadata, str):
                        discount_data = json.loads(metadata)
                    elif isinstance(metadata, dict):
                        discount_data = metadata
                except (json.JSONDecodeError, TypeError) as e:
                    print(f"Warning: Failed to parse item metadata: {e}")
                    discount_data = {}
            
            # Extract discount information from metadata
            original_price = discount_data.get('original_price')
            final_price = discount_data.get('final_price')
            manual_discount = discount_data.get('manual_discount', 0)
            manual_discount_type = discount_data.get('manual_discount_type', 'dollar')
            subscription_discount_amount = discount_data.get('subscription_discount_amount', 0)
            subscription_discount_percentage = discount_data.get('subscription_discount_percentage', 0)
            
            # Calculate total discount amount and determine discount source
            total_discount_amount = 0
            discount_source = ''
            discount_type = 'dollar'  # Default to dollar
            
            # Handle manual discounts (product-level)
            if manual_discount and manual_discount > 0:
                # Ensure values are properly converted to float
                try:
                    manual_discount = float(manual_discount)
                    if manual_discount_type == 'percentage':
                        # Convert percentage discount to dollar amount
                        base_price = float(original_price or item_data.get('price', 0))
                        manual_discount_dollar = (base_price * manual_discount / 100.0)
                        total_discount_amount += manual_discount_dollar
                        discount_type = 'percentage'  # Keep track that it was originally percentage
                    else:
                        total_discount_amount += manual_discount
                        discount_type = 'dollar'
                except (ValueError, TypeError) as e:
                    print(f"Error converting discount values: {e}")
                    # Fallback to safe values
                    total_discount_amount += 0
                
                discount_source = 'manual'
            
            # Handle subscription discounts
            if subscription_discount_amount and subscription_discount_amount > 0:
                total_discount_amount += subscription_discount_amount
                if discount_source:
                    discount_source += ', subscription'
                else:
                    discount_source = 'subscription'
            
            # Set original_price if available
            if original_price:
                item_data['original_price'] = original_price
            
            # Set discount fields
            item_data['discount_amount'] = total_discount_amount
            item_data['discount_type'] = discount_type
            item_data['discount_source'] = discount_source
            
            # Debug logging
            if total_discount_amount > 0:
                print(f"✅ Discount applied to item '{item_data.get('name', 'Unknown')}':") 
                print(f"   - Original Price: ${original_price}")
                print(f"   - Final Price: ${final_price}")
                print(f"   - Total Discount: ${total_discount_amount} ({discount_type})")
                print(f"   - Discount Source: {discount_source}")
                print(f"   - Manual Discount: ${manual_discount} ({manual_discount_type})")
                print(f"   - Subscription Discount: ${subscription_discount_amount} ({subscription_discount_percentage}%)")
            
            # Ensure metadata is always stored as a dict (not a JSON string)
            if 'metadata' in item_data and isinstance(item_data['metadata'], str):
                try:
                    item_data['metadata'] = json.loads(item_data['metadata'])
                except (json.JSONDecodeError, TypeError):
                    item_data['metadata'] = {}
            
            # Create the order item with discount fields populated
            POSOrderItem.objects.create(order=order, **item_data)
            
        if card_data:
            PaymentCard.objects.create(order=order, **card_data)
            
        return order
    
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        card_data = validated_data.pop('card_details', None)
        
        # Handle shipping_info if it's a string
        shipping_info = validated_data.get('shipping_info')
        if shipping_info and isinstance(shipping_info, str):
            try:
                import json
                validated_data['shipping_info'] = json.loads(shipping_info)
            except json.JSONDecodeError:
                pass
        
        # Update order fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if items_data is not None:
            # Delete existing items
            instance.items.all().delete()
            
            # 🔥 ENHANCED DISCOUNT PROCESSING (same as create method)
            # Parse discount metadata and populate POSOrderItem discount fields
            for item_data in items_data:
                # Extract and parse discount metadata
                metadata = item_data.get('metadata', '{}')
                discount_data = {}
                
                if metadata:
                    try:
                        import json
                        if isinstance(metadata, str):
                            discount_data = json.loads(metadata)
                        elif isinstance(metadata, dict):
                            discount_data = metadata
                    except (json.JSONDecodeError, TypeError) as e:
                        print(f"Warning: Failed to parse item metadata: {e}")
                        discount_data = {}
                
                # Extract discount information from metadata
                original_price = discount_data.get('original_price')
                final_price = discount_data.get('final_price')
                manual_discount = discount_data.get('manual_discount', 0)
                manual_discount_type = discount_data.get('manual_discount_type', 'dollar')
                subscription_discount_amount = discount_data.get('subscription_discount_amount', 0)
                subscription_discount_percentage = discount_data.get('subscription_discount_percentage', 0)
                
                # Calculate total discount amount and determine discount source
                total_discount_amount = 0
                discount_source = ''
                discount_type = 'dollar'  # Default to dollar
                
                # Handle manual discounts (product-level)
                if manual_discount and manual_discount > 0:
                    if manual_discount_type == 'percentage':
                        # Convert percentage discount to dollar amount
                        base_price = original_price or item_data.get('price', 0)
                        manual_discount_dollar = (base_price * manual_discount / 100)
                        total_discount_amount += manual_discount_dollar
                        discount_type = 'percentage'  # Keep track that it was originally percentage
                    else:
                        total_discount_amount += manual_discount
                        discount_type = 'dollar'
                    
                    discount_source = 'manual'
                
                # Handle subscription discounts
                if subscription_discount_amount and subscription_discount_amount > 0:
                    total_discount_amount += subscription_discount_amount
                    if discount_source:
                        discount_source += ', subscription'
                    else:
                        discount_source = 'subscription'
                
                # Set original_price if available
                if original_price:
                    item_data['original_price'] = original_price
                
                # Set discount fields
                item_data['discount_amount'] = total_discount_amount
                item_data['discount_type'] = discount_type
                item_data['discount_source'] = discount_source
                
                # Debug logging
                if total_discount_amount > 0:
                    print(f"✅ Discount applied to updated item '{item_data.get('name', 'Unknown')}':")
                    print(f"   - Original Price: ${original_price}")
                    print(f"   - Final Price: ${final_price}")
                    print(f"   - Total Discount: ${total_discount_amount} ({discount_type})")
                    print(f"   - Discount Source: {discount_source}")
                
                # Ensure metadata is always stored as a dict (not a JSON string)
                if 'metadata' in item_data and isinstance(item_data['metadata'], str):
                    try:
                        item_data['metadata'] = json.loads(item_data['metadata'])
                    except (json.JSONDecodeError, TypeError):
                        item_data['metadata'] = {}
                
                # Create the order item with discount fields populated
                POSOrderItem.objects.create(order=instance, **item_data)
                
        if card_data:
            # Update existing card details
            if instance.card_details:
                for attr, value in card_data.items():
                    setattr(instance.card_details, attr, value)
                instance.card_details.save()
            else:
                PaymentCard.objects.create(order=instance, **card_data)
                
        return instance

class POSLocationSerializer(serializers.ModelSerializer):
    """Serializer for POS Location management"""
    
    class Meta:
        model = POSLocation
        fields = ['id', 'name', 'location', 'location_id', 'assigned_location', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_location_id(self, value):
        """Ensure location_id is unique"""
        if POSLocation.objects.filter(location_id=value).exclude(pk=self.instance.pk if self.instance else None).exists():
            raise serializers.ValidationError("A location with this Location ID already exists.")
        return value


class SavedCartCustomerSerializer(serializers.ModelSerializer):
    """Custom serializer for customer data in saved carts with proper address formatting"""
    address = serializers.SerializerMethodField()
    firstName = serializers.CharField(source='first_name', read_only=True)
    lastName = serializers.CharField(source='last_name', read_only=True)
    
    class Meta:
        model = Contact
        fields = ['id', 'firstName', 'lastName', 'first_name', 'last_name', 'email', 'phone', 'address', 'woo_customer_id', 'ghl_contact_id',
                 'billing_address', 'billing_address_2', 'billing_city', 'billing_state', 'billing_postcode', 'billing_country',
                 'shipping_address', 'shipping_address_2', 'shipping_city', 'shipping_state', 'shipping_postcode', 'shipping_country',
                 'shipping_same_as_billing']
    
    def get_address(self, obj):
        """Create address object from billing address fields"""
        if obj.billing_address or obj.billing_city or obj.billing_state or obj.billing_postcode:
            return {
                'street': obj.billing_address or '',
                'street2': obj.billing_address_2 or '',
                'city': obj.billing_city or '',
                'state': obj.billing_state or '',
                'zip': obj.billing_postcode or '',
                'country': obj.billing_country or 'USA'
            }
        return None

class SavedCartSerializer(serializers.ModelSerializer):
    """Serializer for SavedCart model"""
    user = serializers.SerializerMethodField()

    def get_user(self, obj):
        if obj.user.first_name or obj.user.last_name:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return obj.user.username

    customer = SavedCartCustomerSerializer(read_only=True)
    customer_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    
    class Meta:
        model = SavedCart
        fields = ['id', 'name', 'user', 'customer', 'customer_id', 'cart_items', 'order_discount', 'order_discount_type', 'order_discount_reason', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'customer', 'created_at', 'updated_at']
    
    def validate_name(self, value):
        """Ensure cart name is globally unique across all users"""
        if SavedCart.objects.filter(name=value).exclude(pk=self.instance.pk if self.instance else None).exists():
            raise serializers.ValidationError("A saved cart with this name already exists.")
        return value
    
    def validate_customer_id(self, value):
        """Ensure customer exists and belongs to the system (if provided)"""
        if value is None:
            return value
        from .models import Contact
        try:
            customer = Contact.objects.get(id=value)
            return value
        except Contact.DoesNotExist:
            raise serializers.ValidationError("Customer not found.")
    
    def create(self, validated_data):
        """Create saved cart with optional customer"""
        customer_id = validated_data.pop('customer_id', None)
        if customer_id:
            from .models import Contact
            customer = Contact.objects.get(id=customer_id)
            validated_data['customer'] = customer
        return super().create(validated_data)


class CustomerGeneralNoteSerializer(serializers.ModelSerializer):
    """Serializer for customer general notes"""
    created_by_name = serializers.SerializerMethodField()
    created_by_username = serializers.SerializerMethodField()
    
    class Meta:
        model = CustomerGeneralNote
        fields = ['id', 'customer', 'note', 'is_important', 'created_by', 'created_by_name', 'created_by_username', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by_name', 'created_by_username']
    
    def get_created_by_name(self, obj):
        """Get the full name of the user who created the note"""
        if obj.created_by:
            return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip() or obj.created_by.username
        return "Unknown"
    
    def get_created_by_username(self, obj):
        """Get the username of the user who created the note"""
        return obj.created_by.username if obj.created_by else "unknown"


class WebhookLogSerializer(serializers.ModelSerializer):
    """Serializer for webhook logs with formatted timestamps and readable fields"""
    
    received_at_formatted = serializers.SerializerMethodField()
    processed_at_formatted = serializers.SerializerMethodField()
    processing_duration = serializers.SerializerMethodField()
    status_display = serializers.SerializerMethodField()
    webhook_type_display = serializers.SerializerMethodField()
    
    class Meta:
        model = WebhookLog
        fields = [
            'id', 'webhook_type', 'webhook_type_display', 'status', 'status_display',
            'source_ip', 'user_agent', 'content_type', 'request_body',
            'woo_product_id', 'local_product_id', 'action_taken', 'updated_fields', 'update_analysis',
            'is_bundle_product', 'bundle_sync_attempted', 'bundle_sync_success',
            'bundle_items_count', 'default_series_updated',
            'atum_sync_attempted', 'atum_sync_success', 'atum_changes_detected',
            'atum_variations_processed', 'atum_variations_updated',
            'atum_locations_added', 'atum_locations_removed', 'atum_product_updated',
            'atum_errors', 'response_status_code', 'response_message', 'error_message',
            'processing_time_ms', 'received_at', 'received_at_formatted',
            'processed_at', 'processed_at_formatted', 'processing_duration'
        ]
    
    def get_received_at_formatted(self, obj):
        """Format received timestamp for display"""
        return obj.received_at.strftime('%Y-%m-%d %H:%M:%S UTC') if obj.received_at else None
    
    def get_processed_at_formatted(self, obj):
        """Format processed timestamp for display"""
        return obj.processed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if obj.processed_at else None
    
    def get_processing_duration(self, obj):
        """Calculate processing duration in human-readable format"""
        if obj.processing_time_ms:
            if obj.processing_time_ms < 1000:
                return f"{obj.processing_time_ms}ms"
            else:
                return f"{obj.processing_time_ms / 1000:.2f}s"
        return None
    
    def get_status_display(self, obj):
        """Get human-readable status"""
        status_map = {
            'received': 'Received',
            'processing': 'Processing',
            'success': 'Success',
            'failed': 'Failed',
            'skipped': 'Skipped'
        }
        return status_map.get(obj.status, obj.status.title())
    
    def get_webhook_type_display(self, obj):
        """Get human-readable webhook type"""
        type_map = {
            'product': 'Product Update',
            'customer': 'Customer Update',
            'order': 'Order Update',
            'inventory': 'Inventory Update',
            'test': 'Test Webhook'
        }
        return type_map.get(obj.webhook_type, obj.webhook_type.title())
