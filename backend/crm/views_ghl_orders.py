"""
GHL Orders endpoints — list from local DB (Redis-cached) and lazy-sync from GHL API.
"""
import hashlib
import logging
import threading
from decimal import Decimal, InvalidOperation

import django.db
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .ghl_api import list_ghl_orders, get_ghl_order_by_id
from .models import GHLOrder, Contact

logger = logging.getLogger(__name__)

CACHE_TTL = 300          # 5 min — matches CACHE_TTL_SHORT elsewhere
SYNC_COOLDOWN = 300      # Don't re-sync more often than every 5 min
SYNC_RUNNING_TTL = 60    # Short-lived "sync in progress" flag
GHL_SYNC_PAGE_SIZE = 50  # How many orders to fetch per GHL API page

# Versioned cache prefix — increment to invalidate all list caches without
# touching unrelated cache keys (safe for both Redis and LocMemCache).
_CACHE_VERSION_KEY = 'ghl_orders:cache_version'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_cache_version():
    """Return the current cache version counter (int)."""
    v = cache.get(_CACHE_VERSION_KEY)
    if v is None:
        cache.set(_CACHE_VERSION_KEY, 1, None)
        return 1
    return int(v)


def _bump_cache_version():
    """Increment the version counter, which makes all old cache keys stale."""
    v = _get_cache_version() + 1
    cache.set(_CACHE_VERSION_KEY, v, None)
    logger.info(f"GHL orders cache version bumped to {v}")
    return v


def _cache_key(prefix, params_dict):
    """Deterministic cache key from a dict of query params + version."""
    version = _get_cache_version()
    raw = '&'.join(f'{k}={v}' for k, v in sorted(params_dict.items()) if v)
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f'ghl_orders:v{version}:{prefix}:{h}'


def _serialize_order(o):
    """Serialize a GHLOrder model instance to a dict matching the frontend contract."""
    return {
        'ghl_order_id': o.ghl_order_id,
        'alt_id': o.alt_id,
        'contact_id': o.ghl_contact_id,
        'contact_name': o.contact_name,
        'contact_email': o.contact_email,
        'currency': o.currency,
        'amount': str(o.amount),
        'subtotal': str(o.subtotal),
        'discount': str(o.discount),
        'status': o.status,
        'payment_status': o.payment_status,
        'fulfillment_status': o.fulfillment_status,
        'live_mode': o.live_mode,
        'total_products': o.total_products,
        'onetime_products': o.onetime_products,
        'source_type': o.source_type,
        'source_name': o.source_name,
        'source_id': o.source_id,
        'source_meta': o.source_meta,
        'ghl_created_at': o.ghl_created_at.isoformat() if o.ghl_created_at else None,
        'ghl_updated_at': o.ghl_updated_at.isoformat() if o.ghl_updated_at else None,
        'local_contact_id': str(o.contact.id) if o.contact else None,
        'source': 'GHL',
    }


def _resolve_contact(contact_id_ghl, email):
    """Try to find a local Contact by ghl_contact_id or email."""
    if contact_id_ghl:
        c = Contact.objects.filter(ghl_contact_id=contact_id_ghl).first()
        if c:
            return c
    if email:
        return Contact.objects.filter(email__iexact=email).first()
    return None


