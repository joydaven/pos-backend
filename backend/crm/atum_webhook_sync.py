"""
ATUM Inventory Webhook Sync Utilities

This module contains reusable functions extracted from restore_atum_locations.py
for integration with WooCommerce webhooks to provide real-time ATUM inventory sync.
"""

import logging
from django.db import transaction
from .models import Product, ProductInventory, InventoryLocation, ProductVariation
from .woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)

# Location name mappings for common mismatches
LOCATION_NAME_MAPPINGS = {
    'Jupiter Inventory': 'Jupiter Inventory',
    'Boca Inventory': 'Boca Inventory',
    'Boca Clinic': 'Boca Raton Clinic',
    'Jupiter Clinic': 'Jupiter Clinic',
    'Chicago Clinic': 'Chicago Clinic',
    'Vendor Fulfillment': 'Vendor Fulfillment',
    'Dropship': 'Dropship',
    'Clinic Inventory': 'Clinic Inventory',
    'Clinic Inventory.': 'Clinic Inventory',  # Handle period at end
    'Main Inventory': 'Main Inventory',
    'Clinical Inventory': 'Clinical Inventory',
}

def has_atum_inventory_changes(webhook_data):
    """
    Detect if the webhook contains ATUM inventory-related changes.
    
    Args:
        webhook_data (dict): Webhook payload from WooCommerce
        
    Returns:
        bool: True if ATUM inventory changes are detected
    """
    # Enhanced ATUM-specific indicators in webhook data
    atum_indicators = [
        'stock_quantity',
        'stock_status', 
        'manage_stock',
        'meta_data',
        'date_modified',  # Any product modification could include ATUM changes
        'date_modified_gmt',
        'status',  # Status changes might affect inventory
        'type'  # Product type changes might affect inventory structure
    ]
    
    # Check if any ATUM-related fields are present
    for indicator in atum_indicators:
        if indicator in webhook_data:
            logger.info(f"ATUM inventory change detected: {indicator} field present")
            return True
    
    # Check meta_data for ATUM-specific fields
    meta_data = webhook_data.get('meta_data', [])
    if isinstance(meta_data, list):
        for meta in meta_data:
            if isinstance(meta, dict):
                key = meta.get('key', '')
                value = meta.get('value', '')
                # Look for ATUM-specific keys
                if ('atum' in key.lower() or 
                    'inventory' in key.lower() or
                    'location' in key.lower() or
                    'stock' in key.lower() or
                    'manage' in key.lower()):
                    logger.info(f"ATUM inventory change detected: meta_data key '{key}' = '{value}'")
                    return True
    
    # For products with ATUM inventory, assume any webhook could contain inventory changes
    # This is a more aggressive approach but ensures we don't miss ATUM updates
    product_id = webhook_data.get('id')
    if product_id:
        try:
            from .models import Product, ProductInventory
            local_product = Product.objects.get(woo_product_id=product_id)
            
            # Check if this product has any ATUM inventory locations
            has_atum_locations = ProductInventory.objects.filter(
                product=local_product,
                location__atum_location_id__isnull=False
            ).exists()
            
            if has_atum_locations:
                logger.info(f"ATUM inventory change detected: Product {product_id} has ATUM locations - assuming inventory change")
                return True
                
        except Exception as e:
            logger.debug(f"Could not check ATUM locations for product {product_id}: {e}")
    
    return False

def is_atum_data_stale(inventory_data):
    """
    Check if ATUM inventory data is stale using the corrected logic.
    
    Data is only considered stale if ALL records have:
    - No stock management (manage_stock = false) AND
    - Zero/null quantities AND  
    - Not marked as in stock
    
    Args:
        inventory_data (list): List of ATUM inventory records from API
        
    Returns:
        bool: True if data is stale and should be removed
    """
    if not inventory_data:
        return True  # No data = stale
    
    all_stale = True
    for api_inv in inventory_data:
        meta_data = api_inv.get('meta_data', {})
        stock_quantity = meta_data.get('stock_quantity')
        manage_stock = meta_data.get('manage_stock', False)
        stock_status = meta_data.get('stock_status', '')
        
        # Consider record active if ANY of these conditions are true:
        # 1. Stock is actively managed (even if quantity is 0)
        # 2. Stock status is 'instock' (even if not managed)
        # 3. Has positive stock quantity
        if (manage_stock or 
            stock_status == 'instock' or 
            (stock_quantity and int(stock_quantity) > 0)):
            all_stale = False
            break
    
    return all_stale

