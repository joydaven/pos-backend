import logging
from decimal import Decimal
from datetime import date

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import PaymentPlanTemplate, PaymentPlan, PaymentPlanInstallment
from .serializers import (
    PaymentPlanTemplateSerializer,
    PaymentPlanSerializer,
    PaymentPlanInstallmentSerializer,
    PaymentPlanCreateSerializer,
)

logger = logging.getLogger(__name__)


# ─── Template CRUD ────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def template_list(request):
    """List active templates or create a new one."""
    if request.method == 'GET':
        templates = PaymentPlanTemplate.objects.filter(is_active=True)
        serializer = PaymentPlanTemplateSerializer(templates, many=True)
        return Response(serializer.data)

    # POST
    serializer = PaymentPlanTemplateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(created_by=request.user)
    return Response(serializer.data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsAuthenticated])
def template_detail(request, template_id):
    """Get, update, or soft-delete a template."""
    try:
        template = PaymentPlanTemplate.objects.get(id=template_id)
    except PaymentPlanTemplate.DoesNotExist:
        return Response({'error': 'Template not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return Response(PaymentPlanTemplateSerializer(template).data)

    if request.method == 'PUT':
        serializer = PaymentPlanTemplateSerializer(template, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # DELETE — soft delete
    template.is_active = False
    template.save()
    return Response({'status': 'deleted'})


# ─── Payment Plan CRUD ────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def plan_list(request):
    """List plans (filterable) or create a new plan with installments."""
    if request.method == 'GET':
        plans = PaymentPlan.objects.all()

        # Filters
        plan_status = request.query_params.get('status')
        if plan_status:
            plans = plans.filter(status=plan_status)

        contact_id = request.query_params.get('contact_id')
        if contact_id:
            plans = plans.filter(contact_id=contact_id)

        search = request.query_params.get('search')
        if search:
            from django.db.models import Q
            plans = plans.filter(
                Q(plan_number__icontains=search) |
                Q(contact__first_name__icontains=search) |
                Q(contact__last_name__icontains=search) |
                Q(pos_order__order_number__icontains=search)
            )

        serializer = PaymentPlanSerializer(plans, many=True)
        return Response(serializer.data)

    # POST — create plan + installments
    create_ser = PaymentPlanCreateSerializer(data=request.data)
    create_ser.is_valid(raise_exception=True)
    data = create_ser.validated_data

    from crm.models import POSOrder, Contact

    try:
        pos_order = POSOrder.objects.get(id=data['pos_order_id'])
    except POSOrder.DoesNotExist:
        return Response({'error': 'POS Order not found'}, status=status.HTTP_404_NOT_FOUND)

    contact = None
    if data.get('contact_id'):
        try:
            contact = Contact.objects.get(id=data['contact_id'])
        except Contact.DoesNotExist:
            pass

    template = None
    if data.get('template_id'):
        try:
            template = PaymentPlanTemplate.objects.get(id=data['template_id'])
        except PaymentPlanTemplate.DoesNotExist:
            pass

    # Validate installments sum
    installments_data = data['installments']
    if not installments_data:
        return Response({'error': 'At least one installment is required'}, status=status.HTTP_400_BAD_REQUEST)

    total_installments = sum(Decimal(str(i['amount'])) for i in installments_data)
    if abs(total_installments - Decimal(str(data['total_amount']))) > Decimal('0.02'):
        return Response(
            {'error': f'Installments total ${total_installments} does not match plan total ${data["total_amount"]}'},
            status=status.HTTP_400_BAD_REQUEST
        )

    plan = PaymentPlan.objects.create(
        plan_number=PaymentPlan.generate_plan_number(),
        pos_order=pos_order,
        contact=contact,
        template=template,
        total_amount=data['total_amount'],
        original_amount=data.get('original_amount') or data['total_amount'],
        service_charge_type=data.get('service_charge_type', 'none'),
        service_charge_value=Decimal(str(data.get('service_charge_value', 0))),
        service_charge_amount=Decimal(str(data.get('service_charge_amount', 0))),
        payment_method=data.get('payment_method', {}),
        notes=data.get('notes', ''),
        created_by=request.user,
    )

    for idx, inst_data in enumerate(installments_data, start=1):
        PaymentPlanInstallment.objects.create(
            plan=plan,
            installment_number=idx,
            amount=Decimal(str(inst_data['amount'])),
            due_date=inst_data['due_date'],
            status='paid' if idx == 1 else 'scheduled',
            payment_method=inst_data.get('payment_method', data.get('payment_method', {})),
            # First installment is already paid at checkout
            paid_at=timezone.now() if idx == 1 else None,
            paid_amount=Decimal(str(inst_data['amount'])) if idx == 1 else None,
            transaction_id=inst_data.get('transaction_id', ''),
        )

    logger.info(f"Created payment plan {plan.plan_number} with {len(installments_data)} installments for order {pos_order.order_number}")

    from users.activity_log import log_activity
    log_activity(request, f"Created payment plan {plan.plan_number}", category='payment_plan', details={
        'plan_number': plan.plan_number,
        'plan_id': str(plan.id),
        'total_amount': str(plan.total_amount),
        'installments': len(installments_data),
        'order_number': pos_order.order_number,
        'customer': str(contact) if contact else None,
    })

    return Response(PaymentPlanSerializer(plan).data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PUT'])
@permission_classes([IsAuthenticated])
def plan_detail(request, plan_id):
    """Get or update a payment plan."""
    try:
        plan = PaymentPlan.objects.get(id=plan_id)
    except PaymentPlan.DoesNotExist:
        return Response({'error': 'Payment plan not found'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        return Response(PaymentPlanSerializer(plan).data)

    # PUT — update plan fields (notes, payment_method, status, total_amount)
    allowed_fields = ['notes', 'payment_method', 'status']
    for field in allowed_fields:
        if field in request.data:
            setattr(plan, field, request.data[field])
    # Allow updating total_amount (recalculates remaining balance automatically via property)
    if 'total_amount' in request.data:
        plan.total_amount = Decimal(str(request.data['total_amount']))
    plan.save()
    return Response(PaymentPlanSerializer(plan).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def plan_cancel(request, plan_id):
    """Cancel remaining installments on a plan."""
    try:
        plan = PaymentPlan.objects.get(id=plan_id)
    except PaymentPlan.DoesNotExist:
        return Response({'error': 'Payment plan not found'}, status=status.HTTP_404_NOT_FOUND)

    if plan.status != 'active':
        return Response({'error': f'Cannot cancel a plan with status "{plan.status}"'}, status=status.HTTP_400_BAD_REQUEST)

    cancelled_count = plan.installments.filter(
        status__in=['pending', 'scheduled']
    ).update(status='cancelled')

    plan.status = 'cancelled'
    plan.save()

    logger.info(f"Cancelled payment plan {plan.plan_number}, {cancelled_count} installments cancelled")

    from users.activity_log import log_activity
    log_activity(request, f"Cancelled payment plan {plan.plan_number}", category='payment_plan', details={
        'plan_number': plan.plan_number,
        'plan_id': str(plan.id),
        'cancelled_installments': cancelled_count,
    })

    return Response(PaymentPlanSerializer(plan).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def plan_restructure(request, plan_id):
    """
    Restructure an active payment plan: change total, add/remove installments,
    or redistribute remaining balance.

    Body:
        total_amount (optional): New total plan amount
        num_installments (optional): New total number of installments
        installments (optional): Array of {amount, due_date} for each unpaid installment
                                 If not provided, remaining balance is split evenly.
    """
    try:
        plan = PaymentPlan.objects.get(id=plan_id)
    except PaymentPlan.DoesNotExist:
        return Response({'error': 'Payment plan not found'}, status=status.HTTP_404_NOT_FOUND)

    if plan.status != 'active':
        return Response({'error': f'Cannot restructure a plan with status "{plan.status}"'}, status=status.HTTP_400_BAD_REQUEST)

    new_total = Decimal(str(request.data['total_amount'])) if 'total_amount' in request.data else None
    new_num = request.data.get('num_installments')
    custom_installments = request.data.get('installments')

    # Update total if changed
    if new_total is not None:
        plan.total_amount = new_total

    total_paid = plan.total_paid
    remaining = plan.total_amount - total_paid

    if remaining <= 0:
        return Response({'error': 'No remaining balance to restructure'}, status=status.HTTP_400_BAD_REQUEST)

    # Get current paid installments (keep them)
    paid_installments = list(plan.installments.filter(status='paid').order_by('installment_number'))
    paid_count = len(paid_installments)

    # Delete all unpaid installments — they'll be recreated
    plan.installments.exclude(status='paid').delete()

    if custom_installments:
        # User provided exact installment amounts/dates
        total_custom = sum(Decimal(str(i['amount'])) for i in custom_installments)
        if abs(total_custom - remaining) > Decimal('0.02'):
            return Response(
                {'error': f'Custom installments total ${total_custom} does not match remaining balance ${remaining}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        for idx, inst_data in enumerate(custom_installments, start=paid_count + 1):
            PaymentPlanInstallment.objects.create(
                plan=plan,
                installment_number=idx,
                amount=Decimal(str(inst_data['amount'])),
                due_date=inst_data['due_date'],
                status='scheduled',
            )
    elif new_num:
        # Auto-distribute remaining balance evenly across new installment count
        new_unpaid_count = int(new_num) - paid_count
        if new_unpaid_count <= 0:
            return Response(
                {'error': f'New installment count ({new_num}) must be greater than already-paid count ({paid_count})'},
                status=status.HTTP_400_BAD_REQUEST
            )

        per_installment = (remaining / new_unpaid_count).quantize(Decimal('0.01'))
        # Handle rounding: put any leftover cents on the last installment
        total_distributed = per_installment * (new_unpaid_count - 1)
        last_amount = remaining - total_distributed

        from datetime import timedelta
        # Start from today or the last paid installment's due date, whichever is later
        last_paid_date = paid_installments[-1].due_date if paid_installments else date.today()
        base_date = max(last_paid_date, date.today())

        for idx in range(new_unpaid_count):
            inst_num = paid_count + idx + 1
            amount = last_amount if idx == new_unpaid_count - 1 else per_installment
            due = base_date + timedelta(days=30 * (idx + 1))
            PaymentPlanInstallment.objects.create(
                plan=plan,
                installment_number=inst_num,
                amount=amount,
                due_date=due,
                status='scheduled',
            )
    else:
        return Response(
            {'error': 'Provide either num_installments or installments array'},
            status=status.HTTP_400_BAD_REQUEST
        )

    plan.save()
    logger.info(f"Restructured plan {plan.plan_number}: total=${plan.total_amount}, remaining=${remaining}")
    return Response(PaymentPlanSerializer(plan).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def plan_pay_early(request, plan_id):
    """Pay off the remaining balance of a plan."""
    try:
        plan = PaymentPlan.objects.get(id=plan_id)
    except PaymentPlan.DoesNotExist:
        return Response({'error': 'Payment plan not found'}, status=status.HTTP_404_NOT_FOUND)

    if plan.status != 'active':
        return Response({'error': f'Cannot pay off a plan with status "{plan.status}"'}, status=status.HTTP_400_BAD_REQUEST)

    remaining = plan.remaining_balance
    if remaining <= 0:
        return Response({'error': 'No remaining balance'}, status=status.HTTP_400_BAD_REQUEST)

    payment_method = request.data.get('payment_method', plan.payment_method)
    transaction_id = request.data.get('transaction_id', '')

    # Mark all pending/scheduled installments as paid
    pending = plan.installments.filter(status__in=['pending', 'scheduled']).order_by('installment_number')
    for inst in pending:
        inst.mark_paid(
            amount=inst.amount,
            transaction_id=transaction_id,
            payment_method=payment_method,
        )

    logger.info(f"Paid off remaining ${remaining} on plan {plan.plan_number}")
    return Response(PaymentPlanSerializer(plan).data)


# ─── Installment Actions ──────────────────────────────────────────────────────

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def installment_update(request, installment_id):
    """Update an installment (reschedule date, change amount, notes)."""
    try:
        inst = PaymentPlanInstallment.objects.get(id=installment_id)
    except PaymentPlanInstallment.DoesNotExist:
        return Response({'error': 'Installment not found'}, status=status.HTTP_404_NOT_FOUND)

    if inst.status == 'paid':
        return Response({'error': 'Cannot modify a paid installment'}, status=status.HTTP_400_BAD_REQUEST)

    allowed_fields = ['due_date', 'amount', 'notes', 'payment_method', 'card_override']
    for field in allowed_fields:
        if field in request.data:
            value = request.data[field]
            if field == 'amount':
                value = Decimal(str(value))
            setattr(inst, field, value)
    inst.save()

    logger.info(f"Updated installment {inst.installment_number} of plan {inst.plan.plan_number}")
    return Response(PaymentPlanInstallmentSerializer(inst).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def installment_mark_paid(request, installment_id):
    """Manually mark an installment as paid (for cash/check payments)."""
    try:
        inst = PaymentPlanInstallment.objects.get(id=installment_id)
    except PaymentPlanInstallment.DoesNotExist:
        return Response({'error': 'Installment not found'}, status=status.HTTP_404_NOT_FOUND)

    if inst.status == 'paid':
        return Response({'error': 'Installment is already paid'}, status=status.HTTP_400_BAD_REQUEST)

    amount = request.data.get('amount', inst.amount)
    transaction_id = request.data.get('transaction_id', '')
    payment_method = request.data.get('payment_method', inst.payment_method)

    inst.mark_paid(
        amount=Decimal(str(amount)),
        transaction_id=transaction_id,
        payment_method=payment_method,
    )

    logger.info(f"Manually marked installment {inst.installment_number} of plan {inst.plan.plan_number} as paid")
    return Response(PaymentPlanInstallmentSerializer(inst).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def installment_charge(request, installment_id):
    """Manually trigger a CIM charge for an installment."""
    try:
        inst = PaymentPlanInstallment.objects.get(id=installment_id)
    except PaymentPlanInstallment.DoesNotExist:
        return Response({'error': 'Installment not found'}, status=status.HTTP_404_NOT_FOUND)

    if inst.status == 'paid':
        return Response({'error': 'Installment is already paid'}, status=status.HTTP_400_BAD_REQUEST)

    plan = inst.plan

    # Per-installment card override takes priority, then plan-level card
    inst_card = inst.card_override if inst.card_override and inst.card_override.get('customer_profile_id') else None
    plan_card = plan.payment_method if plan.payment_method and plan.payment_method.get('customer_profile_id') else None
    pm = inst_card or plan_card

    if not pm or not pm.get('customer_profile_id') or not pm.get('payment_profile_id'):
        return Response(
            {'error': 'No saved card on this plan or installment. Use mark-paid for non-card payments.'},
            status=status.HTTP_400_BAD_REQUEST
        )

    from payments.authorize_net import AuthorizeNetGateway

    try:
        inst.status = 'processing'
        inst.save()

        card_desc = f"{pm.get('brand', '?')} ****{pm.get('last4', '????')}"
        card_source = 'installment override' if inst_card else 'plan default'
        logger.info(f"Charging {card_desc} ({card_source}) for installment {inst.installment_number} of {plan.plan_number}")

        result = AuthorizeNetGateway.charge_customer_profile(
            customer_profile_id=pm['customer_profile_id'],
            payment_profile_id=pm['payment_profile_id'],
            amount=float(inst.amount),
            order_id=f"{plan.plan_number}-{inst.installment_number}",
        )

        if result.get('status') == 'approved':
            inst.mark_paid(
                amount=inst.amount,
                transaction_id=result.get('transaction_id', ''),
                payment_method=pm,
            )
            logger.info(f"Successfully charged installment {inst.installment_number} of plan {plan.plan_number}")
            return Response({
                'status': 'success',
                'transaction_id': result.get('transaction_id', ''),
                'installment': PaymentPlanInstallmentSerializer(inst).data,
            })
        else:
            # If installment-level card failed, try plan-level card as fallback
            if inst_card and plan_card and inst_card != plan_card:
                logger.warning(f"Installment card failed, trying plan default card for {plan.plan_number} #{inst.installment_number}")
                fallback_result = AuthorizeNetGateway.charge_customer_profile(
                    customer_profile_id=plan_card['customer_profile_id'],
                    payment_profile_id=plan_card['payment_profile_id'],
                    amount=float(inst.amount),
                    order_id=f"{plan.plan_number}-{inst.installment_number}",
                )
                if fallback_result.get('status') == 'approved':
                    inst.mark_paid(
                        amount=inst.amount,
                        transaction_id=fallback_result.get('transaction_id', ''),
                        payment_method=plan_card,
                    )
                    logger.info(f"Fallback card succeeded for {plan.plan_number} #{inst.installment_number}")
                    return Response({
                        'status': 'success',
                        'transaction_id': fallback_result.get('transaction_id', ''),
                        'fallback_card_used': True,
                        'installment': PaymentPlanInstallmentSerializer(inst).data,
                    })

            inst.status = 'failed'
            inst.failure_reason = result.get('message', 'Charge failed')
            inst.retry_count += 1
            inst.save()
            logger.warning(f"Failed to charge installment {inst.installment_number} of plan {plan.plan_number}: {inst.failure_reason}")
            return Response({
                'status': 'failed',
                'error': inst.failure_reason,
                'installment': PaymentPlanInstallmentSerializer(inst).data,
            }, status=status.HTTP_402_PAYMENT_REQUIRED)

    except Exception as e:
        inst.status = 'failed'
        inst.failure_reason = str(e)
        inst.retry_count += 1
        inst.save()
        logger.error(f"Error charging installment {inst.installment_number} of plan {plan.plan_number}: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def installment_send_receipt(request, installment_id):
    """Send a receipt email for a paid installment."""
    try:
        inst = PaymentPlanInstallment.objects.get(id=installment_id)
    except PaymentPlanInstallment.DoesNotExist:
        return Response({'error': 'Installment not found'}, status=status.HTTP_404_NOT_FOUND)

    if inst.status != 'paid':
        return Response({'error': 'Can only send receipts for paid installments'}, status=status.HTTP_400_BAD_REQUEST)

    email = request.data.get('email')
    if not email:
        return Response({'error': 'Email address is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from .receipts import send_installment_receipt
        send_installment_receipt(inst.plan, inst, email)
        return Response({'status': 'sent'})
    except Exception as e:
        logger.error(f"Error sending installment receipt: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ─── Settings ─────────────────────────────────────────────────────────────────

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def payment_plan_settings(request):
    """Get or update payment plan settings."""
    from crm.models import POSSetting

    if request.method == 'GET':
        return Response({
            'enabled': POSSetting.get_setting('payment_plans_enabled', False),
            'minimum_order_threshold': POSSetting.get_setting('payment_plans_min_threshold', 10000),
        })

    # POST
    if 'enabled' in request.data:
        POSSetting.set_setting(
            'payment_plans_enabled',
            request.data['enabled'],
            setting_type='boolean',
            description='Enable payment plans for orders above threshold',
            category='payment_plans',
            user=request.user,
        )
    if 'minimum_order_threshold' in request.data:
        POSSetting.set_setting(
            'payment_plans_min_threshold',
            request.data['minimum_order_threshold'],
            setting_type='float',
            description='Minimum order total to enable payment plans',
            category='payment_plans',
            user=request.user,
        )

    return Response({
        'enabled': POSSetting.get_setting('payment_plans_enabled', False),
        'minimum_order_threshold': POSSetting.get_setting('payment_plans_min_threshold', 10000),
    })


# ─── Lookup by order ──────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def plan_by_order(request, order_id):
    """Get payment plan for a specific POS order."""
    try:
        plan = PaymentPlan.objects.get(pos_order_id=order_id)
        return Response(PaymentPlanSerializer(plan).data)
    except PaymentPlan.DoesNotExist:
        return Response({'error': 'No payment plan for this order'}, status=status.HTTP_404_NOT_FOUND)
