# Production Security Report — Doctors Studio POS

**Date**: 2026-03-13  
**Server**: `pos.doctorsstudio.com` (`35.231.141.115`)  
**Stack**: Django 4.2 + DRF + PostgreSQL + React 18 SPA  
**Auditor**: Automated code & runtime audit via Cascade  

---

## Executive Summary

The production POS server has a **solid security foundation** — secrets are centralized in GCP Secret Manager, TLS is enforced on the public domain, authentication defaults to `IsAuthenticated`, and Sentry monitors errors with PII disabled. However, several **medium and high-severity issues** remain that should be addressed:

| Severity | Count | Summary |
|----------|-------|---------|
| 🔴 CRITICAL | 2 | WooCommerce TLS verification disabled; GHL API calls have no request timeouts |
| 🟠 HIGH | 5 | `.env` file permissions too broad; log files world-writable; GHL logs leak Bearer tokens & full response headers; no HTTP→HTTPS redirect for POS domain; Redis has no authentication |
| 🟡 MEDIUM | 5 | Frontend stores token in localStorage; CORS includes development origins in production; card operations not scoped to requesting user; webhook signature mismatches logged but allowed through; cache KEY_PREFIX still says "staging_pos" |
| 🔵 LOW | 4 | Missing `POS_SENTRY_ENVIRONMENT` secret; `SECURE_SSL_REDIRECT=False`; `SECURE_CROSS_ORIGIN_OPENER_POLICY=None`; gunicorn `--log-level debug` in production |

---

## 1. Django Configuration Security

### ✅ What's Good

| Setting | Value | Status |
|---------|-------|--------|
| `DEBUG` | `False` | ✅ Correct |
| `SECRET_KEY` | Pulled from GCP Secret Manager (`POS_production_SECRET_KEY`) | ✅ Secure |
| `ALLOWED_HOSTS` | `pos.doctorsstudio.com, localhost, 127.0.0.1, 35.231.141.115` | ✅ Scoped |
| `SESSION_COOKIE_SECURE` | `True` (enforced in `not DEBUG` block) | ✅ |
| `CSRF_COOKIE_SECURE` | `True` (enforced in `not DEBUG` block) | ✅ |
| `SESSION_COOKIE_HTTPONLY` | `True` | ✅ |
| `CSRF_COOKIE_HTTPONLY` | `True` | ✅ |
| `SESSION_COOKIE_SAMESITE` | `Strict` (from .env) | ✅ |
| `CSRF_COOKIE_SAMESITE` | `Strict` (from .env) | ✅ |
| `SECURE_HSTS_SECONDS` | `31536000` (1 year) | ✅ |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `True` | ✅ |
| `SECURE_HSTS_PRELOAD` | `True` | ✅ |
| `SECURE_CONTENT_TYPE_NOSNIFF` | `True` | ✅ |
| `X_FRAME_OPTIONS` | `SAMEORIGIN` | ✅ |
| `SECURE_PROXY_SSL_HEADER` | `('HTTP_X_FORWARDED_PROTO', 'https')` | ✅ |
| `PKCE_REQUIRED` (OAuth2) | `True` | ✅ |
| Password validators | All 4 Django defaults enabled | ✅ |
| Sentry `send_default_pii` | `False` | ✅ |

### ⚠️ Issues

#### 🟡 MEDIUM — CORS allows development origins in production
**File**: `settings.py:253-266`  
Production CORS includes `http://localhost:3000`, `http://127.0.0.1:3000`, `http://127.0.0.1:8000`, and raw IP origins (`http://35.231.141.115:83`, `:84`). These should be removed or conditional on `DEBUG`.

#### 🟡 MEDIUM — Redis cache KEY_PREFIX is `staging_pos`
**File**: `settings.py:237`  
The production Redis cache key prefix is `staging_pos`. This is cosmetic but may cause confusion and could lead to cache collisions if staging and production ever share the same Redis instance.

