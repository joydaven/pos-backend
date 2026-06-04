BEGIN;
--
-- Remove index crm_product_atum_lo_92bc55_idx from productinventory
--
DROP INDEX IF EXISTS "crm_product_atum_lo_92bc55_idx";
--
-- Remove index crm_product_product_14e0d8_idx from productinventory
--
DROP INDEX IF EXISTS "crm_product_product_14e0d8_idx";
--
-- Remove field atum_data from productinventory
--
ALTER TABLE "crm_productinventory" DROP COLUMN "atum_data" CASCADE;
--
-- Remove field atum_location_id from productinventory
--
ALTER TABLE "crm_productinventory" DROP COLUMN "atum_location_id" CASCADE;
--
-- Remove field atum_location_name from productinventory
--
ALTER TABLE "crm_productinventory" DROP COLUMN "atum_location_name" CASCADE;
--
-- Add field atum_barcode to inventorylocation
--
ALTER TABLE "crm_inventorylocation" ADD COLUMN "atum_barcode" varchar(100) DEFAULT '' NOT NULL;
ALTER TABLE "crm_inventorylocation" ALTER COLUMN "atum_barcode" DROP DEFAULT;
--
-- Add field atum_count to inventorylocation
--
ALTER TABLE "crm_inventorylocation" ADD COLUMN "atum_count" integer DEFAULT 0 NOT NULL;
ALTER TABLE "crm_inventorylocation" ALTER COLUMN "atum_count" DROP DEFAULT;
--
-- Add field atum_last_sync to inventorylocation
--
ALTER TABLE "crm_inventorylocation" ADD COLUMN "atum_last_sync" timestamp with time zone NULL;
--
-- Add field atum_location_id to inventorylocation
--
ALTER TABLE "crm_inventorylocation" ADD COLUMN "atum_location_id" integer NULL UNIQUE;
--
-- Add field atum_parent_id to inventorylocation
--
ALTER TABLE "crm_inventorylocation" ADD COLUMN "atum_parent_id" integer NULL;
--
-- Add field atum_slug to inventorylocation
--
ALTER TABLE "crm_inventorylocation" ADD COLUMN "atum_slug" varchar(100) DEFAULT '' NOT NULL;
ALTER TABLE "crm_inventorylocation" ALTER COLUMN "atum_slug" DROP DEFAULT;
--
-- Add field atum_bbe_date to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_bbe_date" date NULL;
--
-- Add field atum_expiry_days to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_expiry_days" integer NULL;
--
-- Add field atum_inbound_stock to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_inbound_stock" integer DEFAULT 0 NOT NULL;
ALTER TABLE "crm_productinventory" ALTER COLUMN "atum_inbound_stock" DROP DEFAULT;
--
-- Add field atum_inventory_id to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_inventory_id" integer NULL;
--
-- Add field atum_is_main to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_is_main" boolean DEFAULT false NOT NULL;
ALTER TABLE "crm_productinventory" ALTER COLUMN "atum_is_main" DROP DEFAULT;
--
-- Add field atum_last_sync to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_last_sync" timestamp with time zone NULL;
--
-- Add field atum_lot to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_lot" varchar(100) DEFAULT '' NOT NULL;
ALTER TABLE "crm_productinventory" ALTER COLUMN "atum_lot" DROP DEFAULT;
--
-- Add field atum_priority to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_priority" integer DEFAULT 0 NOT NULL;
ALTER TABLE "crm_productinventory" ALTER COLUMN "atum_priority" DROP DEFAULT;
--
-- Add field atum_region to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_region" varchar(100) DEFAULT '' NOT NULL;
ALTER TABLE "crm_productinventory" ALTER COLUMN "atum_region" DROP DEFAULT;
--
-- Add field atum_reserved_stock to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_reserved_stock" integer DEFAULT 0 NOT NULL;
ALTER TABLE "crm_productinventory" ALTER COLUMN "atum_reserved_stock" DROP DEFAULT;
--
-- Add field atum_stock_on_hold to productinventory
--
ALTER TABLE "crm_productinventory" ADD COLUMN "atum_stock_on_hold" integer DEFAULT 0 NOT NULL;
ALTER TABLE "crm_productinventory" ALTER COLUMN "atum_stock_on_hold" DROP DEFAULT;
COMMIT;