def sync_product_atum_inventory(product, dry_run=False):
    """
    Sync ATUM inventory for a single product (extracted from restore_atum_locations.py).
    
    Args:
        product (Product): Django Product instance
        dry_run (bool): If True, only log what would be changed
        
    Returns:
        dict: Sync results with statistics
    """
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        logger.info(f"🔄 Syncing ATUM inventory for product: {product.name} (WooID: {product.woo_product_id})")
        
        sync_results = {
            'success': True,
            'product_updated': False,
            'variations_processed': 0,
            'variations_updated': 0,
            'locations_added': 0,
            'locations_removed': 0,
            'errors': []
        }
        
        # Check product type and handle accordingly
        is_variable = product.product_type in ['variable', 'variable_subscription']
        is_bundle = product.product_type == 'bundle'
        is_subscription = product.product_type == 'subscription'
        
        if is_variable:
            # For variable products, process each variation
            variations = ProductVariation.objects.filter(product=product)
            logger.info(f"Found {variations.count()} variations for variable product: {product.name}")
            
            for variation in variations:
                logger.info(f"  Processing variation: {variation.woo_variation_id}")
                sync_results['variations_processed'] += 1
                
                try:
                    variation_inventories = wc_api.get_product_inventories(variation.woo_variation_id)
                    
                    if not variation_inventories:
                        logger.warning(f"  No ATUM inventory found for variation: {variation.woo_variation_id}")
                        
                        # Remove any existing ATUM inventory records for this variation
                        existing_variation_inventories = ProductInventory.objects.filter(
                            product=product,
                            variation=variation,
                            location__atum_location_id__isnull=False,
                            notes__contains='Synced from ATUM inventory'
                        )
                        
                        if existing_variation_inventories.exists():
                            count = existing_variation_inventories.count()
                            if not dry_run:
                                with transaction.atomic():
                                    deleted_count = existing_variation_inventories.delete()[0]
                                    logger.info(f"    Removed {deleted_count} orphaned ATUM inventory records for variation {variation.woo_variation_id}")
                                    sync_results['locations_removed'] += deleted_count
                                    sync_results['variations_updated'] += 1
                            else:
                                logger.info(f"    Would remove {count} orphaned ATUM inventory records for variation {variation.woo_variation_id} (dry run)")
                        continue
                    
                    # Check if API data is stale
                    if is_atum_data_stale(variation_inventories):
                        logger.warning(f"  API returned stale data for variation {variation.woo_variation_id} (all locations inactive)")
                        
                        # Remove existing ATUM inventory records as they're based on stale data
                        existing_variation_inventories = ProductInventory.objects.filter(
                            product=product,
                            variation=variation,
                            location__atum_location_id__isnull=False,
                            notes__contains='Synced from ATUM inventory'
                        )
                        
                        if existing_variation_inventories.exists():
                            count = existing_variation_inventories.count()
                            if not dry_run:
                                with transaction.atomic():
                                    deleted_count = existing_variation_inventories.delete()[0]
                                    logger.info(f"    Removed {deleted_count} stale ATUM inventory records for variation {variation.woo_variation_id}")
                                    sync_results['locations_removed'] += deleted_count
                                    sync_results['variations_updated'] += 1
                            else:
                                logger.info(f"    Would remove {count} stale ATUM inventory records for variation {variation.woo_variation_id} (dry run)")
                        continue
                    
                    # Process variation inventory locations
                    variation_result = _process_variation_inventory(product, variation, variation_inventories, dry_run)
                    if variation_result['updated']:
                        sync_results['variations_updated'] += 1
                        sync_results['locations_added'] += variation_result['locations_added']
                        sync_results['locations_removed'] += variation_result['locations_removed']
                        
                except Exception as e:
                    error_msg = f"Error processing variation {variation.woo_variation_id}: {str(e)}"
                    logger.error(error_msg)
                    sync_results['errors'].append(error_msg)
        elif is_bundle:
            # Handle bundle products - they need special ATUM inventory sync
            logger.info(f"Processing bundle product: {product.name}")
            product_inventories = wc_api.get_product_inventories(product.woo_product_id)
            
            if not product_inventories:
                logger.warning(f"No ATUM inventory found for bundle product: {product.name}")
                
                # Remove any existing ATUM inventory records for this bundle
                existing_atum_inventories = ProductInventory.objects.filter(
                    product=product,
                    variation__isnull=True,  # Only product-level inventory
                    location__atum_location_id__isnull=False,
                    notes__contains='ATUM'  # More flexible matching for bundle products
                )
                
                if existing_atum_inventories.exists():
                    count = existing_atum_inventories.count()
                    if not dry_run:
                        with transaction.atomic():
                            deleted_count = existing_atum_inventories.delete()[0]
                            logger.info(f"  Removed {deleted_count} orphaned ATUM inventory records for bundle")
                            sync_results['locations_removed'] += deleted_count
                            sync_results['product_updated'] = True
                    else:
                        logger.info(f"  Would remove {count} orphaned ATUM inventory records for bundle (dry run)")
            else:
                logger.info(f"Found {len(product_inventories)} ATUM inventory locations for bundle: {product.name}")
                
                # For bundle products, we need to clean up ALL existing ATUM records first
                # then add only the current ones to prevent duplicates
                existing_atum_inventories = ProductInventory.objects.filter(
                    product=product,
                    variation__isnull=True,
                    location__atum_location_id__isnull=False,
                    notes__contains='ATUM'
                )
                
                if not dry_run:
                    with transaction.atomic():
                        # Remove all existing ATUM records first
                        if existing_atum_inventories.exists():
                            deleted_count = existing_atum_inventories.delete()[0]
                            logger.info(f"  Cleaned up {deleted_count} existing ATUM inventory records for bundle")
                            sync_results['locations_removed'] += deleted_count
                        
                        # Process bundle inventory locations (will create fresh records)
                        bundle_result = _process_bundle_inventory(product, product_inventories, dry_run)
                        if bundle_result['updated']:
                            sync_results['product_updated'] = True
                            sync_results['locations_added'] += bundle_result['locations_added']
                else:
                    logger.info(f"  Would clean and recreate {existing_atum_inventories.count()} ATUM inventory records for bundle (dry run)")
        elif is_subscription:
            # Handle subscription products - they typically have minimal ATUM inventory
            logger.info(f"Processing subscription product: {product.name}")
            product_inventories = wc_api.get_product_inventories(product.woo_product_id)
            
            if not product_inventories:
                logger.info(f"No ATUM inventory found for subscription product: {product.name} (this is normal)")
                
                # Remove any existing ATUM inventory records for this subscription
                existing_atum_inventories = ProductInventory.objects.filter(
                    product=product,
                    variation__isnull=True,  # Only product-level inventory
                    location__atum_location_id__isnull=False,
                    notes__contains='ATUM'
                )
                
                if existing_atum_inventories.exists():
                    count = existing_atum_inventories.count()
                    if not dry_run:
                        with transaction.atomic():
                            deleted_count = existing_atum_inventories.delete()[0]
                            logger.info(f"  Removed {deleted_count} orphaned ATUM inventory records for subscription")
                            sync_results['locations_removed'] += deleted_count
                            sync_results['product_updated'] = True
                    else:
                        logger.info(f"  Would remove {count} orphaned ATUM inventory records for subscription (dry run)")
            else:
                logger.info(f"Found {len(product_inventories)} ATUM inventory locations for subscription: {product.name}")
                
                # Process subscription inventory locations (same as simple products)
                subscription_result = _process_product_inventory(product, product_inventories, dry_run)
                if subscription_result['updated']:
                    sync_results['product_updated'] = True
                    sync_results['locations_added'] += subscription_result['locations_added']
                    sync_results['locations_removed'] += subscription_result['locations_removed']
        else:
            # Handle simple products and other types
            product_inventories = wc_api.get_product_inventories(product.woo_product_id)
            
            if not product_inventories:
                logger.warning(f"No ATUM inventory found for product: {product.name}")
                
                # Remove any existing ATUM inventory records for this product
                existing_atum_inventories = ProductInventory.objects.filter(
                    product=product,
                    variation__isnull=True,  # Only product-level inventory
                    location__atum_location_id__isnull=False,
                    notes__contains='Synced from ATUM inventory'
                )
                
                if existing_atum_inventories.exists():
                    count = existing_atum_inventories.count()
                    if not dry_run:
                        with transaction.atomic():
                            deleted_count = existing_atum_inventories.delete()[0]
                            logger.info(f"  Removed {deleted_count} orphaned ATUM inventory records")
                            sync_results['locations_removed'] += deleted_count
                            sync_results['product_updated'] = True
                    else:
                        logger.info(f"  Would remove {count} orphaned ATUM inventory records (dry run)")
            else:
                logger.info(f"Found {len(product_inventories)} ATUM inventory locations for product: {product.name}")
                
                # Process product inventory locations
                product_result = _process_product_inventory(product, product_inventories, dry_run)
                if product_result['updated']:
                    sync_results['product_updated'] = True
                    sync_results['locations_added'] += product_result['locations_added']
                    sync_results['locations_removed'] += product_result['locations_removed']
        
        # Set overall success based on whether any changes were made
        sync_results['success'] = len(sync_results['errors']) == 0
        
        logger.info(f"✅ ATUM inventory sync completed for {product.name}: {sync_results}")
        return sync_results
        
    except Exception as e:
        error_msg = f"Error syncing ATUM inventory for product {product.name}: {str(e)}"
        logger.error(error_msg)
        return {
            'success': False,
            'product_updated': False,
            'variations_processed': 0,
            'variations_updated': 0,
            'locations_added': 0,
            'locations_removed': 0,
            'errors': [error_msg]
        }

