import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from crm.models import Contact
from .models import CreditBankAccount, CreditBankTransaction

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_credit_bank_balance(request, customer_id):
    """
    GET /api/credit-bank/<uuid:customer_id>/
    Returns the customer's credit bank balance.
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    account, _ = CreditBankAccount.objects.get_or_create(
        customer=customer, defaults={"balance": Decimal("0")}
    )

    return Response({
        "success": True,
        "balance": str(account.balance),
        "customer_id": str(customer.id),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_credits(request, customer_id):
    """
    POST /api/credit-bank/<uuid:customer_id>/add/
    Body: { "amount": 500.00, "description": "Purchased credit bank package", "order_id": "ORD-123" }
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        amount = Decimal(str(request.data.get('amount', 0)))
        description = request.data.get('description', 'Credits added')
        order_id = request.data.get('order_id')
    except (ValueError, InvalidOperation) as e:
        return Response(
            {"success": False, "error": f"Invalid numeric value: {e}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if amount <= 0:
        return Response(
            {"success": False, "error": "Amount must be greater than 0"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account, _ = CreditBankAccount.objects.get_or_create(
        customer=customer, defaults={"balance": Decimal("0")}
    )

    try:
        with transaction.atomic():
            txn = account.add_credits(
                amount=amount,
                order_id=order_id,
                description=description,
                user=request.user if request.user.is_authenticated else None,
            )
            logger.info(f"Added ${amount} credits for customer {customer_id}. New balance: ${account.balance}")

            from users.activity_log import log_activity
            log_activity(request, f"Added ${amount} credits for customer {customer_id}", category='credit_bank', details={
                'customer_id': str(customer_id),
                'amount': str(amount),
                'new_balance': str(account.balance),
                'description': description,
            })

            return Response({
                "success": True,
                "amount": str(amount),
                "new_balance": str(account.balance),
                "transaction_id": str(txn.id),
                "message": f"Successfully added ${amount} to credit bank",
            })
    except ValueError as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.error(f"Error adding credits for customer {customer_id}: {e}", exc_info=True)
        return Response(
            {"success": False, "error": "An error occurred while adding credits"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def redeem_credits(request, customer_id):
    """
    POST /api/credit-bank/<uuid:customer_id>/redeem/
    Body: { "amount": 150.00, "order_id": "ORD-456", "order_total": 200.00 }
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        amount = Decimal(str(request.data.get('amount', 0)))
        order_id = request.data.get('order_id')
        order_total = Decimal(str(request.data.get('order_total', 0)))
    except (ValueError, InvalidOperation) as e:
        return Response(
            {"success": False, "error": f"Invalid numeric value: {e}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if amount <= 0:
        return Response(
            {"success": False, "error": "Amount must be greater than 0"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if order_total > 0 and amount > order_total:
        return Response(
            {"success": False, "error": f"Redemption amount (${amount}) exceeds order total (${order_total})"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        account = CreditBankAccount.objects.get(customer=customer)
    except CreditBankAccount.DoesNotExist:
        return Response(
            {"success": False, "error": "No credit bank account found for this customer"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if account.balance < amount:
        return Response(
            {"success": False, "error": f"Insufficient credits. Available: ${account.balance}, Requested: ${amount}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        with transaction.atomic():
            txn = account.redeem_credits(
                amount=amount,
                order_id=order_id,
                description=f"Credits redeemed for order {order_id}" if order_id else "Credits redeemed",
                user=request.user if request.user.is_authenticated else None,
            )
            logger.info(f"Redeemed ${amount} credits for customer {customer_id}. Remaining: ${account.balance}")

            from users.activity_log import log_activity
            log_activity(request, f"Redeemed ${amount} credits for customer {customer_id}", category='credit_bank', details={
                'customer_id': str(customer_id),
                'amount': str(amount),
                'remaining_balance': str(account.balance),
                'order_id': order_id,
            })

            return Response({
                "success": True,
                "amount_redeemed": str(amount),
                "remaining_balance": str(account.balance),
                "transaction_id": str(txn.id),
                "message": f"Successfully redeemed ${amount} from credit bank",
            })
    except ValueError as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.error(f"Error redeeming credits for customer {customer_id}: {e}", exc_info=True)
        return Response(
            {"success": False, "error": "An error occurred while redeeming credits"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def adjust_credits(request, customer_id):
    """
    POST /api/credit-bank/<uuid:customer_id>/adjust/
    Body: { "amount": -50.00, "description": "Correction" }
    Positive = add, Negative = subtract.
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        amount = Decimal(str(request.data.get('amount', 0)))
        description = request.data.get('description', 'Manual adjustment')
        order_id = request.data.get('order_id')
    except (ValueError, InvalidOperation) as e:
        return Response(
            {"success": False, "error": f"Invalid numeric value: {e}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if amount == 0:
        return Response(
            {"success": False, "error": "Amount cannot be zero"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    account, _ = CreditBankAccount.objects.get_or_create(
        customer=customer, defaults={"balance": Decimal("0")}
    )

    try:
        with transaction.atomic():
            if amount > 0:
                txn = account.add_credits(
                    amount=amount,
                    order_id=order_id,
                    description=description,
                    user=request.user if request.user.is_authenticated else None,
                )
            else:
                if account.balance < abs(amount):
                    return Response(
                        {"success": False, "error": f"Insufficient credits. Available: ${account.balance}, Requested: ${abs(amount)}"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                txn = account.redeem_credits(
                    amount=abs(amount),
                    order_id=order_id,
                    description=description,
                    user=request.user if request.user.is_authenticated else None,
                )

            logger.info(f"Adjusted credits by ${amount} for customer {customer_id}. New balance: ${account.balance}")

            from users.activity_log import log_activity
            log_activity(request, f"Adjusted credits by ${amount} for customer {customer_id}", category='credit_bank', details={
                'customer_id': str(customer_id),
                'amount': str(amount),
                'new_balance': str(account.balance),
                'description': description,
            })

            return Response({
                "success": True,
                "amount": str(amount),
                "new_balance": str(account.balance),
                "transaction_id": str(txn.id),
                "message": f"Successfully adjusted credit bank by ${amount}",
            })
    except ValueError as e:
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.error(f"Error adjusting credits for customer {customer_id}: {e}", exc_info=True)
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_credit_bank_transactions(request, customer_id):
    """
    GET /api/credit-bank/<uuid:customer_id>/transactions/
    Query params: limit (default 50), offset (default 0)
    """
    try:
        customer = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {"success": False, "error": "Customer not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    limit = int(request.query_params.get('limit', 50))
    offset = int(request.query_params.get('offset', 0))

    transactions = CreditBankTransaction.objects.filter(customer=customer)[offset:offset + limit]
    total_count = CreditBankTransaction.objects.filter(customer=customer).count()

    # Also return current balance
    try:
        account = CreditBankAccount.objects.get(customer=customer)
        balance = str(account.balance)
    except CreditBankAccount.DoesNotExist:
        balance = "0.00"

    data = [
        {
            "id": str(t.id),
            "type": t.transaction_type,
            "amount": str(t.amount),
            "balance_after": str(t.balance_after),
            "description": t.description,
            "order_id": t.order_id,
            "created_at": t.created_at.isoformat(),
            "created_by": t.created_by.username if t.created_by else None,
            "metadata": t.metadata,
        }
        for t in transactions
    ]

    return Response({
        "success": True,
        "balance": balance,
        "transactions": data,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    })
