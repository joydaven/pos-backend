# Membership Email Toggle Implementation

## Overview
Implemented toggle functionality to enable/disable membership onboarding and cancellation email notifications sent through GoHighLevel (GHL) workflows.

## How It Works

The system syncs membership events (purchase, cancellation, renewal, etc.) to GoHighLevel by:
1. Updating GHL custom fields (member status, billing dates, etc.)
2. Adding notes to the contact timeline

**GHL workflows/automations are triggered by these notes**, which then send emails to customers. By controlling whether notes are added, we can enable/disable email notifications.

## Implementation Details

### 1. Backend Settings Storage
Uses the existing `POSSetting` model to store toggle states:
- `membership_onboarding_email_enabled` (default: `true`)
- `membership_cancellation_email_enabled` (default: `true`)

### 2. Modified Sync Functions
**File:** `backend/crm/ghl_membership_sync.py`

#### Onboarding Emails (Membership Purchase)
- Function: `sync_membership_purchase_to_ghl()`
- Checks `membership_onboarding_email_enabled` setting
- If enabled: Adds "🎉 MEMBERSHIP PURCHASE COMPLETED" note to GHL
- If disabled: Skips note, logs "⏭️ Skipped membership purchase note (onboarding email disabled)"
- **Custom fields are always updated** (member status, billing dates, etc.)

#### Cancellation Emails
- Function: `sync_membership_cancellation_to_ghl()`
- Checks `membership_cancellation_email_enabled` setting
- If enabled: Adds "❌ MEMBERSHIP CANCELLED" note to GHL
- If disabled: Skips note, logs "⏭️ Skipped membership cancellation note (cancellation email disabled)"
- **Custom fields are always updated** (member status cleared, next charge cleared)

### 3. API Endpoints
**File:** `backend/crm/views_membership_settings.py`

#### Get Settings
```
GET /api/membership/email-settings/
```

**Response:**
```json
{
  "success": true,
  "settings": {
    "onboarding_email_enabled": true,
    "cancellation_email_enabled": true
  }
}
```

#### Update Settings
```
POST /api/membership/email-settings/update/
```

**Request Body:**
```json
{
  "onboarding_email_enabled": false,
  "cancellation_email_enabled": true
}
```

**Response:**
```json
{
  "success": true,
  "message": "Membership email settings updated successfully",
  "settings": {
    "onboarding_email_enabled": false,
    "cancellation_email_enabled": true
  }
}
```

### 4. URL Routes
**File:** `backend/crm/urls.py`

Added routes:
- `membership/email-settings/` - GET settings
- `membership/email-settings/update/` - POST to update

## Testing

### Test Onboarding Email Toggle

1. **Disable onboarding emails:**
```bash
POST /api/membership/email-settings/update/
{
  "onboarding_email_enabled": false
}
```

2. **Create a membership purchase** (via POS or WooCommerce webhook)
3. **Verify:** 
   - GHL custom fields are updated (member status, billing dates)
   - No note is added to GHL timeline
   - No onboarding email is sent
   - Backend logs show: "⏭️ Skipped membership purchase note (onboarding email disabled)"

4. **Re-enable onboarding emails:**
```bash
POST /api/membership/email-settings/update/
{
  "onboarding_email_enabled": true
}
```

5. **Create another membership purchase**
6. **Verify:**
   - GHL custom fields are updated
   - Note IS added to GHL timeline
   - Onboarding email IS sent
   - Backend logs show: "✅ Added membership purchase note to GHL (onboarding email enabled)"

### Test Cancellation Email Toggle

1. **Disable cancellation emails:**
```bash
POST /api/membership/email-settings/update/
{
  "cancellation_email_enabled": false
}
```

2. **Cancel a membership** (via POS or WooCommerce webhook)
3. **Verify:**
   - GHL custom fields are updated (member status = "Cancelled", next charge cleared)
   - No note is added to GHL timeline
   - No cancellation email is sent
   - Backend logs show: "⏭️ Skipped membership cancellation note (cancellation email disabled)"

## Frontend Integration (To Be Implemented)

### Recommended UI Location
Add to Settings page or Membership Management section:

```tsx
import { Switch, FormControlLabel, Box, Typography } from '@mui/material';

function MembershipEmailSettings() {
  const [settings, setSettings] = useState({
    onboarding_email_enabled: true,
    cancellation_email_enabled: true
  });

  const fetchSettings = async () => {
    const response = await api.get('/api/membership/email-settings/');
    setSettings(response.data.settings);
  };

  const updateSetting = async (key: string, value: boolean) => {
    await api.post('/api/membership/email-settings/update/', {
      [key]: value
    });
    setSettings(prev => ({ ...prev, [key]: value }));
  };

  return (
    <Box>
      <Typography variant="h6">Membership Email Notifications</Typography>
      <FormControlLabel
        control={
          <Switch
            checked={settings.onboarding_email_enabled}
            onChange={(e) => updateSetting('onboarding_email_enabled', e.target.checked)}
          />
        }
        label="Send onboarding emails when membership is purchased"
      />
      <FormControlLabel
        control={
          <Switch
            checked={settings.cancellation_email_enabled}
            onChange={(e) => updateSetting('cancellation_email_enabled', e.target.checked)}
          />
        }
        label="Send cancellation emails when membership is cancelled"
      />
    </Box>
  );
}
```

## Important Notes

1. **Custom Fields Always Updated:** Regardless of email toggle state, GHL custom fields (member status, billing dates, etc.) are ALWAYS updated. Only the timeline notes (which trigger emails) are controlled by the toggles.

2. **Default Behavior:** Both settings default to `true` to maintain existing behavior. Emails will continue to be sent unless explicitly disabled.

3. **Other Membership Events:** Currently only onboarding (purchase) and cancellation emails are toggleable. Other events (renewal, pause, reactivation) still add notes and trigger emails. These can be added if needed.

4. **Logging:** All actions are logged with clear indicators:
   - "✅ Added ... note to GHL (email enabled)"
   - "⏭️ Skipped ... note (email disabled)"

5. **Permissions:** API endpoints require authentication (`IsAuthenticated` permission).

6. **Audit Trail:** Settings changes are logged with the username of who made the change.

## Files Modified

1. `backend/crm/ghl_membership_sync.py` - Added settings checks before adding GHL notes
2. `backend/crm/views_membership_settings.py` - New file with API endpoints
3. `backend/crm/urls.py` - Added URL routes for settings endpoints

## Future Enhancements

If needed, similar toggles can be added for:
- Membership renewal emails
- Membership pause/hold emails
- Membership reactivation emails
- Membership expiration emails

Simply add new settings keys and check them in the respective sync functions (`sync_membership_renewal_to_ghl`, `sync_membership_pause_to_ghl`, etc.).
