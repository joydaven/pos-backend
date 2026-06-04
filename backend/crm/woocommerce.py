from woocommerce import API
import logging
import json
import requests
from requests.exceptions import RequestException, Timeout
import time
from functools import wraps
from collections import defaultdict
from django.conf import settings
import os
from .config import (
    WOO_API_URL,
    WOO_CONSUMER_KEY,
    WOO_CONSUMER_SECRET
)

# Set up logging to file
log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'woocommerce.log')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def retry_on_error(max_retries=3, delay=1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (RequestException, Timeout) as e:
                    retries += 1
                    if retries == max_retries:
                        raise
                    logger.warning(f"Request failed, retrying ({retries}/{max_retries}): {str(e)}")
                    time.sleep(delay * retries)  # Exponential backoff
            return func(*args, **kwargs)
        return wrapper
    return decorator

class WooCommerceAPI:
    def __init__(self, url=None, consumer_key=None, consumer_secret=None):
        # Use the base URL without /wp-json/wc/v3 as it's added by the API class
        self.base_url = url or WOO_API_URL or ''
        self.consumer_key = consumer_key or WOO_CONSUMER_KEY or ''
        self.consumer_secret = consumer_secret or WOO_CONSUMER_SECRET or ''
        
        # In-memory product cache to avoid duplicate API calls within the same request
        # Key: product_id (int), Value: API response dict
        self._product_cache = {}
        self._variation_cache = {}
        self._sku_cache = {}
        self._search_cache = {}
        self._atum_inventory_cache = {}  # Key: "product_id:location_name", Value: inventory_id or None
        
        logger.info(f"Initializing WooCommerce API with URL: {self.base_url}")
        
        # Only log partial keys for security
        if self.consumer_key:
            masked_key = self.consumer_key[:4] + '*' * (len(self.consumer_key) - 4) if len(self.consumer_key) > 4 else '****'
            logger.info(f"Consumer Key: {masked_key}")
        else:
            logger.warning("Consumer Key is empty or not set")
            
        if self.consumer_secret:
            masked_secret = self.consumer_secret[:4] + '*' * (len(self.consumer_secret) - 4) if len(self.consumer_secret) > 4 else '****'
            logger.info(f"Consumer Secret: {masked_secret}")
        else:
            logger.warning("Consumer Secret is empty or not set")
            
        try:
            # Use the API version from environment or default to wc/v3
            api_version = os.environ.get('WOOCOMMERCE_API_VERSION', 'wc/v3')
            logger.info(f"Using WooCommerce API version: {api_version}")
            
            self.wcapi = API(
                url=self.base_url,
                consumer_key=self.consumer_key,
                consumer_secret=self.consumer_secret,
                version=api_version,
                timeout=120,  # Increased from 60 to 120 seconds
                verify_ssl=False  # For testing only
            )
            self._test_connection(silent=True)
            logger.info("WooCommerce API initialized successfully")
        except Exception as e:
            error_msg = f"Failed to initialize WooCommerce API: {str(e)}"
            logger.error(error_msg)
            raise Exception(error_msg)

    @retry_on_error(max_retries=3)
    def _test_connection(self, silent=False):
        """Test the WooCommerce API connection."""
        try:
            response = self.wcapi.get("products", params={"per_page": 1})
            if not response.ok:
                logger.error(f"WooCommerce API connection test failed. Status code: {response.status_code}, Response: {response.text}")
                raise Exception(f"Failed to connect to WooCommerce API. Status code: {response.status_code}")
            logger.info("WooCommerce API connection test successful")
            return True
        except Exception as e:
            logger.error(f"WooCommerce API connection error: {str(e)}")
            if not silent:
                raise

    @retry_on_error(max_retries=3)
    def get_products(self, page=1, per_page=100):
        """Get products with pagination support and better error handling."""
        try:
            response = self.wcapi.get("products", params={
                "per_page": per_page,
                "page": page,
                "status": "publish"
            })
            
            if not response.ok:
                error_msg = f"Failed to get products. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            # Get headers for pagination
            total_items = int(response.headers.get('X-WP-Total', 0))
            total_pages = int(response.headers.get('X-WP-TotalPages', 0))
            
            # Parse response data
            try:
                data = response.json()
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                raise Exception("Invalid JSON response from WooCommerce API")
            
            if not isinstance(data, list):
                logger.error(f"Invalid response format. Expected list but got: {type(data)}")
                raise Exception("Invalid response format from WooCommerce API")
            
            logger.info(f"Retrieved {len(data)} products. Total pages: {total_pages}, Total items: {total_items}")
            
            return {
                'data': data,
                'total': total_items,
                'total_pages': total_pages,
                'current_page': page
            }
            
        except Exception as e:
            logger.error(f"Error getting products: {str(e)}")
            raise

    @retry_on_error(max_retries=3)
    def search_products(self, search_term):
        """
        Search for products by name or other attributes.
        
        Args:
            search_term: The search term to look for in product names
            
        Returns:
            Dictionary containing search results or None on error
        """
        try:
            # Encode the search term for URL
            params = {
                "search": search_term,
                "per_page": 10  # Limit results to avoid performance issues
            }
            
            response = self.wcapi.get("products", params=params)
            
            if not response.ok:
                error_msg = f"Failed to search products. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return None
            
            # Get headers for pagination
            total_items = int(response.headers.get('X-WP-Total', 0))
            total_pages = int(response.headers.get('X-WP-TotalPages', 0))
            
            # Parse response data
            try:
                data = response.json()
                
                if not isinstance(data, list):
                    logger.error(f"Invalid response format. Expected list but got: {type(data)}")
                    return None
                
                logger.info(f"Found {len(data)} products matching '{search_term}'")
                
                return {
                    'data': data,
                    'total': total_items,
                    'total_pages': total_pages,
                    'current_page': 1
                }
                
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                return None
                
        except Exception as e:
            logger.error(f"Error searching products: {str(e)}")
            return None

    @retry_on_error(max_retries=3)
    def get_customers(self, page=1, per_page=100, include_all_roles=True):
        try:
            logger.info(f"Fetching customers from WooCommerce (page {page}, per_page {per_page}, include_all_roles={include_all_roles})")
            
            # Build parameters - include all user roles to get complete customer list
            params = {"per_page": per_page, "page": page}
            if include_all_roles:
                params["role"] = "all"
            
            response = self.wcapi.get("customers", params=params)
            logger.info(f"Customers response status: {response.status_code}")
            logger.debug(f"Customers response: {response.text[:500]}")  # Log first 500 chars of response
            
            if response.status_code == 200:
                customers = response.json()
                
                # Get pagination information from headers
                total_items = int(response.headers.get('X-WP-Total', 0))
                total_pages = int(response.headers.get('X-WP-TotalPages', 0))
                
                logger.info(f"Retrieved {len(customers)} customers. Page {page}/{total_pages}, Total: {total_items}")
                
                return {
                    'data': customers,
                    'total': total_items,
                    'total_pages': total_pages,
                    'current_page': page
                }
            else:
                logger.error(f"Failed to get customers: {response.status_code} - {response.text}")
                return None
        except RequestException as e:
            logger.error(f"Network error getting customers: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error fetching customers: {str(e)}")
            return None

    @retry_on_error(max_retries=3)
    def get_staff_users(self, page=1, per_page=100):
        """
        Fetch WordPress admin/staff users (administrator, shop_manager, editor, author roles)
        from WooCommerce API to sync into POS system
        """
        try:
            logger.info(f"Fetching staff users from WooCommerce (page {page}, per_page {per_page})")
            
            # Staff roles to sync into POS system
            staff_roles = ['administrator', 'shop_manager', 'editor', 'author']
            
            all_staff = []
            
            # Query each staff role separately since WooCommerce API doesn't support multiple roles in one call
            for role in staff_roles:
                params = {"per_page": per_page, "page": page, "role": role}
                
                response = self.wcapi.get("customers", params=params)
                logger.info(f"Staff users ({role}) response status: {response.status_code}")
                
                if response.status_code == 200:
                    users = response.json()
                    logger.info(f"Retrieved {len(users)} users with role '{role}'")
                    
                    # Add role information to each user
                    for user in users:
                        user['wordpress_role'] = role
                    
                    all_staff.extend(users)
                else:
                    logger.error(f"Failed to get staff users with role {role}: {response.status_code} - {response.text}")
            
            # Remove duplicates (users might have multiple roles)
            seen_ids = set()
            unique_staff = []
            for user in all_staff:
                if user['id'] not in seen_ids:
                    seen_ids.add(user['id'])
                    unique_staff.append(user)
            
            logger.info(f"Retrieved {len(unique_staff)} unique staff users total")
            
            return {
                'data': unique_staff,
                'total': len(unique_staff),
                'total_pages': 1,  # We're getting all staff in one call
                'current_page': page
            }
            
        except Exception as e:
            logger.error(f"Error fetching staff users: {str(e)}")
            return None

    @retry_on_error(max_retries=3)
    def get_customer_points(self, customer_id):
        """
        Get YITH Points and Rewards data for a specific customer by ID.
        
        Args:
            customer_id: The WooCommerce customer ID
            
        Returns:
            Dictionary containing points data or None if not found
            Format: {
                'points_collected': points_balance, 
                'points_to_redeem': redeemable_points,
                'rank': customer_rank,
                'points_value': monetary_value
            }
        """
        try:
            import requests
            from requests.auth import HTTPBasicAuth

            base_url = self.base_url
            if '/wp-json' in base_url:
                base_url = base_url.split('/wp-json')[0]

            # Primary path: custom endpoint registered in divi-child/functions.php
            points_url = f"{base_url}/wp-json/custom/v1/points/{customer_id}"
            logger.info(f"Fetching points from custom endpoint: {points_url}")
            try:
                response = requests.get(
                    points_url,
                    auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                    verify=False,
                    timeout=10
                )

                if response.ok:
                    data = response.json()
                    logger.info(f"Custom points endpoint returned for customer {customer_id}: {data}")

                    points_collected = int(data.get('points_collected', 0))
                    usable_points = int(data.get('usable_points', data.get('total_points', 0)))
                    level = data.get('level', 0)

                    return {
                        'points_collected': points_collected,
                        'points_to_redeem': usable_points,
                        'rank': str(level) if level else "1",
                        'points_value': float(usable_points)
                    }
                else:
                    logger.warning(
                        f"Custom points endpoint returned {response.status_code} for customer {customer_id}: "
                        f"{response.text[:300]}"
                    )
            except Exception as e:
                logger.warning(f"Custom points endpoint failed for customer {customer_id}: {e}")

            # Fallback: direct WordPress DB query via SSH tunnel
            try:
                from .views_woocommerce_points import get_wordpress_database_connection
                logger.info(f"Falling back to direct DB query for YITH points (customer {customer_id})")
                with get_wordpress_database_connection() as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT meta_key, meta_value
                            FROM wp_usermeta
                            WHERE user_id = %s
                            AND meta_key IN (
                                '_ywpar_user_total_points',
                                '_ywpar_user_total_earned_points',
                                '_ywpar_points_collected',
                                '_ywpar_rank',
                                '_ywpar_total_earned'
                            )
                            """,
                            (customer_id,)
                        )
                        rows = cursor.fetchall()

                        if rows:
                            db_points = {}
                            for row in rows:
                                db_points[row['meta_key']] = row['meta_value']

                            db_points_to_redeem = int(float(db_points.get('_ywpar_user_total_points', 0)))
                            db_points_collected = int(float(db_points.get('_ywpar_points_collected', db_points.get('_ywpar_total_earned', db_points.get('_ywpar_user_total_earned_points', 0)))))
                            db_rank = db_points.get('_ywpar_rank', '1')

                            logger.info(f"Direct DB: customer {customer_id} has {db_points_to_redeem} redeemable points, {db_points_collected} collected, rank {db_rank}")
                            return {
                                'points_collected': db_points_collected,
                                'points_to_redeem': db_points_to_redeem,
                                'rank': str(db_rank),
                                'points_value': float(db_points_to_redeem)
                            }
                        else:
                            logger.info(f"No YITH points meta found in wp_usermeta for customer {customer_id}")
            except Exception as e:
                logger.warning(f"Direct WordPress DB fallback failed for customer {customer_id}: {e}")

            logger.warning(f"No YITH points data found for customer {customer_id} after custom endpoint + DB fallback")
            return {
                'points_collected': 0,
                'points_to_redeem': 0,
                'rank': "1",
                'points_value': 0
            }

        except Exception as e:
            logger.error(f"Error getting points for customer {customer_id}: {e}")
            return None
    
    @retry_on_error(max_retries=3)
    def redeem_customer_points(self, customer_id, points_to_redeem):
        """
        Redeem customer points by directly updating the wc_points_balance meta field.
        
        Since YITH Points & Rewards doesn't have REST API endpoints, we directly
        update the customer's points balance using WooCommerce REST API.
        
        Args:
            customer_id: WooCommerce customer ID
            points_to_redeem: Number of points to redeem
            
        Returns:
            dict: {
                'success': bool,
                'points_redeemed': int,
                'remaining_points': int,
                'error': str (if failed)
            }
        """
        logger.info(f"Attempting to redeem {points_to_redeem} points for customer {customer_id}")
        
        try:
            # First, get current points balance
            current_points_data = self.get_customer_points(customer_id)
            if not current_points_data:
                return {
                    'success': False,
                    'error': 'Could not retrieve current points balance'
                }
            
            current_points = current_points_data.get('points_to_redeem', 0)
            
            # Calculate new points balance
            new_points_balance = current_points - points_to_redeem
            
            if new_points_balance < 0:
                return {
                    'success': False,
                    'error': f'Insufficient points. Available: {current_points}, Requested: {points_to_redeem}'
                }
            
            # Try multiple approaches to actually update the points balance
            logger.info(f"Updating customer {customer_id} points balance from {current_points} to {new_points_balance}")
            
            # Approach 1: WordPress Users API with multiple meta keys
            try:
                logger.info(f"Attempting WordPress Users API update for user {customer_id}")
                
                # Try multiple meta keys that YITH might use
                meta_keys_to_try = [
                    '_ywpar_user_total_points',
                    'wc_points_balance', 
                    '_yith_ywpar_customer_total_points',
                    'ywpar_user_total_points'
                ]
                
                for meta_key in meta_keys_to_try:
                    logger.info(f"Trying WordPress Users API with meta key: {meta_key}")
                    
                    response = requests.put(
                        f"{self.base_url}/wp-json/wp/v2/users/{customer_id}",
                        json={
                            'meta': {
                                meta_key: str(new_points_balance)
                            }
                        },
                        auth=(self.consumer_key, self.consumer_secret),
                        headers={'Content-Type': 'application/json'},
                        timeout=30,
                        verify=False
                    )
                    
                    logger.info(f"WordPress Users API response for {meta_key}: {response.status_code} - {response.text}")
                    
                    if response.status_code == 200:
                        logger.info(f"Successfully updated {meta_key} via WordPress Users API")
                        return {
                            'success': True,
                            'points_redeemed': points_to_redeem,
                            'remaining_points': new_points_balance,
                            'message': f"Successfully redeemed {points_to_redeem} points"
                        }
                        
            except Exception as e:
                logger.error(f"WordPress Users API error: {str(e)}")
            
            # Approach 2: Direct database update via custom WordPress endpoint
            try:
                logger.info(f"Attempting direct database update approach")
                
                # Try a custom endpoint that might exist or create one
                response = requests.post(
                    f"{self.base_url}/wp-json/custom/v1/update-points",
                    json={
                        'user_id': customer_id,
                        'points': new_points_balance
                    },
                    auth=(self.consumer_key, self.consumer_secret),
                    headers={'Content-Type': 'application/json'},
                    timeout=30,
                    verify=False
                )
                
                logger.info(f"Custom endpoint response: {response.status_code} - {response.text}")
                
                if response.status_code == 200:
                    response_data = response.json()
                    if response_data.get('success'):
                        logger.info(f"Successfully updated points via custom endpoint for customer {customer_id}")
                        return {
                            'success': True,
                            'points_redeemed': points_to_redeem,
                            'remaining_points': new_points_balance,
                            'message': f"Successfully redeemed {points_to_redeem} points"
                        }
                
            except Exception as e:
                logger.error(f"Custom endpoint error: {str(e)}")
            
            # Return failure if all approaches failed
            logger.error(f"All approaches failed to update points balance for customer {customer_id}")
            return {
                'success': False,
                'error': f'Points redemption failed - unable to update balance in WordPress. All API approaches returned errors.',
                'debug_info': f'Tried WordPress Users API with multiple meta keys, all failed'
            }
            
        except Exception as e:
            logger.error(f"Error redeeming points for customer {customer_id}: {str(e)}")
            return {
                'success': False,
                'error': f"Failed to redeem points: {str(e)}"
            }
    
    def set_customer_points(self, customer_id, points_balance):
        """
        Set customer's YITH points balance directly (for syncing fractional points)
        
        Args:
            customer_id: WooCommerce customer ID
            points_balance: Integer points balance to set
            
        Returns:
            dict: {'success': bool, 'error': str (if failed)}
        """
        logger.info(f"Setting points balance for customer {customer_id} to {points_balance}")
        
        try:
            # Try WordPress Users API with multiple meta keys
            meta_keys_to_try = [
                '_ywpar_user_total_points',
                'wc_points_balance',
                '_yith_ywpar_customer_total_points',
                'ywpar_user_total_points'
            ]
            
            for meta_key in meta_keys_to_try:
                try:
                    response = requests.put(
                        f"{self.base_url}/wp-json/wp/v2/users/{customer_id}",
                        json={
                            'meta': {
                                meta_key: str(points_balance)
                            }
                        },
                        auth=(self.consumer_key, self.consumer_secret),
                        headers={'Content-Type': 'application/json'},
                        timeout=30,
                        verify=False
                    )
                    
                    if response.status_code == 200:
                        logger.info(f"Successfully set {meta_key} to {points_balance} via WordPress Users API")
                        return {
                            'success': True,
                            'points_balance': points_balance,
                            'meta_key': meta_key
                        }
                        
                except Exception as e:
                    logger.debug(f"Failed to set {meta_key}: {str(e)}")
                    continue
            
            logger.warning(f"All meta key attempts failed for customer {customer_id}")
            return {
                'success': False,
                'error': 'Unable to set points balance in YITH'
            }
            
        except Exception as e:
            logger.error(f"Error setting points balance: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_wordpress_user_id_from_customer(self, customer_id):
        """
        Get WordPress user ID from WooCommerce customer ID.
        WooCommerce customers are WordPress users, so we need the WP user ID for direct meta updates.
        """
        try:
            # Get customer data from WooCommerce
            response = self.wcapi.get(f"customers/{customer_id}")
            if response.status_code == 200:
                customer_data = response.json()
                # WooCommerce customer ID should match WordPress user ID
                return customer_data.get('id')
            else:
                logger.error(f"Failed to get customer {customer_id}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error getting WordPress user ID for customer {customer_id}: {str(e)}")
            return None
    
    def get_customer_by_id(self, customer_id):
        """
        Get a single customer by ID.
        
        Args:
            customer_id: The WooCommerce customer ID
            
        Returns:
            Dictionary containing customer data or None if not found
        """
        try:
            logger.info(f"Fetching customer with ID {customer_id} from WooCommerce")
            response = self.wcapi.get(f"customers/{customer_id}")
            
            if not response.ok:
                # Handle 404 gracefully - customer might not exist
                if response.status_code == 404:
                    logger.warning(f"Customer with ID {customer_id} not found in WooCommerce")
                    return None
                
                error_msg = f"Failed to get customer {customer_id}. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            # Parse response data
            try:
                data = response.json()
                logger.info(f"Successfully retrieved customer with ID {customer_id}")
                return data
            except Exception as e:
                logger.error(f"Failed to parse JSON response for customer {customer_id}: {str(e)}")
                raise Exception("Invalid JSON response from WooCommerce API")
            
        except Exception as e:
            logger.error(f"Error getting customer {customer_id}: {str(e)}")
            return None
    
    @retry_on_error(max_retries=3)
    def get_all_customers(self, batch_size=100, include_all_roles=True):
        """
        Get all customers using pagination.
        Returns a list of all customers from all pages.
        
        Args:
            batch_size: Number of customers per page
            include_all_roles: If True, includes all user roles (not just 'customer' role)
        """
        try:
            all_customers = []
            first_page = self.get_customers(page=1, per_page=batch_size, include_all_roles=include_all_roles)
            
            if not first_page or 'total' not in first_page:
                logger.error("Failed to get initial customer data")
                return []
                
            all_customers.extend(first_page['data'])
            processed = len(first_page['data'])
            logger.info(f"Processing page 1/{first_page['total_pages']} - {processed}/{first_page['total']} customers")
            
            # Process remaining pages
            for page in range(2, first_page['total_pages'] + 1):
                result = self.get_customers(page=page, per_page=batch_size, include_all_roles=include_all_roles)
                if result and result.get('data'):
                    all_customers.extend(result['data'])
                    processed += len(result['data'])
                    logger.info(f"Processing page {page}/{first_page['total_pages']} - {processed}/{first_page['total']} customers")
            
            logger.info(f"Retrieved all {len(all_customers)} customers")
            return all_customers
                    
        except Exception as e:
            logger.error(f"Error in get_all_customers: {str(e)}")
            return []

    @retry_on_error(max_retries=3)
    def get_orders(self):
        try:
            logger.info("Fetching orders from WooCommerce")
            response = self.wcapi.get("orders", params={"per_page": 100})
            logger.info(f"Orders response status: {response.status_code}")
            logger.debug(f"Orders response: {response.text[:500]}")  # Log first 500 chars of response
            
            if response.status_code == 200:
                orders = response.json()
                logger.info(f"Retrieved {len(orders)} orders")
                return orders
            else:
                logger.error(f"Failed to get orders: {response.status_code} - {response.text}")
                return []
        except RequestException as e:
            logger.error(f"Network error getting orders: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Error getting orders: {str(e)}")
            return []

    @retry_on_error(max_retries=3)
    def get_order(self, order_id):
        """
        Get a single order from WooCommerce by ID
        
        Args:
            order_id: The WooCommerce order ID
            
        Returns:
            Dictionary containing order data or None if not found
        """
        try:
            logger.info(f"Getting order {order_id} from WooCommerce")
            response = self.wcapi.get(f"orders/{order_id}")
            logger.info(f"Order response status: {response.status_code}")
            
            if response.status_code == 200:
                order = response.json()
                logger.info(f"Retrieved order {order_id}: {order.get('number', 'Unknown')}")
                return order
            else:
                logger.error(f"Failed to get order {order_id}: {response.status_code} - {response.text}")
                return None
        except RequestException as e:
            logger.error(f"Network error getting order {order_id}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error getting order {order_id}: {str(e)}")
            return None

    @retry_on_error(max_retries=3)
    def get_order_refunds(self, order_id):
        """
        Get all refunds for a specific order from WooCommerce
        
        Args:
            order_id: The WooCommerce order ID
            
        Returns:
            List of refund dictionaries containing refund details including line items
        """
        try:
            logger.info(f"Getting refunds for order {order_id} from WooCommerce")
            response = self.wcapi.get(f"orders/{order_id}/refunds")
            logger.info(f"Refunds response status: {response.status_code}")
            
            if response.status_code == 200:
                refunds = response.json()
                logger.info(f"Retrieved {len(refunds)} refunds for order {order_id}")
                return refunds
            else:
                logger.error(f"Failed to get refunds for order {order_id}: {response.status_code} - {response.text}")
                return []
        except RequestException as e:
            logger.error(f"Network error getting refunds for order {order_id}: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Error getting refunds for order {order_id}: {str(e)}")
            return []

    @retry_on_error(max_retries=3)
    def get_order_refund(self, order_id, refund_id):
        """
        Get a specific refund for an order from WooCommerce
        
        Args:
            order_id: The WooCommerce order ID
            refund_id: The WooCommerce refund ID
            
        Returns:
            Dictionary containing refund details including line items or None on error
        """
        try:
            logger.info(f"Getting refund {refund_id} for order {order_id} from WooCommerce")
            response = self.wcapi.get(f"orders/{order_id}/refunds/{refund_id}")
            logger.info(f"Refund response status: {response.status_code}")
            
            if response.status_code == 200:
                refund = response.json()
                logger.info(f"Retrieved refund {refund_id} for order {order_id}: ${refund.get('total', '0')}")
                return refund
            else:
                logger.error(f"Failed to get refund {refund_id} for order {order_id}: {response.status_code} - {response.text}")
                return None
        except RequestException as e:
            logger.error(f"Network error getting refund {refund_id} for order {order_id}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error getting refund {refund_id} for order {order_id}: {str(e)}")
            return None

    @retry_on_error(max_retries=3)
    def get_product_variations(self, product_id, force_fresh=False):
        """Get variations for a specific product."""
        # Check variation list cache first
        cache_key = int(product_id) if str(product_id).isdigit() else str(product_id)
        if not force_fresh and cache_key in self._variation_cache:
            logger.info(f"[CACHE HIT] Variations for product {product_id} from cache")
            return self._variation_cache[cache_key]
        try:
            logger.info(f"Fetching variations for product ID: {product_id}")
            
            # Cache-busting parameters for fresh data
            params = {"per_page": 100}
            if force_fresh:
                import time
                params.update({
                    "nocache": int(time.time()),
                    "_": int(time.time() * 1000),  # Additional cache buster
                    "force_fresh": "true"
                })
                logger.info(f"🔄 Using cache-busting parameters for fresh variation data")
            
            response = self.wcapi.get(f"products/{product_id}/variations", params=params)
            
            if not response.ok:
                # If it's a 404 error, it might mean the product doesn't have variations
                if response.status_code == 404:
                    logger.info(f"No variations found for product ID: {product_id}")
                    return []
                
                error_msg = f"Failed to get variations for product ID {product_id}. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                raise Exception(error_msg)
            
            try:
                variations = response.json()
                logger.info(f"Retrieved {len(variations)} variations for product ID: {product_id}")
                self._variation_cache[cache_key] = variations
                return variations
            except Exception as e:
                logger.error(f"Failed to parse JSON response for variations: {str(e)}")
                raise Exception("Invalid JSON response from WooCommerce API")
                
        except Exception as e:
            logger.error(f"Error getting variations for product ID {product_id}: {str(e)}")
            return []
    
    @retry_on_error(max_retries=3)
    def get_product_variation(self, variation_id):
        """Get a specific product variation by its ID.
        
        Note: WooCommerce API doesn't have a direct endpoint for getting a variation by ID alone.
        We need to get the parent product ID first, then fetch the specific variation.
        For bundle items, we'll try to get the variation data directly from the products endpoint.
        """
        # Check cache first — keyed by variation_id in the product cache
        v_cache_key = int(variation_id) if str(variation_id).isdigit() else str(variation_id)
        if v_cache_key in self._product_cache:
            logger.info(f"[CACHE HIT] Variation {variation_id} from product cache")
            return {'status': 'success', 'data': self._product_cache[v_cache_key]}
        try:
            logger.info(f"Fetching variation details for variation ID: {variation_id}")
            
            # Try to get the variation as a product first (WooCommerce stores variations as products)
            response = self.wcapi.get(f"products/{variation_id}")
            
            if response.ok:
                variation_data = response.json()
                logger.info(f"Retrieved variation data for ID: {variation_id}")
                self._product_cache[v_cache_key] = variation_data
                return {'status': 'success', 'data': variation_data}
            else:
                error_msg = f"Failed to get variation ID {variation_id}. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {'status': 'error', 'message': error_msg}
                
        except Exception as e:
            logger.error(f"Error getting variation ID {variation_id}: {str(e)}")
            return {'status': 'error', 'message': str(e)}
    
    def prefetch_products_for_order(self, pos_order_items):
        """
        Pre-fetch all product, variation, and ATUM inventory data needed for order creation
        using parallel threads. This populates the in-memory caches so the per-item loop
        only does cache hits instead of sequential API calls.
        
        For a bundle with 5 items, this reduces ~15-25 sequential API calls (15-50s)
        down to ~3-5 parallel batches (3-8s).
        
        Two-wave strategy:
          Wave 1: Fetch all products, variations, variation lists, and ATUM inventories in parallel
          Wave 2: Fetch any parent products discovered from variation responses (for name resolution)
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import json as _json
        
        product_ids_to_fetch = set()
        variation_ids_to_fetch = set()
        parent_ids_needing_variations = set()
        atum_lookups = set()  # (product_id, location_name) tuples — use set to deduplicate
        
        for item in pos_order_items:
            # Parse metadata
            metadata = item.metadata
            if isinstance(metadata, str):
                try:
                    metadata = _json.loads(metadata)
                except Exception:
                    metadata = {}
            
            # Check for variation selections in metadata (works for both numeric and UUID product_ids)
            variation_selection = metadata.get('variation_selection') if metadata else None
            
            if item.product_id and item.product_id.isdigit():
                # Numeric product ID — fetch the product directly
                pid = int(item.product_id)
                product_ids_to_fetch.add(pid)
                
                if variation_selection:
                    vs = str(variation_selection)
                    if '_pa_' in vs:
                        # Synthetic ID — need parent product + its variations list
                        try:
                            parent_id = int(vs.split('_pa_')[0])
                            product_ids_to_fetch.add(parent_id)
                            parent_ids_needing_variations.add(parent_id)
                        except (ValueError, IndexError):
                            pass
                    elif vs.isdigit():
                        variation_ids_to_fetch.add(int(vs))
                
                # Collect ATUM inventory lookups (deduplicated)
                if hasattr(item, 'fulfillment_location') and item.fulfillment_location:
                    atum_lookups.add((pid, item.fulfillment_location))
            
            elif variation_selection:
                # UUID product_id (bundle item) but has variation_selection — prefetch the variation
                vs = str(variation_selection)
                if '_pa_' in vs:
                    try:
                        parent_id = int(vs.split('_pa_')[0])
                        product_ids_to_fetch.add(parent_id)
                        parent_ids_needing_variations.add(parent_id)
                    except (ValueError, IndexError):
                        pass
                elif vs.isdigit():
                    vid = int(vs)
                    variation_ids_to_fetch.add(vid)
                    # Also queue ATUM lookup for this variation (will run in wave 2 after we know the ID)
                    if hasattr(item, 'fulfillment_location') and item.fulfillment_location:
                        atum_lookups.add((vid, item.fulfillment_location))
        
        # Remove IDs already in cache
        product_ids_to_fetch = {pid for pid in product_ids_to_fetch if pid not in self._product_cache}
        variation_ids_to_fetch = {vid for vid in variation_ids_to_fetch if vid not in self._product_cache}
        parent_ids_needing_variations = {pid for pid in parent_ids_needing_variations if pid not in self._variation_cache}
        atum_lookups = {t for t in atum_lookups if f"{t[0]}:{t[1]}" not in self._atum_inventory_cache}
        
        total_fetches = (len(product_ids_to_fetch) + len(variation_ids_to_fetch) + 
                         len(parent_ids_needing_variations) + len(atum_lookups))
        if total_fetches == 0:
            logger.info("[PREFETCH] All data already cached, skipping prefetch")
            return
        
        logger.info(f"[PREFETCH] Wave 1: {len(product_ids_to_fetch)} products, "
                     f"{len(variation_ids_to_fetch)} variations, "
                     f"{len(parent_ids_needing_variations)} variation lists, "
                     f"{len(atum_lookups)} ATUM lookups")
        
        start_time = time.time()
        success_count = 0
        fail_count = 0
        
        def _fetch_product(pid):
            try:
                self.get_product(pid)
                return ('product', pid, True)
            except Exception as e:
                logger.warning(f"[PREFETCH] Failed to fetch product {pid}: {e}")
                return ('product', pid, False)
        
        def _fetch_variation(vid):
            try:
                resp = self.get_product_variation(vid)
                # Return parent_id so wave 2 can fetch it if needed
                parent_id = None
                if resp and isinstance(resp, dict) and resp.get('data'):
                    parent_id = resp['data'].get('parent_id')
                return ('variation', vid, True, parent_id)
            except Exception as e:
                logger.warning(f"[PREFETCH] Failed to fetch variation {vid}: {e}")
                return ('variation', vid, False, None)
        
        def _fetch_variations_list(pid):
            try:
                self.get_product_variations(pid)
                return ('variations_list', pid, True)
            except Exception as e:
                logger.warning(f"[PREFETCH] Failed to fetch variations list for {pid}: {e}")
                return ('variations_list', pid, False)
        
        def _fetch_atum(args):
            pid, location = args
            try:
                self.find_atum_inventory_id_for_location(pid, location)
                return ('atum', pid, True)
            except Exception as e:
                logger.warning(f"[PREFETCH] Failed to fetch ATUM for {pid}/{location}: {e}")
                return ('atum', pid, False)
        
        # ── Wave 1: Fetch everything in parallel (max 6 concurrent) ──
        parent_ids_for_wave2 = set()
        
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = []
            for pid in product_ids_to_fetch:
                futures.append(executor.submit(_fetch_product, pid))
            for vid in variation_ids_to_fetch:
                futures.append(executor.submit(_fetch_variation, vid))
            for pid in parent_ids_needing_variations:
                futures.append(executor.submit(_fetch_variations_list, pid))
            for args in atum_lookups:
                futures.append(executor.submit(_fetch_atum, args))
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result[2]:
                        success_count += 1
                    else:
                        fail_count += 1
                    # Collect parent IDs from variation fetches for wave 2
                    if len(result) > 3 and result[0] == 'variation' and result[3]:
                        parent_id = result[3]
                        if parent_id not in self._product_cache:
                            parent_ids_for_wave2.add(parent_id)
                except Exception:
                    fail_count += 1
        
        # ── Wave 2: Fetch parent products discovered from variations ──
        if parent_ids_for_wave2:
            logger.info(f"[PREFETCH] Wave 2: {len(parent_ids_for_wave2)} parent products from variations")
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(_fetch_product, pid) for pid in parent_ids_for_wave2]
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result[2]:
                            success_count += 1
                        else:
                            fail_count += 1
                    except Exception:
                        fail_count += 1
        
        elapsed = time.time() - start_time
        total_fetched = success_count + fail_count
        logger.info(f"[PREFETCH] Completed in {elapsed:.1f}s — {success_count}/{total_fetched} succeeded "
                     f"(estimated ~{total_fetched * 1.5:.0f}s saved vs sequential)")

    @retry_on_error(max_retries=3)
    def get_product_subscription_data(self, product_id):
        """Get subscription data for a specific product using WooCommerce Subscriptions API."""
        try:
            logger.info(f"Fetching subscription data for product ID: {product_id}")
            
            # Use cached get_product to avoid redundant API calls
            product_response = self.get_product(product_id)
            if not product_response or not product_response.get('data'):
                logger.error(f"Failed to get product data for ID {product_id}")
                return None
            product_data = product_response['data']
            
            # Check if this is a subscription product by looking at product type or meta data
            if product_data.get('type') in ['subscription', 'variable-subscription']:
                logger.info(f"Product ID {product_id} is a subscription product")
                
                # Extract subscription data from meta_data
                subscription_data = {}
                for meta in product_data.get('meta_data', []):
                    if meta.get('key') and meta.get('key').startswith('_subscription_'):
                        key = meta.get('key').replace('_subscription_', '')
                        subscription_data[key] = meta.get('value')
                
                if subscription_data:
                    logger.info(f"Found subscription data for product ID {product_id}")
                    return {
                        'subscription_data': subscription_data,
                        'product_data': product_data
                    }
            
            # If no subscription data found in product meta data, try the subscriptions endpoint if available
            try:
                logger.info(f"Checking subscriptions endpoint for product ID: {product_id}")
                subscription_response = self.wcapi.get(f"subscriptions", params={"product": product_id, "per_page": 1})
                
                if subscription_response.ok and subscription_response.json():
                    logger.info(f"Found subscription data in subscriptions endpoint for product ID {product_id}")
                    return {
                        'subscription_data': subscription_response.json()[0],
                        'product_data': product_data
                    }
            except Exception as sub_error:
                logger.warning(f"Error checking subscriptions endpoint: {str(sub_error)}")
            
            logger.info(f"No subscription data found for product ID {product_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting subscription data for product ID {product_id}: {str(e)}")
            return None
    
    @retry_on_error(max_retries=3)
    def get_product_bundle_data(self, product_id):
        """Get bundle data for a specific product using WooCommerce Product Bundles API."""
        try:
            logger.info(f"Fetching bundle data for product ID: {product_id}")
            
            # Use cached get_product to avoid redundant API calls
            product_response = self.get_product(product_id)
            if not product_response or not product_response.get('data'):
                logger.error(f"Failed to get product data for ID {product_id}")
                return None
            product_data = product_response['data']
            
            # Check if this is a bundle product by looking at product type or meta data
            if product_data.get('type') == 'bundle':
                logger.info(f"Product ID {product_id} is a bundle product")
                
                # Extract bundle data from meta_data
                bundle_data = {}
                bundled_items = []
                
                # Look for bundle data in meta_data with more flexible key matching
                for meta in product_data.get('meta_data', []):
                    # Check for common bundle plugin meta keys
                    key = meta.get('key', '')
                    if '_wc_pb_bundle_' in key or '_bundle_' in key:
                        bundle_data[key.replace('_', '')] = meta.get('value', {})
                    elif '_wc_pb_bundled_' in key or '_bundled_' in key:
                        bundled_items_data = meta.get('value', [])
                        if isinstance(bundled_items_data, list):
                            bundled_items.extend(bundled_items_data)
                        elif isinstance(bundled_items_data, dict):
                            bundled_items.append(bundled_items_data)
                
                # If we didn't find specific bundle data, look for any related product IDs
                if not bundled_items:
                    # Check for related products in the product data
                    related_ids = product_data.get('related_ids', [])
                    cross_sell_ids = product_data.get('cross_sell_ids', [])
                    upsell_ids = product_data.get('upsell_ids', [])
                    
                    # Combine all related product IDs as potential bundle items
                    potential_bundle_items = list(set(related_ids + cross_sell_ids + upsell_ids))
                    
                    if potential_bundle_items:
                        logger.info(f"Using related product IDs as bundle items for product ID {product_id}")
                        bundled_items = potential_bundle_items
                
                # Even if we don't find specific bundle data, return what we have with the product data
                # This ensures we at least save the product as a bundle type
                return {
                    'bundle_data': bundle_data,
                    'bundled_items': bundled_items,
                    'product_data': product_data
                }
            
            logger.info(f"No bundle data found for product ID {product_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting bundle data for product ID {product_id}: {str(e)}")
            return None
    
    @retry_on_error(max_retries=3)
    def get_product_grouped_data(self, product_id):
        """Get grouped product data for a specific product."""
        try:
            logger.info(f"Fetching grouped data for product ID: {product_id}")
            
            # Use cached get_product to avoid redundant API calls
            product_response = self.get_product(product_id)
            if not product_response or not product_response.get('data'):
                logger.error(f"Failed to get product data for ID {product_id}")
                return None
            product_data = product_response['data']
            
            # Check if this is a grouped product
            if product_data.get('type') == 'grouped':
                logger.info(f"Product ID {product_id} is a grouped product")
                
                # Get the grouped products
                grouped_products = product_data.get('grouped_products', [])
                
                if grouped_products:
                    logger.info(f"Found {len(grouped_products)} grouped products for product ID {product_id}")
                    return {
                        'grouped_products': grouped_products,
                        'product_data': product_data
                    }
            
            logger.info(f"No grouped data found for product ID {product_id}")
            return None
            
        except Exception as e:
            logger.error(f"Error getting grouped data for product ID {product_id}: {str(e)}")
            return None

    @retry_on_error(max_retries=3)
    def get_product(self, product_id, **kwargs):
        """
        Get a specific product by ID with detailed information.
        Uses in-memory cache to avoid duplicate API calls within the same request.
        Pass params={'_cb': ...} to bypass cache for stock-sync callers.
        
        Args:
            product_id: The WooCommerce product ID
            
        Returns:
            Dictionary containing product data and metadata
        """
        # Check cache first (skip cache if extra params like cache_buster are passed)
        cache_key = int(product_id) if str(product_id).isdigit() else str(product_id)
        if not kwargs.get('params') and cache_key in self._product_cache:
            logger.info(f"[CACHE HIT] Product {product_id} from cache")
            return self._product_cache[cache_key]
        try:
            logger.info(f"Fetching product with ID: {product_id}")
            response = self.wcapi.get(f"products/{product_id}", **kwargs)
            
            if not response.ok:
                error_msg = f"Failed to get product {product_id}. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                
                # Special handling for 404 errors
                if response.status_code == 404:
                    return {'data': None, 'error': 'Product not found'}
                    
                raise Exception(error_msg)
            
            # Parse response data
            try:
                data = response.json()
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                raise Exception("Invalid JSON response from WooCommerce API")
            
            logger.info(f"Successfully retrieved product {product_id}")
            
            result = {
                'data': data,
                'status': 'success'
            }
            # Cache the result (only when no cache-busting params)
            if not kwargs.get('params'):
                self._product_cache[cache_key] = result
            return result
            
        except Exception as e:
            logger.error(f"Error getting product {product_id}: {str(e)}")
            return {
                'data': None,
                'error': str(e),
                'status': 'error'
            }

    @retry_on_error(max_retries=3)
    def get_all_products(self, batch_size=100):
        """
        Get all products using pagination.
        """
        try:
            first_page = self.get_products(page=1, per_page=batch_size)
            if not first_page or 'total' not in first_page:
                logger.error("Failed to get initial product data")
                return []
                
            processed = len(first_page['data'])
            logger.info(f"Processing page 1/{first_page['total_pages']} - {processed}/{first_page['total']} products")
            
            yield first_page['data']
            
            # Process remaining pages
            for page in range(2, first_page['total_pages'] + 1):
                result = self.get_products(page=page, per_page=batch_size)
                if result and result.get('data'):
                    processed += len(result['data'])
                    logger.info(f"Processing page {page}/{first_page['total_pages']} - {processed}/{first_page['total']} products")
                    yield result['data']
                    
        except Exception as e:
            logger.error(f"Error in get_all_products: {str(e)}")
            yield []

    @retry_on_error(max_retries=3)
    def get_product_categories(self, page=1, per_page=100):
        """
        Get product categories with hierarchical structure from WooCommerce API.
        
        Args:
            page: Page number to fetch
            per_page: Number of categories per page
            
        Returns:
            Dictionary with categories data and pagination info
        """
        try:
            logger.info(f"Fetching product categories from WooCommerce (page {page}, per_page {per_page})")
            
            response = self.wcapi.get("products/categories", params={
                "per_page": per_page,
                "page": page
            })
            
            if not response.ok:
                error_msg = f"Failed to get product categories. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {'data': [], 'total': 0, 'total_pages': 0, 'current_page': page}
            
            # Get pagination information from headers
            total_items = int(response.headers.get('X-WP-Total', 0))
            total_pages = int(response.headers.get('X-WP-TotalPages', 0))
            
            # Parse response data
            try:
                categories = response.json()
                logger.info(f"Retrieved {len(categories)} product categories. Page {page}/{total_pages}, Total: {total_items}")
                
                return {
                    'data': categories,
                    'total': total_items,
                    'total_pages': total_pages,
                    'current_page': page
                }
            except Exception as e:
                logger.error(f"Failed to parse JSON response for categories: {str(e)}")
                return {'data': [], 'total': 0, 'total_pages': 0, 'current_page': page}
                
        except Exception as e:
            logger.error(f"Error getting product categories: {str(e)}")
            return {'data': [], 'total': 0, 'total_pages': 0, 'current_page': page}
    
    @retry_on_error(max_retries=3)
    def get_all_product_categories(self, batch_size=100):
        """
        Get all product categories using pagination.
        
        Args:
            batch_size: Number of categories to fetch per page
            
        Returns:
            List of all categories with hierarchical structure
        """
        try:
            first_page = self.get_product_categories(page=1, per_page=batch_size)
            if not first_page or 'total' not in first_page:
                logger.error("Failed to get initial category data")
                return []
                
            all_categories = first_page['data']
            processed = len(all_categories)
            logger.info(f"Processing page 1/{first_page['total_pages']} - {processed}/{first_page['total']} categories")
            
            # Process remaining pages
            for page in range(2, first_page['total_pages'] + 1):
                result = self.get_product_categories(page=page, per_page=batch_size)
                if result and result.get('data'):
                    all_categories.extend(result['data'])
                    processed += len(result['data'])
                    logger.info(f"Processing page {page}/{first_page['total_pages']} - {processed}/{first_page['total']} categories")
            
            logger.info(f"Retrieved all {len(all_categories)} categories")
            return all_categories
                    
        except Exception as e:
            logger.error(f"Error in get_all_product_categories: {str(e)}")
            return []
            
    @retry_on_error(max_retries=3)
    def get_product_by_sku(self, sku):
        """
        Get a product by its SKU.
        
        Args:
            sku: The product SKU to search for
            
        Returns:
            Product data if found, None otherwise
        """
        try:
            logger.info(f"Searching for product with SKU: {sku}")
            response = self.wcapi.get("products", params={"sku": sku})
            
            if not response.ok:
                logger.error(f"Failed to get product by SKU. Status code: {response.status_code}, Response: {response.text}")
                return None
            
            products = response.json()
            if products and len(products) > 0:
                logger.info(f"Found product with SKU {sku}: {products[0]['id']}")
                return products[0]
            else:
                logger.info(f"No product found with SKU: {sku}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting product by SKU: {str(e)}")
            return None
            
    @retry_on_error(max_retries=3)
    def create_product(self, product_data):
        """
        Create a new product in WooCommerce.
        
        Args:
            product_data: Dictionary containing product data in WooCommerce format
            
        Returns:
            Dictionary containing the created product data or error information
        """
        try:
            logger.info(f"Creating product in WooCommerce: {json.dumps(product_data)[:500]}...")
            response = self.wcapi.post("products", product_data)
            
            if not response.ok:
                error_msg = f"Failed to create product. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {
                    'status': 'error',
                    'message': error_msg,
                    'data': None
                }
            
            # Parse response data
            try:
                data = response.json()
                logger.info(f"Successfully created product in WooCommerce with ID: {data.get('id')}")
                return {
                    'status': 'success',
                    'data': data
                }
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                return {
                    'status': 'error',
                    'message': f"Invalid JSON response from WooCommerce API: {str(e)}",
                    'data': None
                }
            
        except Exception as e:
            error_msg = f"Error creating product in WooCommerce: {str(e)}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'data': None
            }

    def get_product_atum_inventories(self, product_id):
        """
        Get all ATUM Multi-Inventory records for a product.
        Uses ATUM REST API: GET /wp-json/wc/v3/products/{id}/inventories
        
        Returns:
            List of inventory dicts with id, name, location, stock info, etc.
            Returns empty list on error.
        """
        try:
            response = self.wcapi.get(f"products/{product_id}/inventories")
            if response.ok:
                inventories = response.json()
                logger.info(f"🎯 ATUM: Found {len(inventories)} inventories for product {product_id}")
                return inventories
            else:
                logger.warning(f"🎯 ATUM: Failed to get inventories for product {product_id}: {response.status_code}")
                return []
        except Exception as e:
            logger.warning(f"🎯 ATUM: Error fetching inventories for product {product_id}: {e}")
            return []

    def find_atum_inventory_id_for_location(self, product_id, location_name):
        """
        Find the ATUM inventory record ID for a specific product at a specific location.
        
        Uses multi-level matching because ATUM inventory names often differ from
        Django location names (e.g. ATUM: "Boca Clinic" vs Django: "Boca Inventory").
        
        Matching priority:
        1. Exact name match
        2. First-word match (e.g. "Boca" matches "Boca Clinic")
        3. Contains match (one name contains the other)
        4. Location taxonomy dict match
        5. Fallback: single non-main inventory
        
        Args:
            product_id: WooCommerce product ID
            location_name: Location name from POS (e.g., "Boca Inventory", "Jupiter Inventory")
            
        Returns:
            ATUM inventory record ID (int) or None if not found
        """
        # Check cache first
        cache_key = f"{product_id}:{location_name}"
        if cache_key in self._atum_inventory_cache:
            cached = self._atum_inventory_cache[cache_key]
            logger.info(f"[CACHE HIT] ATUM inventory for product {product_id} at '{location_name}': {cached}")
            return cached
        
        inventories = self.get_product_atum_inventories(product_id)
        if not inventories:
            self._atum_inventory_cache[cache_key] = None
            return None
        
        # Normalize the location name for comparison
        location_name_lower = location_name.lower().strip()
        location_slug = location_name_lower.replace(' ', '-')
        location_first_word = location_name_lower.split()[0] if location_name_lower else ''
        
        logger.info(f"🎯 ATUM: Matching '{location_name}' against {len(inventories)} inventories for product {product_id}")
        
        # Build list of non-main inventories with normalized names
        candidates = []
        for inv in inventories:
            inv_id = inv.get('id')
            inv_name = (inv.get('name') or '').lower().strip()
            inv_first_word = inv_name.split()[0] if inv_name else ''
            inv_locations = inv.get('location', {})
            is_main = inv.get('is_main', False)
            candidates.append({
                'id': inv_id, 'name': inv_name, 'first_word': inv_first_word,
                'locations': inv_locations, 'is_main': is_main, 'raw': inv
            })
            logger.info(f"🎯 ATUM:   inventory_id={inv_id}, name='{inv_name}', is_main={is_main}")
        
        # PASS 1: Exact name match
        for c in candidates:
            if c['name'] == location_name_lower:
                logger.info(f"🎯 ATUM: EXACT match: '{c['name']}' -> inventory_id={c['id']}")
                result = int(c['id'])
                self._atum_inventory_cache[cache_key] = result
                return result
        
        # PASS 2: First-word match (e.g. "Boca" in "Boca Inventory" matches "Boca Clinic")
        # Only match non-main inventories to avoid accidentally matching the main/dropship one
        if location_first_word and len(location_first_word) >= 3:
            first_word_matches = [
                c for c in candidates
                if not c['is_main'] and c['first_word'] == location_first_word
            ]
            if len(first_word_matches) == 1:
                match = first_word_matches[0]
                logger.info(f"🎯 ATUM: FIRST-WORD match: '{location_name}' ~ '{match['name']}' -> inventory_id={match['id']}")
                result = int(match['id'])
                self._atum_inventory_cache[cache_key] = result
                return result
        
        # PASS 3: Contains match (one name is substring of the other)
        contains_matches = [
            c for c in candidates
            if not c['is_main'] and (c['name'] in location_name_lower or location_name_lower in c['name'])
        ]
        if len(contains_matches) == 1:
            match = contains_matches[0]
            logger.info(f"🎯 ATUM: CONTAINS match: '{location_name}' ~ '{match['name']}' -> inventory_id={match['id']}")
            result = int(match['id'])
            self._atum_inventory_cache[cache_key] = result
            return result
        
        # PASS 4: Location taxonomy dict match
        for c in candidates:
            if isinstance(c['locations'], dict):
                for loc_term_id, loc_slug in c['locations'].items():
                    loc_slug_lower = loc_slug.lower()
                    if (loc_slug_lower == location_slug or 
                        loc_slug_lower == location_name_lower or
                        loc_slug_lower.replace('-', ' ') == location_name_lower):
                        logger.info(f"🎯 ATUM: LOCATION-SLUG match: '{loc_slug}' -> inventory_id={c['id']}")
                        result = int(c['id'])
                        self._atum_inventory_cache[cache_key] = result
                        return result
        
        # PASS 5: Fallback - if only one non-main inventory exists, use it
        non_main = [c for c in candidates if not c['is_main']]
        if len(non_main) == 1:
            match = non_main[0]
            logger.info(f"🎯 ATUM: FALLBACK (single non-main): '{match['name']}' -> inventory_id={match['id']}")
            result = int(match['id'])
            self._atum_inventory_cache[cache_key] = result
            return result
        
        logger.warning(f"🎯 ATUM: NO MATCH for '{location_name}' product {product_id}. Available: {[(c['id'], c['name']) for c in candidates]}")
        self._atum_inventory_cache[cache_key] = None
        return None

    @retry_on_error(max_retries=3)
    def create_order(self, order_data):
        """
        Create a new order in WooCommerce.
        
        Args:
            order_data: Dictionary containing order data in WooCommerce format
            
        Returns:
            Dictionary containing the created order data or error information
        """
        try:
            # NOTE: _test_connection() removed here — it was adding ~1-3s per order
            # by firing a redundant GET products?per_page=1 request. The connection
            # is already validated during __init__(), and if it's broken the POST
            # below will fail with a clear error anyway.
            
            # Log the order data being sent
            logger.info(f"Creating order in WooCommerce with data: {json.dumps(order_data)[:500]}...")
            
            # Make the API request
            response = self.wcapi.post("orders", order_data)
            
            # Log the full response for debugging
            logger.info(f"WooCommerce API response status: {response.status_code}")
            logger.info(f"WooCommerce API response headers: {response.headers}")
            logger.info(f"WooCommerce API response body: {response.text[:1000]}")
            
            if not response.ok:
                error_msg = f"Failed to create order. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {
                    'status': 'error',
                    'message': error_msg,
                    'data': None
                }
            
            # Parse response data
            try:
                data = response.json()
                logger.info(f"Successfully created order in WooCommerce with ID: {data.get('id')}")
                return {
                    'status': 'success',
                    'data': data
                }
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                return {
                    'status': 'error',
                    'message': f"Invalid JSON response from WooCommerce API: {str(e)}",
                    'data': None
                }
            
        except Exception as e:
            error_msg = f"Error creating order in WooCommerce: {str(e)}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'data': None
            }

    @retry_on_error(max_retries=3)
    def get_subscriptions(self, page=1, per_page=10, **kwargs):
        """
        Get subscriptions with pagination support and better error handling.
        
        Args:
            page: Page number
            per_page: Number of items per page
            **kwargs: Additional filter parameters
            
        Returns:
            List of subscriptions or error information
        """
        try:
            # Prepare parameters
            params = {
                'page': page,
                'per_page': per_page
            }
            
            # Add any additional filters
            params.update(kwargs)
            
            logger.info(f"Getting subscriptions from WooCommerce with params: {params}")
            
            # Make API call
            response = self.wcapi.get("subscriptions", params=params)
            
            # Log response details for debugging
            logger.info(f"WooCommerce API response status: {response.status_code}")
            if hasattr(response, 'headers'):
                logger.info(f"WooCommerce API response headers: {response.headers}")
            
            # Handle 404 error (no subscriptions found)
            if response.status_code == 404:
                logger.warning("No subscriptions found or endpoint not available")
                return {
                    'status': 'warning',
                    'message': 'No subscriptions found or endpoint not available',
                    'data': []
                }
                
            if not response.ok:
                error_msg = f"Failed to get subscriptions. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {
                    'status': 'error',
                    'message': error_msg,
                    'data': None
                }
                
            # Parse response data
            try:
                data = response.json()
                logger.info(f"Successfully retrieved {len(data)} subscriptions")
                return {
                    'status': 'success',
                    'data': data,
                    'headers': dict(response.headers) if hasattr(response, 'headers') else {}
                }
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                return {
                    'status': 'error',
                    'message': f"Invalid JSON response from WooCommerce API: {str(e)}",
                    'data': None
                }
                
        except Exception as e:
            error_msg = f"Error getting subscriptions from WooCommerce: {str(e)}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'data': None
            }
    
    @retry_on_error(max_retries=3)
    def create_subscription(self, subscription_data):
        """
        Create a new subscription in WooCommerce.
        
        Args:
            subscription_data: Dictionary containing subscription data in WooCommerce format
            
        Returns:
            Dictionary containing the created subscription data or error information
        """
        try:
            logger.info(f"Creating subscription in WooCommerce: {json.dumps(subscription_data)[:500]}...")
            
            # WooCommerce Subscriptions API endpoint
            endpoint = "subscriptions"
            response = self.wcapi.post(endpoint, subscription_data)
            
            # Log the full response for debugging
            logger.info(f"WooCommerce API response status: {response.status_code}")
            logger.info(f"WooCommerce API response headers: {response.headers}")
            logger.info(f"WooCommerce API response body: {response.text[:1000]}")
            
            if not response.ok:
                error_msg = f"Failed to create subscription. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {
                    'status': 'error',
                    'message': error_msg,
                    'data': None
                }
            
            # Parse response data
            try:
                data = response.json()
                logger.info(f"Successfully created subscription in WooCommerce with ID: {data.get('id')}")
                return {
                    'status': 'success',
                    'data': data
                }
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                return {
                    'status': 'error',
                    'message': f"Invalid JSON response from WooCommerce API: {str(e)}",
                    'data': None
                }
            
        except Exception as e:
            error_msg = f"Error creating subscription in WooCommerce: {str(e)}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'data': None
            }
            
    @retry_on_error(max_retries=3)
    def update_order_status(self, order_id, status):
        """
        Update the status of an order in WooCommerce.
        
        Args:
            order_id: The WooCommerce order ID
            status: The new status to set (e.g., 'refunded', 'completed', 'processing')
            
        Returns:
            Dictionary containing the updated order data or error information
        """
        try:
            logger.info(f"Updating WooCommerce order {order_id} status to '{status}'")
            
            # Prepare the update data with force parameter to override WooCommerce status transition restrictions
            update_data = {
                'status': status,
                'force': True  # Force the status change regardless of WooCommerce's internal status flow
            }
            
            # Make the API request to update the order
            response = self.wcapi.put(f"orders/{order_id}", update_data)
            
            # Log the response for debugging
            logger.info(f"WooCommerce API response status: {response.status_code}")
            logger.info(f"WooCommerce API response headers: {response.headers}")
            logger.info(f"WooCommerce API response body: {response.text[:1000]}")
            
            # If the first attempt fails, try a direct REST API call as fallback
            if not response.ok:
                logger.warning(f"Standard API update failed. Attempting direct REST API call with custom parameters")
                
                # Build the endpoint URL manually
                base_url = self.wcapi.url
                if not base_url.endswith('/'):
                    base_url += '/'
                
                endpoint = f"{base_url}wp-json/wc/v3/orders/{order_id}"
                
                # Enhanced update data with additional parameters
                enhanced_update_data = {
                    'status': status,
                    'force': True,
                    '_wpnonce': True,  # Try to bypass nonce check
                    'override_restrictions': True  # Custom parameter that might be used by plugins
                }
                
                # Make direct request with OAuth1 authentication
                from requests_oauthlib import OAuth1
                auth = OAuth1(self.consumer_key, self.consumer_secret)
                direct_response = requests.put(endpoint, json=enhanced_update_data, auth=auth, verify=False)
                
                logger.info(f"Direct API response status: {direct_response.status_code}")
                logger.info(f"Direct API response body: {direct_response.text[:1000]}")
                
                if direct_response.ok:
                    response = direct_response
                else:
                    error_msg = f"Both standard and direct API calls failed. Status codes: {response.status_code}, {direct_response.status_code}"
                    if hasattr(response, 'text'):
                        error_msg += f", Response: {response.text}"
                    logger.error(error_msg)
                    return {
                        'status': 'error',
                        'message': error_msg,
                        'data': None
                    }
            
            # Parse response data
            try:
                data = response.json()
                logger.info(f"Successfully updated WooCommerce order {order_id} status to '{status}'")
                return {
                    'status': 'success',
                    'data': data
                }
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                return {
                    'status': 'error',
                    'message': f"Invalid JSON response from WooCommerce API: {str(e)}",
                    'data': None
                }
            
        except Exception as e:
            error_msg = f"Error updating order status in WooCommerce: {str(e)}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'data': None
            }

    @retry_on_error(max_retries=3)
    def add_order_note(self, order_id, note_data):
        """
        Add a note to a WooCommerce order.
        
        Args:
            order_id: The WooCommerce order ID
            note_data: Dictionary containing note information:
                - note: The note content (required)
                - customer_note: Boolean, whether the note is visible to customer (default: False)
                - added_by_user: Boolean, whether the note was added by a user (default: True)
                
        Returns:
            Dictionary containing the created note data or error information
        """
        try:
            logger.info(f"Adding note to WooCommerce order {order_id}: {note_data.get('note', '')[:100]}...")
            
            # Prepare the note data
            note_payload = {
                'note': note_data.get('note', ''),
                'customer_note': note_data.get('customer_note', False),
                'added_by_user': note_data.get('added_by_user', True)
            }
            
            # Make the API request to add the note
            response = self.wcapi.post(f"orders/{order_id}/notes", note_payload)
            
            # Log the response for debugging
            logger.info(f"WooCommerce add note API response status: {response.status_code}")
            
            if response.ok:
                try:
                    data = response.json()
                    logger.info(f"Successfully added note to WooCommerce order {order_id}")
                    return {
                        'status': 'success',
                        'data': data
                    }
                except Exception as e:
                    logger.error(f"Failed to parse JSON response: {str(e)}")
                    return {
                        'status': 'error',
                        'message': f"Invalid JSON response from WooCommerce API: {str(e)}",
                        'data': None
                    }
            else:
                error_msg = f"Failed to add note to order {order_id}. Status: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {
                    'status': 'error',
                    'message': error_msg,
                    'data': None
                }
                
        except Exception as e:
            error_msg = f"Error adding note to WooCommerce order {order_id}: {str(e)}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'data': None
            }

    @retry_on_error(max_retries=3)
    def create_customer(self, customer_data):
        """
        Create a new customer in WooCommerce.
        
        Args:
            customer_data: Dictionary containing customer information
            
        Returns:
            Dictionary with customer creation result
        """
        try:
            logger.info("Creating WooCommerce customer...")
            logger.info(f"Customer data: {json.dumps(customer_data, indent=2)}")
            
            # Validate required fields
            if not customer_data.get('email'):
                return {
                    'status': 'error',
                    'message': 'Email is required for customer creation',
                    'data': None
                }
            
            # Prepare WooCommerce customer data
            woo_customer_data = {
                'email': customer_data['email'],
                'first_name': customer_data.get('first_name', ''),
                'last_name': customer_data.get('last_name', ''),
                'username': customer_data.get('username', customer_data['email']),  # Use email as username if not provided
                'billing': {
                    'first_name': customer_data.get('first_name', ''),
                    'last_name': customer_data.get('last_name', ''),
                    'company': customer_data.get('company', ''),
                    'address_1': customer_data.get('billing_address', ''),
                    'address_2': customer_data.get('billing_address_2', ''),
                    'city': customer_data.get('billing_city', ''),
                    'state': customer_data.get('billing_state', ''),
                    'postcode': customer_data.get('billing_postcode', ''),
                    'country': customer_data.get('billing_country', 'US'),
                    'email': customer_data['email'],
                    'phone': customer_data.get('phone', '')
                },
                'shipping': {
                    'first_name': customer_data.get('first_name', ''),
                    'last_name': customer_data.get('last_name', ''),
                    'company': customer_data.get('company', ''),
                    'address_1': customer_data.get('shipping_address', customer_data.get('billing_address', '')),
                    'address_2': customer_data.get('shipping_address_2', customer_data.get('billing_address_2', '')),
                    'city': customer_data.get('shipping_city', customer_data.get('billing_city', '')),
                    'state': customer_data.get('shipping_state', customer_data.get('billing_state', '')),
                    'postcode': customer_data.get('shipping_postcode', customer_data.get('billing_postcode', '')),
                    'country': customer_data.get('shipping_country', customer_data.get('billing_country', 'US'))
                }
            }
            
            # Handle shipping same as billing
            if customer_data.get('shipping_same_as_billing', True):
                woo_customer_data['shipping'] = woo_customer_data['billing'].copy()
                # Remove email and phone from shipping as they're not needed there
                woo_customer_data['shipping'].pop('email', None)
                woo_customer_data['shipping'].pop('phone', None)
            
            logger.info(f"Sending WooCommerce customer data: {json.dumps(woo_customer_data, indent=2)}")
            
            # Create customer in WooCommerce
            response = self.wcapi.post("customers", woo_customer_data)
            
            if not response.ok:
                error_msg = f"Failed to create WooCommerce customer. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                
                # Handle specific error cases
                if response.status_code == 400:
                    try:
                        error_data = response.json()
                        if 'code' in error_data and error_data['code'] == 'registration-error-email-exists':
                            return {
                                'status': 'error',
                                'message': 'A customer with this email already exists in WooCommerce',
                                'data': None,
                                'error_code': 'email_exists'
                            }
                    except:
                        pass
                
                return {
                    'status': 'error',
                    'message': error_msg,
                    'data': None
                }
            
            # Parse response data
            try:
                customer_response = response.json()
                logger.info(f"Successfully created WooCommerce customer with ID: {customer_response.get('id')}")
                
                return {
                    'status': 'success',
                    'data': customer_response,
                    'customer_id': customer_response.get('id')
                }
                
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {str(e)}")
                return {
                    'status': 'error',
                    'message': f"Invalid JSON response from WooCommerce API: {str(e)}",
                    'data': None
                }
                
        except Exception as e:
            error_msg = f"Error creating WooCommerce customer: {str(e)}"
            logger.error(error_msg)
            return {
                'status': 'error',
                'message': error_msg,
                'data': None
            }

    @retry_on_error(max_retries=3)
    def get_customer_by_email(self, email, deep_search=False):
        """
        Get a customer by email address.
        
        Args:
            email: Customer email address
            deep_search: If True, fetch all customers and filter locally (slower but more reliable)
            
        Returns:
            Customer data if found, None otherwise
        """
        try:
            logger.info(f"Searching for WooCommerce customer with email: {email}")
            response = self.wcapi.get("customers", params={"email": email})
            
            if not response.ok:
                logger.error(f"Failed to search for customer. Status code: {response.status_code}, Response: {response.text}")
                return None
            
            customers = response.json()
            if customers and len(customers) > 0:
                logger.info(f"Found WooCommerce customer: {customers[0].get('id')}")
                return customers[0]
            else:
                logger.info(f"No WooCommerce customer found with email: {email}")
                
                # If deep_search is enabled and no results, try fetching all customers
                if deep_search:
                    logger.info(f"Performing deep search for customer: {email}")
                    return self._deep_search_customer_by_email(email)
                
                return None
                
        except Exception as e:
            logger.error(f"Error searching for customer by email: {str(e)}")
            return None
    
    def _deep_search_customer_by_email(self, email):
        """
        Deep search for customer by fetching all customers and filtering locally.
        Used when standard email search fails but customer exists.
        
        Args:
            email: Customer email address
            
        Returns:
            Customer data if found, None otherwise
        """
        try:
            logger.info(f"Deep searching all WooCommerce customers for email: {email}")
            page = 1
            max_pages = 5  # Limit to first 500 customers (100 per page)
            
            while page <= max_pages:
                response = self.wcapi.get("customers", params={
                    "per_page": 100,
                    "page": page
                })
                
                if not response.ok:
                    logger.error(f"Failed to fetch customers page {page}: {response.status_code}")
                    break
                
                customers = response.json()
                if not customers:
                    break
                
                # Search for matching email in this batch
                for customer in customers:
                    customer_email = customer.get('email', '').lower()
                    billing_email = customer.get('billing', {}).get('email', '').lower()
                    
                    if customer_email == email.lower() or billing_email == email.lower():
                        logger.info(f"✅ Deep search found customer: ID={customer.get('id')}, Email={customer_email}")
                        return customer
                
                page += 1
            
            logger.warning(f"Deep search completed, no customer found for email: {email}")
            return None
            
        except Exception as e:
            logger.error(f"Error in deep search: {str(e)}")
            return None
    
    @retry_on_error(max_retries=3)
    def find_guest_orders(self, email):
        """
        Find all guest orders (customer_id = 0) for a given email address.
        
        Args:
            email: Email address to search for
            
        Returns:
            List of guest order data
        """
        guest_orders = []
        page = 1
        max_pages = 10  # Limit to prevent excessive API calls
        
        try:
            logger.info(f"Searching for guest orders with email: {email}")
            
            while page <= max_pages:
                response = self.wcapi.get("orders", params={
                    "per_page": 100,
                    "page": page,
                    "orderby": "date",
                    "order": "desc"
                })
                
                if not response.ok:
                    logger.error(f"Failed to fetch orders page {page}: {response.status_code}")
                    break
                
                orders = response.json()
                if not orders:
                    break
                
                # Filter for guest orders with matching email
                for order in orders:
                    if order.get('customer_id') == 0:
                        billing_email = order.get('billing', {}).get('email', '').lower()
                        if billing_email == email.lower():
                            guest_orders.append(order)
                
                page += 1
            
            logger.info(f"Found {len(guest_orders)} guest orders for email: {email}")
            return guest_orders
            
        except Exception as e:
            logger.error(f"Error finding guest orders: {str(e)}")
            return []
    
    def extract_customer_data_from_orders(self, guest_orders):
        """
        Extract customer data from guest orders.
        
        Args:
            guest_orders: List of guest order data
            
        Returns:
            Dictionary with customer data extracted from most recent order
        """
        if not guest_orders:
            return None
        
        try:
            # Use first order (most recent)
            order = guest_orders[0]
            billing = order.get('billing', {})
            shipping = order.get('shipping', {})
            
            # Create username from email
            email = billing.get('email', '')
            username = email.split('@')[0].replace('.', '_').replace('+', '_')
            
            customer_data = {
                'email': email,
                'username': username,
                'first_name': billing.get('first_name', ''),
                'last_name': billing.get('last_name', ''),
                'billing': {
                    'first_name': billing.get('first_name', ''),
                    'last_name': billing.get('last_name', ''),
                    'company': billing.get('company', ''),
                    'address_1': billing.get('address_1', ''),
                    'address_2': billing.get('address_2', ''),
                    'city': billing.get('city', ''),
                    'state': billing.get('state', ''),
                    'postcode': billing.get('postcode', ''),
                    'country': billing.get('country', 'US'),
                    'email': email,
                    'phone': billing.get('phone', '')
                },
                'shipping': {
                    'first_name': shipping.get('first_name', '') or billing.get('first_name', ''),
                    'last_name': shipping.get('last_name', '') or billing.get('last_name', ''),
                    'company': shipping.get('company', ''),
                    'address_1': shipping.get('address_1', '') or billing.get('address_1', ''),
                    'address_2': shipping.get('address_2', ''),
                    'city': shipping.get('city', '') or billing.get('city', ''),
                    'state': shipping.get('state', '') or billing.get('state', ''),
                    'postcode': shipping.get('postcode', '') or billing.get('postcode', ''),
                    'country': shipping.get('country', '') or billing.get('country', 'US')
                }
            }
            
            logger.info(f"Extracted customer data from {len(guest_orders)} guest orders")
            return customer_data
            
        except Exception as e:
            logger.error(f"Error extracting customer data from orders: {str(e)}")
            return None
    
    def generate_secure_password(self, length=12):
        """
        Generate a secure random password.
        
        Args:
            length: Password length (default 12)
            
        Returns:
            String with secure random password
        """
        import secrets
        import string
        
        alphabet = string.ascii_letters + string.digits
        password = ''.join(secrets.choice(alphabet) for i in range(length))
        return password

    # ATUM Inventory Methods
    @retry_on_error(max_retries=3)
    def get_atum_locations_from_inventories(self):
        """
        Get ATUM inventory locations by extracting unique locations from all inventory records.
        Since ATUM doesn't expose a direct locations endpoint, we gather them from inventory data.
        
        Returns:
            list: Unique ATUM locations found across all inventories
        """
        try:
            logger.info("Extracting ATUM locations from inventory records...")
            
            # Get all inventory records
            all_inventories = self.get_all_atum_inventories()
            
            if not all_inventories:
                logger.warning("No ATUM inventories found")
                return []
            
            # Extract unique locations
            unique_locations = {}
            
            for inventory in all_inventories:
                location_name = inventory.get('name', '')
                inventory_id = inventory.get('id')
                
                if location_name and location_name not in unique_locations:
                    unique_locations[location_name] = {
                        'id': len(unique_locations) + 1,  # Generate sequential ID
                        'name': location_name,
                        'slug': location_name.lower().replace(' ', '-'),
                        'description': f'ATUM inventory location: {location_name}',
                        'count': 0,
                        'barcode': '',
                        'parent': 0
                    }
                
                # Count products in this location
                if location_name in unique_locations:
                    unique_locations[location_name]['count'] += 1
            
            locations_list = list(unique_locations.values())
            logger.info(f"Found {len(locations_list)} unique ATUM locations")
            
            return locations_list
            
        except Exception as e:
            logger.error(f"Error extracting ATUM locations from inventories: {str(e)}")
            return []

    @retry_on_error(max_retries=3)
    def get_all_atum_inventories(self, batch_size=100):
        """
        Get all ATUM inventory records using pagination.
        
        Args:
            batch_size: Number of inventory records per page
            
        Returns:
            list: All ATUM inventory records
        """
        try:
            all_inventories = []
            first_page = self.get_atum_inventories(page=1, per_page=batch_size)
            
            if not first_page or 'data' not in first_page:
                logger.error("Failed to get initial ATUM inventories data")
                return []
                
            all_inventories.extend(first_page['data'])
            processed = len(first_page['data'])
            logger.info(f"Processing page 1/{first_page['total_pages']} - {processed}/{first_page['total']} inventories")
            
            # Process remaining pages
            for page in range(2, first_page['total_pages'] + 1):
                result = self.get_atum_inventories(page=page, per_page=batch_size)
                if result and result.get('data'):
                    all_inventories.extend(result['data'])
                    processed += len(result['data'])
                    logger.info(f"Processing page {page}/{first_page['total_pages']} - {processed}/{first_page['total']} inventories")
            
            logger.info(f"Retrieved all {len(all_inventories)} ATUM inventory records")
            return all_inventories
                    
        except Exception as e:
            logger.error(f"Error in get_all_atum_inventories: {str(e)}")
            return []

    @retry_on_error(max_retries=3)
    def get_product_inventories(self, product_id):
        """
        Get ATUM inventory data for a specific product.
        
        Args:
            product_id: WooCommerce product ID
            
        Returns:
            list: ATUM inventory records for the product
        """
        try:
            logger.info(f"Fetching ATUM inventories for product {product_id}")
            
            # Add cache-busting parameter to force fresh data
            import time
            cache_buster = int(time.time())
            
            # Get product inventories with cache-busting
            response = self.wcapi.get(f"products/{product_id}/inventories", params={'_': cache_buster})
            
            if not response.ok:
                logger.error(f"Failed to fetch inventories for product {product_id}. Status: {response.status_code}")
                return []
                
            inventories = response.json()
            
            logger.info(f"Found {len(inventories)} ATUM inventory records for product {product_id}")
            return inventories
            
        except Exception as e:
            logger.error(f"Error fetching ATUM inventories for product {product_id}: {str(e)}")
            return []

    @retry_on_error(max_retries=3)
    def get_atum_inventories(self, page=1, per_page=100, product_id=None):
        """
        Get ATUM inventory data using the ATUM REST API.
        
        Args:
            page: Page number for pagination
            per_page: Number of inventory records per page
            product_id: Optional product ID to filter by
            
        Returns:
            dict: API response with inventory data
        """
        try:
            logger.info(f"Fetching ATUM inventories - page {page}, per_page {per_page}")
            
            # ATUM inventories endpoint
            endpoint = "atum/inventories"
            params = {
                'page': page,
                'per_page': per_page
            }
            
            if product_id:
                params['product'] = product_id
            
            response = self.wcapi.get(endpoint, params=params)
            
            if not response.ok:
                logger.error(f"Failed to fetch ATUM inventories. Status: {response.status_code}, Response: {response.text}")
                return None
                
            data = response.json()
            
            # Get total from headers if available
            total = response.headers.get('X-WP-Total', 0)
            total_pages = response.headers.get('X-WP-TotalPages', 1)
            
            result = {
                'data': data,
                'total': int(total),
                'total_pages': int(total_pages),
                'current_page': page
            }
            
            logger.info(f"Successfully retrieved {len(data)} ATUM inventory records from page {page}")
            return result
            
        except Exception as e:
            logger.error(f"Error fetching ATUM inventories: {str(e)}")
            return None

    @retry_on_error(max_retries=3)
    def get_product_attributes(self, page=1, per_page=100):
        """
        Get product attributes from WooCommerce API.
        
        Args:
            page (int): Page number for pagination
            per_page (int): Number of attributes per page
            
        Returns:
            dict: Response containing attributes data and pagination info
        """
        try:
            logger.info(f"Fetching product attributes from WooCommerce (page {page}, per_page {per_page})")
            
            response = self.wcapi.get("products/attributes", params={
                "per_page": per_page,
                "page": page
            })
            
            if not response.ok:
                error_msg = f"Failed to get product attributes. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {'data': [], 'total': 0, 'total_pages': 0, 'current_page': page}
            
            # Get headers for pagination
            total_items = int(response.headers.get('X-WP-Total', 0))
            total_pages = int(response.headers.get('X-WP-TotalPages', 0))
            
            # Parse response data
            data = response.json()
            
            logger.info(f"Retrieved {len(data)} product attributes. Total pages: {total_pages}, Total items: {total_items}")
            
            return {
                'data': data,
                'total': total_items,
                'total_pages': total_pages,
                'current_page': page
            }
            
        except Exception as e:
            logger.error(f"Error getting product attributes: {str(e)}")
            return {'data': [], 'total': 0, 'total_pages': 0, 'current_page': page}

    @retry_on_error(max_retries=3)
    def get_attribute_terms(self, attribute_id, page=1, per_page=100):
        """
        Get terms for a specific product attribute (e.g., brand values).
        
        Args:
            attribute_id (int): The attribute ID
            page (int): Page number for pagination
            per_page (int): Number of terms per page
            
        Returns:
            dict: Response containing terms data and pagination info
        """
        try:
            logger.info(f"Fetching terms for attribute {attribute_id} (page {page}, per_page {per_page})")
            
            response = self.wcapi.get(f"products/attributes/{attribute_id}/terms", params={
                "per_page": per_page,
                "page": page
            })
            
            if not response.ok:
                error_msg = f"Failed to get attribute terms. Status code: {response.status_code}"
                if hasattr(response, 'text'):
                    error_msg += f", Response: {response.text}"
                logger.error(error_msg)
                return {'data': [], 'total': 0, 'total_pages': 0, 'current_page': page}
            
            # Get headers for pagination
            total_items = int(response.headers.get('X-WP-Total', 0))
            total_pages = int(response.headers.get('X-WP-TotalPages', 0))
            
            # Parse response data
            data = response.json()
            
            logger.info(f"Retrieved {len(data)} terms for attribute {attribute_id}. Total pages: {total_pages}, Total items: {total_items}")
            
            return {
                'data': data,
                'total': total_items,
                'total_pages': total_pages,
                'current_page': page
            }
            
        except Exception as e:
            logger.error(f"Error getting attribute terms for attribute {attribute_id}: {str(e)}")
            return {'data': [], 'total': 0, 'total_pages': 0, 'current_page': page}

    @retry_on_error(max_retries=3)
    def get_all_brands_from_woocommerce(self):
        """
        Get all brand terms directly from WooCommerce product attributes.
        
        Returns:
            list: List of brand names from WooCommerce
        """
        try:
            logger.info("Fetching all brands from WooCommerce product attributes")
            
            # First, get all product attributes to find the brand attribute
            attributes_response = self.get_product_attributes(per_page=100)
            
            if not attributes_response or not attributes_response.get('data'):
                logger.warning("No product attributes found in WooCommerce")
                return []
            
            # Find the brand attribute
            brand_attribute_id = None
            for attr in attributes_response['data']:
                if attr.get('slug') == 'pa_brand' or attr.get('name', '').lower() == 'brand':
                    brand_attribute_id = attr.get('id')
                    logger.info(f"Found brand attribute with ID: {brand_attribute_id}")
                    break
            
            if not brand_attribute_id:
                logger.warning("Brand attribute not found in WooCommerce")
                return []
            
            # Get all brand terms
            brands = []
            page = 1
            
            while True:
                terms_response = self.get_attribute_terms(brand_attribute_id, page=page, per_page=100)
                
                if not terms_response or not terms_response.get('data'):
                    break
                
                # Extract brand names from terms
                for term in terms_response['data']:
                    brand_name = term.get('name', '').strip()
                    if brand_name and brand_name not in brands:
                        brands.append(brand_name)
                
                # Check if there are more pages
                if page >= terms_response.get('total_pages', 0):
                    break
                
                page += 1
            
            logger.info(f"Retrieved {len(brands)} brands from WooCommerce: {brands[:10]}{'...' if len(brands) > 10 else ''}")
            return sorted(brands)
            
        except Exception as e:
            logger.error(f"Error getting all brands from WooCommerce: {str(e)}")
            return []

    # ==================== SHIPMENT TRACKING METHODS ====================
    
    @retry_on_error(max_retries=3)
    def get_shipment_trackings(self, order_id):
        """
        Get all shipment trackings for a specific order.
        Uses the standard wc-shipment-tracking/v3 API and enriches
        with products_list from order meta (_wc_shipment_tracking_items).
        
        Args:
            order_id: WooCommerce order ID
            
        Returns:
            list: List of shipment tracking objects or empty list on error
        """
        try:
            logger.info(f"Fetching shipment trackings for order {order_id}")
            
            import requests
            from requests.auth import HTTPBasicAuth
            from concurrent.futures import ThreadPoolExecutor
            import time
            
            no_cache_headers = {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
            auth = HTTPBasicAuth(self.consumer_key, self.consumer_secret)
            
            tracking_url = (
                f"{self.base_url}/wp-json/wc-shipment-tracking/v3"
                f"/orders/{order_id}/shipment-trackings"
                f"?_nocache={int(time.time() * 1000)}"
            )
            meta_url = (
                f"{self.base_url}/wp-json/wc/v3/orders/{order_id}"
                f"?_nocache={int(time.time() * 1000)}"
            )

            def _fetch_trackings():
                return requests.get(
                    tracking_url, auth=auth, verify=False,
                    timeout=30, headers=no_cache_headers
                )

            def _fetch_order_meta():
                return requests.get(
                    meta_url, auth=auth, verify=False,
                    timeout=30, headers=no_cache_headers
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                tracking_future = executor.submit(_fetch_trackings)
                meta_future = executor.submit(_fetch_order_meta)
                response = tracking_future.result()
                meta_resp = meta_future.result()

            if response.ok:
                data = response.json()
                trackings = data if isinstance(data, list) else []
                logger.info(f"Retrieved {len(trackings)} shipment trackings for order {order_id} via standard API")
                
                # Enrich with products_list from order meta (fetched in parallel above)
                try:
                    order_data = meta_resp.json() if meta_resp.ok else None
                    if order_data and 'meta_data' in order_data:
                        tracking_meta = None
                        for meta in order_data['meta_data']:
                            if meta.get('key') == '_wc_shipment_tracking_items':
                                tracking_meta = meta.get('value', [])
                                break
                        
                        if tracking_meta and isinstance(tracking_meta, list):
                            meta_by_number = {}
                            for mt in tracking_meta:
                                tn = mt.get('tracking_number', '')
                                if tn:
                                    meta_by_number[tn] = mt
                            
                            for tracking in trackings:
                                tn = tracking.get('tracking_number', '')
                                if tn and tn in meta_by_number:
                                    meta_entry = meta_by_number[tn]
                                    meta_date = meta_entry.get('date_shipped', '')
                                    if meta_date:
                                        tracking['date_shipped'] = meta_date
                                    raw_products = meta_entry.get('products_list', [])
                                    if raw_products:
                                        products_list = []
                                        for p in raw_products:
                                            if isinstance(p, dict):
                                                products_list.append({
                                                    'product': str(p.get('product', '')),
                                                    'item_id': str(p.get('item_id', '')),
                                                    'qty': str(p.get('qty', '1')),
                                                })
                                            else:
                                                products_list.append({
                                                    'product': str(getattr(p, 'product', '')),
                                                    'item_id': str(getattr(p, 'item_id', '')),
                                                    'qty': str(getattr(p, 'qty', '1')),
                                                })
                                        tracking['products_list'] = products_list
                                        logger.info(f"Enriched tracking {tn} with {len(products_list)} products from order meta")
                except Exception as enrich_err:
                    logger.warning(f"Failed to enrich trackings with products_list for order {order_id}: {str(enrich_err)}")
                
                return trackings
            else:
                logger.warning(f"Failed to get shipment trackings for order {order_id}. Status: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting shipment trackings for order {order_id}: {str(e)}")
            return []
    
    @retry_on_error(max_retries=3)
    def get_shipment_tracking(self, order_id, tracking_id):
        """
        Get a specific shipment tracking by tracking ID.
        
        Args:
            order_id: WooCommerce order ID
            tracking_id: Shipment tracking ID
            
        Returns:
            dict: Shipment tracking object or None on error
        """
        try:
            logger.info(f"Fetching shipment tracking {tracking_id} for order {order_id}")
            
            endpoint = f"wc-shipment-tracking/v3/orders/{order_id}/shipment-trackings/{tracking_id}"
            
            import requests
            from requests.auth import HTTPBasicAuth
            
            url = f"{self.base_url}/wp-json/{endpoint}"
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                verify=False,
                timeout=30
            )
            
            if response.ok:
                data = response.json()
                logger.info(f"Retrieved shipment tracking {tracking_id} for order {order_id}")
                return data
            else:
                logger.warning(f"Failed to get shipment tracking {tracking_id}. Status: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting shipment tracking {tracking_id}: {str(e)}")
            return None
    
    def _is_line_item_non_shippable(self, line_item):
        """
        Check if a WooCommerce order line item is non-shippable.
        Mirrors the frontend isNonShippableItem() logic and the receipt helper
        in views_receipts.py._is_line_item_non_shippable().

        Non-shippable if:
        - MISC items (shipping fees, other charges)
        - Virtual / downloadable products
        - Clinic location (Boca/Jupiter) without _needs_shipping = '1'
        Shippable if:
        - Dropship location (always needs shipping)
        - Clinic location with _needs_shipping = '1'
        - No fulfillment location detected and not otherwise excluded
        """
        name = (line_item.get('name', '') or '').lower()
        sku = (line_item.get('sku', '') or '').lower()

        # MISC items
        if name.startswith('misc:') or name.startswith('misc -') or sku.startswith('misc-') or sku.startswith('misc_'):
            return True
        # LAB items (lab tests, panels) — never need physical shipment
        if name.startswith('lab:') or name.startswith('lab -') or sku.startswith('lab'):
            return True
        # Items named "MISCELLANEOUS" (generic service/fee items)
        if name == 'miscellaneous':
            return True
        # Virtual / downloadable
        if line_item.get('is_virtual') or line_item.get('virtual'):
            return True
        # Shipping / fee line items
        if 'shipping' in name and ('other' in name or 'fee' in name or 'flat rate' in name):
            return True
        # Zero-price items with no product_id (fee placeholders)
        if not line_item.get('product_id') and float(line_item.get('price', 0) or line_item.get('total', 0) or 0) <= 0:
            return True

        meta_data = line_item.get('meta_data', [])
        if not isinstance(meta_data, list):
            return False

        fulfillment = ''
        needs_shipping = None
        for meta in meta_data:
            key = meta.get('key', '')
            value = meta.get('value', '')
            if key == '_fulfillment_location':
                fulfillment = str(value).lower()
            elif key == '_atum_location_name':
                if not fulfillment:
                    fulfillment = str(value).lower()
            elif key == '_needs_shipping':
                needs_shipping = str(value)
            elif key == 'ds_is_credited_service' and str(value) == '1':
                return True

        # Dropship → always needs shipping
        if 'dropship' in fulfillment:
            return False

        # Clinic locations (Boca/Jupiter): check _needs_shipping flag
        is_clinic = 'boca' in fulfillment or 'jupiter' in fulfillment
        if is_clinic:
            return needs_shipping != '1'

        # No fulfillment location → default to shippable (consistent with receipt helper)
        if not fulfillment:
            return False

        return False

    def _bundle_parent_line_item_ids(self, line_items):
        """Line item IDs that are bundle parents (excluded from shipment fulfillment totals)."""
        bundle_parent_ids = set()
        if not isinstance(line_items, list):
            return bundle_parent_ids
        for li in line_items:
            li_meta = li.get('meta_data', [])
            if isinstance(li_meta, list):
                is_parent = any(
                    (m.get('key') == '_ds_bundle_parent_item' and str(m.get('value', '')) == 'true') or
                    (m.get('key') == 'Bundle Type' and str(m.get('value', '')) == 'Parent Item') or
                    m.get('key') == '_bundled_items'
                    for m in li_meta if isinstance(m, dict)
                )
                if is_parent:
                    bundle_parent_ids.add(str(li.get('id', '')))
            if li.get('bundled_items') and isinstance(li.get('bundled_items'), list) and len(li['bundled_items']) > 0:
                bundle_parent_ids.add(str(li.get('id', '')))
        return bundle_parent_ids

    def _required_qty_shippable_line_items(self, line_items, bundle_parent_ids):
        """Map line item id -> ordered qty for shippable lines only."""
        required_qty = {}
        if not isinstance(line_items, list):
            return required_qty
        for li in line_items:
            iid = str(li.get('id', ''))
            if not iid or iid in bundle_parent_ids:
                continue
            if self._is_line_item_non_shippable(li):
                continue
            required_qty[iid] = li.get('quantity', 1)
        return required_qty

    def _aggregate_fulfilled_qty_from_trackings(
        self, all_trackings, required_qty, tracking_number=None, formatted_products=None
    ):
        """
        Sum fulfilled qty per line item from _wc_shipment_tracking_items.
        Matches POS getFulfilledQty: a tracking with empty/missing products_list counts as
        full required qty for every shippable line (one contribution per such tracking).
        If meta is missing products_list for the row matching tracking_number, merge
        formatted_products for that row instead of applying the empty-list rule.
        """
        fulfilled_qty = {}
        if not isinstance(all_trackings, list) or not required_qty:
            return fulfilled_qty

        tn = (tracking_number or '').strip()
        fp = formatted_products if formatted_products else []

        for t in all_trackings:
            if not isinstance(t, dict):
                continue
            raw_pl = t.get('products_list')
            pl = raw_pl if isinstance(raw_pl, list) else []

            use_meta_fallback = (
                tn and fp and t.get('tracking_number') == tn and len(pl) == 0
            )
            if use_meta_fallback:
                pl = fp

            if len(pl) == 0:
                for iid, req in required_qty.items():
                    try:
                        rq = int(req)
                    except (TypeError, ValueError):
                        rq = int(str(req or 0))
                    fulfilled_qty[iid] = fulfilled_qty.get(iid, 0) + rq
                continue

            for p in pl:
                if not isinstance(p, dict):
                    continue
                p_item_id = str(p.get('item_id', '') or '')
                try:
                    p_qty = int(str(p.get('qty', 0) or 0))
                except (TypeError, ValueError):
                    p_qty = 0
                if p_item_id:
                    fulfilled_qty[p_item_id] = fulfilled_qty.get(p_item_id, 0) + p_qty

        return fulfilled_qty

    def _get_shipment_tracking_items_from_order_payload(self, order_payload):
        if not order_payload or not isinstance(order_payload.get('meta_data'), list):
            return []
        for meta in order_payload['meta_data']:
            if meta.get('key') == '_wc_shipment_tracking_items':
                val = meta.get('value', [])
                return val if isinstance(val, list) else []
        return []

    def recalculate_shipment_order_status(
        self, order_id, tracking_data=None, tracking_number=None, formatted_products=None
    ):
        """
        Recompute shipped vs partial-shipped from order line items and tracking meta, then PUT Woo status.

        Args:
            order_id: WooCommerce order ID
            tracking_data: Optional dict with status_shipped ('shipped'|'partial') for fallback when
                fulfillment cannot be inferred from meta.
            tracking_number: If meta row for this tracking has no products_list, use formatted_products.
            formatted_products: Normalized list of {product, item_id, qty} for the current tracking row.

        Returns:
            str: target status applied ('shipped' or 'partial-shipped'), or None if order could not be loaded.
        """
        from requests.auth import HTTPBasicAuth

        try:
            status_url = f"{self.base_url}/wp-json/wc/v3/orders/{order_id}?_nocache={int(time.time() * 1000)}"
            status_resp = requests.get(
                status_url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                verify=False,
                timeout=30,
                headers={'Cache-Control': 'no-cache, no-store', 'Pragma': 'no-cache'},
            )
            updated_order = status_resp.json() if status_resp.ok else None
            if not updated_order:
                logger.warning(f"recalculate_shipment_order_status: could not load order {order_id}")
                return None

            line_items = updated_order.get('line_items', [])
            bundle_parent_ids = self._bundle_parent_line_item_ids(line_items)
            if bundle_parent_ids:
                logger.info(f"Bundle parent line item IDs excluded from fulfillment check: {bundle_parent_ids}")

            required_qty = self._required_qty_shippable_line_items(line_items, bundle_parent_ids)
            all_trackings = self._get_shipment_tracking_items_from_order_payload(updated_order)

            fulfilled_qty = self._aggregate_fulfilled_qty_from_trackings(
                all_trackings,
                required_qty,
                tracking_number=tracking_number,
                formatted_products=formatted_products,
            )

            if not required_qty:
                logger.info(f"No shippable items found for order {order_id}, marking as shipped")
                target_status = 'shipped'
            else:
                any_fulfilled = any(v > 0 for v in fulfilled_qty.values())
                all_fulfilled = True
                if any_fulfilled:
                    for iid, req in required_qty.items():
                        try:
                            need = int(req)
                        except (TypeError, ValueError):
                            need = int(str(req or 0))
                        if fulfilled_qty.get(iid, 0) < need:
                            all_fulfilled = False
                            break

                if any_fulfilled:
                    target_status = 'shipped' if all_fulfilled else 'partial-shipped'
                else:
                    frontend_status = (tracking_data or {}).get('status_shipped', 'partial')
                    target_status = 'shipped' if frontend_status == 'shipped' else 'partial-shipped'

            logger.info(
                f"Auto-determined order status: {target_status} (fulfilled: {fulfilled_qty}, required: {required_qty})"
            )
            if target_status == 'partial-shipped':
                logger.info(
                    f"Order {order_id} remains partial-shipped: fulfilled_qty={fulfilled_qty}, required_qty={required_qty}"
                )

            update_response = self.wcapi.put(f"orders/{order_id}", {"status": target_status})
            if update_response.status_code == 200:
                logger.info(f"Successfully updated order {order_id} to {target_status} status")
            else:
                logger.warning(
                    f"Failed to update order status via wcapi: {update_response.status_code} - {update_response.text[:500]}"
                )
                fallback_url = f"{self.base_url}/wp-json/wc/v3/orders/{order_id}"
                fallback_response = requests.put(
                    fallback_url,
                    auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                    json={"status": target_status},
                    headers={'Content-Type': 'application/json'},
                    verify=False,
                    timeout=30,
                )
                if fallback_response.ok:
                    logger.info(f"Successfully updated order {order_id} to {target_status} via direct REST API")
                else:
                    logger.error(
                        f"Direct REST API fallback also failed: {fallback_response.status_code} - {fallback_response.text[:500]}"
                    )

            return target_status
        except Exception as e:
            logger.error(f"recalculate_shipment_order_status failed for order {order_id}: {e}")
            return None

    @retry_on_error(max_retries=3)
    def create_shipment_tracking(self, order_id, tracking_data):
        """
        Create a new shipment tracking for an order.
        Uses the standard wc-shipment-tracking/v3 API, then patches
        the order meta to inject products_list for partial fulfillment support.
        
        Args:
            order_id: WooCommerce order ID
            tracking_data: Dictionary containing tracking information:
                - tracking_provider: Provider name (e.g., "USPS", "FedEx")
                - tracking_number: Tracking number
                - date_shipped: Date shipped (optional, format: YYYY-MM-DD)
                - custom_tracking_provider: Custom provider name (optional)
                - custom_tracking_link: Custom tracking link (optional)
                - products_list: List of products to track (optional)
                  [{"product_id": "123", "item_id": "456", "qty": 2}, ...]
                - status_shipped: Order status ("shipped" or "partial", optional)
                
        Returns:
            dict: Created shipment tracking object or None on error
        """
        try:
            logger.info(f"Creating shipment tracking for order {order_id}")
            logger.info(f"Tracking data received: {tracking_data}")
            
            # Get the WooCommerce order to map product IDs to line item IDs
            woo_order = self.get_order(order_id)
            if not woo_order:
                logger.error(f"Could not fetch WooCommerce order {order_id}")
                return None
            
            # Build line-item mappings from the WooCommerce order
            product_to_items = defaultdict(list)  # product_id -> [line item ids] (multiple lines per SKU)
            item_to_product = {}   # item_id (str) -> product_id (str)
            valid_item_ids = set()
            
            for line_item in woo_order.get('line_items', []):
                pid = str(line_item.get('product_id', ''))
                iid = str(line_item.get('id', ''))
                if pid and iid:
                    product_to_items[pid].append(iid)
                item_to_product[iid] = pid
                valid_item_ids.add(iid)
                logger.info(f"Line item mapping: product_id={pid} -> item_id={iid}")
            
            # Convert products_list to {product, item_id, qty} format
            formatted_products = []
            if tracking_data.get('products_list'):
                logger.info(f"Original products_list: {tracking_data['products_list']}")
                
                for product in tracking_data['products_list']:
                    sent_product_id = str(product.get('product_id', ''))
                    sent_item_id = str(product.get('item_id', ''))
                    qty = str(product.get('qty', 1))
                    
                    resolved_product_id = ''
                    resolved_item_id = ''
                    
                    # Case 1: Frontend sent both product_id and item_id
                    if sent_item_id and sent_item_id in valid_item_ids:
                        resolved_item_id = sent_item_id
                        resolved_product_id = sent_product_id or item_to_product.get(sent_item_id, '')
                    # Case 2: sent_product_id is actually a line item ID
                    elif sent_product_id in valid_item_ids:
                        resolved_item_id = sent_product_id
                        resolved_product_id = item_to_product.get(sent_product_id, '')
                    # Case 3: sent_product_id is a WooCommerce product ID (only if unambiguous)
                    elif sent_product_id in product_to_items:
                        candidates = product_to_items[sent_product_id]
                        if len(candidates) == 1:
                            resolved_product_id = sent_product_id
                            resolved_item_id = candidates[0]
                        else:
                            logger.warning(
                                f"Ambiguous product_id={sent_product_id} maps to {len(candidates)} line items; "
                                f"send item_id. Skipping row."
                            )
                            continue
                    else:
                        logger.warning(f"Could not resolve IDs for product_id={sent_product_id}, item_id={sent_item_id}")
                        continue
                    
                    formatted_products.append({
                        'product': resolved_product_id,
                        'item_id': resolved_item_id,
                        'qty': qty,
                    })
                    logger.info(f"Resolved: product={resolved_product_id}, item_id={resolved_item_id}, qty={qty}")
            
            import requests
            from requests.auth import HTTPBasicAuth
            import json
            
            # Use standard WC Shipment Tracking API
            wc_endpoint = f"wc-shipment-tracking/v3/orders/{order_id}/shipment-trackings"
            wc_url = f"{self.base_url}/wp-json/{wc_endpoint}"
            
            wc_payload = {
                'tracking_number': tracking_data.get('tracking_number', ''),
                'tracking_provider': tracking_data.get('tracking_provider', ''),
                'custom_tracking_provider': tracking_data.get('custom_tracking_provider', ''),
                'custom_tracking_link': tracking_data.get('custom_tracking_link', ''),
                'date_shipped': tracking_data.get('date_shipped', ''),
                'status_shipped': 0,  # Never let WC plugin set status — our auto-determination handles it
            }
            
            logger.info(f"POST {wc_url}")
            logger.info(f"WC API payload: {json.dumps(wc_payload, indent=2)}")
            
            response = requests.post(
                wc_url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                json=wc_payload,
                headers={'Content-Type': 'application/json'},
                verify=False,
                timeout=30
            )
            
            data = None
            
            if response.ok:
                data = response.json()
                logger.info(f"Created shipment tracking for order {order_id}: {data.get('tracking_id', 'unknown')}")
                
                # Patch order meta to inject products_list and date_shipped into the tracking entry
                # The standard WC API doesn't store products_list and overwrites date_shipped
                logger.info(f"[PATCH DEBUG] formatted_products = {formatted_products}")
                try:
                    import time as _time
                    # Use direct HTTP request with cache-busting to avoid stale meta
                    meta_url = f"{self.base_url}/wp-json/wc/v3/orders/{order_id}?_nocache={int(_time.time() * 1000)}"
                    meta_resp = requests.get(
                        meta_url,
                        auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                        verify=False,
                        timeout=30,
                        headers={'Cache-Control': 'no-cache, no-store', 'Pragma': 'no-cache'}
                    )
                    order_for_meta = meta_resp.json() if meta_resp.ok else None
                    logger.info(f"[PATCH DEBUG] order_for_meta fetched (direct): {bool(order_for_meta)}, status={meta_resp.status_code}")
                    if order_for_meta and 'meta_data' in order_for_meta:
                        meta_id = None
                        tracking_items = None
                        for meta in order_for_meta['meta_data']:
                            if meta.get('key') == '_wc_shipment_tracking_items':
                                meta_id = meta.get('id')
                                tracking_items = meta.get('value', [])
                                logger.info(f"[PATCH DEBUG] Found tracking meta: meta_id={meta_id}, type={type(tracking_items).__name__}, count={len(tracking_items) if isinstance(tracking_items, list) else 'N/A'}, value={str(tracking_items)[:500]}")
                                break
                        
                        if not tracking_items:
                            logger.warning(f"[PATCH DEBUG] No _wc_shipment_tracking_items meta found for order {order_id}")
                        elif not isinstance(tracking_items, list):
                            logger.warning(f"[PATCH DEBUG] tracking_items is not a list, type={type(tracking_items).__name__}")
                        
                        if tracking_items and isinstance(tracking_items, list):
                            tn = wc_payload.get('tracking_number', '')
                            patched = False
                            
                            # Try matching by tracking_number first
                            for idx, ti in enumerate(tracking_items):
                                logger.info(f"[PATCH DEBUG] Checking tracking_items[{idx}]: type={type(ti).__name__}, tn={ti.get('tracking_number', 'N/A') if isinstance(ti, dict) else 'N/A'}")
                                if isinstance(ti, dict) and ti.get('tracking_number') == tn:
                                    ti['date_shipped'] = tracking_data.get('date_shipped', '')
                                    if formatted_products:
                                        ti['products_list'] = formatted_products
                                    patched = True
                                    logger.info(f"[PATCH DEBUG] Matched tracking_number={tn}, patched date_shipped={ti['date_shipped']}" + (f" and products_list" if formatted_products else ""))
                                    break
                            
                            # Fallback: if no match by tracking_number, patch the LAST entry
                            # (the most recently created tracking)
                            if not patched and len(tracking_items) > 0:
                                last_entry = tracking_items[-1]
                                if isinstance(last_entry, dict):
                                    last_entry['date_shipped'] = tracking_data.get('date_shipped', '')
                                    if formatted_products:
                                        last_entry['products_list'] = formatted_products
                                    patched = True
                                    logger.info(f"[PATCH DEBUG] Fallback: patching last entry (tn={last_entry.get('tracking_number', 'N/A')}) with date_shipped={last_entry['date_shipped']}" + (f" and products_list" if formatted_products else ""))
                            
                            if not patched:
                                logger.warning(f"[PATCH DEBUG] Could not patch any tracking entry")
                            
                            if patched and meta_id:
                                patch_payload = {
                                    'meta_data': [{
                                        'id': meta_id,
                                        'key': '_wc_shipment_tracking_items',
                                        'value': tracking_items,
                                    }]
                                }
                                logger.info(f"[PATCH DEBUG] Sending PUT to update order meta")
                                patch_resp = requests.put(
                                    f"{self.base_url}/wp-json/wc/v3/orders/{order_id}",
                                    auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                                    json=patch_payload,
                                    headers={'Content-Type': 'application/json'},
                                    verify=False,
                                    timeout=30
                                )
                                if patch_resp.ok:
                                    logger.info(f"Patched order meta with date_shipped for tracking {tn}")
                                    if formatted_products:
                                        data['products_list'] = formatted_products
                                else:
                                    logger.warning(f"Failed to patch order meta: {patch_resp.status_code} - {patch_resp.text[:500]}")
                except Exception as patch_err:
                    logger.warning(f"Failed to patch order meta: {str(patch_err)}")
            else:
                logger.error(f"Failed to create shipment tracking. Status: {response.status_code} - {response.text[:500]}")
                return None
            
            # Auto-determine order status based on fulfilled items across all trackings
            if data:
                try:
                    self.recalculate_shipment_order_status(
                        order_id,
                        tracking_data=tracking_data,
                        tracking_number=wc_payload.get('tracking_number', ''),
                        formatted_products=formatted_products,
                    )
                except Exception as status_error:
                    logger.error(f"Error determining/updating order status: {str(status_error)}")
            
            return data
                
        except Exception as e:
            logger.error(f"Error creating shipment tracking for order {order_id}: {str(e)}")
            return None
    
    @retry_on_error(max_retries=3)
    def delete_shipment_tracking(self, order_id, tracking_id):
        """
        Delete a shipment tracking from an order.
        Uses the standard wc-shipment-tracking/v3 API.
        
        Args:
            order_id: WooCommerce order ID
            tracking_id: Shipment tracking ID to delete
            
        Returns:
            bool: True if deleted successfully, False otherwise
        """
        try:
            logger.info(f"Deleting shipment tracking {tracking_id} for order {order_id}")
            
            import requests
            from requests.auth import HTTPBasicAuth
            
            wc_endpoint = f"wc-shipment-tracking/v3/orders/{order_id}/shipment-trackings/{tracking_id}"
            url = f"{self.base_url}/wp-json/{wc_endpoint}"
            
            response = requests.delete(
                url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                verify=False,
                timeout=30
            )
            
            if response.ok:
                logger.info(f"Deleted shipment tracking {tracking_id} for order {order_id}")
                
                # Check if there are remaining trackings — if none, revert order to processing
                try:
                    remaining = self.get_shipment_trackings(order_id)
                    if not remaining:
                        logger.info(f"No trackings remaining for order {order_id}, reverting status to processing")
                        update_resp = requests.put(
                            f"{self.base_url}/wp-json/wc/v3/orders/{order_id}",
                            auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                            json={"status": "processing"},
                            headers={'Content-Type': 'application/json'},
                            verify=False,
                            timeout=30
                        )
                        if update_resp.ok:
                            logger.info(f"Reverted order {order_id} to processing status")
                        else:
                            logger.warning(f"Failed to revert order status: {update_resp.status_code}")
                except Exception as status_err:
                    logger.warning(f"Failed to check/revert order status after delete: {str(status_err)}")
                
                return True
            else:
                logger.error(f"Failed to delete shipment tracking {tracking_id}. Status: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error deleting shipment tracking {tracking_id}: {str(e)}")
            return False
    
    @retry_on_error(max_retries=3)
    def get_shipment_tracking_providers(self, order_id):
        """
        Get all available shipment tracking providers.
        
        Args:
            order_id: WooCommerce order ID (required by API but not used)
            
        Returns:
            dict: Dictionary of provider names or empty dict on error
        """
        try:
            logger.info(f"Fetching shipment tracking providers for order {order_id}")
            
            endpoint = f"wc-shipment-tracking/v3/orders/{order_id}/shipment-trackings/providers"
            
            import requests
            from requests.auth import HTTPBasicAuth
            
            url = f"{self.base_url}/wp-json/{endpoint}"
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                verify=False,
                timeout=30
            )
            
            if response.ok:
                data = response.json()
                logger.info(f"Retrieved {len(data) if isinstance(data, dict) else 0} shipment tracking providers")
                return data if isinstance(data, dict) else {}
            else:
                logger.warning(f"Failed to get shipment tracking providers. Status: {response.status_code}")
                return {}
                
        except Exception as e:
            logger.error(f"Error getting shipment tracking providers: {str(e)}")
            return {}
    
    @retry_on_error(max_retries=3)
    def get_memberships(self, customer_id=None, order_id=None, subscription_id=None, status=None):
        """
        Get WooCommerce Memberships via the official Memberships REST API.
        Uses GET /wp-json/wc/v3/memberships/members with query filters.
        
        Docs: https://godaddy-wordpress.github.io/woocommerce-memberships-rest-api-docs/
        
        Args:
            customer_id: Filter by WC customer ID
            order_id: Filter by WC order ID
            subscription_id: Filter by linked subscription ID
            status: Filter by status ('active', 'paused', 'cancelled', etc.)
            
        Returns:
            list: Membership objects, or empty list on failure
        """
        try:
            params = {'per_page': 100}
            if customer_id:
                params['customer'] = customer_id
            if order_id:
                params['order'] = order_id
            if subscription_id:
                params['subscription'] = subscription_id
            if status:
                params['status'] = status

            logger.info(f"Fetching memberships via memberships/members with params: {params}")
            response = self.wcapi.get("memberships/members", params=params)

            if response.ok:
                data = response.json()
                logger.info(f"Retrieved {len(data)} memberships")
                return data
            else:
                logger.warning(f"memberships/members API returned {response.status_code}: {response.text[:300]}")
                return []

        except Exception as e:
            logger.error(f"Error fetching memberships: {str(e)}")
            return []

    @retry_on_error(max_retries=3)
    def update_membership(self, membership_id, data):
        """
        Update a WooCommerce Membership via the official Memberships REST API.
        Uses PUT /wp-json/wc/v3/memberships/members/{id}
        
        Docs: https://godaddy-wordpress.github.io/woocommerce-memberships-rest-api-docs/
        
        Args:
            membership_id: The membership ID to update
            data: Dict of fields to update (subscription_id, status, order_id, etc.)
            
        Returns:
            dict: {'success': True/False, 'data': updated membership or error}
        """
        try:
            logger.info(f"Updating membership {membership_id} with: {data}")
            response = self.wcapi.put(f"memberships/members/{membership_id}", data)

            if response.ok:
                updated = response.json()
                logger.info(f"Successfully updated membership {membership_id}")
                return {'success': True, 'data': updated}
            else:
                error_msg = f"Failed to update membership {membership_id}. Status: {response.status_code}, Response: {response.text[:500]}"
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}

        except Exception as e:
            logger.error(f"Error updating membership {membership_id}: {str(e)}")
            return {'success': False, 'error': str(e)}

    @retry_on_error(max_retries=3)
    def update_subscription_status(self, subscription_id, status):
        """
        Update a WooCommerce Subscription status via the REST API.
        Uses PUT /wp-json/wc/v3/subscriptions/{id}
        
        Args:
            subscription_id: The subscription ID to update
            status: New status (active, on-hold, cancelled, pending-cancel, expired)
            
        Returns:
            dict: {'success': True/False, 'data': updated subscription or error}
        """
        try:
            logger.info(f"Updating subscription {subscription_id} status to: {status}")
            response = self.wcapi.put(f"subscriptions/{subscription_id}", {'status': status})

            if response.ok:
                updated = response.json()
                new_status = updated.get('status', 'unknown')
                logger.info(f"Successfully updated subscription {subscription_id} to status: {new_status}")
                return {'success': True, 'data': updated, 'new_status': new_status}
            else:
                error_msg = f"Failed to update subscription {subscription_id}. Status: {response.status_code}, Response: {response.text[:500]}"
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}

        except Exception as e:
            logger.error(f"Error updating subscription {subscription_id}: {str(e)}")
            return {'success': False, 'error': str(e)}

    @retry_on_error(max_retries=3)
    def update_subscription_status_with_data(self, subscription_id, data):
        """
        Update a WooCommerce Subscription with multiple fields in a single PUT call.
        Uses PUT /wp-json/wc/v3/subscriptions/{id}
        
        Args:
            subscription_id: The subscription ID to update
            data: Dict of fields to update (e.g. {'status': 'cancelled', 'end_date': '2026-03-17T00:00:00'})
            
        Returns:
            dict: {'success': True/False, 'data': updated subscription or error, 'new_status': str}
        """
        try:
            logger.info(f"Updating subscription {subscription_id} with: {data}")
            response = self.wcapi.put(f"subscriptions/{subscription_id}", data)

            if response.ok:
                updated = response.json()
                new_status = updated.get('status', 'unknown')
                logger.info(f"Successfully updated subscription {subscription_id} to status: {new_status}")
                return {'success': True, 'data': updated, 'new_status': new_status}
            else:
                error_msg = f"Failed to update subscription {subscription_id}. Status: {response.status_code}, Response: {response.text[:500]}"
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}

        except Exception as e:
            logger.error(f"Error updating subscription {subscription_id}: {str(e)}")
            return {'success': False, 'error': str(e)}

    @retry_on_error(max_retries=3)
    def update_subscription_end_date(self, subscription_id, end_date):
        """
        Update a WooCommerce Subscription's end_date via the REST API.
        Uses PUT /wp-json/wc/v3/subscriptions/{id}
        
        Args:
            subscription_id: The subscription ID
            end_date: ISO date string (e.g. '2026-03-17T00:00:00')
            
        Returns:
            dict: {'success': True/False}
        """
        try:
            logger.info(f"Setting subscription {subscription_id} end_date to: {end_date}")
            response = self.wcapi.put(f"subscriptions/{subscription_id}", {'end_date': end_date})

            if response.ok:
                logger.info(f"Successfully set end_date for subscription {subscription_id}")
                return {'success': True}
            else:
                error_msg = f"Failed to set end_date for subscription {subscription_id}. Status: {response.status_code}, Response: {response.text[:500]}"
                logger.error(error_msg)
                return {'success': False, 'error': error_msg}

        except Exception as e:
            logger.error(f"Error setting end_date for subscription {subscription_id}: {str(e)}")
            return {'success': False, 'error': str(e)}

    @retry_on_error(max_retries=3)
    def get_customer_memberships(self, customer_id, status=None):
        """
        Get WooCommerce Memberships for a specific customer.
        
        Args:
            customer_id: WooCommerce customer ID
            status: Optional status filter ('active', 'cancelled', etc.)
            
        Returns:
            dict: API response with status and data
        """
        try:
            logger.info(f"Fetching memberships for customer ID: {customer_id}")
            
            # Try the standard WooCommerce Memberships REST API first
            params = {'user': customer_id}
            if status:
                params['status'] = status
            
            try:
                # Try v2 API first (newer)
                logger.info(f"Trying WooCommerce Memberships API v2 with params: {params}")
                response = self.wcapi.get("memberships", params=params)
                
                if response.ok:
                    memberships_data = response.json()
                    logger.info(f"Successfully retrieved {len(memberships_data)} memberships via v2 API")
                    return {
                        'status': 'success',
                        'data': memberships_data,
                        'headers': dict(response.headers),
                        'source': 'wc_memberships_v2'
                    }
                else:
                    logger.warning(f"WC Memberships v2 API failed with status {response.status_code}")
                    
            except Exception as v2_error:
                logger.warning(f"WC Memberships v2 API failed: {str(v2_error)}")
            
            # Try custom REST endpoint as fallback
            logger.info("Trying custom memberships endpoint")
            custom_response = self.get_custom_memberships(customer_id, status)
            
            if custom_response['status'] == 'success':
                return custom_response
            
            # If all methods fail, return warning
            logger.warning(f"No memberships found for customer {customer_id}")
            return {
                'status': 'warning',
                'message': 'No memberships API available or no memberships found',
                'data': []
            }
            
        except Exception as e:
            logger.error(f"Error fetching memberships for customer {customer_id}: {str(e)}")
            return {
                'status': 'error',
                'message': str(e),
                'data': []
            }
    
    @retry_on_error(max_retries=3)
    def get_custom_memberships(self, customer_id=None, status=None):
        """
        Get memberships via custom REST endpoint.
        
        Args:
            customer_id: Optional WooCommerce customer ID
            status: Optional status filter
            
        Returns:
            dict: API response with status and data
        """
        try:
            logger.info(f"Fetching memberships via custom endpoint for customer ID: {customer_id}")
            
            import requests
            from requests.auth import HTTPBasicAuth
            
            # Build endpoint URL
            endpoint = "custom/v1/memberships"
            params = {}
            
            if customer_id:
                params['user'] = customer_id
            if status:
                params['status'] = status
            
            url = f"{self.base_url}/wp-json/{endpoint}"
            
            logger.info(f"Making request to: {url} with params: {params}")
            
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                params=params,
                headers={'Content-Type': 'application/json'},
                verify=False,
                timeout=30
            )
            
            logger.info(f"Custom memberships API response status: {response.status_code}")
            
            if response.ok:
                memberships_data = response.json()
                logger.info(f"Successfully retrieved {len(memberships_data)} memberships via custom endpoint")
                return {
                    'status': 'success',
                    'data': memberships_data,
                    'headers': dict(response.headers),
                    'source': 'custom_endpoint'
                }
            else:
                error_msg = f"Custom memberships API failed. Status: {response.status_code}, Response: {response.text}"
                logger.warning(error_msg)
                return {
                    'status': 'warning',
                    'message': error_msg,
                    'data': []
                }
                
        except Exception as e:
            logger.error(f"Error with custom memberships endpoint: {str(e)}")
            return {
                'status': 'error',
                'message': str(e),
                'data': []
            }
    
    @retry_on_error(max_retries=3)
    def get_membership_plans(self):
        """
        Get all available membership plans.
        
        Returns:
            dict: API response with membership plans
        """
        try:
            logger.info("Fetching membership plans")
            
            # Try to get membership plans via products endpoint with membership type
            params = {
                'type': 'wc_membership_plan',
                'per_page': 100,
                'status': 'publish'
            }
            
            response = self.wcapi.get("products", params=params)
            
            if response.ok:
                plans_data = response.json()
                logger.info(f"Successfully retrieved {len(plans_data)} membership plans")
                return {
                    'status': 'success',
                    'data': plans_data,
                    'headers': dict(response.headers)
                }
            else:
                error_msg = f"Failed to get membership plans. Status: {response.status_code}"
                logger.warning(error_msg)
                return {
                    'status': 'warning',
                    'message': error_msg,
                    'data': []
                }
                
        except Exception as e:
            logger.error(f"Error fetching membership plans: {str(e)}")
            return {
                'status': 'error',
                'message': str(e),
                'data': []
            }

    @retry_on_error(max_retries=3)
    def update_customer(self, woo_customer_id, data):
        """
        Update a WooCommerce customer by ID.

        Args:
            woo_customer_id (int): WooCommerce customer ID
            data (dict): Customer data to update (e.g. billing, shipping fields)

        Returns:
            dict: Updated customer data or None on failure
        """
        try:
            logger.info(f"Updating WooCommerce customer {woo_customer_id} with: {list(data.keys())}")
            response = self.wcapi.put(f"customers/{woo_customer_id}", data)

            if response.ok:
                updated = response.json()
                woo_shipping = updated.get('shipping', {})
                logger.info(f"Successfully updated WooCommerce customer {woo_customer_id} | "
                            f"Woo returned shipping: {woo_shipping}")
                return updated
            else:
                logger.error(f"Failed to update WooCommerce customer {woo_customer_id}. "
                             f"Status: {response.status_code}, Response: {response.text[:500]}")
                return None
        except Exception as e:
            logger.error(f"Error updating WooCommerce customer {woo_customer_id}: {str(e)}")
            return None


