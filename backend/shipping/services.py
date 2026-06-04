import requests
import base64
import json
import logging
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from .models import ShippingCarrier, ShippingService, Shipment, ShippingRate, ShipmentPackage

logger = logging.getLogger(__name__)

class ShipStationService:
    """
    Service class for interacting with the ShipStation API
    Documentation: https://www.shipstation.com/docs/api/
    """
    
    def __init__(self):
        self.api_key = settings.SHIPSTATION_API_KEY
        self.api_secret = settings.SHIPSTATION_API_SECRET
        self.base_url = 'https://ssapi.shipstation.com'
        self.auth_header = self._get_auth_header()
        
    def _get_auth_header(self):
        """Generate the authorization header for ShipStation API"""
        auth_string = f"{self.api_key}:{self.api_secret}"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        return {'Authorization': f'Basic {encoded_auth}'}
    
    def _make_request(self, method, endpoint, data=None, params=None):
        """Make a request to the ShipStation API"""
        url = f"{self.base_url}/{endpoint}"
        headers = {**self._get_auth_header(), 'Content-Type': 'application/json'}
        
        try:
            if method.lower() == 'get':
                response = requests.get(url, headers=headers, params=params)
            elif method.lower() == 'post':
                response = requests.post(url, headers=headers, json=data)
            elif method.lower() == 'put':
                response = requests.put(url, headers=headers, json=data)
            elif method.lower() == 'delete':
                response = requests.delete(url, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            return response.json() if response.content else None
        
        except requests.exceptions.RequestException as e:
            logger.error(f"ShipStation API error: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response content: {e.response.content}")
            raise
    
    def sync_carriers(self):
        """Sync carriers from ShipStation to local database"""
        carriers_data = self._make_request('get', 'carriers')
        
        for carrier_data in carriers_data:
            carrier, created = ShippingCarrier.objects.update_or_create(
                code=carrier_data['code'],
                defaults={
                    'name': carrier_data['name'],
                    'is_active': True
                }
            )
            
            logger.info(f"{'Created' if created else 'Updated'} carrier: {carrier.name}")
            
        return ShippingCarrier.objects.filter(is_active=True).count()
    
    def sync_services(self, carrier_code=None):
        """Sync shipping services for a specific carrier or all carriers"""
        carriers = [ShippingCarrier.objects.get(code=carrier_code)] if carrier_code else ShippingCarrier.objects.filter(is_active=True)
        
        service_count = 0
        for carrier in carriers:
            services_data = self._make_request('get', f'carriers/listservices?carrierCode={carrier.code}')
            
            for service_data in services_data:
                service, created = ShippingService.objects.update_or_create(
                    carrier=carrier,
                    code=service_data['code'],
                    defaults={
                        'name': service_data['name'],
                        'domestic': service_data.get('domestic', True),
                        'international': service_data.get('international', False),
                        'is_active': True
                    }
                )
                
                service_count += 1
                logger.info(f"{'Created' if created else 'Updated'} service: {service.name} for carrier {carrier.name}")
        
        return service_count
    
    def sync_packages(self, carrier_code=None):
        """Sync package types for a specific carrier or all carriers"""
        carriers = [ShippingCarrier.objects.get(code=carrier_code)] if carrier_code else ShippingCarrier.objects.filter(is_active=True)
        
        package_count = 0
        for carrier in carriers:
            packages_data = self._make_request('get', f'carriers/listpackages?carrierCode={carrier.code}')
            
            for package_data in packages_data:
                package, created = ShipmentPackage.objects.update_or_create(
                    code=package_data['code'],
                    defaults={
                        'name': package_data['name'],
                        'carrier': carrier,
                        'is_active': True
                    }
                )
                
                package_count += 1
                logger.info(f"{'Created' if created else 'Updated'} package: {package.name} for carrier {carrier.name}")
        
        return package_count
    
    def create_order(self, pos_order):
        """
        Create an order in ShipStation from a POS order
        
        Args:
            pos_order: POSOrder instance
            
        Returns:
            str: ShipStation order ID
        """
        # Get customer information
        customer = pos_order.contact
        
        # Check if we have shipping address in the order
        has_shipping_address = (
            pos_order.shipping_address and 
            pos_order.shipping_city and 
            pos_order.shipping_state and 
            pos_order.shipping_postcode
        )
        
        # Format address information
        if has_shipping_address:
            shipping_address = {
                'name': f"{customer.first_name} {customer.last_name}" if customer else "Customer",
                'street1': pos_order.shipping_address,
                'city': pos_order.shipping_city,
                'state': pos_order.shipping_state,
                'postalCode': pos_order.shipping_postcode,
                'country': pos_order.shipping_country or 'US',
                'phone': customer.phone if customer else "",
                'email': customer.email if customer else ""
            }
        else:
            # Use customer address if no shipping address in order
            shipping_address = {
                'name': f"{customer.first_name} {customer.last_name}" if customer else "Customer",
                'street1': customer.billing_address if customer else "",
                'city': customer.billing_city if customer else "",
                'state': customer.billing_state if customer else "",
                'postalCode': customer.billing_postcode if customer else "",
                'country': 'US',
                'phone': customer.phone if customer else "",
                'email': customer.email if customer else ""
            }
        
        # Format order items
        order_items = []
        for item in pos_order.items.all():
            # Get item dimensions and weight if available
            item_data = {
                'lineItemKey': str(item.id),
                'sku': item.product_id or '',
                'name': item.name,
                'quantity': item.quantity,
                'unitPrice': float(item.price),
                'taxAmount': float(item.tax) if hasattr(item, 'tax') and item.tax else 0.0,
                'shippingAmount': 0.0,  # Will be calculated by ShipStation
                'productId': item.product_id or ''
            }
            
            # Add weight and dimensions if available
            if hasattr(item, 'weight') and item.weight:
                item_data['weight'] = {
                    'value': float(item.weight),
                    'units': 'pounds'
                }
                
            if hasattr(item, 'length') and item.length and hasattr(item, 'width') and item.width and hasattr(item, 'height') and item.height:
                item_data['dimensions'] = {
                    'length': float(item.length),
                    'width': float(item.width),
                    'height': float(item.height),
                    'units': 'inches'
                }
                
            order_items.append(item_data)
        
        # Get shipping info from order if available
        shipping_info = {}
        shipping_cost = 0.0
        
        if pos_order.shipping_info:
            # If shipping_info is stored as a string, parse it
            if isinstance(pos_order.shipping_info, str):
                try:
                    shipping_info = json.loads(pos_order.shipping_info)
                except json.JSONDecodeError:
                    shipping_info = {}
            else:
                shipping_info = pos_order.shipping_info
                
            # Get shipping cost
            shipping_cost = float(pos_order.shipping_cost) if pos_order.shipping_cost else 0.0
        
        # Format order data for ShipStation
        order_data = {
            'orderNumber': pos_order.order_number,
            'orderKey': str(pos_order.id),
            'orderDate': pos_order.created_at.isoformat(),
            'paymentDate': pos_order.created_at.isoformat(),
            'orderStatus': 'awaiting_shipment',
            'customerUsername': customer.email if customer else "",
            'customerEmail': customer.email if customer else "",
            'billTo': shipping_address,  # Use same address for billing
            'shipTo': shipping_address,
            'items': order_items,
            'amountPaid': float(pos_order.total),
            'taxAmount': 0.0,  # Add tax calculation if needed
            'shippingAmount': shipping_cost,
            'customerNotes': pos_order.notes or '',
            'internalNotes': f"Order created from POS system on {timezone.now().strftime('%Y-%m-%d %H:%M')}",
            'gift': False,
            'paymentMethod': 'other'
        }
        
        # Add payment method if available
        if pos_order.payment_method:
            payment_info = pos_order.payment_method
            if isinstance(payment_info, str):
                try:
                    payment_info = json.loads(payment_info)
                    if isinstance(payment_info, dict) and 'type' in payment_info:
                        order_data['paymentMethod'] = payment_info['type']
                except json.JSONDecodeError:
                    pass
            elif isinstance(payment_info, dict) and 'type' in payment_info:
                order_data['paymentMethod'] = payment_info['type']
        
        # Add carrier and service info if available
        if shipping_info and 'carrierCode' in shipping_info:
            order_data['carrierCode'] = shipping_info['carrierCode']
            
        if shipping_info and 'serviceCode' in shipping_info:
            order_data['serviceCode'] = shipping_info['serviceCode']
        
        # Create order in ShipStation
        try:
            response = self._make_request('post', 'orders/createorder', data=order_data)
            shipstation_order_id = response.get('orderId')
            
            # Create or update the shipment record
            shipment, created = Shipment.objects.update_or_create(
                order=pos_order,
                defaults={
                    'shipstation_order_id': shipstation_order_id,
                    'status': 'awaiting_shipment'
                }
            )
            
            # If we have carrier and service info, update the shipment
            if shipping_info:
                if 'carrierCode' in shipping_info:
                    carrier, _ = ShippingCarrier.objects.get_or_create(
                        code=shipping_info['carrierCode'],
                        defaults={'name': shipping_info.get('carrierName', shipping_info['carrierCode'])}
                    )
                    shipment.carrier = carrier
                
                if 'serviceCode' in shipping_info:
                    service, _ = ShippingService.objects.get_or_create(
                        carrier=carrier,
                        code=shipping_info['serviceCode'],
                        defaults={'name': shipping_info.get('serviceName', shipping_info['serviceCode'])}
                    )
                    shipment.service = service
                
                shipment.shipping_cost = shipping_cost
                shipment.save()
            
            logger.info(f"Created order in ShipStation: {shipstation_order_id} for POS order: {pos_order.order_number}")
            return shipstation_order_id
            
        except Exception as e:
            logger.error(f"Error creating order in ShipStation: {str(e)}")
            raise
    
    def get_rates(self, pos_order, carrier_code=None, service_code=None, package_code=None):
        """
        Get shipping rates for an order
        
        Args:
            pos_order: POSOrder instance
            carrier_code: Optional carrier code to filter rates
            service_code: Optional service code to filter rates
            package_code: Optional package code to use for rate calculation
            
        Returns:
            list: Available shipping rates
        """
        # Get customer information
        customer = pos_order.contact
        
        # Check if we have shipping address in the order
        has_shipping_address = (
            pos_order.shipping_address and 
            pos_order.shipping_city and 
            pos_order.shipping_state and 
            pos_order.shipping_postcode
        )
        
        # Format address information
        if has_shipping_address:
            shipping_address = {
                'name': f"{customer.first_name} {customer.last_name}" if customer else "Customer",
                'street1': pos_order.shipping_address,
                'city': pos_order.shipping_city,
                'state': pos_order.shipping_state,
                'postalCode': pos_order.shipping_postcode,
                'country': pos_order.shipping_country or 'US',
                'phone': customer.phone if customer else ""
            }
        else:
            # Use customer address if no shipping address in order
            shipping_address = {
                'name': f"{customer.first_name} {customer.last_name}" if customer else "Customer",
                'street1': customer.billing_address if customer else "",
                'city': customer.billing_city if customer else "",
                'state': customer.billing_state if customer else "",
                'postalCode': customer.billing_postcode if customer else "",
                'country': 'US',
                'phone': customer.phone if customer else ""
            }
        
        # Calculate package dimensions and weight based on items
        items = pos_order.items.all()
        
        # Filter out digital products that don't need shipping
        physical_items = [item for item in items if not (hasattr(item, 'is_digital') and item.is_digital)]
        
        # If no physical items, use default weight
        if not physical_items:
            total_weight = 1.0
            max_length = 12.0
            max_width = 12.0
            max_height = 12.0
        else:
            # Calculate total weight - sum of (item weight * quantity)
            total_weight = sum(
                float(item.weight) * item.quantity 
                for item in physical_items 
                if hasattr(item, 'weight') and item.weight
            )
            
            # If no weights defined, use default
            if total_weight <= 0:
                total_weight = 1.0
                
            # Find largest dimensions for the package
            # This is a simple approach - for more complex packing algorithms, consider using a dedicated package
            max_length = max(
                (float(item.length) for item in physical_items if hasattr(item, 'length') and item.length),
                default=12.0
            )
            max_width = max(
                (float(item.width) for item in physical_items if hasattr(item, 'width') and item.width),
                default=12.0
            )
            max_height = max(
                (float(item.height) for item in physical_items if hasattr(item, 'height') and item.height),
                default=12.0
            )
            
            # Add some padding for packaging materials (10%)
            max_length *= 1.1
            max_width *= 1.1
            max_height *= 1.1
        
        # Use specified package or default
        package = None
        if package_code:
            try:
                package = ShipmentPackage.objects.get(code=package_code)
            except ShipmentPackage.DoesNotExist:
                pass
        
        # Format rate request data
        rate_data = {
            'carrierCode': carrier_code,
            'serviceCode': service_code,
            'packageCode': package.code if package else None,
            'fromPostalCode': settings.SHIPSTATION_FROM_ZIP,
            'toState': shipping_address['state'],
            'toCountry': shipping_address['country'],
            'toPostalCode': shipping_address['postalCode'],
            'toCity': shipping_address['city'],
            'weight': {
                'value': total_weight,
                'units': 'pounds'
            },
            'dimensions': {
                'units': 'inches',
                'length': package.length if package and hasattr(package, 'length') and package.length else max_length,
                'width': package.width if package and hasattr(package, 'width') and package.width else max_width,
                'height': package.height if package and hasattr(package, 'height') and package.height else max_height
            },
            'confirmation': 'none',
            'residential': True
        }
        
        try:
            response = self._make_request('post', 'shipments/getrates', data=rate_data)
            
            # Save rates to database
            saved_rates = []
            for rate_info in response:
                carrier, _ = ShippingCarrier.objects.get_or_create(
                    code=rate_info['carrierCode'],
                    defaults={'name': rate_info['carrierCode']}
                )
                
                service, _ = ShippingService.objects.get_or_create(
                    carrier=carrier,
                    code=rate_info['serviceCode'],
                    defaults={'name': rate_info['serviceName']}
                )
                
                rate, created = ShippingRate.objects.update_or_create(
                    order=pos_order,
                    carrier=carrier,
                    service=service,
                    defaults={
                        'rate': rate_info['shipmentCost'],
                        'delivery_days': rate_info.get('deliveryDays'),
                        'is_guaranteed': rate_info.get('guaranteedDelivery', False),
                        'shipstation_rate_id': rate_info.get('rateId')
                    }
                )
                
                saved_rates.append(rate)
            
            return saved_rates
            
        except Exception as e:
            logger.error(f"Error getting shipping rates: {str(e)}")
            raise
    
    def create_label(self, shipment_id, rate_id=None, service_code=None, carrier_code=None):
        """
        Create a shipping label for a shipment
        
        Args:
            shipment_id: Shipment ID to create label for
            rate_id: Optional rate ID to use for label creation
            service_code: Optional service code to use for label creation
            carrier_code: Optional carrier code to use for label creation
            
        Returns:
            dict: Label information
        """
        try:
            shipment = Shipment.objects.get(id=shipment_id)
            pos_order = shipment.order
            
            # Get rate if provided
            if rate_id:
                try:
                    rate = ShippingRate.objects.get(id=rate_id)
                    carrier_code = rate.carrier.code
                    service_code = rate.service.code
                except ShippingRate.DoesNotExist:
                    pass
            
            # Ensure we have carrier and service codes
            if not carrier_code or not service_code:
                raise ValueError("Carrier code and service code are required")
            
            # Get package information
            package = shipment.package
            
            # Calculate package dimensions and weight based on items
            items = pos_order.items.all()
            
            # Filter out digital products that don't need shipping
            physical_items = [item for item in items if not (hasattr(item, 'is_digital') and item.is_digital)]
            
            # If no physical items, use default weight
            if not physical_items:
                total_weight = 1.0
                max_length = 12.0
                max_width = 12.0
                max_height = 12.0
            else:
                # Calculate total weight - sum of (item weight * quantity)
                total_weight = sum(
                    float(item.weight) * item.quantity 
                    for item in physical_items 
                    if hasattr(item, 'weight') and item.weight
                )
                
                # If no weights defined, use default
                if total_weight <= 0:
                    total_weight = 1.0
                    
                # Find largest dimensions for the package
                max_length = max(
                    (float(item.length) for item in physical_items if hasattr(item, 'length') and item.length),
                    default=12.0
                )
                max_width = max(
                    (float(item.width) for item in physical_items if hasattr(item, 'width') and item.width),
                    default=12.0
                )
                max_height = max(
                    (float(item.height) for item in physical_items if hasattr(item, 'height') and item.height),
                    default=12.0
                )
                
                # Add some padding for packaging materials (10%)
                max_length *= 1.1
                max_width *= 1.1
                max_height *= 1.1
            
            # Format shipping address
            shipping_address = {
                'name': f"{pos_order.contact.first_name} {pos_order.contact.last_name}" if pos_order.contact else "Customer",
                'street1': pos_order.shipping_address or (pos_order.contact.billing_address if pos_order.contact else ""),
                'city': pos_order.shipping_city or (pos_order.contact.billing_city if pos_order.contact else ""),
                'state': pos_order.shipping_state or (pos_order.contact.billing_state if pos_order.contact else ""),
                'postalCode': pos_order.shipping_postcode or (pos_order.contact.billing_postcode if pos_order.contact else ""),
                'country': pos_order.shipping_country or 'US',
                'phone': pos_order.contact.phone if pos_order.contact else ""
            }
            
            # Format label data
            label_data = {
                'carrierCode': carrier_code,
                'serviceCode': service_code,
                'packageCode': package.code if package else 'package',
                'confirmation': 'none',
                'shipDate': timezone.now().strftime('%Y-%m-%d'),
                'weight': {
                    'value': total_weight,
                    'units': 'pounds'
                },
                'dimensions': {
                    'units': 'inches',
                    'length': package.length if package and hasattr(package, 'length') and package.length else max_length,
                    'width': package.width if package and hasattr(package, 'width') and package.width else max_width,
                    'height': package.height if package and hasattr(package, 'height') and package.height else max_height
                },
                'shipFrom': {
                    'name': settings.SHIPSTATION_FROM_NAME,
                    'company': settings.SHIPSTATION_FROM_COMPANY,
                    'street1': settings.SHIPSTATION_FROM_STREET1,
                    'street2': settings.SHIPSTATION_FROM_STREET2,
                    'city': settings.SHIPSTATION_FROM_CITY,
                    'state': settings.SHIPSTATION_FROM_STATE,
                    'postalCode': settings.SHIPSTATION_FROM_ZIP,
                    'country': settings.SHIPSTATION_FROM_COUNTRY,
                    'phone': settings.SHIPSTATION_FROM_PHONE,
                },
                'shipTo': shipping_address,
                'testLabel': settings.SHIPSTATION_TEST_MODE
            }
            
            response = self._make_request('post', 'shipments/createlabel', data=label_data)
            
            # Update shipment with label information
            shipment.shipstation_shipment_id = response.get('shipmentId')
            shipment.tracking_number = response.get('trackingNumber')
            shipment.shipping_cost = response.get('shipmentCost', 0.0)
            shipment.insurance_cost = response.get('insuranceCost', 0.0)
            shipment.label_url = response.get('labelData')
            shipment.status = 'shipped'
            shipment.ship_date = datetime.now().date()
            
            # Update the POS order with tracking information
            pos_order.tracking_number = shipment.tracking_number
            pos_order.shipping_status = 'shipped'
            pos_order.save()
            
            # Save the shipment
            shipment.save()
            
            return {
                'tracking_number': shipment.tracking_number,
                'label_url': shipment.label_url,
                'shipping_cost': shipment.shipping_cost,
                'carrier': carrier_code,
                'service': service_code
            }
            
        except Exception as e:
            logger.error(f"Error creating shipping label: {str(e)}")
            raise
    
    def track_shipment(self, tracking_number, carrier_code):
        """
        Track a shipment using its tracking number
        
        Args:
            tracking_number: Tracking number
            carrier_code: Carrier code
            
        Returns:
            dict: Tracking information
        """
        try:
            endpoint = f'shipments/track?carrierCode={carrier_code}&trackingNumber={tracking_number}'
            return self._make_request('get', endpoint)
        except Exception as e:
            logger.error(f"Error tracking shipment: {str(e)}")
            raise
    
    def void_label(self, shipment_id):
        """
        Void a shipping label
        
        Args:
            shipment_id: Shipment ID
            
        Returns:
            bool: True if successful
        """
        try:
            shipment = Shipment.objects.get(id=shipment_id)
            
            if not shipment.shipstation_shipment_id:
                raise ValueError("No ShipStation shipment ID found")
            
            response = self._make_request('post', 'shipments/voidlabel', data={
                'shipmentId': shipment.shipstation_shipment_id
            })
            
            if response.get('approved', False):
                shipment.status = 'awaiting_shipment'
                shipment.tracking_number = None
                shipment.label_url = None
                shipment.ship_date = None
                shipment.save()
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error voiding label: {str(e)}")
            raise
    
    def get_order_status(self, shipstation_order_id):
        """
        Get the status of an order from ShipStation
        
        Args:
            shipstation_order_id: ShipStation order ID
            
        Returns:
            dict: Order information
        """
        try:
            return self._make_request('get', f'orders/{shipstation_order_id}')
        except Exception as e:
            logger.error(f"Error getting order status: {str(e)}")
            raise
