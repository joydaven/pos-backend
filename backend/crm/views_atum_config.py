"""
ATUM Inventory Configuration Views
Handles ATUM location mapping and sync configuration
"""

import logging
import subprocess
import os
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
import json

from .models import InventoryLocation, POSSetting
from .woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_atum_locations(request):
    """
    Get all ATUM locations from database
    """
    try:
        # Get all locations with ATUM location IDs
        atum_locations = InventoryLocation.objects.filter(
            atum_location_id__isnull=False
        ).values(
            'id', 'name', 'atum_location_id', 'slug', 'description', 'is_active'
        ).order_by('name')
        
        return Response({
            'success': True,
            'locations': list(atum_locations)
        })
        
    except Exception as e:
        logger.error(f"Error fetching ATUM locations: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_atum_api_locations(request):
    """
    Get sample ATUM locations from WooCommerce API
    """
    try:
        wc_api = WooCommerceAPI()
        
        # Get a sample product to fetch ATUM locations
        from .models import Product
        sample_product = Product.objects.filter(
            product_type__in=['simple', 'variable']
        ).first()
        
        if not sample_product:
            return Response({
                'success': False,
                'error': 'No products found to sample ATUM locations'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get ATUM inventory for sample product
        inventories = wc_api.get_product_inventories(sample_product.woo_product_id)
        
        # Extract unique location names
        api_locations = []
        seen_names = set()
        
        for inventory in inventories:
            location_name = inventory.get('name', '')
            if location_name and location_name not in seen_names:
                api_locations.append({
                    'name': location_name,
                    'id': inventory.get('id'),
                    'sample_product': sample_product.name
                })
                seen_names.add(location_name)
        
        return Response({
            'success': True,
            'api_locations': api_locations,
            'sample_product': sample_product.name
        })
        
    except Exception as e:
        logger.error(f"Error fetching ATUM API locations: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_atum_location(request):
    """
    Create a new ATUM location in database
    """
    try:
        data = request.data
        name = data.get('name', '').strip()
        atum_location_id = data.get('atum_location_id')
        description = data.get('description', '').strip()
        
        if not name:
            return Response({
                'success': False,
                'error': 'Location name is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if location already exists
        if InventoryLocation.objects.filter(name=name).exists():
            return Response({
                'success': False,
                'error': f'Location "{name}" already exists'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Find next available ATUM location ID if not provided
        if not atum_location_id:
            existing_ids = set(
                InventoryLocation.objects.filter(
                    atum_location_id__isnull=False
                ).values_list('atum_location_id', flat=True)
            )
            atum_location_id = 1
            while atum_location_id in existing_ids:
                atum_location_id += 1
        
        # Generate unique code from name
        code = name.upper().replace(' ', '_').replace('.', '').replace('-', '_')[:50]
        
        # Ensure code is unique
        original_code = code
        counter = 1
        while InventoryLocation.objects.filter(code=code).exists():
            code = f"{original_code}_{counter}"
            counter += 1
        
        # Create location
        location = InventoryLocation.objects.create(
            name=name,
            code=code,
            atum_location_id=atum_location_id,
            slug=name.lower().replace(' ', '-').replace('.', ''),
            description=description or f'ATUM location: {name}',
            is_active=True
        )
        
        return Response({
            'success': True,
            'location': {
                'id': location.id,
                'name': location.name,
                'atum_location_id': location.atum_location_id,
                'slug': location.slug,
                'description': location.description,
                'is_active': location.is_active
            }
        })
        
    except Exception as e:
        logger.error(f"Error creating ATUM location: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_atum_locations(request):
    """
    Run the ATUM location restoration script
    """
    try:
        data = request.data
        dry_run = data.get('dry_run', False)
        batch_size = data.get('batch_size', 50)
        product_id = data.get('product_id')
        
        # Build command
        cmd = ['python3', 'manage.py', 'restore_atum_locations']
        
        if dry_run:
            cmd.append('--dry-run')
        
        if batch_size:
            cmd.extend(['--batch-size', str(batch_size)])
            
        if product_id:
            cmd.extend(['--product-id', str(product_id)])
        
        # Get the backend directory
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # Run the command
        logger.info(f"Running ATUM sync command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode == 0:
            return Response({
                'success': True,
                'message': 'ATUM location sync completed successfully',
                'output': result.stdout,
                'dry_run': dry_run
            })
        else:
            return Response({
                'success': False,
                'error': f'Sync failed with return code {result.returncode}',
                'output': result.stdout,
                'stderr': result.stderr
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except subprocess.TimeoutExpired:
        return Response({
            'success': False,
            'error': 'Sync operation timed out (5 minutes)'
        }, status=status.HTTP_408_REQUEST_TIMEOUT)
        
    except Exception as e:
        logger.error(f"Error running ATUM sync: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def atum_location_mappings(request):
    """
    Get or update ATUM location name mappings
    """
    try:
        if request.method == 'GET':
            # Get current mappings from settings
            try:
                setting = POSSetting.objects.get(key='ATUM_LOCATION_MAPPINGS')
                mappings = json.loads(setting.value) if setting.value else {}
            except POSSetting.DoesNotExist:
                mappings = {}
            
            return Response({
                'success': True,
                'mappings': mappings
            })
            
        elif request.method == 'POST':
            # Update mappings
            mappings = request.data.get('mappings', {})
            
            # Validate mappings format
            if not isinstance(mappings, dict):
                return Response({
                    'success': False,
                    'error': 'Mappings must be a dictionary'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Save to settings
            setting, created = POSSetting.objects.get_or_create(
                key='ATUM_LOCATION_MAPPINGS',
                defaults={'value': json.dumps(mappings)}
            )
            
            if not created:
                setting.value = json.dumps(mappings)
                setting.save()
            
            return Response({
                'success': True,
                'message': 'ATUM location mappings updated successfully',
                'mappings': mappings
            })
            
    except Exception as e:
        logger.error(f"Error handling ATUM location mappings: {str(e)}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
