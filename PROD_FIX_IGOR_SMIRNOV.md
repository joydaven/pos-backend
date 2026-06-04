# Production Fix: Igor Smirnov (avluga@gmail.com) — Membership Not Showing

**Date**: 2026-03-13
**Applied on staging**: Yes
**Issue**: The `avluga@gmail.com` contact has `woo_customer_id=None` in the local POS database. The correct WooCommerce customer ID (9289) is on a duplicate contact (`i.smirnov@srx.aero`). This causes the Membership Info tab to fail with "Error loading membership products".

> **Note**: Staging and production share the same WooCommerce store, so the WooCommerce-side fixes (subscription, order, membership) were already applied on staging and are live. Only the **local POS database** change is needed on production.

---

## Production change: Move woo_customer_id to correct contact

There are two duplicate contacts for Igor Smirnov. The `woo_customer_id=9289` needs to be on the `avluga@gmail.com` contact, not the `i.smirnov@srx.aero` one.

```bash
source /path/to/backend/venv/bin/activate
cd /path/to/backend

python3 manage.py shell -c "
from crm.models import Contact

# Verify the two contacts exist
c1 = Contact.objects.filter(email='avluga@gmail.com', first_name__icontains='igor').first()
c2 = Contact.objects.filter(email='i.smirnov@srx.aero', first_name__icontains='igor').first()

print(f'avluga contact: id={c1.id}, woo_customer_id={c1.woo_customer_id}')
print(f'srx.aero contact: id={c2.id}, woo_customer_id={c2.woo_customer_id}')

# Only proceed if woo_customer_id is on the wrong contact
if c2.woo_customer_id == 9289 and c1.woo_customer_id is None:
    c2.woo_customer_id = None
    c2.save(update_fields=['woo_customer_id'])
    c1.woo_customer_id = 9289
    c1.save(update_fields=['woo_customer_id'])
    print('DONE: Moved woo_customer_id=9289 to avluga@gmail.com')
else:
    print('WARNING: State does not match expected. Check manually.')
    print(f'  c1 (avluga) woo_customer_id = {c1.woo_customer_id}')
    print(f'  c2 (srx)    woo_customer_id = {c2.woo_customer_id}')
"
```

---

## Verify

After running the above, open the POS, search for Igor Smirnov (avluga@gmail.com), and confirm the Membership Info tab loads his Doctors Studio Membership.

---

## What was already fixed on the shared WooCommerce store (from staging)

These changes are already live since staging and production share the same WooCommerce:

1. **Subscription #291820**: `customer_id` updated from 0 → 9289
2. **Parent order #291819**: `customer_id` updated from 0 → 9289
3. **Membership #291830**: Created via API — Doctors Studio Membership, active, linked to order #291819, product #278574, subscription #291820
