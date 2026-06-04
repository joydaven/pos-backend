"""
WooCommerce Webhook handlers for real-time data synchronization
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .models import Product, InventoryLocation, ProductInventory, Contact, ProductBundle
from .woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def atum_inventory_webhook(request):
    """
    Webhook endpoint for ATUM inventory updates from WooCommerce.
    
    This endpoint receives standard WooCommerce 'product.updated' events
    and checks for ATUM inventory data to sync multi-location inventory.
    
    Expected webhook events:
    - product.updated (when ATUM inventory changes)
    """
    try:
        logger.info("Received ATUM inventory webhook request")
        
        # Parse webhook data
        webhook_data = None
        if request.body:
            body_str = request.body.decode('utf-8')
            if body_str.strip():
                try:
                    webhook_data = json.loads(body_str)
                    logger.info(f"ATUM webhook data: {webhook_data}")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in ATUM webhook: {body_str}")
                    return JsonResponse({
                        'success': False,
                        'message': 'Invalid JSON format'
                    }, status=400)
        
        if not webhook_data:
            return JsonResponse({
                'success': True,
                'message': 'ATUM webhook test received successfully'
            }, status=200)
        
        # Extract product data
        product_id = webhook_data.get('id')
        if not product_id:
            logger.error("No product ID found in ATUM webhook data")
            return JsonResponse({
                'success': False,
                'message': 'Product ID required'
            }, status=400)
        
        # Find local product
        try:
            local_product = Product.objects.get(woo_product_id=product_id)
        except Product.DoesNotExist:
            logger.warning(f"Product with WooCommerce ID {product_id} not found locally")
            return JsonResponse({
                'success': True,
                'message': f'Product {product_id} not found locally - skipping'
            }, status=200)
        
        # Trigger ATUM inventory sync for this specific product
        updated_locations = []
        
        try:
            from django.core.management import call_command
            from io import StringIO
            import sys
            
            # Capture command output
            old_stdout = sys.stdout
            sys.stdout = captured_output = StringIO()
            
            try:
                # Call ATUM sync for this specific product
                # This will fetch fresh ATUM data from WooCommerce API
                call_command('sync_atum_inventory', product_id=product_id, verbose=False)
                output = captured_output.getvalue()
                
                # Parse output to get updated locations info
                if "Created new inventory record" in output or "Updated inventory record" in output:
                    logger.info(f"ATUM inventory sync completed for product {product_id}")
                    
                    # Get current inventory records for this product
                    inventories = ProductInventory.objects.filter(
                        product=local_product,
                        location__atum_location_id__isnull=False
                    ).select_related('location')
                    
                    for inv in inventories:
                        updated_locations.append({
                            'location': inv.location.name,
                            'quantity': inv.quantity,
                            'available': inv.is_available,
                            'action': 'synced'
                        })
                
            except Exception as cmd_error:
                logger.error(f"ATUM sync command failed: {str(cmd_error)}")
                # Fallback to basic stock update
                stock_quantity = webhook_data.get('stock_quantity', 0)
                stock_status = webhook_data.get('stock_status', 'outofstock')
                
                if stock_quantity is not None or stock_status:
                    # Update product stock fields
                    local_product.stock_quantity = stock_quantity
                    local_product.stock_status = stock_status
                    local_product.save()
                    
                    updated_locations.append({
                        'location': 'Default',
                        'quantity': stock_quantity,
                        'available': stock_status == 'instock',
                        'action': 'fallback_updated'
                    })
                
            finally:
                sys.stdout = old_stdout
        
        except Exception as e:
            logger.error(f"Error in ATUM sync process: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f'ATUM sync failed: {str(e)}'
            }, status=500)
        
        return JsonResponse({
            'success': True,
            'message': f'ATUM inventory processed for product {local_product.name}',
            'product_id': product_id,
            'product_name': local_product.name,
            'updated_locations': updated_locations
        }, status=200)
        
    except Exception as e:
        logger.error(f"Error in ATUM inventory webhook: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f'Webhook processing failed: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def inventory_location_webhook(request):
    """
    Webhook endpoint for inventory location updates from WooCommerce.
    
    This endpoint receives notifications when inventory locations are
    created, updated, or deleted in WooCommerce.
    """
    try:
        logger.info("Received inventory location webhook request")
        
        # Parse webhook data
        webhook_data = None
        if request.body:
            body_str = request.body.decode('utf-8')
            if body_str.strip():
                try:
                    webhook_data = json.loads(body_str)
                    logger.info(f"Location webhook data: {webhook_data}")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in location webhook: {body_str}")
                    return JsonResponse({
                        'success': False,
                        'message': 'Invalid JSON format'
                    }, status=400)
        
        if not webhook_data:
            return JsonResponse({
                'success': True,
                'message': 'Location webhook test received successfully'
            }, status=200)
        
        # Extract location data
        location_id = webhook_data.get('id')
        location_name = webhook_data.get('name')
        location_status = webhook_data.get('status', 'active')
        
        if not location_id or not location_name:
            logger.error("Missing location ID or name in webhook data")
            return JsonResponse({
                'success': False,
                'message': 'Location ID and name required'
            }, status=400)
        
        # Update or create inventory location
        location, created = InventoryLocation.objects.update_or_create(
            atum_location_id=str(location_id),
            defaults={
                'name': location_name,
                'code': location_name.upper().replace(' ', '_'),
                'description': webhook_data.get('description', f'Synced from WooCommerce ATUM location'),
                'is_active': location_status == 'active',
                'address': webhook_data.get('address', ''),
                'phone': webhook_data.get('phone', ''),
                'email': webhook_data.get('email', '')
            }
        )
        
        action = 'Created' if created else 'Updated'
        logger.info(f"{action} inventory location: {location_name} (ID: {location_id})")
        
        return JsonResponse({
            'success': True,
            'message': f'{action} inventory location: {location_name}',
            'location_id': location_id,
            'location_name': location_name,
            'action': action.lower()
        }, status=200)
        
    except Exception as e:
        logger.error(f"Error in inventory location webhook: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f'Webhook processing failed: {str(e)}'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def product_stock_webhook(request):
    """
    Webhook endpoint for general product stock updates from WooCommerce.
    
    This endpoint handles stock changes for products that may not use ATUM.
    """
    try:
        logger.info("Received product stock webhook request")
        
        # Parse webhook data
        webhook_data = None
        if request.body:
            body_str = request.body.decode('utf-8')
            if body_str.strip():
                try:
                    webhook_data = json.loads(body_str)
                    logger.info(f"Stock webhook data: {webhook_data}")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in stock webhook: {body_str}")
                    return JsonResponse({
                        'success': False,
                        'message': 'Invalid JSON format'
                    }, status=400)
        
        if not webhook_data:
            return JsonResponse({
                'success': True,
                'message': 'Stock webhook test received successfully'
            }, status=200)
        
        # Extract product data
        product_id = webhook_data.get('id')
        if not product_id:
            logger.error("No product ID found in stock webhook data")
            return JsonResponse({
                'success': False,
                'message': 'Product ID required'
            }, status=400)
        
        # Find local product
        try:
            local_product = Product.objects.get(woo_product_id=product_id)
        except Product.DoesNotExist:
            logger.warning(f"Product with WooCommerce ID {product_id} not found locally")
            return JsonResponse({
                'success': True,
                'message': f'Product {product_id} not found locally - skipping'
            }, status=200)
        
        # Update product stock fields
        updated_fields = []
        
        if 'stock_quantity' in webhook_data:
            stock_quantity = webhook_data['stock_quantity']
            if stock_quantity is not None:
                local_product.stock_quantity = int(stock_quantity) if stock_quantity else 0
                updated_fields.append('stock_quantity')
        
        if 'stock_status' in webhook_data:
            local_product.stock_status = webhook_data['stock_status']
            updated_fields.append('stock_status')
        
        if 'manage_stock' in webhook_data:
            local_product.manage_stock = webhook_data['manage_stock']
            updated_fields.append('manage_stock')
        
        if updated_fields:
            local_product.save()
            logger.info(f"Updated product stock: {local_product.name} - {updated_fields}")
        
        return JsonResponse({
            'success': True,
            'message': f'Stock updated for product {local_product.name}',
            'product_id': product_id,
            'product_name': local_product.name,
            'updated_fields': updated_fields
        }, status=200)
        
    except Exception as e:
        logger.error(f"Error in product stock webhook: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f'Webhook processing failed: {str(e)}'
        }, status=500)

def sync_bundle_data_from_webhook_payload(product, webhook_data):
    """
    Sync bundle data directly from the webhook payload instead of fetching from WooCommerce API.
    This function updates the ProductBundle table with bundled_items and default_variation_attributes
    from the webhook payload.
    
    Args:
        product: The Product instance
        webhook_data: The webhook payload containing bundle data
        
    Returns:
        bool: True if bundle data was successfully synced, False otherwise
    """
    try:
        if not isinstance(webhook_data, dict):
            logger.error(f"Invalid webhook data format for bundle sync: {type(webhook_data)}")
            return False
            
        # Check if this is actually a bundle product
        if webhook_data.get('type') != 'bundle' and product.product_type != 'bundle':
            logger.info(f"Product {product.id} is not a bundle type")
            return False
            
        # Extract bundle-specific data
        bundled_items = webhook_data.get('bundled_items', [])
        
        if not bundled_items:
            logger.warning(f"No bundled_items found in webhook data for bundle product {product.id}")
            # Still update with empty bundle data to keep it current
        
        logger.info(f"Found {len(bundled_items)} bundled items in webhook data for product {product.id}")
        
        # Process and validate bundle items
        processed_bundle_items = []
        default_series_by_item = {}
        
        for item in bundled_items:
            item_id = item.get('product_id')
            if not item_id:
                logger.warning(f"Bundle item missing product_id in bundle {product.id}")
                continue
                
            # Check if bundled product exists in our database
            try:
                bundled_product = Product.objects.get(woo_product_id=item_id)
                item['local_product_id'] = str(bundled_product.id)
                
                # Extract and store default series selections
                default_attrs = item.get('default_variation_attributes', {})
                if default_attrs:
                    # Store default series selections for this item
                    default_series_by_item[str(item_id)] = default_attrs
                    logger.info(f"Default series for item {item_id}: {default_attrs}")
                    
                processed_bundle_items.append(item)
                
            except Product.DoesNotExist:
                logger.warning(f"Bundled product {item_id} not found in local database - skipping from bundle")
                # Don't include products that don't exist in our database
                # This prevents 404 errors when frontend tries to fetch these products
        
        # Add processed data to the webhook data
        webhook_data['processed_bundle_items'] = processed_bundle_items
        webhook_data['default_series_by_item'] = default_series_by_item
        
        # Also update the original bundled_items with fresh default_variation_attributes
        for original_item in webhook_data.get('bundled_items', []):
            item_id = original_item.get('product_id')
            if item_id and str(item_id) in default_series_by_item:
                original_item['default_variation_attributes'] = default_series_by_item[str(item_id)]
                logger.info(f"🔄 Updated default series in original webhook item {item_id}: {default_series_by_item[str(item_id)]}")
        
        logger.info(f"✅ Updated webhook bundle with fresh default series data")
        
        # Get or create ProductBundle record
        product_bundle, created = ProductBundle.objects.get_or_create(
            product_id=product.id,
            defaults={
                'woo_data': webhook_data,
                'created_at': timezone.now(),
                'updated_at': timezone.now()
            }
        )
        
        if not created:
            # Update existing ProductBundle with fresh data
            product_bundle.woo_data = webhook_data
            product_bundle.updated_at = timezone.now()
            product_bundle.save()
            logger.info(f"✅ Updated existing ProductBundle record for product {product.id}")
        else:
            logger.info(f"✅ Created new ProductBundle record for product {product.id}")
        
        # Log bundle data details for debugging
        if processed_bundle_items:
            logger.info(f"Bundle items processed and synced:")
            for item in processed_bundle_items[:3]:  # Log first 3 items to avoid spam
                item_id = item.get('product_id', 'Unknown')
                item_name = item.get('title', 'Unknown')
                local_id = item.get('local_product_id', 'Not found')
                default_attrs = item.get('default_variation_attributes', {})
                logger.info(f"  - {item_name} (WooID: {item_id}, LocalID: {local_id}) - Default attrs: {default_attrs}")
        
        # Add a cache invalidation flag to indicate bundle data has changed
        product_bundle.woo_data['last_updated'] = timezone.now().isoformat()
        product_bundle.save(update_fields=['woo_data'])
        
        return True
        
    except Exception as e:
        logger.error(f"Error syncing bundle data from webhook payload for product {product.id}: {str(e)}")
        return False
