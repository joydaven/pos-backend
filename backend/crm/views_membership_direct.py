"""
Direct WooCommerce Membership and Subscription Management API
Connects directly to WooCommerce Cloud SQL database for membership and subscription operations
"""

import os
import json
import re
import logging
from datetime import datetime
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
import pymysql
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class WooCommerceMembershipManager:
    """Direct WooCommerce Cloud SQL database connection for membership management"""
    
    def __init__(self):
        from core.secrets import get_woo_db_config
        woo_cfg = get_woo_db_config()
        self.db_host = woo_cfg['db_host']
        self.db_port = woo_cfg['db_port']
        self.db_name = woo_cfg['db_name']
        self.db_username = woo_cfg['db_username']
        self.db_password = woo_cfg['db_password']

    @contextmanager
    def get_database_connection(self):
        """Direct connection to WooCommerce Cloud SQL database"""
        connection = None
        
        try:
            connection = pymysql.connect(
                host=self.db_host,
                port=int(self.db_port),
                user=self.db_username,
                password=self.db_password,
                database=self.db_name,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10
            )
            
            yield connection
            
        except Exception as e:
            logger.error(f"Database connection error: {str(e)}")
            raise
        finally:
            if connection:
                connection.close()

    def parse_serialized_array(self, serialized_string):
        """Parse PHP serialized array to Python list"""
        if not serialized_string:
            return []
        
        try:
            # Simple regex to extract numbers from serialized PHP array
            # Format: a:4:{i:0;i:278574;i:1;i:278787;i:2;i:285171;i:3;i:285172;}
            matches = re.findall(r'i:\d+;i:(\d+);', serialized_string)
            return [int(match) for match in matches]
        except Exception as e:
            logger.error(f"Error parsing serialized array: {str(e)}")
            return []

    def get_customer_memberships(self, customer_email):
        """Get all memberships for a customer"""
        query = """
        SELECT 
            u.ID AS user_id,
            u.user_email,
            u.display_name,
            m.ID AS membership_id,
            m.post_status AS membership_status,
            pm_start.meta_value AS membership_start,
            pm_end.meta_value AS membership_end,
            m.post_parent AS plan_id,
            p_plan.post_title AS plan_name,
            m.post_date AS created_date,
            m.post_modified AS modified_date
        FROM wp_users u
        JOIN wp_posts m 
            ON m.post_author = u.ID 
           AND m.post_type = 'wc_user_membership'
        LEFT JOIN wp_postmeta pm_start 
            ON pm_start.post_id = m.ID 
           AND pm_start.meta_key = '_start_date'
        LEFT JOIN wp_postmeta pm_end 
            ON pm_end.post_id = m.ID 
           AND pm_end.meta_key = '_end_date'
        LEFT JOIN wp_posts p_plan 
            ON p_plan.ID = m.post_parent
        WHERE u.user_email = %s
        ORDER BY m.post_date DESC
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (customer_email,))
            results = cursor.fetchall()
            
            memberships = []
            for row in results:
                # Format dates
                start_date = None
                end_date = None
                
                if row['membership_start']:
                    try:
                        start_date = datetime.fromtimestamp(int(row['membership_start'])).isoformat()
                    except (ValueError, TypeError):
                        start_date = None
                
                if row['membership_end']:
                    try:
                        end_date = datetime.fromtimestamp(int(row['membership_end'])).isoformat()
                    except (ValueError, TypeError):
                        end_date = None
                
                # Determine status
                current_status = self._determine_membership_status(
                    row['membership_status'],
                    start_date,
                    end_date
                )
                
                membership = {
                    'membership_id': row['membership_id'],
                    'plan_id': row['plan_id'],
                    'plan_name': row['plan_name'],
                    'status': current_status,
                    'raw_status': row['membership_status'],
                    'start_date': start_date,
                    'end_date': end_date,
                    'created_date': row['created_date'].isoformat() if row['created_date'] else None,
                    'modified_date': row['modified_date'].isoformat() if row['modified_date'] else None,
                    'user_id': row['user_id'],
                    'user_email': row['user_email'],
                    'display_name': row['display_name']
                }
                
                memberships.append(membership)
            
            return memberships

    def get_membership_plan_details(self, plan_id):
        """Get detailed plan information including products"""
        plan_query = """
        SELECT 
            plan.ID AS plan_id,
            plan.post_title AS plan_name,
            plan.post_content AS plan_description,
            access_method.meta_value AS grant_access_upon,
            COALESCE(length_type.meta_value, 'unlimited') AS membership_length_type,
            product_ids.meta_value AS raw_product_ids,
            length_amount.meta_value AS length_amount,
            length_period.meta_value AS length_period
        FROM wp_posts plan
        LEFT JOIN wp_postmeta access_method 
            ON access_method.post_id = plan.ID 
           AND access_method.meta_key = '_access_method'
        LEFT JOIN wp_postmeta length_type 
            ON length_type.post_id = plan.ID 
           AND length_type.meta_key = '_access_length_type'
        LEFT JOIN wp_postmeta product_ids
            ON product_ids.post_id = plan.ID
           AND product_ids.meta_key = '_product_ids'
        LEFT JOIN wp_postmeta length_amount
            ON length_amount.post_id = plan.ID
           AND length_amount.meta_key = '_access_length'
        LEFT JOIN wp_postmeta length_period
            ON length_period.post_id = plan.ID
           AND length_period.meta_key = '_access_length_period'
        WHERE plan.ID = %s
          AND plan.post_type = 'wc_membership_plan'
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(plan_query, (plan_id,))
            plan_data = cursor.fetchone()
            
            if not plan_data:
                return None
            
            # Parse product IDs and get product details
            product_ids = self.parse_serialized_array(plan_data['raw_product_ids'])
            products = []
            
            if product_ids:
                product_query = """
                SELECT 
                    p.ID AS product_id, 
                    p.post_title AS product_name,
                    p.post_excerpt AS short_description,
                    p.post_status,
                    pm_price.meta_value AS price,
                    pm_sku.meta_value AS sku
                FROM wp_posts p
                LEFT JOIN wp_postmeta pm_price
                    ON pm_price.post_id = p.ID
                   AND pm_price.meta_key = '_price'
                LEFT JOIN wp_postmeta pm_sku
                    ON pm_sku.post_id = p.ID
                   AND pm_sku.meta_key = '_sku'
                WHERE p.ID IN ({})
                  AND p.post_type = 'product'
                ORDER BY p.post_title
                """.format(','.join(['%s'] * len(product_ids)))
                
                cursor.execute(product_query, product_ids)
                product_results = cursor.fetchall()
                
                for product in product_results:
                    products.append({
                        'product_id': product['product_id'],
                        'name': product['product_name'],
                        'short_description': product['short_description'],
                        'status': product['post_status'],
                        'price': float(product['price']) if product['price'] else 0.0,
                        'sku': product['sku']
                    })
            
            # Format plan details
            plan_details = {
                'plan_id': plan_data['plan_id'],
                'name': plan_data['plan_name'],
                'description': plan_data['plan_description'],
                'grant_access_upon': plan_data['grant_access_upon'],
                'length_type': plan_data['membership_length_type'],
                'length_amount': plan_data['length_amount'],
                'length_period': plan_data['length_period'],
                'products': products,
                'product_count': len(products)
            }
            
            return plan_details

    def update_membership_status(self, membership_id, new_status):
        """Update membership status (cancel, pause, resume)"""
        # Map frontend actions to WooCommerce statuses
        status_mapping = {
            'cancel': 'wcm-cancelled',
            'pause': 'wcm-paused',
            'resume': 'wcm-active',
            'active': 'wcm-active'
        }
        
        woo_status = status_mapping.get(new_status, new_status)
        
        update_query = """
        UPDATE wp_posts 
        SET post_status = %s,
            post_modified = NOW()
        WHERE ID = %s 
          AND post_type = 'wc_user_membership'
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(update_query, (woo_status, membership_id))
            conn.commit()
            
            # Verify update
            verify_query = "SELECT post_status FROM wp_posts WHERE ID = %s"
            cursor.execute(verify_query, (membership_id,))
            result = cursor.fetchone()
            
            return {
                'membership_id': membership_id,
                'new_status': result['post_status'] if result else None,
                'updated': cursor.rowcount > 0
            }

    def _determine_membership_status(self, raw_status, start_date, end_date):
        """Determine user-friendly membership status"""
        now = datetime.now()
        
        # Handle expired memberships
        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                if end_dt < now:
                    return 'expired'
            except:
                pass
        
        # Map WooCommerce statuses to user-friendly names
        status_map = {
            'wcm-active': 'active',
            'wcm-cancelled': 'cancelled',
            'wcm-paused': 'paused',
            'wcm-pending': 'pending',
            'wcm-expired': 'expired',
            'wcm-complimentary': 'complimentary'
        }
        
        return status_map.get(raw_status, raw_status)

    def get_customer_subscriptions(self, customer_email):
        """Get all WooCommerce subscriptions for a customer using HPOS (High-Performance Order Storage)"""
        query = """
        SELECT 
            o.id AS subscription_id,
            o.type AS order_type,
            o.status AS subscription_status,
            o.date_created_gmt AS created_date,
            o.date_updated_gmt AS modified_date,
            o.total_amount AS total_amount,

            -- Customer
            u.ID AS user_id,
            u.user_email,
            u.display_name,

            -- Subscription schedule meta
            MAX(CASE WHEN om.meta_key = '_schedule_start' THEN om.meta_value END) AS start_date,
            MAX(CASE WHEN om.meta_key = '_schedule_trial_end' THEN om.meta_value END) AS trial_end_date,
            MAX(CASE WHEN om.meta_key = '_schedule_next_payment' THEN om.meta_value END) AS next_payment_date,
            MAX(CASE WHEN om.meta_key = '_schedule_cancelled' THEN om.meta_value END) AS cancelled_date,
            MAX(CASE WHEN om.meta_key = '_schedule_end' THEN om.meta_value END) AS end_date,
            MAX(CASE WHEN om.meta_key = '_billing_period' THEN om.meta_value END) AS billing_period,
            MAX(CASE WHEN om.meta_key = '_billing_interval' THEN om.meta_value END) AS billing_interval,
            MAX(CASE WHEN om.meta_key = '_order_source' THEN om.meta_value END) AS order_source,
            MAX(CASE WHEN om.meta_key = '_created_via' THEN om.meta_value END) AS created_via

        FROM wp_wc_orders o
        LEFT JOIN wp_wc_orders_meta om 
               ON o.id = om.order_id
        LEFT JOIN wp_users u 
               ON u.ID = o.customer_id
        WHERE o.type = 'shop_subscription'
          AND u.user_email = %s
        GROUP BY o.id, o.type, o.status, o.date_created_gmt, o.date_updated_gmt, o.total_amount, u.ID, u.user_email, u.display_name
        ORDER BY o.date_created_gmt DESC
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (customer_email,))
            results = cursor.fetchall()
            
            logger.info(f"🔍 Found {len(results)} subscription records for {customer_email}") #Inventory not lining up with Atum locations during ordering            
            # Batch fetch all subscription items in a single query (avoids N+1 SSH tunnel problem)
            subscription_ids = [row['subscription_id'] for row in results]
            items_by_subscription = self._get_subscription_items_batch(cursor, subscription_ids) if subscription_ids else {}
            
            subscriptions = []
            for row in results:
                # Get subscription items from the batch result
                subscription_items = items_by_subscription.get(row['subscription_id'], [])
                
                # Format dates
                start_date = self._format_timestamp_or_date(row['start_date'])
                end_date = self._format_timestamp_or_date(row['end_date'])
                trial_end_date = self._format_timestamp_or_date(row['trial_end_date'])
                
                # For trash/cancelled subscriptions, clear next payment date as WooCommerce does
                if row['subscription_status'] in ['trash', 'wc-trash', 'cancelled', 'wc-cancelled']:
                    next_payment_date = None
                else:
                    next_payment_date = self._format_timestamp_or_date(row['next_payment_date'])
                
                subscription = {
                    'subscription_id': row['subscription_id'],
                    'status': self._determine_subscription_status(row['subscription_status']),
                    'raw_status': row['subscription_status'],
                    'start_date': start_date,
                    'end_date': end_date,
                    'next_payment_date': next_payment_date,
                    'trial_end_date': trial_end_date,
                    'billing_period': row['billing_period'],
                    'billing_interval': row['billing_interval'],
                    'total_amount': float(row['total_amount']) if row['total_amount'] else 0.0,
                    'created_date': row['created_date'].isoformat() if row['created_date'] else None,
                    'modified_date': row['modified_date'].isoformat() if row['modified_date'] else None,
                    'user_id': row['user_id'],
                    'user_email': row['user_email'],
                    'display_name': row['display_name'],
                    'created_via': row['created_via'] or '',
                    'order_source': row['order_source'] or '',
                    'items': subscription_items
                }
                
                subscriptions.append(subscription)
            
            return subscriptions

    def _get_subscription_items_batch(self, cursor, subscription_ids):
        """Batch fetch items for multiple subscriptions in a single query (reuses existing DB cursor)"""
        if not subscription_ids:
            return {}
        
        placeholders = ','.join(['%s'] * len(subscription_ids))
        query = f"""
        SELECT 
            o.id AS subscription_id,
            oi.order_item_id,
            oi.order_item_name AS product_name,
            MAX(CASE WHEN oim.meta_key = '_product_id' THEN oim.meta_value END) AS product_id,
            MAX(CASE WHEN oim.meta_key = '_variation_id' THEN oim.meta_value END) AS variation_id,
            MAX(CASE WHEN oim.meta_key = '_qty' THEN oim.meta_value END) AS quantity,
            MAX(CASE WHEN oim.meta_key = '_line_total' THEN oim.meta_value END) AS line_total,
            MAX(CASE WHEN oim.meta_key = '_line_subtotal' THEN oim.meta_value END) AS line_subtotal

        FROM wp_wc_orders o
        JOIN wp_woocommerce_order_items oi 
              ON o.id = oi.order_id
        JOIN wp_woocommerce_order_itemmeta oim 
              ON oi.order_item_id = oim.order_item_id

        WHERE o.type = 'shop_subscription'
          AND oi.order_item_type = 'line_item'
          AND o.id IN ({placeholders})

        GROUP BY o.id, oi.order_item_id
        ORDER BY o.id, oi.order_item_id
        """
        
        cursor.execute(query, subscription_ids)
        results = cursor.fetchall()
        
        logger.info(f"🔍 Batch fetched {len(results)} items for {len(subscription_ids)} subscriptions")
        
        # Group items by subscription_id
        items_by_subscription = {}
        for row in results:
            sub_id = row['subscription_id']
            item = {
                'order_item_id': row['order_item_id'],
                'product_id': int(row['product_id']) if row['product_id'] else None,
                'variation_id': int(row['variation_id']) if row['variation_id'] and row['variation_id'] != '0' else None,
                'product_name': row['product_name'],
                'quantity': int(row['quantity']) if row['quantity'] else 1,
                'line_total': float(row['line_total']) if row['line_total'] else 0.0,
                'line_subtotal': float(row['line_subtotal']) if row['line_subtotal'] else 0.0
            }
            if sub_id not in items_by_subscription:
                items_by_subscription[sub_id] = []
            items_by_subscription[sub_id].append(item)
        
        return items_by_subscription

    def get_subscription_items(self, subscription_id):
        """Get items (products) for a specific subscription using HPOS with proper item details"""
        query = """
        SELECT 
            o.id AS subscription_id,
            o.status AS subscription_status,
            u.ID AS user_id,
            u.user_email,

            -- Item details
            oi.order_item_id,
            oi.order_item_name AS product_name,
            MAX(CASE WHEN oim.meta_key = '_product_id' THEN oim.meta_value END) AS product_id,
            MAX(CASE WHEN oim.meta_key = '_variation_id' THEN oim.meta_value END) AS variation_id,
            MAX(CASE WHEN oim.meta_key = '_qty' THEN oim.meta_value END) AS quantity,
            MAX(CASE WHEN oim.meta_key = '_line_total' THEN oim.meta_value END) AS line_total,
            MAX(CASE WHEN oim.meta_key = '_line_subtotal' THEN oim.meta_value END) AS line_subtotal

        FROM wp_wc_orders o
        JOIN wp_woocommerce_order_items oi 
              ON o.id = oi.order_id
        JOIN wp_woocommerce_order_itemmeta oim 
              ON oi.order_item_id = oim.order_item_id
        LEFT JOIN wp_users u 
              ON u.ID = o.customer_id

        WHERE o.type = 'shop_subscription'
          AND oi.order_item_type = 'line_item'
          AND o.id = %s

        GROUP BY o.id, oi.order_item_id
        ORDER BY oi.order_item_id
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (subscription_id,))
            results = cursor.fetchall()
            
            logger.info(f"🔍 Found {len(results)} items for subscription {subscription_id}")
            
            items = []
            for row in results:
                item = {
                    'order_item_id': row['order_item_id'],
                    'product_id': int(row['product_id']) if row['product_id'] else None,
                    'variation_id': int(row['variation_id']) if row['variation_id'] and row['variation_id'] != '0' else None,
                    'product_name': row['product_name'],
                    'quantity': int(row['quantity']) if row['quantity'] else 1,
                    'line_total': float(row['line_total']) if row['line_total'] else 0.0,
                    'line_subtotal': float(row['line_subtotal']) if row['line_subtotal'] else 0.0
                }
                items.append(item)
            
            return items

    def update_subscription_status(self, subscription_id, action):
        """Update subscription status (pause, resume, cancel)"""
        # Map frontend actions to WooCommerce subscription statuses
        status_mapping = {
            'pause': 'wc-on-hold',
            'resume': 'wc-active', 
            'cancel': 'wc-cancelled',
            'active': 'wc-active',
            'pending-cancel': 'wc-pending-cancel',
            'expire': 'wc-expired',
            'restore': 'wc-active',  # Restore from trash to active
            'delete': 'trash'  # Move to permanent trash (WooCommerce doesn't have wc-deleted)
        }
        
        woo_status = status_mapping.get(action, action)
        
        update_query = """
        UPDATE wp_wc_orders 
        SET status = %s,
            date_updated_gmt = NOW()
        WHERE id = %s 
          AND type = 'shop_subscription'
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(update_query, (woo_status, subscription_id))
            conn.commit()
            
            # Update subscription meta if needed
            if action == 'pause':
                # Set pause date
                self._update_subscription_meta(cursor, subscription_id, '_schedule_paused_date', str(int(datetime.now().timestamp())))
            elif action == 'resume':
                # Remove pause date and update next payment if needed
                self._remove_subscription_meta(cursor, subscription_id, '_schedule_paused_date')
                # Could calculate new next payment date here
            elif action == 'cancel':
                # Set cancellation date
                self._update_subscription_meta(cursor, subscription_id, '_schedule_cancelled_date', str(int(datetime.now().timestamp())))
            elif action == 'pending-cancel':
                # Set scheduled cancellation date
                self._update_subscription_meta(cursor, subscription_id, '_schedule_cancelled_date', str(int(datetime.now().timestamp())))
            elif action == 'expire':
                # Set end date for expired subscription
                self._update_subscription_meta(cursor, subscription_id, '_schedule_end', str(int(datetime.now().timestamp())))
            
            conn.commit()
            
            # Verify update
            verify_query = "SELECT status FROM wp_wc_orders WHERE id = %s"
            cursor.execute(verify_query, (subscription_id,))
            result = cursor.fetchone()
            
            update_result = {
                'subscription_id': subscription_id,
                'action': action,
                'new_status': result['status'] if result else None,
                'updated': cursor.rowcount > 0
            }
        
        # Sync membership status OUTSIDE the DB connection block
        # Direct SQL doesn't fire WP hooks, so the linked membership
        # won't auto-update. Uses WC Memberships REST API.
        # Note: GHL custom fields are synced automatically by WPFusion plugin.
        if update_result.get('updated'):
            try:
                membership_sync = self._sync_membership_status(subscription_id, action)
                update_result['membership_sync'] = membership_sync
            except Exception as e:
                logger.error(f"Error syncing membership status: {str(e)}")
                update_result['membership_sync'] = {'error': str(e)}
        
        return update_result

    def _update_subscription_meta(self, cursor, subscription_id, meta_key, meta_value):
        """Update or insert subscription metadata using HPOS"""
        query = """
        INSERT INTO wp_wc_orders_meta (order_id, meta_key, meta_value)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE meta_value = VALUES(meta_value)
        """
        cursor.execute(query, (subscription_id, meta_key, meta_value))

    def _remove_subscription_meta(self, cursor, subscription_id, meta_key):
        """Remove subscription metadata using HPOS"""
        query = "DELETE FROM wp_wc_orders_meta WHERE order_id = %s AND meta_key = %s"
        cursor.execute(query, (subscription_id, meta_key))

    def _format_timestamp_or_date(self, date_value):
        """Format timestamp or date string to ISO format"""
        if not date_value:
            return None
        
        try:
            # Try as timestamp first
            if date_value.isdigit():
                return datetime.fromtimestamp(int(date_value)).isoformat()
            else:
                # Try as date string
                dt = datetime.fromisoformat(date_value.replace('Z', '+00:00'))
                return dt.isoformat()
        except (ValueError, TypeError, AttributeError):
            return None

    def _determine_subscription_status(self, raw_status):
        """Determine user-friendly subscription status"""
        status_map = {
            'wc-active': 'active',
            'wc-on-hold': 'on-hold',
            'wc-cancelled': 'cancelled',
            'wc-pending': 'pending',
            'wc-expired': 'expired',
            'wc-pending-cancel': 'pending-cancel',
            'wc-switched': 'switched',
            'wc-trash': 'trash',
            'trash': 'trash',
        }
        
        return status_map.get(raw_status, raw_status)

    def _sync_subscription_status_to_ghl(self, subscription_id, action):
        """
        Sync subscription status changes to GHL
        """
        from .ghl_membership_sync import (
            sync_membership_pause_to_ghl,
            sync_membership_reactivation_to_ghl,
            sync_membership_cancellation_to_ghl,
            extract_billing_schedule_from_order_data
        )
        import logging
        
        logger = logging.getLogger(__name__)
        
        try:
            # Get customer email and subscription details
            customer_email = self._get_customer_email_from_subscription(subscription_id)
            if not customer_email:
                return {'error': 'Customer email not found'}
            
            logger.info(f"🔄 Syncing subscription {subscription_id} action '{action}' to GHL for {customer_email}")
            
            # Get billing schedule info
            billing_info = self._get_subscription_billing_info(subscription_id)
            
            # Prepare data for GHL sync
            sync_data = {
                'subscription_id': subscription_id,
                'billing_interval': billing_info.get('billing_interval', 1),
                'billing_period': billing_info.get('billing_period', 'month'),
                'reason': 'Subscription status change via frontend'
            }
            
            # Call appropriate GHL sync function based on action
            if action == 'pause':
                return sync_membership_pause_to_ghl(customer_email, sync_data)
            elif action in ['resume', 'active', 'restore']:
                return sync_membership_reactivation_to_ghl(customer_email, sync_data)
            elif action in ['cancel', 'delete', 'pending-cancel', 'expire']:
                sync_data['reason'] = f'Subscription {action} via frontend'
                return sync_membership_cancellation_to_ghl(customer_email, sync_data)
            else:
                logger.warning(f"Unknown subscription action for GHL sync: {action}")
                return {'skipped': f'Unknown action: {action}'}
                
        except Exception as e:
            logger.error(f"Error in subscription GHL sync: {str(e)}")
            return {'error': str(e)}

    def _sync_membership_status(self, subscription_id, action):
        """
        Sync membership status when subscription is managed from POS.
        Direct SQL updates don't fire WP hooks, so the linked membership
        won't auto-update. This uses the WC Memberships REST API.
        See docs/MEMBERSHIP_SUBSCRIPTION_LINKING.md
        """
        from .woocommerce import WooCommerceAPI

        # Map POS actions to WC Membership status values
        membership_status_mapping = {
            'pause': 'paused',
            'resume': 'active',
            'cancel': 'cancelled',
            'active': 'active',
            'pending-cancel': 'pending-cancel',
            'expire': 'expired',
            'restore': 'active',
            'delete': 'cancelled',
        }

        membership_status = membership_status_mapping.get(action)
        if not membership_status:
            logger.warning(f"No membership status mapping for action: {action}")
            return {'skipped': f'Unknown action: {action}'}

        try:
            wc = WooCommerceAPI()
            memberships = wc.get_memberships(subscription_id=subscription_id)

            if not memberships:
                logger.info(f"ℹ️ No membership linked to subscription {subscription_id}")
                return {'skipped': 'No linked membership found'}

            membership = memberships[0]
            membership_id = membership.get('id')
            current_status = membership.get('status')

            if current_status == membership_status:
                logger.info(f"ℹ️ Membership {membership_id} already has status '{membership_status}'")
                return {'skipped': 'Status already matches'}

            result = wc.update_membership(membership_id, {'status': membership_status})
            if result.get('success'):
                logger.info(f"✅ Synced membership {membership_id} status to '{membership_status}' (subscription {subscription_id} action: {action})")
            else:
                logger.warning(f"⚠️ Failed to sync membership {membership_id}: {result.get('error')}")
            return result

        except Exception as e:
            logger.error(f"Error syncing membership status for subscription {subscription_id}: {str(e)}")
            return {'error': str(e)}

    def _get_customer_email_from_subscription(self, subscription_id):
        """Get customer email from subscription ID"""
        query = """
        SELECT u.user_email
        FROM wp_wc_orders o
        JOIN wp_users u ON o.customer_id = u.ID
        WHERE o.id = %s AND o.type = 'shop_subscription'
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (subscription_id,))
            result = cursor.fetchone()
            return result['user_email'] if result else None

    def _get_subscription_billing_info(self, subscription_id):
        """Get billing schedule info from subscription"""
        query = """
        SELECT 
            period.meta_value as billing_period,
            interval_meta.meta_value as billing_interval
        FROM wp_wc_orders o
        LEFT JOIN wp_wc_orders_meta period 
            ON period.order_id = o.id AND period.meta_key = '_billing_period'
        LEFT JOIN wp_wc_orders_meta interval_meta 
            ON interval_meta.order_id = o.id AND interval_meta.meta_key = '_billing_interval'
        WHERE o.id = %s AND o.type = 'shop_subscription'
        """
        
        with self.get_database_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (subscription_id,))
            result = cursor.fetchone()
            
            if result:
                return {
                    'billing_period': result['billing_period'] or 'month',
                    'billing_interval': int(result['billing_interval'] or 1)
                }
            else:
                # Default values
                return {
                    'billing_period': 'month',
                    'billing_interval': 1
                }

    def get_customer_subscription_products(self, customer_email):
        """Get membership products for a customer (both POS orders and WooCommerce subscriptions)"""
        logger.info(f"🔍 Fetching membership products for customer: {customer_email}")
        
        membership_products = []
        
        # Get membership products from WooCommerce subscriptions (primary source)
        logger.info(f"🔍 Checking WooCommerce subscriptions for membership products")
        subscriptions = self.get_customer_subscriptions(customer_email)
        
        # Only use POS orders as fallback if no WooCommerce subscriptions exist
        if not subscriptions:
            logger.info(f"🔍 No WooCommerce subscriptions found, checking POS orders as fallback")
            pos_membership_products = self.get_pos_membership_products(customer_email)
            membership_products.extend(pos_membership_products)
        
        for subscription in subscriptions:
            logger.info(f"  Processing subscription {subscription['subscription_id']} with {len(subscription['items'])} items")
            
            # Check if this subscription has billing info (indicates it's a real subscription)
            has_billing_info = (subscription.get('billing_period') or subscription.get('billing_interval') or 
                              subscription.get('next_payment_date') or subscription.get('start_date'))
            
            if len(subscription['items']) > 0:
                # Process subscriptions with items (normal case)
                for item in subscription['items']:
                    # Check if this is a membership-related product
                    product_name = item['product_name'].lower()
                    keywords = ['membership', 'member', 'subscription']
                    is_membership = any(keyword in product_name for keyword in keywords)
                    
                    logger.info(f"    Product: '{item['product_name']}' -> lowercase: '{product_name}' -> is_membership: {is_membership}")
                    
                    if is_membership:
                        subscription_product = {
                            'subscription_id': subscription['subscription_id'],
                            'product_id': item['product_id'],
                            'variation_id': item['variation_id'],
                            'product_name': item['product_name'],
                            'quantity': item['quantity'],
                            'line_total': item['line_total'],
                            'subscription_status': subscription['status'],
                            'raw_subscription_status': subscription['raw_status'],
                            'start_date': subscription['start_date'],
                            'next_payment_date': subscription['next_payment_date'],
                            'billing_period': subscription['billing_period'],
                            'billing_interval': subscription['billing_interval'],
                            'total_amount': subscription['total_amount']
                        }
                        membership_products.append(subscription_product)
                        logger.info(f"    ✅ Added WooCommerce membership product: {item['product_name']}")
            
            elif has_billing_info and subscription['status'] in ['active', 'wc-active']:
                # Include active subscriptions with billing info even if they don't have items
                # This handles HPOS subscriptions that may not have items in wp_wc_order_product_lookup
                logger.info(f"    📋 Active subscription with billing info but no items - including as membership")
                subscription_product = {
                    'subscription_id': subscription['subscription_id'],
                    'product_id': None,  # No specific product ID
                    'variation_id': None,
                    'product_name': f"Membership Subscription #{subscription['subscription_id']}",  # Generic name
                    'quantity': 1,
                    'line_total': subscription['total_amount'],
                    'subscription_status': subscription['status'],
                    'raw_subscription_status': subscription['raw_status'],
                    'start_date': subscription['start_date'],
                    'next_payment_date': subscription['next_payment_date'],
                    'billing_period': subscription['billing_period'],
                    'billing_interval': subscription['billing_interval'],
                    'total_amount': subscription['total_amount']
                }
                membership_products.append(subscription_product)
                logger.info(f"    ✅ Added WooCommerce subscription without items: {subscription['subscription_id']}")
        
        pos_count = 0 if subscriptions else len(membership_products) - len([p for p in membership_products if not str(p['subscription_id']).startswith('pos-')])
        woo_count = len([p for p in membership_products if not str(p['subscription_id']).startswith('pos-')])
        logger.info(f"🎯 Final result: {len(membership_products)} membership products found ({pos_count} from POS, {woo_count} from WooCommerce)")
        return membership_products

    def get_pos_membership_products(self, customer_email):
        """Get membership products from POS orders (fallback when no WooCommerce subscriptions exist)"""
        from .models import Contact, POSOrder
        
        try:
            # Get customer from local database
            customer = Contact.objects.get(email=customer_email)
            logger.info(f"🔍 Found customer in local DB: {customer.id} - {customer.first_name} {customer.last_name}")
            
            # Get POS orders for this customer
            pos_orders = POSOrder.objects.filter(contact=customer).order_by('-created_at')
            logger.info(f"🔍 Found {len(pos_orders)} POS orders for customer")
            
            membership_products = []
            for order in pos_orders:
                logger.info(f"  Processing POS order {order.id} with {len(order.items.all())} items")
                
                for item in order.items.all():
                    # Check if this is a membership-related product
                    product_name = item.name.lower()
                    keywords = ['membership', 'member', 'subscription']
                    is_membership = any(keyword in product_name for keyword in keywords)
                    
                    logger.info(f"    Product: '{item.name}' -> lowercase: '{product_name}' -> is_membership: {is_membership}")
                    
                    if is_membership:
                        # Create a subscription-like object for POS membership products
                        membership_product = {
                            'subscription_id': f"pos-{order.id}",  # Use POS order ID as subscription ID
                            'product_id': str(item.product_id) if item.product_id else None,
                            'variation_id': None,  # POS orders don't have variation IDs in this context
                            'product_name': item.name,
                            'quantity': int(item.quantity),
                            'line_total': float(item.subtotal),
                            'subscription_status': 'active',  # Default to active for POS orders
                            'raw_subscription_status': 'pos-active',
                            'start_date': order.created_at.isoformat() if order.created_at else None,
                            'next_payment_date': None,  # POS orders don't have recurring payments
                            'billing_period': None,
                            'billing_interval': None,
                            'total_amount': float(item.subtotal)
                        }
                        membership_products.append(membership_product)
                        logger.info(f"    ✅ Added POS membership product: {item.name}")
            
            logger.info(f"🎯 Found {len(membership_products)} POS membership products")
            return membership_products
            
        except Exception as e:
            logger.error(f"❌ Error fetching POS membership products: {str(e)}")
            return []

# Initialize the manager
membership_manager = WooCommerceMembershipManager()

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_memberships(request, customer_id):
    """Get all memberships for a customer by customer ID"""
    try:
        # First, get customer email from local database
        from .models import Contact
        try:
            customer = Contact.objects.get(id=customer_id)
            customer_email = customer.email
        except Contact.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
        
        if not customer_email:
            return Response({'error': 'Customer email not available'}, status=status.HTTP_400_BAD_REQUEST)
        
        memberships = membership_manager.get_customer_memberships(customer_email)
        
        return Response({
            'customer_id': customer_id,
            'customer_email': customer_email,
            'memberships': memberships,
            'count': len(memberships)
        })
        
    except Exception as e:
        logger.error(f"Error fetching customer memberships: {str(e)}")
        return Response({
            'error': 'Failed to fetch memberships',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_membership_details(request, membership_id):
    """Get detailed membership information including plan and products"""
    try:
        # First get membership info
        with membership_manager.get_database_connection() as conn:
            cursor = conn.cursor()
            
            membership_query = """
            SELECT 
                m.ID AS membership_id,
                m.post_status AS membership_status,
                m.post_parent AS plan_id,
                pm_start.meta_value AS membership_start,
                pm_end.meta_value AS membership_end,
                u.user_email,
                u.display_name
            FROM wp_posts m
            LEFT JOIN wp_postmeta pm_start 
                ON pm_start.post_id = m.ID 
               AND pm_start.meta_key = '_start_date'
            LEFT JOIN wp_postmeta pm_end 
                ON pm_end.post_id = m.ID 
               AND pm_end.meta_key = '_end_date'
            LEFT JOIN wp_users u ON u.ID = m.post_author
            WHERE m.ID = %s 
              AND m.post_type = 'wc_user_membership'
            """
            
            cursor.execute(membership_query, (membership_id,))
            membership_data = cursor.fetchone()
            
            if not membership_data:
                return Response({'error': 'Membership not found'}, status=status.HTTP_404_NOT_FOUND)
        
        # Get plan details
        plan_details = membership_manager.get_membership_plan_details(membership_data['plan_id'])
        
        # Format dates
        start_date = None
        end_date = None
        
        if membership_data['membership_start']:
            try:
                start_date = datetime.fromtimestamp(int(membership_data['membership_start'])).isoformat()
            except (ValueError, TypeError):
                start_date = None
        
        if membership_data['membership_end']:
            try:
                end_date = datetime.fromtimestamp(int(membership_data['membership_end'])).isoformat()
            except (ValueError, TypeError):
                end_date = None
        
        # Determine status
        current_status = membership_manager._determine_membership_status(
            membership_data['membership_status'],
            start_date,
            end_date
        )
        
        response_data = {
            'membership_id': membership_data['membership_id'],
            'status': current_status,
            'raw_status': membership_data['membership_status'],
            'start_date': start_date,
            'end_date': end_date,
            'customer_email': membership_data['user_email'],
            'customer_name': membership_data['display_name'],
            'plan': plan_details
        }
        
        return Response(response_data)
        
    except Exception as e:
        logger.error(f"Error fetching membership details: {str(e)}")
        return Response({
            'error': 'Failed to fetch membership details',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def manage_membership(request, membership_id):
    """Manage membership (cancel, pause, resume)"""
    try:
        action = request.data.get('action')
        if not action:
            return Response({'error': 'Action is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        if action not in ['cancel', 'pause', 'resume']:
            return Response({'error': 'Invalid action. Must be cancel, pause, or resume'}, status=status.HTTP_400_BAD_REQUEST)
        
        result = membership_manager.update_membership_status(membership_id, action)
        
        if result['updated']:
            return Response({
                'success': True,
                'message': f'Membership {action}ed successfully',
                'membership_id': membership_id,
                'new_status': result['new_status']
            })
        else:
            return Response({
                'error': f'Failed to {action} membership'
            }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        logger.error(f"Error managing membership: {str(e)}")
        return Response({
            'error': f'Failed to {action} membership',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def test_membership_connection(request):
    """Test database connection and return sample data"""
    try:
        # Test with a known email or return structure info
        test_email = request.GET.get('email', 'jennifer@doctorsstudio.com')
        
        with membership_manager.get_database_connection() as conn:
            cursor = conn.cursor()
            
            # Test basic connection with user count
            cursor.execute("SELECT COUNT(*) as user_count FROM wp_users")
            user_count = cursor.fetchone()
            
            # Test membership count
            cursor.execute("SELECT COUNT(*) as membership_count FROM wp_posts WHERE post_type = 'wc_user_membership'")
            membership_count = cursor.fetchone()
            
            # Test plan count
            cursor.execute("SELECT COUNT(*) as plan_count FROM wp_posts WHERE post_type = 'wc_membership_plan'")
            plan_count = cursor.fetchone()
            
            # Try to get sample memberships
            sample_memberships = membership_manager.get_customer_memberships(test_email)
            
        return Response({
            'connection': 'successful',
            'database_stats': {
                'total_users': user_count['user_count'],
                'total_memberships': membership_count['membership_count'],
                'total_plans': plan_count['plan_count']
            },
            'test_email': test_email,
            'sample_memberships': sample_memberships,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Database connection test failed: {str(e)}")
        return Response({
            'connection': 'failed',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_subscription_products(request, customer_id):
    """Get subscription products for a customer (for Product Membership Management)"""
    try:
        # First, get customer email from local database
        from .models import Contact
        try:
            customer = Contact.objects.get(id=customer_id)
            customer_email = customer.email
        except Contact.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
        
        if not customer_email:
            return Response({'error': 'Customer email not available'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Debug: Get all subscriptions first to see what we have
        all_subscriptions = membership_manager.get_customer_subscriptions(customer_email)
        logger.info(f"🔍 All subscriptions for {customer_email}: {len(all_subscriptions)} found")
        
        for sub in all_subscriptions:
            logger.info(f"  Subscription {sub['subscription_id']}: status={sub['status']}, items={len(sub['items'])}")
            for item in sub['items']:
                logger.info(f"    Item: {item['product_name']} (ID: {item['product_id']})")
        
        subscription_products = membership_manager.get_customer_subscription_products(customer_email)
        
        return Response({
            'customer_id': customer_id,
            'customer_email': customer_email,
            'subscription_products': subscription_products,
            'all_subscriptions_debug': all_subscriptions,  # Debug info
            'count': len(subscription_products)
        })
        
    except Exception as e:
        logger.error(f"Error fetching customer subscription products: {str(e)}")
        return Response({
            'error': 'Failed to fetch subscription products',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_customer_subscriptions(request, customer_id):
    """Get all subscriptions for a customer by customer ID"""
    try:
        # First, get customer email from local database
        from .models import Contact
        try:
            customer = Contact.objects.get(id=customer_id)
            customer_email = customer.email
        except Contact.DoesNotExist:
            return Response({'error': 'Customer not found'}, status=status.HTTP_404_NOT_FOUND)
        
        if not customer_email:
            return Response({'error': 'Customer email not available'}, status=status.HTTP_400_BAD_REQUEST)
        
        subscriptions = membership_manager.get_customer_subscriptions(customer_email)
        
        return Response({
            'customer_id': customer_id,
            'customer_email': customer_email,
            'subscriptions': subscriptions,
            'count': len(subscriptions)
        })
        
    except Exception as e:
        logger.error(f"Error fetching customer subscriptions: {str(e)}")
        return Response({
            'error': 'Failed to fetch subscriptions',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def manage_subscription(request, subscription_id):
    """Manage subscription (pause, resume, cancel, delete, restore)"""
    try:
        action = request.data.get('action')
        if not action:
            return Response({'error': 'Action is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        if action not in ['pause', 'resume', 'cancel', 'delete', 'restore', 'active', 'pending-cancel', 'expire']:
            return Response({'error': 'Invalid action. Must be pause, resume, cancel, delete, restore, active, pending-cancel, or expire'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if this is a POS order (subscription_id starts with "pos-")
        if str(subscription_id).startswith('pos-'):
            return Response({
                'success': False,
                'error': 'POS membership products cannot be managed through subscription actions',
                'message': 'This membership was purchased through POS and does not support subscription management (pause/resume/cancel). Contact support for assistance.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Map frontend actions to WooCommerce subscription statuses
        status_mapping = {
            'pause': 'on-hold',
            'resume': 'active',
            'cancel': 'cancelled',
            'active': 'active',
            'pending-cancel': 'pending-cancel',
            'expire': 'expired',
            'restore': 'active',
            'delete': 'trash',
        }
        
        woo_status = status_mapping.get(action)
        if not woo_status:
            return Response({'error': f'Unknown action: {action}'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Use WooCommerce REST API instead of direct SQL (no SSH tunnel needed)
        from .woocommerce import WooCommerceAPI
        wc = WooCommerceAPI()
        
        # For cancel/expire: fetch next_payment_date BEFORE status change to use as end_date
        # For pending-cancel: WooCommerce handles end_date automatically (auto-cancels at next_payment_date)
        end_date = request.data.get('end_date')
        if action in ['cancel', 'expire'] and not end_date:
            try:
                sub_response = wc.wcapi.get(f"subscriptions/{subscription_id}")
                if sub_response.ok:
                    sub_data = sub_response.json()
                    end_date = (
                        sub_data.get('next_payment_date') or 
                        sub_data.get('next_payment_date_gmt') or 
                        sub_data.get('end_date') or 
                        sub_data.get('end_date_gmt')
                    )
                    logger.info(f"📅 Fetched next_payment_date for end_date: {end_date}")
            except Exception as e:
                logger.warning(f"⚠️ Could not fetch subscription for end_date: {str(e)}")
        
        # First: update the status
        result = wc.update_subscription_status_with_data(subscription_id, {'status': woo_status})
        
        # Then: set end_date separately for cancel/expire (avoids WC validation conflict)
        if result.get('success') and end_date and action in ['cancel', 'expire']:
            try:
                formatted_end_date = str(end_date).replace('T', ' ')
                logger.info(f"📅 Setting end_date to {formatted_end_date} (last paid-for day)")
                wc.update_subscription_end_date(subscription_id, formatted_end_date)
            except Exception as e:
                logger.warning(f"⚠️ Could not set end_date: {str(e)}")
        
        if result.get('success'):
            new_status = result.get('new_status', woo_status)
            
            # Also sync membership status via Memberships REST API
            # Subscription controls membership, and REST API fires WP hooks
            # so membership should auto-update. But sync as safety net.
            try:
                membership_status_mapping = {
                    'pause': 'paused', 'resume': 'active', 'cancel': 'cancelled',
                    'active': 'active', 'pending-cancel': 'pending-cancel',
                    'expire': 'expired', 'restore': 'active', 'delete': 'cancelled',
                }
                mem_status = membership_status_mapping.get(action)
                if mem_status:
                    memberships = wc.get_memberships(subscription_id=subscription_id)
                    if memberships:
                        membership = memberships[0]
                        mem_update = {'status': mem_status}
                        if end_date and action in ['cancel', 'pending-cancel', 'expire']:
                            mem_update['end_date'] = end_date
                        if membership.get('status') != mem_status:
                            wc.update_membership(membership['id'], mem_update)
                            logger.info(f"✅ Synced membership {membership['id']} to '{mem_status}'")
            except Exception as e:
                logger.error(f"Error syncing membership status: {str(e)}")
            
            return Response({
                'success': True,
                'message': f'Subscription {action}d successfully',
                'subscription_id': subscription_id,
                'action': action,
                'new_status': new_status
            })
        else:
            return Response({
                'error': result.get('error', f'Failed to {action} subscription')
            }, status=status.HTTP_400_BAD_REQUEST)
        
    except Exception as e:
        logger.error(f"Error managing subscription: {str(e)}")
        return Response({
            'error': f'Failed to {action} subscription',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_subscription_details(request, subscription_id):
    """Get detailed subscription information including items"""
    try:
        with membership_manager.get_database_connection() as conn:
            cursor = conn.cursor()
            
            subscription_query = """
            SELECT 
                s.ID AS subscription_id,
                s.post_status AS subscription_status,
                s.post_date AS created_date,
                s.post_modified AS modified_date,
                u.ID AS user_id,
                u.user_email,
                u.display_name,
                pm_start.meta_value AS start_date,
                pm_end.meta_value AS end_date,
                pm_next_payment.meta_value AS next_payment_date,
                pm_billing_period.meta_value AS billing_period,
                pm_billing_interval.meta_value AS billing_interval,
                pm_total.meta_value AS total_amount,
                pm_pm.meta_value AS payment_method,
                pm_pmt.meta_value AS payment_method_title,
                pm_authnet_type.meta_value AS authnet_cc_type,
                pm_authnet_last4.meta_value AS authnet_cc_last4,
                pm_pos_pm.meta_value AS pos_payment_method
            FROM wp_posts s
            JOIN wp_users u ON u.ID = s.post_author
            LEFT JOIN wp_postmeta pm_start 
                ON pm_start.post_id = s.ID 
               AND pm_start.meta_key = '_schedule_start'
            LEFT JOIN wp_postmeta pm_end 
                ON pm_end.post_id = s.ID 
               AND pm_end.meta_key = '_schedule_end'
            LEFT JOIN wp_postmeta pm_next_payment 
                ON pm_next_payment.post_id = s.ID 
               AND pm_next_payment.meta_key = '_schedule_next_payment'
            LEFT JOIN wp_postmeta pm_billing_period 
                ON pm_billing_period.post_id = s.ID 
               AND pm_billing_period.meta_key = '_billing_period'
            LEFT JOIN wp_postmeta pm_billing_interval 
                ON pm_billing_interval.post_id = s.ID 
               AND pm_billing_interval.meta_key = '_billing_interval'
            LEFT JOIN wp_postmeta pm_total 
                ON pm_total.post_id = s.ID 
               AND pm_total.meta_key = '_order_total'
            LEFT JOIN wp_postmeta pm_pm
                ON pm_pm.post_id = s.ID
               AND pm_pm.meta_key = '_payment_method'
            LEFT JOIN wp_postmeta pm_pmt
                ON pm_pmt.post_id = s.ID
               AND pm_pmt.meta_key = '_payment_method_title'
            LEFT JOIN wp_postmeta pm_authnet_type
                ON pm_authnet_type.post_id = s.ID
               AND pm_authnet_type.meta_key = '_authnet_cc_type'
            LEFT JOIN wp_postmeta pm_authnet_last4
                ON pm_authnet_last4.post_id = s.ID
               AND pm_authnet_last4.meta_key = '_authnet_cc_last4'
            LEFT JOIN wp_postmeta pm_pos_pm
                ON pm_pos_pm.post_id = s.ID
               AND pm_pos_pm.meta_key = '_pos_payment_method'
            WHERE s.ID = %s 
              AND s.post_type = 'shop_subscription'
            """
            
            cursor.execute(subscription_query, (subscription_id,))
            subscription_data = cursor.fetchone()
            
            if not subscription_data:
                return Response({'error': 'Subscription not found'}, status=status.HTTP_404_NOT_FOUND)
        
        # Get subscription items
        subscription_items = membership_manager.get_subscription_items(subscription_id)
        
        # Format dates
        start_date = membership_manager._format_timestamp_or_date(subscription_data['start_date'])
        end_date = membership_manager._format_timestamp_or_date(subscription_data['end_date'])
        next_payment_date = membership_manager._format_timestamp_or_date(subscription_data['next_payment_date'])
        
        # Build payment method title with enrichment
        pm_title = subscription_data.get('payment_method_title') or ''
        pm_method = subscription_data.get('payment_method') or ''
        
        # Enrich from authnet meta
        authnet_type = subscription_data.get('authnet_cc_type')
        authnet_last4 = subscription_data.get('authnet_cc_last4')
        if authnet_type and authnet_last4:
            pm_title = f'{authnet_type} ending in {authnet_last4}'
        
        # Enrich from _pos_payment_method JSON if still generic
        if not pm_title or pm_title.lower() in ('split payment', 'manual renewal', 'pos payment', ''):
            pos_pm_raw = subscription_data.get('pos_payment_method')
            if pos_pm_raw:
                try:
                    import json
                    pos_pm = json.loads(pos_pm_raw) if isinstance(pos_pm_raw, str) else pos_pm_raw
                    if pos_pm.get('name') and pos_pm['name'].lower() != 'split payment':
                        pm_title = pos_pm['name']
                    elif pos_pm.get('brand') and pos_pm.get('last4'):
                        pm_title = f"{pos_pm['brand']} ending in {pos_pm['last4']}"
                except Exception:
                    pass

        response_data = {
            'subscription_id': subscription_data['subscription_id'],
            'status': membership_manager._determine_subscription_status(subscription_data['subscription_status']),
            'raw_status': subscription_data['subscription_status'],
            'start_date': start_date,
            'end_date': end_date,
            'next_payment_date': next_payment_date,
            'billing_period': subscription_data['billing_period'],
            'billing_interval': subscription_data['billing_interval'],
            'total_amount': float(subscription_data['total_amount']) if subscription_data['total_amount'] else 0.0,
            'created_date': subscription_data['created_date'].isoformat() if subscription_data['created_date'] else None,
            'modified_date': subscription_data['modified_date'].isoformat() if subscription_data['modified_date'] else None,
            'customer_email': subscription_data['user_email'],
            'customer_name': subscription_data['display_name'],
            'payment_method': pm_method,
            'payment_method_title': pm_title,
            'items': subscription_items
        }
        
        return Response(response_data)
        
    except Exception as e:
        logger.error(f"Error fetching subscription details: {str(e)}")
        return Response({
            'error': 'Failed to fetch subscription details',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
