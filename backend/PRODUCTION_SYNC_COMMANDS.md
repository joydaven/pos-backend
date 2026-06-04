# Production Database Sync Commands

## 🚀 Complete Production Database Sync Commands

Use these commands to sync your production database with fresh WooCommerce data and OAuth2 authentication.

### Prerequisites
```bash
# Set proper ownership
sudo chown -R joy:joy /var/www/newpos-posds/woo-ghl-contact-db
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
source venv/bin/activate
```

## 📋 1. Database Setup (New Database Only)
```bash
# Apply Django migrations (only needed for new database)
python3 manage.py migrate
```

## 🏪 2. WooCommerce Data Sync (Fresh Data)
```bash
# Sync products from WooCommerce (includes all product types and variations)
python3 manage.py sync_woocommerce_products

# Sync customers from WooCommerce
python3 manage.py sync_woocommerce_customers

# Sync subscription products (ensures subscription table is populated)
python3 manage.py sync_subscription_products

# Sync inventory locations (creates default location and inventory records)
python3 manage.py sync_inventory_locations --create-default-location

# Sync brands (optional - may have no data)
python3 manage.py sync_brands --verbose
```

## 🔐 3. OAuth2 Authentication Sync (From Old Database)
```bash
# Run the OAuth2 sync script (syncs users, applications, tokens)
python3 sync_oauth_final.py

# Fix OAuth2 sequences to prevent duplicate key errors
python3 -c "
import psycopg2

conn = psycopg2.connect(
    dbname='pos_prod',
    user='pos_produser', 
    password='$DB_PASSWORD (from .env)',
    host='127.0.0.1',
    port='5432'
)

cursor = conn.cursor()

tables_to_fix = [
    'oauth2_provider_application',
    'oauth2_provider_refreshtoken', 
    'oauth2_provider_accesstoken',
    'oauth2_provider_grant'
]

for table in tables_to_fix:
    try:
        cursor.execute(f'SELECT COALESCE(MAX(id), 0) FROM {table};')
        max_id = cursor.fetchone()[0]
        sequence_name = f'{table}_id_seq'
        cursor.execute(f'SELECT setval(\\'{sequence_name}\\', {max_id + 1});')
        print(f'✅ Fixed {table} sequence: set to {max_id + 1}')
    except Exception as e:
        print(f'⚠️ Could not fix {table}: {e}')

cursor.execute('SELECT COALESCE(MAX(id), 0) FROM auth_user;')
max_user_id = cursor.fetchone()[0]
cursor.execute(f'SELECT setval(\\'auth_user_id_seq\\', {max_user_id + 1});')
print(f'✅ Fixed auth_user sequence: set to {max_user_id + 1}')

conn.commit()
cursor.close()
conn.close()
print('🎉 OAuth2 sequence fix completed!')
"
```

## 🧹 4. Clean Slate Option (Remove Old Data, Keep Only Fresh)
```bash
# Clean old product data and keep only fresh WooCommerce data
python3 -c "
import psycopg2
from datetime import datetime

conn = psycopg2.connect(
    dbname='pos_prod',
    user='pos_produser',
    password='$DB_PASSWORD (from .env)',
    host='127.0.0.1',
    port='5432'
)

cursor = conn.cursor()
today = datetime.now().strftime('%Y-%m-%d')

print('🧹 Cleaning old product data...')

# Disable foreign key constraints
cursor.execute('SET session_replication_role = replica;')

# Get old product IDs
cursor.execute(f\"\"\"
    SELECT id FROM crm_product 
    WHERE DATE(created_at) < '{today}';
\"\"\")
old_product_ids = [row[0] for row in cursor.fetchall()]

if old_product_ids:
    id_list = \"','\".join(str(id) for id in old_product_ids)
    
    # Clean product-related tables
    tables = ['crm_productvariation', 'crm_productsimple', 'crm_productbundle', 
              'crm_productgrouped', 'crm_productsubscription', 'crm_productinventory']
    
    for table in tables:
        try:
            cursor.execute(f\"DELETE FROM {table} WHERE product_id IN ('{id_list}');\")
            print(f'✅ Cleaned {table}: {cursor.rowcount} records')
        except Exception as e:
            print(f'⚠️ {table}: {e}')
    
    # Delete old products
    cursor.execute(f\"DELETE FROM crm_product WHERE DATE(created_at) < '{today}';\")
    print(f'✅ Removed {cursor.rowcount} old products')

# Re-enable foreign key constraints
cursor.execute('SET session_replication_role = DEFAULT;')
conn.commit()
cursor.close()
conn.close()
print('🎉 Cleanup complete!')
"

# After cleanup, run fresh WooCommerce sync
python3 manage.py sync_woocommerce_products
python3 manage.py sync_subscription_products
```

## 🔍 5. Verification Commands
```bash
# Verify final database state
python3 -c "
import psycopg2

conn = psycopg2.connect(
    dbname='pos_prod',
    user='pos_produser',
    password='$DB_PASSWORD (from .env)',
    host='127.0.0.1',
    port='5432'
)

cursor = conn.cursor()

print('📊 Production Database Status:')
cursor.execute('SELECT COUNT(*) FROM crm_product;')
print(f'Products: {cursor.fetchone()[0]:,}')

cursor.execute('SELECT COUNT(*) FROM crm_productvariation;')
print(f'Product Variations: {cursor.fetchone()[0]:,}')

cursor.execute('SELECT COUNT(*) FROM crm_contact WHERE woo_customer_id IS NOT NULL;')
print(f'WooCommerce Customers: {cursor.fetchone()[0]:,}')

cursor.execute('SELECT COUNT(*) FROM oauth2_provider_accesstoken;')
print(f'OAuth2 Access Tokens: {cursor.fetchone()[0]:,}')

cursor.execute('SELECT COUNT(*) FROM crm_productinventory;')
print(f'Inventory Records: {cursor.fetchone()[0]:,}')

cursor.close()
conn.close()
print('✅ Verification complete!')
"
```

## 🛠️ 6. Set Proper Permissions
```bash
# Set web server permissions
sudo chown -R www-data:www-data /var/www/newpos-posds/woo-ghl-contact-db
```

## 📝 Quick Reference - Complete Fresh Sync
```bash
# Complete fresh production sync (run all at once)
cd /var/www/newpos-posds/woo-ghl-contact-db/backend
source venv/bin/activate

# WooCommerce sync
python3 manage.py sync_woocommerce_products
python3 manage.py sync_woocommerce_customers
python3 manage.py sync_subscription_products
python3 manage.py sync_inventory_locations --create-default-location

# OAuth2 sync (if needed)
python3 sync_oauth_final.py

# Set permissions
sudo chown -R www-data:www-data /var/www/newpos-posds/woo-ghl-contact-db
```

## 🎯 Database Configuration
Make sure your `.env` file has the correct production database settings:
```
DB_NAME=pos_prod
DB_USER=pos_produser
DB_PASSWORD=$DB_PASSWORD (from .env)
DB_HOST=127.0.0.1
DB_PORT=5432
```

## 📋 Expected Results
After running all commands, you should have:
- ✅ ~1,309 products from WooCommerce
- ✅ ~1,753 customers with WooCommerce IDs
- ✅ Product variations, bundles, subscriptions
- ✅ Working OAuth2 authentication
- ✅ Inventory locations and records
- ✅ No duplicate key errors

## ⚠️ Important Notes
1. **Always backup your database before running these commands**
2. **Run commands in the specified order**
3. **Check the verification output to ensure success**
4. **The OAuth2 sync script (`sync_oauth_final.py`) should exist in your backend directory**
5. **Adjust database credentials in the scripts if they change**
