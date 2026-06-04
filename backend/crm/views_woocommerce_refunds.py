from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_woocommerce_order_refunds(request, order_id):
    """
    Get all refunds for a WooCommerce order with detailed line item information
    
    Args:
        order_id: WooCommerce order ID
        
    Returns:
        List of refunds with detailed line item information showing which items were refunded
    """
    try:
        logger.info(f"Fetching WooCommerce refunds for order {order_id}")
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get all refunds for the order
        refunds = wc_api.get_order_refunds(order_id)
        
        if not refunds:
            return Response({
                'refunds': [],
                'refunded_items': {}
            })
        
        # Dictionary to map line item IDs to refund information
        refunded_items = {}
        detailed_refunds = []
        
        # Process each refund to get detailed information
        for refund_summary in refunds:
            refund_id = refund_summary.get('id')
            
            # Get detailed refund information including line items
            detailed_refund = wc_api.get_order_refund(order_id, refund_id)
            
            if detailed_refund:
                detailed_refunds.append(detailed_refund)
                
                # Process refunded line items
                refund_line_items = detailed_refund.get('line_items', [])
                
                for refunded_item in refund_line_items:
                    # Get the original line item ID (meta data contains reference)
                    item_meta = refunded_item.get('meta_data', [])
                    original_item_id = None
                    
                    # Look for the original line item reference in meta data
                    for meta in item_meta:
                        if meta.get('key') == '_refunded_item_id':
                            original_item_id = meta.get('value')
                            break
                    
                    # If no meta reference, try to match by product_id and name
                    if not original_item_id:
                        # Create a unique key based on product info for matching
                        match_key = f"{refunded_item.get('product_id')}_{refunded_item.get('name', '').replace(' ', '_')}"
                        original_item_id = match_key
                    
                    # Store refund information for this item
                    if original_item_id not in refunded_items:
                        refunded_items[original_item_id] = {
                            'total_refunded_qty': 0,
                            'total_refunded_amount': 0.0,
                            'refund_history': []
                        }
                    
                    # Add this refund to the item's history
                    refund_qty = abs(int(refunded_item.get('quantity', 0)))
                    refund_amount = abs(float(refunded_item.get('total', 0)))
                    
                    refunded_items[original_item_id]['total_refunded_qty'] += refund_qty
                    refunded_items[original_item_id]['total_refunded_amount'] += refund_amount
                    refunded_items[original_item_id]['refund_history'].append({
                        'refund_id': refund_id,
                        'date': detailed_refund.get('date_created', ''),
                        'quantity': refund_qty,
                        'amount': refund_amount,
                        'reason': detailed_refund.get('reason', ''),
                        'product_name': refunded_item.get('name', ''),
                        'product_id': refunded_item.get('product_id')
                    })
        
        logger.info(f"Successfully retrieved {len(detailed_refunds)} refunds for order {order_id}")
        
        return Response({
            'refunds': detailed_refunds,
            'refunded_items': refunded_items,
            'total_refunds': len(detailed_refunds)
        })
        
    except Exception as e:
        logger.error(f"Error fetching WooCommerce refunds for order {order_id}: {str(e)}")
        return Response({
            'error': f'Failed to fetch refunds: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_woocommerce_order_refund(request, order_id, refund_id):
    """
    Get a specific refund for a WooCommerce order
    
    Args:
        order_id: WooCommerce order ID
        refund_id: WooCommerce refund ID
        
    Returns:
        Detailed refund information including line items
    """
    try:
        logger.info(f"Fetching WooCommerce refund {refund_id} for order {order_id}")
        
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        # Get detailed refund information
        refund = wc_api.get_order_refund(order_id, refund_id)
        
        if not refund:
            return Response({
                'error': 'Refund not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        logger.info(f"Successfully retrieved refund {refund_id} for order {order_id}")
        
        return Response(refund)
        
    except Exception as e:
        logger.error(f"Error fetching WooCommerce refund {refund_id} for order {order_id}: {str(e)}")
        return Response({
            'error': f'Failed to fetch refund: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