#### 🔵 LOW — `SECURE_SSL_REDIRECT` not set
**File**: `settings.py:517-527`  
Django's `SECURE_SSL_REDIRECT` is not enabled. HTTP→HTTPS redirect is expected to be handled at the nginx layer (see Section 6 for an issue there).

#### 🔵 LOW — `SECURE_CROSS_ORIGIN_OPENER_POLICY = None`
**File**: `settings.py:75`  
Disabled for Google Auth compatibility. Acceptable trade-off but should be documented.

---

## 2. API Authentication & Authorization

### ✅ What's Good

- **Default permission**: `rest_framework.permissions.IsAuthenticated` globally in DRF settings
- **All CRM views** (`views.py`, `views_memberships.py`, `views_membership_direct.py`, `views_subscriptions.py`, `views_woocommerce_points.py`): Every endpoint uses `@permission_classes([IsAuthenticated])`
- **All payment views** (`payments/views.py`): Every endpoint except the Authorize.net webhook uses `IsAuthenticated`
- **All payment plan views** (`payment_plans/views.py`): All endpoints use `IsAuthenticated`
- **All shipping views**: All ViewSets use `IsAuthenticated`
- **Rate limiting**: `PaymentRateThrottle` (20/min) on payment endpoints, `AuthRateThrottle` (10/min) on Google login
- **Google login domain restriction**: Only `@doctorsstudio.com` emails can authenticate
- **Django Admin rate limiting**: `limit_req zone=admin_limit burst=10 nodelay` in nginx

### AllowAny Endpoints (Legitimate)

These are correctly public:

| Endpoint | File | Purpose | Mitigation |
|----------|------|---------|------------|
| `GoogleLoginView` | `auth_api/views.py:41` | Google OAuth login | AuthRateThrottle (10/min), domain restriction, Google token verification |
| `RefreshTokenView` | `crm/auth_views.py:103` | Refresh token exchange | Requires valid refresh token in cookie |
| `LogoutView` | `crm/auth_views.py:131` | Clear auth cookies | No sensitive operation |
| `authorizenet_webhook` | `payments/views.py:1031` | Authorize.net CIM events | HMAC signature verification (see issue below) |
| `ShippingWebhookViewSet.webhook` | `shipping/views.py:362` | ShipStation webhook | HMAC signature verification (see issue below) |

### ⚠️ Issues

#### 🟡 MEDIUM — Webhook signature mismatches are logged but allowed through
**File**: `payments/views.py:1073`  
The Authorize.net webhook logs `"allowing through (re-serialization may differ)"` when HMAC doesn't match, rather than rejecting. This is a conscious trade-off documented in code comments but reduces webhook integrity.

**File**: `shipping/views.py:367`  
The ShipStation webhook only verifies HMAC *if* a signature header is present (`if signature and ...`). If no header is sent, the request proceeds unauthenticated.

#### 🟡 MEDIUM — Card operations not scoped to requesting user
**File**: `payments/views.py` (multiple endpoints)  
Payment card endpoints (`get_saved_cards`, `delete_saved_card`, `set_default_card`, `update_saved_card`, `charge_customer_profile`) require `IsAuthenticated` but don't verify that the authenticated user has permission to access the specific customer's cards. Any authenticated staff user can enumerate, edit, delete, or charge any customer's cards by providing arbitrary customer/profile IDs.

---

## 3. Secrets Management

### ✅ What's Good

| Aspect | Status |
|--------|--------|
| `USE_GCP_SECRETS` | `true` — actively pulling from GCP Secret Manager |
| `APP_ENVIRONMENT` | `production` — correct prefix for server-specific secrets |
| `GCP_PROJECT_ID` | `doctors-studio-backend` |
| GCP Authentication | GCE metadata service account (`629465229019-compute@developer.gserviceaccount.com`) — no key file to leak |
| SDK version | `google-cloud-secret-manager` v2.20.0 |
| In-memory caching | Secrets cached per-process to avoid repeated API calls |
| Fallback | Graceful degradation to `.env` if GCP lookup fails |
| Secret naming | `POS_{key}` for shared, `POS_{environment}_{key}` for server-specific |

