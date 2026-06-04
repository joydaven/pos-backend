# GHL Contact ID Sync Management Command

## Overview

The `sync_ghl_contact_ids` management command synchronizes GoHighLevel (GHL) contact IDs for all contacts in the database by searching GHL via email address. This is crucial for GHL integrations, credit service point syncing, and maintaining data consistency between systems.

## Features

- **Smart Contact Detection**: Searches GHL by email and updates local database with GHL contact IDs
- **Priority Processing**: Processes contacts with credit points first
- **Flexible Filtering**: Multiple options for targeting specific contacts
- **Rate Limiting**: Configurable delays to respect API limits
- **Comprehensive Logging**: Detailed progress tracking and error reporting
- **Dry Run Mode**: Test changes without modifying the database
- **Batch Processing**: Efficient handling of large contact lists

## Installation

The command is automatically available after Django setup. No additional installation required.

## Usage

### Basic Commands

```bash
# Activate virtual environment
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
source venv/bin/activate

# Sync all contacts missing GHL IDs (recommended)
python manage.py sync_ghl_contact_ids --missing-only

# Dry run to preview changes
python manage.py sync_ghl_contact_ids --missing-only --dry-run

# Process specific email
python manage.py sync_ghl_contact_ids --email "customer@example.com"

# Process priority contacts (those with credit points)
python manage.py sync_ghl_contact_ids --priority-only

# Batch processing with limit
python manage.py sync_ghl_contact_ids --missing-only --limit 100
```

### Advanced Options

```bash
# Force update existing GHL IDs (re-sync)
python manage.py sync_ghl_contact_ids --force-update --limit 50

# Verbose output with detailed logging
python manage.py sync_ghl_contact_ids --missing-only --verbose

# Custom API delay (default: 0.5 seconds)
python manage.py sync_ghl_contact_ids --missing-only --delay 0.3

# Process all contacts (including those with existing GHL IDs)
python manage.py sync_ghl_contact_ids --force-update
```

## Command Options

| Option | Description | Example |
|--------|-------------|---------|
| `--dry-run` | Preview changes without saving | `--dry-run` |
| `--limit N` | Process maximum N contacts | `--limit 100` |
| `--email EMAIL` | Process specific email only | `--email "test@example.com"` |
| `--missing-only` | Only contacts without GHL IDs | `--missing-only` |
| `--priority-only` | Only contacts with credit points | `--priority-only` |
| `--force-update` | Update existing GHL IDs | `--force-update` |
| `--verbose` | Enable detailed logging | `--verbose` |
| `--delay SECONDS` | API request delay (default: 0.5) | `--delay 0.3` |

## Database Impact

### Fields Updated

- `ghl_contact_id`: GHL contact identifier
- `ghl_last_sync`: Timestamp of last sync
- `ghl_data`: Complete GHL contact data (JSON)

### Query Logic

The command builds queries based on options:

1. **Default**: Contacts missing GHL IDs (`ghl_contact_id IS NULL`)
2. **Priority**: Contacts with credit points, ordered by priority
3. **Force Update**: All contacts (including existing GHL IDs)
4. **Email Filter**: Single contact by email address

## API Integration

### GHL API Usage

- **Endpoint**: `https://services.leadconnectorhq.com/contacts/search`
- **Authentication**: Bearer token authentication
- **Rate Limiting**: Configurable delays between requests
- **Search Method**: Email-based exact matching

### Error Handling

- Network timeouts and connection errors
- API rate limiting responses
- Invalid email format handling
- Missing or malformed GHL responses

## Performance Considerations

### Recommended Batch Sizes

- **Small batches**: 50-100 contacts (testing/development)
- **Medium batches**: 200-500 contacts (production)
- **Large batches**: 1000+ contacts (bulk operations)

### API Rate Limiting

- Default delay: 0.5 seconds between requests
- Recommended for production: 0.3-0.5 seconds
- High-volume operations: 0.2-0.3 seconds

## Monitoring and Logging

### Output Format

