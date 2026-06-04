"""
Server-side reporting API endpoints.

All aggregation happens in Django ORM — no client-side processing needed.
Endpoints are cached via Redis with short TTLs for near-real-time freshness.
"""

import logging
from datetime import timedelta, datetime
from decimal import Decimal

from django.core.cache import cache
from django.db.models import (
    Sum, Count, Avg, Q, F, Value, CharField, IntegerField, DecimalField,
    Case, When, Subquery, OuterRef, Min, Max,
)
from django.db.models.functions import (
    TruncDate, TruncWeek, TruncMonth, TruncHour, ExtractHour, ExtractWeekDay,
    Coalesce, Concat,
)
from django.utils import timezone
from django.utils.dateparse import parse_date

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status as http_status

from .models import (
    POSOrder, POSOrderItem, POSOrderRefund, POSOrderRefundItem,
    Contact, Product, POSLocation,
    CreditServicePoints, WooCreditedService,
)
from payments.models import PaymentTransaction

logger = logging.getLogger(__name__)

CACHE_TTL_SHORT = 300   # 5 min for KPIs
CACHE_TTL_MEDIUM = 900  # 15 min for heavy aggregations


# ─── Helpers ────────────────────────────────────────────────────────────────

def _parse_dates(request):
    """Parse and validate start_date / end_date from query params."""
    start_str = request.query_params.get('start_date')
    end_str = request.query_params.get('end_date')

    if not start_str or not end_str:
        return None, None, 'start_date and end_date are required'

    start = parse_date(start_str)
    end = parse_date(end_str)
    if not start or not end:
        return None, None, 'Invalid date format. Use YYYY-MM-DD'

    if (end - start).days > 366:
        return None, None, 'Date range cannot exceed 1 year'

    # Make end date inclusive (end of day)
    end_dt = timezone.make_aware(datetime.combine(end, datetime.max.time()))
    start_dt = timezone.make_aware(datetime.combine(start, datetime.min.time()))
    return start_dt, end_dt, None


def _base_order_qs(start_dt, end_dt, request):
    """Return a base POSOrder queryset filtered by date + optional location/cashier."""
    qs = POSOrder.objects.filter(
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    ).exclude(status__in=['trash', 'cancelled', 'draft', 'failed'])

    location = request.query_params.get('location')
    if location:
        qs = qs.filter(assigned_location=location)

    cashier = request.query_params.get('cashier')
    if cashier:
        qs = qs.filter(
            Q(metadata__created_by__icontains=cashier) |
            Q(notes__icontains=cashier)
        )

    return qs


def _decimal(val):
    """Safely convert to 2-decimal string."""
    if val is None:
        return '0.00'
    return f'{Decimal(str(val)):.2f}'


def _cache_key(prefix, request):
    """Build a deterministic cache key from request params."""
    params = sorted(request.query_params.items())
    param_str = '&'.join(f'{k}={v}' for k, v in params)
    return f'report:{prefix}:{param_str}'


def _get_woo_created_by(woo_order_ids):
    """Batch-fetch _created_by meta from WooCommerce HPOS tables for a list of order IDs.
    Returns dict of {woo_order_id: email_string}.
    """
    if not woo_order_ids:
        return {}

    result = {}
    try:
        from .views_membership_direct import membership_manager
        import pymysql

        int_ids = []
        for wid in woo_order_ids:
            try:
                int_ids.append(int(wid))
            except (ValueError, TypeError):
                pass

        if not int_ids:
            return {}

        with membership_manager.get_database_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                placeholders = ','.join(['%s'] * len(int_ids))
                cursor.execute(f"""
                    SELECT order_id, meta_value
                    FROM wp_wc_orders_meta
                    WHERE meta_key = '_created_by'
                    AND order_id IN ({placeholders})
                """, int_ids)
                rows = cursor.fetchall()
                for row in rows:
                    result[row['order_id']] = row['meta_value'] or ''

    except Exception as e:
        logger.warning(f"Failed to fetch _created_by from WooCommerce: {e}")

    return result


