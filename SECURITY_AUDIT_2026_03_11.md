# Security Audit Report — Doctor's Studio POS

**Date:** March 11, 2026  
**Scope:** Full-stack red team review of `woo-ghl-contact-db` (Django backend), `pos` (React frontend), and WordPress snippets  
**Severity Scale:** CRITICAL / HIGH / MEDIUM / LOW / INFO

---

## Executive Summary

The application has solid foundations in several areas (GCP Secret Manager migration, PKCE OAuth2, HMAC webhook verification, parameterized SQL queries). However, **a massive number of API endpoints are publicly accessible without authentication**, which is the single most dangerous finding. An attacker who discovers these URLs can read/modify customer data, redeem loyalty points, manage subscriptions, process payment retries, and manipulate membership statuses — all without logging in.

---

## CRITICAL Findings

### 1. Mass Broken Access Control — `AllowAny` on Sensitive Endpoints

**Severity: CRITICAL**  
**Impact: Full unauthorized access to customer PII, financial data, and payment operations**

Dozens of production API endpoints use `@permission_classes([AllowAny])` or have authentication **commented out**, meaning anyone on the internet can call them with zero credentials.

#### Financial / Payment Operations (unauthenticated):

| File | Endpoint | Risk |
|------|----------|------|
| `views_woocommerce_points.py` | `POST /api/woocommerce/customers/<id>/points/redeem/` | **Drain any customer's loyalty points** |
| `views_woocommerce_points.py` | `POST /api/woocommerce/customers/<id>/points/adjust/` | **Add/remove points for any customer** |
| `views_fractional_points.py` | `POST /api/customers/<id>/fractional-points/redeem/` | **Drain fractional points** |
| `views_fractional_points.py` | `POST /api/customers/<id>/fractional-points/adjust/` | **Arbitrary points manipulation** |
| `views_woocommerce_orders.py` | `POST /api/woocommerce/orders/<id>/retry-payment/` | **Retry payment charges on any order using saved cards** |
| `views_subscriptions.py` | `POST /api/subscriptions/<id>/update-payment-method/` | **Change payment method on any subscription** |
| `views_subscriptions.py` | `POST /api/subscriptions/<id>/update-dates/` | **Modify subscription billing dates** |

#### Customer PII Exposure (unauthenticated):

| File | Endpoint | Risk |
|------|----------|------|
| `views_woocommerce_orders.py` | `GET /api/woocommerce/orders/` | **List all orders with full customer info** |
| `views_woocommerce_orders.py` | `GET /api/woocommerce/orders/<id>/` | **Read any order details** |
| `views_subscriptions.py` | `GET /api/woocommerce/subscriptions/` | **List all subscriptions** |
| `views_subscriptions.py` | `GET /api/customers/<id>/woocommerce-subscriptions/` | **View customer subscription + payment info** |
| `views_subscriptions.py` | `GET /api/subscriptions/<id>/payment-profiles/` | **Expose Authorize.net CIM payment profiles** |
| `views_woocommerce_points.py` | `GET /api/woocommerce/customers/<id>/points/` | **View any customer's points balance** |
| `views_woocommerce_points.py` | `GET /api/woocommerce/customers/<id>/points/history/` | **Full points transaction history** |
| `views_memberships.py` | `GET /api/customers/<id>/woocommerce-memberships/` | **View customer memberships** |
| `views_memberships.py` | `POST /api/memberships/<id>/manage/` | **Pause/cancel/resume any membership** |
| `product_categories.py` | `GET /api/product-categories/` | Product catalog data |

#### Shipment Tracking (unauthenticated CRUD):

| File | Endpoint | Risk |
|------|----------|------|
| `views_shipment_tracking.py` | `POST .../shipment-trackings/create/` | **Create fake tracking on any order** |
| `views_shipment_tracking.py` | `DELETE .../shipment-trackings/<id>/delete/` | **Delete real tracking from any order** |
| `views_shipment_tracking.py` | `PUT .../update-shipping/` | **Change shipping address on any order** |
| `views_shipment_tracking.py` | `POST .../resend-tracking-email/` | **Spam tracking emails to customers** |

#### Credited Services (authentication explicitly commented out):

| File | Endpoint | Risk |
|------|----------|------|
| `views_credited_services.py:22-24` | `POST /api/store-manager/credits/webhook` | **Add/subtract service credits for any customer** |
| `views_credited_services.py:222-224` | `GET /api/store-manager/<woo_id>/<product_id>` | **View customer service credits** |
| `views_credited_services.py:271-273` | `GET /api/store-manager/<woo_id>` | **View all credits for any customer** |
| `views_credited_services.py:306-308` | `POST .../credits/webhook-with-variation` | **Manipulate credits with variation** |

