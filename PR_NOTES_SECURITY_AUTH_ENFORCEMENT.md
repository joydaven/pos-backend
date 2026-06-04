# PR: Security — Enforce Authentication on All API Endpoints

## Summary

Full security audit identified **~63 API endpoints** that were publicly accessible without authentication (`AllowAny` or commented-out auth). This PR enforces `IsAuthenticated` on all non-webhook endpoints and fixes the corresponding frontend services that were missing auth headers.

**This is a HIPAA-critical fix** — the exposed endpoints included customer PII, financial operations (points redemption, payment retries, credit bank), membership management, and shipment tracking CRUD.

---

## Backend Changes (19 files)

### Security: `AllowAny` → `IsAuthenticated`

Every non-webhook `@permission_classes([AllowAny])` decorator replaced with `@permission_classes([IsAuthenticated])`. Unused `AllowAny` imports cleaned up.

| File | Endpoints Secured |
|------|-------------------|
| `crm/views.py` | ~15 function-based views + `POSOrderViewSet` + `POSCustomerViewSet` |
| `crm/views_woocommerce_points.py` | 4 (get points, redeem, history, adjust) |
| `crm/views_fractional_points.py` | 4 (balance, redeem, adjust, transactions) |
| `crm/views_woocommerce_orders.py` | 5 (list, detail, subscription detail, retry-payment, failed orders) |
| `crm/views_subscriptions.py` | 7 (list, customer subs, update payment method, payment profiles, update dates, counts, related orders) |
| `crm/views_memberships.py` | 2 (list memberships, manage membership) |
| `crm/views_membership_actions.py` | 5 (pause, resume, cancel, delete, get status) |
| `crm/views_shipment_tracking.py` | 8 (get trackings, get tracking, create, delete, providers, update shipping address, product lookup, resend email) |
| `crm/views_bundle.py` | 3 (batch product details, batch variation lookup, get bundle) |
| `crm/views_orders.py` | 1 (get order) |
| `crm/views_receipts.py` | 2 (send receipt, send refund receipt) |
| `crm/product_categories.py` | 2 (categories, hierarchical categories) |
| `crm/views_refunds.py` | 1 (`@permission_classes([])` → `[IsAuthenticated]`) |
| `credit_bank/views.py` | 5 (balance, add, redeem, adjust, transactions) |
| `crm/views_credited_services.py` | 4 (uncommented `@permission_classes([IsAuthenticated])` — was `# Temporarily removed authentication for testing`) |

### Import cleanup (no functional change)
| File | Change |
|------|--------|
| `crm/views_database_schema.py` | Removed unused `AllowAny` from import |
| `crm/views_webhooks.py` | `AllowAny` → `IsAuthenticated` in import |
| `payment_plans/views.py` | Removed unused `AllowAny` from import |

### Google OAuth hardening (`auth_api/views.py`)
- Added `is_active` check — disabled accounts (`is_active=False`) now get 403 instead of tokens
- Sanitized error message in exception handler (no longer leaks `str(e)` to client)
- Auto-create for `@doctorsstudio.com` accounts preserved (per business requirement)

### Intentionally left as `AllowAny` (correct for webhooks/auth):
- `auth_api/views.py` — `GoogleLoginView` (login endpoint)
- `crm/auth_views.py` — `RefreshTokenView`, `LogoutView` (must work without valid access token)
- `payments/views.py` — `authorizenet_webhook` (secured by HMAC signature)
- `shipping/views.py` — ShipStation webhook (secured by HMAC signature)

---

## Frontend Changes (4 files)

### Auth header fixes for services that were calling secured endpoints without tokens

| File | Fix |
|------|-----|
| `wooOrdersService.ts` | Switched from raw `axios` to centralized `api` instance (which has auth interceptor). Affected: `getOrders()`, `getOrder()` |
| `subscriptionService.ts` | Switched 7 raw `axios.get/post/patch` calls to `api` instance. Affected: `fetchWooCommerceSubscriptions()`, `getOriginalSubscriptions()`, `getSubscriptionById()`, `createWooCommerceSubscription()`, `createLocalSubscription()`, `updateSubscriptionStatus()`, `getSubscriptionCounts()` |
| `productService.ts` | Added `Authorization: Bearer` header to `getProductCategories()` and `getHierarchicalCategories()` (raw `fetch` calls) |

### Stale date propagation fix (`CustomerDetailsModal.tsx`)
- After editing a subscription date (e.g., `next_payment_date`), the updated value now propagates to all 3 state sources: `membershipProducts`, `membershipSubscriptionHistory`, and `customerSubscriptions`
- Triggers `refetchMemberships()` after successful date edit to ensure all data is fresh

---

## Testing

Verified on staging (`staging-pos.doctorsstudio.com`):

- **Unauthenticated requests** return 401 on all secured endpoints (orders, subscriptions, points, categories, POS settings, etc.)
- **Authenticated requests** return 200 with valid data
- **Frontend pages** load correctly after hard refresh: POS, Orders, Subscriptions, Membership Subscriptions, Refunds, Reports
- **Webhooks** continue to work (WooCommerce order/subscription webhooks use HMAC verification, not OAuth)
- **Google OAuth login** works for `@doctorsstudio.com` accounts, auto-creates staff on first login

---

## Deployment Notes

- **Backend**: Restart gunicorn after deploy (`sudo systemctl restart <service>`)
- **Frontend**: Build is included — no additional steps
- **No migrations** required
- **No env var changes** required