def _get_woo_orders_with_created_by(start_dt, end_dt):
    """Fetch WooCommerce orders with _created_by meta for team performance.
    Uses HPOS tables (wp_wc_orders + wp_wc_orders_meta).
    POS orders have _order_source='DS POS', others are 'Online Store'.
    """
    results = []
    try:
        from .views_membership_direct import membership_manager
        import pymysql

        with membership_manager.get_database_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT 
                        o.id as order_id,
                        o.date_created_gmt as order_date,
                        o.total_amount as total,
                        m_created.meta_value as created_by,
                        m_source.meta_value as source
                    FROM wp_wc_orders o
                    LEFT JOIN wp_wc_orders_meta m_created
                        ON o.id = m_created.order_id AND m_created.meta_key = '_created_by'
                    LEFT JOIN wp_wc_orders_meta m_source
                        ON o.id = m_source.order_id AND m_source.meta_key = '_order_source'
                    WHERE o.type = 'shop_order'
                    AND o.status IN ('wc-completed', 'wc-processing', 'wc-on-hold')
                    AND o.date_created_gmt >= %s
                    AND o.date_created_gmt <= %s
                    ORDER BY o.date_created_gmt DESC
                """, [start_dt.strftime('%Y-%m-%d %H:%M:%S'), end_dt.strftime('%Y-%m-%d %H:%M:%S')])
                rows = cursor.fetchall()
                for row in rows:
                    total_val = Decimal(str(row['total'] or 0))
                    created_by = row['created_by'] or ''
                    source = row['source'] or ''
                    is_pos = bool(source and 'pos' in source.lower())
                    results.append({
                        'order_id': row['order_id'],
                        'date': row['order_date'],
                        'total': total_val,
                        'created_by': created_by,
                        'source': 'DS POS' if is_pos else 'WooCommerce',
                    })
    except Exception as e:
        logger.warning(f"Failed to fetch WooCommerce orders for team performance: {e}")

    return results


# ─── 1.1 Executive KPIs ────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_kpis(request):
    """Executive KPI summary with period-over-period comparison."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('kpis', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    def _compute_kpis(qs):
        agg = qs.aggregate(
            net_sales=Coalesce(Sum('total'), Decimal('0')),
            total_orders=Count('id'),
            avg_order_value=Coalesce(Avg('total'), Decimal('0')),
        )
        units = POSOrderItem.objects.filter(order__in=qs).aggregate(
            total_units=Coalesce(Sum('quantity'), 0),
        )
        refund_qs = POSOrderRefund.objects.filter(
            order__in=qs, status='completed',
        )
        refund_agg = refund_qs.aggregate(
            refund_total=Coalesce(Sum('refund_amount'), Decimal('0')),
            refund_count=Count('id'),
        )
        customer_ids = qs.exclude(contact__isnull=True).values_list('contact_id', flat=True).distinct()
        unique_customers = customer_ids.count()

        # New vs returning: customers whose first-ever order is within this period
        first_order_in_period = Contact.objects.filter(
            id__in=customer_ids,
        ).annotate(
            first_order_date=Min('pos_orders__created_at'),
        ).filter(
            first_order_date__gte=qs.query.where.children[0].rhs if hasattr(qs.query, 'where') else start_dt,
        )
        # Simpler approach: count contacts whose earliest POS order is in this range
        new_customers = 0
        for cid in customer_ids:
            earliest = POSOrder.objects.filter(
                contact_id=cid
            ).exclude(status__in=['trash', 'cancelled', 'draft', 'failed']).order_by('created_at').values_list('created_at', flat=True).first()
            if earliest and earliest >= start_dt:
                new_customers += 1

        net_sales = agg['net_sales'] or Decimal('0')
        total_orders = agg['total_orders'] or 0
        refund_total = refund_agg['refund_total'] or Decimal('0')
        refund_count = refund_agg['refund_count'] or 0

        return {
            'net_sales': _decimal(net_sales),
            'total_orders': total_orders,
            'avg_order_value': _decimal(agg['avg_order_value']),
            'units_sold': units['total_units'] or 0,
            'refund_total': _decimal(refund_total),
            'refund_count': refund_count,
            'refund_rate': _decimal((refund_total / net_sales * 100) if net_sales else 0),
            'unique_customers': unique_customers,
            'new_customers': new_customers,
            'returning_customers': unique_customers - new_customers,
        }

    # Current period
    current_qs = _base_order_qs(start_dt, end_dt, request)
    current = _compute_kpis(current_qs)

    # Previous period (same duration, immediately before)
    duration = end_dt - start_dt
    prev_start = start_dt - duration
    prev_end = start_dt - timedelta(seconds=1)
    prev_qs = _base_order_qs(prev_start, prev_end, request)
    previous = _compute_kpis(prev_qs)

    # Compute deltas
    def _delta(curr_val, prev_val):
        c = Decimal(str(curr_val).replace(',', ''))
        p = Decimal(str(prev_val).replace(',', ''))
        if p == 0:
            return '0.00' if c == 0 else '100.00'
        return _decimal(((c - p) / p) * 100)

    comparison = {
        'net_sales_delta': _delta(current['net_sales'], previous['net_sales']),
        'total_orders_delta': _delta(current['total_orders'], previous['total_orders']),
        'avg_order_value_delta': _delta(current['avg_order_value'], previous['avg_order_value']),
        'unique_customers_delta': _delta(current['unique_customers'], previous['unique_customers']),
    }

    result = {
        'current': current,
        'previous': previous,
        'comparison': comparison,
    }
    cache.set(ck, result, CACHE_TTL_SHORT)
    return Response(result)