def _process_product_inventory(product, product_inventories, dry_run):
    """
    Process ATUM inventory for a simple product (extracted from restore_atum_locations.py).
    """
    # Get existing inventory records for this product (excluding variations)
    existing_inventories = ProductInventory.objects.filter(
        product=product,
        variation__isnull=True,  # Only product-level inventory
        location__atum_location_id__isnull=False,
        notes__contains='Synced from ATUM inventory'
    )
    
    # Create a set of existing ATUM location IDs
    existing_location_ids = set(existing_inventories.values_list('location__atum_location_id', flat=True))
    
    # Create a set of ATUM location IDs from API data
    api_locations = []
    for inventory_data in product_inventories:
        location_name = inventory_data.get('name', '')
        if not location_name:
            continue
        
        # Use mapped name if available, otherwise use original
        mapped_location_name = LOCATION_NAME_MAPPINGS.get(location_name, location_name)
        
        try:
            # Find the corresponding InventoryLocation by name
            location = InventoryLocation.objects.get(
                name=mapped_location_name,
                atum_location_id__isnull=False
            )
            
            api_locations.append({
                'location': location,
                'inventory_id': inventory_data.get('id'),
                'quantity': inventory_data.get('meta_data', {}).get('stock_quantity', 0),
                'location_name': mapped_location_name
            })
            
        except InventoryLocation.DoesNotExist:
            logger.warning(f'ATUM location "{mapped_location_name}" not found in database')
    
    api_location_ids = set(loc['location'].atum_location_id for loc in api_locations)
    
    # Calculate differences
    to_add = api_location_ids - existing_location_ids
    to_remove = existing_location_ids - api_location_ids
    
    logger.info(f'  API locations: {len(api_location_ids)}, Existing: {len(existing_location_ids)}')
    logger.info(f'  Locations to add: {len(to_add)}, Locations to remove: {len(to_remove)}')
    
    locations_added = 0
    locations_removed = 0
    
    if not dry_run:
        with transaction.atomic():
            # Remove excess locations
            if to_remove:
                removed = existing_inventories.filter(
                    location__atum_location_id__in=to_remove
                ).delete()[0]
                locations_removed = removed
                logger.info(f'  Removed {removed} excess inventory records')
            
            # Add missing locations and update existing ones
            for loc_data in api_locations:
                # Handle quantity conversion with proper null handling for unmanaged stock
                raw_quantity = loc_data['quantity']
                
                # Check if this location is managed or unmanaged
                # If quantity is None or empty string, this indicates unmanaged stock
                if raw_quantity is None or raw_quantity == '' or raw_quantity == 'null':
                    quantity = None  # Unmanaged stock
                    logger.info(f'  Location {loc_data["location_name"]} is UNMANAGED (quantity: {raw_quantity})')
                else:
                    try:
                        quantity = int(raw_quantity)
                        logger.info(f'  Location {loc_data["location_name"]} is MANAGED (quantity: {quantity})')
                    except (ValueError, TypeError):
                        quantity = None  # Treat invalid values as unmanaged
                        logger.warning(f'  Invalid quantity value for {loc_data["location_name"]}: {raw_quantity} - treating as unmanaged')
                
                # Create or update inventory record for ALL locations (new and existing)
                inventory, created = ProductInventory.objects.get_or_create(
                    product=product,
                    location=loc_data['location'],
                    variation=None,  # Explicitly set to None for product-level inventory
                    defaults={
                        'quantity': quantity,
                        'notes': f'[ATUM] Synced from ATUM inventory',
                    }
                )
                
                # Always update quantity and notes for existing records
                if not created:
                    # Only update if quantity has changed to avoid unnecessary saves
                    # Handle comparison between None and 0 properly
                    needs_update = (
                        (inventory.quantity is None and quantity is not None) or
                        (inventory.quantity is not None and quantity is None) or
                        (inventory.quantity != quantity)
                    )
                    
                    if needs_update:
                        old_qty_display = 'unmanaged' if inventory.quantity is None else str(inventory.quantity)
                        new_qty_display = 'unmanaged' if quantity is None else str(quantity)
                        logger.info(f'  Updating quantity for {loc_data["location_name"]}: {old_qty_display} → {new_qty_display}')
                        inventory.quantity = quantity
                        inventory.notes = f'[ATUM] Synced from ATUM inventory'
                        inventory.save()
                
                # Count as added only if it's a new location
                if loc_data['location'].atum_location_id in to_add:
                    locations_added += 1
                    logger.info(f'  Added inventory record for location: {loc_data["location_name"]} (qty: {quantity})')
                elif not created and inventory.quantity == quantity:
                    logger.info(f'  Updated inventory record for location: {loc_data["location_name"]} (qty: {quantity})')
    
    # Track if any quantities were updated (locations that existed but had quantity changes)
    quantities_updated = len(api_locations) > 0 and len(to_add) < len(api_locations)
    
    return {
        'updated': bool(to_add or to_remove or quantities_updated),
        'locations_added': locations_added,
        'locations_removed': locations_removed
    }

