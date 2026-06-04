# PR Notes: Sentry Error Fixes & Related Subscriptions Feature

**Date:** March 2, 2026  
**Branch:** staging

---

## 1. Fix: pyxb ContentNondeterminismExceededError in Authorize.net CIM (1.3K Sentry events)

**File:** `backend/payments/authorize_net.py` — `get_customer_payment_profiles()`

**Problem:** The `authorizenet` Python SDK's pyxb XML parser fails with `ContentNondeterminismExceededError` when parsing newer Authorize.net API responses containing `customerAddressType` elements. This was crashing the `/woocommerce/subscriptions/` endpoint because it loops through all subscriptions fetching CIM payment profiles for each one. The existing `getchildren()` fallback caught *some* failures but not all (1.3K Sentry events over 5 days, escalating).

**Fix:** Replaced the SDK-based `getCustomerProfileController` call with raw HTTP/XML using `requests` + `xml.etree.ElementTree`, same pattern already applied to `get_customer_profile_by_email()` and `sync_cim_profiles`. All three CIM fetch paths now bypass the pyxb SDK entirely.

---

## 2. Fix: GHL Credit Service API 404s — Circuit Breaker (387+ Sentry events each for create/get)

**File:** `backend/crm/ghl_api.py`

**Problem:** The GHL v1 Credits API endpoint (`https://rest.gohighlevel.com/v1/credits/services/location/{locationId}`) returns 404 for every request — both GET (list services) and POST (create service). Every order completion triggers `sync_all_credit_points_to_ghl()` which loops through all credit point records, calling the dead endpoint twice per product (GET + POST). A 13-item order generated hundreds of 404 calls wasting ~20 seconds.

**Fix:** Added a circuit breaker pattern:
- `_credits_api_circuit` dict tracks open/closed state with timestamp
- `_is_credits_circuit_open()` / `_open_credits_circuit()` helper functions
- On first 404, circuit opens for 1 hour (`CIRCUIT_BREAKER_TTL = 3600`)
- All subsequent calls to `get_ghl_credit_services()` and `create_ghl_credit_service()` immediately return empty/None
- After TTL expires, allows one retry to check if endpoint recovered
- Notes API fallback (which works) continues to run regardless

**Action needed:** Investigate in GHL admin whether Credits feature is still enabled for location `pgfekl6sKgofVPSuYOJo`, or if the API was deprecated/moved to v2.

---

## 3. Feature: Related Subscriptions in Order Details Modal

**Files changed:**
- `backend/crm/views_subscriptions.py` — Added `parent` query param passthrough to WooCommerce Subscriptions API
- `pos/src/features/pos/components/CustomerDetailsModal.tsx` — Added Related Subscriptions section to POS order details modal
- `pos/src/components/customers/OrderDetailsModal.tsx` — Added Related Subscriptions section to standalone order details modal

**What it does:**
- When viewing an order, fetches WooCommerce subscriptions whose `parent_id` matches the order's WooCommerce ID
- Displays a "Related Subscriptions" section (indigo theme) showing:
  - Subscription badge + clickable `#ID` link
  - Status badge (active/on-hold/cancelled/pending-cancel/expired)
  - Billing frequency (e.g., "Every 4 weeks")
  - Amount and next payment date
- Clicking a subscription ID opens the full `SubscriptionOrderDetailsModal` with Subscription Management panel
- Section only renders when subscriptions exist (no empty section for non-subscription orders)

**Backend:** Added `parent` query parameter support to `get_woocommerce_subscriptions()` view, passed through to WooCommerce's Subscriptions REST API which natively supports filtering by parent order ID.

**Navigation fix:**
- `handleViewInlineSubscription()` uses `orderDetailsService.getSubscriptionDetails()` (not `getOrderDetails()`) so subscription-specific fields (`billing_period`, `next_payment_date`, `customer_id`) are returned, enabling the Subscription Management panel
- `onClose` on outer Dialog is guarded: `if (!inlineSubModalId) onClose()` — prevents the order modal from closing when interacting with the subscription modal overlay
- `onNavigateToOrder` just closes the subscription modal (the parent order modal is already open underneath)

---

## 4. Security: P0 Hardcoded Secrets Removed

**Problem:** Multiple secrets were hardcoded directly in source files committed to git — API tokens, database passwords, OAuth client secrets, and the Django SECRET_KEY fallback.

### 4a. GHL API Token moved to `get_secret()`
**File:** `backend/crm/ghl_api.py`

The GHL bearer token (`pit-...`) was hardcoded on line 21. Moved to `get_secret('GHL_API_TOKEN', '')` and `get_secret('GHL_DEFAULT_LOCATION_ID', '')`. Values added to `.env`.

### 4b. Database passwords removed from 7 sync scripts
**Files:** `sync_oauth_safe.py`, `sync_oauth_final.py`, `sync_oauth_complete.py`, `sync_oauth_tokens.py`, `sync_product_tables.py`, `sync_complete_products.py`, `copy_tables.py`

Hardcoded `pos_dev` password (`8C4Nax19LRT`) and Neon DB password (`npg_...`) replaced with `os.environ.get()` calls.

### 4c. GHL OAuth defaults removed from settings.py
**File:** `backend/doctorsstudio/settings.py`

`GHL_CLIENT_ID` and `GHL_CLIENT_SECRET` had real values as default fallbacks in `get_secret()` calls. Changed defaults to empty strings — values must come from `.env` or Secret Manager.

### 4d. Django SECRET_KEY insecure default removed
**File:** `backend/doctorsstudio/settings.py`

Removed the `django-insecure-...` fallback value. App now fails fast with `ValueError` if `SECRET_KEY` is not configured in `.env` or Secret Manager.

### Action required after merge:
- **Rotate** GHL API Token, GHL OAuth Client Secret, `pos_dev` DB password, and Neon DB password (all were in git history)
- Ensure production `.env` has `GHL_API_TOKEN` and `GHL_DEFAULT_LOCATION_ID`
- Optional: run BFG Repo-Cleaner to scrub old secrets from git history

---

## Files Modified

| File | Change |
|------|--------|
| `backend/payments/authorize_net.py` | Replaced SDK-based `get_customer_payment_profiles()` with raw HTTP/XML |
| `backend/crm/ghl_api.py` | Added circuit breaker for GHL Credits API 404s + moved token to `get_secret()` |
| `backend/crm/views_subscriptions.py` | Added `parent` query param passthrough |
| `backend/doctorsstudio/settings.py` | Removed hardcoded GHL OAuth defaults + insecure SECRET_KEY fallback |
| `backend/sync_oauth_safe.py` | Removed hardcoded DB password |
| `backend/sync_oauth_final.py` | Removed hardcoded DB password |
| `backend/sync_oauth_complete.py` | Removed hardcoded DB password |
| `backend/sync_oauth_tokens.py` | Removed hardcoded DB password |
| `backend/sync_product_tables.py` | Removed hardcoded DB password |
| `backend/sync_complete_products.py` | Removed hardcoded DB password |
| `backend/copy_tables.py` | Removed hardcoded DB + Neon passwords |
| `pos/src/features/pos/components/CustomerDetailsModal.tsx` | Added Related Subscriptions section + subscription modal navigation |
| `pos/src/components/customers/OrderDetailsModal.tsx` | Added Related Subscriptions section (standalone modal) |
