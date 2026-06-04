from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.contrib.auth import get_user_model
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from oauth2_provider.views import TokenView
from oauth2_provider.models import get_access_token_model, get_refresh_token_model
from oauth2_provider.signals import app_authorized

import json
import logging

logger = logging.getLogger(__name__)
User = get_user_model()
AccessToken = get_access_token_model()
RefreshToken = get_refresh_token_model()

# Sensitive parameters that should not be logged
SENSITIVE_PARAMETERS = [
    'password', 'client_secret', 'code', 'refresh_token', 'token'
]


class CookieTokenView(TokenView):
    """
    Extends the standard OAuth2 token view to also set HTTP-only cookies
    """
    @method_decorator(csrf_exempt)
    @method_decorator(sensitive_post_parameters(*SENSITIVE_PARAMETERS))
    def post(self, request, *args, **kwargs):
        # Use the parent class to handle the token request
        response = super().post(request, *args, **kwargs)
        
        # If the response is successful, set cookies
        if response.status_code == 200:
            try:
                token_data = json.loads(response.content.decode('utf-8'))

                # Log login activity
                try:
                    from users.models import UserActivity
                    from oauth2_provider.models import get_access_token_model
                    AT = get_access_token_model()
                    token_obj = AT.objects.filter(token=token_data.get('access_token')).select_related('user').first()
                    if token_obj and token_obj.user:
                        ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() or request.META.get('REMOTE_ADDR')
                        grant = request.POST.get('grant_type', 'password')
                        if grant == 'password':
                            UserActivity.objects.create(
                                user=token_obj.user,
                                action=f"Logged in",
                                ip_address=ip,
                                category='auth',
                                method='POST',
                                endpoint=request.path[:500],
                                source='manual',
                            )
                except Exception as auth_log_err:
                    logger.debug(f"Auth activity log error: {auth_log_err}")
                
                # Set access token as HTTP-only cookie
                response.set_cookie(
                    'access_token',
                    token_data['access_token'],
                    max_age=token_data.get('expires_in', 3600),
                    httponly=True,
                    samesite='Lax',
                    secure=not settings.DEBUG,  # True in production
                    path='/'
                )
                
                # Set refresh token as HTTP-only cookie if present
                if 'refresh_token' in token_data:
                    response.set_cookie(
                        'refresh_token',
                        token_data['refresh_token'],
                        max_age=settings.OAUTH2_PROVIDER.get('REFRESH_TOKEN_EXPIRE_SECONDS', 2592000),  # 30 days by default
                        httponly=True,
                        samesite='Lax',
                        secure=not settings.DEBUG,  # True in production
                        path='/'
                    )
                
                # Add a flag to indicate the user is authenticated
                response.set_cookie(
                    'is_authenticated',
                    'true',
                    max_age=token_data.get('expires_in', 3600),
                    httponly=False,  # This one can be accessed by JS
                    samesite='Lax',
                    secure=not settings.DEBUG,
                    path='/'
                )
                
                # Ensure the response content remains intact for the frontend to parse
                # This is crucial for the frontend to receive the tokens in the response body
                response.content = json.dumps(token_data).encode('utf-8')
                response['Content-Type'] = 'application/json'
            except Exception as e:
                logger.error(f"Error setting cookies: {str(e)}")
        
        return response


class AuthCheckView(APIView):
    """
    View to check if the user is authenticated
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, *args, **kwargs):
        return Response({'is_authenticated': True})


class RefreshTokenView(APIView):
    """
    View to refresh an access token using a refresh token from cookies
    """
    permission_classes = [permissions.AllowAny]
    
    @method_decorator(sensitive_post_parameters(*SENSITIVE_PARAMETERS))
    def post(self, request, *args, **kwargs):
        # Get refresh token from cookie
        refresh_token = request.COOKIES.get('refresh_token')
        
        if not refresh_token:
            return Response(
                {'error': 'No refresh token provided'},
                status=status.HTTP_400_BAD_REQUEST
            )   
        
        # Create a new request with the refresh token
        token_request = request._request
        token_request.POST = token_request.POST.copy()
        token_request.POST['grant_type'] = 'refresh_token'
        token_request.POST['refresh_token'] = refresh_token
        
        # Use the token view to handle the refresh
        token_view = CookieTokenView()
        return token_view.post(token_request, *args, **kwargs)


class LogoutView(APIView):
    """
    View to log out a user by clearing cookies
    """
    permission_classes = [permissions.AllowAny]
    
    def post(self, request, *args, **kwargs):
        # Log logout before clearing auth
        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            try:
                from users.activity_log import log_activity
                log_activity(request, "Logged out", category='auth')
            except Exception:
                pass

        response = Response({'detail': 'Successfully logged out'})
        
        # Clear all auth cookies
        for cookie_name in ['access_token', 'refresh_token', 'is_authenticated']:
            response.delete_cookie(
                cookie_name,
                path='/'
            )
        
        return response


class UserInfoView(APIView):
    """
    View to get information about the authenticated user
    """
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request, *args, **kwargs):
        user = request.user
        
        # Build role and permissions data
        role_data = None
        permissions_data = {}
        
        try:
            profile = getattr(user, 'profile', None)
            if profile and profile.role:
                role = profile.role
                role_data = {
                    'id': role.id,
                    'name': role.name,
                    'display_name': role.get_name_display(),
                    'description': role.description,
                }
                # Build permissions dict from all boolean toggle fields
                permission_fields = [
                    'can_access_pos', 'can_access_customers', 'can_access_orders',
                    'can_process_refunds', 'can_access_subscriptions',
                    'can_access_membership_subscriptions', 'can_access_payment_plans',
                    'can_manage_users', 'can_manage_products', 'can_manage_inventory',
                    'can_process_orders', 'can_view_reports', 'can_manage_settings',
                ]
                for field in permission_fields:
                    permissions_data[field] = getattr(role, field, False)
        except Exception as e:
            logger.warning(f"Error fetching role data for user {user.username}: {e}")
        
        # Get global permissions_enabled setting
        from    crm.models import POSSetting
        permissions_enabled = POSSetting.get_setting('permissions_enabled', default=False)
        
        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'permissions_enabled': permissions_enabled,
            'role': role_data,
            'permissions': permissions_data,
        })
