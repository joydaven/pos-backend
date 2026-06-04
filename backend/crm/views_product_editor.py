"""
Product Editor API views — provides full product data for the enhanced product editor
and handles bidirectional sync with WooCommerce for all product fields.
"""
import json
import logging
from rest_framework import status, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import (
    Product, ProductSimple, ProductVariation, ProductSubscription,
    ProductBundle, ProductGrouped, ProductInventory, InventoryLocation, Brand
)
from .serializers import ProductVariationSerializer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ProductFull Serializer
# ---------------------------------------------------------------------------

class ProductFullSerializer(serializers.ModelSerializer):
    """
    Comprehensive serializer that returns ALL product data including related
    tables, suitable for the enhanced product editor.
    """
    sku = serializers.SerializerMethodField()
    brand = serializers.SerializerMethodField()
    simple_details = serializers.SerializerMethodField()
    variations = serializers.SerializerMethodField()
    subscription_data = serializers.SerializerMethodField()
    bundle_data = serializers.SerializerMethodField()
    grouped_data = serializers.SerializerMethodField()
    atum_inventory = serializers.SerializerMethodField()
    attributes = serializers.SerializerMethodField()
    gallery_images = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = '__all__'

    def _parse_json(self, data):
        if isinstance(data, str):
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError):
                return {}
        return data or {}

    def _extract_brand(self, woo_data):
        wd = self._parse_json(woo_data)
        for attr in wd.get('attributes', []):
            if (attr.get('name') == 'Brand' or attr.get('slug') == 'pa_brand') and attr.get('options'):
                return attr['options'][0]
        return None

    def get_sku(self, obj):
        simple = ProductSimple.objects.filter(product=obj).first()
        if simple and simple.sku:
            return simple.sku
        if simple and simple.woo_data:
            wd = self._parse_json(simple.woo_data)
            if wd.get('sku'):
                return wd['sku']
        var = ProductVariation.objects.filter(product=obj).first()
        if var and var.sku:
            return var.sku
        if obj.product_type == 'bundle':
            bundle = ProductBundle.objects.filter(product=obj).first()
            if bundle and bundle.woo_data:
                wd = self._parse_json(bundle.woo_data)
                if wd.get('sku'):
                    return wd['sku']
        return ''

    def get_brand(self, obj):
        for ModelClass in (ProductSimple, ProductVariation, ProductBundle):
            record = ModelClass.objects.filter(product=obj).first()
            if record and hasattr(record, 'woo_data') and record.woo_data:
                brand = self._extract_brand(record.woo_data)
                if brand:
                    return brand
        return None

    def get_simple_details(self, obj):
        simple = ProductSimple.objects.filter(product=obj).first()
        if not simple:
            return None
        return {
            'id': str(simple.id),
            'sku': simple.sku or '',
            'weight': str(simple.weight) if simple.weight else None,
            'length': str(simple.length) if simple.length else None,
            'width': str(simple.width) if simple.width else None,
            'height': str(simple.height) if simple.height else None,
            'is_virtual': simple.is_virtual,
            'is_downloadable': simple.is_downloadable,
        }

    def get_variations(self, obj):
        if obj.product_type not in ('variable', 'variable_subscription'):
            return []
        variations = ProductVariation.objects.filter(product=obj).order_by('menu_order')
        results = []
        for v in variations:
            wd = self._parse_json(v.woo_data)
            results.append({
                'id': str(v.id),
                'woo_variation_id': v.woo_variation_id,
                'sku': v.sku or '',
                'price': str(v.price) if v.price else None,
                'regular_price': str(v.regular_price) if v.regular_price else None,
                'sale_price': str(v.sale_price) if v.sale_price else None,
                'stock_quantity': v.stock_quantity,
                'manage_stock': wd.get('manage_stock', False),
                'backorders': wd.get('backorders', 'no'),
                'weight': wd.get('weight'),
                'dimensions': wd.get('dimensions', {}),
                'attributes': v.attributes or [],
                'image': wd.get('image'),
                'description': wd.get('description', ''),
                'enabled': wd.get('status', 'publish') == 'publish',
                'menu_order': v.menu_order,
            })
        return results

    def get_subscription_data(self, obj):
        if obj.product_type not in ('subscription', 'variable_subscription', 'simple'):
            return None
        sub = ProductSubscription.objects.filter(product=obj).first()
        if not sub:
            return None
        wd = self._parse_json(sub.woo_data)
        schemes = []
        raw_schemes = wd.get('_wc_subscription_schemes') or wd.get('subscription_schemes') or []
        if isinstance(raw_schemes, str):
            try:
                raw_schemes = json.loads(raw_schemes)
            except Exception:
                raw_schemes = []
        for s in raw_schemes:
            schemes.append({
                'price': s.get('subscription_price') or s.get('price'),
                'period': s.get('subscription_period') or s.get('period', 'month'),
                'interval': s.get('subscription_period_interval') or s.get('interval', 1),
                'sign_up_fee': s.get('subscription_sign_up_fee') or s.get('sign_up_fee', 0),
                'trial_period': s.get('subscription_trial_period'),
                'trial_length': s.get('subscription_trial_length'),
                'discount': s.get('discount') or s.get('subscription_discount', 0),
            })
        sell_mode = wd.get('_wcsatt_force_subscription', 'no')
        if sell_mode == 'yes':
            sell_on_subscription = 'force'
        elif schemes:
            sell_on_subscription = 'choose'
        else:
            sell_on_subscription = 'one-time'
        return {
            'price': str(sub.price) if sub.price else None,
            'period': sub.period,
            'interval': sub.interval,
            'trial_period': sub.trial_period,
            'trial_length': sub.trial_length,
            'sign_up_fee': str(sub.sign_up_fee) if sub.sign_up_fee else None,
            'sell_on_subscription': sell_on_subscription,
            'schemes': schemes,
        }

    def get_bundle_data(self, obj):
        if obj.product_type != 'bundle':
            return None
        bundle = ProductBundle.objects.filter(product=obj).first()
        if not bundle:
            return None
        bundled_items = []
        wd = self._parse_json(bundle.woo_data)
        raw_items = wd.get('bundled_items', [])
        for item in raw_items:
            pid = item.get('product_id') or item.get('id')
            name = item.get('name', '')
            if not name and pid:
                try:
                    p = Product.objects.get(woo_product_id=pid)
                    name = p.name
                except Product.DoesNotExist:
                    name = f'Product #{pid}'
            bundled_items.append({
                'product_id': pid,
                'product_name': name,
                'quantity_min': item.get('quantity_min', 1),
                'quantity_max': item.get('quantity_max', 1),
                'quantity_default': item.get('quantity_default', 1),
                'optional': item.get('optional', False),
                'discount': item.get('discount', 0),
            })
        return {
            'bundled_items': bundled_items,
            'bundled_product_ids': bundle.bundled_products or [],
            'min_bundle_size': bundle.min_quantity,
            'max_bundle_size': bundle.max_quantity,
        }

    def get_grouped_data(self, obj):
        if obj.product_type != 'grouped':
            return None
        grouped = ProductGrouped.objects.filter(product=obj).first()
        if not grouped:
            return None
        grouped_products = []
        for pid in (grouped.grouped_products or []):
            try:
                p = Product.objects.get(woo_product_id=pid)
                grouped_products.append({
                    'product_id': pid,
                    'product_name': p.name,
                    'price': str(p.price) if p.price else None,
                })
            except Product.DoesNotExist:
                grouped_products.append({
                    'product_id': pid,
                    'product_name': f'Product #{pid}',
                    'price': None,
                })
        return {
            'grouped_products': grouped_products,
            'grouped_product_ids': grouped.grouped_products or [],
        }

    def get_atum_inventory(self, obj):
        records = ProductInventory.objects.filter(
            product=obj,
            location__atum_location_id__isnull=False,
        ).select_related('location', 'variation')
        results = []
        for r in records:
            results.append({
                'id': str(r.id),
                'location_id': str(r.location.id),
                'location_name': r.location.name,
                'atum_location_id': r.location.atum_location_id,
                'variation_id': str(r.variation.id) if r.variation else None,
                'variation_woo_id': r.variation.woo_variation_id if r.variation else None,
                'stock_quantity': r.quantity,
                'is_available': r.is_available,
                'reorder_level': r.reorder_level,
                'notes': r.notes,
                'last_updated': r.last_updated.isoformat() if r.last_updated else None,
            })
        return results

    def get_attributes(self, obj):
        """Extract attributes from the best available woo_data source."""
        sources = [
            ProductSimple.objects.filter(product=obj).first(),
            ProductBundle.objects.filter(product=obj).first(),
            ProductVariation.objects.filter(product=obj).first(),
        ]
        for source in sources:
            if source and hasattr(source, 'woo_data') and source.woo_data:
                wd = self._parse_json(source.woo_data)
                raw_attrs = wd.get('attributes', [])
                if raw_attrs:
                    return [
                        {
                            'id': a.get('id', 0),
                            'name': a.get('name', ''),
                            'slug': a.get('slug', ''),
                            'options': a.get('options', []) if isinstance(a.get('options'), list) else [a.get('option', '')],
                            'visible': a.get('visible', True),
                            'variation': a.get('variation', False),
                        }
                        for a in raw_attrs
                    ]
        if obj.woo_data:
            wd = self._parse_json(obj.woo_data)
            raw_attrs = wd.get('attributes', [])
            if raw_attrs:
                return [
                    {
                        'id': a.get('id', 0),
                        'name': a.get('name', ''),
                        'slug': a.get('slug', ''),
                        'options': a.get('options', []) if isinstance(a.get('options'), list) else [a.get('option', '')],
                        'visible': a.get('visible', True),
                        'variation': a.get('variation', False),
                    }
                    for a in raw_attrs
                ]
        return []

    def get_gallery_images(self, obj):
        imgs = obj.images or []
        if isinstance(imgs, str):
            try:
                imgs = json.loads(imgs)
            except Exception:
                imgs = []
        return imgs