# ─── 1.2 Sales Over Time ───────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_sales_over_time(request):
    """Sales aggregated by time period (hour/day/week/month)."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('sales_time', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    granularity = request.query_params.get('granularity', 'day')
    trunc_map = {
        'hour': TruncHour,
        'day': TruncDate,
        'week': TruncWeek,
        'month': TruncMonth,
    }
    trunc_fn = trunc_map.get(granularity, TruncDate)

    qs = _base_order_qs(start_dt, end_dt, request)
    data = (
        qs.annotate(period=trunc_fn('created_at'))
        .values('period')
        .annotate(
            net_sales=Coalesce(Sum('total'), Decimal('0')),
            orders=Count('id'),
            units=Coalesce(
                Sum(Subquery(
                    POSOrderItem.objects.filter(order=OuterRef('pk'))
                    .values('order')
                    .annotate(s=Sum('quantity'))
                    .values('s')[:1]
                )), 0
            ),
        )
        .order_by('period')
    )

    result = []
    for row in data:
        period_val = row['period']
        if hasattr(period_val, 'isoformat'):
            period_str = period_val.isoformat()
        else:
            period_str = str(period_val)
        orders = row['orders'] or 0
        net = row['net_sales'] or Decimal('0')
        result.append({
            'period': period_str,
            'net_sales': _decimal(net),
            'orders': orders,
            'units': row['units'] or 0,
            'aov': _decimal(net / orders if orders else 0),
        })

    cache.set(ck, result, CACHE_TTL_SHORT)
    return Response(result)


# ─── 1.3 Sales by Location ─────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_sales_by_location(request):
    """Sales breakdown by assigned location."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('sales_loc', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    qs = _base_order_qs(start_dt, end_dt, request).exclude(
        Q(assigned_location__isnull=True) | Q(assigned_location='')
    )

    data = (
        qs.values('assigned_location')
        .annotate(
            net_sales=Coalesce(Sum('total'), Decimal('0')),
            orders=Count('id'),
            avg_order_value=Coalesce(Avg('total'), Decimal('0')),
        )
        .order_by('-net_sales')
    )

    # Also get refund totals per location
    refund_data = (
        POSOrderRefund.objects.filter(
            order__in=_base_order_qs(start_dt, end_dt, request),
            status='completed',
        )
        .exclude(Q(assigned_location__isnull=True) | Q(assigned_location=''))
        .values('assigned_location')
        .annotate(refund_total=Coalesce(Sum('refund_amount'), Decimal('0')))
    )
    refund_map = {r['assigned_location']: r['refund_total'] for r in refund_data}

    result = []
    for row in data:
        loc = row['assigned_location']
        result.append({
            'location': loc,
            'net_sales': _decimal(row['net_sales']),
            'orders': row['orders'],
            'aov': _decimal(row['avg_order_value']),
            'refunds': _decimal(refund_map.get(loc, 0)),
        })

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.4 Sales by Cashier ──────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_sales_by_cashier(request):
    """Sales breakdown by cashier/sales manager."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('sales_cashier', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    qs = _base_order_qs(start_dt, end_dt, request)

    # Get team member (cashier) from WooCommerce _created_by meta
    # POS orders have woo_order_id in metadata — we query WooCommerce for _created_by
    import json as json_mod
    cashier_map = {}
    woo_id_to_order = {}  # woo_order_id -> list of (order_id, total)
    orders_without_woo = []  # orders that don't have a woo_order_id

    for order in qs.values('id', 'total', 'metadata', 'notes'):
        meta = order['metadata'] or {}
        if isinstance(meta, str):
            try:
                meta = json_mod.loads(meta)
            except (ValueError, TypeError):
                meta = {}

        woo_id = meta.get('woo_order_id') if isinstance(meta, dict) else None
        # Check metadata for created_by first (future orders will have this)
        local_cashier = ''
        if isinstance(meta, dict):
            local_cashier = meta.get('created_by', '')

        if local_cashier:
            if local_cashier not in cashier_map:
                cashier_map[local_cashier] = {'net_sales': Decimal('0'), 'orders': 0, 'order_ids': []}
            cashier_map[local_cashier]['net_sales'] += Decimal(str(order['total'] or 0))
            cashier_map[local_cashier]['orders'] += 1
            cashier_map[local_cashier]['order_ids'].append(order['id'])
        elif woo_id:
            if woo_id not in woo_id_to_order:
                woo_id_to_order[woo_id] = []
            woo_id_to_order[woo_id].append(order)
        else:
            orders_without_woo.append(order)

    # Batch query WooCommerce for _created_by meta
    if woo_id_to_order:
        woo_cashier_map = _get_woo_created_by(list(woo_id_to_order.keys()))
        for woo_id, order_list in woo_id_to_order.items():
            cashier = woo_cashier_map.get(int(woo_id) if str(woo_id).isdigit() else woo_id, 'Unknown')
            if not cashier:
                cashier = 'Unknown'
            for order in order_list:
                if cashier not in cashier_map:
                    cashier_map[cashier] = {'net_sales': Decimal('0'), 'orders': 0, 'order_ids': []}
                cashier_map[cashier]['net_sales'] += Decimal(str(order['total'] or 0))
                cashier_map[cashier]['orders'] += 1
                cashier_map[cashier]['order_ids'].append(order['id'])

    # Handle orders without woo_order_id
    for order in orders_without_woo:
        cashier = 'Unknown'
        if cashier not in cashier_map:
            cashier_map[cashier] = {'net_sales': Decimal('0'), 'orders': 0, 'order_ids': []}
        cashier_map[cashier]['net_sales'] += Decimal(str(order['total'] or 0))
        cashier_map[cashier]['orders'] += 1
        cashier_map[cashier]['order_ids'].append(order['id'])

    # Get discount totals per cashier
    result = []
    for cashier, data in sorted(cashier_map.items(), key=lambda x: x[1]['net_sales'], reverse=True):
        discount_total = POSOrderItem.objects.filter(
            order_id__in=data['order_ids'],
        ).aggregate(
            total_discount=Coalesce(Sum('discount_amount'), Decimal('0'))
        )['total_discount']

        refund_count = POSOrderRefund.objects.filter(
            order_id__in=data['order_ids'],
            status='completed',
        ).count()

        orders = data['orders']
        net = data['net_sales']
        result.append({
            'cashier': cashier,
            'net_sales': _decimal(net),
            'orders': orders,
            'aov': _decimal(net / orders if orders else 0),
            'discount_total': _decimal(discount_total),
            'refund_count': refund_count,
        })

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.5 Sales by Payment Method ───────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_sales_by_payment_method(request):
    """Sales breakdown by payment method."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('sales_payment', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    qs = _base_order_qs(start_dt, end_dt, request)

    # Normalization helper
    def _normalize_method(raw):
        name_map = {
            'credit card': 'Credit Card',
            'credit': 'Credit Card',
            'cash': 'Cash',
            'cod': 'Cash',
            'check': 'Check',
            'cheque': 'Check',
            'points': 'Points Redemption',
            'points redemption': 'Points Redemption',
            'other': 'Other',
            'outside_pos': 'Outside POS',
            'outside pos': 'Outside POS',
        }
        return name_map.get(raw.strip().lower(), raw.strip() or 'Other')

    def _type_to_name(method_type):
        """Map split-payment component type to display name."""
        type_map = {
            'credit': 'Credit Card',
            'debit': 'Debit Card',
            'cash': 'Cash',
            'check': 'Check',
            'points': 'Points Redemption',
            'care_credit': 'Care Credit',
            'paypal': 'PayPal',
            'bank_transfer': 'Bank Transfer',
        }
        return type_map.get(method_type, 'Other')

    method_map = {}

    def _add(name, amount, order_fraction=1):
        if name not in method_map:
            method_map[name] = {'net_sales': Decimal('0'), 'orders': 0}
        method_map[name]['net_sales'] += Decimal(str(amount))
        method_map[name]['orders'] += order_fraction

    # --- Non-split orders: aggregate normally ---
    non_split_qs = qs.exclude(
        Q(payment_method_title__isnull=True) | Q(payment_method_title='')
    ).exclude(payment_method_title__icontains='split')

    non_split_data = (
        non_split_qs
        .values('payment_method_title')
        .annotate(
            net_sales=Coalesce(Sum('total'), Decimal('0')),
            orders=Count('id'),
        )
        .order_by('-net_sales')
    )
    for row in non_split_data:
        name = _normalize_method(row['payment_method_title'] or 'Other')
        _add(name, row['net_sales'] or 0, row['orders'] or 0)

    # --- Split orders: decompose into component payment methods ---
    import json as _json
    split_orders = qs.filter(payment_method_title__icontains='split').values_list(
        'payment_method', 'total'
    )
    for pm_raw, order_total in split_orders:
        decomposed = False
        try:
            pm_data = pm_raw if isinstance(pm_raw, dict) else _json.loads(pm_raw) if pm_raw else {}
            split_payments = pm_data.get('splitPayments', [])
            if split_payments:
                n_methods = len(split_payments)
                for sp in split_payments:
                    method = sp.get('method', {})
                    amount = sp.get('amount', 0)
                    method_type = method.get('type', 'other')
                    name = _type_to_name(method_type)
                    # Each component gets a fractional order count so total orders stays correct
                    _add(name, amount, 1 / n_methods if n_methods else 1)
                decomposed = True
        except (TypeError, ValueError, _json.JSONDecodeError):
            pass
        if not decomposed:
            _add('Other', order_total or 0, 1)

    # --- Orders with empty payment_method_title ---
    empty_count = qs.filter(
        Q(payment_method_title__isnull=True) | Q(payment_method_title='')
    ).aggregate(
        net_sales=Coalesce(Sum('total'), Decimal('0')),
        orders=Count('id'),
    )
    if empty_count['orders']:
        _add('Other', empty_count['net_sales'] or 0, empty_count['orders'] or 0)

    # Round fractional order counts to nearest integer
    for v in method_map.values():
        v['orders'] = round(v['orders'])

    result = [
        {
            'method': name,
            'net_sales': _decimal(d['net_sales']),
            'orders': d['orders'],
        }
        for name, d in sorted(method_map.items(), key=lambda x: x[1]['net_sales'], reverse=True)
    ]

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.6 Product Performance ───────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_products(request):
    """Product performance ranked by revenue."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('products', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    order_qs = _base_order_qs(start_dt, end_dt, request)
    limit = int(request.query_params.get('limit', 50))
    sort_by = request.query_params.get('sort_by', 'revenue')

    items_qs = POSOrderItem.objects.filter(order__in=order_qs)

    category_filter = request.query_params.get('category')
    if category_filter:
        items_qs = items_qs.filter(categories__contains=[{'name': category_filter}])

    brand_filter = request.query_params.get('brand')
    if brand_filter:
        items_qs = items_qs.filter(
            Q(metadata__brand__icontains=brand_filter) |
            Q(name__icontains=brand_filter)
        )

    data = (
        items_qs.values('product_id', 'name')
        .annotate(
            revenue=Coalesce(Sum('subtotal'), Decimal('0')),
            units=Coalesce(Sum('quantity'), 0),
            orders=Count('order_id', distinct=True),
            discount_total=Coalesce(Sum('discount_amount'), Decimal('0')),
        )
    )

    sort_map = {
        'revenue': '-revenue',
        'units': '-units',
        'orders': '-orders',
    }
    data = data.order_by(sort_map.get(sort_by, '-revenue'))[:limit]

    result = []
    for row in data:
        units = row['units'] or 0
        revenue = row['revenue'] or Decimal('0')
        orders = row['orders'] or 0

        # Try to get category and brand from Product model
        cat = ''
        brand = ''
        try:
            product = Product.objects.filter(
                Q(id=row['product_id']) | Q(woo_product_id=row['product_id'])
            ).first()
            if product:
                cats = product.categories or []
                if cats and isinstance(cats, list) and len(cats) > 0:
                    cat = cats[0].get('name', '') if isinstance(cats[0], dict) else str(cats[0])
        except Exception:
            pass

        result.append({
            'product_id': str(row['product_id']),
            'name': row['name'],
            'category': cat,
            'brand': brand,
            'revenue': _decimal(revenue),
            'units': units,
            'orders': orders,
            'aov': _decimal(revenue / orders if orders else 0),
            'discount_total': _decimal(row['discount_total']),
        })

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.7 Category Performance ──────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_categories(request):
    """Category-level performance summary."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('categories', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    order_qs = _base_order_qs(start_dt, end_dt, request)
    items = POSOrderItem.objects.filter(order__in=order_qs).select_related('order')

    cat_map = {}
    for item in items.iterator():
        cats = item.categories or []
        cat_name = 'Uncategorized'
        if cats and isinstance(cats, list) and len(cats) > 0:
            first = cats[0]
            if isinstance(first, dict):
                cat_name = first.get('name', 'Uncategorized')
            elif isinstance(first, str):
                cat_name = first

        if cat_name not in cat_map:
            cat_map[cat_name] = {'revenue': Decimal('0'), 'units': 0, 'orders': set(), 'products': set()}
        cat_map[cat_name]['revenue'] += item.subtotal or Decimal('0')
        cat_map[cat_name]['units'] += item.quantity or 0
        cat_map[cat_name]['orders'].add(str(item.order_id))
        cat_map[cat_name]['products'].add(item.product_id)

    result = [
        {
            'category': name,
            'revenue': _decimal(d['revenue']),
            'units': d['units'],
            'orders': len(d['orders']),
            'product_count': len(d['products']),
        }
        for name, d in sorted(cat_map.items(), key=lambda x: x[1]['revenue'], reverse=True)
    ]

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.8 Customer Analytics ────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_customers(request):
    """Customer analytics: segments, top customers, new vs returning."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('customers', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    order_qs = _base_order_qs(start_dt, end_dt, request).exclude(contact__isnull=True)
    limit = int(request.query_params.get('limit', 20))

    # Top customers
    top = (
        order_qs.values('contact_id', 'contact__first_name', 'contact__last_name', 'contact__email')
        .annotate(
            total_spend=Coalesce(Sum('total'), Decimal('0')),
            order_count=Count('id'),
            first_order=Min('created_at'),
            last_order=Max('created_at'),
        )
        .order_by('-total_spend')[:limit]
    )

    top_customers = []
    for row in top:
        oc = row['order_count'] or 0
        ts = row['total_spend'] or Decimal('0')
        top_customers.append({
            'id': str(row['contact_id']),
            'name': f"{row['contact__first_name'] or ''} {row['contact__last_name'] or ''}".strip() or 'Unknown',
            'email': row['contact__email'] or '',
            'total_spend': _decimal(ts),
            'order_count': oc,
            'aov': _decimal(ts / oc if oc else 0),
            'first_order': row['first_order'].isoformat() if row['first_order'] else None,
            'last_order': row['last_order'].isoformat() if row['last_order'] else None,
        })

    # Segments based on order count in period
    all_customers = (
        order_qs.values('contact_id')
        .annotate(order_count=Count('id'))
    )
    segments = {'New': 0, 'Returning': 0, 'Frequent': 0, 'VIP': 0}
    segment_revenue = {'New': Decimal('0'), 'Returning': Decimal('0'), 'Frequent': Decimal('0'), 'VIP': Decimal('0')}

    for c in all_customers:
        oc = c['order_count']
        # Check if this is a truly new customer (first order ever is in this period)
        earliest = POSOrder.objects.filter(
            contact_id=c['contact_id']
        ).exclude(status__in=['trash', 'cancelled', 'draft', 'failed']).order_by('created_at').values_list('created_at', flat=True).first()

        if earliest and earliest >= start_dt:
            seg = 'New'
        elif oc <= 2:
            seg = 'Returning'
        elif oc <= 5:
            seg = 'Frequent'
        else:
            seg = 'VIP'

        segments[seg] += 1
        # Get revenue for this customer in period
        rev = order_qs.filter(contact_id=c['contact_id']).aggregate(
            s=Coalesce(Sum('total'), Decimal('0'))
        )['s']
        segment_revenue[seg] += rev

    total_customers = Contact.objects.count()
    unique_in_period = all_customers.count()

    # New customers over time (by day)
    new_by_day = (
        order_qs.values('contact_id')
        .annotate(first_order=Min('created_at'))
        .filter(first_order__gte=start_dt)
        .annotate(day=TruncDate('first_order'))
        .values('day')
        .annotate(count=Count('contact_id', distinct=True))
        .order_by('day')
    )

    result = {
        'summary': {
            'total_customers': total_customers,
            'active_in_period': unique_in_period,
            'new': segments['New'],
            'returning': segments['Returning'] + segments['Frequent'] + segments['VIP'],
        },
        'top_customers': top_customers,
        'segments': [
            {'segment': seg, 'count': cnt, 'revenue': _decimal(segment_revenue[seg])}
            for seg, cnt in segments.items()
        ],
        'new_customers_by_day': [
            {'date': row['day'].isoformat() if row['day'] else '', 'count': row['count']}
            for row in new_by_day
        ],
    }

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.9 Refund Report ─────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_refunds(request):
    """Refund analytics with reason breakdown."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('refunds', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    refund_qs = POSOrderRefund.objects.filter(
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    )

    location = request.query_params.get('location')
    if location:
        refund_qs = refund_qs.filter(assigned_location=location)

    # Filter by gateway type (e.g., gateway=pos_authorize_net for credit card only)
    gateway_filter = request.query_params.get('gateway')
    if gateway_filter:
        refund_qs = refund_qs.filter(metadata__gateway_response__gateway=gateway_filter)

    # Summary
    total_orders_in_period = _base_order_qs(start_dt, end_dt, request).count()
    agg = refund_qs.aggregate(
        total_refunded=Coalesce(Sum('refund_amount'), Decimal('0')),
        refund_count=Count('id'),
    )
    refund_count = agg['refund_count'] or 0
    total_refunded = agg['total_refunded'] or Decimal('0')

    # Top reasons
    reasons = (
        POSOrderRefundItem.objects.filter(refund__in=refund_qs)
        .exclude(Q(refund_reason='') | Q(refund_reason__isnull=True))
        .values('refund_reason')
        .annotate(count=Count('id'), total=Coalesce(Sum('subtotal'), Decimal('0')))
        .order_by('-count')[:10]
    )

    # Recent refunds list
    recent = refund_qs.select_related('order').order_by('-created_at')[:50]
    refund_list = []
    for r in recent:
        refund_list.append({
            'refund_number': r.refund_number,
            'order_number': r.order.order_number if r.order else '',
            'amount': _decimal(r.refund_amount),
            'reason': r.refund_reason or '',
            'cashier': r.created_by or '',
            'location': r.assigned_location or '',
            'date': r.created_at.isoformat(),
            'status': r.status,
        })

    result = {
        'summary': {
            'total_refunded': _decimal(total_refunded),
            'refund_count': refund_count,
            'refund_rate': _decimal(
                (refund_count / total_orders_in_period * 100) if total_orders_in_period else 0
            ),
        },
        'top_reasons': [
            {'reason': r['refund_reason'], 'count': r['count'], 'total': _decimal(r['total'])}
            for r in reasons
        ],
        'refunds': refund_list,
    }

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.10 Payment Transactions ─────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_payments(request):
    """Payment transaction analytics (Authorize.Net)."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('payments', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    txn_qs = PaymentTransaction.objects.filter(
        created_at__gte=start_dt,
        created_at__lte=end_dt,
    )

    status_filter = request.query_params.get('status')
    if status_filter:
        txn_qs = txn_qs.filter(status=status_filter)

    # Summary by status
    by_status = txn_qs.values('status').annotate(
        count=Count('id'),
        total=Coalesce(Sum('amount'), Decimal('0')),
    )
    status_map = {r['status']: r for r in by_status}

    approved = status_map.get('approved', {'count': 0, 'total': Decimal('0')})
    declined = status_map.get('declined', {'count': 0, 'total': Decimal('0')})
    total_attempts = txn_qs.count()

    summary = {
        'approved_count': approved['count'],
        'approved_total': _decimal(approved['total']),
        'declined_count': declined['count'],
        'declined_total': _decimal(declined['total']),
        'total_attempts': total_attempts,
        'success_rate': _decimal(
            (approved['count'] / total_attempts * 100) if total_attempts else 0
        ),
    }

    # Decline reasons
    decline_reasons = (
        txn_qs.filter(status='declined')
        .exclude(Q(response_message='') | Q(response_message__isnull=True))
        .values('response_message')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    # Recent transactions — resolve POS order numbers to WooCommerce order IDs
    recent = txn_qs.order_by('-created_at')[:50]
    import json as json_mod
    pos_order_numbers = set(t.order_id for t in recent if t.order_id)
    woo_id_map = {}
    if pos_order_numbers:
        for o in POSOrder.objects.filter(order_number__in=pos_order_numbers).values('order_number', 'metadata'):
            meta = o['metadata'] or {}
            if isinstance(meta, str):
                try:
                    meta = json_mod.loads(meta)
                except (ValueError, TypeError):
                    meta = {}
            if isinstance(meta, dict):
                woo_id = meta.get('woo_order_id')
                if woo_id:
                    woo_id_map[o['order_number']] = str(woo_id)

    txn_list = [
        {
            'transaction_id': t.transaction_id,
            'order_id': woo_id_map.get(t.order_id, t.order_id) or '',
            'amount': _decimal(t.amount),
            'status': t.status,
            'payment_type': t.payment_type,
            'auth_code': t.auth_code or '',
            'response_code': t.response_code or '',
            'response_message': t.response_message or '',
            'created_at': t.created_at.isoformat(),
        }
        for t in recent
    ]

    result = {
        'summary': summary,
        'decline_reasons': [
            {'reason': r['response_message'], 'count': r['count']}
            for r in decline_reasons
        ],
        'transactions': txn_list,
    }

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.11 Subscription Analytics ───────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_subscriptions(request):
    """Subscription analytics from WooCommerce MariaDB."""
    ck = _cache_key('subscriptions', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    try:
        from .views_membership_direct import membership_manager
        import pymysql
        result_data = {'active_count': 0, 'total_count': 0, 'statuses': {}, 'mrr': Decimal('0')}

        with membership_manager.get_database_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # HPOS: subscriptions are in wp_wc_orders with type='shop_subscription'
                # Status is on the row itself (wc-active, wc-cancelled, etc.)
                cursor.execute("""
                    SELECT status, COUNT(*) as cnt
                    FROM wp_wc_orders
                    WHERE type = 'shop_subscription'
                    AND status != 'trash'
                    GROUP BY status
                """)
                rows = cursor.fetchall()
                for row in rows:
                    raw_status = row['status'] or 'unknown'
                    # Strip 'wc-' prefix for display
                    s = raw_status.replace('wc-', '') if raw_status.startswith('wc-') else raw_status
                    c = row['cnt'] or 0
                    result_data['statuses'][s] = c
                    result_data['total_count'] += c
                    if s == 'active':
                        result_data['active_count'] = c

                # MRR: sum of total_amount for active subscriptions
                # total_amount is the recurring charge per billing cycle
                # We normalize to monthly: total * (4 / interval) for weekly, etc.
                cursor.execute("""
                    SELECT 
                        o.total_amount as total,
                        m_period.meta_value as billing_period,
                        m_interval.meta_value as billing_interval
                    FROM wp_wc_orders o
                    LEFT JOIN wp_wc_orders_meta m_period
                        ON o.id = m_period.order_id AND m_period.meta_key = '_billing_period'
                    LEFT JOIN wp_wc_orders_meta m_interval
                        ON o.id = m_interval.order_id AND m_interval.meta_key = '_billing_interval'
                    WHERE o.type = 'shop_subscription'
                    AND o.status = 'wc-active'
                """)
                sub_rows = cursor.fetchall()
                mrr = Decimal('0')
                for sr in sub_rows:
                    amt = Decimal(str(sr['total'] or 0))
                    period = sr['billing_period'] or 'month'
                    interval = int(sr['billing_interval'] or 1)
                    # Normalize to monthly
                    if period == 'week':
                        mrr += amt * Decimal('4.33') / Decimal(str(interval))
                    elif period == 'day':
                        mrr += amt * Decimal('30') / Decimal(str(interval))
                    elif period == 'year':
                        mrr += amt / (Decimal(str(interval)) * Decimal('12'))
                    else:  # month
                        mrr += amt / Decimal(str(interval))
                result_data['mrr'] = mrr

        result = {
            'active_count': result_data['active_count'],
            'total_count': result_data['total_count'],
            'mrr': _decimal(result_data['mrr']),
            'statuses': result_data['statuses'],
        }

        cache.set(ck, result, CACHE_TTL_MEDIUM)
        return Response(result)

    except Exception as e:
        logger.error(f"Error fetching subscription analytics: {e}")
        return Response({
            'active_count': 0,
            'total_count': 0,
            'mrr': '0.00',
            'statuses': {},
            'error': str(e),
        })


# ─── 1.12 Discount Analysis ────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_discounts(request):
    """Discount analysis by source and type."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('discounts', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    order_qs = _base_order_qs(start_dt, end_dt, request)
    items_qs = POSOrderItem.objects.filter(
        order__in=order_qs,
        discount_amount__gt=0,
    )

    # By source
    by_source = (
        items_qs.values('discount_source')
        .annotate(
            total_discount=Coalesce(Sum('discount_amount'), Decimal('0')),
            item_count=Count('id'),
            avg_discount=Coalesce(Avg('discount_amount'), Decimal('0')),
        )
        .order_by('-total_discount')
    )

    # By type
    by_type = (
        items_qs.values('discount_type')
        .annotate(
            total_discount=Coalesce(Sum('discount_amount'), Decimal('0')),
            item_count=Count('id'),
        )
        .order_by('-total_discount')
    )

    # Overall summary
    summary = items_qs.aggregate(
        total_discount=Coalesce(Sum('discount_amount'), Decimal('0')),
        total_items=Count('id'),
        total_orders=Count('order_id', distinct=True),
    )

    result = {
        'summary': {
            'total_discount': _decimal(summary['total_discount']),
            'total_items_discounted': summary['total_items'] or 0,
            'total_orders_with_discount': summary['total_orders'] or 0,
        },
        'by_source': [
            {
                'source': r['discount_source'] or 'Unknown',
                'total_discount': _decimal(r['total_discount']),
                'item_count': r['item_count'],
                'avg_discount': _decimal(r['avg_discount']),
            }
            for r in by_source
        ],
        'by_type': [
            {
                'type': r['discount_type'] or 'Unknown',
                'total_discount': _decimal(r['total_discount']),
                'item_count': r['item_count'],
            }
            for r in by_type
        ],
    }

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.13 Hourly Heatmap ───────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_hourly_heatmap(request):
    """7x24 heatmap: day-of-week × hour-of-day."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('heatmap', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    qs = _base_order_qs(start_dt, end_dt, request)

    data = (
        qs.annotate(
            dow=ExtractWeekDay('created_at'),  # 1=Sunday, 7=Saturday
            hour=ExtractHour('created_at'),
        )
        .values('dow', 'hour')
        .annotate(
            orders=Count('id'),
            revenue=Coalesce(Sum('total'), Decimal('0')),
        )
        .order_by('dow', 'hour')
    )

    # Build 7x24 matrix
    day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    matrix = []
    for row in data:
        dow_idx = (row['dow'] or 1) - 1  # 0-indexed
        matrix.append({
            'day': day_names[dow_idx] if 0 <= dow_idx < 7 else 'Unknown',
            'day_index': dow_idx,
            'hour': row['hour'],
            'orders': row['orders'] or 0,
            'revenue': _decimal(row['revenue']),
        })

    cache.set(ck, matrix, CACHE_TTL_MEDIUM)
    return Response(matrix)


# ─── 1.14 Reconciliation ───────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_reconciliation(request):
    """Data integrity / reconciliation checks."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('reconciliation', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    order_qs = _base_order_qs(start_dt, end_dt, request)

    # 1. Credit orders without transaction ID
    credit_orders = order_qs.filter(payment_method_title__icontains='credit')
    missing_txn = credit_orders.filter(
        Q(transaction_id='') | Q(transaction_id__isnull=True) | Q(transaction_id__startswith='ORD-')
    ).count()

    # 2. Orders missing WooCommerce sync
    import json as json_mod
    missing_woo = 0
    for order in order_qs.values('metadata'):
        meta = order['metadata'] or {}
        if isinstance(meta, str):
            try:
                meta = json_mod.loads(meta)
            except (ValueError, TypeError):
                meta = {}
        if isinstance(meta, dict) and not meta.get('woo_order_id'):
            missing_woo += 1

    # 3. PaymentTransactions without matching POS order
    txn_qs = PaymentTransaction.objects.filter(
        created_at__gte=start_dt,
        created_at__lte=end_dt,
        status='approved',
    )
    orphan_txns = 0
    for txn in txn_qs.values('order_id'):
        oid = txn['order_id'] or ''
        if not oid:
            orphan_txns += 1
        elif not POSOrder.objects.filter(order_number=oid).exists():
            orphan_txns += 1

    # 4. Line item totals vs order totals mismatch
    mismatches = 0
    for order in order_qs.prefetch_related('items'):
        items_total = sum(item.subtotal or Decimal('0') for item in order.items.all())
        if abs(items_total - (order.total or Decimal('0'))) > Decimal('1.00'):
            mismatches += 1

    result = {
        'credit_orders_missing_transaction': missing_txn,
        'orders_missing_woo_sync': missing_woo,
        'orphan_payment_transactions': orphan_txns,
        'line_item_total_mismatches': mismatches,
        'total_issues': missing_txn + missing_woo + orphan_txns + mismatches,
        'period': {
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat(),
        },
    }

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)


