CREATE TABLE IF NOT EXISTS saved_carts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    user_id INTEGER NOT NULL,
    customer_id UUID,
    cart_items JSONB DEFAULT '[]'::jsonb,
    order_discount DECIMAL(10,2),
    order_discount_type VARCHAR(10) DEFAULT 'dollar',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS customer_general_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL,
    note TEXT NOT NULL,
    created_by_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    is_important BOOLEAN DEFAULT FALSE
);

ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS bundle_products JSONB DEFAULT '[]'::jsonb;
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS trashed_at TIMESTAMP;
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS trashed_by_id INTEGER;
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_info JSONB DEFAULT '{}'::jsonb;
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_cost DECIMAL(10,2) DEFAULT 0;
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_address TEXT DEFAULT '';
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_city VARCHAR(100) DEFAULT '';
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_state VARCHAR(100) DEFAULT '';
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_postcode VARCHAR(20) DEFAULT '';
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_country VARCHAR(100) DEFAULT '';
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS tracking_number VARCHAR(100) DEFAULT '';
ALTER TABLE crm_posorder ADD COLUMN IF NOT EXISTS shipping_status VARCHAR(50) DEFAULT '';

-- InventoryLocation missing columns
ALTER TABLE crm_inventorylocation ADD COLUMN IF NOT EXISTS atum_location_id INTEGER;
ALTER TABLE crm_inventorylocation ADD COLUMN IF NOT EXISTS slug VARCHAR(255) DEFAULT '';
ALTER TABLE crm_inventorylocation ADD COLUMN IF NOT EXISTS parent_location_id INTEGER;
ALTER TABLE crm_inventorylocation ADD COLUMN IF NOT EXISTS barcode VARCHAR(255) DEFAULT '';
ALTER TABLE crm_inventorylocation ADD COLUMN IF NOT EXISTS product_count INTEGER DEFAULT 0;

-- WooServiceTypes table
CREATE TABLE IF NOT EXISTS woo_service_types (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    default_index INTEGER,
    series JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- WooCreditLog table
CREATE TABLE IF NOT EXISTS woo_credit_logs (
    id BIGSERIAL PRIMARY KEY,
    woo_credited_service_id INTEGER NOT NULL,
    action VARCHAR(255) NOT NULL,
    order_number VARCHAR(255) NOT NULL,
    points_change INTEGER NOT NULL,
    updated_points INTEGER NOT NULL,
    reason VARCHAR(255) NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
