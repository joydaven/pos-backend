# Bundle Product API Documentation

## Problem

The current implementation has two issues with bundle products:

1. When syncing bundle data from WooCommerce webhooks, we were including non-existent products in the bundle data with a `not_found: true` flag
2. The frontend is trying to fetch bundle content products directly from WooCommerce using the `/api/product/{woo_id}/` endpoint, which causes 404 errors when those products don't exist in WooCommerce

## Solution

We've implemented a two-part solution:

1. **Backend Fix**: Modified the webhook handler to filter out non-existent products from bundle data
   - Products that don't exist in our local database are now completely excluded from the bundle data
   - This prevents the frontend from trying to fetch non-existent products

2. **New API Endpoint**: Created a dedicated endpoint for bundle products that uses our local database
   - `/api/bundle-product/{woo_product_id}/` - Returns bundle data from our local database
   - This endpoint should be used instead of `/api/product/{woo_id}/` for bundle products

## Frontend Integration

The frontend needs to be updated to use the new endpoint for bundle products:

1. When loading a bundle product, use `/api/bundle-product/{woo_product_id}/` instead of `/api/product/{woo_id}/`
2. The response format is similar to the existing product endpoint but includes the processed bundle data from our local database

Example response:

```json
{
  "status": "success",
  "product": {
    "id": 600,
    "name": "Studio Detox Program",
    "type": "bundle",
    "price": "1500.00",
    "regular_price": "1800.00",
    "description": "...",
    "short_description": "...",
    "bundle_data": {
      "processed_bundle_items": [
        {
          "product_id": 5731,
          "title": "CONSULT: Member Visit",
          "quantity": 1,
          "optional": false,
          "local_product_id": "90148268-fc16-4dcb-a571-6ff850ba24bb",
          "default_variation_attributes": {
            "attribute_series": "series-1"
          }
        },
        {
          "product_id": 5717,
          "title": "COACH: Health Coach",
          "quantity": 1,
          "optional": false,
          "local_product_id": "8143704d-4284-46e5-b384-6c75b99f2020"
        }
      ],
      "default_series_by_item": {
        "5731": {
          "attribute_series": "series-1"
        }
      }
    },
    "local_data": {
      "id": "3bd5bd16-fac3-45f3-b6ef-4371f229ff55",
      "stock_quantity": 100,
      "created_at": "2025-08-01T12:00:00Z",
      "updated_at": "2025-08-21T02:27:28.625Z"
    }
  }
}
```

## Benefits

1. **Improved Reliability**: No more 404 errors when trying to fetch non-existent bundle content products
2. **Better Performance**: Fetches data from our local database instead of making external API calls to WooCommerce
3. **Data Consistency**: Bundle data is always in sync with our local database

## Implementation Details

1. The webhook handler (`sync_bundle_data_from_webhook_payload`) now filters out non-existent products
2. A new view (`get_bundle_product`) in `views_bundle.py` serves bundle data from our local database
3. The URL pattern `/api/bundle-product/<int:woo_product_id>/` is registered in `urls.py`

## Testing

To test this implementation:

1. Update a bundle product in WooCommerce
2. Verify the webhook processes it correctly and filters out non-existent products
3. Use the new endpoint to fetch the bundle product data
4. Confirm no 404 errors occur when loading bundle products in the frontend
