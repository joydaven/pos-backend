# Order Synchronization Fix Documentation

## Problem Statement

When orders were cancelled in WooCommerce (via admin interface or API), the corresponding POS orders remained unchanged instead of being updated to "cancelled" status. This caused data inconsistency between the two systems.

## Root Cause Analysis

1. **Missing WooCommerce Order Webhook**: No webhook handler existed to catch WooCommerce order status changes
2. **One-way Sync Only**: POS orders could sync to WooCommerce, but not vice versa
3. **Manual Cancellations**: Direct WooCommerce order cancellations didn't propagate to POS system

## Solution Implemented

### 1. WooCommerce Order Status Webhook Handler

**File**: `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/webhooks_order.py`

**Key Features**:
- Handles all WooCommerce order status changes (pending, processing, completed, cancelled, refunded)
- Comprehensive logging with `WebhookLog` model
- Multiple strategies to find corresponding POS orders
- Robust error handling and fallback mechanisms

**Webhook Endpoint**: `/api/webhooks/woocommerce/order/`

**Status Mapping**:
```python
{
    'pending': 'pending',
    'processing': 'processing', 
    'completed': 'completed',
    'cancelled': 'cancelled',
    'refunded': 'refunded',
    'on-hold': 'processing',
    'failed': 'cancelled'
}
```

### 2. Enhanced WooCommerce Order Update Endpoint

**File**: `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/views.py`

**Enhancement**: Modified `update_woocommerce_order_status()` function to include reverse synchronization:
- Updates WooCommerce order status
- Finds corresponding POS order
- Updates POS order status accordingly
- Returns detailed sync results

### 3. POS Order Finding Strategies

The system uses multiple strategies to find corresponding POS orders:

1. **POS Transaction ID in Meta Data**: Looks for `_pos_transaction_id` in WooCommerce order meta_data
2. **Direct Transaction ID**: Checks the transaction_id field directly
3. **WooCommerce Order ID in POS Metadata**: Searches POS orders for `woo_order_id` in metadata
4. **Order Number Matching**: Fallback to order number matching

### 4. Comprehensive Logging

**WebhookLog Model Fields**:
- `webhook_type`: 'order'
- `status`: 'received', 'processing', 'success', 'failed', 'skipped'
- `order_id`: WooCommerce order ID
- `action_taken`: Description of action performed
- `updated_fields`: Details of what was updated
- `processing_time_ms`: Performance tracking
- `error_message`: Error details if failed

## API Endpoints

### New Endpoints
- `POST /api/webhooks/woocommerce/order/` - Main order status webhook
- `POST /api/webhooks/woocommerce/order/test/` - Test webhook functionality

### Enhanced Endpoints
- `POST /api/woocommerce-orders/{order_id}/update_status/` - Now includes POS sync

### Existing Endpoints (Still Working)
- `POST /api/pos-orders/{uuid}/update_status/` - POS order status updates
- All order status service functions (`completeOrder`, `voidOrder`, `refundOrder`)

## Usage Scenarios

### 1. WooCommerce Admin Cancellation
1. Admin cancels order in WooCommerce dashboard
2. WooCommerce fires webhook to `/api/webhooks/woocommerce/order/`
3. Webhook handler finds corresponding POS order
4. POS order status updated to 'cancelled'
5. WebhookLog entry created for audit trail

### 2. API-Based Order Management
```bash
# Cancel WooCommerce order (will sync to POS automatically)
curl -X POST http://your-domain/api/woocommerce-orders/12345/update_status/ \
  -H "Content-Type: application/json" \
  -d '{"status": "cancelled"}'
```

### 3. POS Order Management (Existing)
```bash
# Cancel POS order (will sync to WooCommerce if linked)
curl -X POST http://your-domain/api/pos-orders/abc-123-def/update_status/ \
  -H "Content-Type: application/json" \
  -d '{"status": "cancelled", "sync_woocommerce": true}'
```

## Testing and Verification

