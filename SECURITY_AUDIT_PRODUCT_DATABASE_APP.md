# Security Audit — ds-product-database.web.app (Remote)

**Date:** March 11, 2026  
**Scope:** Remote black-box security assessment of `https://ds-product-database.web.app/`  
**Method:** Static analysis of client-side JavaScript bundle, HTTP header inspection, Firebase/Firestore rule probing, credential validation  
**Severity Scale:** CRITICAL / HIGH / MEDIUM / LOW / INFO

---

## Executive Summary

The site is a React + Firebase (Firestore) product management tool for Doctors Studio. The most critical finding is that **live WooCommerce REST API credentials with read/write access are hardcoded in the client-side JavaScript bundle**, publicly accessible to anyone who views the page source. These credentials are confirmed working and grant full product CRUD access to the production WooCommerce store at `store.doctorsstudio.com`. Additionally, the Cloud Functions sync endpoint has no authentication.

---

## CRITICAL Findings

### 1. Hardcoded WooCommerce API Credentials in Client-Side Bundle

**Severity: CRITICAL**  
**CVSS ~9.1** — Publicly exposed production credentials with write access

The minified JavaScript bundle (`/assets/index-DC6yAg1d.js`) contains hardcoded WooCommerce REST API credentials in plain text:

```
siteUrl: "https://store.doctorsstudio.com"
consumerKey: "ck_5f9afe9a2b34a2271b824219b7bc2e64ab0246fb"
consumerSecret: "cs_6b8fed362aa18106b4ecc0a140221d46bc1ebd2c"
```

These appear in **two locations** in the bundle:
1. As default state in the Settings page component (`Y3` function)
2. As fallback defaults in the `At()` config helper function

**Confirmed impact:**
- The credentials are **live and working** — a `GET /wc/v3/products?per_page=1` request returns HTTP 200 with full product data
- The WooCommerce API response includes `targetHints.allow: ["GET","POST","PUT","PATCH","DELETE"]` — these are **read/write** keys, not read-only
- An attacker can: **list all products**, **modify prices**, **create/delete products**, **read order data** (if order scope is granted), **access customer data**, **read ATUM inventory data** including purchase prices (cost of goods) and supplier info

**The credentials are also used to construct URLs like:**
```javascript
De(`${Jt(r)}/wc/v3/products/${t}`, s, i)
// Produces: https://store.doctorsstudio.com/wp-json/wc/v3/products/123?consumer_key=ck_...&consumer_secret=cs_...
```

The credentials are passed as **query parameters** (not headers), which means they will appear in:
- Server access logs
- CDN/proxy logs
- Browser history
- Any intermediary that logs URLs

**Remediation:**
1. **Immediately revoke** the exposed consumer key/secret pair in WooCommerce Admin → Settings → Advanced → REST API
2. Generate **new read-only** keys for the Product Database app
3. Move WooCommerce API calls to a **backend proxy** (Cloud Function or server) so credentials are never sent to the browser
4. If a backend proxy is not feasible, at minimum use environment variables via `VITE_WC_CONSUMER_KEY` / `VITE_WC_CONSUMER_SECRET` (the code already references these) and ensure `.env` is not committed — but note this only prevents accidental git exposure, NOT client-side exposure since Vite bundles `VITE_*` vars into the build

---

### 2. WooCommerce API Keys Have Write Permissions

**Severity: CRITICAL**  
**Extends Finding #1**

The exposed keys have **read/write** permissions. The bundle contains code that performs:
- `PUT /wc/v3/products/{id}` — update any product (price, status, description, stock)
- `POST /wc/v3/products` — create new products
- `DELETE /wc/v3/products/{id}` — delete/trash products
- `POST /wc/v3/products/{id}/variations` — create variations
- `POST /wc/v3/products/{id}/variations/batch` — batch update variations
- `POST /wc/v3/atum/inventories/batch` — modify inventory records

