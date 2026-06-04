"""
GoHighLevel (GHL) integration API views for frontend integration management.
"""

import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.core.management import call_command
from io import StringIO
import sys

from .models import Contact
from .ghl_api import search_ghl_contact_by_email, get_ghl_contact_by_id, apply_ghl_contact_fields

logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_ghl_sync_stats(request):
    """
    Get GHL contact ID sync statistics.
    
    Returns:
        JSON response with sync statistics
    """
    try:
        # Get contact statistics
        total_contacts = Contact.objects.count()
        with_ghl_id = Contact.objects.filter(
            ghl_contact_id__isnull=False
        ).exclude(ghl_contact_id__exact='').count()
        
        # Contacts without GHL ID (could be missing sync OR don't exist in GHL)
        without_ghl_id = total_contacts - with_ghl_id
        
        # Contacts that were attempted to sync but not found in GHL
        # (These have been searched but no GHL contact was found)
        not_found_in_ghl = Contact.objects.filter(
            ghl_contact_id__isnull=True,
            ghl_last_sync__isnull=False  # Attempted sync but no ID found
        ).count()
        
        # Contacts that haven't been synced yet (never attempted)
        never_synced = Contact.objects.filter(
            ghl_contact_id__isnull=True,
            ghl_last_sync__isnull=True
        ).count()
        
        # Contacts with potentially outdated GHL IDs (need verification)
        potentially_outdated = Contact.objects.filter(
            ghl_contact_id__isnull=False,
            ghl_last_sync__lt=timezone.now() - timezone.timedelta(days=30)  # Not synced in 30+ days
        ).exclude(ghl_contact_id__exact='').count()
        
        progress_percentage = (with_ghl_id / total_contacts * 100) if total_contacts > 0 else 0
        
        return Response({
            'total_contacts': total_contacts,
            'with_ghl_id': with_ghl_id,
            'without_ghl_id': without_ghl_id,
            'not_found_in_ghl': not_found_in_ghl,
            'never_synced': never_synced,
            'potentially_outdated': potentially_outdated,
            'progress_percentage': progress_percentage
        })
        
    except Exception as e:
        logger.error(f"Error getting GHL sync stats: {str(e)}")
        return Response({
            'error': f'Failed to get GHL sync stats: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_missing_ghl_contact_ids(request):
    """
    Sync GHL contact IDs for contacts missing them.
    
    Query parameters:
        - limit: Maximum number of contacts to process
        - delay: Delay between API requests in seconds
        - dry_run: If true, preview changes without saving
    
    Returns:
        JSON response with sync results
    """
    try:
        # Get query parameters
        limit = request.GET.get('limit', 100)
        delay = request.GET.get('delay', 0.3)
        dry_run = request.GET.get('dry_run', 'false').lower() == 'true'
        
        # Capture command output
        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()
        
        try:
            # Build command arguments
            cmd_args = ['--missing-only', f'--limit={limit}', f'--delay={delay}']
            if dry_run:
                cmd_args.append('--dry-run')
            
            # Call the management command
            call_command('sync_ghl_contact_ids', *cmd_args)
            output = captured_output.getvalue()
            
            # Parse output for statistics (simplified parsing)
            processed = 0
            found = 0
            updated = 0
            skipped = 0
            errors = 0
            
            # Extract numbers from output
            lines = output.split('\n')
            for line in lines:
                if 'Total Processed:' in line:
                    try:
                        processed = int(line.split(':')[1].split('/')[0].strip())
                    except:
                        pass
                elif 'GHL IDs Found:' in line:
                    try:
                        found = int(line.split(':')[1].strip())
                    except:
                        pass
                elif 'Records Updated:' in line:
                    try:
                        updated = int(line.split(':')[1].strip())
                    except:
                        pass
                elif 'Errors:' in line:
                    try:
                        errors = int(line.split(':')[1].strip())
                    except:
                        pass
            
            success_rate = (found / processed * 100) if processed > 0 else 0
            
            return Response({
                'success': True,
                'message': f'GHL sync completed. Processed {processed} contacts, found {found} GHL contact IDs.',
                'processed': processed,
                'found': found,
                'updated': updated,
                'skipped': skipped,
                'errors': errors,
                'success_rate': success_rate
            })
            
        except Exception as cmd_error:
            logger.error(f"GHL sync command failed: {str(cmd_error)}")
            return Response({
                'success': False,
                'message': f'GHL sync failed: {str(cmd_error)}',
                'processed': 0,
                'found': 0,
                'updated': 0,
                'skipped': 0,
                'errors': 1,
                'success_rate': 0
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        finally:
            sys.stdout = old_stdout
            
    except Exception as e:
        logger.error(f"Error in GHL sync endpoint: {str(e)}")
        return Response({
            'success': False,
            'message': f'GHL sync failed: {str(e)}',
            'processed': 0,
            'found': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 1,
            'success_rate': 0
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_existing_ghl_contact_ids(request):
    """
    Verify existing GHL contact IDs to ensure they are current.
    
    Query parameters:
        - limit: Maximum number of contacts to verify
        - delay: Delay between API requests in seconds
    
    Returns:
        JSON response with verification results
    """
    try:
        # Get query parameters
        limit = request.GET.get('limit', 50)
        delay = request.GET.get('delay', 0.3)
        
        # Capture command output
        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()
        
        try:
            # Call the management command for verification
            call_command('sync_ghl_contact_ids', '--verify-existing', f'--limit={limit}', f'--delay={delay}')
            output = captured_output.getvalue()
            
            # Parse output for statistics
            processed = 0
            verified = 0
            updated = 0
            errors = 0
            
            lines = output.split('\n')
            for line in lines:
                if 'Total Processed:' in line:
                    try:
                        processed = int(line.split(':')[1].split('/')[0].strip())
                    except:
                        pass
                elif 'IDs Verified as Current:' in line:
                    try:
                        verified = int(line.split(':')[1].strip())
                    except:
                        pass
                elif 'IDs Updated (Changed):' in line:
                    try:
                        updated = int(line.split(':')[1].strip())
                    except:
                        pass
                elif 'Errors:' in line:
                    try:
                        errors = int(line.split(':')[1].strip())
                    except:
                        pass
            
            success_rate = ((verified + updated) / processed * 100) if processed > 0 else 0
            
            return Response({
                'success': True,
                'message': f'GHL ID verification completed. Processed {processed} contacts, {verified} verified as current, {updated} updated.',
                'processed': processed,
                'verified_current': verified,
                'updated_changed': updated,
                'errors': errors,
                'success_rate': success_rate
            })
            
        except Exception as cmd_error:
            logger.error(f"GHL verification command failed: {str(cmd_error)}")
            return Response({
                'success': False,
                'message': f'GHL verification failed: {str(cmd_error)}',
                'processed': 0,
                'verified_current': 0,
                'updated_changed': 0,
                'errors': 1,
                'success_rate': 0
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        finally:
            sys.stdout = old_stdout
            
    except Exception as e:
        logger.error(f"Error in GHL verification endpoint: {str(e)}")
        return Response({
            'success': False,
            'message': f'GHL verification failed: {str(e)}',
            'processed': 0,
            'verified_current': 0,
            'updated_changed': 0,
            'errors': 1,
            'success_rate': 0
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_priority_ghl_contact_ids(request):
    """
    Sync GHL contact IDs for priority contacts (those with credit points).
    
    Query parameters:
        - limit: Maximum number of contacts to process
        - delay: Delay between API requests in seconds
    
    Returns:
        JSON response with sync results
    """
    try:
        # Get query parameters
        limit = request.GET.get('limit', 50)
        delay = request.GET.get('delay', 0.3)
        
        # Capture command output
        old_stdout = sys.stdout
        sys.stdout = captured_output = StringIO()
        
        try:
            # Call the management command for priority contacts
            call_command('sync_ghl_contact_ids', '--priority-only', f'--limit={limit}', f'--delay={delay}')
            output = captured_output.getvalue()
            
            # Parse output for statistics (simplified parsing)
            processed = 0
            found = 0
            updated = 0
            
            lines = output.split('\n')
            for line in lines:
                if 'Total Processed:' in line:
                    try:
                        processed = int(line.split(':')[1].split('/')[0].strip())
                    except:
                        pass
                elif 'GHL IDs Found:' in line:
                    try:
                        found = int(line.split(':')[1].strip())
                    except:
                        pass
                elif 'Records Updated:' in line:
                    try:
                        updated = int(line.split(':')[1].strip())
                    except:
                        pass
            
            success_rate = (found / processed * 100) if processed > 0 else 0
            
            return Response({
                'success': True,
                'message': f'Priority GHL sync completed. Processed {processed} contacts, found {found} GHL contact IDs.',
                'processed': processed,
                'found': found,
                'updated': updated,
                'skipped': 0,
                'errors': 0,
                'success_rate': success_rate
            })
            
        except Exception as cmd_error:
            logger.error(f"Priority GHL sync command failed: {str(cmd_error)}")
            return Response({
                'success': False,
                'message': f'Priority GHL sync failed: {str(cmd_error)}',
                'processed': 0,
                'found': 0,
                'updated': 0,
                'skipped': 0,
                'errors': 1,
                'success_rate': 0
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
        finally:
            sys.stdout = old_stdout
            
    except Exception as e:
        logger.error(f"Error in priority GHL sync endpoint: {str(e)}")
        return Response({
            'success': False,
            'message': f'Priority GHL sync failed: {str(e)}',
            'processed': 0,
            'found': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 1,
            'success_rate': 0
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def search_ghl_contact(request):
    """
    Search for a GHL contact by email address.
    
    Request body:
        - email: Email address to search for
    
    Returns:
        JSON response with search results
    """
    try:
        email = request.data.get('email')
        if not email:
            return Response({
                'found': False,
                'message': 'Email address is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Search GHL for the contact
        ghl_contact_data = search_ghl_contact_by_email(email)
        
        if ghl_contact_data and 'id' in ghl_contact_data:
            return Response({
                'found': True,
                'contact_id': ghl_contact_data['id'],
                'contact_data': ghl_contact_data,
                'message': f'Found GHL contact for {email}'
            })
        else:
            return Response({
                'found': False,
                'message': f'No GHL contact found for {email}'
            })
            
    except Exception as e:
        logger.error(f"Error searching GHL contact: {str(e)}")
        return Response({
            'found': False,
            'message': f'Search failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ghl_integrity_check(request):
    """
    Run a database integrity check for GHL integration.
    
    Returns:
        JSON response with integrity check results
    """
    try:
        # Get basic statistics
        total_contacts = Contact.objects.count()
        contacts_with_ghl_id = Contact.objects.filter(
            ghl_contact_id__isnull=False
        ).exclude(ghl_contact_id__exact='').count()
        
        # More detailed breakdown
        contacts_without_ghl_id = total_contacts - contacts_with_ghl_id
        contacts_not_found_in_ghl = Contact.objects.filter(
            ghl_contact_id__isnull=True,
            ghl_last_sync__isnull=False
        ).count()
        contacts_never_synced = Contact.objects.filter(
            ghl_contact_id__isnull=True,
            ghl_last_sync__isnull=True
        ).count()
        
        # Get recent sync activity
        recent_syncs = Contact.objects.filter(
            ghl_last_sync__isnull=False,
            ghl_last_sync__gte=timezone.now() - timezone.timedelta(days=7)
        ).count()
        
        # Get last sync time
        last_sync_contact = Contact.objects.filter(
            ghl_last_sync__isnull=False
        ).order_by('-ghl_last_sync').first()
        
        last_sync_time = last_sync_contact.ghl_last_sync.isoformat() if last_sync_contact else None
        
        # Calculate integrity score
        integrity_score = (contacts_with_ghl_id / total_contacts * 100) if total_contacts > 0 else 0
        
        # Contacts with potentially outdated GHL IDs
        contacts_potentially_outdated = Contact.objects.filter(
            ghl_contact_id__isnull=False,
            ghl_last_sync__lt=timezone.now() - timezone.timedelta(days=30)
        ).exclude(ghl_contact_id__exact='').count()
        
        # Generate recommendations
        recommendations = []
        if contacts_never_synced > 0:
            recommendations.append(f"Sync {contacts_never_synced} contacts that haven't been checked against GHL yet")
        if contacts_not_found_in_ghl > 0:
            recommendations.append(f"{contacts_not_found_in_ghl} contacts don't exist in GHL - consider creating them or updating email addresses")
        if contacts_potentially_outdated > 0:
            recommendations.append(f"{contacts_potentially_outdated} contacts may have outdated GHL IDs - consider re-syncing to verify current IDs")
        if recent_syncs < 10:
            recommendations.append("Consider running regular GHL sync operations")
        if integrity_score < 90:
            recommendations.append("Run priority sync for high-value customers first")
        
        return Response({
            'total_contacts': total_contacts,
            'contacts_with_ghl_id': contacts_with_ghl_id,
            'contacts_without_ghl_id': contacts_without_ghl_id,
            'contacts_not_found_in_ghl': contacts_not_found_in_ghl,
            'contacts_never_synced': contacts_never_synced,
            'contacts_potentially_outdated': contacts_potentially_outdated,
            'recent_syncs': recent_syncs,
            'last_sync_time': last_sync_time,
            'integrity_score': round(integrity_score, 1),
            'recommendations': recommendations
        })
        
    except Exception as e:
        logger.error(f"Error in GHL integrity check: {str(e)}")
        return Response({
            'error': f'Integrity check failed: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def test_ghl_connection(request):
    """
    Test GHL API connectivity.
    
    Returns:
        JSON response with connection test results
    """
    try:
        import time
        start_time = time.time()
        
        # Try to search for a test email
        test_result = search_ghl_contact_by_email('test@example.com')
        
        response_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        
        return Response({
            'success': True,
            'message': 'GHL API connection successful',
            'api_status': 'connected',
            'response_time': round(response_time, 2)
        })
        
    except Exception as e:
        logger.error(f"GHL connection test failed: {str(e)}")
        return Response({
            'success': False,
            'message': f'GHL API connection failed: {str(e)}',
            'api_status': 'disconnected'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_recent_ghl_activity(request):
    """
    Get recent GHL sync activity.
    
    Query parameters:
        - limit: Maximum number of recent activities to return (default: 10)
    
    Returns:
        JSON response with recent sync activity
    """
    try:
        limit = int(request.GET.get('limit', 10))
        
        # Get recent contacts that were synced with GHL
        recent_contacts = Contact.objects.filter(
            ghl_contact_id__isnull=False,
            ghl_last_sync__isnull=False
        ).exclude(ghl_contact_id__exact='').order_by('-ghl_last_sync')[:limit]
        
        recent_syncs = []
        for contact in recent_contacts:
            recent_syncs.append({
                'customer_email': contact.email,
                'ghl_contact_id': contact.ghl_contact_id,
                'sync_time': contact.ghl_last_sync.isoformat(),
                'sync_result': 'success'
            })
        
        return Response({
            'recent_syncs': recent_syncs,
            'total_recent': len(recent_syncs)
        })
        
    except Exception as e:
        logger.error(f"Error getting recent GHL activity: {str(e)}")
        return Response({
            'error': f'Failed to get recent activity: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_ghl_performance_metrics(request):
    """
    Get GHL sync performance metrics.
    
    Returns:
        JSON response with performance metrics
    """
    try:
        # Get basic metrics
        total_contacts = Contact.objects.count()
        successful_syncs = Contact.objects.filter(
            ghl_contact_id__isnull=False
        ).exclude(ghl_contact_id__exact='').count()
        failed_syncs = total_contacts - successful_syncs
        
        # Calculate success rate
        success_rate = (successful_syncs / total_contacts * 100) if total_contacts > 0 else 0
        
        # Get recent activity
        last_24h_syncs = Contact.objects.filter(
            ghl_last_sync__isnull=False,
            ghl_last_sync__gte=timezone.now() - timezone.timedelta(days=1)
        ).count()
        
        last_7d_syncs = Contact.objects.filter(
            ghl_last_sync__isnull=False,
            ghl_last_sync__gte=timezone.now() - timezone.timedelta(days=7)
        ).count()
        
        return Response({
            'total_api_calls': total_contacts,  # Approximation
            'successful_syncs': successful_syncs,
            'failed_syncs': failed_syncs,
            'average_response_time': 500,  # Placeholder - could be calculated from actual metrics
            'success_rate': round(success_rate, 1),
            'last_24h_syncs': last_24h_syncs,
            'last_7d_syncs': last_7d_syncs
        })
        
    except Exception as e:
        logger.error(f"Error getting GHL performance metrics: {str(e)}")
        return Response({
            'error': f'Failed to get performance metrics: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync_contact_from_ghl(request, customer_id):
    """
    Manual trigger: fetch fresh data from GHL API and overwrite POS contact fields.
    GHL is the source of truth.

    POST /api/ghl/sync-contact/<customer_id>/
    """
    try:
        contact = Contact.objects.get(id=customer_id)
    except Contact.DoesNotExist:
        return Response(
            {'error': f'Contact {customer_id} not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Determine how to fetch from GHL: by ghl_contact_id or by email
    ghl_data = None
    if contact.ghl_contact_id:
        ghl_data = get_ghl_contact_by_id(contact.ghl_contact_id)
    if not ghl_data and contact.email:
        ghl_data = search_ghl_contact_by_email(contact.email)

    if not ghl_data:
        return Response(
            {'error': f'No GHL contact found for {contact.email}'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Store ghl_contact_id if we didn't have it
    if not contact.ghl_contact_id and ghl_data.get('id'):
        contact.ghl_contact_id = ghl_data['id']

    # Store raw data + timestamp
    contact.ghl_data = ghl_data
    contact.ghl_last_sync = timezone.now()
    contact.save(update_fields=['ghl_contact_id', 'ghl_data', 'ghl_last_sync'])

    # Apply field mapping (GHL → POS)
    updated_fields = apply_ghl_contact_fields(contact, ghl_data)

    logger.info(f"Manual GHL sync for {contact.email}: updated {updated_fields}")

    return Response({
        'success': True,
        'contact_id': str(contact.id),
        'email': contact.email,
        'ghl_contact_id': contact.ghl_contact_id,
        'updated_fields': updated_fields,
    })