def _safe_decimal(val, default=0):
    try:
        return Decimal(str(val)) if val is not None else Decimal(default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _safe_datetime(val):
    """Parse an ISO datetime string, falling back to now() on failure."""
    if not val:
        return timezone.now()
    parsed = parse_datetime(str(val))
    if parsed is None:
        return timezone.now()
    return parsed


def _upsert_order(raw):
    """Upsert a single GHL order dict into the local GHLOrder table."""
    ghl_id = raw.get('_id')
    if not ghl_id:
        return None, False

    contact = _resolve_contact(raw.get('contactId'), raw.get('contactEmail'))

    defaults = {
        'alt_id': raw.get('altId', ''),
        'ghl_contact_id': raw.get('contactId', ''),
        'contact_name': raw.get('contactName', ''),
        'contact_email': raw.get('contactEmail', ''),
        'currency': raw.get('currency', 'USD'),
        'amount': _safe_decimal(raw.get('amount')),
        'subtotal': _safe_decimal(raw.get('subtotal')),
        'discount': _safe_decimal(raw.get('discount')),
        'status': raw.get('status', ''),
        'payment_status': raw.get('paymentStatus', ''),
        'fulfillment_status': raw.get('fulfillmentStatus', 'unfulfilled'),
        'live_mode': raw.get('liveMode', True),
        'total_products': raw.get('totalProducts', 0),
        'onetime_products': raw.get('onetimeProducts', 0),
        'source_type': raw.get('sourceType', ''),
        'source_name': raw.get('sourceName', ''),
        'source_id': raw.get('sourceId', ''),
        'source_meta': raw.get('sourceMeta', {}),
        'ghl_created_at': _safe_datetime(raw.get('createdAt')),
        'ghl_updated_at': _safe_datetime(raw.get('updatedAt')),
        'contact': contact,
    }

    obj, created = GHLOrder.objects.update_or_create(
        ghl_order_id=ghl_id,
        defaults=defaults,
    )
    return obj, created


# ---------------------------------------------------------------------------
# Sync logic (runs in background thread on page load)
# ---------------------------------------------------------------------------

def _do_sync(incremental=True):
    """Pull orders from GHL API and upsert into local DB."""
    last_sync_iso = cache.get('ghl_orders_last_sync_time')

    offset = 0
    total_synced = 0
    total_new = 0

    while True:
        kwargs = {
            'limit': GHL_SYNC_PAGE_SIZE,
            'offset': offset,
        }
        if incremental and last_sync_iso:
            kwargs['start_at'] = last_sync_iso

        result = list_ghl_orders(**kwargs)
        if not result or 'data' not in result:
            break

        orders = result['data']
        if not orders:
            break

        for raw_order in orders:
            try:
                _, created = _upsert_order(raw_order)
                total_synced += 1
                if created:
                    total_new += 1
            except Exception as e:
                logger.warning(f"Failed to upsert GHL order {raw_order.get('_id')}: {e}")

        if len(orders) < GHL_SYNC_PAGE_SIZE:
            break
        offset += GHL_SYNC_PAGE_SIZE

    now_iso = timezone.now().isoformat()
    cache.set('ghl_orders_last_sync_time', now_iso, 86400)

    _bump_cache_version()

    logger.info(
        f"GHL orders sync complete: {total_synced} processed, {total_new} new"
    )
    return {'synced': total_synced, 'new': total_new}


# ---------------------------------------------------------------------------
# API Views
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_ghl_orders(request):
    """List GHL orders from local DB with Redis caching."""
    page = int(request.query_params.get('page', 1))
    per_page = int(request.query_params.get('per_page', 10))
    status_filter = request.query_params.get('status')
    search = request.query_params.get('search')
    contact_email = request.query_params.get('contact_email')
    ghl_contact_id = request.query_params.get('contact_id')
    date_from = request.query_params.get('date_from')
    date_to = request.query_params.get('date_to')

    cache_params = {
        'page': page, 'per_page': per_page,
        'status': status_filter or '', 'search': search or '',
        'email': contact_email or '', 'ghl_cid': ghl_contact_id or '',
        'df': date_from or '', 'dt': date_to or '',
    }
    ck = _cache_key('list', cache_params)

    cached = cache.get(ck)
    if cached:
        return Response(cached)

    qs = GHLOrder.objects.select_related('contact').all()

    if status_filter:
        qs = qs.filter(status__iexact=status_filter)

    if search:
        qs = qs.filter(
            Q(contact_name__icontains=search) |
            Q(contact_email__icontains=search) |
            Q(source_name__icontains=search) |
            Q(ghl_order_id__icontains=search)
        )

    if contact_email:
        qs = qs.filter(contact_email__iexact=contact_email)

    if ghl_contact_id:
        qs = qs.filter(ghl_contact_id=ghl_contact_id)

    if date_from:
        qs = qs.filter(ghl_created_at__gte=date_from)
    if date_to:
        qs = qs.filter(ghl_created_at__lte=date_to)

    total = qs.count()
    offset = (page - 1) * per_page
    orders = qs[offset:offset + per_page]

    result = {
        'orders': [_serialize_order(o) for o in orders],
        'pagination': {
            'total': total,
            'total_pages': max(1, -(-total // per_page)),
            'current_page': page,
            'per_page': per_page,
        }
    }

    cache.set(ck, result, CACHE_TTL)
    return Response(result)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_ghl_order_detail(request, order_id):
    """Get a single GHL order — local DB first, then live API fallback."""
    local = GHLOrder.objects.select_related('contact').filter(ghl_order_id=order_id).first()
    if local:
        data = _serialize_order(local)
        live = get_ghl_order_by_id(order_id)
        if live:
            data['live_detail'] = live
        return Response(data)

    live = get_ghl_order_by_id(order_id)
    if live:
        return Response({'ghl_order_id': order_id, 'live_detail': live, 'source': 'GHL'})

    return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_ghl_orders(request):
    """
    Trigger a lazy sync of GHL orders.
    Deduplicates: skips if a sync is currently running or ran recently.
    Runs in a background thread so the response returns immediately.
    """
    if cache.get('ghl_orders_sync_running'):
        return Response({
            'status': 'skipped',
            'message': 'Sync is currently running',
        })

    last_sync = cache.get('ghl_orders_last_sync')
    if last_sync:
        return Response({
            'status': 'skipped',
            'message': 'Sync already ran recently',
            'last_sync': last_sync,
        })

    cache.set('ghl_orders_sync_running', True, SYNC_RUNNING_TTL)

    full_sync = request.data.get('full', False)

    def _background():
        try:
            django.db.close_old_connections()
            _do_sync(incremental=not full_sync)
            cache.set('ghl_orders_last_sync', timezone.now().isoformat(), SYNC_COOLDOWN)
        except Exception as e:
            logger.error(f"Background GHL order sync failed: {e}")
        finally:
            cache.delete('ghl_orders_sync_running')
            django.db.close_old_connections()

    thread = threading.Thread(target=_background, daemon=True)
    thread.start()

    return Response({'status': 'started', 'message': 'Sync running in background'})