```
🔍 Starting GHL Contact ID Sync
📊 Found 150 contacts to process
✅ Found GHL ID for customer@example.com: ABC123XYZ
❌ No GHL contact found for missing@example.com
============================================================
📊 SYNC SUMMARY
============================================================
📈 Total Processed: 150/150
✅ GHL IDs Found: 145
💾 Records Updated: 145
⏭️  Skipped: 0
❌ Errors: 5
📊 Success Rate: 96.7%
```

### Log Levels

- **INFO**: Basic progress and results
- **DEBUG**: Detailed API calls and responses (with `--verbose`)
- **ERROR**: Failed operations and exceptions

## Examples

### Production Sync Workflow

```bash
# 1. Check current status
python manage.py shell -c "
from crm.models import Contact
total = Contact.objects.count()
with_ghl = Contact.objects.filter(ghl_contact_id__isnull=False).count()
print(f'Total: {total}, With GHL: {with_ghl}, Missing: {total-with_ghl}')
"

# 2. Dry run to preview changes
python manage.py sync_ghl_contact_ids --missing-only --limit 50 --dry-run

# 3. Process priority contacts first
python manage.py sync_ghl_contact_ids --priority-only --verbose

# 4. Process remaining contacts in batches
python manage.py sync_ghl_contact_ids --missing-only --limit 200 --delay 0.3

# 5. Verify results
python manage.py shell -c "
from crm.models import Contact
recent = Contact.objects.filter(ghl_last_sync__isnull=False).order_by('-ghl_last_sync')[:5]
for c in recent: print(f'{c.email} → {c.ghl_contact_id}')
"
```

### Troubleshooting Commands

```bash
# Check specific contact
python manage.py sync_ghl_contact_ids --email "problem@example.com" --verbose

# Re-sync existing contacts
python manage.py sync_ghl_contact_ids --force-update --limit 10 --verbose

# Test API connectivity
python manage.py shell -c "
from crm.ghl_api import search_ghl_contact_by_email
result = search_ghl_contact_by_email('test@doctorsstudio.com')
print('API Test:', 'SUCCESS' if result else 'FAILED')
"
```

## Integration with Other Systems

### Credit Service Points

After syncing GHL contact IDs, you can sync credit service points:

```bash
# Using the existing populate script
python populate_ghl_contact_ids.py --sync-credits --limit 50
```

### WooCommerce Integration

GHL contact IDs enable enhanced customer data synchronization with WooCommerce customer records.

## Troubleshooting

### Common Issues

1. **No GHL contacts found**: Many WooCommerce customers may not exist in GHL
2. **API rate limiting**: Increase `--delay` value if getting rate limit errors
3. **Network timeouts**: Check internet connectivity and GHL API status
4. **Permission errors**: Ensure proper database write permissions

### Error Messages

- `"No GHL contact found for email"`: Contact doesn't exist in GHL (normal)
- `"Error searching GHL for email"`: API or network issue
- `"Cannot reorder a query once a slice has been taken"`: Internal query error (fixed)

## Security Considerations

- GHL API token stored in `crm/ghl_api.py` (should be moved to environment variables)
- Database credentials managed through Django settings
- No sensitive data logged in standard output

## Performance Metrics

Based on testing with 8,690 total contacts:

- **Initial Status**: 8,165 contacts with GHL IDs (94.0%)
- **Sync Results**: Found 3 additional GHL contact IDs
- **Final Status**: 8,168 contacts with GHL IDs (94.0%)
- **Success Rate**: ~2% of missing contacts found in GHL
- **Processing Speed**: ~2-3 contacts per second with 0.3s delay

## Future Enhancements

- Environment variable configuration for API tokens
- Webhook integration for real-time sync
- Bulk export/import functionality
- Enhanced error recovery mechanisms
- Integration with Django admin interface

## Support

For issues or questions:

1. Check Django logs for detailed error messages
2. Use `--verbose` flag for debugging information
3. Test with single email using `--email` option
4. Verify GHL API connectivity and credentials