### Runtime Evidence

Logs confirm GCP Secret Manager is **actively working**. Only one secret is missing from GCP:

| Missing Secret | Expected GCP Name | Impact |
|----------------|-------------------|--------|
| `SENTRY_ENVIRONMENT` | `POS_SENTRY_ENVIRONMENT` | 🔵 LOW — Falls back to `.env` default `'production'` |

### ⚠️ Issues

#### 🟠 HIGH — `.env` file permissions too broad (644)
**File**: `/var/www/newpos-posds/woo-ghl-contact-db/backend/.env`  
Permissions: `www-data:www-data 644` — world-readable. This file still contains all secrets as fallback values. Should be `600` (owner read/write only).

**Recommendation**:
```bash
sudo chmod 600 /var/www/newpos-posds/woo-ghl-contact-db/backend/.env
```

---

## 4. Payment Security (Authorize.Net / PCI)

### ✅ What's Good

| Aspect | Status |
|--------|--------|
| Card storage model | Tokenized — `payments.PaymentCard` stores CIM profile IDs, last4, brand only |
| Legacy PAN storage | 0 rows in `crm.PaymentCard` (legacy table) — no live encrypted PANs |
| Production card records | 123 rows in `payments.PaymentCard`, 103 with CIM profile IDs |
| Card validation mode | `none` — avoids false-declines on AmEx (intentional decision 2026-03-05) |
| Raw XML fix | `billTo` before `payment` ordering — resolves Authorize.net SDK XML bug |
| Payment rate limiting | `PaymentRateThrottle` (20/min) on payment processing and card creation |
| Card number logging | Only last 4 digits logged (`card_number[-4:]`) — no full PAN in logs |

### ⚠️ Issues

See Section 2 — card operations not scoped to the requesting user's owned customers.

---

## 5. External Integrations

### 🔴 CRITICAL — WooCommerce TLS verification disabled

**File**: `crm/woocommerce.py:91`
```python
verify_ssl=False  # For testing only
```

TLS certificate verification is disabled for **all** WooCommerce API calls (the main API client and **12+ additional `requests.*` calls** throughout the file using `verify=False`). This exposes WooCommerce consumer key/secret to man-in-the-middle attacks. The comment says "For testing only" but this is running in production.

**Recommendation**: Set `verify_ssl=True` and remove all `verify=False` from direct `requests.*` calls.

---

### 🔴 CRITICAL — GHL API calls have no request timeouts

**File**: `crm/ghl_api.py` (all `requests.post/get/put/delete` calls)  
None of the GHL API calls specify a `timeout` parameter. A hung GHL API endpoint will block the gunicorn worker indefinitely, eventually exhausting all 3 workers and causing a full service outage.

**Recommendation**: Add `timeout=30` to all `requests.*` calls in `ghl_api.py`.

---

### 🟠 HIGH — GHL API logging leaks Bearer tokens and full response headers

**File**: `crm/ghl_api.py:148-162`
```python
logger.info(f"   Headers: {headers}")          # Contains: Authorization: Bearer {token}
logger.info(f"   Response Headers: {dict(response.headers)}")
logger.info(f"   Response Body: {response.text}")
```

The `update_ghl_custom_field` function logs the full request headers (including the `Authorization: Bearer` token), full response headers, and full response body to disk. These logs are written to `django.log` which has world-readable permissions (see Section 6).

**Recommendation**: Strip `Authorization` from logged headers, and truncate or remove response body logging.

---

## 6. Network & Server Configuration

### ✅ What's Good

| Aspect | Status |
|--------|--------|
| TLS | Let's Encrypt certificate for `pos.doctorsstudio.com`, ECDSA, valid until 2026-05-06 (53 days) |
| TLS protocols | `TLSv1.2 TLSv1.3` only |
| Ciphers | `HIGH:!aNULL:!MD5` |
| `ssl_prefer_server_ciphers` | `on` |
| Gunicorn | Runs as `www-data:www-data`, binds to Unix socket (not TCP) |
| PostgreSQL | Bound to `127.0.0.1:5432` — not externally accessible |
| Redis | Bound to `127.0.0.1:6379` — not externally accessible |
| Django admin | Rate limited via nginx `limit_req` |