# ---------------------------------------------------------------------------
# API Views
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_full(request, product_id):
    """Return complete product data with all related tables for the product editor."""
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    serializer = ProductFullSerializer(product)
    return Response(serializer.data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_product_full_sync(request, product_id):
    """
    Update a product in both local database and WooCommerce.
    Handles ALL product fields, variations, attributes, etc.
    """
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    data = request.data
    logger.info(f"[ProductEditor] Updating product {product.name} (ID: {product_id})")

    # ---- Update local Product model fields ----
    direct_fields = [
        'name', 'description', 'short_description', 'price', 'regular_price',
        'sale_price', 'status', 'stock_status', 'stock_quantity', 'product_type',
        'tax_status', 'tax_class', 'manage_stock', 'backorders',
        'sold_individually', 'purchase_note', 'menu_order', 'reviews_allowed',
        'low_stock_amount', 'shipping_class', 'categories', 'images', 'tags',
        'upsell_ids', 'cross_sell_ids',
    ]
    for field in direct_fields:
        if field in data:
            setattr(product, field, data[field])

    for date_field in ('date_on_sale_from', 'date_on_sale_to'):
        if date_field in data:
            val = data[date_field]
            setattr(product, date_field, val if val else None)

    product.save()

    # ---- Update ProductSimple if present ----
    if 'simple_details' in data and data['simple_details']:
        sd = data['simple_details']
        simple, _ = ProductSimple.objects.get_or_create(product=product)
        for f in ('sku', 'weight', 'length', 'width', 'height', 'is_virtual', 'is_downloadable'):
            if f in sd:
                setattr(simple, f, sd[f] if sd[f] not in (None, '', 'null') else None)
        simple.save()

    # ---- Sync to WooCommerce ----
    woo_sync_result = _sync_product_to_woocommerce(product, data)

    from users.activity_log import log_activity
    log_activity(request, f"Updated product: {product.name}", category='product', details={
        'product_id': str(product.id),
        'product_name': product.name,
        'woo_product_id': product.woo_id if hasattr(product, 'woo_id') else None,
        'fields_updated': [f for f in data.keys() if f != 'simple_details'],
    })

    serializer = ProductFullSerializer(product)
    return Response({
        'success': True,
        'product': serializer.data,
        'woocommerce_sync': woo_sync_result,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_variation(request, product_id):
    """Create a new variation for a variable product, synced to WooCommerce."""
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    if product.product_type not in ('variable', 'variable_subscription'):
        return Response({'error': 'Product is not a variable product'}, status=status.HTTP_400_BAD_REQUEST)

    data = request.data
    wc_data = {}
    for f in ('sku', 'regular_price', 'sale_price', 'description', 'manage_stock', 'stock_quantity', 'backorders', 'weight'):
        if f in data:
            wc_data[f] = data[f]
    if 'dimensions' in data:
        wc_data['dimensions'] = data['dimensions']
    if 'attributes' in data:
        wc_data['attributes'] = data['attributes']
    if 'image' in data:
        wc_data['image'] = data['image']

    try:
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        response = wc.wcapi.post(f"products/{product.woo_product_id}/variations", wc_data)
        if response.status_code not in (200, 201):
            return Response({
                'error': f'WooCommerce error: {response.status_code}',
                'detail': response.text
            }, status=status.HTTP_502_BAD_GATEWAY)
        wc_variation = response.json()
    except Exception as e:
        logger.error(f"[ProductEditor] WC create variation error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

    variation = ProductVariation.objects.create(
        product=product,
        woo_variation_id=wc_variation['id'],
        sku=wc_variation.get('sku', ''),
        price=wc_variation.get('price') or None,
        regular_price=wc_variation.get('regular_price') or None,
        sale_price=wc_variation.get('sale_price') or None,
        stock_quantity=wc_variation.get('stock_quantity'),
        menu_order=wc_variation.get('menu_order', 0),
        attributes=wc_variation.get('attributes', []),
        woo_data=wc_variation,
    )

    return Response({
        'success': True,
        'variation': ProductVariationSerializer(variation).data,
    }, status=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_variation(request, product_id, variation_id):
    """Update an existing variation in both local DB and WooCommerce."""
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        variation = ProductVariation.objects.get(id=variation_id, product=product)
    except ProductVariation.DoesNotExist:
        return Response({'error': 'Variation not found'}, status=status.HTTP_404_NOT_FOUND)

    data = request.data
    for f in ('sku', 'price', 'regular_price', 'sale_price', 'stock_quantity', 'menu_order'):
        if f in data:
            setattr(variation, f, data[f] if data[f] not in (None, '', 'null') else None)
    if 'attributes' in data:
        variation.attributes = data['attributes']
    variation.save()

    wc_data = {}
    for f in ('sku', 'regular_price', 'sale_price', 'description', 'manage_stock', 'stock_quantity', 'backorders', 'weight', 'status'):
        if f in data:
            wc_data[f] = str(data[f]) if f in ('regular_price', 'sale_price') and data[f] is not None else data[f]
    if 'dimensions' in data:
        wc_data['dimensions'] = data['dimensions']
    if 'attributes' in data:
        wc_data['attributes'] = data['attributes']
    if 'image' in data:
        wc_data['image'] = data['image']

    wc_sync = {'success': True, 'message': 'No WC sync needed'}
    if wc_data and variation.woo_variation_id:
        try:
            from .woocommerce import WooCommerceAPI
            wc = WooCommerceAPI()
            resp = wc.wcapi.put(f"products/{product.woo_product_id}/variations/{variation.woo_variation_id}", wc_data)
            if resp.status_code == 200:
                wc_sync = {'success': True, 'message': 'Synced to WooCommerce'}
                variation.woo_data = resp.json()
                variation.save(update_fields=['woo_data'])
            else:
                wc_sync = {'success': False, 'message': f'WC error {resp.status_code}'}
        except Exception as e:
            wc_sync = {'success': False, 'message': str(e)}

    return Response({
        'success': True,
        'variation': ProductVariationSerializer(variation).data,
        'woocommerce_sync': wc_sync,
    })


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_variation(request, product_id, variation_id):
    """Delete a variation from both local DB and WooCommerce."""
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        variation = ProductVariation.objects.get(id=variation_id, product=product)
    except ProductVariation.DoesNotExist:
        return Response({'error': 'Variation not found'}, status=status.HTTP_404_NOT_FOUND)

    wc_sync = {'success': True, 'message': 'No WC sync needed'}
    if variation.woo_variation_id:
        try:
            from .woocommerce import WooCommerceAPI
            wc = WooCommerceAPI()
            resp = wc.wcapi.delete(f"products/{product.woo_product_id}/variations/{variation.woo_variation_id}", params={'force': True})
            if resp.status_code in (200, 204):
                wc_sync = {'success': True, 'message': 'Deleted from WooCommerce'}
            else:
                wc_sync = {'success': False, 'message': f'WC error {resp.status_code}'}
        except Exception as e:
            wc_sync = {'success': False, 'message': str(e)}

    variation.delete()
    return Response({
        'success': True,
        'message': 'Variation deleted',
        'woocommerce_sync': wc_sync,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reorder_variations(request, product_id):
    """Bulk-update menu_order for a product's variations based on the submitted ordering."""
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    variation_ids = request.data.get('variation_ids', [])
    if not variation_ids or not isinstance(variation_ids, list):
        return Response({'error': 'variation_ids must be a non-empty list'}, status=status.HTTP_400_BAD_REQUEST)

    variations = ProductVariation.objects.filter(product=product, id__in=variation_ids)
    id_to_variation = {str(v.id): v for v in variations}

    updated = []
    for idx, vid in enumerate(variation_ids):
        variation = id_to_variation.get(str(vid))
        if variation and variation.menu_order != idx:
            variation.menu_order = idx
            variation.save(update_fields=['menu_order'])
            updated.append(str(variation.id))

    logger.info(f"[ProductEditor] Reordered {len(updated)} variations for product {product_id}")
    return Response({
        'success': True,
        'updated_count': len(updated),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_product_attributes_list(request):
    """Get all global product attributes from WooCommerce."""
    try:
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        result = wc.get_product_attributes(per_page=100)
        return Response(result.get('data', []))
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_attribute_terms_list(request, attribute_id):
    """Get terms for a specific product attribute."""
    try:
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        result = wc.get_attribute_terms(attribute_id, per_page=100)
        return Response(result.get('data', []))
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_attribute_term(request, attribute_id):
    """Create a new term for a product attribute in WooCommerce."""
    try:
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        resp = wc.wcapi.post(f"products/attributes/{attribute_id}/terms", request.data)
        if resp.status_code in (200, 201):
            return Response(resp.json(), status=status.HTTP_201_CREATED)
        return Response({'error': f'WC error {resp.status_code}'}, status=status.HTTP_502_BAD_GATEWAY)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_products_for_linking(request):
    """Lightweight product search for upsell/cross-sell pickers."""
    query = request.GET.get('q', '')
    if len(query) < 2:
        return Response([])
    products = Product.objects.filter(name__icontains=query)[:20]
    return Response([
        {
            'id': str(p.id),
            'woo_product_id': p.woo_product_id,
            'name': p.name,
            'price': str(p.price) if p.price else None,
            'product_type': p.product_type,
        }
        for p in products
    ])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_product_from_woocommerce(request, product_id):
    """Pull latest product data from WooCommerce and update local DB."""
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

    if not product.woo_product_id:
        return Response({'error': 'No WooCommerce ID'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        wc_product = wc.get_product(product.woo_product_id)
        if not wc_product:
            return Response({'error': 'Product not found in WooCommerce'}, status=status.HTTP_404_NOT_FOUND)

        # Normalize Woo product type to local enum before any branching.
        wc_type_raw = (wc_product.get('type') or '').strip().lower()
        product_type_map = {
            'variable-subscription': 'variable_subscription',
            'variable_subscription': 'variable_subscription',
            'variable': 'variable',
            'subscription': 'subscription',
            'simple': 'simple',
            'bundle': 'bundle',
            'grouped': 'grouped',
        }
        normalized_product_type = product_type_map.get(wc_type_raw, product.product_type)

        product.name = wc_product.get('name', product.name)
        product.description = wc_product.get('description', product.description)
        product.short_description = wc_product.get('short_description', '')
        product.price = wc_product.get('price') or None
        product.regular_price = wc_product.get('regular_price') or None
        product.sale_price = wc_product.get('sale_price') or None
        product.status = wc_product.get('status', product.status)
        product.stock_status = wc_product.get('stock_status', product.stock_status)
        product.stock_quantity = wc_product.get('stock_quantity')
        product.tax_status = wc_product.get('tax_status', 'taxable')
        product.tax_class = wc_product.get('tax_class', '')
        product.manage_stock = wc_product.get('manage_stock', False)
        product.backorders = wc_product.get('backorders', 'no')
        product.sold_individually = wc_product.get('sold_individually', False)
        product.purchase_note = wc_product.get('purchase_note', '')
        product.menu_order = wc_product.get('menu_order', 0)
        product.reviews_allowed = wc_product.get('reviews_allowed', True)
        product.low_stock_amount = wc_product.get('low_stock_amount')
        product.shipping_class = wc_product.get('shipping_class', '')
        product.upsell_ids = wc_product.get('upsell_ids', [])
        product.cross_sell_ids = wc_product.get('cross_sell_ids', [])
        product.tags = [t.get('name', '') for t in wc_product.get('tags', [])]
        product.categories = [c.get('name', '') for c in wc_product.get('categories', [])]
        product.images = wc_product.get('images', [])
        product.product_type = normalized_product_type
        product.woo_data = wc_product
        product.save()

        # Keep ProductSimple in sync for simple/subscription and for variable parent SKU metadata.
        if normalized_product_type in ('simple', 'subscription', 'variable', 'variable_subscription'):
            simple, _ = ProductSimple.objects.get_or_create(product=product)
            simple.sku = wc_product.get('sku', '')
            simple.weight = wc_product.get('weight') or None
            dims = wc_product.get('dimensions', {})
            simple.length = dims.get('length') or None
            simple.width = dims.get('width') or None
            simple.height = dims.get('height') or None
            simple.is_virtual = wc_product.get('virtual', False)
            simple.is_downloadable = wc_product.get('downloadable', False)
            simple.woo_data = wc_product
            simple.save()

        if normalized_product_type in ('variable', 'variable_subscription'):
            wc_variations = wc.get_product_variations(product.woo_product_id)
            if wc_variations:
                synced_variation_ids = set()
                for wc_var in wc_variations:
                    variation_woo_id = wc_var['id']
                    synced_variation_ids.add(variation_woo_id)
                    ProductVariation.objects.update_or_create(
                        product=product,
                        woo_variation_id=variation_woo_id,
                        defaults={
                            'sku': wc_var.get('sku', ''),
                            'price': wc_var.get('price') or None,
                            'regular_price': wc_var.get('regular_price') or None,
                            'sale_price': wc_var.get('sale_price') or None,
                            'stock_quantity': wc_var.get('stock_quantity'),
                            'menu_order': wc_var.get('menu_order', 0),
                            'attributes': wc_var.get('attributes', []),
                            'woo_data': wc_var,
                        }
                    )

                # Remove stale local variations that no longer exist in Woo.
                ProductVariation.objects.filter(product=product).exclude(
                    woo_variation_id__in=synced_variation_ids
                ).delete()

        serializer = ProductFullSerializer(product)
        return Response({
            'success': True,
            'message': 'Product synced from WooCommerce',
            'product': serializer.data,
        })
    except Exception as e:
        logger.error(f"[ProductEditor] Sync from WC error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------------------------------
# Helper: sync product data to WooCommerce
# ---------------------------------------------------------------------------

def _sync_product_to_woocommerce(product, data):
    """Push product changes to WooCommerce. Returns sync result dict."""
    if not product.woo_product_id:
        return {'success': False, 'message': 'No WooCommerce product ID'}

    woo_update = {}

    field_map = {
        'name': 'name',
        'description': 'description',
        'short_description': 'short_description',
        'status': 'status',
        'tax_status': 'tax_status',
        'tax_class': 'tax_class',
        'manage_stock': 'manage_stock',
        'backorders': 'backorders',
        'sold_individually': 'sold_individually',
        'purchase_note': 'purchase_note',
        'menu_order': 'menu_order',
        'reviews_allowed': 'reviews_allowed',
        'shipping_class': 'shipping_class',
    }
    for local_key, wc_key in field_map.items():
        if local_key in data:
            woo_update[wc_key] = data[local_key]

    for pf in ('regular_price', 'sale_price'):
        if pf in data:
            woo_update[pf] = str(data[pf]) if data[pf] not in (None, '', 'null') else ''

    if 'stock_quantity' in data:
        woo_update['stock_quantity'] = data['stock_quantity']

    if 'date_on_sale_from' in data:
        woo_update['date_on_sale_from'] = data['date_on_sale_from'] or ''
    if 'date_on_sale_to' in data:
        woo_update['date_on_sale_to'] = data['date_on_sale_to'] or ''

    if 'categories' in data and data['categories']:
        woo_update['categories'] = _resolve_wc_categories(data['categories'])

    if 'tags' in data and data['tags']:
        woo_update['tags'] = [{'name': t} for t in data['tags']]

    if 'upsell_ids' in data:
        woo_update['upsell_ids'] = data['upsell_ids']
    if 'cross_sell_ids' in data:
        woo_update['cross_sell_ids'] = data['cross_sell_ids']

    if 'images' in data:
        woo_update['images'] = data['images']

    if 'attributes' in data:
        woo_update['attributes'] = data['attributes']

    if 'simple_details' in data and data['simple_details']:
        sd = data['simple_details']
        if 'weight' in sd:
            woo_update['weight'] = str(sd['weight']) if sd['weight'] else ''
        dims = {}
        for d in ('length', 'width', 'height'):
            if d in sd:
                dims[d] = str(sd[d]) if sd[d] else ''
        if dims:
            woo_update['dimensions'] = dims

    if not woo_update:
        return {'success': True, 'message': 'No changes to sync'}

    try:
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        resp = wc.wcapi.put(f"products/{product.woo_product_id}", woo_update)
        if resp.status_code == 200:
            return {'success': True, 'message': 'Synced to WooCommerce'}
        else:
            logger.error(f"[ProductEditor] WC sync failed: {resp.status_code} - {resp.text[:500]}")
            return {'success': False, 'message': f'WC error {resp.status_code}'}
    except Exception as e:
        logger.error(f"[ProductEditor] WC sync exception: {e}")
        return {'success': False, 'message': str(e)}


def _resolve_wc_categories(category_names):
    """Try to resolve category names to WC category IDs via search."""
    categories = []
    try:
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        for name in category_names:
            resp = wc.wcapi.get("products/categories", params={'search': name, 'per_page': 1})
            if resp.status_code == 200 and resp.json():
                categories.append({'id': resp.json()[0]['id']})
            else:
                categories.append({'name': name})
    except Exception:
        categories = [{'name': n} for n in category_names]
    return categories
