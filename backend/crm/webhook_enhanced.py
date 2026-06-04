"""
Enhanced webhook implementation with comprehensive logging
"""

import json
import logging
import time
import traceback
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db import transaction
from .models import Product, WebhookLog
from .utils.webhook_signature import verify_woocommerce_signature, check_webhook_duplicate

logger = logging.getLogger(__name__)

def get_client_ip(request):
    """Get the real client IP address"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@csrf_exempt
def woocommerce_product_webhook_enhanced(request):
    """
    Enhanced webhook endpoint for WooCommerce product updates with comprehensive logging.
    
    This endpoint receives notifications from WooCommerce when products are
    created, updated, or deleted, and syncs the changes to the local database.
    
    Expected webhook events:
    - product.created
    - product.updated  
    - product.deleted
    
    Returns:
        JSON response with sync status
    """
    start_time = time.time()
    webhook_log = None
    
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Only POST method allowed'
        }, status=405)
    
    # Verify WooCommerce webhook HMAC signature
    valid, error_response = verify_woocommerce_signature(request)
    if not valid:
        return error_response

    # Deduplicate webhook deliveries
    is_dup, dup_response = check_webhook_duplicate(request)
    if is_dup:
        return dup_response

    try:
        import os
        
        # Create initial webhook log entry
        webhook_log = WebhookLog.objects.create(
            webhook_type='product',
            source_ip=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            content_type=request.META.get('CONTENT_TYPE', ''),
            request_body=request.body.decode('utf-8', errors='ignore') if request.body else ''
        )
        
        logger.info(f"🔗 Received WooCommerce webhook request (Log ID: {webhook_log.id})")
        logger.info(f"Content-Type: {request.META.get('CONTENT_TYPE', 'Not set')}")
        logger.info(f"User-Agent: {request.META.get('HTTP_USER_AGENT', 'Not set')}")
        logger.info(f"Request body length: {len(request.body) if request.body else 0}")
        
        webhook_log.mark_processing()
        
        # Safety mechanism: ensure webhook status is always updated
        webhook_completed = False
        
        # Parse webhook data from request body
        webhook_data = None
        try:
            if request.body:
                body_str = request.body.decode('utf-8')
                if body_str.strip():  # Check if body is not just whitespace
                    # Check if this is JSON or form data
                    if body_str.startswith('{') and body_str.endswith('}'):
                        # This looks like JSON
                        webhook_data = json.loads(body_str)
                        logger.info(f"Successfully parsed JSON webhook data: {type(webhook_data)}")
                        logger.info(f"Webhook data keys: {list(webhook_data.keys()) if isinstance(webhook_data, dict) else 'Not a dict'}")
                    else:
                        # This looks like form data (e.g., webhook_id=20)
                        logger.info(f"Received form data: {body_str}")
                        # Parse form data
                        from urllib.parse import parse_qs
                        parsed_data = parse_qs(body_str)
                        
                        # Check if this is a test webhook
                        if 'webhook_id' in parsed_data:
                            logger.info(f"This is a WooCommerce test webhook with ID: {parsed_data['webhook_id']}")
                            webhook_log.webhook_type = 'test'
                            webhook_log.save()
                            
                            processing_time = int((time.time() - start_time) * 1000)
                            webhook_log.mark_success(
                                f'WooCommerce test webhook received successfully (ID: {parsed_data["webhook_id"][0]})',
                                processing_time
                            )
                            webhook_completed = True
                            
                            return JsonResponse({
                                'success': True,
                                'message': f'WooCommerce test webhook received successfully (ID: {parsed_data["webhook_id"][0]})',
                                'log_id': str(webhook_log.id)
                            }, status=200)
                        else:
                            # Try to extract product data from form data if available
                            webhook_data = {}
                            for key, value_list in parsed_data.items():
                                webhook_data[key] = value_list[0] if value_list else None
                            logger.info(f"Parsed form data into webhook_data: {webhook_data}")
                else:
                    logger.warning("Request body is empty or whitespace only")
                    processing_time = int((time.time() - start_time) * 1000)
                    webhook_log.mark_skipped('Empty body - likely test webhook')
                    
                    # For WooCommerce test webhooks, return success even with empty body
                    return JsonResponse({
                        'success': True,
                        'message': 'Webhook test received - empty body is OK for test webhooks',
                        'log_id': str(webhook_log.id)
                    }, status=200)
            else:
                logger.warning("No request body received")
                processing_time = int((time.time() - start_time) * 1000)
                webhook_log.mark_skipped('No body - likely test webhook')
                
                # For WooCommerce test webhooks, return success even with no body
                return JsonResponse({
                    'success': True,
                    'message': 'Webhook test received - no body is OK for test webhooks',
                    'log_id': str(webhook_log.id)
                }, status=200)
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from request body: {e}")
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.mark_failed(f'Invalid JSON data received: {str(e)}', processing_time)
            webhook_completed = True
            
            return JsonResponse({
                'success': False,
                'message': 'Invalid JSON data received',
                'log_id': str(webhook_log.id)
            }, status=400)
            
        except Exception as e:
            logger.error(f"Error parsing webhook data: {e}")
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.mark_failed(f'Error parsing webhook data: {str(e)}', processing_time)
            webhook_completed = True
            
            return JsonResponse({
                'success': False,
                'message': f'Error parsing webhook data: {str(e)}',
                'log_id': str(webhook_log.id)
            }, status=400)
        
        if not isinstance(webhook_data, dict):
            logger.error(f"Webhook data is not a dictionary: {type(webhook_data)}")
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.mark_failed(f'Expected JSON object, got {type(webhook_data).__name__}', processing_time)
            webhook_completed = True
            
            return JsonResponse({
                'success': False,
                'message': f'Expected JSON object, got {type(webhook_data).__name__}',
                'log_id': str(webhook_log.id)
            }, status=400)
        
        woo_product_id = webhook_data.get('id')
        if not woo_product_id:
            logger.warning(f"No product ID found in webhook data. Available keys: {list(webhook_data.keys())}")
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.mark_skipped('No product ID - likely test webhook')
            
            # This might be a test webhook from WooCommerce - return success
            return JsonResponse({
                'success': True,
                'message': 'Webhook received but no product ID - this may be a test webhook',
                'available_keys': list(webhook_data.keys()),
                'log_id': str(webhook_log.id)
            }, status=200)
        
        # Update webhook log with product ID
        webhook_log.woo_product_id = woo_product_id
        webhook_log.save()
        
        logger.info(f"Processing webhook for WooCommerce product ID: {woo_product_id}")
        
        # Check if this is a bundle product
        product_type = webhook_data.get('type', '')
        is_bundle = product_type == 'bundle'
        webhook_log.is_bundle_product = is_bundle
        webhook_log.save()
        
        # Check for product deletion event
        if webhook_data.get('status') == 'trash' or webhook_data.get('date_modified_gmt') and not webhook_data.get('name'):
            logger.info(f"Product {woo_product_id} appears to be deleted (status: {webhook_data.get('status')})")
            
            try:
                local_product = Product.objects.get(woo_product_id=woo_product_id)
                webhook_log.local_product_id = local_product.id
                webhook_log.action_taken = 'delete'
                
                # Remove related data first
                from .models import ProductVariation, ProductBundle, ProductSubscription
                ProductVariation.objects.filter(product=local_product).delete()
                ProductBundle.objects.filter(product=local_product).delete()
                ProductSubscription.objects.filter(product=local_product).delete()
                
                # Remove the product
                product_name = local_product.name
                local_product.delete()
                
                logger.info(f"Successfully deleted product {product_name} (WooCommerce ID: {woo_product_id}) from local database")
                
                processing_time = int((time.time() - start_time) * 1000)
                webhook_log.mark_success(f'Product {woo_product_id} deleted successfully', processing_time)
                webhook_completed = True
                
                return JsonResponse({
                    'success': True,
                    'message': f'Product {woo_product_id} deleted successfully',
                    'action': 'product_deleted',
                    'product_name': product_name,
                    'log_id': str(webhook_log.id)
                }, status=200)
                
            except Product.DoesNotExist:
                logger.info(f"Product {woo_product_id} was already deleted or doesn't exist locally")
                webhook_log.action_taken = 'delete_not_found'
                
                processing_time = int((time.time() - start_time) * 1000)
                webhook_log.mark_success(f'Product {woo_product_id} was already deleted or doesn\'t exist locally', processing_time)
                webhook_completed = True
                
                return JsonResponse({
                    'success': True,
                    'message': f'Product {woo_product_id} was already deleted or doesn\'t exist locally',
                    'action': 'product_already_deleted',
                    'log_id': str(webhook_log.id)
                }, status=200)
                
            except Exception as delete_error:
                logger.error(f"Error deleting product {woo_product_id}: {str(delete_error)}")
                processing_time = int((time.time() - start_time) * 1000)
                webhook_log.mark_failed(f'Failed to delete product {woo_product_id}: {str(delete_error)}', processing_time)
                webhook_completed = True
                
                return JsonResponse({
                    'success': False,
                    'message': f'Failed to delete product {woo_product_id}: {str(delete_error)}',
                    'log_id': str(webhook_log.id)
                }, status=500)

        # Check if product exists in local database for update/creation
        try:
            # Use transaction management to ensure database consistency
            with transaction.atomic():
                local_product = Product.objects.get(woo_product_id=woo_product_id)
                webhook_log.local_product_id = local_product.id
                webhook_log.action_taken = 'update'
                
                # Update local product with WooCommerce data
                updated_fields = []
                if 'name' in webhook_data and webhook_data['name']:
                    local_product.name = webhook_data['name']
                    updated_fields.append('name')
                if 'description' in webhook_data:
                    local_product.description = webhook_data['description'] or ''
                    updated_fields.append('description')
                if 'regular_price' in webhook_data:
                    # Handle empty or invalid price values
                    regular_price = webhook_data['regular_price']
                    if regular_price and str(regular_price).strip() and str(regular_price) != '':
                        try:
                            local_product.price = regular_price
                            local_product.regular_price = regular_price
                            updated_fields.append('price')
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid regular_price value: {regular_price}, error: {e}")
                if 'sale_price' in webhook_data:
                    # Handle empty or invalid sale price values
                    sale_price = webhook_data['sale_price']
                    if sale_price and str(sale_price).strip() and str(sale_price) != '':
                        try:
                            local_product.sale_price = sale_price
                            updated_fields.append('sale_price')
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid sale_price value: {sale_price}, error: {e}")
                    else:
                        # Set to 0 or None for empty sale price
                        local_product.sale_price = 0
                        updated_fields.append('sale_price')
                if 'status' in webhook_data and webhook_data['status']:
                    local_product.status = webhook_data['status']
                    updated_fields.append('status')
                if 'stock_status' in webhook_data and webhook_data['stock_status']:
                    local_product.stock_status = webhook_data['stock_status']
                    updated_fields.append('stock_status')
                if 'stock_quantity' in webhook_data:
                    # Handle empty or invalid stock quantity
                    stock_quantity = webhook_data['stock_quantity']
                    if stock_quantity is not None and str(stock_quantity).strip():
                        try:
                            local_product.stock_quantity = int(stock_quantity) if stock_quantity else None
                            updated_fields.append('stock_quantity')
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid stock_quantity value: {stock_quantity}, error: {e}")
                    else:
                        local_product.stock_quantity = None
                        updated_fields.append('stock_quantity')
                
                # Update categories if provided
                if 'categories' in webhook_data and isinstance(webhook_data['categories'], list):
                    categories = [cat.get('name', '') for cat in webhook_data['categories'] if isinstance(cat, dict)]
                    
                    # Check if product was recently updated (within last 2 minutes) to avoid webhook race condition
                    from datetime import timedelta
                    
                    recent_update_threshold = timezone.now() - timedelta(minutes=2)
                    is_recently_updated = local_product.updated_at and local_product.updated_at > recent_update_threshold
                    
                    # Only update categories if not recently updated, or if the webhook categories are different from "Uncategorized"
                    should_update_categories = True
                    if is_recently_updated:
                        # If recently updated and webhook is trying to set to "Uncategorized", skip it
                        if len(categories) == 1 and categories[0] == 'Uncategorized' and local_product.categories != ['Uncategorized']:
                            logger.info(f"Skipping webhook category update to 'Uncategorized' for recently updated product {local_product.id}")
                            should_update_categories = False
                    
                    if should_update_categories:
                        local_product.categories = categories
                        updated_fields.append('categories')
                        logger.info(f"Updated categories via webhook: {categories}")
                    else:
                        logger.info(f"Preserved existing categories: {local_product.categories}")
                
                # Update images if provided
                if 'images' in webhook_data and isinstance(webhook_data['images'], list):
                    images = [img.get('src', '') for img in webhook_data['images'] if isinstance(img, dict)]
                    local_product.images = images
                    updated_fields.append('images')
                
                # Save the updated fields to webhook log
                webhook_log.updated_fields = updated_fields
                webhook_log.save()
                
                # Log product data before saving
                logger.info(f"About to save product {local_product.id} with data:")
                logger.info(f"  Name: {local_product.name}")
                logger.info(f"  Price: {local_product.price}")
                logger.info(f"  Status: {local_product.status}")
                
                local_product.save()
            
            # Handle bundle data sync for bundle products
            bundle_sync_status = False
            if local_product.product_type == 'bundle':
                logger.info(f"🔧 Product {local_product.id} is a bundle - syncing bundle data from WooCommerce")
                webhook_log.bundle_sync_attempted = True
                webhook_log.save()
                
                try:
                    # Use transaction to ensure bundle sync doesn't leave webhook in processing state
                    with transaction.atomic():
                        # Try to sync bundle data directly from webhook payload first
                        from .views_webhooks import sync_bundle_data_from_webhook_payload
                        
                        # First try to sync using the webhook payload
                        bundle_sync_status = sync_bundle_data_from_webhook_payload(local_product, webhook_data)
                        
                        # If direct sync failed, fall back to API sync
                        if not bundle_sync_status:
                            logger.warning(f"Direct bundle sync from webhook payload failed, falling back to API sync")
                            from .views import sync_bundle_data_from_webhook
                            bundle_sync_status = sync_bundle_data_from_webhook(local_product, woo_product_id)
                except Exception as bundle_error:
                    logger.error(f"Error syncing bundle data: {str(bundle_error)}")
                    logger.error(traceback.format_exc())
                    bundle_sync_status = False
                
                # Always update the webhook log status, even if there was an error
                webhook_log.bundle_sync_success = bundle_sync_status
                webhook_log.save()
                
                # Check if default series were updated
                if bundle_sync_status and 'bundled_items' in webhook_data:
                    bundled_items = webhook_data.get('bundled_items', [])
                    webhook_log.bundle_items_count = len(bundled_items)
                    
                    # Check if any items have default variation attributes
                    has_default_series = any(
                        item.get('default_variation_attributes') 
                        for item in bundled_items
                    )
                    webhook_log.default_series_updated = has_default_series
                
                webhook_log.save()
                
                if bundle_sync_status:
                    updated_fields.append('bundle_data')
            
            logger.info(f"Successfully updated local product {local_product.id} from WooCommerce webhook")
            logger.info(f"Updated fields: {updated_fields}")
            
            # Prepare response with bundle sync status
            response_data = {
                'success': True,
                'message': f'Product {woo_product_id} updated successfully',
                'product_id': str(local_product.id),
                'updated_fields': updated_fields,
                'log_id': str(webhook_log.id)
            }
            
            # Add bundle sync information if applicable
            if local_product.product_type == 'bundle':
                response_data['bundle_sync'] = {
                    'attempted': True,
                    'success': bundle_sync_status,
                    'message': 'Bundle data synced from WooCommerce' if bundle_sync_status else 'Bundle data sync failed'
                }
            
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.mark_success(f'Product {woo_product_id} updated successfully', processing_time)
            webhook_completed = True
            
            return JsonResponse(response_data, status=200)
            
        except Product.DoesNotExist:
            # Product doesn't exist locally - this could be a new product creation
            logger.info(f"Webhook received for product {woo_product_id} that doesn't exist locally - skipping creation")
            webhook_log.action_taken = 'create_skipped'
            
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.mark_skipped(f'Product {woo_product_id} does not exist locally - webhook creation skipped')
            webhook_completed = True
            
            return JsonResponse({
                'success': True,
                'message': f'Product {woo_product_id} does not exist locally - webhook creation skipped',
                'action': 'create_skipped',
                'log_id': str(webhook_log.id)
            }, status=200)
            
        except Exception as update_error:
            logger.error(f"Error updating product {woo_product_id}: {str(update_error)}")
            processing_time = int((time.time() - start_time) * 1000)
            webhook_log.mark_failed(f'Error updating product {woo_product_id}: {str(update_error)}', processing_time)
            webhook_completed = True
            
            return JsonResponse({
                'success': False,
                'message': f'Error updating product {woo_product_id}: {str(update_error)}',
                'log_id': str(webhook_log.id)
            }, status=500)
        
    except Exception as e:
        logger.error(f"Unexpected error in webhook processing: {str(e)}")
        logger.error(traceback.format_exc())
        
        if webhook_log:
            # Make sure we update the webhook log status even if there's an error
            try:
                processing_time = int((time.time() - start_time) * 1000)
                webhook_log.mark_failed(f'Unexpected error: {str(e)}', processing_time)
                webhook_completed = True
            except Exception as log_error:
                logger.error(f"Error updating webhook log: {str(log_error)}")
            
            return JsonResponse({
                'success': False,
                'message': f'Webhook processing failed: {str(e)}',
                'log_id': str(webhook_log.id)
            }, status=500)
        else:
            return JsonResponse({
                'success': False,
                'message': f'Webhook processing failed: {str(e)}'
            }, status=500)
    
    finally:
        # Safety net: ensure webhook status is never left in 'processing'
        if webhook_log and not webhook_completed:
            try:
                processing_time = int((time.time() - start_time) * 1000)
                webhook_log.mark_failed(
                    'Webhook processing incomplete - marked as failed by safety mechanism', 
                    processing_time
                )
                logger.warning(f"Safety mechanism triggered: marked webhook {webhook_log.id} as failed")
            except Exception as safety_error:
                logger.error(f"Error in safety mechanism: {str(safety_error)}")