### ⚠️ Issues

#### 🟠 HIGH — No HTTP→HTTPS redirect for POS domain
The nginx config for `pos.doctorsstudio.com` only has a `listen 443 ssl;` block. There is **no corresponding `listen 80` server block** that redirects HTTP to HTTPS. Users accessing `http://pos.doctorsstudio.com` will get a connection error or hit the default nginx server block, rather than being redirected to HTTPS.

**Recommendation**: Add an HTTP→HTTPS redirect server block:
```nginx
server {
    listen 80;
    server_name pos.doctorsstudio.com;
    return 301 https://$host$request_uri;
}
```

#### 🟠 HIGH — Log files are world-writable (666)
```
www-data:www-data 666 gunicorn.log
www-data:www-data 666 payments.log
www-data:www-data 666 sku_sync.log
www-data:www-data 666 woocommerce.log
```
These log files contain sensitive information (API responses, customer emails, payment profile IDs). Permissions `666` means **any user on the system** can read and write to them.

**Recommendation**:
```bash
sudo chmod 640 /var/www/newpos-posds/woo-ghl-contact-db/backend/logs/*.log
```

#### 🟠 HIGH — Redis has no authentication
Redis (`127.0.0.1:6379`) has `requirepass` set to empty. While Redis is only bound to localhost, any process on the machine can read/write cache data (including throttle counters, cached secrets, session data).

**Recommendation**: Set a strong `requirepass` in Redis config and update `REDIS_URL` in settings.

#### 🔵 LOW — Gunicorn runs with `--log-level debug`
**File**: `/etc/systemd/system/gunicorn.service`  
Debug-level logging in production generates excessive log volume and may expose internal details.

**Recommendation**: Change to `--log-level warning` or `--log-level info`.

#### ⚠️ NOTE — UFW firewall is inactive
The host-level firewall (UFW) is `inactive`. Network security is likely handled by GCP VPC firewall rules. This is acceptable if GCP firewall rules are properly configured, but should be verified.

#### ⚠️ NOTE — Multiple services listen on public interfaces
Several non-POS services listen on `0.0.0.0` (ports 80, 443, 22, 91-97, 631, 5433, 6380, 8000, 8001, 8095). These are accessible from the internet unless GCP firewall rules restrict them.

---

## 7. Logging & Sensitive Data Leakage

### ✅ What's Good

- Card numbers: Only last 4 digits logged
- No raw PAN/CVV found in explicit `logger.*` calls
- Sentry: `send_default_pii=False`
- Google OAuth: Token value not logged, only length
- WooCommerce: Consumer key/secret masked in initialization logs (first 4 chars shown)

### ⚠️ Issues

#### 🟠 HIGH — GHL Bearer token logged to disk
See Section 5 — `crm/ghl_api.py:150` logs full request headers including `Authorization: Bearer {token}`.

#### 🟡 MEDIUM — Verbose debug logging in production
- `crm/ghl_api.py:45-48` logs email search queries with `DEBUG:` prefix at INFO level
- `crm/woocommerce.py:21-28` configures `logging.basicConfig(level=logging.DEBUG)` which can affect the root logger
- `crm/ghl_api.py:162` logs full GHL response bodies (may contain PII — customer data, contact info)

---

## 8. Frontend Security

### ⚠️ Issues

#### 🟡 MEDIUM — Bearer token stored in `localStorage`
**File**: `frontend/src/services/api.ts:45`
```typescript
const token = localStorage.getItem('token');
```

`localStorage` is accessible to any JavaScript running on the page, making it vulnerable to XSS attacks. HttpOnly cookies are the recommended alternative.

