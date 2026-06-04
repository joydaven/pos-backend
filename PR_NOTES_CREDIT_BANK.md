# PR: Credit Bank System

**Branch:** `payments_update` → `main`
**Repos:** `woo-ghl-contact-db` (backend) + `drs_all_in_one` (frontend)
**Date:** March 9, 2026

---

## Summary

Introduces a POS-side Credit Bank system — a dollar-balance account per customer that can be loaded by purchasing "BANK: Credit Bank $X" products and redeemed as a payment method at checkout. Fully integrated into the order flow, receipts, and customer management UI.

---

## Backend Changes (`woo-ghl-contact-db`)

### New Django App: `credit_bank`

| File | Description |
|------|-------------|
| `backend/credit_bank/models.py` | `CreditBankAccount` (OneToOne with Contact, decimal balance) + `CreditBankTransaction` (full audit trail) |
| `backend/credit_bank/views.py` | REST endpoints: balance GET, add POST, redeem POST, adjust POST, transactions GET |
| `backend/credit_bank/urls.py` | Mounted at `api/credit-bank/` |
| `backend/credit_bank/admin.py` | Django admin registration |
| `backend/credit_bank/migrations/0001_initial.py` | Creates `credit_bank_account` and `credit_bank_transaction` tables |

- Registered in `settings.py` `INSTALLED_APPS` and `doctorsstudio/urls.py`

### Order Creation (`views.py`)

- **Auto-credit on purchase**: After WooCommerce order creation succeeds, scans order items for products matching `BANK: Credit Bank $X` pattern (regex: `(?i)bank:\s*credit\s*bank\s*\$?([\d,]+(?:\.\d{2})?)`) and automatically adds credits to the customer's bank account via `CreditBankAccount.add_credits()`. Handles quantity multiplier.
- **Credit bank meta on WooCommerce order**: Parses `[CREDIT_BANK_REDEEMED:amount:remaining]` flag from order notes and adds `_credit_bank_redeemed` + `_credit_bank_remaining` meta to the WooCommerce order for receipt rendering.

### Receipts (`views_receipts.py`)

- New helper `_get_credit_bank_info()` extracts credit bank data from WooCommerce order meta
- Returns `{ redeemed, remaining, starting }` where starting = redeemed + remaining
- **PDF receipt** (`_build_pdf_receipt_html`): Cyan-themed Credit Bank section with Starting Balance, Amount Redeemed, Remaining Balance
- **Email receipt** (`_build_order_receipt_html`): Matching Credit Bank section

---

## Frontend Changes (`drs_all_in_one`)

### New Service

| File | Description |
|------|-------------|
| `src/services/api/creditBankService.ts` | Full API client: `getBalance`, `getTransactions`, `addCredits`, `redeemCredits`, `adjustCredits` |

### CustomerDetailsModal — New "Credit Bank" Tab

- **Tab index 6** (between Points & Rewards and Order History)
- Balance display card with Add/Remove Credits buttons
- Adjustment modal (add or subtract with description)
- Transaction history table with pagination
- All `setSelectedTab(6)` references updated to `setSelectedTab(7)` for Order History navigation

### PaymentModal — Credit Bank Payment Method

- Shows as "Credit Bank" tab when customer has balance > 0
- Balance display + "Amount to Use" input with "Pay Full" button
- Split payment support: `creditBankUsed` field on `SplitPayment` interface
- Redemption via `creditBankService.redeemCredits()` in both:
  - `handlePaymentSubmit` (direct payment)
  - `handleCompleteSplitPayment` (split payment mode)
- `buildNotesWithFlags()` embeds `[CREDIT_BANK_REDEEMED:amount:remaining]` in order notes (checks both direct state and split payment entries)

### Receipts — Print Receipt Consistency

All three receipt channels now show identical Credit Bank section:

| Location | File |
|----------|------|
| PDF + Email | `views_receipts.py` (backend) |
| Manual Print (Customer Details) | `CustomerDetailsModal.tsx` → `receiptHtmlBuilder.ts` |
| Manual Print (Order History) | `OrderReceipt.tsx` → `receiptHtmlBuilder.ts` |

- `PrintOrderReceiptData` interface extended with `creditBank?: { starting, redeemed, remaining }`
- `buildOrderPrintHtml` renders cyan-themed Credit Bank section matching backend templates
- Both `handlePrint` (CustomerDetailsModal) and `printReceipt` (OrderReceipt) extract `_credit_bank_redeemed` / `_credit_bank_remaining` from WooCommerce `meta_data`

---

## Tab Index Reference

```
0 = Details
1 = Payment Info
2 = Membership Info
3 = Subscription
4 = Credited Services
5 = Points & Rewards
6 = Credit Bank       ← NEW
7 = Order History      ← shifted +1
8 = Appointments       ← shifted +1
9 = Notes              ← shifted +1
```

---

## Database Changes

- New table: `credit_bank_account` (UUID PK, FK to Contact, decimal balance)
- New table: `credit_bank_transaction` (UUID PK, FK to account + Contact, type, amount, balance_after, order_id, description, metadata JSON)
- Indexes on customer, account, transaction_type, order_id, and composite (customer + created_at)

---

## How to Test

### 1. Auto-credit on purchase
1. Search "credit bank" in POS
2. Add "BANK: Credit Bank $1500" to cart for a customer
3. Complete order
4. Open customer → Credit Bank tab → should show $1500.00 balance with transaction

### 2. Credit bank as payment method
1. Add any product to cart for a customer with credit bank balance
2. Open payment → Credit Bank tab appears with balance
3. Enter amount to use → Add Payment
4. Complete order → balance should deduct

### 3. Receipt verification
1. After credit bank payment, check all 3 receipt channels:
   - Email receipt (Credit Bank section with Starting/Redeemed/Remaining)
   - PDF attached to email (same)
   - Print Receipt button in Order History (same)

### 4. Manual adjustments
1. Open customer → Credit Bank tab
2. Click "Add Credits" or "Remove Credits"
3. Enter amount and description → balance updates with transaction log

---

## Merge Notes

- Merged `origin/main` into `payments_update` on both repos
- Single conflict resolved in `PaymentModal.tsx`: kept both `skipReceiptEmail` flag (from main) and credit bank flag (from this branch)
