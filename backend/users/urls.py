from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'all', views.UserViewSet)
router.register(r'roles', views.UserRoleViewSet)
router.register(r'activity', views.UserActivityViewSet)

urlpatterns = [
    path('', include(router.urls)),
    path('me/', views.get_current_user, name='current-user'),
    path('stats/', views.get_user_stats, name='user-stats'),
]
