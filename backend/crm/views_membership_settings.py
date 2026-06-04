"""
Membership Email Settings API
Handles toggling membership onboarding and cancellation email notifications
"""

import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import POSSetting

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_membership_email_settings(request):
    """
    Get current membership email notification settings.
    
    GET /api/membership/email-settings/
    
    Returns:
        {
            "onboarding_email_enabled": true,
            "cancellation_email_enabled": true
        }
    """
    try:
        onboarding_enabled = POSSetting.get_setting('membership_onboarding_email_enabled', True)
        cancellation_enabled = POSSetting.get_setting('membership_cancellation_email_enabled', True)
        
        return Response({
            'success': True,
            'settings': {
                'onboarding_email_enabled': onboarding_enabled,
                'cancellation_email_enabled': cancellation_enabled
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting membership email settings: {str(e)}")
        return Response({
            'success': False,
            'error': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_membership_email_settings(request):
    """
    Update membership email notification settings.
    
    POST /api/membership/email-settings/
    
    Request body:
    {
        "onboarding_email_enabled": true,
        "cancellation_email_enabled": false
    }
    """
    try:
        data = request.data
        updated_settings = {}
        
        # Update onboarding email setting if provided
        if 'onboarding_email_enabled' in data:
            onboarding_enabled = bool(data['onboarding_email_enabled'])
            POSSetting.set_setting(
                key='membership_onboarding_email_enabled',
                value=onboarding_enabled,
                setting_type='boolean',
                description='Enable/disable membership onboarding email notifications',
                category='membership',
                user=request.user
            )
            updated_settings['onboarding_email_enabled'] = onboarding_enabled
            logger.info(f"Updated membership onboarding email setting to: {onboarding_enabled} by {request.user.username}")
        
        # Update cancellation email setting if provided
        if 'cancellation_email_enabled' in data:
            cancellation_enabled = bool(data['cancellation_email_enabled'])
            POSSetting.set_setting(
                key='membership_cancellation_email_enabled',
                value=cancellation_enabled,
                setting_type='boolean',
                description='Enable/disable membership cancellation email notifications',
                category='membership',
                user=request.user
            )
            updated_settings['cancellation_email_enabled'] = cancellation_enabled
            logger.info(f"Updated membership cancellation email setting to: {cancellation_enabled} by {request.user.username}")
        
        if not updated_settings:
            return Response({
                'success': False,
                'error': 'No settings provided to update'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'success': True,
            'message': 'Membership email settings updated successfully',
            'settings': updated_settings
        })
        
    except Exception as e:
        logger.error(f"Error updating membership email settings: {str(e)}")
        return Response({
            'success': False,
            'error': f'An error occurred: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
