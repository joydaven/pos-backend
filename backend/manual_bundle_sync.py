#!/usr/bin/env python
"""
Manual Bundle Sync Script

Use this script when you change bundle item configurations in WooCommerce
(like default series selections) that don't trigger automatic webhooks.

Usage:
python manual_bundle_sync.py <bundle_woo_id>

Example:
python manual_bundle_sync.py 278975  # Sync Longevity 6M bundle
"""

import os
import sys
import django
import requests
from datetime import datetime

# Add the project directory to the Python path
sys.path.append('/var/www/newpos-posds/woo-ghl-contact-db/backend')

# Set up Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'dialpad_integration.settings')
django.setup()

from crm.models import Product, ProductBundle
from crm.config import WOO_API_URL, WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET

def manual_sync_bundle(bundle_woo_id):
    """Manually sync a specific bundle from WooCommerce"""
    
    print(f"=== Manual Bundle Sync: WooID {bundle_woo_id} ===\n")
    
    try:
        # Find the bundle product in our database
        bundle_product = Product.objects.get(woo_product_id=bundle_woo_id)
        print(f"Found bundle: {bundle_product.name}")
        
        # Fetch fresh data from WooCommerce
        url = f"{WOO_API_URL}/products/{bundle_woo_id}"
        response = requests.get(url, auth=(WOO_CONSUMER_KEY, WOO_CONSUMER_SECRET))
        
        if response.status_code != 200:
            print(f"❌ Failed to fetch from WooCommerce: {response.status_code}")
            print("Please check WooCommerce API credentials in environment variables:")
            print("- WOO_API_URL")
            print("- WOO_CONSUMER_KEY") 
            print("- WOO_CONSUMER_SECRET")
            return False
            
        fresh_data = response.json()
        bundled_items = fresh_data.get('bundled_items', [])
        
        print(f"Fetched fresh data with {len(bundled_items)} bundled items")
        
        # Process bundled items and extract default series
        processed_bundle_items = []
        default_series_by_item = {}
        updated_items = []
        
        for item in bundled_items:
            item_id = item.get('product_id')
            if not item_id:
                continue
            
            # Check if bundled product exists in our database
            try:
                bundled_product = Product.objects.get(woo_product_id=item_id)
                item['local_product_id'] = str(bundled_product.id)
                
                # Extract and store default series selections
                default_attrs = item.get('default_variation_attributes', [])
                if default_attrs:
                    default_series_by_item[str(item_id)] = default_attrs
                    
                    # Show what was updated
                    series_option = "Unknown"
                    for attr in default_attrs:
                        if attr.get('name') == 'Series':
                            series_option = attr.get('option')
                            break
                    
                    updated_items.append({
                        'name': item.get('title', 'Unknown'),
                        'woo_id': item_id,
                        'series': series_option
                    })
                
                processed_bundle_items.append(item)
                print(f"  ✅ {item.get('title')} (WooID: {item_id})")
                
            except Product.DoesNotExist:
                print(f"  ❌ {item.get('title')} (WooID: {item_id}) -> NOT FOUND locally")
        
        # Update bundle data with fresh WooCommerce data
        fresh_data['processed_bundle_items'] = processed_bundle_items
        fresh_data['default_series_by_item'] = default_series_by_item
        fresh_data['last_synced'] = datetime.now().isoformat()
        
        # Also update the original bundled_items with fresh default_variation_attributes
        for original_item in fresh_data.get('bundled_items', []):
            item_id = original_item.get('product_id')
            if item_id and str(item_id) in default_series_by_item:
                original_item['default_variation_attributes'] = default_series_by_item[str(item_id)]
        
        # Update ProductBundle record
        product_bundle, created = ProductBundle.objects.get_or_create(
            product_id=bundle_product.id,
            defaults={
                'woo_data': fresh_data,
                'created_at': datetime.now(),
                'updated_at': datetime.now()
            }
        )
        
        if not created:
            product_bundle.woo_data = fresh_data
            product_bundle.updated_at = datetime.now()
            product_bundle.save()
        
        print(f"\n✅ Bundle sync completed successfully!")
        
        # Show items with default series that were updated
        if updated_items:
            print(f"\nItems with default series:")
            for item in updated_items:
                print(f"  - {item['name']} → {item['series']}")
        
        return True
        
    except Product.DoesNotExist:
        print(f"❌ Bundle with WooID {bundle_woo_id} not found in local database")
        return False
        
    except Exception as e:
        print(f"❌ Error syncing bundle: {str(e)}")
        return False

def list_known_bundles():
    """List all known bundle products"""
    
    print("=== Known Bundle Products ===\n")
    
    # Get all products that have ProductBundle records
    bundle_products = []
    for bundle in ProductBundle.objects.all():
        try:
            product = Product.objects.get(id=bundle.product_id)
            bundle_products.append(product)
        except Product.DoesNotExist:
            continue
    
    if bundle_products:
        print("Available bundles for manual sync:")
        for product in bundle_products:
            print(f"  - {product.name} (WooID: {product.woo_product_id})")
        
        print(f"\nTo sync a bundle: python manual_bundle_sync.py <woo_id>")
    else:
        print("No bundle products found in database")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python manual_bundle_sync.py <bundle_woo_id>")
        print("       python manual_bundle_sync.py list")
        print("\nExample: python manual_bundle_sync.py 278975")
        sys.exit(1)
    
    arg = sys.argv[1]
    
    if arg.lower() == 'list':
        list_known_bundles()
    else:
        try:
            bundle_woo_id = int(arg)
            manual_sync_bundle(bundle_woo_id)
        except ValueError:
            print("Error: Bundle WooID must be a number")
            sys.exit(1)
