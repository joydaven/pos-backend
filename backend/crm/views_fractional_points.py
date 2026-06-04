from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.utils import timezone
from crm.models import Contact, CustomerPointsAccount, PointsTransaction
from crm.woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_fractional_points_balance(request, customer_id):
    """
    Get customer's fractional points balance
    YITH is source of truth - if no POS DB record, fetch from YITH first
    
    GET /api/customers/{customer_id}/fractional-points/
    
    Returns:
        {
            "success": true,
            "balance": "153.60",
            "yith_synced": 153,
            "last_sync": "2024-01-21T09:00:00Z",
            "source": "pos_db" or "yith_fallback"
        }
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Always sync from YITH first to ensure WooCommerce-added points are reflected
    if customer.woo_customer_id:
        try:
            wc_api = WooCommerceAPI()
            yith_data = wc_api.get_customer_points(customer.woo_customer_id)
            
            if yith_data:
                yith_points = yith_data.get('points_to_redeem', 0)
                logger.info(f"Fetched current YITH points for customer {customer_id}: {yith_points}")
                
                # Get or create POS DB account
                account, created = CustomerPointsAccount.objects.get_or_create(
                    customer=customer,
                    defaults={
                        'points_balance': Decimal(str(yith_points)),
                        'yith_synced_points': yith_points,
                        'yith_last_sync': timezone.now(),
                        'sync_needed': False
                    }
                )
                
                if not created:
                    # Account exists - check if YITH points changed (WooCommerce added points)
                    if yith_points != account.yith_synced_points:
                        points_diff = yith_points - account.yith_synced_points
                        logger.info(f"YITH points changed by {points_diff} (WooCommerce added points). Syncing to fractional DB.")
                        
                        # Update POS DB to match YITH (preserve decimals by adding the integer difference)
                        account.points_balance += Decimal(str(points_diff))
                        account.yith_synced_points = yith_points
                        account.yith_last_sync = timezone.now()
                        account.sync_needed = False
                        account.save()
                
                source = "yith_synced"
                logger.info(f"Synced fractional points from YITH: {account.points_balance}")
                
                return Response({
                    "success": True,
                    "balance": str(account.points_balance),
                    "yith_synced": account.yith_synced_points,
                    "last_sync": account.yith_last_sync.isoformat() if account.yith_last_sync else None,
                    "sync_needed": account.sync_needed,
                    "source": source
                })
        except Exception as e:
            logger.error(f"Error syncing from YITH: {str(e)}")
            # Fall through to use existing POS DB data if YITH sync fails
    
    # Fallback: use existing POS DB account if YITH sync failed or no WooCommerce ID
    try:
        account = CustomerPointsAccount.objects.get(customer=customer)
        source = "pos_db_fallback"
        logger.info(f"Using POS DB points (YITH sync unavailable) for customer {customer_id}: {account.points_balance}")
        
    except CustomerPointsAccount.DoesNotExist:
        # No POS DB record - fallback to YITH (source of truth)
        logger.info(f"No POS DB account for customer {customer_id}, fetching from YITH")
        
        if not customer.woo_customer_id:
            # No WooCommerce ID, create empty account
            account = CustomerPointsAccount.objects.create(
                customer=customer,
                points_balance=Decimal('0'),
                yith_synced_points=0
            )
            source = "new_account"
        else:
            # Fetch from YITH and initialize POS DB
            wc_api = WooCommerceAPI()
            yith_data = wc_api.get_customer_points(customer.woo_customer_id)
            
            if yith_data:
                yith_points = yith_data.get('points_to_redeem', 0)
                account = CustomerPointsAccount.objects.create(
                    customer=customer,
                    points_balance=Decimal(str(yith_points)),  # Initialize with YITH value
                    yith_synced_points=yith_points,
                    yith_last_sync=timezone.now(),
                    sync_needed=False
                )
                source = "yith_fallback"
                logger.info(f"Initialized POS DB from YITH: {yith_points} points")
            else:
                # YITH fetch failed, create with 0
                account = CustomerPointsAccount.objects.create(
                    customer=customer,
                    points_balance=Decimal('0'),
                    yith_synced_points=0
                )
                source = "yith_error"
                logger.warning(f"Failed to fetch YITH points, initialized with 0")
    
    return Response({
        "success": True,
        "balance": str(account.points_balance),
        "yith_synced": account.yith_synced_points,
        "last_sync": account.yith_last_sync.isoformat() if account.yith_last_sync else None,
        "sync_needed": account.sync_needed,
        "source": source
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def redeem_fractional_points(request, customer_id):
    """
    Redeem fractional points with decimal precision
    
    POST /api/customers/{customer_id}/fractional-points/redeem/
    Body: {
        "points_to_redeem": 12.75,
        "order_id": "ORD-123456",
        "order_total": 53.60
    }
    
    Returns:
        {
            "success": true,
            "points_redeemed": "12.75",
            "discount_amount": "12.75",
            "remaining_points": "140.85",
            "message": "Successfully redeemed 12.75 points for $12.75 discount"
        }
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Parse request data
    try:
        points_to_redeem = Decimal(str(request.data.get('points_to_redeem', 0)))
        order_total = Decimal(str(request.data.get('order_total', 0)))
        order_id = request.data.get('order_id')
    except (ValueError, InvalidOperation) as e:
        return Response(
            {"success": False, "error": f"Invalid numeric value: {str(e)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    if points_to_redeem <= 0:
        return Response(
            {"success": False, "error": "Points to redeem must be greater than 0"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get or create points account - initialize from YITH if needed
    try:
        account = CustomerPointsAccount.objects.get(customer=customer)
        logger.info(f"Using existing POS DB account: {account.points_balance} points")
    except CustomerPointsAccount.DoesNotExist:
        # No POS DB record - initialize from YITH (source of truth)
        logger.info(f"No POS DB account, initializing from YITH for redemption")
        
        if customer.woo_customer_id:
            wc_api = WooCommerceAPI()
            yith_data = wc_api.get_customer_points(customer.woo_customer_id)
            
            if yith_data:
                yith_points = yith_data.get('points_to_redeem', 0)
                account = CustomerPointsAccount.objects.create(
                    customer=customer,
                    points_balance=Decimal(str(yith_points)),
                    yith_synced_points=yith_points,
                    yith_last_sync=timezone.now(),
                    sync_needed=False
                )
                logger.info(f"Initialized POS DB from YITH with {yith_points} points")
            else:
                # YITH fetch failed, create with 0
                account = CustomerPointsAccount.objects.create(
                    customer=customer,
                    points_balance=Decimal('0'),
                    yith_synced_points=0
                )
                logger.warning(f"Failed to fetch YITH points during redemption, initialized with 0")
        else:
            # No WooCommerce ID, create with 0
            account = CustomerPointsAccount.objects.create(
                customer=customer,
                points_balance=Decimal('0'),
                yith_synced_points=0
            )
            logger.warning(f"No WooCommerce ID for customer, initialized with 0 points")
    
    # Validate sufficient balance
    if account.points_balance < points_to_redeem:
        return Response(
            {
                "success": False, 
                "error": f"Insufficient points. Available: {account.points_balance}, Requested: {points_to_redeem}"
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Calculate discount (1 point = $1.00)
    discount_amount = points_to_redeem
    
    # Validate discount doesn't exceed order total
    if discount_amount > order_total:
        return Response(
            {
                "success": False, 
                "error": f"Discount amount (${discount_amount}) exceeds order total (${order_total})"
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        with transaction.atomic():
            # Redeem points
            transaction_record = account.redeem_points(
                amount=points_to_redeem,
                order_id=order_id,
                description=f"Points redeemed for ${discount_amount} discount",
                user=request.user if request.user.is_authenticated else None
            )
            
            # Sync to YITH (floor to integer) - Direct database update
            if customer.woo_customer_id:
                try:
                    from crm.views_woocommerce_points import get_wordpress_database_connection
                    from datetime import datetime
                    
                    integer_points = int(account.points_balance)
                    
                    # Direct WordPress database update (same as perform_points_adjustment)
                    with get_wordpress_database_connection() as connection:
                        with connection.cursor() as cursor:
                            # Update wp_usermeta table with new points balance
                            update_query = """
                            UPDATE wp_usermeta 
                            SET meta_value = %s 
                            WHERE user_id = %s 
                            AND meta_key = '_ywpar_user_total_points'
                            """
                            cursor.execute(update_query, (str(integer_points), customer.woo_customer_id))
                            
                            # If no rows updated, insert the meta key
                            if cursor.rowcount == 0:
                                insert_query = """
                                INSERT INTO wp_usermeta (user_id, meta_key, meta_value)
                                VALUES (%s, '_ywpar_user_total_points', %s)
                                """
                                cursor.execute(insert_query, (customer.woo_customer_id, str(integer_points)))
                            
                            # Insert record into YITH points log
                            log_query = """
                            INSERT INTO wp_yith_ywpar_points_log 
                            (user_id, action, amount, date_earning, description, cancelled, order_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """
                            cursor.execute(
                                log_query,
                                (
                                    customer.woo_customer_id,
                                    'redeemed_points',
                                    -int(points_to_redeem),  # Negative for redemption
                                    datetime.now(),
                                    f"Points redeemed via POS - Order {order_id}",
                                    0,  # not cancelled
                                    0   # order_id (could parse if needed)
                                )
                            )
                            
                            connection.commit()
                            
                    account.yith_synced_points = integer_points
                    account.yith_last_sync = timezone.now()
                    account.sync_needed = False
                    account.save()
                    logger.info(f"Synced points to YITH via direct DB update: {integer_points}")
                        
                except Exception as e:
                    logger.error(f"Error syncing to YITH database: {str(e)}")
            
            logger.info(
                f"Successfully redeemed {points_to_redeem} points for customer {customer_id}. "
                f"Remaining: {account.points_balance}"
            )
            
            return Response({
                "success": True,
                "points_redeemed": str(points_to_redeem),
                "discount_amount": str(discount_amount),
                "remaining_points": str(account.points_balance),
                "transaction_id": str(transaction_record.id),
                "message": f"Successfully redeemed {points_to_redeem} points for ${discount_amount} discount"
            })
            
    except ValueError as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.error(f"Error redeeming points for customer {customer_id}: {str(e)}", exc_info=True)
        return Response(
            {"success": False, "error": "An error occurred while redeeming points"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def adjust_fractional_points(request, customer_id):
    """
    Manual adjustment of fractional points (admin only)
    
    POST /api/customers/{customer_id}/fractional-points/adjust/
    Body: {
        "amount": 10.50,  # Positive to add, negative to subtract
        "description": "Manual adjustment - customer service credit",
        "order_id": "ORD-123456"  # Optional
    }
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Parse request data
    try:
        amount = Decimal(str(request.data.get('amount', 0)))
        description = request.data.get('description', 'Manual adjustment')
        order_id = request.data.get('order_id')
    except (ValueError, InvalidOperation) as e:
        return Response(
            {"success": False, "error": f"Invalid numeric value: {str(e)}"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    if amount == 0:
        return Response(
            {"success": False, "error": "Amount cannot be zero"},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Get or create points account
    account, created = CustomerPointsAccount.objects.get_or_create(
        customer=customer,
        defaults={'points_balance': Decimal('0')}
    )
    
    try:
        with transaction.atomic():
            if amount > 0:
                # Add points
                transaction_record = account.add_points(
                    amount=amount,
                    order_id=order_id,
                    description=description,
                    user=request.user if request.user.is_authenticated else None,
                    transaction_type='adjust'
                )
            else:
                # Deduct points (convert to positive for redeem_points)
                if account.points_balance < abs(amount):
                    return Response(
                        {
                            "success": False,
                            "error": f"Insufficient points. Available: {account.points_balance}, Requested: {abs(amount)}"
                        },
                        status=status.HTTP_400_BAD_REQUEST
                    )
                
                transaction_record = account.redeem_points(
                    amount=abs(amount),
                    order_id=order_id,
                    description=description,
                    user=request.user if request.user.is_authenticated else None
                )
            
            # Sync to YITH - Direct database update
            if customer.woo_customer_id:
                try:
                    from crm.views_woocommerce_points import get_wordpress_database_connection
                    from datetime import datetime
                    
                    integer_points = int(account.points_balance)
                    
                    with get_wordpress_database_connection() as connection:
                        with connection.cursor() as cursor:
                            # Update wp_usermeta
                            update_query = """
                            UPDATE wp_usermeta 
                            SET meta_value = %s 
                            WHERE user_id = %s 
                            AND meta_key = '_ywpar_user_total_points'
                            """
                            cursor.execute(update_query, (str(integer_points), customer.woo_customer_id))
                            
                            if cursor.rowcount == 0:
                                insert_query = """
                                INSERT INTO wp_usermeta (user_id, meta_key, meta_value)
                                VALUES (%s, '_ywpar_user_total_points', %s)
                                """
                                cursor.execute(insert_query, (customer.woo_customer_id, str(integer_points)))
                            
                            # Log in YITH points history
                            log_query = """
                            INSERT INTO wp_yith_ywpar_points_log 
                            (user_id, action, amount, date_earning, description, cancelled, order_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """
                            cursor.execute(
                                log_query,
                                (
                                    customer.woo_customer_id,
                                    'admin_action',
                                    int(amount),
                                    datetime.now(),
                                    description,
                                    0,
                                    0
                                )
                            )
                            
                            connection.commit()
                    
                    account.yith_synced_points = integer_points
                    account.yith_last_sync = timezone.now()
                    account.sync_needed = False
                    account.save()
                    logger.info(f"Synced adjusted points to YITH via direct DB: {integer_points}")
                except Exception as e:
                    logger.error(f"Error syncing to YITH database: {str(e)}")
            
            return Response({
                "success": True,
                "amount": str(amount),
                "new_balance": str(account.points_balance),
                "transaction_id": str(transaction_record.id),
                "message": f"Successfully adjusted points by {amount}"
            })
            
    except Exception as e:
        logger.error(f"Error adjusting points for customer {customer_id}: {str(e)}", exc_info=True)
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_points_transactions(request, customer_id):
    """
    Get transaction history for customer's points
    
    GET /api/customers/{customer_id}/fractional-points/transactions/
    Query params:
        - limit: Number of transactions to return (default: 50)
        - offset: Pagination offset (default: 0)
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND
        )
    
    limit = int(request.query_params.get('limit', 50))
    offset = int(request.query_params.get('offset', 0))
    
    transactions = PointsTransaction.objects.filter(customer=customer)[offset:offset+limit]
    total_count = PointsTransaction.objects.filter(customer=customer).count()
    
    transactions_data = [
        {
            "id": str(t.id),
            "type": t.transaction_type,
            "amount": str(t.amount),
            "balance_after": str(t.balance_after),
            "description": t.description,
            "order_id": t.order_id,
            "created_at": t.created_at.isoformat(),
            "created_by": t.created_by.username if t.created_by else None,
            "metadata": t.metadata
        }
        for t in transactions
    ]
    
    return Response({
        "success": True,
        "transactions": transactions_data,
        "total_count": total_count,
        "limit": limit,
        "offset": offset
    })
