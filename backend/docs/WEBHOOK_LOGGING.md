# WooCommerce Webhook Logging System

## Overview

The WooCommerce webhook logging system provides comprehensive tracking of webhook requests and their processing status. It logs all webhook events, including success and failure states, and captures detailed information about the request, processing, and response.

## WebhookLog Model

The `WebhookLog` model stores the following information:

- **Basic Information**
  - `id`: UUID primary key
  - `webhook_type`: Type of webhook (e.g., 'product', 'customer', 'test')
  - `status`: Current status ('received', 'processing', 'success', 'failed', 'skipped')
  - `action_taken`: Action taken ('create', 'update', 'delete', etc.)

- **Request Details**
  - `source_ip`: IP address of the webhook sender
  - `user_agent`: User agent of the webhook sender
  - `content_type`: Content type of the request
  - `request_body`: Raw request body
  - `received_at`: Timestamp when the webhook was received

- **Processing Details**
  - `woo_product_id`: WooCommerce product ID (for product webhooks)
  - `local_product_id`: Local product ID (for product webhooks)
  - `updated_fields`: List of fields that were updated
  - `processed_at`: Timestamp when processing was completed
  - `processing_time_ms`: Processing time in milliseconds

- **Bundle-Specific Information**
  - `is_bundle_product`: Whether the product is a bundle
  - `bundle_sync_attempted`: Whether bundle sync was attempted
  - `bundle_sync_success`: Whether bundle sync was successful
  - `bundle_items_count`: Number of items in the bundle
  - `default_series_updated`: Whether default series selections were updated

- **Response Information**
  - `response_status_code`: HTTP status code of the response
  - `response_message`: Response message
  - `error_message`: Error message (if any)

## Enhanced Webhook Endpoint

The enhanced webhook endpoint is available at:

```
/api/webhooks/woocommerce/product/enhanced/
```

This endpoint provides the same functionality as the original webhook endpoint but with comprehensive logging.

## How to Use

### Configuring WooCommerce

1. In WooCommerce admin, go to Settings > Advanced > Webhooks
2. Add a new webhook for product updates
3. Set the delivery URL to your enhanced webhook endpoint
4. Set the topic to "Product updated"
5. Set the status to "Active"

### Viewing Webhook Logs

You can view webhook logs in the Django admin interface or query them directly:

```python
# Get all webhook logs
logs = WebhookLog.objects.all().order_by('-received_at')

# Get logs for a specific product
logs = WebhookLog.objects.filter(woo_product_id=123).order_by('-received_at')

# Get failed webhook logs
failed_logs = WebhookLog.objects.filter(status='failed').order_by('-received_at')
```

### Troubleshooting

If a webhook is failing, check the following:

1. Look at the `error_message` field in the webhook log
2. Check if the `request_body` contains valid data
3. For bundle products, check if `bundle_sync_attempted` is True and `bundle_sync_success` is False
4. Verify that WooCommerce API credentials are configured correctly

## Testing

A test script is available to test the enhanced webhook:

```bash
python test_webhook_direct.py
```

This script simulates a webhook request for a bundle product and checks the webhook logs.

## Implementation Details

The enhanced webhook implementation:

1. Creates a WebhookLog entry when a webhook request is received
2. Updates the log with processing status and details
3. Handles product updates, deletions, and bundle synchronization
4. Records success or failure status and response details
5. Provides detailed logging of product field updates and bundle sync attempts

For bundle products, the webhook attempts to sync bundle data from WooCommerce, including bundled items and default variation attributes.

## Notes

- The bundle sync functionality requires valid WooCommerce API credentials to be configured
- The webhook logs are automatically pruned after 30 days to prevent database bloat
- The enhanced webhook endpoint is backward compatible with the original endpoint
