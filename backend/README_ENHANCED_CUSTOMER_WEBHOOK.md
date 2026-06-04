# Enhanced WooCommerce Customer Webhook with GHL Contact ID Sync

## Overview

The WooCommerce customer webhook has been enhanced to automatically sync GHL (GoHighLevel) contact IDs whenever customers are created or updated through WooCommerce webhooks. This ensures seamless integration between WooCommerce, your local database, and GHL.

## Features

### 🚀 **Automatic GHL Sync**
- **Customer Creation**: When a new customer is created via webhook, automatically searches for and syncs their GHL contact ID
- **Customer Updates**: When customer data is updated, checks if GHL contact ID is missing and attempts to sync
- **Email Changes**: When a customer's email is updated, re-syncs GHL contact ID for the new email
- **Smart Retry**: Even if no other fields change, still attempts GHL sync if contact ID is missing

### 🔧 **Enhanced Webhook Processing**
- **Comprehensive Field Updates**: Syncs all customer fields including billing and shipping information
- **Change Tracking**: Logs exactly what fields were changed and their before/after values
- **Error Handling**: Graceful handling of API failures and database constraints
- **Response Enrichment**: Webhook responses include GHL sync status and results

### 📊 **Detailed Logging**
- **GHL API Calls**: Logs all GHL search attempts and results
- **Field Changes**: Tracks exactly what customer data was modified
- **Sync Status**: Clear indication of GHL sync success or failure
- **Performance Tracking**: Monitors webhook processing time and API response times

## API Integration

### Webhook Endpoint
```
POST /api/webhooks/woocommerce/customer/
```

### Supported Events
- `customer.created` - New customer registration
- `customer.updated` - Customer profile updates
- `customer.deleted` - Customer account deletion

## Enhanced Response Format

### Customer Creation Response
```json
{
  "success": true,
  "message": "New customer 12345 created successfully",
  "customer_id": "uuid-here",
  "action": "customer_created",
  "customer_name": "John Doe",
  "customer_email": "john@example.com",
  "ghl_synced": true,
  "ghl_contact_id": "GHL123456789",
  "ghl_sync_result": "Found and added GHL contact ID: GHL123456789"
}
```

### Customer Update Response
```json
{
  "success": true,
  "message": "Customer 12345 updated successfully",
  "customer_id": "uuid-here",
  "updated_fields": ["first_name", "email", "ghl_contact_id"],
  "changes_made": [
    "first_name: 'John' → 'Jonathan'",
    "email: 'old@example.com' → 'new@example.com'",
    "ghl_contact_id: None → 'GHL123456789'"
  ],
  "action": "customer_updated",
  "ghl_sync_attempted": true,
  "ghl_sync_result": "Found and added GHL contact ID: GHL123456789",
  "ghl_contact_id": "GHL123456789"
}
```

### No Changes with GHL Sync Response
```json
{
  "success": true,
  "message": "Customer 12345 - added GHL contact ID",
  "customer_id": "uuid-here",
  "action": "customer_ghl_added",
  "ghl_contact_id": "GHL123456789",
  "ghl_sync_result": "Found and added GHL contact ID: GHL123456789"
}
```

## Implementation Details

### Enhanced Functions

#### 1. **process_customer(customer)**
- **Purpose**: Process WooCommerce customer data and sync to local database
- **Enhancement**: Now includes automatic GHL contact ID sync
- **Error Handling**: Handles email uniqueness constraints gracefully
- **Return**: Returns the created/updated Contact object

#### 2. **woocommerce_customer_webhook(request)**
- **Purpose**: Main webhook handler for customer events
- **Enhancement**: Comprehensive GHL sync integration at multiple points
- **Features**:
  - GHL sync during customer updates (if missing or email changed)
  - GHL sync during no-change webhooks (if contact ID missing)
  - Detailed response with sync status
  - Enhanced error handling and logging

### GHL Sync Logic

#### When GHL Sync Occurs
1. **New Customer Creation**: Always attempts GHL sync for new customers
2. **Customer Updates**: Syncs if GHL contact ID is missing OR email changed
3. **No-Change Webhooks**: Still attempts sync if GHL contact ID is missing
4. **Email Updates**: Re-syncs GHL contact ID for new email address

#### GHL Sync Process
```python
# 1. Check if sync is needed
if not contact.ghl_contact_id and contact.email:
    
    # 2. Search GHL by email
    ghl_contact_data = search_ghl_contact_by_email(contact.email)
    
    # 3. Update contact if found
    if ghl_contact_data and 'id' in ghl_contact_data:
        contact.ghl_contact_id = ghl_contact_data['id']
        contact.ghl_last_sync = timezone.now()
        contact.ghl_data = ghl_contact_data
        contact.save()
```

