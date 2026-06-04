"""
API views for WooServiceTypes bulk operations (CSV upload)
"""
import csv
import io
import re
from typing import List, Dict, Tuple, Optional

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from django.db import transaction

from crm.models import WooServiceTypes


def parse_service_name(service_name: str) -> Tuple[str, Optional[List[int]]]:
    """
    Parse service name to extract base name and series.
    
    Rules:
    - "Service Name - X series" -> base: "Service Name", series: [X]
    - "Service Name - Single" -> base: "Service Name", series: [1]
    - "Service Name" -> base: "Service Name", series: None
    
    Args:
        service_name: Raw service name from CSV
        
    Returns:
        Tuple of (base_name, series_list or None)
    """
    service_name = service_name.strip()
    
    # Check for " - X series" pattern
    series_pattern = r'^(.+?)\s*-\s*(\d+)\s+series\s*$'
    match = re.match(series_pattern, service_name, re.IGNORECASE)
    if match:
        base_name = match.group(1).strip()
        series_num = int(match.group(2))
        return base_name, [series_num]
    
    # Check for " - Single" pattern
    single_pattern = r'^(.+?)\s*-\s*Single\s*$'
    match = re.match(single_pattern, service_name, re.IGNORECASE)
    if match:
        base_name = match.group(1).strip()
        return base_name, [1]
    
    # No series suffix found
    return service_name, None


@api_view(['POST'])
def bulk_upload_service_types(request):
    """
    Bulk upload service types from CSV file.
    
    Expected CSV format:
    - Header row: post_title,_sku (or just post_title)
    - Data rows: Service name, SKU (optional)
    
    Returns:
    - created: Number of new service types created
    - updated: Number of existing service types updated
    - skipped: Number of rows skipped (invalid data)
    - errors: List of error messages
    """
    if 'file' not in request.FILES:
        return Response(
            {'error': 'No file provided'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    csv_file = request.FILES['file']
    
    # Validate file type
    if not csv_file.name.endswith('.csv'):
        return Response(
            {'error': 'File must be a CSV'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Read CSV file
        decoded_file = csv_file.read().decode('utf-8')
        csv_reader = csv.DictReader(io.StringIO(decoded_file))
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        errors = []
        
        # First pass: Group rows by base name and accumulate series
        service_data = {}  # {base_name: set(series_values)}
        
        for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 (header is row 1)
            # Get service name from either 'post_title' or 'name' column
            raw_name = row.get('post_title', '') or row.get('name', '')
            
            if not raw_name or not raw_name.strip():
                skipped_count += 1
                errors.append(f'Row {row_num}: Empty service name')
                continue
            
            try:
                # Parse service name
                base_name, series = parse_service_name(raw_name)
                
                # Initialize or accumulate series for this base name
                if base_name not in service_data:
                    service_data[base_name] = set()
                
                # Add series values if present
                if series:
                    service_data[base_name].update(series)
                    
            except Exception as e:
                skipped_count += 1
                errors.append(f'Row {row_num}: {str(e)}')
        
        # Second pass: Create or update service types with accumulated series
        with transaction.atomic():
            for base_name, series_set in service_data.items():
                try:
                    # Convert set to sorted list (for consistent ordering)
                    series_list = sorted(list(series_set)) if series_set else None
                    
                    # Update or create service type
                    service_type, created = WooServiceTypes.objects.update_or_create(
                        name=base_name,
                        defaults={
                            'series': series_list,
                            'updated_at': timezone.now(),
                        }
                    )
                    
                    if created:
                        created_count += 1
                    else:
                        updated_count += 1
                        
                except Exception as e:
                    skipped_count += 1
                    errors.append(f'Service "{base_name}": {str(e)}')
        
        return Response({
            'success': True,
            'created': created_count,
            'updated': updated_count,
            'skipped': skipped_count,
            'errors': errors,
            'message': f'Processed {created_count + updated_count} service types successfully'
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        return Response(
            {'error': f'Failed to process CSV: {str(e)}'},
            status=status.HTTP_400_BAD_REQUEST
        )


@api_view(['GET'])
def download_service_types_template(request):
    """
    Download a CSV template for service types bulk upload.
    """
    from django.http import HttpResponse
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="service_types_template.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['post_title', '_sku'])
    writer.writerow(['LAB: Baseline/Annual - 1 series', 'LAB001'])
    writer.writerow(['LAB: Baseline/Annual - 3 series', 'LAB003'])
    writer.writerow(['LAB: Baseline/Annual - 6 series', 'LAB006'])
    writer.writerow(['LAB: Follow-Up - Single', 'LAB-FU'])
    writer.writerow(['Service: Consultation', 'CONSULT'])
    
    return response