Comment in code: `# Temporarily removed authentication for testing` — this has been deployed to production/staging.

#### Refund History (empty permission classes):

| File | Endpoint | Risk |
|------|----------|------|
| `views_refunds.py:1937` | `GET /api/order-refunds/<order_id>/` | `@permission_classes([])` — **empty list = no auth** |

**Remediation:** Change all `AllowAny` to `IsAuthenticated` on every non-webhook endpoint. Remove the `# Temporarily removed` commented-out auth. This is a **deploy-blocking** issue.

---

### 2. Webhook Endpoints Without Signature Verification

**Severity: CRITICAL**  
**Impact: An attacker can forge WooCommerce/ATUM events to manipulate inventory, orders, and customer data**

Several webhook endpoints use `@csrf_exempt` but **do not verify HMAC signatures**:

| Endpoint | File | Signature Check? |
|----------|------|:----------------:|
| `POST /api/webhooks/woocommerce/product/` | `views.py:10587` | ❌ **None** |
| `POST /api/webhooks/woocommerce/customer/` | `views.py:11440` | ❌ **None** |
| `POST /api/webhooks/woocommerce/product/enhanced/` | `webhook_enhanced.py` | ❌ **None** |
| `POST /api/webhooks/atum/inventory/` | `views_webhooks.py:23` | ❌ **None** |
| `POST /api/webhooks/atum/location/` | `views_webhooks.py` | ❌ **None** |
| `POST /api/webhooks/woocommerce/stock/` | `views_webhooks.py` | ❌ **None** |
| `POST /api/webhooks/woocommerce/tracking-email/` | `views_shipment_tracking.py` | ❌ **None** |
| `POST /api/webhooks/woocommerce/order/` | `webhooks_order.py:20` | ✅ `verify_woocommerce_signature()` |
| `POST /api/webhooks/woocommerce/subscription/` | `webhooks_subscription.py:19` | ✅ `verify_woocommerce_signature()` |
| `POST /api/webhooks/ghl/membership/` | `webhooks_ghl.py:92` | ✅ Bearer token check |

The `woocommerce_product_webhook` and `woocommerce_customer_webhook` in `views.py` are the original webhook handlers and **never call** `verify_woocommerce_signature()`. An attacker can POST crafted payloads to these endpoints and:
- Overwrite product data (prices, names, stock quantities)
- Modify/create customer records
- Trigger inventory sync operations

**Also:** When `WOOCOMMERCE_WEBHOOK_SECRET` is not configured, `verify_woocommerce_signature()` **silently passes** (line 48: `return True, None`). This is a fail-open design.

**Remediation:** Add `verify_woocommerce_signature()` to all WooCommerce webhook handlers. Change the missing-secret behavior to fail-closed.

---

### 3. Authorize.net Webhook Signature Verification is Non-Enforcing

**Severity: HIGH**  
**Impact: Attacker can spoof Authorize.net events to link their CIM profile to any customer, taking over payment methods**

In `payments/views.py:1030-1075`, the `authorizenet_webhook` endpoint:
1. Uses `@permission_classes([AllowAny])` (correct for webhooks)
2. Attempts HMAC verification **but on signature mismatch, logs a warning and continues** (line 1073):
   ```python
   logger.warning("...signature mismatch — allowing through (re-serialization may differ)")
   ```
3. If the `AUTHORIZE_NET_SIGNATURE_KEY` secret is empty, verification is skipped entirely

An attacker can send a POST to `/api/payments/authorize-net/webhook/` with a crafted payload like:
```json
{"eventType": "net.authorize.customer.paymentProfile.created", "payload": {"customerProfileId": "ATTACKER_PROFILE_ID"}}
```

