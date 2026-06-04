# Service Types Sync Guide

This guide explains how to sync service types from Google Sheets into your PostgreSQL `woo_service_types` table.

## 📋 Table of Contents
- [Overview](#overview)
- [Quick Start](#quick-start)
- [Data Format](#data-format)
- [Usage Examples](#usage-examples)
- [Parsing Rules](#parsing-rules)
- [Troubleshooting](#troubleshooting)

## Overview

The sync script processes service names from Google Sheets and automatically:
- Extracts the base service name
- Parses series information (e.g., "1 series", "3 series", "Single")
- Creates or updates records in the `woo_service_types` table

## Quick Start

### Option 1: Export Google Sheets to CSV (Recommended)

1. Open your Google Sheet
2. Click **File** → **Download** → **Comma Separated Values (.csv)**
3. Save the file (e.g., `service_types.csv`)
4. Run the sync command:

```bash
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
python3 manage.py sync_service_types --csv /path/to/service_types.csv
```

### Option 2: Test First with Dry Run

Preview what will be imported without saving:

```bash
python3 manage.py sync_service_types --csv /path/to/service_types.csv --dry-run
```

### Option 3: Verbose Output

See detailed processing information:

```bash
python3 manage.py sync_service_types --csv /path/to/service_types.csv --verbose
```

## Data Format

### Expected CSV Structure

Your CSV should have these columns:

| Column Name | Description | Example |
|-------------|-------------|---------|
| `post_title` | Full service name with series | LAB: Baseline/Annual - 1 series |
| `_sku` | SKU (optional) | LAB1082-1 |

### Example CSV Content

```csv
post_title,_sku
LAB: Baseline/Annual,LAB1082
LAB: Baseline/Annual - 1 series,LAB1082-1
LAB: Baseline/Annual - Single,LAB1082-S
LAB: Blood Draw,LAB1000
LAB: Blood Draw - 1 series,LAB1000-S
LAB: Cancer Panel,LAB1077
LAB: Cancer Panel - 3 series,LAB1077-1
```

## Usage Examples

### Basic Import

```bash
python3 manage.py sync_service_types --csv ~/Downloads/service_types.csv
```

### Dry Run (Preview Only)

```bash
python3 manage.py sync_service_types --csv ~/Downloads/service_types.csv --dry-run
```

Output:
```
Processing 33 service type entries...

  [1] "LAB: Baseline/Annual" -> base: "LAB: Baseline/Annual", series: None
  [2] "LAB: Baseline/Annual - 1 series" -> base: "LAB: Baseline/Annual", series: [1]
  [3] "LAB: Baseline/Annual - Single" -> base: "LAB: Baseline/Annual", series: [1]
  ...

⚠️  DRY RUN - No changes were saved to database

============================================================
SYNC COMPLETED
============================================================
✅ Created:  10
🔄 Updated:  20
⏭️  Skipped:  3
❌ Errors:   0
============================================================
```

### Verbose Import

```bash
python3 manage.py sync_service_types --csv ~/Downloads/service_types.csv --verbose
```

Output:
```
✓ Read 33 rows from CSV: /home/user/Downloads/service_types.csv

Processing 33 service type entries...

  [1] "LAB: Baseline/Annual" -> base: "LAB: Baseline/Annual", series: None
    ✓ Created: LAB: Baseline/Annual (series: None)
  [2] "LAB: Baseline/Annual - 1 series" -> base: "LAB: Baseline/Annual", series: [1]
    ✓ Updated: LAB: Baseline/Annual (series: [1])
  ...
```

## Parsing Rules

The script intelligently parses service names based on these rules:

### Rule 1: No Series Suffix
**Input:** `LAB: Baseline/Annual`  
**Result:** 
- `name`: "LAB: Baseline/Annual"
- `series`: `null`

### Rule 2: "Single" Suffix
**Input:** `LAB: Baseline/Annual - Single`  
**Result:** 
- `name`: "LAB: Baseline/Annual"
- `series`: `[1]`

### Rule 3: "X series" Suffix
**Input:** `LAB: Cancer Panel - 3 series`  
**Result:** 
- `name`: "LAB: Cancer Panel"
- `series`: `[3]`

**Input:** `LAB: Female Baseline/Annual - 1 series`  
**Result:** 
- `name`: "LAB: Female Baseline/Annual"
- `series`: `[1]`

### Deduplication Logic

If multiple rows have the same **base name**, the script will:
1. Create the record on first occurrence
2. **Update** the record on subsequent occurrences
3. Keep the **last** series value encountered

Example:
```
Row 1: "LAB: Baseline/Annual" -> Creates with series: null
Row 2: "LAB: Baseline/Annual - 1 series" -> Updates to series: [1]
Row 3: "LAB: Baseline/Annual - Single" -> Updates to series: [1]
```

Final result: `LAB: Baseline/Annual` with `series: [1]`

## Database Schema

After import, your `woo_service_types` table will look like:

```sql
SELECT * FROM woo_service_types;
```

| id | name | default_index | series | created_at | updated_at |
|----|------|---------------|--------|------------|------------|
| 1 | LAB: Baseline/Annual | null | null | 2025-12-24 | 2025-12-24 |
| 2 | LAB: Baseline/Annual | null | [1] | 2025-12-24 | 2025-12-24 |
| 3 | LAB: Blood Draw | null | null | 2025-12-24 | 2025-12-24 |
| 4 | LAB: Cancer Panel | null | [3] | 2025-12-24 | 2025-12-24 |

## Troubleshooting

### Issue: "No data found to import"

**Cause:** CSV file is empty or has no data rows  
**Solution:** Verify your CSV has a header row and at least one data row

### Issue: "CSV file not found"

**Cause:** Wrong file path  
**Solution:** Use absolute path or verify file location
```bash
# Find your file
ls -la ~/Downloads/*.csv

# Use absolute path
python3 manage.py sync_service_types --csv /home/username/Downloads/service_types.csv
```

### Issue: "Skipped - empty name"

**Cause:** Row has empty `post_title` column  
**Solution:** Check your CSV for empty rows or missing data

### Issue: Series not parsing correctly

**Cause:** Non-standard naming format  
**Solution:** Check your names follow one of these patterns:
- `Service Name` (no series)
- `Service Name - Single` (series: [1])
- `Service Name - X series` (series: [X])

### Issue: Duplicate records

**Cause:** Multiple rows with same base name  
**Solution:** This is expected behavior - the script will **update** existing records. The last occurrence wins.

## Advanced: Google Sheets API (Optional)

If you want to sync directly from Google Sheets without CSV export:

### 1. Install Required Packages

```bash
pip install google-auth google-api-python-client
```

### 2. Set Up Google Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable Google Sheets API
4. Create Service Account credentials
5. Download JSON key file
6. Share your Google Sheet with the service account email

### 3. Update Script Configuration

Edit the script at line 116:
```python
SERVICE_ACCOUNT_FILE = '/path/to/your/credentials.json'
```

### 4. Run with Sheet ID

```bash
python3 manage.py sync_service_types --sheet YOUR_SHEET_ID
```

Sheet ID is found in the URL:
```
https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit
```

## Command Reference

```bash
python3 manage.py sync_service_types [OPTIONS]

Options:
  --csv PATH          Path to CSV file with service types data
  --sheet SHEET_ID    Google Sheets ID to import from (requires credentials)
  --dry-run           Show what would be imported without saving
  --verbose           Show detailed processing information
  --help              Show this help message
```

## Example Workflow

```bash
# Step 1: Export from Google Sheets to CSV
# (Do this manually in Google Sheets: File → Download → CSV)

# Step 2: Preview what will happen
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
python3 manage.py sync_service_types --csv ~/Downloads/Series.csv --dry-run

# Step 3: If everything looks good, run the actual import
python3 manage.py sync_service_types --csv ~/Downloads/Series.csv --verbose

# Step 4: Verify in database
python3 manage.py dbshell
SELECT * FROM woo_service_types ORDER BY name;
```

## Support

For issues or questions, check:
- Script location: `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/management/commands/sync_service_types.py`
- Model definition: `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/models.py` (WooServiceTypes class)
- This guide: `/var/www/newpos-posds/woo-ghl-contact-db/backend/SYNC_SERVICE_TYPES_GUIDE.md`
