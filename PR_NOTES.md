# Pull Request: Production Readiness — Security, Performance & Reliability

## Summary

Comprehensive production-readiness hardening across the full stack. Covers security fixes (P0), operational improvements (P1), and performance/reliability enhancements (P2). All changes are backward-compatible with existing functionality.

---

## Backend (`woo-ghl-contact-db`)

### New Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| `django-redis` | 6.0.0 | Redis cache backend for Django |
| `redis` | 7.2.0 | Redis Python client (installed as dependency of django-redis) |
| `sentry-sdk` | 2.53.0 | Error tracking and performance monitoring |

### System Dependencies
| Package | Purpose |
|---------|---------|
| `redis-server` | In-memory cache for rate limiting + webhook deduplication |
| `cron` | Scheduled jobs (backups, reconciliation) |

### New Files
- **`crm/throttles.py`** — Custom DRF throttle classes (`PaymentRateThrottle`, `AuthRateThrottle`) for per-endpoint rate limiting
- **`crm/utils/webhook_signature.py`** — WooCommerce webhook HMAC-SHA256 signature validation + Redis-backed delivery deduplication
- **`crm/management/commands/reconcile_payments.py`** — Daily reconciliation command: POS orders ↔ PaymentTransactions ↔ WooCommerce sync status
- **`crm/migrations/0083_add_performance_indexes.py`** — 17 new database indexes across Contact, Order, Product, POSOrder models
- **`payments/migrations/0006_add_idempotency_key.py`** — Adds `idempotency_key` field to PaymentTransaction model
- **`scripts/backup_db.sh`** — Automated PostgreSQL backup script (gzip compressed, 30-day retention)

### Modified Files

#### `doctorsstudio/settings.py`
- Added `sentry_sdk` import and conditional Sentry initialization (reads `SENTRY_DSN` env var)
- Added Redis cache configuration (`default` cache on DB 0, `throttle` cache on DB 1)
- Added `ALLOWED_HOSTS` with explicit domain list (was `['*']`)
- Added structured `LOGGING` config with rotating file handlers for django + payments loggers
- Added `DEFAULT_THROTTLE_RATES` for payment (20/min) and auth (10/min) endpoints
- Added production security settings (HSTS, secure cookies, X-Frame-Options, etc.)

#### `doctorsstudio/urls.py`
- Added `/api/health/` endpoint for uptime monitoring (returns 200 + timestamp, no auth required)
- Restricted `/api/docs/` and `/api/schema/` to authenticated users only

#### `auth_api/views.py`
- `GoogleLoginView`: Added `AuthRateThrottle` (10/min), restricted to `@doctorsstudio.com` Google domain
- Removed `client_secret` from frontend token exchange (PKCE flow doesn't need it)

#### `payments/views.py`
- Added `PaymentRateThrottle` (20/min) on `process_payment` endpoint
- Wrapped payment processing in `transaction.atomic()` for database consistency
- Added idempotency key support — duplicate payment submissions return cached result instead of double-charging

#### `payments/models.py`
- Added `idempotency_key` field (CharField, unique, nullable) to `PaymentTransaction` model

#### `crm/models.py`
- Added 17 database indexes:
  - **Contact**: `authorize_net_customer_profile_id`, `normalized_phone`
  - **Order**: `woo_order_id`, `status`, `created_at`
  - **Product**: `name`, `product_type`, `status`, `stock_status`, `updated_at`
  - **POSOrder**: `status`, `transaction_id`, `pos_location_id`, `assigned_location`, `created_at`, `trashed_at`

#### `crm/webhook_enhanced.py`, `crm/webhooks_order.py`, `crm/webhooks_subscription.py`
- Added HMAC signature verification on all WooCommerce webhook endpoints
- Added Redis-backed delivery deduplication (5-min TTL using `X-WC-Webhook-Delivery-ID` header)

#### `crm/views.py`
- Added HMAC signature verification to legacy product/customer webhook handlers

#### `crm/views_subscriptions.py`
- Added `get_subscription_counts` endpoint for lightweight subscription stats
- Added `get_subscription_related_orders` endpoint for parent + renewal order lookup

#### `crm/urls.py`
- Wired new subscription endpoints

### Environment Variables

#### Required (already set on staging)
No new required env vars — all existing functionality preserved.

#### Optional (activate when ready)
| Variable | Purpose |
|----------|---------|
| `SENTRY_DSN` | Sentry error tracking DSN for backend |
| `SENTRY_ENVIRONMENT` | Environment name (default: `staging`) |
| `SECRET_KEY` | Django secret key (falls back to default if not set) |
| `REDIS_URL` | Redis connection URL (default: `redis://127.0.0.1:6379/0`) |
| `WOOCOMMERCE_WEBHOOK_SECRET` | HMAC secret for webhook signature validation |

### Cron Jobs to Install
```
0 3 * * *  /path/to/scripts/backup_db.sh          # Daily PostgreSQL backup at 3 AM UTC
0 4 * * *  /path/to/venv/bin/python manage.py reconcile_payments --days 1  # Daily reconciliation at 4 AM UTC
```

### Database Migrations to Run
```bash
python manage.py migrate crm      # 0083_add_performance_indexes
python manage.py migrate payments  # 0006_add_idempotency_key
```

---

## Frontend (`drs_all_in_one`)

### New Dependencies
| Package | Version | Purpose |
|---------|---------|---------|
| `@sentry/react` | latest | Frontend error tracking and performance monitoring |

Install: `npm install --legacy-peer-deps`

### Modified Files

#### `src/index.tsx`
- Added Sentry initialization (reads `REACT_APP_SENTRY_DSN` env var, only activates if set)

#### `src/services/auth/authService.ts`
- Removed `client_secret` from OAuth token exchange requests (PKCE flow — secret should never be in frontend code)

#### `src/services/api/paymentService.ts`
- Added idempotency key generation (`crypto.randomUUID()`) sent as `X-Idempotency-Key` header on payment requests
- Prevents double-charge on network retry or user double-click

#### `src/App.tsx`
- Minor routing fix

#### `src/components/PurchaseOptionsModal.tsx`
- Minor UI fix

#### `src/components/subscriptions/OrderDetailsModal.tsx`
- Cleanup and simplification

### Environment Variables (Optional)
| Variable | Purpose |
|----------|---------|
| `REACT_APP_SENTRY_DSN` | Sentry error tracking DSN for frontend |
| `REACT_APP_SENTRY_ENVIRONMENT` | Environment name (default: `staging`) |

---

## Testing Checklist

- [ ] **Payment processing** — Complete a credit card payment, verify no double-charge on rapid clicks
- [ ] **Customer profile tabs** — Subscriptions, Points, Memberships all load correctly
- [ ] **Webhook processing** — Product/order/subscription webhooks still process normally
- [ ] **Login** — Google OAuth login works (must be @doctorsstudio.com)
- [ ] **Health endpoint** — `GET /api/health/` returns 200
- [ ] **API docs** — `/api/docs/` requires authentication
- [ ] **Reconciliation** — `python manage.py reconcile_payments --days 7` runs without error
- [ ] **Redis** — `redis-cli ping` returns PONG
- [ ] **Backup** — `scripts/backup_db.sh` creates compressed backup in backups directory

## Risk Assessment

**Low risk** — All changes are additive or configuration-level. No existing API contracts changed. Rate limiting is per-endpoint only (not global). Webhook signature validation gracefully skips if secret is not configured.