An attacker could:
- Change product prices to $0.01 and purchase them
- Delete the entire product catalog
- Modify product descriptions to include malicious content (XSS on the store)
- Manipulate stock levels and inventory data
- Access and modify ATUM purchase prices (confidential cost data)

**Remediation:**
- Even after rotating keys, the new keys for a client-side app should be **read-only**
- All write operations should go through an authenticated backend (Cloud Function) that validates the user's Firebase auth token before proxying to WooCommerce

---

## HIGH Findings

### 3. Unauthenticated Cloud Functions Endpoint

**Severity: HIGH**

The bundle reveals a Cloud Functions endpoint used for Firestore cache sync:

```
https://us-central1-ds-product-database.cloudfunctions.net/syncProductsHttp
```

This endpoint is called via `GET` with no authentication token:
```javascript
const t = "https://us-central1-ds-product-database.cloudfunctions.net/syncProductsHttp";
await fetch(t, { method: "GET" })
```

**Impact:**
- Anyone can trigger a product sync, potentially causing excessive API calls to WooCommerce (DoS on WooCommerce rate limits)
- If the sync function uses admin-level WooCommerce credentials, the function itself becomes a proxy for elevated access
- Repeated calls could exhaust Cloud Functions billing quota

**Remediation:**
- Add Firebase Auth token verification in the Cloud Function (`verifyIdToken`)
- Or use Firebase callable functions which automatically include auth context

---

### 4. Firebase Configuration Exposed (Info Disclosure)

**Severity: MEDIUM**

The Firebase project configuration is embedded in the bundle:

```javascript
{
  apiKey: "AIzaSyDr4bA1tTBawXddLzDHZFAM44w1yBIH4Ww",
  authDomain: "ds-product-database.firebaseapp.com",
  projectId: "ds-product-database",
  storageBucket: "ds-product-database.firebasestorage.app",
  messagingSenderId: "891946159111",
  appId: "1:891946159111:web:f3695c3c8f602de55deea2"
}
```

**Note:** Firebase API keys are *designed* to be public and are not secrets by themselves. However, they enable:
- Enumeration of whether the project exists
- Abuse of Firebase Auth (password spraying, account enumeration) if email/password auth is enabled
- Abuse of any Firebase service where security rules are misconfigured

**Positive finding:** Firestore security rules appear properly configured — unauthenticated REST API requests to both `users` and `products_cache` collections return `403 PERMISSION_DENIED`. No Realtime Database is configured (404).

---

### 5. Domain-Based Auth Bypass Risk

**Severity: MEDIUM**

Authentication restricts login to `@doctorsstudio.com` Google accounts:
```javascript
const Hd = "doctorsstudio.com";
// Check: d.email?.split("@")[1] !== Hd
```

However:
- The domain check happens **client-side only** after Google OAuth popup
- The `hd` hint is set via `setCustomParameters({hd: "doctorsstudio.com"})` which is only a UI hint, not enforcement
- If Firestore rules don't independently verify the email domain, a user who bypasses the client-side check (by modifying the JS) could access the app with any Google account
- New users auto-provisioned as `viewer` role, with role stored in Firestore `users/{uid}` doc

**Remediation:**
- Verify email domain in Firestore security rules: `request.auth.token.email.matches('.*@doctorsstudio\\.com$')`
- Or use Firebase Auth blocking functions to reject sign-ups from non-approved domains server-side

---

### 6. Client-Side Role Enforcement Only

**Severity: MEDIUM**

Role-based access (admin/editor/viewer) is enforced entirely client-side:
```javascript
const { isViewer, isAdmin } = jt();
// Used to hide/show UI elements and disable form fields
```

The roles (`admin`, `editor`, `viewer`) are stored in Firestore and checked in React components, but:
- All WooCommerce API calls go **directly from the browser** using the hardcoded credentials
- A `viewer` user can open browser DevTools, call the WooCommerce API directly with the exposed credentials, and perform write operations
- There is no backend intermediary to validate roles before executing writes

**Remediation:**
- Route all WooCommerce write operations through Cloud Functions that verify both Firebase auth token AND user role from Firestore before proxying

