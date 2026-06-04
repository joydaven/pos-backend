# PR: Order Creation Performance — Background Post-Order Tasks

## Summary

Speeds up WooCommerce order creation response time by **~6-10 seconds** by removing a redundant API health check and moving all post-order side-effect tasks (credit points, ATUM inventory sync, GHL webhook) into a background daemon thread.

**No frontend changes.** Backend only.

---

## Problem

Even a simple 1-item order was slow because the HTTP response was blocked by several tasks that don't affect the order itself:

| Blocking Task | Time Cost |
|---|---|
| `_test_connection()` — redundant `GET products?per_page=1` before every order POST | ~1-3s |
| `time.sleep(2)` — hard wait before ATUM sync | 2s |
| ATUM inventory sync — sequential API calls per product | ~2-5s |
| Credit service points processing | ~0.5-1s |
| GHL skip-email webhook (when applicable) | ~0.5-1s |

**Total overhead: ~6-10s on top of the actual WooCommerce API call.**

---

## Changes

### `backend/crm/woocommerce.py`

- **Removed `_test_connection()` from `create_order()`** — This was firing a `GET products?per_page=1` round-trip to WooCommerce before every single order creation. The connection is already validated during `WooCommerceAPI.__init__()`, and if it's broken the POST itself will fail with a clear error. Saves ~1-3s per order.

### `backend/crm/views.py` — `create_woocommerce_order()`

- **Moved credit points, ATUM sync, and GHL webhook to a background daemon thread** so the HTTP response returns immediately after the WooCommerce order + status update completes.

  The background thread:
  - **Snapshots** all needed values (contact info, order data, items) before spawning to avoid Django lazy-loading / stale DB connection issues
  - Opens its own Django DB connection via `connection.ensure_connection()` and closes it in `finally`
  - Runs as `daemon=True` so it won't block gunicorn worker shutdown
  - Increased ATUM sleep from 2s → 3s since it's now in background (gives WordPress more time to finish stock deduction before we sync)

- **Removed** `credit_points_results` and `atum_sync_results` from the API response payload (these were never consumed by the frontend)

---

## What Still Runs Synchronously (Before Response)

These are order-critical and must complete before the frontend gets a response:

1. POS order lookup + item prefetch
2. Product/variation resolution (with parallel prefetch)
3. WooCommerce `POST orders` (the main ~15s bottleneck — server-side)
4. Parallel status update + internal note (ThreadPoolExecutor)
5. Subscription creation (if applicable)

---

## Risk Assessment

- **Low risk** — The moved tasks (credit points, ATUM sync, GHL webhook) are all fire-and-forget side effects. They update local DB caches and fire external webhooks. None affect the WooCommerce order itself.
- All three tasks have their own `try/except` blocks with logging, so failures are isolated and logged with `[BG]` prefix for easy filtering.
- The background thread properly manages its own DB connection lifecycle.

---

## Testing

1. Create a simple 1-item POS order → verify response returns faster (~6-10s improvement)
2. Check logs for `[BG]` prefixed entries confirming background tasks ran
3. Verify ATUM inventory sync still updates stock levels (check a few minutes after order)
4. Verify credit service points are still awarded after order completion
5. Test with `[SKIP_GHL_EMAIL]` flag → verify GHL webhook still fires in background

---

## Files Changed

| File | Change |
|---|---|
| `backend/crm/woocommerce.py` | Removed redundant `_test_connection()` from `create_order()` |
| `backend/crm/views.py` | Moved credit points + ATUM sync + GHL webhook to background thread |
