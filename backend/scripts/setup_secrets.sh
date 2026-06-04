#!/bin/bash
#
# Setup Google Cloud Secret Manager secrets for DS POS
#
# Usage:
#   ./setup_secrets.sh staging    # Create staging secrets from current .env
#   ./setup_secrets.sh production # Create production secrets (prompts for values)
#
# Prerequisites:
#   1. gcloud CLI installed and authenticated as a project owner/editor
#   2. Secret Manager API enabled (already done)
#   3. Service account has roles/secretmanager.secretAccessor
#
# To grant the service account access (run from a machine with owner permissions):
#   gcloud projects add-iam-policy-binding doctors-studio-backend \
#     --member="serviceAccount:629465229019-compute@developer.gserviceaccount.com" \
#     --role="roles/secretmanager.secretAccessor"

set -euo pipefail

PROJECT_ID="doctors-studio-backend"
ENVIRONMENT="${1:-staging}"

if [[ "$ENVIRONMENT" != "staging" && "$ENVIRONMENT" != "production" ]]; then
    echo "Usage: $0 [staging|production]"
    exit 1
fi

echo "============================================"
echo "  DS POS Secret Manager Setup"
echo "  Project:     $PROJECT_ID"
echo "  Environment: $ENVIRONMENT"
echo "============================================"
echo ""

# Function to create or update a shared secret (POS_{key})
# Used by get_secret() in secrets.py — same value across staging/production
create_secret() {
    local key="$1"
    local value="$2"
    local secret_id="POS_${key}"

    # Check if secret already exists
    if gcloud secrets describe "$secret_id" --project="$PROJECT_ID" &>/dev/null; then
        echo "  ↻ Updating: $secret_id"
        echo -n "$value" | gcloud secrets versions add "$secret_id" \
            --project="$PROJECT_ID" \
            --data-file=- \
            --quiet
    else
        echo "  + Creating: $secret_id"
        echo -n "$value" | gcloud secrets create "$secret_id" \
            --project="$PROJECT_ID" \
            --replication-policy="automatic" \
            --data-file=- \
            --quiet
    fi
}

# Function to create or update a server-specific secret (POS_{environment}_{key})
# Used by get_server_secret() in secrets.py — different value per staging/production
create_server_secret() {
    local key="$1"
    local value="$2"
    local secret_id="POS_${ENVIRONMENT}_${key}"

    if gcloud secrets describe "$secret_id" --project="$PROJECT_ID" &>/dev/null; then
        echo "  ↻ Updating: $secret_id"
        echo -n "$value" | gcloud secrets versions add "$secret_id" \
            --project="$PROJECT_ID" \
            --data-file=- \
            --quiet
    else
        echo "  + Creating: $secret_id"
        echo -n "$value" | gcloud secrets create "$secret_id" \
            --project="$PROJECT_ID" \
            --replication-policy="automatic" \
            --data-file=- \
            --quiet
    fi
}

# Function to read value from .env file
read_env() {
    local key="$1"
    local default="${2:-}"
    local env_file="${3:-/var/www/staging-pos/woo-ghl-contact-db/backend/.env}"

    # Read from .env file, handling quoted values and inline comments
    local value
    value=$(grep -E "^${key}=" "$env_file" 2>/dev/null | head -1 | sed "s/^${key}=//" | sed "s/^['\"]//;s/['\"]$//" | sed 's/ *#.*//')

    if [[ -z "$value" ]]; then
        echo "$default"
    else
        echo "$value"
    fi
}

