from django.urls import path
from .views import GoogleLoginView

app_name = 'auth_api'

urlpatterns = [
    path('google/', GoogleLoginView.as_view(), name='google_login'),
]