# ─── 1.15 Team Performance ─────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_team_performance(request):
    """Team member performance — POS orders attributed to team member,
    WooCommerce-only orders attributed to 'Online Store'."""
    start_dt, end_dt, err = _parse_dates(request)
    if err:
        return Response({'error': err}, status=http_status.HTTP_400_BAD_REQUEST)

    ck = _cache_key('team_perf', request)
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    woo_orders = _get_woo_orders_with_created_by(start_dt, end_dt)

    # Build email -> full name map from Django User model
    from django.contrib.auth.models import User
    email_to_name = {}
    for u in User.objects.all().only('email', 'first_name', 'last_name'):
        full = u.get_full_name().strip()
        if full:
            email_to_name[u.email.lower()] = full

    def _display_name(email):
        """Resolve email to full name, fallback to email prefix."""
        if not email or email == 'Online Store':
            return 'Online Store'
        name = email_to_name.get(email.lower(), '')
        if name:
            return name
        # Fallback: capitalize the part before @
        return email.split('@')[0].capitalize()

    team_map = {}
    for wo in woo_orders:
        created_by = wo['created_by']
        source = wo['source']
        total = wo['total']

        if source == 'DS POS' and created_by:
            member = created_by
        else:
            member = 'Online Store'

        if member not in team_map:
            team_map[member] = {
                'total_sales': Decimal('0'),
                'order_count': 0,
                'pos_sales': Decimal('0'),
                'pos_orders': 0,
                'woo_sales': Decimal('0'),
                'woo_orders': 0,
            }
        team_map[member]['total_sales'] += total
        team_map[member]['order_count'] += 1
        if source == 'DS POS':
            team_map[member]['pos_sales'] += total
            team_map[member]['pos_orders'] += 1
        else:
            team_map[member]['woo_sales'] += total
            team_map[member]['woo_orders'] += 1

    grand_total = sum(d['total_sales'] for d in team_map.values())
    result_list = []
    for member, data in sorted(team_map.items(), key=lambda x: x[1]['total_sales'], reverse=True):
        oc = data['order_count']
        ts = data['total_sales']
        result_list.append({
            'team_member': _display_name(member),
            'total_sales': _decimal(ts),
            'order_count': oc,
            'aov': _decimal(ts / oc if oc else 0),
            'pos_sales': _decimal(data['pos_sales']),
            'pos_orders': data['pos_orders'],
            'woo_sales': _decimal(data['woo_sales']),
            'woo_orders': data['woo_orders'],
            'share': _decimal((ts / grand_total * 100) if grand_total else 0),
        })

    team_result = {
        'team': result_list,
        'summary': {
            'total_sales': _decimal(grand_total),
            'total_orders': sum(d['order_count'] for d in team_map.values()),
            'team_members': len([m for m in team_map if m != 'Online Store']),
        },
    }

    cache.set(ck, team_result, CACHE_TTL_MEDIUM)
    return Response(team_result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def report_liabilities(request):
    """Credited-services liabilities: services paid for but not yet redeemed, grouped by product."""
    ck = 'report_liabilities'
    cached = cache.get(ck)
    if cached:
        return Response(cached)

    product_map = {}  # product_id -> { info + customers dict }

    # Primary source: CreditServicePoints (FK-based, preferred)
    csp_qs = (
        CreditServicePoints.objects
        .filter(points__gt=0)
        .select_related('customer', 'product')
    )
    seen_pairs = set()  # (customer_id, product_id) to avoid legacy duplicates

    for row in csp_qs:
        pid = str(row.product_id)
        cid = str(row.customer_id)
        seen_pairs.add((cid, pid))

        if pid not in product_map:
            product_map[pid] = {
                'product_name': row.product.name if row.product else pid,
                'product_id': pid,
                'total_points': 0,
                'customers': {},
            }
        product_map[pid]['total_points'] += row.points
        product_map[pid]['customers'][cid] = {
            'customer_id': cid,
            'customer_name': f"{row.customer.first_name or ''} {row.customer.last_name or ''}".strip() or 'Unknown',
            'email': row.customer.email or '',
            'phone': row.customer.phone or '',
            'points': row.points,
        }

    # Fallback: WooCreditedService for legacy rows not yet in CreditServicePoints
    legacy_qs = WooCreditedService.objects.filter(product_points__gt=0)
    for row in legacy_qs:
        pid = str(row.product_id)
        woo_id = str(row.contact_woo_id or '')
        # Try to resolve to a Contact to check against seen_pairs
        contact = None
        if woo_id:
            contact = Contact.objects.filter(woo_customer_id=woo_id).first()
        cid = str(contact.id) if contact else f"woo_{woo_id}"

        if (cid, pid) in seen_pairs:
            continue
        seen_pairs.add((cid, pid))

        if pid not in product_map:
            product_map[pid] = {
                'product_name': row.product_name or pid,
                'product_id': pid,
                'total_points': 0,
                'customers': {},
            }
        product_map[pid]['total_points'] += row.product_points
        product_map[pid]['customers'][cid] = {
            'customer_id': cid,
            'customer_name': f"{row.contact_fname or ''} {row.contact_lname or ''}".strip() or 'Unknown',
            'email': row.contact_email or '',
            'phone': row.contact_phone or '',
            'points': row.product_points,
        }

    products = []
    total_points = 0
    all_customer_ids = set()

    for pid, pdata in sorted(product_map.items(), key=lambda x: x[1]['total_points'], reverse=True):
        customers_list = sorted(pdata['customers'].values(), key=lambda c: c['points'], reverse=True)
        all_customer_ids.update(c['customer_id'] for c in customers_list)
        total_points += pdata['total_points']
        products.append({
            'product_name': pdata['product_name'],
            'product_id': pdata['product_id'],
            'total_points': pdata['total_points'],
            'customer_count': len(customers_list),
            'customers': customers_list,
        })

    result = {
        'summary': {
            'total_customers_with_credits': len(all_customer_ids),
            'total_outstanding_points': total_points,
            'total_products_with_credits': len(products),
        },
        'products': products,
    }

    cache.set(ck, result, CACHE_TTL_MEDIUM)
    return Response(result)
