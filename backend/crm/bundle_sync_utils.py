"""
Bundle Sync Utilities for Webhook Integration

This module provides wrapper functions to run comprehensive bundle sync
and default series sync operations from webhook handlers.
"""

import os
import sys
import logging
import subprocess
from django.conf import settings

logger = logging.getLogger(__name__)

def run_comprehensive_bundle_sync(woo_product_id=None):
    """
    Run comprehensive bundle sync for a specific product or all products.
    
    Args:
        woo_product_id: Optional WooCommerce product ID to sync specific product
        
    Returns:
        bool: True if sync completed successfully, False otherwise
    """
    try:
        # Get the backend directory path
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(backend_dir, 'comprehensive_bundle_sync.py')
        
        if not os.path.exists(script_path):
            logger.error(f"Comprehensive bundle sync script not found at {script_path}")
            return False
            
        logger.info(f"🔄 Running comprehensive bundle sync from webhook...")
        
        # Run the script using Django management command approach
        cmd = [
            sys.executable, 
            os.path.join(backend_dir, 'manage.py'), 
            'shell', 
            '-c', 
            f"exec(open('{script_path}').read()); sync = ComprehensiveBundleSync(); sync.run_comprehensive_sync()"
        ]
        
        # Set working directory to backend
        result = subprocess.run(
            cmd,
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Comprehensive bundle sync completed successfully")
            # Log some output for visibility
            if result.stdout:
                logger.info(f"Sync output: {result.stdout[-500:]}")  # Last 500 chars
            return True
        else:
            logger.error(f"❌ Comprehensive bundle sync failed with return code {result.returncode}")
            if result.stderr:
                logger.error(f"Error output: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("⏰ Comprehensive bundle sync timed out after 5 minutes")
        return False
    except Exception as e:
        logger.error(f"❌ Error running comprehensive bundle sync: {str(e)}")
        return False

def run_bundle_default_series_sync(woo_product_id=None):
    """
    Run bundle default series sync for a specific product or all products.
    
    Args:
        woo_product_id: Optional WooCommerce product ID to sync specific product
        
    Returns:
        bool: True if sync completed successfully, False otherwise
    """
    try:
        # Get the backend directory path
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(backend_dir, 'sync_bundle_default_series.py')
        
        if not os.path.exists(script_path):
            logger.error(f"Bundle default series sync script not found at {script_path}")
            return False
            
        logger.info(f"🔄 Running bundle default series sync from webhook...")
        
        # Run the script using Django management command approach
        cmd = [
            sys.executable, 
            os.path.join(backend_dir, 'manage.py'), 
            'shell', 
            '-c', 
            f"exec(open('{script_path}').read()); sync = BundleDefaultSeriesSync(); sync.run_sync()"
        ]
        
        # Set working directory to backend
        result = subprocess.run(
            cmd,
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Bundle default series sync completed successfully")
            # Log some output for visibility
            if result.stdout:
                logger.info(f"Sync output: {result.stdout[-500:]}")  # Last 500 chars
            return True
        else:
            logger.error(f"❌ Bundle default series sync failed with return code {result.returncode}")
            if result.stderr:
                logger.error(f"Error output: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("⏰ Bundle default series sync timed out after 5 minutes")
        return False
    except Exception as e:
        logger.error(f"❌ Error running bundle default series sync: {str(e)}")
        return False

def run_manual_bundle_sync(woo_product_id):
    """
    Run manual bundle sync for a specific product.
    
    Args:
        woo_product_id: WooCommerce product ID to sync
        
    Returns:
        bool: True if sync completed successfully, False otherwise
    """
    try:
        # Get the backend directory path
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(backend_dir, 'manual_bundle_sync.py')
        
        if not os.path.exists(script_path):
            logger.error(f"Manual bundle sync script not found at {script_path}")
            return False
            
        logger.info(f"🔄 Running manual bundle sync for product {woo_product_id}...")
        
        # Run the script using Django management command approach
        cmd = [
            sys.executable, 
            os.path.join(backend_dir, 'manage.py'), 
            'shell', 
            '-c', 
            f"exec(open('{script_path}').read()); sync = ManualBundleSync(); sync.sync_bundle({woo_product_id})"
        ]
        
        # Set working directory to backend
        result = subprocess.run(
            cmd,
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=120  # 2 minute timeout for single product
        )
        
        if result.returncode == 0:
            logger.info(f"✅ Manual bundle sync completed successfully for product {woo_product_id}")
            # Log some output for visibility
            if result.stdout:
                logger.info(f"Sync output: {result.stdout[-300:]}")  # Last 300 chars
            return True
        else:
            logger.error(f"❌ Manual bundle sync failed for product {woo_product_id} with return code {result.returncode}")
            if result.stderr:
                logger.error(f"Error output: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error(f"⏰ Manual bundle sync timed out for product {woo_product_id}")
        return False
    except Exception as e:
        logger.error(f"❌ Error running manual bundle sync for product {woo_product_id}: {str(e)}")
        return False
