import logging
import json
import os
import pymysql
from contextlib import contextmanager
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from datetime import datetime
from .woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)


@contextmanager
def get_wordpress_database_connection():
    """Direct connection to WordPress/WooCommerce Cloud SQL database"""
    connection = None
    
    try:
        from core.secrets import get_woo_db_config
        woo_cfg = get_woo_db_config()
        
        if not all([woo_cfg['db_host'], woo_cfg['db_name'], woo_cfg['db_username'], woo_cfg['db_password']]):
            raise ValueError("Missing required WordPress database connection credentials")
        
        logger.info(f"Connecting to WordPress database: {woo_cfg['db_host']}:{woo_cfg['db_port']}")
        
        connection = pymysql.connect(
            host=woo_cfg['db_host'],
            port=int(woo_cfg['db_port']),
            user=woo_cfg['db_username'],
            password=woo_cfg['db_password'],
            database=woo_cfg['db_name'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10
        )
        logger.info("WordPress database connection established")
        
        yield connection
        
    except Exception as e:
        logger.error(f"WordPress database connection error: {str(e)}")
        raise
    finally:
        if connection:
            connection.close()
            logger.info("WordPress database connection closed")


def get_yith_points_history(woo_customer_id):
    """
    Query YITH Points and Rewards history directly from WordPress database.
    
    Args:
        woo_customer_id: WooCommerce customer ID (same as WordPress user ID)
        
    Returns:
        List of points history records with date, amount, order_id, action, description
    """
    try:
        with get_wordpress_database_connection() as connection:
            with connection.cursor() as cursor:
                # Query wp_yith_ywpar_points_log table
                # WooCommerce customer ID = WordPress user ID in most cases
                query = """
                SELECT 
                    id,
                    user_id,
                    action,
                    order_id,
                    amount,
                    date_earning,
                    cancelled,
                    description,
                    info
                FROM wp_yith_ywpar_points_log
                WHERE user_id = %s
                ORDER BY date_earning DESC
                LIMIT 100
                """
                
                cursor.execute(query, (woo_customer_id,))
                results = cursor.fetchall()
                
                logger.info(f"Retrieved {len(results)} YITH points history records for customer {woo_customer_id}")
                
                # Format the results
                history = []
                
                # Pre-fetch WooCommerce order IDs for admin_action entries with POS order numbers in description
                import re
                pos_order_numbers = set()
                for row in results:
                    if not row['order_id'] and row['description']:
                        match = re.search(r'ORD-\d+', row['description'])
                        if match:
                            pos_order_numbers.add(match.group())
                
                # Batch lookup POS order → WooCommerce order ID mapping
                pos_to_woo = {}
                if pos_order_numbers:
                    try:
                        from crm.models import POSOrder
                        pos_orders = POSOrder.objects.filter(
                            order_number__in=pos_order_numbers,
                            woocommerce_order_id__isnull=False
                        ).values_list('order_number', 'woocommerce_order_id')
                        pos_to_woo = {on: wid for on, wid in pos_orders}
                    except Exception as e:
                        logger.warning(f"Could not look up WooCommerce IDs for POS orders: {e}")
                
                for row in results:
                    order_id = row['order_id']
                    
                    # For admin actions without order_id, try to resolve from POS order number in description
                    if not order_id and row['description']:
                        match = re.search(r'ORD-\d+', row['description'])
                        if match:
                            order_id = pos_to_woo.get(match.group())
                    
                    history.append({
                        'id': row['id'],
                        'user_id': row['user_id'],
                        'action': row['action'],
                        'order_id': order_id,
                        'amount': int(row['amount']) if row['amount'] else 0,
                        'date': row['date_earning'].isoformat() if row['date_earning'] else None,
                        'cancelled': bool(row['cancelled']),
                        'description': row['description'] or '',
                        'info': row['info'] or '',
                        'reason': format_action_reason(row['action'])
                    })
                
                return history
                
    except Exception as e:
        logger.error(f"Error retrieving YITH points history: {str(e)}")
        raise


def format_action_reason(action):
    """
    Format YITH action code into human-readable reason.
    
    Args:
        action: Action code from database (e.g., 'order_completed', 'redeemed_points')
        
    Returns:
        Human-readable reason string
    """
    action_map = {
        'order_completed': 'Order Completed',
        'redeemed_points': 'Points Redeemed',
        'admin_action': 'Admin Action',
        'expired_points': 'Points Expired',
        'cancelled_order': 'Order Cancelled',
        'refunded_order': 'Order Refunded',
        'review_points': 'Review Points',
        'birthday_points': 'Birthday Points',
        'registration_points': 'Registration Points',
        'extra_points': 'Extra Points',
    }
    
    return action_map.get(action, action.replace('_', ' ').title())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_woocommerce_customer_points(request, customer_id):
    """
    Get customer points directly from WooCommerce API.
    
    This endpoint fetches points data for a customer directly from WooCommerce,
    bypassing any local database caching or processing.
    
    Args:
        request: The HTTP request
        customer_id: The WooCommerce customer ID
        
    Returns:
        Response with points data: {'points': points_value, 'points_value': monetary_value}
    """
    logger.info(f"Fetching WooCommerce points for customer ID: {customer_id}")
    
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get customer points directly from WooCommerce
        points_data = wc_api.get_customer_points(customer_id)
        
        if points_data is None:
            logger.warning(f"Could not retrieve points data for WooCommerce customer ID: {customer_id}")
            return Response(
                {"error": "Could not retrieve points data", "points": 0, "points_value": 0},
                status=status.HTTP_404_NOT_FOUND
            )
        
        logger.info(f"Successfully retrieved WooCommerce points for customer ID {customer_id}: {points_data}")
        return Response(points_data)
        
    except Exception as e:
        logger.error(f"Error retrieving WooCommerce customer points: {str(e)}")
        return Response(
            {"error": f"Error retrieving points: {str(e)}", "points": 0, "points_value": 0},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def redeem_customer_points(request, customer_id):
    """
    Redeem customer points and apply discount to order.
    
    This endpoint processes points redemption by:
    1. Validating the points to redeem against available points
    2. Calculating the discount amount (points * 1.00)
    3. Calling YITH plugin API to deduct points from customer account
    4. Returning the discount amount to apply to the order
    
    Args:
        request: The HTTP request with JSON body containing:
            - points_to_redeem: Number of points to redeem
            - order_total: Total order amount for validation
        customer_id: The WooCommerce customer ID
        
    Returns:
        Response with redemption data: {
            'success': True/False,
            'points_redeemed': number,
            'discount_amount': decimal,
            'remaining_points': number,
            'message': string
        }
    """
    logger.info(f"Processing points redemption for customer ID: {customer_id}")
    
    try:
        # Parse request data - use request.data with DRF @api_view decorator
        data = request.data or {}
        points_to_redeem = int(data.get('points_to_redeem', 0))
        order_total = float(data.get('order_total', 0))
        
        if points_to_redeem <= 0:
            return Response(
                {"success": False, "error": "Points to redeem must be greater than 0"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get current customer points
        points_data = wc_api.get_customer_points(customer_id)
        
        if points_data is None:
            logger.warning(f"Could not retrieve points data for customer ID: {customer_id}")
            return Response(
                {"success": False, "error": "Could not retrieve customer points data"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        available_points = points_data.get('points_to_redeem', 0)
        
        # Validate points availability
        if points_to_redeem > available_points:
            return Response(
                {
                    "success": False, 
                    "error": f"Insufficient points. Available: {available_points}, Requested: {points_to_redeem}"
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Calculate discount amount (1 point = $1.00)
        discount_amount = points_to_redeem * 1.00
        
        # Validate discount doesn't exceed order total
        if discount_amount > order_total:
            max_points = int(order_total)  # 1 point = $1, so max points = order total
            return Response(
                {
                    "success": False, 
                    "error": f"Discount amount (${discount_amount:.2f}) exceeds order total (${order_total:.2f}). Maximum points: {max_points}"
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Process points redemption through YITH plugin API
        redemption_result = wc_api.redeem_customer_points(customer_id, points_to_redeem)
        
        if not redemption_result.get('success', False):
            logger.error(f"Points redemption failed for customer {customer_id}: {redemption_result.get('error', 'Unknown error')}")
            return Response(
                {
                    "success": False, 
                    "error": f"Points redemption failed: {redemption_result.get('error', 'Unknown error')}"
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Calculate remaining points
        remaining_points = available_points - points_to_redeem
        
        logger.info(f"Successfully redeemed {points_to_redeem} points for customer {customer_id}. Discount: ${discount_amount:.2f}")
        
        return Response({
            "success": True,
            "points_redeemed": points_to_redeem,
            "discount_amount": discount_amount,
            "remaining_points": remaining_points,
            "message": f"Successfully redeemed {points_to_redeem} points for ${discount_amount:.2f} discount"
        })
        
    except json.JSONDecodeError:
        return Response(
            {"success": False, "error": "Invalid JSON in request body"},
            status=status.HTTP_400_BAD_REQUEST
        )
    except ValueError as e:
        return Response(
            {"success": False, "error": f"Invalid data format: {str(e)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.error(f"Error processing points redemption: {str(e)}")
        return Response(
            {"success": False, "error": f"Error processing redemption: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_woocommerce_customer_points_history(request, customer_id):
    """
    Get customer points history directly from WordPress database.
    
    This endpoint queries the YITH Points and Rewards wp_yith_ywpar_points_log table
    to retrieve the complete points transaction history for a customer.
    
    Based on the screenshot, returns:
    - Date (date_earning)
    - Amount (points change - can be positive or negative)
    - Order No. (order_id from WooCommerce)
    - Reason (formatted action like "Order Completed")
    - Description (additional details)
    
    Args:
        request: The HTTP request
        customer_id: The WooCommerce customer ID
        
    Returns:
        Response with points history: {
            'success': True,
            'customer_id': number,
            'total_records': number,
            'history': [
                {
                    'id': record_id,
                    'date': '2025-09-16T10:56:18',
                    'amount': 3,
                    'order_id': 285493,
                    'action': 'order_completed',
                    'reason': 'Order Completed',
                    'description': '',
                    'cancelled': False
                },
                ...
            ]
        }
    """
    logger.info(f"Fetching YITH points history for customer ID: {customer_id}")
    
    try:
        # Get points history from WordPress database
        history = get_yith_points_history(customer_id)
        
        logger.info(f"Successfully retrieved {len(history)} points history records for customer {customer_id}")
        
        return Response({
            "success": True,
            "customer_id": customer_id,
            "total_records": len(history),
            "history": history
        })
        
    except ValueError as e:
        logger.error(f"Configuration error retrieving points history: {str(e)}")
        return Response(
            {
                "success": False,
                "error": "Database configuration error. Please check server configuration.",
                "history": []
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    except Exception as e:
        logger.error(f"Error retrieving points history for customer {customer_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return Response(
            {
                "success": False,
                "error": f"Error retrieving points history: {str(e)}",
                "history": []
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def perform_points_adjustment(customer_id, points_change, reason='Manual adjustment by POS staff'):
    """
    Helper function to adjust customer points without requiring a request object.
    
    Args:
        customer_id: The WooCommerce customer ID
        points_change: Number of points to add (positive) or subtract (negative)
        reason: Optional description for the adjustment
        
    Returns:
        dict with adjustment result: {
            'success': True/False,
            'points_change': number,
            'new_balance': number,
            'previous_balance': number,
            'message': string,
            'error': string (if success is False)
        }
    """
    logger.info(f"Processing points adjustment for customer ID: {customer_id}, change: {points_change}")
    
    try:
        points_change = int(points_change)
        
        if points_change == 0:
            return {
                "success": False,
                "error": "Points change must be non-zero"
            }
        
        # Get current points balance
        wc_api = WooCommerceAPI()
        points_data = wc_api.get_customer_points(customer_id)
        
        if points_data is None:
            logger.warning(f"Could not retrieve points data for customer ID: {customer_id}")
            return {
                "success": False,
                "error": "Could not retrieve customer points data"
            }
        
        current_points = points_data.get('points_to_redeem', 0)
        new_balance = current_points + points_change
        
        # Prevent negative balance
        if new_balance < 0:
            return {
                "success": False,
                "error": f"Insufficient points. Current balance: {current_points}, Requested change: {points_change}"
            }
        
        # Update points in WordPress database
        with get_wordpress_database_connection() as connection:
            with connection.cursor() as cursor:
                # Update wp_usermeta table with new points balance
                # YITH Points & Rewards uses _ywpar_user_total_points meta key
                update_query = """
                UPDATE wp_usermeta 
                SET meta_value = %s 
                WHERE user_id = %s 
                AND meta_key = '_ywpar_user_total_points'
                """
                
                cursor.execute(update_query, (str(new_balance), customer_id))
                
                # If no rows were updated, insert the meta key
                if cursor.rowcount == 0:
                    insert_query = """
                    INSERT INTO wp_usermeta (user_id, meta_key, meta_value)
                    VALUES (%s, '_ywpar_user_total_points', %s)
                    """
                    cursor.execute(insert_query, (customer_id, str(new_balance)))
                
                logger.info(f"Updated points balance for customer {customer_id}: {current_points} -> {new_balance}")
                
                # Insert record into YITH points log
                current_time = datetime.now()
                log_query = """
                INSERT INTO wp_yith_ywpar_points_log 
                (user_id, action, amount, date_earning, description, cancelled, order_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
                
                cursor.execute(
                    log_query,
                    (
                        customer_id,
                        'admin_action',
                        points_change,
                        current_time,
                        reason,
                        0,  # not cancelled
                        0   # no associated order
                    )
                )
                
                connection.commit()
                logger.info(f"Logged points adjustment in YITH history for customer {customer_id}")
        
        action_type = "added" if points_change > 0 else "subtracted"
        abs_change = abs(points_change)
        
        return {
            "success": True,
            "points_change": points_change,
            "new_balance": new_balance,
            "previous_balance": current_points,
            "message": f"Successfully {action_type} {abs_change} points. New balance: {new_balance}"
        }
        
    except ValueError as e:
        logger.error(f"Configuration error adjusting points: {str(e)}")
        return {
            "success": False,
            "error": "Database configuration error"
        }
    except Exception as e:
        logger.error(f"Error adjusting points for customer {customer_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            "success": False,
            "error": f"Error adjusting points: {str(e)}"
        }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def adjust_customer_points(request, customer_id):
    """
    Manually adjust customer points (add or subtract).
    
    This endpoint allows manual points adjustment similar to WP admin +/- buttons.
    It directly updates the WordPress database and logs the transaction in YITH points history.
    
    Args:
        request: The HTTP request with JSON body containing:
            - points_change: Number of points to add (positive) or subtract (negative)
            - reason: Optional description for the adjustment
        customer_id: The WooCommerce customer ID
        
    Returns:
        Response with adjustment data: {
            'success': True/False,
            'points_change': number,
            'new_balance': number,
            'message': string
        }
    """
    logger.info(f"API: Processing manual points adjustment for customer ID: {customer_id}")
    
    try:
        # Parse request data
        data = request.data or {}
        points_change = int(data.get('points_change', 0))
        reason = data.get('reason', 'Manual adjustment by POS staff')
        
        # Call helper function
        result = perform_points_adjustment(customer_id, points_change, reason)
        
        # Return appropriate HTTP status based on result
        if result['success']:
            return Response(result, status=status.HTTP_200_OK)
        else:
            # Determine status code based on error type
            error_msg = result.get('error', '')
            if 'not found' in error_msg.lower() or 'could not retrieve' in error_msg.lower():
                return Response(result, status=status.HTTP_404_NOT_FOUND)
            elif 'insufficient' in error_msg.lower() or 'must be non-zero' in error_msg.lower():
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response(result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
                
    except Exception as e:
        logger.error(f"API: Error in adjust_customer_points: {str(e)}")
        return Response(
            {"success": False, "error": f"Error processing request: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