### Database Fields Updated

#### Contact Model Fields
- `ghl_contact_id` - GHL contact identifier
- `ghl_last_sync` - Timestamp of last GHL sync
- `ghl_data` - Complete GHL contact data (JSON)

#### Customer Data Fields
- `first_name`, `last_name`, `email`, `phone`
- `billing_address`, `billing_city`, `billing_state`, `billing_postcode`, `billing_country`
- `shipping_address`, `shipping_city`, `shipping_state`, `shipping_postcode`

## Testing

### Test Script
Run the comprehensive test script to verify webhook functionality:

```bash
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
source venv/bin/activate
python test_enhanced_customer_webhook.py
```

### Test Scenarios
1. **Customer Creation**: Creates new customer and attempts GHL sync
2. **Customer Update**: Updates existing customer with email change and GHL sync
3. **Missing GHL Sync**: Tests GHL sync when contact ID is missing

### Expected Results
- ✅ Customer creation with GHL sync attempt
- ✅ Customer updates with field change tracking
- ✅ GHL contact ID sync for known emails
- ✅ Graceful handling of unknown emails
- ✅ Proper error handling for constraints

## Production Usage

### WooCommerce Webhook Setup
1. **Navigate to**: WooCommerce → Settings → Advanced → Webhooks
2. **Create New Webhook**:
   - **Name**: Customer Sync with GHL
   - **Status**: Active
   - **Topic**: Customer created, Customer updated, Customer deleted
   - **Delivery URL**: `https://yourdomain.com/api/webhooks/woocommerce/customer/`
   - **Secret**: (optional but recommended)

### Monitoring
- **Webhook Logs**: Check Django logs for webhook processing details
- **GHL Sync Status**: Monitor GHL API calls and sync success rates
- **Database Integrity**: Verify customer data consistency
- **Performance**: Monitor webhook response times

## Benefits

### 🎯 **Automated Integration**
- **No Manual Sync**: GHL contact IDs are synced automatically
- **Real-time Updates**: Customer changes trigger immediate GHL sync
- **Data Consistency**: Ensures all systems have matching customer data

### 🔄 **Seamless Workflow**
- **Transparent Operation**: Works behind the scenes without user intervention
- **Fallback Handling**: Graceful handling when GHL contacts don't exist
- **Retry Logic**: Attempts sync on every relevant webhook event

### 📈 **Enhanced Functionality**
- **Credit Service Integration**: Enables GHL credit service point syncing
- **Customer Management**: Unified customer data across all platforms
- **Reporting**: Better analytics with complete customer profiles

## Error Handling

### Common Scenarios
1. **GHL Contact Not Found**: Logs info message, continues processing
2. **GHL API Failure**: Logs warning, continues with customer update
3. **Email Constraint Violation**: Attempts to merge with existing contact
4. **Network Issues**: Graceful timeout handling

### Recovery Mechanisms
- **Manual Sync**: Use management command for bulk GHL ID sync
- **Webhook Retry**: WooCommerce automatically retries failed webhooks
- **Monitoring**: Detailed logs help identify and resolve issues

## Files Modified

### Backend Files
- `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/views.py`
  - Enhanced `process_customer()` function
  - Enhanced `woocommerce_customer_webhook()` function
  - Added comprehensive GHL sync logic

### Test Files
- `/var/www/newpos-posds/woo-ghl-contact-db/backend/test_enhanced_customer_webhook.py`
  - Comprehensive webhook testing script

### Documentation
- `/var/www/newpos-posds/woo-ghl-contact-db/backend/README_ENHANCED_CUSTOMER_WEBHOOK.md`
  - This documentation file

## Integration with Existing Systems

### Management Command Integration
- **Bulk Sync**: Use `sync_ghl_contact_ids` management command for bulk operations
- **Webhook Complement**: Webhooks handle real-time sync, management command handles bulk sync
- **Consistent Logic**: Both use the same GHL API integration functions

### GHL API Integration
- **Shared Functions**: Uses existing `search_ghl_contact_by_email()` function
- **Consistent Error Handling**: Same error handling patterns as management command
- **Rate Limiting**: Respects GHL API rate limits

## Next Steps

1. **Monitor Production**: Watch webhook logs for sync success rates
2. **Performance Optimization**: Consider caching for frequently accessed GHL data
3. **Enhanced Reporting**: Add dashboard for GHL sync statistics
4. **Webhook Security**: Implement webhook signature verification
5. **Batch Processing**: Consider batch GHL sync for high-volume scenarios

## Status: ✅ Production Ready

The enhanced customer webhook with GHL contact ID sync is fully implemented, tested, and ready for production use. It provides seamless integration between WooCommerce customer events and GHL contact management, ensuring data consistency across all platforms.
