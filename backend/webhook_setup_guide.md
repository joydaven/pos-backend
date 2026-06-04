# WooCommerce Webhook Setup Guide

This guide will help you configure WooCommerce webhooks for real-time data synchronization with your POS system.

## Available Webhook Endpoints

Your POS system now supports the following webhook endpoints:

### 1. Product Updates (Existing)
- **URL**: `https://pos.doctorsstudio.com/api/webhooks/woocommerce/product/`
- **Events**: `product.created`, `product.updated`, `product.deleted`
- **Purpose**: Syncs product data, prices, descriptions, categories

### 2. Customer Updates (Existing)  
- **URL**: `https://pos.doctorsstudio.com/api/webhooks/woocommerce/customer/`
- **Events**: `customer.created`, `customer.updated`, `customer.deleted`
- **Purpose**: Syncs customer information and contact details

### 3. ATUM Inventory Updates (New)
- **URL**: `https://pos.doctorsstudio.com/api/webhooks/atum/inventory/`
- **Events**: Custom ATUM inventory events
- **Purpose**: Syncs multi-location inventory quantities and availability

### 4. Inventory Location Updates (New)
- **URL**: `https://pos.doctorsstudio.com/api/webhooks/atum/location/`
- **Events**: Custom ATUM location events  
- **Purpose**: Syncs inventory location data (addresses, status, etc.)

### 5. General Stock Updates (New)
- **URL**: `https://pos.doctorsstudio.com/api/webhooks/woocommerce/stock/`
- **Events**: `product.updated` (stock-focused)
- **Purpose**: Syncs basic stock quantities for non-ATUM products

## WooCommerce Webhook Configuration

### Step 1: Access WooCommerce Webhooks
1. Go to your WooCommerce admin dashboard
2. Navigate to **WooCommerce → Settings → Advanced → Webhooks**
3. Click **Add webhook**

### Step 2: Configure Each Webhook

#### Product Updates Webhook
- **Name**: `POS Product Sync`
- **Status**: `Active`
- **Topic**: `Product updated` (or `Product created`, `Product deleted`)
- **Delivery URL**: `https://pos.doctorsstudio.com/api/webhooks/woocommerce/product/`
- **Secret**: `your-webhook-secret-key`
- **API Version**: `WP REST API Integration v3`

#### Customer Updates Webhook
- **Name**: `POS Customer Sync`
- **Status**: `Active`
- **Topic**: `Customer updated` (or `Customer created`, `Customer deleted`)
- **Delivery URL**: `https://pos.doctorsstudio.com/api/webhooks/woocommerce/customer/`
- **Secret**: `your-webhook-secret-key`
- **API Version**: `WP REST API Integration v3`

#### Stock Updates Webhook
- **Name**: `POS Stock Sync`
- **Status**: `Active`
- **Topic**: `Product updated`
- **Delivery URL**: `https://pos.doctorsstudio.com/api/webhooks/woocommerce/stock/`
- **Secret**: `your-webhook-secret-key`
- **API Version**: `WP REST API Integration v3`

### Step 3: ATUM-Specific Webhooks (If Available)
If ATUM provides webhook functionality, configure:

#### ATUM Inventory Webhook
- **Name**: `POS ATUM Inventory Sync`
- **Status**: `Active`
- **Topic**: Custom ATUM inventory events
- **Delivery URL**: `https://pos.doctorsstudio.com/api/webhooks/atum/inventory/`
- **Secret**: `your-webhook-secret-key`

#### ATUM Location Webhook
- **Name**: `POS ATUM Location Sync`
- **Status**: `Active`
- **Topic**: Custom ATUM location events
- **Delivery URL**: `https://pos.doctorsstudio.com/api/webhooks/atum/location/`
- **Secret**: `your-webhook-secret-key`

## Testing Webhooks

### Test Individual Webhooks
1. In WooCommerce webhook settings, click on a webhook
2. Click **Deliver** to send a test payload
3. Check the **Logs** section for delivery status
4. Monitor your Django logs for webhook processing

### Test with Real Data
1. Update a product in WooCommerce admin
2. Check your POS system to see if changes appear
3. Update inventory quantities
4. Verify inventory updates in POS

## Webhook Security

### Secret Key Validation
All webhooks should include a secret key for security. Update your webhook endpoints to validate the secret:

```python
# In your webhook views, add secret validation:
import hmac
import hashlib

def validate_webhook_signature(request, secret):
    signature = request.META.get('HTTP_X_WC_WEBHOOK_SIGNATURE')
    if not signature:
        return False
    
    expected = hmac.new(
        secret.encode('utf-8'),
        request.body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected)
```

## Benefits of Real-Time Webhooks

✅ **Instant Updates**: Changes in WooCommerce appear immediately in POS
✅ **Reduced Manual Sync**: No need to run manual sync operations frequently  
✅ **Data Consistency**: Both systems stay perfectly synchronized
✅ **Inventory Accuracy**: Real-time stock updates prevent overselling
✅ **Customer Experience**: Always show current product information