def _process_variation_inventory(product, variation, variation_inventories, dry_run):
    """
    Process ATUM inventory for a specific product variation (extracted from restore_atum_locations.py).
    """
    # Get existing inventory records for this variation
    existing_inventories = ProductInventory.objects.filter(
        product=product,
        variation=variation,
        location__atum_location_id__isnull=False,
        notes__contains='Synced from ATUM inventory'
    )
    
    # Create a set of existing ATUM location IDs for this variation
    existing_location_ids = set(existing_inventories.values_list('location__atum_location_id', flat=True))
    
    # Create a set of ATUM location IDs from API data
    api_locations = []
    for inventory_data in variation_inventories:
        location_name = inventory_data.get('name', '')
        if not location_name:
            continue
        
        # Use mapped name if available, otherwise use original
        mapped_location_name = LOCATION_NAME_MAPPINGS.get(location_name, location_name)
        
        try:
            # Find the corresponding InventoryLocation by name
            location = InventoryLocation.objects.get(
                name=mapped_location_name,
                atum_location_id__isnull=False
            )
            
            # Extract ATUM inventory metadata for variation
            meta_data = inventory_data.get('meta_data', {})
            stock_quantity = meta_data.get('stock_quantity')
            manage_stock = meta_data.get('manage_stock', False)
            
            # Log the raw ATUM data for debugging variations
            logger.info(f'    Raw ATUM variation data for {mapped_location_name}: stock_quantity={stock_quantity}, manage_stock={manage_stock}')
            
            api_locations.append({
                'location': location,
                'inventory_id': inventory_data.get('id'),
                'quantity': stock_quantity,
                'manage_stock': manage_stock,
                'location_name': mapped_location_name
            })
            
        except InventoryLocation.DoesNotExist:
            logger.warning(f'    ATUM location "{mapped_location_name}" not found in database')
    
    api_location_ids = set(loc['location'].atum_location_id for loc in api_locations)
    
    # Calculate differences
    to_add = api_location_ids - existing_location_ids
    to_remove = existing_location_ids - api_location_ids
    
    logger.info(f'    Variation {variation.woo_variation_id}: API locations: {len(api_location_ids)}, Existing: {len(existing_location_ids)}')
    logger.info(f'    Locations to add: {len(to_add)}, Locations to remove: {len(to_remove)}')
    
    locations_added = 0
    locations_removed = 0
    
    if not dry_run:
        with transaction.atomic():
            # Remove excess locations for this variation
            if to_remove:
                removed = existing_inventories.filter(
                    location__atum_location_id__in=to_remove
                ).delete()[0]
                locations_removed = removed
                logger.info(f'    Removed {removed} excess inventory records for variation {variation.woo_variation_id}')
            
            # Add missing locations and update existing ones for this variation
            for loc_data in api_locations:
                # Handle quantity conversion with proper null handling for unmanaged stock
                raw_quantity = loc_data['quantity']
                manage_stock = loc_data.get('manage_stock', False)
                
                # Check if this location is managed or unmanaged
                # Use manage_stock flag as primary indicator, fallback to quantity analysis
                if not manage_stock:
                    quantity = None  # Unmanaged stock
                    logger.info(f'    Variation {variation.woo_variation_id} at {loc_data["location_name"]} is UNMANAGED (manage_stock=False)')
                elif raw_quantity is None or raw_quantity == '' or raw_quantity == 'null':
                    quantity = None  # Unmanaged stock (no quantity data)
                    logger.info(f'    Variation {variation.woo_variation_id} at {loc_data["location_name"]} is UNMANAGED (quantity: {raw_quantity})')
                else:
                    try:
                        quantity = int(raw_quantity)
                        logger.info(f'    Variation {variation.woo_variation_id} at {loc_data["location_name"]} is MANAGED (quantity: {quantity})')
                    except (ValueError, TypeError):
                        quantity = None  # Treat invalid values as unmanaged
                        logger.warning(f'    Invalid quantity value for variation {variation.woo_variation_id} at {loc_data["location_name"]}: {raw_quantity} - treating as unmanaged')
                
                # Create or update inventory record for ALL locations (new and existing)
                inventory, created = ProductInventory.objects.get_or_create(
                    product=product,
                    variation=variation,
                    location=loc_data['location'],
                    defaults={
                        'quantity': quantity,
                        'notes': f'[ATUM] Synced from ATUM inventory for variation {variation.woo_variation_id}',
                    }
                )
                
                # Always update quantity and notes for existing records
                if not created:
                    # Only update if quantity has changed to avoid unnecessary saves
                    # Handle comparison between None and 0 properly
                    needs_update = (
                        (inventory.quantity is None and quantity is not None) or
                        (inventory.quantity is not None and quantity is None) or
                        (inventory.quantity != quantity)
                    )
                    
                    if needs_update:
                        old_qty_display = 'unmanaged' if inventory.quantity is None else str(inventory.quantity)
                        new_qty_display = 'unmanaged' if quantity is None else str(quantity)
                        logger.info(f'    Updating quantity for variation {variation.woo_variation_id} at {loc_data["location_name"]}: {old_qty_display} → {new_qty_display}')
                        inventory.quantity = quantity
                        inventory.notes = f'[ATUM] Synced from ATUM inventory for variation {variation.woo_variation_id}'
                        inventory.save()
                
                # Count as added only if it's a new location
                if loc_data['location'].atum_location_id in to_add:
                    locations_added += 1
                    qty_display = 'unmanaged' if quantity is None else str(quantity)
                    logger.info(f'    Added inventory record for variation {variation.woo_variation_id} at location: {loc_data["location_name"]} (qty: {qty_display})')
                elif not created:
                    qty_display = 'unmanaged' if quantity is None else str(quantity)
                    logger.info(f'    Confirmed inventory record for variation {variation.woo_variation_id} at location: {loc_data["location_name"]} (qty: {qty_display})')
    
    # Track if any quantities were updated (locations that existed but had quantity changes)
    quantities_updated = len(api_locations) > 0 and len(to_add) < len(api_locations)
    
    return {
        'updated': bool(to_add or to_remove or quantities_updated),
        'locations_added': locations_added,
        'locations_removed': locations_removed
    }

