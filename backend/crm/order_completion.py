import logging
from typing import Any, Dict, List, Tuple

from .woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _build_validation_result(
    *,
    eligible: bool,
    code: str,
    message: str,
    missing_items: List[Dict[str, Any]] | None = None,
    required_qty: Dict[str, int] | None = None,
    fulfilled_qty: Dict[str, int] | None = None,
) -> Dict[str, Any]:
    return {
        "eligible": eligible,
        "code": code,
        "message": message,
        "missing_items": missing_items or [],
        "required_qty": required_qty or {},
        "fulfilled_qty": fulfilled_qty or {},
    }


def _compute_missing_items(
    woo_order: Dict[str, Any],
    required_qty: Dict[str, int],
    fulfilled_qty: Dict[str, int],
) -> List[Dict[str, Any]]:
    missing_items: List[Dict[str, Any]] = []
    line_items = woo_order.get("line_items", []) if isinstance(woo_order, dict) else []
    line_lookup = {str(item.get("id", "")): item for item in line_items if isinstance(item, dict)}

    for item_id, required in required_qty.items():
        fulfilled = fulfilled_qty.get(item_id, 0)
        if fulfilled >= required:
            continue

        line_item = line_lookup.get(item_id, {})
        missing_items.append(
            {
                "item_id": item_id,
                "name": line_item.get("name", f"Line item {item_id}"),
                "required_qty": required,
                "fulfilled_qty": fulfilled,
                "remaining_qty": max(required - fulfilled, 0),
            }
        )

    return missing_items


def _required_and_fulfilled_quantities(
    wc_api: WooCommerceAPI,
    woo_order: Dict[str, Any],
    strict_item_level: bool = True,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    line_items = woo_order.get("line_items", []) if isinstance(woo_order, dict) else []
    bundle_parent_ids = wc_api._bundle_parent_line_item_ids(line_items)
    required_qty_raw = wc_api._required_qty_shippable_line_items(line_items, bundle_parent_ids)
    required_qty = {item_id: _safe_int(qty) for item_id, qty in required_qty_raw.items()}

    all_trackings = wc_api._get_shipment_tracking_items_from_order_payload(woo_order)
    fulfilled_qty: Dict[str, int] = {}

    for tracking in all_trackings:
        if not isinstance(tracking, dict):
            continue
        products_list = tracking.get("products_list")
        if not isinstance(products_list, list):
            products_list = []

        # Strict mode: only explicit products_list contributions count.
        # Empty lists do not imply full fulfillment coverage.
        if strict_item_level and len(products_list) == 0:
            continue

        for product in products_list:
            if not isinstance(product, dict):
                continue
            item_id = str(product.get("item_id", "") or "")
            if not item_id:
                continue
            qty = _safe_int(product.get("qty"), default=0)
            if qty <= 0:
                continue
            fulfilled_qty[item_id] = fulfilled_qty.get(item_id, 0) + qty

    return required_qty, fulfilled_qty


def validate_woocommerce_order_can_complete(
    wc_api: WooCommerceAPI,
    woo_order: Dict[str, Any],
) -> Dict[str, Any]:
    if not woo_order or not isinstance(woo_order, dict):
        return _build_validation_result(
            eligible=False,
            code="MISSING_WOO_ORDER",
            message="Cannot validate completion because WooCommerce order data is unavailable.",
        )

    required_qty, fulfilled_qty = _required_and_fulfilled_quantities(wc_api, woo_order, strict_item_level=True)

    if not required_qty:
        return _build_validation_result(
            eligible=True,
            code="NO_SHIPPABLE_ITEMS",
            message="Order has no shippable items requiring tracking coverage.",
            required_qty=required_qty,
            fulfilled_qty=fulfilled_qty,
        )

    missing_items = _compute_missing_items(woo_order, required_qty, fulfilled_qty)
    if missing_items:
        return _build_validation_result(
            eligible=False,
            code="TRACKING_COVERAGE_INCOMPLETE",
            message="Order cannot be completed until shipment tracking covers all shippable item quantities.",
            missing_items=missing_items,
            required_qty=required_qty,
            fulfilled_qty=fulfilled_qty,
        )

    return _build_validation_result(
        eligible=True,
        code="TRACKING_COVERAGE_COMPLETE",
        message="Shipment tracking covers all shippable item quantities.",
        required_qty=required_qty,
        fulfilled_qty=fulfilled_qty,
    )


def validate_pos_order_can_complete(pos_order) -> Dict[str, Any]:
    metadata = pos_order.metadata if isinstance(getattr(pos_order, "metadata", {}), dict) else {}
    woo_order_id = metadata.get("woo_order_id")

    if not woo_order_id:
        # If there are no shippable POS items, completion is still allowed.
        shippable_pos_items = []
        for item in pos_order.items.all():
            fulfillment = (getattr(item, "fulfillment_location", "") or "").lower()
            needs_shipping = bool(getattr(item, "needs_shipping", False))
            is_digital = bool(getattr(item, "is_digital", False))

            if is_digital:
                continue
            if "dropship" in fulfillment or (("boca" in fulfillment or "jupiter" in fulfillment) and needs_shipping):
                shippable_pos_items.append(item)

        if not shippable_pos_items:
            return _build_validation_result(
                eligible=True,
                code="NO_SHIPPABLE_ITEMS",
                message="Order has no shippable items requiring tracking coverage.",
            )

        return _build_validation_result(
            eligible=False,
            code="MISSING_WOO_ORDER_ID",
            message="Order cannot be completed because shipment tracking coverage cannot be validated for shippable items.",
            missing_items=[
                {
                    "item_id": str(item.id),
                    "name": item.name,
                    "required_qty": _safe_int(item.quantity, 1),
                    "fulfilled_qty": 0,
                    "remaining_qty": _safe_int(item.quantity, 1),
                }
                for item in shippable_pos_items
            ],
        )

    wc_api = WooCommerceAPI()
    woo_order = wc_api.get_order(woo_order_id)
    if not woo_order:
        return _build_validation_result(
            eligible=False,
            code="MISSING_WOO_ORDER",
            message="Order cannot be completed because the linked WooCommerce order could not be loaded.",
        )

    return validate_woocommerce_order_can_complete(wc_api, woo_order)
