# PR: Staging → Main (Full Release Notes)

**Branch:** `payments_update` → `main`
**Scope:** 137 backend commits, ~90 frontend files changed, 3 new Django apps, 20+ migrations

---

## Table of Contents
1. [New Features](#new-features)
2. [Bug Fixes](#bug-fixes)
3. [Security & Infrastructure](#security--infrastructure)
4. [Migrations & Deployment](#migrations--deployment)
5. [WordPress Snippets](#wordpress-snippets)
6. [Files Changed](#files-changed)

---

## New Features

### 1. Credit Bank (NEW Django App)
Dollar-balance system for customer payments — staff can add/remove credits, customers can pay with credit bank balance.

**Backend (`credit_bank/`)**
- New app: `credit_bank` with `CreditBankAccount` (OneToOne with Contact) and `CreditBankTransaction` (audit trail) models
- API at `api/credit-bank/`: balance GET, add POST, redeem POST, adjust POST, transactions GET
- Migration: `credit_bank/migrations/0001_initial.py`

**Frontend**
- New service: `creditBankService.ts` — full API client
- Credit Bank tab (index 6) in CustomerDetailsModal: balance card, add/remove buttons, adjustment modal, transaction history
- PaymentModal integration: Credit Bank payment method appears when balance > 0, amount input with "Use maximum" shortcut
- Credit bank info displayed on order/refund receipts (PDF, email, print)

**Backend receipt integration**
- `views_receipts.py`: reads `_credit_bank_redeemed` and `_credit_bank_remaining` from WooCommerce order meta
- Credit bank section added to PDF and email receipt templates

### 2. Payment Plans (NEW Django App)
Installment-based payment plans for high-value orders.

**Backend (`payment_plans/`)**
- Models: `PaymentPlanTemplate`, `PaymentPlan`, `PaymentPlanInstallment`
- API at `api/payment-plans/`: templates CRUD, plans CRUD, installment actions (mark-paid, charge, send-receipt), settings
- Auto-billing: `manage.py process_installments` charges saved cards via Authorize.net CIM
- Systemd timer for daily auto-billing at 8am EST
- Installment receipts: HTML email + PDF via `payment_plans/receipts.py`
- Service charge support + per-installment card override
- Seed command: `manage.py seed_payment_plan_templates`

**Frontend**
- `PaymentPlanSetupModal.tsx` — 3-step wizard (choose template → configure → review)
- `PaymentPlansView.tsx` — sidebar management view with search, filters, stats
- `PaymentPlanDetailModal.tsx` — view/manage individual plan + installments
- `PaymentPlanSettings.tsx` — enable toggle, threshold, template management
- OrderPanel: "Create Payment Plan" button when enabled and total >= threshold
- OrderDetailsModal: linked payment plan banner with progress bar

### 3. Reports Dashboard (NEW)
**Frontend**
- New `ReportsView.tsx` with tabbed dashboard
- Tabs: Overview, Sales, Products, Customers, Payments, Subscriptions, Reconciliation, Team
- Shared components: `ChartCard`, `KPICard`, `ReportTable`, `GlobalFilters`, `ComingSoonCard`
- New service: `reportApiService.ts`

### 4. Email System Overhaul
- New `email_sender.py` module — branded teal-themed HTML email templates
- Auto-send receipt emails for: order completion, refunds, cancellations, shipment tracking
- Email audit logging: `EmailLog` model + Django admin view with toggle switches
- Redis dedup to prevent duplicate emails
- Skip webhook receipt email for POS-originated orders (prevents double-send)
- Migration: `0084_add_email_log_model.py`, `0085_seed_email_toggle_settings.py`

### 5. GHL Notes (Split-Screen)
- `GHLNotesPanel.tsx` — full CRUD for GoHighLevel notes in customer Notes tab
- POS Notes edit + author attribution
- `ghl_user_id` added to `UserProfile` model + `populate_ghl_user_ids` management command
- Migration: `users/0004_add_ghl_user_id_to_userprofile.py`

### 6. Bidirectional Contact Sync
- **POS ↔ GHL**: `ghl_api.py` — `push_contact_to_ghl()` + `pull_ghl_fields_to_contact()`
- **POS ↔ WooCommerce**: `woocommerce.py` — `push_contact_to_woo()` for billing/shipping address sync
- `perform_update()` on both `ContactViewSet` and `POSCustomerViewSet` pushes edits to GHL + WooCommerce
- `sync_ghl_addresses` management command for bulk address sync
- `sync_ghl_contact_ids` management command

### 7. Customer Deletion
- `delete_ghl_contact()` in `ghl_api.py` — DELETE API call to GoHighLevel
- `destroy()` override on `ContactViewSet` and `POSCustomerViewSet`: deletes GHL contact → WooCommerce customer → local Contact record, returns detailed `deletion_log`
- Frontend: Danger Zone section in CustomerDetailsModal Details tab with double-confirmation (Step 1: confirm intent → Step 2: type "DELETE")
- `deleteCustomer()` in `customerService.ts` with cache cleanup

### 8. Shipment Tracking Improvements
- `views_shipment_tracking.py` — create/get/delete using DS WP endpoint (supports partial fulfillment)
- Fulfillment badges and clinic pickup logic
- Partial-shipped email: accounts for ALL trackings, excludes non-shippable items
- Auto-determine shipped vs partial-shipped from fulfilled items
- Removed dependency on `ds-shipment/v1` plugin, uses standard WC API

### 9. Subscription Management Enhancements
- Inline subscription actions (`InlineSubscriptionActions.tsx`) — resume/cancel/pause from customer profile
- Subscription details open inline in CustomerDetailsModal instead of navigating away
- Status filter dropdown (replaces bubbles) in customer profile subscription tab
- Variable-subscription product support: variation-aware trial detection, period fallback
- Separate WooCommerce subscriptions created for each Subscribe & Save product
- Membership history in customer profile
- `SubscriptionPaymentMethodModal.tsx` for changing payment method on subscriptions

### 10. Saved Carts Enhancement
- Save and restore per-item discounts and order discount reason
- Display team member name instead of email in saved cart list
- Migration: `0086_add_order_discount_reason_to_saved_cart.py`

### 11. GHL Membership Sync
- `ghl_membership_sync.py` — bidirectional GHL membership sync
- `webhooks_ghl.py` — webhook receiver with signature verification (`webhook_signature.py`)
- `webhook_enhanced.py` — enhanced webhook processing

---

## Bug Fixes

### Payments & Authorize.net
- **Declined payments proceeding as successful**: Now checks `transaction.responseCode` even when `resultCode == "Ok"`; friendly error messages via `AUTHNET_ERROR_MESSAGES` dict
- **AmEx card add failure**: Replaced SDK with raw XML for CIM profile creation
- **pyxb `ContentNondeterminismExceededError`**: Replaced SDK calls with raw HTTP/XML in `get_customer_profile_by_email()`, `get_customer_payment_profiles()`, `sync_cim_profiles`
- **Missing auth headers**: Fixed `getSavedCards`, `deleteSavedCard`, `setDefaultCard` in `paymentService.ts`
- Payment card notes: `0082_add_payment_card_note.py` migration
- Idempotency key for payments: `0006_add_idempotency_key.py` migration

### Order Creation
- **Same-name product mix-up**: Frontend now stores numeric `woo_product_id` in cart metadata; backend checks metadata before name-based fallback
- **Bundle pricing**: Fixed $0 bundle pricing in mixed orders, blank child prices in receipts
- **Bundle children "Product not found" during reorder**: Strip `└─ ` prefix from names
- **ATUM crash on `on-hold`**: Create orders as `pending` instead (avoids `reduce_stock_levels` crash)
- **ATUM inventory**: Fuzzy matching for inventory names, native `mi_inventories` for stock deduction, dynamic inventory ID resolution

### Receipts
- Unified teal-branded template across email, PDF, and print receipts
- Brand + SKU added to all receipt types
- Subscription billing split by frequency
- Partial refund receipt shows only refunded items with correct payment method
- Bundle pricing display fix (blank child prices and subtotal)
- WooCommerce order number in PDF attachment filenames
- Actual payment method shown in refund receipt destination
- Receipt subscription address improvements

### Refunds
- Per-item remaining display instead of global ratio in refund form
- Block already-refunded items using `refund_status` from order line items
- POS DB-first `refund_status` enrichment (stops relying on WooCommerce API)
- Name-based and productId-based item matching for refund processing

### Customers & UI
- Customers search spinner stuck after searching
- "Loading more customers..." spinner stuck permanently
- Load More button showing when all search results displayed
- Customer information name not updating through GHL
- Discount applying to both identical items when only one was discounted
- Date display standardized to US format (MM/DD/YYYY)
- Order details panel responsive on smaller screens

### Subscriptions
- Subscription frequency per-item on receipts
- Variable products showing all subscription plans (WCSATT)
- Subscription period override: always apply frontend metadata `billing_period`
- Webhook race condition: merge `woo_data` instead of replacing
- `variable-subscription` product type handling throughout

### Emails
- Prevent double sending of shipment tracking emails
- Cancellation email upgraded to match WooCommerce template
- Handle string format in tracking email `products_list`
- Auto-send refund receipt for pure WooCommerce orders refunded from POS

---

## Security & Infrastructure

### Google Cloud Secret Manager Migration
- New `core/secrets.py` with `get_secret()`, `get_woo_db_config()`, `get_ghl_db_config()`
- Feature flag: `USE_GCP_SECRETS=true` enables GCP; otherwise falls back to `os.environ`
- All hardcoded credentials removed from source files
- `scripts/setup_secrets.sh` for provisioning secrets
- `.gitignore` updated: `.env.backup`, `.env.dev`, `.env.prod`, `.env.save`, `.env.staging`

### Django Admin Hardening
- Custom admin URL: `/drs-admin/` (replaces default `/admin/`)
- Rate limiting via `throttles.py`
- `@doctorsstudio.com` email restriction for admin access
- SQL injection fix + auth added to database schema endpoints

### Other
- `reconcile_payments` management command
- `sync_cim_profiles` management command — bulk match CIM profiles to contacts
- Console log suppression in production (`suppressConsole.ts`)
- Code splitting: POS, Customers, Settings lazy-loaded (1.7MB → 556KB initial bundle)
- Performance indexes: `0079_add_contact_search_indexes.py`, `0083_add_performance_indexes.py`

---

## Migrations & Deployment

### New Django Apps (register in `INSTALLED_APPS`)
1. `credit_bank`
2. `payment_plans`

### Migrations to Run
```bash
python manage.py migrate
```

| App | Migration | Description |
|-----|-----------|-------------|
| `credit_bank` | `0001_initial` | CreditBankAccount + CreditBankTransaction tables |
| `crm` | `0077` through `0087` | Merge migrations, search indexes, payment card note, performance indexes, email log, email toggle settings, saved cart discount reason |
| `payment_plans` | `0001_initial` | PaymentPlanTemplate + PaymentPlan + PaymentPlanInstallment |
| `payment_plans` | `0002_add_overdue_status` | Overdue status for installments |
| `payment_plans` | `0003_add_service_charge_and_card_override` | Service charge + per-installment card |
| `payments` | `0005_add_payment_card_note` | Note field on PaymentCard |
| `payments` | `0006_add_idempotency_key` | Idempotency key for transactions |
| `users` | `0004_add_ghl_user_id_to_userprofile` | GHL user ID on UserProfile |

### Management Commands to Run (one-time)
```bash
python manage.py seed_payment_plan_templates
python manage.py populate_ghl_user_ids
python manage.py sync_cim_profiles
```

### Systemd Timer (payment plans auto-billing)
```bash
sudo cp backend/payment_plans/systemd/process-installments.service /etc/systemd/system/
sudo cp backend/payment_plans/systemd/process-installments.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now process-installments.timer
```

### New Python Dependencies
```
google-cloud-secret-manager==2.20.0
```
(Plus any others added in `requirements.txt` diff)

### Environment Variables (if using GCP secrets)
```
USE_GCP_SECRETS=true
GCP_PROJECT_ID=doctors-studio-backend
APP_ENVIRONMENT=staging|production
```

---

## WordPress Snippets

| File | Description |
|------|-------------|
| `ds-pos-pricing-lock-plugin.php` | **NEW** — Locks POS pricing to prevent WooCommerce overrides |
| `ds-shipment-rest-api.php` | **NEW** — REST API for shipment tracking (partial fulfillment support) |
| `ds-tracking-email-trigger.php` | **NEW** — Triggers tracking emails on shipment meta update |
| `ds-refund-receipt-pdf.php` | Updated — Refund receipt PDF attachment |
| `force-fulfillment-location-safe.php` | Updated — Fulfillment location logic |
| `functions.php` | Updated — Various hooks and filters |

---

## Files Changed

### Backend (158 files, +20,122 / -3,075 lines excl. logs)

**New apps:** `credit_bank/` (8 files), `payment_plans/` (15 files)
**New modules:** `core/secrets.py`, `crm/email_sender.py`, `crm/throttles.py`, `crm/webhooks_ghl.py`, `crm/webhook_enhanced.py`, `crm/views_reports.py`, `crm/ghl_membership_sync.py`, `crm/utils/webhook_signature.py`, `doctorsstudio/admin.py`, `scripts/setup_secrets.sh`, `scripts/backup_db.sh`
**New management commands:** `reconcile_payments`, `sync_ghl_addresses`, `sync_ghl_contact_ids`, `populate_ghl_user_ids`, `sync_cim_profiles`, `process_installments`, `seed_payment_plan_templates`

**Major modified files:**
- `crm/views.py` — Contact deletion, address sync, order creation fixes
- `crm/woocommerce.py` — Push contact to WooCommerce, bundle/ATUM fixes, prefetch optimization
- `crm/ghl_api.py` — Delete contact, push/pull contact sync, credit points sync
- `crm/views_receipts.py` — Credit bank info, unified template
- `crm/views_refunds.py` — Per-item refund processing
- `crm/views_woocommerce_orders.py` — Guest checkout orders, name search, payment enrichment
- `crm/views_shipment_tracking.py` — Partial fulfillment, auto-status
- `crm/views_subscriptions.py` — Variable-subscription handling
- `crm/views_membership_direct.py` — Subscription query fixes
- `crm/models.py` — EmailLog model, new fields
- `crm/serializers.py` — New serializers for extended data
- `crm/urls.py` — New route registrations
- `payments/authorize_net.py` — Raw XML replacement for SDK, decline handling
- `payments/views.py` — CIM profile auto-linking, saved cards from Authorize.net
- `doctorsstudio/settings.py` — Secret Manager integration, new apps

### Frontend (93 files, +15,145 / -5,729 lines)

**New components:** `PaymentPlanSetupModal`, `PaymentPlanDetailModal`, `PaymentPlansView`, `PaymentPlanSettings`, `GHLNotesPanel`, `InlineSubscriptionActions`, `ReportsView` (+ 8 tab components + 5 shared components)
**New services:** `creditBankService.ts`, `paymentPlanService.ts`, `reportApiService.ts`, `receiptHtmlBuilder.ts`
**New contexts:** `NotificationContext.tsx`

**Major modified files:**
- `CustomerDetailsModal.tsx` — Credit Bank tab, GHL Notes, delete customer, address sync, subscription inline view
- `PaymentModal.tsx` — Credit bank payment method, decline handling
- `CheckoutModal.tsx` — Credit bank deduction, payment plan integration
- `POS.tsx` — Payment plan flow, product SKU in cart metadata, bundle fixes
- `OrderPanel.tsx` — Payment plan button, discount reason
- `App.tsx` — Payment plans + reports views in sidebar, code splitting
- `customerService.ts` — Delete customer, cache management
- `paymentService.ts` — Auth headers fix, decline response handling
- `subscriptionService.ts` — Variable-subscription support
- `SubscriptionsView.tsx` — Status filter dropdown, inline actions
- `OrderDetailsModal.tsx` — Payment plan banner, refund status
- `RefundOrderView.tsx` — Per-item remaining, block already-refunded
