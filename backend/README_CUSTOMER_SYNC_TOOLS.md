# Customer Sync Tools

This directory contains two powerful scripts for managing customer synchronization between WooCommerce and your local database.

## 🔍 1. Customer Sync Analysis Script

**File:** `identify_unsynced_customers.py`

**Purpose:** Analyzes and identifies sync issues between WooCommerce and local database.

### Usage:
```bash
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
source venv/bin/activate
python3 identify_unsynced_customers.py
```

### What it finds:
- **Unsynced customers** - Missing from local DB
- **Orphaned customers** - In local DB but deleted from WooCommerce
- **Data mismatches** - Field differences between systems
- **Local-only customers** - Without WooCommerce IDs

### Sample Output:
```
📊 Sync Status: 8690/8690 (100.0%)
🚨 Orphaned in local DB: 2 customers
📝 Data mismatches: 14 customers

🗑️ DELETED CUSTOMER:
   Local ID: ed620044-f639-4845-b04d-d1d7d86888f4
   WooCommerce ID: 2034 (NO LONGER EXISTS)
   Name: Ashley Maroto
   Email: ashley@doctorsstudio.com
```

---

## 🔧 2. Customer Sync Fix Script

**File:** `fix_customer_sync_issues.py`

**Purpose:** Automatically fixes identified sync issues.

### Basic Usage:
```bash
# DRY RUN (see what would happen)
python3 fix_customer_sync_issues.py --dry-run --fix-orphaned delete --fix-mismatches woo_to_local

# LIVE EXECUTION (make actual changes)
python3 fix_customer_sync_issues.py --fix-orphaned delete --fix-mismatches woo_to_local --confirm
```

### Command Options:

#### Orphaned Customer Handling:
- `--fix-orphaned delete` - Delete orphaned customers from local DB
- `--fix-orphaned restore` - Restore orphaned customers in WooCommerce
- `--fix-orphaned skip` - Don't fix orphaned customers

#### Data Mismatch Handling:
- `--fix-mismatches woo_to_local` - Update local DB with WooCommerce data
- `--fix-mismatches local_to_woo` - Update WooCommerce with local DB data
- `--fix-mismatches skip` - Don't fix data mismatches

#### Safety Options:
- `--dry-run` - Show what would happen without making changes
- `--confirm` - Required for destructive operations (like deletion)

### Example Scenarios:

#### Scenario 1: Clean up deleted customers
```bash
# See what would be deleted
python3 fix_customer_sync_issues.py --dry-run --fix-orphaned delete

# Actually delete them
python3 fix_customer_sync_issues.py --fix-orphaned delete --confirm
```

#### Scenario 2: Fix data inconsistencies
```bash
# Update local DB to match WooCommerce
python3 fix_customer_sync_issues.py --fix-mismatches woo_to_local

# Update WooCommerce to match local DB
python3 fix_customer_sync_issues.py --fix-mismatches local_to_woo
```

#### Scenario 3: Full cleanup (recommended)
```bash
# DRY RUN first
python3 fix_customer_sync_issues.py --dry-run --fix-orphaned delete --fix-mismatches woo_to_local

# Execute if results look good
python3 fix_customer_sync_issues.py --fix-orphaned delete --fix-mismatches woo_to_local --confirm
```

---

## 🔄 Enhanced Customer Webhook

The customer webhook has been enhanced to handle comprehensive updates:

### Features:
- **Real-time sync** - Automatically syncs customer updates from WooCommerce
- **Change detection** - Only updates fields that actually changed
- **Comprehensive logging** - Detailed logs of all changes
- **Error handling** - Robust error handling and recovery

### Webhook URL:
```
https://yourdomain.com/api/webhooks/woocommerce/customer/
```

### What gets synced:
- Basic info (name, email)
- Billing address (phone, address, city, state, zip, country)
- Shipping address (address, city, state, zip)
- Customer creation/update/deletion events

### Webhook Response Example:
```json
{
    "success": true,
    "message": "Customer 1234 updated successfully",
    "customer_id": "uuid-here",
    "updated_fields": ["first_name", "billing_city"],
    "changes_made": [
        "first_name: 'Chris' → 'Christopher'",
        "billing_city: 'Miami' → 'Miami Beach'"
    ],
    "action": "customer_updated"
}
```

---

## 📋 Recommended Workflow

### 1. Regular Monitoring
Run the analysis script weekly:
```bash
python3 identify_unsynced_customers.py > customer_sync_report_$(date +%Y%m%d).txt
```

### 2. Fix Issues as Needed
Based on analysis results:
```bash
# For orphaned customers (usually safe to delete)
python3 fix_customer_sync_issues.py --fix-orphaned delete --confirm

# For data mismatches (sync from WooCommerce)
python3 fix_customer_sync_issues.py --fix-mismatches woo_to_local
```

### 3. Webhook Maintenance
- Ensure webhook is active in WooCommerce
- Monitor webhook logs for errors
- Test webhook after WooCommerce updates

---

## ⚠️ Safety Notes

1. **Always run with --dry-run first** to see what would happen
2. **Backup your database** before running fix operations
3. **Use --confirm for deletions** to prevent accidental data loss
4. **Test in staging** before running in production
5. **Monitor webhook logs** for sync issues

---

## 🐛 Troubleshooting

### Common Issues:

#### Script fails with Django import error:
```bash
# Make sure you're in the right directory and virtual environment
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
source venv/bin/activate
```

#### WooCommerce API timeout:
- The script handles large customer lists automatically
- If it fails, check your WooCommerce API limits

#### Webhook not working:
- Check WooCommerce webhook settings
- Verify webhook URL is accessible
- Check Django logs for webhook errors

### Log Locations:
- Django logs: Check your Django logging configuration
- Webhook logs: Look for "woocommerce_customer_webhook" in logs
- Script output: Captured in terminal or redirect to file

---

## 📊 Current Status

Based on your latest analysis:
- ✅ **8,690/8,690 customers synced (100%)**
- 🚨 **2 orphaned customers** (Ashley Maroto, Jean Gallano)
- 📝 **14 data mismatches** (mostly name formatting)
- 🎯 **Overall status: Excellent**

Your sync is in great shape with only minor cleanup needed!