def _process_bundle_inventory(product, bundle_inventories, dry_run):
    """
    Process ATUM inventory for a bundle product with clean-slate approach.
    This prevents duplicate inventory records by creating fresh records only.
    """
    logger.info(f"Processing bundle inventory for: {product.name}")
    
    # Process API locations for bundle
    api_locations = []
    for inventory_data in bundle_inventories:
        location_name = inventory_data.get('name', '')
        if not location_name:
            continue
        
        # Use mapped name if available, otherwise use original
        mapped_location_name = LOCATION_NAME_MAPPINGS.get(location_name, location_name)
        
        try:
            # Find the corresponding InventoryLocation by name
            location = InventoryLocation.objects.get(
                name=mapped_location_name,
                atum_location_id__isnull=False
            )
            
            api_locations.append({
                'location': location,
                'inventory_id': inventory_data.get('id'),
                'quantity': inventory_data.get('meta_data', {}).get('stock_quantity', 0),
                'location_name': mapped_location_name
            })
            
        except InventoryLocation.DoesNotExist:
            logger.warning(f'Bundle ATUM location "{mapped_location_name}" not found in database')
    
    logger.info(f'  Found {len(api_locations)} valid ATUM locations for bundle')
    
    locations_added = 0
    
    if not dry_run:
        with transaction.atomic():
            # Create fresh inventory records for all API locations
            for loc_data in api_locations:
                # Convert quantity to int if it's a string, preserve None for unmanaged bundle stock
                try:
                    if loc_data['quantity'] is not None:
                        quantity = int(loc_data['quantity'])
                    else:
                        quantity = None  # Keep as None for unmanaged bundle stock
                except (ValueError, TypeError):
                    quantity = None  # Default to unmanaged for bundle products
                
                # Create new inventory record (existing ones were already deleted)
                inventory = ProductInventory.objects.create(
                    product=product,
                    location=loc_data['location'],
                    variation=None,  # Bundle products don't have variations
                    quantity=quantity,
                    notes=f'[ATUM] Synced from ATUM inventory {loc_data["inventory_id"]} at {loc_data["location_name"]}'
                )
                
                locations_added += 1
                logger.info(f'  Created fresh inventory record for bundle at {loc_data["location_name"]} (qty: {quantity})')
    else:
        logger.info(f'  Would create {len(api_locations)} fresh inventory records for bundle (dry run)')
        locations_added = len(api_locations)
    
    return {
        'updated': len(api_locations) > 0,
        'locations_added': locations_added,
        'locations_removed': 0  # Removal was handled in the calling function
    }