if [[ "$ENVIRONMENT" == "staging" ]]; then
    echo "Reading secrets from .env file..."
    ENV_FILE="/var/www/staging-pos/woo-ghl-contact-db/backend/.env"

    if [[ ! -f "$ENV_FILE" ]]; then
        echo "ERROR: .env file not found at $ENV_FILE"
        exit 1
    fi

    echo ""
    echo "--- Django Core (server-specific) ---"
    create_server_secret "SECRET_KEY" "$(read_env SECRET_KEY)"
    create_secret "DEBUG" "$(read_env DEBUG False)"
    create_secret "ALLOWED_HOSTS" "$(read_env ALLOWED_HOSTS 'localhost,127.0.0.1')"

    echo ""
    echo "--- PostgreSQL (POS Database) ---"
    create_secret "DB_NAME" "$(read_env DB_NAME)"
    create_secret "DB_USER" "$(read_env DB_USER)"
    create_server_secret "DB_PASSWORD" "$(read_env DB_PASSWORD)"
    create_secret "DB_HOST" "$(read_env DB_HOST localhost)"
    create_secret "DB_PORT" "$(read_env DB_PORT 5432)"

    echo ""
    echo "--- PostgreSQL (GHL Database) ---"
    create_secret "GHL_DB_NAME" "$(read_env GHL_DB_NAME)"
    create_secret "GHL_DB_USER" "$(read_env GHL_DB_USER)"
    create_server_secret "GHL_DB_PASSWORD" "$(read_env GHL_DB_PASSWORD)"
    create_secret "GHL_DB_HOST" "$(read_env GHL_DB_HOST '127.0.0.1')"
    create_secret "GHL_DB_PORT" "$(read_env GHL_DB_PORT 5432)"

    echo ""
    echo "--- WooCommerce Database (Cloud SQL) ---"
    create_secret "WOO_DB_NAME" "$(read_env WOO_DB_NAME)"
    create_secret "WOO_DB_USER" "$(read_env WOO_DB_USER)"
    create_server_secret "WOO_DB_PASSWORD" "$(read_env WOO_DB_PASSWORD)"
    create_secret "WOO_DB_HOST" "$(read_env WOO_DB_HOST '10.0.16.3')"
    create_secret "WOO_DB_PORT" "$(read_env WOO_DB_PORT 3306)"

    echo ""
    echo "--- WooCommerce API ---"
    create_secret "WOO_API_URL" "$(read_env WOO_API_URL)"
    create_secret "WOO_CONSUMER_KEY" "$(read_env WOO_CONSUMER_KEY)"
    create_secret "WOO_CONSUMER_SECRET" "$(read_env WOO_CONSUMER_SECRET)"
    create_secret "WOOCOMMERCE_API_VERSION" "$(read_env WOOCOMMERCE_API_VERSION 'wc/v3')"
    create_secret "WOOCOMMERCE_WEBHOOK_SECRET" "$(read_env WOOCOMMERCE_WEBHOOK_SECRET)"

    echo ""
    echo "--- Authorize.net ---"
    create_secret "AUTHORIZE_NET_LOGIN_ID" "$(read_env AUTHORIZE_NET_LOGIN_ID)"
    create_secret "AUTHORIZE_NET_TRANSACTION_KEY" "$(read_env AUTHORIZE_NET_TRANSACTION_KEY)"
    create_secret "AUTHORIZE_NET_CLIENT_KEY" "$(read_env AUTHORIZE_NET_CLIENT_KEY)"
    create_secret "AUTHORIZE_NET_SANDBOX" "$(read_env AUTHORIZE_NET_SANDBOX False)"
    create_secret "AUTHORIZE_NET_CARD_VALIDATION_UPON_ADD" "$(read_env AUTHORIZE_NET_CARD_VALIDATION_UPON_ADD liveMode)"
    create_secret "AUTHORIZE_NET_SIGNATURE_KEY" "$(read_env AUTHORIZE_NET_SIGNATURE_KEY)"

    echo ""
    echo "--- GoHighLevel ---"
    create_secret "GHL_API_TOKEN" "$(read_env GHL_API_TOKEN)"
    create_secret "GHL_DEFAULT_LOCATION_ID" "$(read_env GHL_DEFAULT_LOCATION_ID)"
    create_secret "GHL_CLIENT_ID" "$(read_env GHL_CLIENT_ID)"
    create_secret "GHL_CLIENT_SECRET" "$(read_env GHL_CLIENT_SECRET)"
    create_secret "GHL_REDIRECT_URI" "$(read_env GHL_REDIRECT_URI)"
    create_secret "GHL_INBOUND_WEBHOOK_SECRET" "$(read_env GHL_INBOUND_WEBHOOK_SECRET)"

    echo ""
    echo "--- Email (Mailgun + SMTP) ---"
    create_secret "MAILGUN_API_KEY" "$(read_env MAILGUN_API_KEY)"
    create_secret "MAILGUN_DOMAIN" "$(read_env MAILGUN_DOMAIN doctorsstudio.com)"
    create_secret "MAILGUN_API_URL" "$(read_env MAILGUN_API_URL 'https://api.mailgun.net/v3')"
    create_secret "EMAIL_HOST" "$(read_env EMAIL_HOST)"
    create_secret "EMAIL_PORT" "$(read_env EMAIL_PORT 587)"
    create_secret "EMAIL_HOST_USER" "$(read_env EMAIL_HOST_USER)"
    create_secret "EMAIL_HOST_PASSWORD" "$(read_env EMAIL_HOST_PASSWORD)"
    create_secret "DEFAULT_FROM_EMAIL" "$(read_env DEFAULT_FROM_EMAIL 'Support@doctorsstudio.com')"

    echo ""
    echo "--- Google OAuth2 ---"
    create_secret "GOOGLE_OAUTH2_CLIENT_ID" "$(read_env GOOGLE_OAUTH2_CLIENT_ID)"
    create_secret "GOOGLE_OAUTH2_CLIENT_SECRET" "$(read_env GOOGLE_OAUTH2_CLIENT_SECRET)"

    echo ""
    echo "--- OAuth2 (Django) ---"
    create_secret "CLIENT_ID" "$(read_env CLIENT_ID)"
    create_secret "CLIENT_SECRET" "$(read_env CLIENT_SECRET)"
    create_server_secret "OAUTH2_CLIENT_ID" "$(read_env OAUTH2_CLIENT_ID)"

    echo ""
    echo "--- ShipStation ---"
    create_secret "SHIPSTATION_API_KEY" "$(read_env SHIPSTATION_API_KEY)"
    create_secret "SHIPSTATION_API_SECRET" "$(read_env SHIPSTATION_API_SECRET)"
    create_secret "SHIPSTATION_WEBHOOK_SECRET" "$(read_env SHIPSTATION_WEBHOOK_SECRET)"
    create_secret "SHIPSTATION_TEST_MODE" "$(read_env SHIPSTATION_TEST_MODE True)"

    echo ""
    echo "--- Sentry ---"
    create_server_secret "SENTRY_DSN" "$(read_env SENTRY_DSN)"
    create_secret "SENTRY_ENVIRONMENT" "$(read_env SENTRY_ENVIRONMENT staging)"

    echo ""
    echo "--- Session / Cookie ---"
    create_secret "SESSION_COOKIE_SAMESITE" "$(read_env SESSION_COOKIE_SAMESITE Strict)"
    create_secret "CSRF_COOKIE_SAMESITE" "$(read_env CSRF_COOKIE_SAMESITE Strict)"

    echo ""
    echo "--- Redis ---"
    create_secret "REDIS_URL" "$(read_env REDIS_URL 'redis://127.0.0.1:6379/0')"

    echo ""
    echo "--- Shipping ---"
    create_secret "POS_ENABLE_SHIPPING" "$(read_env POS_ENABLE_SHIPPING True)"
    create_secret "SHIPSTATION_FROM_NAME" "$(read_env SHIPSTATION_FROM_NAME 'Doctors Studio')"
    create_secret "SHIPSTATION_FROM_COMPANY" "$(read_env SHIPSTATION_FROM_COMPANY 'Doctors Studio')"
    create_secret "SHIPSTATION_FROM_STREET1" "$(read_env SHIPSTATION_FROM_STREET1 '1001 S Federal Hwy')"
    create_secret "SHIPSTATION_FROM_STREET2" "$(read_env SHIPSTATION_FROM_STREET2 'Suite 201')"
    create_secret "SHIPSTATION_FROM_CITY" "$(read_env SHIPSTATION_FROM_CITY 'Boca Raton')"
    create_secret "SHIPSTATION_FROM_STATE" "$(read_env SHIPSTATION_FROM_STATE FL)"
    create_secret "SHIPSTATION_FROM_ZIP" "$(read_env SHIPSTATION_FROM_ZIP 33432)"
    create_secret "SHIPSTATION_FROM_COUNTRY" "$(read_env SHIPSTATION_FROM_COUNTRY US)"

    echo ""
    echo "============================================"
    echo "  ✅ All staging secrets created!"
    echo ""
    echo "  Next steps:"
    echo "  1. Grant service account access (if not done):"
    echo "     gcloud projects add-iam-policy-binding $PROJECT_ID \\"
    echo "       --member='serviceAccount:629465229019-compute@developer.gserviceaccount.com' \\"
    echo "       --role='roles/secretmanager.secretAccessor'"
    echo ""
    echo "  2. Add to systemd unit or /etc/environment:"
    echo "     USE_GCP_SECRETS=true"
    echo "     GCP_PROJECT_ID=$PROJECT_ID"
    echo "     APP_ENVIRONMENT=$ENVIRONMENT"
    echo ""
    echo "  3. Restart gunicorn:"
    echo "     sudo systemctl restart staging-pos"
    echo "============================================"

elif [[ "$ENVIRONMENT" == "production" ]]; then
    echo ""
    echo "Production setup requires manual entry of each secret value."
    echo "Run this script interactively and it will prompt for each value."
    echo ""
    echo "Alternatively, create a production .env file and modify the ENV_FILE"
    echo "path in this script to point to it."
    echo ""
    echo "For now, use the GCP Console to create production secrets manually:"
    echo "  https://console.cloud.google.com/security/secret-manager?project=$PROJECT_ID"
    echo ""
    echo "Shared secrets:         POS_{KEY}  (e.g. POS_GHL_API_TOKEN)"
    echo "Server-specific secrets: POS_production_{KEY}  (e.g. POS_production_DB_PASSWORD)"
fi
