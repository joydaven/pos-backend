from django.shortcuts import render, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from .models import UserProfile, UserRole, UserActivity
from .serializers import (
    UserSerializer, UserCreateSerializer, UserUpdateSerializer,
    UserRoleSerializer, UserActivitySerializer, ChangePasswordSerializer
)

class IsAdminOrSelf(permissions.BasePermission):
    """Permission to allow users to edit their own profile or admins to edit any profile"""
    
    def has_object_permission(self, request, view, obj):
        # Allow users to view/edit their own profile
        if obj == request.user:
            return True
        # Allow admins to view/edit any profile
        return request.user.is_staff

class UserRoleViewSet(viewsets.ModelViewSet):
    """API endpoint for user roles"""
    queryset = UserRole.objects.all()
    serializer_class = UserRoleSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    def perform_create(self, serializer):
        serializer.save()
        UserActivity.objects.create(
            user=self.request.user,
            action=f"Created user role: {serializer.instance.name}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
            category='user_management',
            method='POST',
            endpoint=self.request.path[:500],
            source='manual',
        )
        self.request._activity_logged = True

class UserViewSet(viewsets.ModelViewSet):
    """API endpoint for users"""
    queryset = User.objects.all().order_by('-date_joined')
    pagination_class = None
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    def get_serializer_class(self):
        if self.action == 'create':
            return UserCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return UserUpdateSerializer
        return UserSerializer
    
    def perform_create(self, serializer):
        user = serializer.save()
        UserActivity.objects.create(
            user=self.request.user,
            action=f"Created user: {user.username}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
            category='user_management',
            method='POST',
            endpoint=self.request.path[:500],
            source='manual',
        )
        self.request._activity_logged = True
        return Response(UserSerializer(user).data)
    
    def perform_update(self, serializer):
        user = serializer.save()
        UserActivity.objects.create(
            user=self.request.user,
            action=f"Updated user: {user.username}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
            category='user_management',
            method=self.request.method,
            endpoint=self.request.path[:500],
            source='manual',
        )
        self.request._activity_logged = True
        return Response(UserSerializer(user).data)
    
    def perform_destroy(self, instance):
        username = instance.username
        instance.delete()
        UserActivity.objects.create(
            user=self.request.user,
            action=f"Deleted user: {username}",
            ip_address=self.request.META.get('REMOTE_ADDR'),
            category='user_management',
            method='DELETE',
            endpoint=self.request.path[:500],
            source='manual',
        )
        self.request._activity_logged = True
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsAdminOrSelf])
    def change_password(self, request, pk=None):
        user = self.get_object()
        serializer = ChangePasswordSerializer(data=request.data)
        
        if serializer.is_valid():
            # Check old password
            if not user.check_password(serializer.validated_data['old_password']):
                return Response({"old_password": ["Wrong password."]}, status=status.HTTP_400_BAD_REQUEST)
            
            # Set new password
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            
            UserActivity.objects.create(
                user=request.user,
                action=f"Changed password for user: {user.username}",
                ip_address=request.META.get('REMOTE_ADDR'),
                category='user_management',
                method='POST',
                endpoint=request.path[:500],
                source='manual',
            )
            request._activity_logged = True
            
            return Response({"message": "Password updated successfully"}, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class UserActivityViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint for user activity logs"""
    queryset = UserActivity.objects.all().order_by('-action_time')
    serializer_class = UserActivitySerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    def get_queryset(self):
        queryset = UserActivity.objects.select_related('user').order_by('-action_time')
        
        username = self.request.query_params.get('username')
        if username:
            queryset = queryset.filter(user__username=username)
        
        action = self.request.query_params.get('action')
        if action:
            queryset = queryset.filter(action__icontains=action)
        
        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(category=category)
        
        method = self.request.query_params.get('method')
        if method:
            queryset = queryset.filter(method=method.upper())
        
        source = self.request.query_params.get('source')
        if source:
            queryset = queryset.filter(source=source)
        
        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(action_time__gte=date_from)
        
        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(action_time__lte=date_to)
        
        return queryset

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_current_user(request):
    """Get the current logged in user's details"""
    serializer = UserSerializer(request.user)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminUser])
def get_user_stats(request):
    """Get user statistics for admin dashboard"""
    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    staff_users = User.objects.filter(is_staff=True).count()
    admin_users = User.objects.filter(is_superuser=True).count()
    
    return Response({
        'total_users': total_users,
        'active_users': active_users,
        'staff_users': staff_users,
        'admin_users': admin_users
    })
