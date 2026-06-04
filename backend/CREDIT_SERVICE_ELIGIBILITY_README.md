# Credit Service Eligibility Checker

## Overview

This implementation adds a comprehensive eligibility checker for credit services based on product names and series variations. The system checks against the `woo_service_types` table to determine if a specific product name and series combination is eligible for credit services.

## Database Schema

### woo_service_types Table
```sql
CREATE TABLE woo_service_types (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    default_index INTEGER NOT NULL,
    series JSONB NOT NULL,  -- Array of eligible series numbers
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);
```

### Sample Data
```sql
INSERT INTO woo_service_types (name, default_index, series, created_at, updated_at) VALUES 
('IV Ozone Therapy', 0, '[6, 3, 12]', NOW(), NOW()),
('Hyperbaric Oxygen Therapy', 0, '[10, 20, 40]', NOW(), NOW()),
('NAD+ IV Therapy', 0, '[4, 8, 16]', NOW(), NOW());
```

## API Endpoints

### 1. Get Service Types
**GET** `/api/credit-service-points/service-types/`

Returns all available service types with their eligible series.

**Response:**
```json
{
  "status": "success",
  "service_types": [
    {
      "id": 2,
      "name": "IV Ozone Therapy",
      "default_index": 0,
      "series": [6, 3, 12],
      "created_at": "2025-09-02T10:38:50.092279+04:00",
      "updated_at": "2025-09-02T10:38:50.092279+04:00"
    }
  ],
  "total_count": 3
}
```

### 2. Check Variation Eligibility
**POST** `/api/credit-service-points/check-variation-eligibility/`

Checks if a specific product variation is eligible for credit services.

**Request Body (Method 1 - Product Name + Series):**
```json
{
  "product_name": "IV Ozone Therapy",
  "series_value": 6
}
```

**Request Body (Method 2 - Product ID + Variation Data):**
```json
{
  "product_id": "uuid-string",
  "variation_data": {
    "attributes": [
      {
        "name": "Series",
        "value": "6"
      }
    ]
  }
}
```

**Response (Eligible):**
```json
{
  "status": "success",
  "eligible": true,
  "series_value": 6,
  "service_name": "IV Ozone Therapy",
  "available_series": [6, 3, 12],
  "reason": "Series 6 is eligible for credits on \"IV Ozone Therapy\""
}
```

**Response (Not Eligible):**
```json
{
  "status": "success",
  "eligible": false,
  "series_value": 5,
  "service_name": "IV Ozone Therapy",
  "available_series": [6, 3, 12],
  "reason": "Series 5 is not eligible for credits on \"IV Ozone Therapy\""
}
```

### 3. Enhanced Credit Webhook
**POST** `/api/store-manager/credits/webhook-with-variation`

Enhanced version of the credit webhook that includes variation eligibility checking.

**Request Body:**
```json
{
  "contact_woo_id": "123456",
  "product_id": "3024",
  "action": "add",
  "points": 1,
  "variation_data": {
    "attributes": [
      {
        "name": "Series",
        "value": "6"
      }
    ]
  },
  "contact_info": {
    "first_name": "John",
    "last_name": "Doe",
    "email": "john@example.com",
    "phone": "+1234567890"
  }
}
```

**Response:**
```json
{
  "status": "success",
  "message": "Added 1 points to new credit system",
  "product_id": "3024",
  "product_name": "IV Ozone Therapy",
  "available_points": 5,
  "action": "add",
  "points_changed": 1,
  "system": "new_credit_service_points_with_variation_check",
  "eligibility_check": {
    "eligible": true,
    "series_value": 6,
    "service_name": "IV Ozone Therapy",
    "reason": "Product \"IV Ozone Therapy\" with series 6 is eligible for credit services"
  }
}
```

## Model Methods

### CreditServicePoints.is_product_series_eligible_for_credits(product_name, series_value)
Checks if a specific product name and series combination is eligible for credit services.