**Current mitigations**: CORS is restricted (though includes dev origins), CSP headers are not observed.

---

## 9. CSRF Configuration

### ✅ What's Good

- CSRF middleware is active
- `DisableCSRFMiddleware` only exempts `/api/auth/google/` — appropriate for token-based OAuth flow
- `CSRF_COOKIE_SECURE=True`, `CSRF_COOKIE_HTTPONLY=True`, `CSRF_COOKIE_SAMESITE=Strict`

### Exempt Paths

| Path | Method | Reason |
|------|--------|--------|
| `/api/auth/google/` | POST | Google OAuth token exchange (CSRF-exempt via middleware + decorator) |
| All DRF ViewSets | * | DRF applies `csrf_exempt` by default; CSRF is enforced via `SessionAuthentication` when session-based |

---

## 10. Prioritized Remediation Plan

### Immediate (This Week)

| # | Severity | Issue | Effort |
|---|----------|-------|--------|
| 1 | 🔴 CRITICAL | Enable WooCommerce TLS verification (`verify_ssl=True`) | Low |
| 2 | 🔴 CRITICAL | Add `timeout=30` to all GHL API `requests.*` calls | Low |
| 3 | 🟠 HIGH | Fix `.env` file permissions to `600` | 1 command |
| 4 | 🟠 HIGH | Fix log file permissions to `640` | 1 command |
| 5 | 🟠 HIGH | Remove Bearer token from GHL request header logging | Low |

### Short-Term (Next 2 Weeks)

| # | Severity | Issue | Effort |
|---|----------|-------|--------|
| 6 | 🟠 HIGH | Add HTTP→HTTPS redirect for `pos.doctorsstudio.com` in nginx | Low |
| 7 | 🟠 HIGH | Set Redis `requirepass` | Low-Medium |
| 8 | 🟡 MEDIUM | Remove development origins from CORS in production | Low |
| 9 | 🟡 MEDIUM | Reject (not just log) webhook signature mismatches | Medium |
| 10 | 🟡 MEDIUM | Fix Redis cache `KEY_PREFIX` from `staging_pos` to `production_pos` | Low |

### Medium-Term (Next Month)

| # | Severity | Issue | Effort |
|---|----------|-------|--------|
| 11 | 🟡 MEDIUM | Scope card operations to the requesting user's assigned customers | Medium-High |
| 12 | 🟡 MEDIUM | Migrate frontend token storage from `localStorage` to HttpOnly cookies | Medium |
| 13 | 🔵 LOW | Change gunicorn `--log-level` from `debug` to `info` | Low |
| 14 | 🔵 LOW | Create missing `POS_SENTRY_ENVIRONMENT` secret in GCP | 1 command |
| 15 | 🔵 LOW | Verify GCP VPC firewall rules cover all public-listening ports | Low |

---

## Appendix A: Services Listening on Public Interfaces

| Port | Service | Risk |
|------|---------|------|
| 22 | SSH | Standard, key-based auth expected |
| 80 | Nginx (HTTP) | Serves various apps; POS missing HTTP→HTTPS redirect |
| 443 | Nginx (HTTPS) | Production POS and other apps |
| 91-97 | Various nginx server blocks | Other internal apps (analytics, calendar, etc.) |
| 631 | CUPS (printing) | Low risk unless external access needed |
| 5433 | Unknown | Investigate — may be secondary PostgreSQL |
| 6380 | Unknown | Investigate — may be secondary Redis |
| 8000-8001 | Backend servers | Should not be externally accessible |
| 8095 | Unknown | Investigate |

## Appendix B: TLS Certificate Status

| Domain | Issuer | Expiry | Days Remaining |
|--------|--------|--------|----------------|
| `pos.doctorsstudio.com` | Let's Encrypt (E7) | 2026-05-06 | **53 days** |

Ensure Certbot auto-renewal is active (`certbot renew` cron job or systemd timer).

---

*Report generated 2026-03-13. This is a point-in-time code and configuration audit. It does not constitute a formal penetration test.*
