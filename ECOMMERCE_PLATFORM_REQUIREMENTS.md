# Ecommerce Platform Requirements

> **Purpose:** Catalog every capability the Doctors Studio POS system depends on from its ecommerce backend, expressed in platform-agnostic terms. This document answers: *"What must the replacement platform provide?"*
>
> **Non-goals:** Data migration plan, detailed data-structure spec, vendor comparison.

---

## Table of Contents

1. [Product Catalog](#1-product-catalog)
2. [Order Management](#2-order-management)
3. [Subscription Lifecycle](#3-subscription-lifecycle)
4. [Membership Management](#4-membership-management)
5. [Multi-Location Inventory](#5-multi-location-inventory)
6. [Customer Management](#6-customer-management)
7. [Payment Gateway Integration](#7-payment-gateway-integration)
8. [Refunds](#8-refunds)
9. [Shipment Tracking](#9-shipment-tracking)
10. [Webhooks](#10-webhooks)
11. [CRM Integration](#11-crm-integration)
12. [Receipts & Communications](#12-receipts--communications)
13. [Reporting](#13-reporting)
14. [REST API Surface](#14-rest-api-surface)
15. [Credit / Loyalty Programs](#15-credit--loyalty-programs)
16. [Product Editor](#16-product-editor)
17. [Saved Carts](#17-saved-carts)
18. [POS Settings](#18-pos-settings)
19. [Payment Plans (Partial Orders)](#19-payment-plans-partial-orders)

---

## 1. Product Catalog

### 1.1 Product Types

The platform must support the following distinct product types:

- **Simple** — single SKU, single price, optional stock management.
- **Variable** — parent product with one or more variations, each with its own SKU, price, stock, weight, dimensions, and image.
- **Subscription** — simple product with recurring billing (period + interval).
- **Variable Subscription** — variable product where each variation can have its own billing period, interval, and price.
- **Bundle** — a composite product containing a list of child products. The bundle has its own price (often discounted vs. sum of children). Children must resolve to real products/variations for order creation.
- **Grouped** — display-only grouping of multiple products; each child is purchased independently.

### 1.2 Variation Model

Each variation of a variable product requires:

| Field | Notes |
|---|---|
| Unique numeric ID | Variation must be independently addressable via API |
| SKU | Optional, independent of parent |
| Regular price / Sale price | Per-variation pricing |
| Stock quantity | Per-variation stock |
| Attributes | Array of `{name, option}` pairs (e.g., `{name: "Duration", option: "1 month"}`) |
| Weight & dimensions | For shipping calculations |
| Image | Per-variation image |
| Menu order | Display sort order |
| Status | Publish / draft per variation |

The API must allow:
- Listing all variations for a parent product (paginated, up to 100 per page).
- Fetching a single variation by its ID (returning `parent_id` in the response).
- Creating, updating, and deleting variations on a parent product.

### 1.3 Attributes & Terms

- **Global attributes** (e.g., Brand, Series, Duration) must be listable via API with pagination.
- Each attribute has **terms** (e.g., Brand → "Xymogen", "Designs for Health") that must be listable and creatable via API.
- Brand is a first-class attribute (`slug: pa_brand`) used for catalog filtering. The POS syncs brands to a local `Brand` table.

### 1.4 Categories

- Hierarchical category tree stored as a JSON array on each product.
- Each category entry: `{id, name, slug}`.
- Categories are used for product filtering in the POS and for reporting breakdowns.

### 1.5 Images

- Multiple images per product, stored as a JSON array.
- Each entry: `{id, src, name, alt}`.
- The POS displays the first image as the thumbnail.

### 1.6 Pricing

- **Regular price** — the full catalog price.
- **Sale price** — optional, with optional date range (`date_on_sale_from`, `date_on_sale_to`).
- **Subscription price** — for subscription products, the recurring amount per billing cycle.
- **Subscribe & Save schemes** — product-level subscription options (percentage discount off regular price, with period/interval). A product can offer both one-time purchase and subscription purchase.
- **Bundle pricing** — a single price for the bundle; children are priced at $0 on the order.
- **Modified membership pricing** — the POS can override the catalog price for membership products (custom pricing per customer, passed as order line item metadata).
- **Trial membership pricing** — $0 initial charge with deferred billing start date.

### 1.7 Tax

- `tax_status` per product: `taxable`, `shipping`, `none`.
- `tax_class` per product: `standard`, `reduced-rate`, `zero-rate`, or empty (inherit default).

### 1.8 Additional Product Fields

| Field | Type | Notes |
|---|---|---|
| `short_description` | text | Used on receipts |
| `manage_stock` | bool | Whether stock management is enabled |
| `backorders` | enum | `no`, `notify`, `yes` |
| `sold_individually` | bool | Limit to one per order |
| `purchase_note` | text | Shown on order confirmation |
| `shipping_class` | string | For shipping rate calculation |
| `tags` | JSON array | Product tags |
| `upsell_ids` / `cross_sell_ids` | JSON array | Related product IDs |
| `low_stock_amount` | int | Threshold for low-stock alerts |
| `virtual` | bool | No shipping required |
| `downloadable` | bool | Digital delivery |
| `weight` / `length` / `width` / `height` | decimal | Shipping dimensions |

---

## 2. Order Management

### 2.1 Order Creation

The platform must accept order creation via API with the following payload:

| Field | Required | Notes |
|---|---|---|
| `status` | Yes | Must support creating orders in `pending` status to avoid triggering stock-reduction hooks prematurely |
| `customer_id` | No | Link to a registered customer; 0 or null for guest checkout |
| `line_items` | Yes | Array of items (see below) |
| `fee_lines` | No | Negative fees for order-level discounts |
| `payment_method` | Yes | Machine-readable key (e.g., `credit_card`, `cod`, `cheque`, `bacs`, `split_payment`) |
| `payment_method_title` | Yes | Human-readable label (e.g., `"Credit Card (Visa ****1234)"`) |
| `transaction_id` | No | Gateway transaction ID |
| `billing` | No | `{first_name, last_name, email, phone, address_1, city, state, postcode, country}` |
| `shipping` | No | Same shape as billing |
| `customer_note` | No | Visible on customer receipt |
| `meta_data` | No | Array of `{key, value}` pairs |
| `created_via` | No | Origin label (e.g., `"DS POS"`) |

#### Line Item Structure

```json
{
  "product_id": 12345,
  "variation_id": 67890,
  "quantity": 2,
  "subtotal": "200.00",
  "total": "180.00",
  "name": "Product Name - Variation",
  "meta_data": [
    {"key": "_fulfillment_location", "value": "Boca Clinic"},
    {"key": "_ds_pos_original_price", "value": "100.00"},
    {"key": "_ds_pos_modified_price", "value": "90.00"}
  ]
}
```

Key semantics:
- `subtotal` = line total BEFORE item-level discount.
- `total` = line total AFTER item-level discount.
- The platform computes discount as `subtotal - total` (no per-unit rounding errors).
- `variation_id` must be accepted alongside `product_id` for variable products.

#### Fee Lines (Order-Level Discounts)

```json
{
  "name": "Discount (percentage)",
  "total": "-50.00",
  "tax_status": "none"
}
```

Negative fee totals represent discounts applied at the order level (not per-item).

### 2.2 Order Statuses

The platform must support at least these statuses:

| Status | Notes |
|---|---|
| `pending` | Initial creation status (no stock reduction triggered) |
| `processing` | Payment received, awaiting fulfillment |
| `on-hold` | Awaiting action |
| `completed` | Order fulfilled |
| `shipped` | Items have been dispatched |
| `partially-shipped` | Some items shipped |
| `delivered` | Items received by customer |
| `cancelled` | Order cancelled |
| `refunded` | Fully refunded |
| `failed` | Payment failed |
| `draft` | Incomplete order |
| `trash` | Soft-deleted |

The status update API must:
- Allow forced transitions (bypass standard transition rules).
- Accept a `force` parameter to override restrictions.

### 2.3 Order Status Determination

The POS determines final order status based on fulfillment locations of line items:
- All items fulfilled from clinic (non-shipping) → `completed`
- Any item requires shipping → `processing`
- Mixed → `processing` (shipping takes priority)

The platform must support a two-phase status flow: create as `pending`, then update to final status in a separate API call.

### 2.4 Order Notes

- API endpoint to add notes to an existing order: `POST /orders/{id}/notes`.
- Each note has: `note` (text), `customer_note` (bool — visible to customer vs. internal), `added_by_user` (bool).
- The POS adds internal admin notes with the format `[Staff Name] note text`.

### 2.5 Order Metadata

Extensive metadata is stored on orders:

| Key | Purpose |
|---|---|
| `_pos_order_id` | Link back to POS system order UUID |
| `_pos_order_number` | POS-format order number (e.g., `ORD-10001`) |
| `_created_by` | Email of the staff member who created the order |
| `_order_source` | Origin identifier (e.g., `"DS POS"`) |
| `_assigned_location` | Physical location where order was created |
| `_pos_location_name` | Display name for the POS location |
| `_order_origin` | Dynamic origin label (e.g., `"DS POS Boca"`) |
| `_contains_subscription` | Whether order has subscription items |
| `_card_brand` / `_card_last4` | Payment card details for display |
| `_pos_order_discount` | Discount amount applied at order level |
| `_pos_discount_amount` | Calculated discount dollar amount |
| `_skip_ghl_email` | Flag to suppress CRM email on this order |

### 2.6 Order Retrieval & Search

- List orders with pagination, filtering by: status, customer ID, date range.
- Search by order number (exact match) and by customer name (billing name search + customer ID lookup).
- Guest checkout orders (customer_id = 0) must be retrievable by billing email.
- Retrieve a single order by ID with full line items, notes, metadata, fee lines.
- Response headers: `X-WP-Total`, `X-WP-TotalPages` for pagination.

### 2.7 Payment Method Tracking

The POS records multiple payment method types:

| Type | API key | Notes |
|---|---|---|
| Credit Card | `credit_card` | Includes card brand + last 4 in title |
| Cash | `cod` | Cash on Delivery |
| Check | `cheque` | Check payment |
| Bank Transfer | `bacs` | Wire transfer |
| Split Payment | `split_payment` | Title lists component methods |
| Points Redemption | `points` | Loyalty points |
| Care Credit | `care_credit` | Financing |
| Outside POS | `outside_pos` | Payment handled externally |

Split payments store a JSON structure with `splitPayments` array, each containing `{method: {type, name, brand, last4}, amount}`.

---

## 3. Subscription Lifecycle

### 3.1 Subscription Creation

The platform must expose an API endpoint to create subscriptions directly (not only as a side-effect of purchasing a subscription product):

```json
{
  "status": "active",
  "customer_id": 123,
  "billing_period": "month",
  "billing_interval": 1,
  "start_date": "2026-03-01 10:00:00",
  "next_payment_date": "2026-04-01 10:00:00",
  "parent_id": 45678,
  "payment_method": "authnet",
  "payment_method_title": "Visa ending in 1234",
  "transaction_id": "abc123",
  "line_items": [{
    "product_id": 999,
    "quantity": 1,
    "subtotal": "284.10",
    "total": "284.10"
  }],
  "billing": { ... },
  "shipping": { ... },
  "meta_data": [ ... ]
}
```

### 3.2 Billing Periods

Supported billing periods and intervals:

| Period | Common Intervals | Examples |
|---|---|---|
| `day` | 1, 7, 14, 30 | Daily supplements |
| `week` | 1, 2, 4, 6 | Weekly/biweekly |
| `month` | 1, 2, 3, 6 | Monthly, quarterly, biannual |
| `year` | 1 | Annual memberships |

### 3.3 Trial Periods

- Subscription must support a deferred first payment (trial).
- The `next_payment_date` is set to `start_date + trial_period` (e.g., 3 months trial → first charge 3 months after creation).
- During the trial, the subscription status is `active` but the initial order line item total is $0.
- Metadata keys: `_is_trial_membership`, `_trial_period_months`, `_trial_end_date`, `_actual_membership_price`.

### 3.4 Payment Method Linking

After subscription creation, the POS links the customer's Authorize.net CIM payment profile to the subscription via an update API call with:

| Meta Key | Value |
|---|---|
| `payment_method` | `authnet` |
| `_authnet_customer_id` | CIM customer profile ID |
| `_authnet_card_id` | CIM payment profile ID |
| `_authnet_cc_type` | Card brand |
| `_authnet_cc_last4` | Last 4 digits |

This enables automatic renewal billing.

### 3.5 Subscription Status Management

| Status | Meaning |
|---|---|
| `active` | Billing normally |
| `on-hold` | Paused, not billing |
| `cancelled` | Permanently stopped |
| `pending-cancel` | Will cancel at end of current period |
| `expired` | Reached end date |
| `switched` | Upgraded/downgraded to another plan |

The API must support:
- Updating subscription status.
- Updating `next_payment_date`.
- Updating payment method and billing/shipping addresses.
- Retrieving subscriptions with filters: `customer`, `status`, `product`.
- Pagination headers (`X-WP-Total`, `X-WP-TotalPages`).

### 3.6 Subscription-Order Relationship

- A subscription has a `parent_id` linking to the originating order.
- The parent order stores `_related_subscriptions` (array of subscription IDs) and `_subscription_renewal: "parent"`.
- Renewal orders are created automatically by the platform and linked back to the subscription.

### 3.7 Recurring Price vs. Initial Charge

The recurring subscription price may differ from the initial charge on the parent order (e.g., order has a promotional discount, but renewals charge full price). The POS sets `subtotal` and `total` on subscription line items to the recurring amount, independent of the parent order's line item prices.

---

## 4. Membership Management

### 4.1 Membership Plans

- A membership is tied to a **plan** (e.g., "Doctors Studio Membership").
- Plans are defined in the platform and have an ID + name.
- Membership plans are product-linked: purchasing a membership product grants membership.

### 4.2 Membership Lifecycle

| Status | Meaning |
|---|---|
| `active` | Current member |
| `paused` | Temporarily suspended |
| `cancelled` | Membership ended |
| `expired` | Reached end date |
| `pending` | Awaiting payment/activation |
| `delayed` | Trial period, not yet started |
| `complimentary` | Free membership |

### 4.3 Membership Data Model

Each membership record contains:

| Field | Notes |
|---|---|
| `id` | Unique membership ID |
| `plan_id` / `plan_name` | Which plan this membership belongs to |
| `status` | Current lifecycle status |
| `start_date` | When membership began |
| `end_date` | When membership expires (null for unlimited) |
| `order_id` | Originating order |
| `subscription_id` | Linked subscription (for recurring memberships) |

### 4.4 Membership-Subscription Linking

When a membership product is purchased via API (as the POS does), the platform may auto-create the membership but fail to link it to the subscription. The POS explicitly links them after creation:
- Fetch customer memberships by customer ID.
- Find the unlinked membership (matching `order_id` or no `subscription_id`).
- Update the membership with `subscription_id`.

The API must support:
- Listing memberships filtered by `customer_id`.
- Updating a membership's `subscription_id` and `order_id`.

### 4.5 Direct Database Access

The POS reads membership data directly from the platform database (via SSH tunnel) for:
- Detailed membership status with start/end dates.
- Linked subscription details.
- Membership plan information.
- Product restriction lists.

This direct access is used because the REST API does not expose all needed fields. **A replacement platform must either expose this data via API or provide a read-only database replica.**

### 4.6 Email Toggle per Membership

The POS can toggle whether a membership sends email notifications. This is stored as membership metadata (`_ds_suppress_emails`).

---

## 5. Multi-Location Inventory

### 5.1 Location Model

Inventory locations represent physical places where stock is held:

| Field | Notes |
|---|---|
| `id` | Platform location ID |
| `name` | Human-readable name (e.g., "Boca Raton Clinic", "Warehouse") |
| `code` | Short code for URL/API use |
| `slug` | URL-friendly name |
| `is_default` | Whether this is the primary location |
| `parent_location_id` | For hierarchical locations |
| `barcode` | Physical barcode on the location |
| `product_count` | Number of products stocked here |

### 5.2 Per-Product Per-Location Stock

Each product (or variation) has an inventory record per location:

| Field | Notes |
|---|---|
| `product_id` | Which product |
| `variation_id` | Which variation (null for simple products) |
| `location_id` | Which location |
| `quantity` | Stock level (null = unmanaged) |
| `is_available` | Whether available for sale at this location |

The API must provide:
- `GET /products/{id}/inventories` — list all inventory records for a product.
- Each record includes: `inventory_id`, location name, stock quantity.

### 5.3 Stock Deduction on Order by Location

When creating an order, the POS specifies which location's stock to deduct for each line item using a structured field:

```json
{
  "mi_inventories": [{"inventory_id": 152, "qty": 2}]
}
```

This tells the platform's multi-inventory system exactly which inventory record to deduct stock from, rather than relying on default priority.

Additionally, a `_fulfillment_location` metadata key is added to each line item for reference.

### 5.4 Inventory Sync

After order creation, the POS:
1. Waits ~3 seconds for the platform to complete stock deduction.
2. Fetches updated inventory data via API for each product in the order.
3. Updates the local `ProductInventory` table.

The platform must also support **webhooks for stock/inventory changes** so the POS can react to changes made outside the POS (e.g., website orders, manual stock adjustments).

---

## 6. Customer Management

### 6.1 Customer Profile

| Field | Notes |
|---|---|
| `id` | Platform customer ID (numeric) |
| `email` | Unique identifier |
| `first_name` / `last_name` | |
| `phone` | |
| `billing` | Full billing address |
| `shipping` | Full shipping address |
| `meta_data` | Extensible key-value metadata |
| `role` | Customer role (e.g., `customer`, `subscriber`) |
| `date_created` | Registration date |

### 6.2 Customer Creation

The POS creates customers in the platform when a new contact places their first order. The API must accept: `email`, `first_name`, `last_name`, `billing`, `shipping`.

### 6.3 Customer Search

- Search by email (exact match).
- Search by name (partial match on first/last name).
- Retrieve customer by ID.
- List customers with pagination.

### 6.4 Customer Roles / Staff

The POS tracks a `_created_by` metadata field on orders identifying the staff member. The platform does not need to manage POS staff, but should support custom metadata on orders for attribution.

### 6.5 Points & Rewards

The platform must expose a customer's loyalty points balance:

| Field | Notes |
|---|---|
| `points_collected` | Total points earned all-time |
| `points_to_redeem` | Current redeemable balance |
| `rank` | Customer tier/level |
| `points_value` | Monetary value of points |

Access methods (in order of preference):
1. Customer meta fields (e.g., `_ywpar_user_total_points`, `_ywpar_user_total_earned_points`).
2. Dedicated points API endpoint.
3. Direct database query on `wp_usermeta`.

The platform must also support:
- **Updating points** — API endpoint to add/subtract points for a customer.
- **Points history** — log of point transactions with dates and reasons.
- **Fractional points** — points can be non-integer values.

---

## 7. Payment Gateway Integration

### 7.1 Authorize.net CIM (Customer Information Manager)

The POS integrates directly with Authorize.net (not through the ecommerce platform). The platform itself does not process payments — the POS handles all payment processing and passes the result (transaction ID, card info) to the platform as order metadata.

However, the platform must support:
- **Subscription auto-renewal via CIM**: The platform stores CIM profile IDs on subscriptions (via `_authnet_customer_id` and `_authnet_card_id` metadata) and can initiate charges for renewal orders.

### 7.2 POS Payment Operations

These are handled by the POS backend directly via Authorize.net API:

| Operation | Description |
|---|---|
| `process_payment` | One-time card charge (card number, expiry, CVV) |
| `create_customer_profile` | Tokenize a card into CIM |
| `charge_customer_profile` | Charge a saved card via CIM |
| `get_customer_payment_profiles` | List all saved cards for a customer |
| `get_customer_profile_by_email` | Find CIM profile by email |
| `refund_transaction` | Refund to original payment method |
| `void_transaction` | Void an unsettled transaction |

### 7.3 Saved Cards Discovery

The POS discovers saved cards through a multi-step chain:
1. Check `Contact.authorize_net_customer_profile_id` field.
2. If empty → check local `PaymentCard` records for a `customer_profile_id`.
3. If still empty → real-time lookup via CIM by email (auto-link to contact).
4. Fetch all payment profiles from CIM.
5. Fallback → local PaymentCard database.

### 7.4 Payment Card Storage

The POS stores tokenized card references locally:

| Field | Notes |
|---|---|
| `customer_profile_id` | CIM customer profile ID |
| `payment_profile_id` | CIM payment profile ID |
| `last4` | Last 4 digits |
| `card_brand` | Visa, Mastercard, etc. |
| `exp_month` / `exp_year` | Expiration |
| `is_default` | Default payment method |
| `billing_*` | Billing address on card |

### 7.5 Transaction Decline Handling

- Authorize.net can return `resultCode: "Ok"` but `responseCode: "2"` (declined). The POS checks both levels.
- Friendly error messages are mapped from ~25 common Authorize.net error codes.
- The platform need not handle this, but must accept orders with any payment method title/transaction ID.

---

## 8. Refunds

### 8.1 Refund Types

| Type | Description |
|---|---|
| **Full refund** | Entire order amount refunded |
| **Partial refund** | Selected items or partial amounts |
| **Gateway refund** | Reversed to original payment method via Authorize.net |
| **Manual/alternative refund** | Store credit, check, cash — recorded in POS only |

### 8.2 Gateway Refund Flow

1. POS finds the original Authorize.net transaction ID.
2. POS calls `refundTransaction` via Authorize.net SDK.
3. On success, POS creates a refund record locally (`POSOrderRefund` + `POSOrderRefundItem`).
4. POS creates a refund in the ecommerce platform via API.
5. POS updates the ecommerce order status to `refunded` (full) or leaves as-is (partial).

### 8.3 Platform Refund API

The platform must support:
- `POST /orders/{id}/refunds` — create a refund record with: amount, reason, line items, refunded via (gateway or manual).
- The platform should record the refund and adjust order totals.
- Refund line items: `{item_id, quantity, refund_total}`.

### 8.4 Refund Window

- Authorize.net has a 180-day refund window. After this, card data is archived and gateway refunds are impossible.
- The POS proactively checks order age and blocks gateway refunds after 180 days, directing staff to alternative methods.

### 8.5 Refund Receipts

- Refund receipts are generated by the POS (HTML email + PDF).
- Receipt data is derived from the POS refund records, not from the ecommerce platform.

### 8.6 Refund Data Model

| Field | Notes |
|---|---|
| `refund_number` | Sequential (e.g., `REF-10001`) |
| `order` | FK to original order |
| `refund_amount` | Total refund amount |
| `refund_reason` | Staff-entered reason |
| `payment_method_title` | How refund was issued |
| `transaction_id` | Gateway refund transaction ID |
| `created_by` | Staff member who processed refund |
| `assigned_location` | Where the refund was processed |
| Items | Individual line items with qty, price, reason |

---

## 9. Shipment Tracking

### 9.1 Tracking Data

Per-order tracking records:

| Field | Notes |
|---|---|
| `tracking_number` | Carrier tracking number |
| `tracking_provider` | Carrier name (e.g., "UPS", "FedEx", "USPS") |
| `date_shipped` | Ship date |
| `tracking_link` | URL to carrier tracking page (auto-generated or custom) |

### 9.2 API Requirements

- `GET /orders/{id}/shipment-trackings/` — list all trackings for an order.
- `GET /orders/{id}/shipment-trackings/{tracking_id}/` — get specific tracking.
- `POST /orders/{id}/shipment-trackings/` — create a tracking record.
- `PUT /orders/{id}/shipment-trackings/{tracking_id}/` — update.
- `DELETE /orders/{id}/shipment-trackings/{tracking_id}/` — delete.

### 9.3 Shipping Address Updates

The POS captures shipping address at checkout and passes it to the ecommerce platform order. If the customer has a stored shipping address, it's used as fallback. The platform must accept shipping address on order creation and allow updates.

### 9.4 Fulfillment Location Logic

Each line item has a `fulfillment_location` metadata field. The POS categorizes items as:
- **Clinic pickup** (no shipping needed) → order can be `completed` immediately.
- **Dropship / Warehouse** (needs shipping) → order stays in `processing`.
- **Mixed** → `processing` until all shipped items are dispatched.

---

## 10. Webhooks

### 10.1 Inbound Webhook Events

The POS listens for these webhook events from the ecommerce platform:

| Event | Handler | Purpose |
|---|---|---|
| `product.created` | Product sync | Add new product to local DB |
| `product.updated` | Product sync | Update product data, pricing, stock, variations, images, bundles, brands |
| `product.deleted` | Product sync | Remove or deactivate product |
| `order.updated` | Order status sync | Sync status changes from platform to POS order |
| `order.created` | Order sync | Detect orders created outside POS |
| `subscription.updated` | Subscription sync | Status changes, renewal events → sync to CRM |
| `subscription.created` | Subscription sync | New subscriptions from web |
| `stock.updated` | Inventory sync | Stock level changes at any location |

### 10.2 Webhook Security

- **HMAC signature verification**: Each webhook request includes a signature header. The POS verifies it using a shared secret.
- **Deduplication**: The POS checks for duplicate webhook deliveries using delivery ID/hash to prevent double-processing.

### 10.3 Webhook Logging

Every webhook delivery is logged with:

| Field | Notes |
|---|---|
| `webhook_type` | `product`, `order`, `subscription` |
| `status` | `received`, `processing`, `success`, `failed`, `skipped` |
| `source_ip` | Originating IP |
| `user_agent` | Platform identifier |
| `request_body` | Full payload (for debugging) |
| `action_taken` | What the handler did |
| `error_message` | On failure |
| `processing_time_ms` | Performance tracking |
| `order_id` | Related order/subscription ID |

### 10.4 Product Webhook Data

The product webhook payload must include the full product object with: all fields from §1, variations (for variable products), bundle children (for bundles), attributes, categories, images, and meta data.

---

## 11. CRM Integration

### 11.1 GoHighLevel (GHL) Contact Sync

The POS maintains a bidirectional sync between the ecommerce platform and GoHighLevel CRM:

| Data Flow | Trigger | Data |
|---|---|---|
| Platform → GHL | Order created/updated | Contact info, tags, custom fields |
| Platform → GHL | Subscription status change | Membership status tags |
| Platform → GHL | Credit points change | Custom field with point balance |
| GHL → POS | Appointment created | Appointment data for scheduling views |

### 11.2 GHL Data Points

- **Tags**: Applied/removed based on membership status, subscription status, purchase history.
- **Custom fields**: Credit service point balances synced per product.
- **Contact ID**: Each contact stores a `ghl_contact_id` for direct API calls.

### 11.3 Skip Email Flag

Orders can carry a `_skip_ghl_email: "yes"` metadata flag. When present, the POS fires a webhook to GHL instructing it to suppress the order confirmation email for that customer.

### 11.4 Appointment Data

The POS reads appointment data from a separate GHL database (via SSH tunnel) for the appointments view. This does not depend on the ecommerce platform.

---

## 12. Receipts & Communications

### 12.1 Order Receipts

- Generated by the POS backend from order data + line items + payment info.
- HTML email template with embedded styling.
- Includes: order number, items with prices, discounts, totals, payment method, billing/shipping addresses, subscription details if applicable.
- Sent via Django email backend.

### 12.2 Refund Receipts

- Separate receipt template for refunds.
- Includes: refund number, original order reference, refunded items, refund amount, refund method, reason.
- Available as both HTML email and PDF download.

### 12.3 WordPress-Side Receipts

A WordPress snippet (`ds-refund-receipt-pdf.php`) generates PDF refund receipts on the platform side. The replacement must either:
- Provide server-side PDF generation for refund receipts, OR
- The POS will handle all receipt generation (currently moving toward this).

### 12.4 Email Toggle

Per-membership email suppression: the POS can toggle a metadata flag on memberships to suppress platform-generated emails (e.g., renewal reminders, expiration notices) for specific customers.

---

## 13. Reporting

### 13.1 Report Types

All reports operate on POS order data (local Django DB) with optional enrichment from the ecommerce platform database:

| Report | Data Source | Filters |
|---|---|---|
| **Executive KPIs** | POS orders | Date range, location, cashier |
| **Sales Over Time** | POS orders | Granularity: hour/day/week/month |
| **Sales by Location** | POS orders | Date range |
| **Sales by Cashier** | POS orders + WC `_created_by` meta | Date range, location |
| **Sales by Payment Method** | POS orders | Date range, location |
| **Product Performance** | POS order items | Date range, category, brand, sort by revenue/units/orders |
| **Category Performance** | POS order items | Date range, location |
| **Customer Analytics** | POS orders + contacts | Date range, segments |
| **Refund Report** | POS refunds | Date range, location, gateway |
| **Payment Transactions** | Payment transactions | Date range, status |
| **Subscription Analytics** | WC database (HPOS) | MRR, status breakdown |
| **Discount Analysis** | POS order items | Date range, by source/type |
| **Hourly Heatmap** | POS orders | Date range, hour × day-of-week |
| **Reconciliation** | POS orders + transactions | Date range |
| **Team Performance** | WC `_created_by` + POS orders | Date range, POS vs. online |

### 13.2 Platform Dependencies for Reporting

The ecommerce platform must provide:
- **`_created_by` metadata on orders** — to attribute sales to team members.
- **Subscription data with billing period/interval** — to calculate MRR.
- **Order status in HPOS format** — for subscription status breakdown.
- These are currently queried via direct database access (SSH tunnel to MariaDB). A replacement must expose this data via API or provide a read replica.

### 13.3 KPI Metrics

| Metric | Calculation |
|---|---|
| Net Sales | Sum of order totals (excl. trash/cancelled/draft/failed) |
| Total Orders | Count of valid orders |
| Average Order Value | Net Sales / Total Orders |
| Units Sold | Sum of line item quantities |
| Refund Total / Count / Rate | From refund records |
| Unique Customers | Distinct contact IDs |
| New vs. Returning | Based on whether customer's first-ever order is in the period |

---

## 14. REST API Surface

### 14.1 Authentication

- OAuth 1.0a (consumer key + consumer secret) for server-to-server API calls.
- The POS backend authenticates with the ecommerce platform using API keys stored in environment variables.

### 14.2 Pagination

- Standard pagination headers: `X-WP-Total` (total items), `X-WP-TotalPages` (total pages).
- Query parameters: `page`, `per_page` (max 100).

### 14.3 Filtering

Products: `status`, `type`, `search`, `sku`, `category`, `tag`.
Orders: `status`, `customer`, `search`, `after`, `before`, `per_page`.
Subscriptions: `status`, `customer`, `product`.
Customers: `email`, `search`, `role`.

### 14.4 Search

- `search` parameter performs text search on relevant fields (product name, order number, customer name/email).
- SKU lookup: `GET /products?sku={sku}` returns matching product.

### 14.5 Bulk Operations

- Product sync fetches all products page-by-page (`per_page=100`, iterating pages).
- Variation sync fetches all variations for a product in one call.
- No batch create/update API is currently used, but would be beneficial for initial sync.

### 14.6 Retry & Error Handling

- The POS uses automatic retry (3 attempts) with exponential backoff on transient failures.
- API timeout: 120 seconds.
- In-memory caching of product/variation/inventory data within a single request lifecycle to avoid redundant API calls during order creation.

### 14.7 API Endpoints Summary

| Resource | Endpoints |
|---|---|
| Products | GET list, GET single, POST create, PUT update, GET by SKU |
| Variations | GET list (by parent), GET single, POST create, PUT update, DELETE |
| Attributes | GET list |
| Attribute Terms | GET list (by attribute), POST create |
| Orders | GET list, GET single, POST create, PUT update |
| Order Notes | POST create |
| Order Refunds | POST create |
| Subscriptions | GET list, GET single, POST create, PUT update |
| Customers | GET list, GET single, POST create, PUT update |
| Customer Points | GET (via meta or dedicated endpoint), PUT update |
| Memberships | GET list (by customer), PUT update |
| Shipment Tracking | GET list, GET single, POST create, PUT update, DELETE |
| Product Inventories | GET list (by product) |

---

## 15. Credit / Loyalty Programs

### 15.1 Points Balance

Two separate points systems:

**A. Platform Loyalty Points (e.g., YITH Points & Rewards)**
- Per-customer balance stored as customer metadata.
- Points earned on purchases, redeemable for discounts.
- Fields: `points_collected`, `points_to_redeem`, `rank`, `points_value`.

**B. Credited Service Points (POS-native)**
- Per-customer, per-product point balances.
- Products with "series" (e.g., 3-pack of treatments) grant credit points on purchase.
- Points are redeemed when the customer uses the service.
- Tracked in local `CreditServicePoints` table with full audit log.
- Synced to GHL CRM custom fields.

### 15.2 Points Operations

| Operation | Description |
|---|---|
| Add (on purchase) | Auto-credit based on series value of purchased product |
| Redeem (on service use) | Deduct 1 point when service is consumed |
| Manual add/subtract | Staff can adjust points with reason |
| History log | Every change logged with: action, points_change, order_number, reason, source |

### 15.3 Eligibility Rules

- Only **variable products with series** are eligible for credit points.
- A `WooServiceTypes` table defines which products are eligible and which series values qualify.
- The POS checks eligibility before allowing redemption.

### 15.4 Platform Requirements

- The platform must either provide a points API or allow the POS to read/write customer metadata for point balances.
- Direct database access is currently used as a fallback for reading points from `wp_usermeta`.

---

## 16. Product Editor

### 16.1 Capabilities

The POS includes a full product editor that syncs changes to the ecommerce platform:

| Operation | Local DB | Platform Sync |
|---|---|---|
| View full product details | ✓ | Read from API |
| Update product fields | ✓ | PUT to platform |
| Sync product from platform | ✓ | Overwrite local with API data |
| Create variation | ✓ | POST to platform |
| Update variation | ✓ | PUT to platform |
| Delete variation | ✓ | DELETE from platform |
| List product attributes | — | GET from platform |
| List attribute terms | — | GET from platform |
| Create attribute term | — | POST to platform |
| Search products (for linking) | ✓ | Local DB search |

### 16.2 Editable Fields

Via the product editor, staff can modify:
- Name, description, short description
- Regular price, sale price, sale date range
- SKU
- Stock quantity, manage stock, backorders
- Tax status, tax class
- Weight, dimensions
- Categories, tags
- Images
- Attributes (including brand)
- Upsell/cross-sell product links
- Shipping class
- Product status (publish/draft)
- Per-variation: SKU, price, stock, attributes, image, status

### 16.3 Bidirectional Sync

- **Platform → POS**: `sync_product_from_woocommerce` fetches fresh data from API and overwrites local product record.
- **POS → Platform**: `update_product_full_sync` pushes local changes to the platform via PUT API.
- Webhooks keep local data fresh for changes made outside the POS.

---

## 17. Saved Carts

### 17.1 Data Model

| Field | Notes |
|---|---|
| `name` | Unique cart name (globally unique across staff) |
| `user` | Staff member who created the cart |
| `customer` | Optional linked customer |
| `customer_id` | FK to Contact |
| `cart_items` | JSON array of cart item objects |
| `order_discount` | Optional order-level discount amount |
| `order_discount_type` | `percentage` or `dollar` |

### 17.2 Operations

- **Create**: Save current cart state with name + items + customer.
- **List**: All saved carts (shared across all staff).
- **Load**: Restore cart items and customer to the POS.
- **Update**: Modify an existing saved cart.
- **Delete**: Remove a saved cart.

### 17.3 Platform Independence

Saved carts are entirely POS-native (stored in Django DB). The ecommerce platform is not involved. However, cart items reference product IDs that must be resolvable against the ecommerce catalog when loaded.

---

## 18. POS Settings

### 18.1 Settings Model

Global settings stored in Django DB, shared across all staff in real-time:

| Field | Notes |
|---|---|
| `key` | Unique setting name |
| `value` | Stored as string, converted by type |
| `setting_type` | `boolean`, `string`, `integer`, `float`, `json` |
| `description` | Human-readable explanation |
| `category` | Grouping (e.g., `inventory`, `shipping`, `general`) |
| `updated_by` | Last user to change the setting |

### 18.2 Known Settings

| Key | Type | Purpose |
|---|---|---|
| `prevent_out_of_stock_cart` | boolean | Block adding out-of-stock items to cart |
| `payment_plans_enabled` | boolean | Enable/disable payment plan feature |
| `payment_plans_min_threshold` | float | Minimum order amount for payment plans |
| Various shipping/tax settings | mixed | Configure shipping and tax behavior |

### 18.3 Platform Independence

POS settings are entirely local to the POS. No ecommerce platform dependency.

---

## 19. Payment Plans (Partial Orders)

### 19.1 Overview

Payment plans allow customers to pay for large orders in installments. The **full order is submitted to the ecommerce platform immediately** (so the customer receives all products/services), but the POS tracks installment payments separately.

### 19.2 Plan Templates

Pre-configured installment structures:

| Field | Notes |
|---|---|
| `name` | e.g., "50/25/25 — 3 Monthly Installments" |
| `num_installments` | Total number of payments |
| `installments_config` | Array of `{percentage, offset_days}` |
| `service_charge_type` | `none`, `percentage`, `flat` |
| `service_charge_value` | Charge amount/percentage |

### 19.3 Payment Plan Model

| Field | Notes |
|---|---|
| `plan_number` | Sequential (e.g., `PP-10001`) |
| `pos_order` | 1:1 link to the POS order |
| `contact` | Customer |
| `total_amount` | Full amount including service charge |
| `original_amount` | Order amount before service charge |
| `service_charge_amount` | Calculated charge |
| `status` | `active`, `completed`, `cancelled`, `defaulted` |
| `payment_method` | Default card for auto-billing (CIM profile JSON) |

### 19.4 Installments

Each installment:

| Field | Notes |
|---|---|
| `installment_number` | 1-indexed |
| `amount` | Scheduled payment amount |
| `due_date` | When payment is due |
| `status` | `pending`, `scheduled`, `processing`, `paid`, `failed`, `overdue`, `cancelled` |
| `card_override` | Per-installment card override |
| `transaction_id` | Gateway transaction ID after payment |
| `paid_at` / `paid_amount` | Actual payment details |
| `retry_count` | Auto-billing retry attempts |

### 19.5 Auto-Billing

A scheduled command (`process_installments`) runs daily:
1. Finds installments due today (or overdue).
2. Charges the customer's saved card via Authorize.net CIM.
3. On success: marks installment as `paid`, records transaction ID.
4. On failure: increments retry count, marks as `failed`, logs reason.
5. When all installments are paid: marks plan as `completed`.

### 19.6 Platform Interaction

- The full order is created in the ecommerce platform at plan initiation (first installment payment).
- The order total in the platform equals the full order amount.
- Subsequent installment payments are tracked only in the POS — the platform sees one completed order.
- The POS sends installment receipts and plan summary receipts via email.

### 19.7 Ecommerce Platform Requirement

The platform must accept orders where the recorded payment amount (first installment) is less than the order total. The order is still marked as `completed` or `processing` — the remaining balance is handled outside the platform's payment flow.

---

## Appendix A: WordPress Snippet Dependencies

The following server-side behaviors are currently implemented as WordPress snippets. A replacement platform must provide equivalent capabilities either natively or via extensibility:

| Snippet | Capability |
|---|---|
| **Pricing lock** | Prevent the platform from overriding POS-set prices with catalog prices during order creation |
| **Membership pricing fix** | Ensure modified membership prices from POS are respected (not auto-discounted by membership rules) |
| **Prevent auto-membership discount** | Stop the platform from applying membership-based discounts on POS orders (POS handles its own discounts) |
| **Force fulfillment location** | Ensure per-line-item fulfillment location metadata is preserved through order processing |
| **Refund receipt PDF** | Server-side PDF generation for refund receipts |
| **Tracking email trigger** | Send shipping notification email when tracking is added to an order |
| **Points update endpoint** | Custom REST endpoint for updating customer loyalty points |
| **Discount price handling** | Preserve POS-calculated discount prices through order save hooks |

## Appendix B: Direct Database Access Points

The POS accesses the ecommerce platform's database directly (via SSH tunnel to MariaDB) for data not available through the REST API:

| Query | Tables | Purpose |
|---|---|---|
| Customer memberships | `wp_posts` (type=wc_user_membership), `wp_postmeta` | Full membership data with dates |
| Subscription details | `wp_wc_orders` (type=shop_subscription), `wp_wc_orders_meta` | Billing period, interval, status, MRR calculation |
| Order metadata | `wp_wc_orders_meta` | `_created_by` for team performance reports |
| Customer points | `wp_usermeta` | YITH points balance (fallback) |
| Points history | Custom points tables | Transaction history for points |
| Membership plan restrictions | `wp_postmeta` | Product restriction lists for memberships |

**A replacement platform must expose all of these data points through its REST API** to eliminate the need for direct database access.

---

*Document generated: March 3, 2026*
*Source: Full codebase investigation of POS backend views, models, WooCommerce API wrapper, webhooks, frontend services, payment integration, and WordPress snippets.*
