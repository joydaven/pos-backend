from django.shortcuts import render
from django.contrib.auth import get_user_model
from django.conf import settings
from django.utils import timezone

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from google.oauth2 import id_token
from google.auth.transport import requests

from oauth2_provider.models import Application, AccessToken, RefreshToken
from oauth2_provider.settings import oauth2_settings

import datetime
import uuid
import logging
import json

# Set up logger
logger = logging.getLogger(__name__)

User = get_user_model()

from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework.authentication import BaseAuthentication
from crm.throttles import AuthRateThrottle

class CsrfExemptSessionAuthentication(BaseAuthentication):
    """Custom authentication class that skips CSRF checks"""
    def authenticate(self, request):
        return None  # Skip authentication entirely

@method_decorator(csrf_exempt, name='dispatch')
class GoogleLoginView(APIView):
    """View for handling Google OAuth login"""
    authentication_classes = [CsrfExemptSessionAuthentication]
    permission_classes = [AllowAny]  # Allow unauthenticated access
    throttle_classes = [AuthRateThrottle]
    
    def post(self, request):
        """Process Google ID token and return OAuth2 tokens"""
        logger.info("GoogleLoginView: Processing login request")
        google_token = request.data.get('token')
        
        if not google_token:
            logger.error("GoogleLoginView: No token provided")
            return Response(
                {'error': 'No token provided'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Log the token length for debugging (don't log the full token for security)
            token_length = len(google_token) if google_token else 0
            logger.info(f"GoogleLoginView: Received token of length {token_length}")
            logger.info(f"GoogleLoginView: Verifying token with client ID: {settings.GOOGLE_OAUTH2_CLIENT_ID}")
            
            if not settings.GOOGLE_OAUTH2_CLIENT_ID:
                logger.error("GoogleLoginView: GOOGLE_OAUTH2_CLIENT_ID is not set in settings")
                return Response(
                    {'error': 'Google OAuth2 client ID is not configured'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Verify token with specific audience (client_id) to prevent token substitution attacks
            idinfo = id_token.verify_oauth2_token(
                google_token, 
                requests.Request(), 
                settings.GOOGLE_OAUTH2_CLIENT_ID
            )
            logger.info("GoogleLoginView: Token verified successfully with specific audience")
            
            # Log token info (excluding sensitive parts)
            if idinfo:
                safe_info = {k: v for k, v in idinfo.items() if k not in ['sub', 'email']}
                logger.info(f"GoogleLoginView: Token info: {json.dumps(safe_info)}")
            else:
                logger.error("GoogleLoginView: Token verification returned no info")
                return Response(
                    {'error': 'Invalid token: No user information found'}, 
                    status=status.HTTP_401_UNAUTHORIZED
                )
            
            # Get user information from the token
            email = idinfo.get('email')
            if not email:
                logger.error("GoogleLoginView: Email not provided in token")
                return Response(
                    {'error': 'Email not provided in token'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Restrict login to authorized domain(s)
            allowed_domains = ['doctorsstudio.com']
            email_domain = email.split('@')[-1].lower()
            if email_domain not in allowed_domains:
                logger.warning(f"GoogleLoginView: Rejected login from unauthorized domain: {email}")
                return Response(
                    {'error': f'Access restricted to authorized organization accounts only. Domain {email_domain} is not permitted.'}, 
                    status=status.HTTP_403_FORBIDDEN
                )
                
            # Check if user exists, if not create a new user
            try:
                # First try to get the user by email
                try:
                    user = User.objects.get(email=email)
                    created = False
                    logger.info(f"GoogleLoginView: Found existing user with email {email}")
                except User.DoesNotExist:
                    # User doesn't exist, create a new one
                    user = User.objects.create(
                        email=email,
                        username=email,  # Use email as username
                        first_name=idinfo.get('given_name', ''),
                        last_name=idinfo.get('family_name', ''),
                        is_active=True
                    )
                    created = True
                    logger.info(f"GoogleLoginView: Created new user with email {email}")
                except User.MultipleObjectsReturned:
                    # Handle duplicate users - get the first one
                    users = User.objects.filter(email=email)
                    user = users.first()
                    created = False
                    logger.warning(f"GoogleLoginView: Found {users.count()} users with email {email}, using first one")
                    # You might want to log this situation for cleanup later
                
                if created:
                    user.set_password(str(uuid.uuid4()))
                    user.is_staff = True
                    user.is_superuser = False
                    user.save()
                    logger.info(f"GoogleLoginView: Granted staff permissions to {email} (superuser=False)")
                
                if not user.is_active:
                    logger.warning(f"GoogleLoginView: Rejected login — account disabled for {email}")
                    return Response(
                        {'error': 'Your account has been disabled. Please contact an administrator.'},
                        status=status.HTTP_403_FORBIDDEN
                    )
            except Exception as e:
                logger.error(f"GoogleLoginView: Error creating/getting user: {str(e)}")
                return Response(
                    {'error': 'An error occurred during authentication'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Get the OAuth2 application
            try:
                logger.info(f"GoogleLoginView: Looking for OAuth2 application with client_id={settings.OAUTH2_CLIENT_ID}")
                application = Application.objects.get(client_id=settings.OAUTH2_CLIENT_ID)
                logger.info(f"GoogleLoginView: Found OAuth2 application: {application.name}")
            except Application.DoesNotExist:
                logger.error(f"GoogleLoginView: OAuth2 application with client_id={settings.OAUTH2_CLIENT_ID} not found")
                return Response(
                    {'error': 'OAuth2 application not configured'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Create OAuth2 tokens
            expires = timezone.now() + datetime.timedelta(
                seconds=oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS
            )
            
            # Allow multiple concurrent sessions - do not delete existing tokens
            # This allows users to be logged in on multiple devices simultaneously
            logger.info(f"GoogleLoginView: Creating new tokens for user {email} (multiple sessions enabled)")
            
            # Create new tokens
            try:
                access_token = AccessToken.objects.create(
                    user=user,
                    application=application,
                    token=str(uuid.uuid4()),
                    expires=expires,
                    scope='read write'
                )
                
                refresh_token = RefreshToken.objects.create(
                    user=user,
                    application=application,
                    token=str(uuid.uuid4()),
                    access_token=access_token
                )
                
                logger.info(f"GoogleLoginView: Created new tokens for user {email}")
            except Exception as e:
                logger.error(f"GoogleLoginView: Error creating tokens: {str(e)}")
                return Response(
                    {'error': f'Token creation error: {str(e)}'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Return the tokens
            return Response({
                'access_token': access_token.token,
                'refresh_token': refresh_token.token,
                'expires_in': oauth2_settings.ACCESS_TOKEN_EXPIRE_SECONDS,
                'token_type': 'Bearer',
                'scope': access_token.scope
            })
            
        except ValueError as e:
            # Invalid token
            logger.error(f"GoogleLoginView: Invalid token: {str(e)}")
            return Response(
                {'error': f'Invalid token: {str(e)}'}, 
                status=status.HTTP_401_UNAUTHORIZED
            )
        except Exception as e:
            # Other errors
            logger.error(f"GoogleLoginView: Authentication error: {str(e)}")
            import traceback
            logger.error(f"GoogleLoginView: Traceback: {traceback.format_exc()}")
            return Response(
                {'error': f'Authentication error: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
