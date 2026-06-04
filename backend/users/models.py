from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

class UserRole(models.Model):
    """Role model for user permissions"""
    ROLE_CHOICES = [
        ('admin', 'Administrator'),
        ('manager', 'Manager'),
        ('staff', 'Staff'),
        ('cashier', 'Cashier'),
    ]
    
    name = models.CharField(max_length=50, choices=ROLE_CHOICES, unique=True)
    description = models.TextField(blank=True)
    # Sidebar / navigation access toggles
    can_access_pos = models.BooleanField(default=True, help_text='Access POS view')
    can_access_customers = models.BooleanField(default=True, help_text='Access Customers view')
    can_access_orders = models.BooleanField(default=False, help_text='Access Order History view')
    can_process_refunds = models.BooleanField(default=False, help_text='Access Refund Order view')
    can_access_subscriptions = models.BooleanField(default=False, help_text='Access Subscriptions view')
    can_access_membership_subscriptions = models.BooleanField(default=False, help_text='Access Membership Subscriptions view')
    can_access_payment_plans = models.BooleanField(default=False, help_text='Access Payment Plans view')
    # In-view / feature-level action toggles
    can_manage_users = models.BooleanField(default=False, help_text='Manage users and roles in Settings')
    can_manage_products = models.BooleanField(default=False, help_text='Edit products in Settings')
    can_manage_inventory = models.BooleanField(default=False, help_text='Manage inventory levels and ATUM locations')
    can_process_orders = models.BooleanField(default=True, help_text='Create/submit POS orders')
    can_view_reports = models.BooleanField(default=False, help_text='Access Reports view')
    can_manage_settings = models.BooleanField(default=False, help_text='Access Store Settings and Integrations')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.get_name_display()

class UserProfile(models.Model):
    """Extended user profile for POS system"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.ForeignKey(UserRole, on_delete=models.SET_NULL, null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=100, blank=True)
    clinic_location = models.CharField(
        max_length=50,
        choices=[
            ('Boca Raton', 'Boca Raton'),
            ('Chicago', 'Chicago'),
            ('Jupiter', 'Jupiter'),
        ],
        default='Boca Raton'
    )
    ghl_user_id = models.CharField(max_length=100, blank=True, null=True, help_text='GoHighLevel User ID for this POS user')
    is_active_employee = models.BooleanField(default=True)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.user.first_name} {self.user.last_name}" if self.user.first_name else self.user.username

# Signal to create user profile when a user is created
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        # Check if profile already exists to prevent duplicate key error
        if not hasattr(instance, 'profile') or not instance.profile:
            UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    # Check if profile exists before saving
    
    if hasattr(instance, 'profile') and instance.profile:
        instance.profile.save()

class UserActivity(models.Model):
    """Track user activity in the system"""
    CATEGORY_CHOICES = [
        ('user_management', 'User Management'),
        ('pos_order', 'POS Order'),
        ('refund', 'Refund'),
        ('payment', 'Payment'),
        ('payment_plan', 'Payment Plan'),
        ('customer', 'Customer'),
        ('product', 'Product'),
        ('settings', 'Settings'),
        ('credit_bank', 'Credit Bank'),
        ('shipping', 'Shipping'),
        ('subscription', 'Subscription'),
        ('membership', 'Membership'),
        ('inventory', 'Inventory'),
        ('auth', 'Authentication'),
        ('sync', 'Sync'),
        ('other', 'Other'),
    ]

    SOURCE_CHOICES = [
        ('manual', 'Manual'),
        ('middleware', 'Middleware'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    action = models.CharField(max_length=255)
    action_time = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    details = models.JSONField(null=True, blank=True)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='other', db_index=True)
    method = models.CharField(max_length=10, blank=True, default='')
    endpoint = models.CharField(max_length=500, blank=True, default='')
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    source = models.CharField(max_length=15, choices=SOURCE_CHOICES, default='manual')

    class Meta:
        verbose_name_plural = 'User Activities'
        ordering = ['-action_time']
        indexes = [
            models.Index(fields=['-action_time', 'category']),
            models.Index(fields=['user', '-action_time']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.action} - {self.action_time}"