### Management Command
```bash
# Find all linked orders
python3 manage.py test_order_sync --find-linked-orders

# Test specific WooCommerce order sync
python3 manage.py test_order_sync --woo-order-id 12345

# Test specific POS order sync  
python3 manage.py test_order_sync --pos-order-id abc-123-def

# Test webhook functionality
python3 manage.py test_order_sync --test-webhook
```

### Frontend Testing
1. Open Orders View component
2. Use existing "Void Order" button to cancel orders
3. Verify both POS and WooCommerce orders are updated
4. Check WebhookLog entries for audit trail

## WooCommerce Webhook Configuration

To enable automatic synchronization, configure WooCommerce webhooks:

1. **WooCommerce Admin** → Settings → Advanced → Webhooks
2. **Create New Webhook**:
   - Name: "Order Status Updates"
   - Status: Active
   - Topic: Order updated
   - Delivery URL: `https://your-domain.com/api/webhooks/woocommerce/order/`
   - Secret: (optional, for security)
   - API Version: WC/v3

## Monitoring and Troubleshooting

### Webhook Logs
Query webhook logs to monitor order synchronization:
```python
from crm.models import WebhookLog

# Recent order webhooks
recent_order_webhooks = WebhookLog.objects.filter(
    webhook_type='order'
).order_by('-created_at')[:10]

# Failed webhooks
failed_webhooks = WebhookLog.objects.filter(
    webhook_type='order',
    status='failed'
)
```

### Database Queries
```sql
-- Find POS orders with WooCommerce links
SELECT id, order_number, status, metadata->>'woo_order_id' as woo_order_id 
FROM crm_posorder 
WHERE metadata->>'woo_order_id' IS NOT NULL;

-- Recent webhook logs
SELECT * FROM crm_webhooklog 
WHERE webhook_type = 'order' 
ORDER BY created_at DESC LIMIT 10;
```

## Error Handling

The system handles various error scenarios:

1. **Order Not Found**: Graceful handling when orders don't exist
2. **Invalid Status**: Validation against allowed status values
3. **Network Issues**: Retry mechanisms and proper error logging
4. **Missing Links**: Continues operation even if no corresponding order found
5. **Malformed Data**: JSON parsing errors handled gracefully

## Status Flow Examples

### Successful Cancellation Flow
```
WooCommerce Order 12345: processing → cancelled
         ↓ (webhook)
POS Order abc-123: processing → cancelled
         ↓ (response)
WebhookLog: status=success, action="Status change to cancelled"
```

### No Corresponding Order
```
WooCommerce Order 99999: processing → cancelled
         ↓ (webhook)
No POS Order Found
         ↓ (response)
WebhookLog: status=skipped, action="No corresponding POS order found"
```

## Files Modified/Created

### New Files
- `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/webhooks_order.py`
- `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/management/commands/test_order_sync.py`

### Modified Files
- `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/urls.py` (added webhook routes)
- `/var/www/newpos-posds/woo-ghl-contact-db/backend/crm/views.py` (enhanced WooCommerce order update)

### Frontend (No Changes Required)
- Existing order management components continue to work
- OrdersView.tsx: "Void Order" button works for both systems
- CustomerDetailsModal.tsx: Order status displays remain accurate
- orderStatusService.ts: All existing functions unchanged

## Next Steps

1. **Configure WooCommerce Webhook**: Set up the webhook in WooCommerce admin
2. **Monitor Webhook Logs**: Use WebhookLog model to track synchronization
3. **Test Edge Cases**: Verify behavior with various order status combinations
4. **Performance Monitoring**: Track webhook processing times
5. **Staff Training**: Educate staff on new bidirectional synchronization

## Security Considerations

1. **Webhook Authentication**: Consider implementing webhook signature validation
2. **Rate Limiting**: Monitor webhook frequency to prevent abuse
3. **Error Logging**: Ensure sensitive data isn't logged in error messages
4. **Access Control**: Webhook endpoints are public but validate data thoroughly

This comprehensive fix ensures that order status changes in either system (POS or WooCommerce) are properly synchronized, maintaining data consistency and providing a complete audit trail through the WebhookLog system.
