# ATUM Inventory Location Sync Commands

This document provides a reference for the commands and interfaces used to fix and maintain ATUM inventory location synchronization.

## Problem Summary

The ATUM inventory locations were not accurately synchronized between the ATUM system and our local database. Products had incorrect or excess inventory locations that did not match their actual ATUM inventory locations.

## Solution Commands

### 1. Restore Correct ATUM Locations Based on API Data

This command queries the ATUM API for each product's actual inventory locations and updates our local database to match exactly:

```bash
# Run in dry-run mode first to see what would change
python3 manage.py restore_atum_locations --batch-size 50 --dry-run

# Apply the changes to all products
python3 manage.py restore_atum_locations --batch-size 50

# For a specific product (using UUID)
python3 manage.py restore_atum_locations --product-id <product-uuid>
```

Command options:
- `--dry-run`: Show what would be changed without making changes
- `--product-id`: Process only a specific product UUID
- `--batch-size`: Number of products to process in each batch (default: 100)
- `--sleep`: Sleep time between API calls in seconds (default: 0.5)

### 2. Clean Specific ATUM Locations

This command cleans up excess ATUM inventory locations for products:

```bash
# Clean specific product with min/max locations
python3 manage.py clean_specific_atum_locations --product-id <product-uuid> --min-locations 2 --max-locations 2

# Clean all products with min/max locations
python3 manage.py clean_specific_atum_locations --min-locations 2 --max-locations 2
```

Command options:
- `--dry-run`: Show what would be changed without making changes
- `--product-id`: Process only a specific product UUID
- `--min-locations`: Minimum number of ATUM locations to keep
- `--max-locations`: Maximum number of ATUM locations to keep
- `--batch-size`: Number of products to process in each batch

### 3. Verify ATUM Locations

This command verifies the current state of ATUM inventory locations across all products:

```bash
# Verify ATUM location counts
python3 manage.py verify_atum_locations
```

## Manual Sync Button in POS Settings

A manual sync button has been added to the POS system's Settings > Integrations tab to trigger the ATUM inventory location synchronization process:

- **Button Name**: ATUM Inventory Locations
- **Description**: Synchronizes product inventory locations with ATUM inventory system
- **Details**: Updates product inventory locations to match exactly what's in ATUM, adding missing locations and removing extraneous ones
- **API Endpoint**: `/api/sync/atum-locations/`
- **Default Batch Size**: 100 products per batch

### API Endpoint Details

```
POST /api/sync/atum-locations/
Content-Type: application/json
Authorization: Bearer <token>

{
  "batch_size": 100  // Optional, defaults to 100
}
```

## Recommended Workflow for Future ATUM Location Maintenance

1. Use the manual sync button in the POS Settings > Integrations tab to trigger the ATUM location sync.

2. Alternatively, run the verification command to check the current state:
   ```bash
   python3 manage.py verify_atum_locations
   ```

3. If discrepancies are found, restore the correct ATUM locations from the API:
   ```bash
   python3 manage.py restore_atum_locations --batch-size 50
   ```

4. Verify the results again:
   ```bash
   python3 manage.py verify_atum_locations
   ```

## Important Notes

- The `restore_atum_locations` command is the most accurate way to fix ATUM inventory locations as it uses the actual ATUM API data for each product.
- The manual sync button in the POS Settings uses this same command under the hood.
- The `clean_specific_atum_locations` command should only be used if you need to enforce a specific number of locations for all products, which is generally not recommended.
- Always run commands with `--dry-run` first to see what would change before applying changes.
- The batch size and sleep parameters can be adjusted to avoid API rate limiting.
- For large product catalogs, the sync process may take several minutes to complete. The API is designed to return quickly while continuing processing in the background.