The handler will then fetch the CIM profile, extract the email, and **overwrite** `Contact.authorize_net_customer_profile_id` — linking the attacker's profile to the victim's contact record. Next time staff charges the customer's "saved card", it charges the attacker's card (or vice versa if the attacker sets a victim's profile).

**Remediation:** Make signature verification mandatory and fail-closed. Do not log-and-continue on mismatch.

---

## HIGH Findings

### 4. No IDOR Protection on Customer-Scoped Endpoints

**Severity: HIGH**

Even on endpoints that *do* use `IsAuthenticated`, there is **no check that the authenticated user is authorized to access the specific resource**. Any logged-in staff member can access any customer's data by changing the UUID or ID in the URL. Examples:

- `GET /api/customers/<uuid>/notes/` — any staff can read any customer's notes
- `POST /api/credit-bank/<uuid>/add/` — any staff can add credits to any account
- `POST /api/credit-bank/<uuid>/redeem/` — any staff can redeem credits from any account
- `DELETE /api/payments/saved-cards/<card_id>/delete/` — any staff can delete any customer's saved card
- `POST /api/payments/authorize-net/charge-profile/` — any staff can charge any CIM profile

In a HIPAA-compliant healthcare environment, this is a significant gap. While all users are staff, there's no role-based access control (RBAC) or row-level permissions.

**Remediation:** Implement per-resource ownership/role checks, especially for payment operations and patient/customer data.

### 5. Database Schema Endpoint Exposes Full DB Structure

**Severity: HIGH**

`GET /api/database/schema/` (requires `IsAuthenticated`) returns:
- Every table name, column name, data type, constraints, row counts
- Database name and size

`GET /api/database/tables/<table_name>/` returns detailed column schema.

While protected by auth, this gives any authenticated staff member a complete reconnaissance map of the database. Combined with Finding #4, this is valuable for crafting targeted attacks.

**Remediation:** Restrict to superusers/admin role, or remove from production entirely.

### 6. Google OAuth Auto-Creates Staff Accounts

**Severity: HIGH**

In `auth_api/views.py:116-141`, any `@doctorsstudio.com` email that authenticates via Google OAuth is **automatically created as a staff user** (`is_staff=True`). There is no approval workflow or allowlist of specific users.

If an attacker gains access to any `@doctorsstudio.com` Google Workspace account (phishing, compromised account, former employee), they automatically get staff access to the POS backend with full API access.

**Remediation:** Implement an explicit user allowlist or require admin approval for new accounts. Do not auto-grant `is_staff`.

### 7. Concurrent Session Accumulation Without Cleanup

**Severity: MEDIUM → HIGH (operational)**

`auth_api/views.py:170`: "Allow multiple concurrent sessions — do not delete existing tokens." Combined with no token limit per user, a compromised account or automated script can generate unlimited valid tokens. The 8-hour expiry (+ 30-day refresh tokens) means leaked tokens persist for a long time.

**Remediation:** Limit active tokens per user (e.g., max 5 sessions). Add an admin endpoint to revoke all tokens for a user.

---

## MEDIUM Findings

### 8. Error Messages Leak Internal Details

**Severity: MEDIUM**

Multiple exception handlers return `str(e)` to the client:

- `views.py` order creation: `f'An error occurred while processing payment: {str(e)}'`
- `payments/views.py:206`: full exception message
- `views_database_schema.py:140`: `'message': str(e)` — leaks DB error details
- `views_refunds.py`, `credit_bank/views.py`, etc.

These leak stack traces, SQL errors, and internal paths to the frontend/attacker.

**Remediation:** Return generic error messages to clients. Log full details server-side only.

### 9. Excessive Logging of Sensitive Data

**Severity: MEDIUM (HIPAA concern)**

- `views.py:10634`: `logger.info(f"Raw request body: {request.body}")` — logs full webhook payloads including customer PII
- `views.py:11471-11472`: Same for customer webhooks
- `views.py:10649`: `logger.info(f"Full webhook data: {webhook_data}")` — full product data
- `views.py:11486`: Full customer data logged
- Payment logging includes customer profile IDs and amounts

In a HIPAA environment, PII in log files creates compliance risk. Log files rotate at 10MB x 5 backups = 50MB of potentially unencrypted PII.

**Remediation:** Redact PII from logs. Log only IDs and status codes. Ensure log files are encrypted at rest.

### 10. XML Injection in Authorize.net API Calls

**Severity: MEDIUM**

In `payments/views.py:1103-1110`, the XML request to Authorize.net is built via f-string with `customer_profile_id` from the webhook payload:

```python
get_profile_xml = f'''...
    <customerProfileId>{customer_profile_id}</customerProfileId>
...'''
```

While `customer_profile_id` is validated to be `.isdigit()`, the same pattern is used in `authorize_net.py` for other CIM calls. If any path allows non-numeric values, it could enable XML injection against the Authorize.net API.

**Remediation:** Use an XML builder library (e.g., `xml.etree.ElementTree`) instead of f-string interpolation for all XML construction.

### 11. CORS Allows HTTP Origins

**Severity: MEDIUM**

Settings include both HTTP and HTTPS origins:
```python
"http://pos.doctorsstudio.com",
"http://staging-pos.doctorsstudio.com",
"http://35.231.141.115:84",
```

HTTP origins allow MITM attacks to intercept CORS-credentialed requests. The `CORS_ALLOW_CREDENTIALS = True` setting makes this worse — cookies and auth headers are sent on cross-origin requests from HTTP origins.

**Remediation:** Remove all `http://` origins. Use HTTPS only.

### 12. `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` Default to False

**Severity: MEDIUM**

Line 388-393: These cookies default to `False` via environment variables. If the env vars aren't explicitly set in production, cookies are sent over unencrypted HTTP.

The `if not DEBUG:` block (line 518) does force `True`, but the earlier explicit assignments on lines 388-393 may override depending on load order. Verify runtime values.

**Remediation:** Remove the env-var-based defaults. Always set secure cookies in production.

### 13. No Rate Limiting on Most Endpoints

**Severity: MEDIUM**

Rate limiting is configured in `DEFAULT_THROTTLE_RATES` but the comment says: *"Global defaults removed to avoid throttling AllowAny endpoints."* This means:

- Only `AuthorizeNetPaymentView`, `charge_customer_profile`, and `GoogleLoginView` have explicit `throttle_classes`
- All other endpoints have **no rate limiting at all**
- The unauthenticated `AllowAny` endpoints (Finding #1) are completely unbounded

An attacker can brute-force customer IDs, enumerate orders, or spam point adjustments at unlimited speed.

**Remediation:** Re-enable global throttle defaults. Add explicit throttle classes to sensitive endpoints.

---

## LOW Findings

### 14. `SECURE_CROSS_ORIGIN_OPENER_POLICY = None`

**Severity: LOW**

Line 75 disables COOP for Google Auth. This weakens cross-origin isolation and can enable certain side-channel attacks (Spectre-class). Consider using `same-origin-allow-popups` instead of `None`.

### 15. API Schema/Docs Publicly Accessible

**Severity: LOW**

`/api/docs/` and `/api/redoc/` serve Swagger UI. While `SERVE_PERMISSIONS` is set to `IsAuthenticated` in settings, verify this is enforced. If accessible, it provides a complete attack surface map.

### 16. Token Storage in localStorage

**Severity: LOW (given the threat model)**

`authService.ts` stores OAuth tokens in `localStorage`. This is vulnerable to XSS (any JavaScript running on the page can read tokens). For a staff-only internal tool with domain-restricted Google auth, the risk is lower, but `httpOnly` cookies would be more secure.

### 17. `password` Grant Type Allowed

**Severity: LOW**

`OAUTH2_PROVIDER.ALLOWED_GRANT_TYPES` includes `'password'`. This grant type sends username/password directly and is considered insecure by OAuth 2.1. Since the app uses Google OAuth (not password login), this can be removed.

---

## INFO

### Positive Findings (Good Practices)

- **GCP Secret Manager** integration for secrets (not hardcoded)
- **PKCE** required for OAuth2 authorization code flow
- **HMAC-SHA256** signature verification on order/subscription webhooks
- **Parameterized queries** throughout — no SQL injection found in the raw SQL
- **`sql.Identifier()`** used correctly in `views_database_schema.py` for dynamic table names
- **Whitelist validation** on `get_table_detail()` before dynamic SQL
- **Sentry** with `send_default_pii=False`
- **Idempotency keys** on payment processing
- **HSTS** enabled in production (31536000 seconds)
- **Refresh token mutex** prevents race conditions in frontend
- **`.gitignore`** properly excludes all `.env` variants

---

## Priority Remediation Plan

| Priority | Finding | Effort | Impact |
|----------|---------|--------|--------|
| **P0 — Immediate** | #1: Add `IsAuthenticated` to all AllowAny endpoints | Low | Eliminates mass unauthenticated access |
| **P0 — Immediate** | #1b: Uncomment auth on credited services | Trivial | 4 endpoints exposed |
| **P1 — This Week** | #2: Add HMAC verification to all webhook endpoints | Low | Prevents forged webhook attacks |
| **P1 — This Week** | #3: Make Authorize.net webhook signature fail-closed | Trivial | Prevents CIM profile takeover |
| **P1 — This Week** | #6: Add user allowlist for Google OAuth | Low | Prevents unauthorized staff creation |
| **P2 — This Sprint** | #4: Add RBAC/ownership checks | Medium | Defense-in-depth for HIPAA |
| **P2 — This Sprint** | #5: Restrict DB schema endpoint | Trivial | Reduces recon surface |
| **P2 — This Sprint** | #8: Sanitize error messages | Low | Prevent info leakage |
| **P2 — This Sprint** | #11: Remove HTTP CORS origins | Trivial | Prevent MITM |
| **P2 — This Sprint** | #13: Enable global rate limiting | Low | Prevent brute force |
| **P3 — Next Sprint** | #7: Limit concurrent sessions | Low | Reduce token sprawl |
| **P3 — Next Sprint** | #9: Redact PII from logs | Medium | HIPAA compliance |
| **P3 — Next Sprint** | #10: Use XML builder for Authorize.net | Medium | Prevent XML injection |