def push_contact_to_woo(contact):
    """
    Push POS Contact billing & shipping fields to WooCommerce (POS → WooCommerce sync).
    Uses WooCommerceAPI.update_customer() to update the WooCommerce customer record.

    Args:
        contact: crm.models.Contact instance (must have woo_customer_id)

    Returns:
        dict: {'success': bool, 'updated_fields': list, 'error': str|None}
    """
    if not contact.woo_customer_id:
        logger.info(f"POS→Woo skip: {contact.email} has no woo_customer_id")
        return {'success': False, 'error': 'No woo_customer_id', 'updated_fields': []}

    woo_payload = {
        'first_name': (contact.first_name or '').strip(),
        'last_name': (contact.last_name or '').strip(),
        'email': (contact.email or '').strip(),
        'billing': {
            'first_name': (contact.first_name or '').strip(),
            'last_name': (contact.last_name or '').strip(),
            'email': (contact.email or '').strip(),
            'phone': (contact.phone or '').strip(),
            'address_1': (contact.billing_address or '').strip(),
            'address_2': (contact.billing_address_2 or '').strip(),
            'city': (contact.billing_city or '').strip(),
            'state': (contact.billing_state or '').strip(),
            'postcode': (contact.billing_postcode or '').strip(),
            'country': (contact.billing_country or '').strip(),
        },
        'shipping': {
            'first_name': (contact.first_name or '').strip(),
            'last_name': (contact.last_name or '').strip(),
            'address_1': (contact.shipping_address or '').strip(),
            'address_2': (contact.shipping_address_2 or '').strip(),
            'city': (contact.shipping_city or '').strip(),
            'state': (contact.shipping_state or '').strip(),
            'postcode': (contact.shipping_postcode or '').strip(),
            'country': (contact.shipping_country or '').strip(),
        },
    }

    pushed_fields = list(woo_payload['billing'].keys()) + \
                    [f"shipping_{k}" for k in woo_payload['shipping'].keys()]

    logger.info(
        f"POS→Woo push for {contact.email} (Woo ID: {contact.woo_customer_id}): "
        f"billing + shipping fields | "
        f"shipping_same_as_billing={getattr(contact, 'shipping_same_as_billing', 'N/A')} | "
        f"shipping_payload={woo_payload['shipping']}"
    )

    try:
        wc_api = WooCommerceAPI()
        result = wc_api.update_customer(contact.woo_customer_id, woo_payload)

        if result:
            logger.info(f"✅ POS→Woo push success for {contact.email}")
            return {'success': True, 'updated_fields': pushed_fields, 'error': None}
        else:
            error_msg = 'WooCommerce API returned no data'
            logger.error(f"POS→Woo push failed for {contact.email}: {error_msg}")
            return {'success': False, 'updated_fields': [], 'error': error_msg}

    except Exception as e:
        logger.error(f"POS→Woo push error for {contact.email}: {e}")
        return {'success': False, 'updated_fields': [], 'error': str(e)}
