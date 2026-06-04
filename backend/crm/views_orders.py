import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .woocommerce import WooCommerceAPI
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_woocommerce_order(request, order_id):
    """
    Get a specific order from WooCommerce by ID
    """
    try:
        # Initialize WooCommerce API
        wc_api = WooCommerceAPI()
        
        logger.info(f"Fetching WooCommerce order with ID: {order_id}")
        
        # Make API call to WooCommerce
        response = wc_api.wcapi.get(f"orders/{order_id}")
        
        # Handle 404 gracefully when order is not found
        if response.status_code == 404:
            logger.warning(f"Order with ID {order_id} not found")
            return Response({
                'error': f"Order with ID {order_id} not found"
            }, status=status.HTTP_404_NOT_FOUND)
            
        if not response.ok:
            error_msg = f"Failed to fetch order: Status code {response.status_code}"
            if hasattr(response, 'text'):
                error_msg += f", Response: {response.text}"
            logger.error(error_msg)
            return Response({
                'error': error_msg
            }, status=status.HTTP_400_BAD_REQUEST)
            
        # Return order data
        logger.info(f"Successfully fetched order with ID: {order_id}")
        return Response(response.json(), status=status.HTTP_200_OK)
        
    except RequestException as e:
        error_msg = f"Request error fetching order: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except Exception as e:
        error_msg = f"Error fetching order: {str(e)}"
        logger.error(error_msg)
        return Response({
            'error': error_msg
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
