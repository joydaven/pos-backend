# Membership ↔ Subscription Linking

## Overview

This document explains how WooCommerce Memberships, Subscriptions, and Orders relate to each other — and how the POS system ensures they stay linked and in sync.

---

## The Three Entities

| Entity | WooCommerce Plugin | Purpose |
|---|---|---|
| **Order** | WooCommerce Core | The purchase transaction |
| **Subscription** | WC Subscriptions | Recurring billing engine (charges, payment dates) |
| **Membership** | WC Memberships | Access/status layer (member roles, content gating, member pricing) |

A membership product ties all three together: purchasing the product creates an Order, which creates a Subscription (for recurring billing), which is linked to a Membership (for access control).

---

## The Problem: POS vs WordPress Frontend

### WordPress Frontend (Natural Flow)
```
Customer checkout
  → WC creates Order
  → WC Subscriptions plugin creates Subscription (linked to Order)
  → WC Memberships plugin detects membership product in Order
  → Membership auto-created AND auto-linked to Subscription
  → subscription_id is set on the Membership ✅
```

All three entities are linked. Changing the subscription status automatically cascades to the membership via WordPress hooks.

### POS Flow (Before Fix)
```
POS creates Order via REST API
  → POS creates Subscription via REST API (separate call)
  → WC Memberships plugin detects membership product in Order
  → Membership auto-created BUT subscription_id is NULL ❌
```

The membership is created from the Order, but the Subscription didn't exist yet (or the linking hook doesn't fire via REST). Result: **orphaned membership** that doesn't track the subscription.

Additionally, if the membership already existed (same customer, same plan), the plugin **reactivates the old membership** instead of creating a new one — but still doesn't link it to the new subscription.

### Consequences of Broken Link
- Cancelling subscription from POS → membership stays Active
- Pausing subscription from POS → membership stays Active
- Member keeps access/pricing they shouldn't have
- WP admin shows inconsistent statuses

---

## The Fix: Two-Part Solution

### Part 1: Link Membership After Subscription Creation

**File:** `backend/crm/views.py` → `create_woocommerce_order()`

After the POS successfully creates a subscription via `wc.create_subscription()`, we:

1. `GET /wp-json/wc/v3/memberships/members?customer={customer_id}`
2. Find the membership that matches (by `order_id` or unlinked)
3. `PUT /wp-json/wc/v3/memberships/members/{id}` with `subscription_id` and `order_id`

This handles both cases:
- **New membership** → `order_id` matches the new order, `subscription_id` is null → link it
- **Reactivated membership** → plugin reused an old one → update both fields to current

### Part 2: Sync Membership Status on POS Manage Actions

**File:** `backend/crm/views_membership_direct.py` → `update_subscription_status()`

The POS manages subscriptions via **direct SQL** (not WC REST API), which means WordPress hooks don't fire. Even with a linked membership, the status won't cascade automatically.

After updating the subscription status via SQL, we:

1. `GET /wp-json/wc/v3/memberships/members?subscription={subscription_id}`
2. `PUT /wp-json/wc/v3/memberships/members/{id}` with the new status

**Status Mapping:**

| POS Action | Subscription Status | Membership Status (API value) |
|---|---|---|
| `pause` | `wc-on-hold` | `paused` |
| `resume` | `wc-active` | `active` |
| `cancel` | `wc-cancelled` | `cancelled` |
| `restore` | `wc-active` | `active` |
| `delete` | `trash` | `cancelled` |

---

## WooCommerce Memberships REST API Reference

**Official Docs:** https://godaddy-wordpress.github.io/woocommerce-memberships-rest-api-docs/

**Authentication:** Uses the same WC REST API consumer key/secret (`WOO_CONSUMER_KEY` / `WOO_CONSUMER_SECRET`). No additional token needed.

### Key Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/wp-json/wc/v3/memberships/members` | List/search memberships |
| `POST` | `/wp-json/wc/v3/memberships/members` | Create a membership |
| `PUT` | `/wp-json/wc/v3/memberships/members/{id}` | Update a membership |
| `DELETE` | `/wp-json/wc/v3/memberships/members/{id}` | Delete a membership |

### Query Parameters (GET)

| Param | Type | Description |
|---|---|---|
| `customer` | int/string | Filter by WC customer ID |
| `order` | int | Filter by order ID |
| `plan` | int | Filter by membership plan ID |
| `status` | string | Filter by status (`active`, `paused`, `cancelled`, etc.) |
| `subscription` | int | Filter by linked subscription ID |

### Writable Fields (POST/PUT)

| Field | Type | Description |
|---|---|---|
| `customer_id` | int | WC customer ID |
| `plan_id` | int | Membership plan ID |
| `status` | string | `active`, `paused`, `cancelled`, etc. |
| `order_id` | int | Linked WC order ID |
| `product_id` | int | Linked product ID |
| `subscription_id` | int | **Linked subscription ID (the key field for our fix)** |
| `start_date_gmt` | datetime | Membership start date |
| `end_date_gmt` | datetime | Membership end date |
| `cancelled_date_gmt` | datetime | When membership was cancelled |

### Membership Status Values

| API Value | WP Admin Display |
|---|---|
| `active` | Active |
| `paused` | Paused |
| `cancelled` | Cancelled |
| `expired` | Expired |
| `pending` | Pending |
| `complimentary` | Complimentary |
| `free_trial` | Free Trial |

Note: When setting status via API, use the plain value (e.g., `paused`). The `wcm-` prefix (e.g., `wcm-paused`) is the internal WordPress post status — the REST API handles the conversion.

---

## Files Modified

| File | Change |
|---|---|
| `backend/crm/woocommerce.py` | Added `get_memberships()` and `update_membership()` methods |
| `backend/crm/views.py` | After subscription creation → find & link membership |
| `backend/crm/views_membership_direct.py` | After subscription status change → sync membership status |

---

## GHL Sync Integration

This fix works alongside the existing bidirectional GHL sync:

```
POS Action (pause/cancel/resume)
  → Direct SQL: update subscription status
  → REST API: update membership status (this fix)
  → GHL API: update member_status custom field (existing sync)
```

All three systems stay in sync from a single POS action.

### Inbound GHL Webhook

When GHL fires a webhook to change membership status:
```
GHL Workflow → POST /api/webhooks/ghl/membership/
  → Backend updates WC subscription via REST API
  → WC subscription webhook fires → but loop prevention skips GHL re-sync
  → Membership auto-updates IF linked to subscription ✅
```

The membership linking fix ensures the inbound GHL webhook path also cascades correctly to the membership.

---

## Testing Checklist

- [ ] POS purchase of membership product → WP admin shows membership with subscription_id set
- [ ] POS cancel subscription → membership status changes to cancelled
- [ ] POS pause subscription → membership status changes to paused
- [ ] POS resume subscription → membership status changes to active
- [ ] Repeat purchase (reactivation) → old membership reactivated with new subscription_id
- [ ] GHL inbound webhook → subscription + membership both update
- [ ] WP frontend purchase → still works as before (no regression)

---

## Historical Data Fix

Existing memberships created by POS before this fix have `subscription_id: null`. A one-time script can fix these by matching memberships to subscriptions via `customer_id` + `order_id`. See the optional migration script if needed.