---

## LOW Findings

### 7. Missing Security Headers

**Severity: LOW**

The Firebase Hosting response is missing several recommended security headers:

| Header | Status |
|--------|--------|
| `Strict-Transport-Security` | ✅ Present (31556926s, includeSubDomains, preload) |
| `Content-Security-Policy` | ❌ Missing |
| `X-Content-Type-Options` | ❌ Missing |
| `X-Frame-Options` | ❌ Missing |
| `Referrer-Policy` | ❌ Missing |
| `Permissions-Policy` | ❌ Missing |

**Impact:** Without CSP, the app is more vulnerable to XSS. Without X-Frame-Options, the app could be embedded in an iframe for clickjacking attacks.

**Remediation:** Add headers in `firebase.json`:
```json
{
  "hosting": {
    "headers": [
      {
        "source": "**",
        "headers": [
          { "key": "X-Content-Type-Options", "value": "nosniff" },
          { "key": "X-Frame-Options", "value": "DENY" },
          { "key": "Content-Security-Policy", "value": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' https: data:; connect-src 'self' https://*.firebaseio.com https://*.googleapis.com https://store.doctorsstudio.com https://us-central1-ds-product-database.cloudfunctions.net" },
          { "key": "Referrer-Policy", "value": "strict-origin-when-cross-origin" },
          { "key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()" }
        ]
      }
    ]
  }
}
```

---

### 8. Audit Log Stored in localStorage Only

**Severity: LOW**

The audit log (product edits, creates, deletes) is stored entirely in `localStorage`:
```javascript
const ym = "ds_audit_log";
localStorage.setItem(ym, JSON.stringify(l));
```

**Impact:**
- Any user can clear their audit trail by clearing browser storage
- Audit log is per-browser, not centralized — no cross-device audit trail
- An attacker who makes changes can erase evidence

**Remediation:** Write audit entries to Firestore (server-side, via Cloud Functions triggered by writes)

---

### 9. Credentials Stored in localStorage

**Severity: LOW**

When a user saves WooCommerce credentials on the Settings page, they're stored in `localStorage`:
```javascript
localStorage.setItem("ds_wp_config", JSON.stringify(t));
```

Any XSS vulnerability or malicious browser extension can read these credentials.

---

## INFO Findings

### 10. Build Hash Exposed

The build hash `814cc73` is embedded in the bundle for cache invalidation. Minor info disclosure.

### 11. Firebase SDK Versions

- Firebase SDK: `12.10.0`
- Firestore: `4.12.0`

These appear reasonably current.

### 12. Positive Findings

- ✅ Firestore rules block unauthenticated reads (403 on `users` and `products_cache`)
- ✅ No Realtime Database exposed (404)
- ✅ HTTPS enforced via Firebase Hosting with HSTS preload
- ✅ Google OAuth used (no password-based auth to brute force)
- ✅ Domain restriction on login (though client-side only)

---

## Priority Remediation Roadmap

| Priority | Action | Effort |
|----------|--------|--------|
| **P0 — NOW** | Revoke exposed WooCommerce API keys | 5 min |
| **P0 — NOW** | Generate new **read-only** keys (if client-side calls must remain temporarily) | 5 min |
| **P1 — This week** | Move all WooCommerce write operations to authenticated Cloud Functions | 2-3 days |
| **P1 — This week** | Add auth token verification to `syncProductsHttp` Cloud Function | 1 hour |
| **P1 — This week** | Add server-side domain verification for Firebase Auth (blocking function or Firestore rules) | 2 hours |
| **P2 — Next sprint** | Move WooCommerce read operations to Cloud Functions (eliminate client-side credentials entirely) | 2-3 days |
| **P2 — Next sprint** | Add security headers to `firebase.json` | 30 min |
| **P3 — Backlog** | Move audit log to Firestore | 1 day |
| **P3 — Backlog** | Implement server-side role enforcement for write operations | 1 day |