**Parameters:**
- `product_name` (str): Name of the product to check
- `series_value` (int): Series value selected (e.g., 3, 6, 12)

**Returns:**
- `bool`: True if eligible, False otherwise

### CreditServicePoints.check_variation_eligibility_for_credits(product, variation_data)
Comprehensive eligibility check for product variations.

**Parameters:**
- `product`: Product instance
- `variation_data`: Dict containing variation information with series attributes

**Returns:**
```python
{
    'eligible': bool,
    'series_value': int or None,
    'service_name': str or None,
    'reason': str
}
```

## Frontend Integration

### 1. Product Variation Selection
When a user selects a product variation in the frontend:

```javascript
// Check eligibility when variation is selected
const checkEligibility = async (productName, seriesValue) => {
  const response = await api.post('/credit-service-points/check-variation-eligibility/', {
    product_name: productName,
    series_value: seriesValue
  });
  
  if (response.data.eligible) {
    // Show credit service options
    showCreditServiceOptions(response.data.service_name, response.data.series_value);
  } else {
    // Hide credit service options
    hideCreditServiceOptions();
  }
};
```

### 2. ProductSummaryModal Integration
In the ProductSummaryModal component, add eligibility checking:

```typescript
// Add to ProductSummaryModal.tsx
const [isEligibleForCredits, setIsEligibleForCredits] = useState(false);

useEffect(() => {
  if (selectedVariation && product.name) {
    checkVariationEligibility(product.name, selectedVariation);
  }
}, [selectedVariation, product.name]);

const checkVariationEligibility = async (productName: string, variation: VariationOption) => {
  // Extract series value from variation
  const seriesAttribute = variation.attributes?.find(attr => 
    attr.name === 'Series' || attr.name === 'pa_series'
  );
  
  if (seriesAttribute) {
    const seriesValue = parseInt(seriesAttribute.value) || 1;
    
    try {
      const response = await api.post('/credit-service-points/check-variation-eligibility/', {
        product_name: productName,
        series_value: seriesValue
      });
      
      setIsEligibleForCredits(response.data.eligible);
    } catch (error) {
      console.error('Error checking eligibility:', error);
      setIsEligibleForCredits(false);
    }
  }
};
```

## Testing

Run the test script to verify functionality:

```bash
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
python3 test_credit_eligibility.py
```

Expected output shows:
- ✅ IV Ozone Therapy (series 6, 3, 12) - ELIGIBLE
- ❌ IV Ozone Therapy (series 5) - NOT ELIGIBLE
- ✅ Hyperbaric Oxygen Therapy (series 10, 20, 40) - ELIGIBLE
- ❌ Non-existent services - NOT ELIGIBLE

## Configuration

### Adding New Service Types
To add new eligible services:

```sql
INSERT INTO woo_service_types (name, default_index, series, created_at, updated_at) 
VALUES ('New Service Name', 0, '[1, 3, 6]', NOW(), NOW());
```

### Updating Eligible Series
To modify eligible series for existing services:

```sql
UPDATE woo_service_types 
SET series = '[2, 4, 8, 16]', updated_at = NOW() 
WHERE name = 'Service Name';
```

## Error Handling

The system includes comprehensive error handling:

1. **Invalid Series**: Returns `eligible: false` with reason
2. **Non-existent Products**: Returns `eligible: false` 
3. **Missing Variation Data**: Logs warning but allows operation
4. **Database Errors**: Logged and returns `eligible: false`

## Backward Compatibility

- Legacy credit webhook (`/store-manager/credits/webhook`) continues to work
- Non-variable products use the legacy `WooCreditedService` system
- Variable products automatically use the new `CreditServicePoints` system
- Existing credit records are preserved

## Security

- All endpoints require authentication (temporarily disabled for testing)
- Input validation using Django serializers
- SQL injection protection through Django ORM
- Comprehensive logging for audit trails
