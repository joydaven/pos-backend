from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from collections import defaultdict
import uuid
import logging
from django.contrib.auth.models import User

# Create your models here.

class POSSetting(models.Model):
    """
    Global POS system settings stored in database
    This ensures all staff members see the same settings in real-time
    """
    SETTING_TYPES = (
        ('boolean', 'Boolean'),
        ('string', 'String'),
        ('integer', 'Integer'),
        ('float', 'Float'),
        ('json', 'JSON'),
    )
    
    key = models.CharField(max_length=100, unique=True, help_text="Setting key (e.g., 'prevent_out_of_stock_cart')")
    value = models.TextField(help_text="Setting value (stored as string, converted based on type)")
    setting_type = models.CharField(max_length=20, choices=SETTING_TYPES, default='string')
    description = models.TextField(blank=True, help_text="Human-readable description of this setting")
    category = models.CharField(max_length=50, default='general', help_text="Setting category (e.g., 'inventory', 'shipping')")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    
    class Meta:
        db_table = 'crm_pos_setting'
        verbose_name = 'POS Setting'
        verbose_name_plural = 'POS Settings'
    
    def __str__(self):
        return f"{self.key} = {self.value}"
    
    def get_typed_value(self):
        """Convert string value to appropriate type"""
        if self.setting_type == 'boolean':
            return self.value.lower() in ('true', '1', 'yes', 'on')
        elif self.setting_type == 'integer':
            return int(self.value)
        elif self.setting_type == 'float':
            return float(self.value)
        elif self.setting_type == 'json':
            import json
            return json.loads(self.value)
        else:
            return self.value
    
    @classmethod
    def get_setting(cls, key, default=None):
        """Get a setting value by key"""
        try:
            setting = cls.objects.get(key=key)
            return setting.get_typed_value()
        except cls.DoesNotExist:
            return default
    
    @classmethod
    def set_setting(cls, key, value, setting_type='string', description='', category='general', user=None):
        """Set a setting value"""
        # Convert value to string for storage
        if isinstance(value, bool):
            str_value = 'true' if value else 'false'
            setting_type = 'boolean'
        elif isinstance(value, (dict, list)):
            import json
            str_value = json.dumps(value)
            setting_type = 'json'
        else:
            str_value = str(value)
        
        setting, created = cls.objects.update_or_create(
            key=key,
            defaults={
                'value': str_value,
                'setting_type': setting_type,
                'description': description,
                'category': category,
                'updated_by': user
            }
        )
        return setting

class Contact(models.Model):
    PRIMARY_SOURCE_CHOICES = (
        ('woo', 'WooCommerce'),
        ('ghl', 'GoHighLevel'),
        ('crm', 'CRM'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # WooCommerce fields
    woo_customer_id = models.IntegerField(unique=True, null=True, blank=True)
    woo_last_sync = models.DateTimeField(null=True, blank=True)
    woo_data = models.JSONField(default=dict, blank=True)
    
    # GoHighLevel fields
    ghl_contact_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    ghl_last_sync = models.DateTimeField(null=True, blank=True)
    ghl_tags = models.JSONField(default=list, blank=True)
    ghl_custom_fields = models.JSONField(default=dict, blank=True)
    ghl_data = models.JSONField(default=dict, blank=True)
    
    # Core contact fields
    primary_source = models.CharField(max_length=10, choices=PRIMARY_SOURCE_CHOICES, default='crm')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50, blank=True)
    normalized_phone = models.CharField(max_length=50, blank=True, help_text="Standardized phone number format")
    billing_address = models.TextField(blank=True)
    billing_address_2 = models.CharField(max_length=255, blank=True, help_text="Billing address line 2 (apt, suite, unit, etc.)")
    billing_city = models.CharField(max_length=100, blank=True)
    billing_state = models.CharField(max_length=100, blank=True)
    billing_postcode = models.CharField(max_length=20, blank=True)
    billing_country = models.CharField(max_length=100, blank=True, default='USA')
    
    # Shipping address fields
    shipping_address = models.TextField(blank=True)
    shipping_address_2 = models.CharField(max_length=255, blank=True, help_text="Shipping address line 2 (apt, suite, unit, etc.)")
    shipping_city = models.CharField(max_length=100, blank=True)
    shipping_state = models.CharField(max_length=100, blank=True)
    shipping_postcode = models.CharField(max_length=20, blank=True)
    shipping_country = models.CharField(max_length=100, blank=True, default='USA')
    shipping_same_as_billing = models.BooleanField(default=True, help_text="Whether shipping address is the same as billing")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Important notes tracking for customer notifications
    last_important_note_acknowledged_at = models.DateTimeField(
        null=True, 
        blank=True, 
        help_text="Timestamp when user last acknowledged important notes for this customer"
    )
    
    # Membership status (maintained by WC webhooks, GHL sync, and membership API)
    is_active_member = models.BooleanField(
        default=False,
        help_text="Whether this customer has an active WooCommerce membership"
    )
    
    # Authorize.Net payment gateway integration
    authorize_net_customer_profile_id = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Authorize.Net customer profile ID - persists even when all cards are deleted"
    )

    class Meta:
        indexes = [
            models.Index(fields=['last_name', 'first_name']),
            models.Index(fields=['first_name']),
            models.Index(fields=['phone']),
            models.Index(fields=['updated_at']),
            models.Index(fields=['authorize_net_customer_profile_id']),
            models.Index(fields=['normalized_phone']),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
        
    @property
    def has_woo(self):
        return self.woo_customer_id is not None
        
    @property
    def has_ghl(self):
        return self.ghl_contact_id is not None
        
    @property
    def is_multi_source(self):
        sources = 0
        if self.has_woo:
            sources += 1
        if self.has_ghl:
            sources += 1
        return sources > 1

class Order(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name='orders')
    woo_order_id = models.CharField(max_length=100)
    order_date = models.DateTimeField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['woo_order_id']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Order {self.woo_order_id} - {self.contact}"

class Product(models.Model):
    PRODUCT_TYPE_CHOICES = (
        ('simple', 'Simple'),
        ('variable', 'Variable'),
        ('subscription', 'Subscription'),
        ('variable_subscription', 'Variable Subscription'),
        ('bundle', 'Bundle'),
        ('grouped', 'Grouped'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    woo_product_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    regular_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Cost/purchase price from WooCommerce (ATUM _purchase_price)')
    status = models.CharField(max_length=20)
    stock_status = models.CharField(max_length=20)
    stock_quantity = models.IntegerField(null=True, blank=True)
    product_type = models.CharField(max_length=30, choices=PRODUCT_TYPE_CHOICES, default='simple')
    short_description = models.TextField(blank=True, default='')
    tax_status = models.CharField(max_length=20, default='taxable', help_text='taxable, shipping, none')
    tax_class = models.CharField(max_length=50, blank=True, default='', help_text='standard, reduced-rate, zero-rate')
    manage_stock = models.BooleanField(null=True, blank=True, help_text='Whether stock management is enabled')
    backorders = models.CharField(max_length=10, default='no', help_text='no, notify, yes')
    sold_individually = models.BooleanField(default=False)
    purchase_note = models.TextField(blank=True, default='')
    menu_order = models.IntegerField(default=0)
    reviews_allowed = models.BooleanField(default=True)
    upsell_ids = models.JSONField(default=list, blank=True)
    cross_sell_ids = models.JSONField(default=list, blank=True)
    low_stock_amount = models.IntegerField(null=True, blank=True)
    date_on_sale_from = models.DateTimeField(null=True, blank=True)
    date_on_sale_to = models.DateTimeField(null=True, blank=True)
    shipping_class = models.CharField(max_length=100, blank=True, default='')
    tags = models.JSONField(default=list, blank=True)
    categories = models.JSONField(default=list)
    images = models.JSONField(default=list)
    woo_data = models.JSONField(default=dict, blank=True, help_text='Full WooCommerce product JSON for fields not in dedicated columns')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['product_type']),
            models.Index(fields=['status']),
            models.Index(fields=['stock_status']),
            models.Index(fields=['updated_at']),
        ]

class ProductVariation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variations')
    woo_variation_id = models.IntegerField()
    sku = models.CharField(max_length=100, blank=True, null=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    regular_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Cost/purchase price from ATUM wp_atum_product_data for this variation')
    stock_quantity = models.IntegerField(null=True, blank=True)
    menu_order = models.IntegerField(default=0, help_text="Display order from WooCommerce")
    attributes = models.JSONField(default=list)
    woo_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Product Variation'
        verbose_name_plural = 'Product Variations'
        ordering = ['product__name', 'menu_order']
        unique_together = ('product', 'woo_variation_id')
    
    def __str__(self):
        return f"Variation of {self.product.name} (ID: {self.woo_variation_id})"

class ProductSubscription(models.Model):
    PERIOD_CHOICES = (
        ('day', 'Day'),
        ('week', 'Week'),
        ('month', 'Month'),
        ('year', 'Year'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='subscriptions')
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    period = models.CharField(max_length=10, choices=PERIOD_CHOICES, default='month')
    interval = models.IntegerField(default=1)
    trial_period = models.CharField(max_length=10, choices=PERIOD_CHOICES, null=True, blank=True)
    trial_length = models.IntegerField(null=True, blank=True)
    sign_up_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    woo_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Product Subscription'
        verbose_name_plural = 'Product Subscriptions'
        ordering = ['product__name']
        unique_together = ('product',)
    
    def __str__(self):
        return f"{self.product.name} - {self.interval} {self.period}(s)"

class ProductBundle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='bundles')
    bundled_products = models.JSONField(default=list)  # List of product IDs in the bundle
    min_quantity = models.IntegerField(default=1)
    max_quantity = models.IntegerField(null=True, blank=True)
    woo_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Product Bundle'
        verbose_name_plural = 'Product Bundles'
        ordering = ['product__name']
        unique_together = ('product',)
    
    def __str__(self):
        return f"Bundle: {self.product.name}"

class ProductGrouped(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='grouped_items')
    grouped_products = models.JSONField(default=list)  # List of product IDs in the group
    woo_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Product Grouped'
        verbose_name_plural = 'Product Grouped'
        ordering = ['product__name']
        unique_together = ('product',)
    
    def __str__(self):
        return f"Grouped: {self.product.name}"

class ProductSimple(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='simple_details')
    sku = models.CharField(max_length=100, blank=True, null=True)
    weight = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Weight in pounds")
    length = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Length in inches")
    width = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Width in inches")
    height = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Height in inches")
    is_virtual = models.BooleanField(default=False, help_text="Whether this is a virtual product")
    is_downloadable = models.BooleanField(default=False, help_text="Whether this is a downloadable product")
    woo_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Product Simple'
        verbose_name_plural = 'Product Simple'
        ordering = ['product__name']
        unique_together = ('product',)
    
    def __str__(self):
        return f"Simple: {self.product.name}"

class OAuth2Token(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location_id = models.CharField(max_length=100, unique=True)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"OAuth Token for {self.location_id}"
    
    @property
    def is_expired(self):
        from django.utils import timezone
        return self.expires_at <= timezone.now()

class TokenRequestLog(models.Model):
    REQUEST_TYPES = (
        ('auth', 'Authorization'),
        ('refresh', 'Refresh Token'),
    )
    
    STATUS_CHOICES = (
        ('success', 'Success'),
        ('error', 'Error'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    token = models.ForeignKey(OAuth2Token, on_delete=models.CASCADE, related_name='request_logs', null=True, blank=True)
    request_type = models.CharField(max_length=20, choices=REQUEST_TYPES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True)
    request_data = models.JSONField(default=dict)
    response_data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.request_type} - {self.status} - {self.created_at}"

class InventoryLocation(models.Model):
    """
    Model to store inventory locations such as Clinic Inventory or Warehouse
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    address = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # ATUM Inventory fields
    atum_location_id = models.IntegerField(null=True, blank=True, help_text="ATUM location ID from WooCommerce")
    slug = models.CharField(max_length=255, blank=True, help_text="URL-friendly location name")
    parent_location_id = models.IntegerField(null=True, blank=True, help_text="Parent ATUM location ID")
    barcode = models.CharField(max_length=255, blank=True, help_text="Location barcode")
    product_count = models.IntegerField(default=0, help_text="Number of products in this location")
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name

class ProductInventory(models.Model):
    """
    Joining table between Product and InventoryLocation to track inventory levels at different locations
    Supports variation-specific inventory for variable products
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='inventory_locations')
    variation = models.ForeignKey('ProductVariation', on_delete=models.CASCADE, related_name='inventory_locations', null=True, blank=True)
    location = models.ForeignKey(InventoryLocation, on_delete=models.CASCADE, related_name='product_inventory')
    quantity = models.IntegerField(default=0, null=True, blank=True, help_text="Stock quantity. NULL indicates unmanaged stock (ATUM).")
    reorder_level = models.IntegerField(default=0)
    reorder_quantity = models.IntegerField(default=0)
    is_available = models.BooleanField(default=True)
    last_updated = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name_plural = "Product Inventories"
        unique_together = ('product', 'variation', 'location')
        
    def __str__(self):
        if self.variation:
            return f"{self.product.name} (Variation: {self.variation.woo_variation_id}) at {self.location.name}"
        return f"{self.product.name} at {self.location.name}"

class POSOrderManager(models.Manager):
    """Custom manager for POSOrder to handle active vs trashed items"""
    
    def active(self):
        """Get all active (non-trashed) orders"""
        return self.exclude(status='trash')
    
    def trashed(self):
        """Get all trashed orders"""
        return self.filter(status='trash')
    
    def with_trash(self):
        """Get all orders including trashed ones (default behavior)"""
        return self.all()

class POSOrder(models.Model):
    """
    Model to store orders from the POS system
    """
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('pending-payment', 'Pending Payment'),
        ('processing', 'Processing'),
        ('on-hold', 'On Hold'),
        ('completed', 'Completed'),
        ('shipped', 'Shipped'),
        ('partially-shipped', 'Partially Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
        ('failed', 'Failed'),
        ('draft', 'Draft'),
        ('trash', 'Trash'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=100, unique=True)
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, related_name='pos_orders', null=True, blank=True)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='processing')
    payment_method = models.JSONField(default=dict, blank=True)
    payment_method_title = models.CharField(max_length=100, blank=True, help_text="Display name for the payment method")
    transaction_id = models.CharField(max_length=100, blank=True, help_text="Payment transaction ID from payment processor")
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    # Location information for reporting
    pos_location_id = models.CharField(max_length=100, blank=True, null=True, help_text="Selected POS location ID (matches location_id from localStorage)")
    pos_location_name = models.CharField(max_length=255, blank=True, null=True, help_text="Selected POS location display name")
    assigned_location = models.CharField(max_length=255, blank=True, null=True, help_text="Assigned location for filtering (e.g., Jupiter, Boca Raton, Chicago)")
    
    # Bundle products information for receipt display
    bundle_products = models.JSONField(default=list, blank=True, null=True, help_text="Original bundle products for receipt display (preserves bundle structure)")
    
    # Trash/soft delete fields
    trashed_at = models.DateTimeField(null=True, blank=True, help_text="When the order was moved to trash")
    trashed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='trashed_orders', help_text="User who trashed this order")
    
    # Custom manager
    objects = POSOrderManager()
    
    # Shipping information
    shipping_info = models.JSONField(default=dict, blank=True, help_text="Shipping carrier and service information")
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Cost of shipping")
    shipping_address = models.TextField(blank=True, help_text="Shipping address")
    shipping_city = models.CharField(max_length=100, blank=True, help_text="Shipping city")
    shipping_state = models.CharField(max_length=100, blank=True, help_text="Shipping state/province")
    shipping_postcode = models.CharField(max_length=20, blank=True, help_text="Shipping postal code")
    shipping_country = models.CharField(max_length=100, blank=True, help_text="Shipping country")
    tracking_number = models.CharField(max_length=100, blank=True, help_text="Shipping tracking number")
    shipping_status = models.CharField(max_length=50, blank=True, help_text="Status of the shipment")
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'POS Order'
        verbose_name_plural = 'POS Orders'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['transaction_id']),
            models.Index(fields=['pos_location_id']),
            models.Index(fields=['assigned_location']),
            models.Index(fields=['created_at']),
            models.Index(fields=['trashed_at']),
        ]
        
    def __str__(self):
        return f"Order {self.order_number}"
    
    def trash(self, user=None):
        """Move order to trash"""
        from django.utils import timezone
        self.status = 'trash'
        self.trashed_at = timezone.now()
        if user:
            self.trashed_by = user
        self.save()
    
    def restore(self, new_status='processing'):
        """Restore order from trash"""
        self.status = new_status
        self.trashed_at = None
        self.trashed_by = None
        self.save()
    
    def is_trashed(self):
        """Check if order is trashed"""
        return self.status == 'trash'
    
    def can_be_trashed(self):
        """Check if order can be moved to trash"""
        # Orders can be trashed unless they're already trashed
        return self.status != 'trash'

class POSOrderItem(models.Model):
    """
    Model to store order items for POS orders
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name='items')
    product_id = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    code = models.CharField(max_length=100, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    stock_quantity = models.IntegerField(default=0)
    # Add dimension and weight fields for shipping calculations
    weight = models.DecimalField(max_digits=8, decimal_places=2, default=0, help_text="Weight in pounds")
    length = models.DecimalField(max_digits=8, decimal_places=2, default=0, help_text="Length in inches")
    width = models.DecimalField(max_digits=8, decimal_places=2, default=0, help_text="Width in inches")
    height = models.DecimalField(max_digits=8, decimal_places=2, default=0, help_text="Height in inches")
    is_digital = models.BooleanField(default=False, help_text="Whether this is a digital product that doesn't require shipping")
    categories = models.JSONField(default=list, blank=True)
    images = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    # Discount-related fields for subscription and manual discounts
    original_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Original price before any discounts"
    )
    discount_amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=0,
        help_text="Discount amount applied to this item"
    )
    discount_type = models.CharField(
        max_length=10,
        choices=[('percentage', 'Percentage'), ('dollar', 'Dollar')],
        default='dollar',
        help_text="Type of discount applied"
    )
    discount_source = models.CharField(
        max_length=50,
        blank=True,
        help_text="Source of discount (e.g., 'subscription', 'manual', 'coupon')"
    )
    discount_reason = models.TextField(
        blank=True,
        help_text="Optional reason/note for the discount (e.g., 'Customer loyalty', 'Price match', etc.)"
    )
    
    # Fulfillment location for this item
    fulfillment_location = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Fulfillment location for this item (e.g., 'Dropship', 'Boca Clinic', etc.)"
    )
    
    # Shipping flag for clinic locations
    needs_shipping = models.BooleanField(
        default=False,
        help_text="Whether this item needs shipping (for clinic locations like Boca/Jupiter)"
    )
    
    class Meta:
        ordering = ['name']
        verbose_name = 'POS Order Item'
        verbose_name_plural = 'POS Order Items'
    
    def __str__(self):
        return f"{self.name} ({self.quantity})"

class PaymentCard(models.Model):
    """
    Model to securely store encrypted payment card information
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(POSOrder, on_delete=models.CASCADE, related_name='card_details')
    encrypted_card_number = models.BinaryField()
    encrypted_expiry_date = models.BinaryField()
    last4 = models.CharField(max_length=4)
    card_brand = models.CharField(max_length=50, blank=True)
    cardholder_name = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = 'Payment Card'
        verbose_name_plural = 'Payment Cards'
        
    def __str__(self):
        return f"Card ending in {self.last4} for Order {self.order.order_number}"

class POSLocation(models.Model):
    """
    Model to store POS location information for multi-location support
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, help_text="Display name of the location")
    location = models.CharField(max_length=255, help_text="Physical address or description of the location")
    location_id = models.CharField(max_length=100, unique=True, help_text="Unique identifier for the location (matches GHL location ID)")
    assigned_location = models.CharField(max_length=255, blank=True, null=True, help_text="Assigned location for filtering (e.g., Boca Raton, Jupiter, Chicago)")
    is_active = models.BooleanField(default=True, help_text="Whether this location is active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = 'POS Location'
        verbose_name_plural = 'POS Locations'
        
    def __str__(self):
        return f"{self.name} ({self.location_id})"


class Brand(models.Model):
    """
    Model to store unique product brands for efficient filtering.
    
    This table is populated by extracting brand information from product
    woo_data.attributes and is kept in sync with product changes.
    """
    
    name = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique brand name extracted from product attributes"
    )
    
    slug = models.SlugField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="URL-friendly version of the brand name"
    )
    
    product_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of products associated with this brand"
    )
    
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Whether this brand is currently active (has products)"
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="When this brand was first discovered"
    )
    
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="When this brand was last updated"
    )
    
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this brand was last synced from products"
    )

    class Meta:
        db_table = 'crm_brand'
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['is_active', 'name']),
            models.Index(fields=['product_count']),
        ]

    def __str__(self):
        return f"{self.name} ({self.product_count} products)"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @classmethod
    def sync_from_products(cls):
        """
        Sync brands from all product tables.
        
        This method extracts brands from ProductSimple, ProductVariation,
        and ProductBundle woo_data and updates the Brand table accordingly.
        
        Returns:
            dict: Statistics about the sync operation
        """
        logger = logging.getLogger(__name__)
        
        try:
            # Track brand counts across all product types
            brand_counts = defaultdict(int)
            
            # Process ProductSimple
            simple_products = ProductSimple.objects.exclude(woo_data__isnull=True)
            for product in simple_products:
                brands = cls._extract_brands_from_woo_data(product.woo_data)
                for brand in brands:
                    brand_counts[brand] += 1
            
            # Process ProductVariation
            variation_products = ProductVariation.objects.exclude(woo_data__isnull=True)
            for product in variation_products:
                brands = cls._extract_brands_from_woo_data(product.woo_data)
                for brand in brands:
                    brand_counts[brand] += 1
            
            # Process ProductBundle
            bundle_products = ProductBundle.objects.exclude(woo_data__isnull=True)
            for product in bundle_products:
                brands = cls._extract_brands_from_woo_data(product.woo_data)
                for brand in brands:
                    brand_counts[brand] += 1
            
            # Update Brand table
            sync_time = timezone.now()
            brands_created = 0
            brands_updated = 0
            brands_deactivated = 0
            
            # Create or update brands
            for brand_name, count in brand_counts.items():
                if not brand_name or not brand_name.strip():
                    continue
                    
                brand_name = brand_name.strip()
                slug = slugify(brand_name)
                
                brand, created = cls.objects.get_or_create(
                    name=brand_name,
                    defaults={
                        'slug': slug,
                        'product_count': count,
                        'is_active': True,
                        'last_synced_at': sync_time
                    }
                )
                
                if created:
                    brands_created += 1
                    logger.info(f"Created new brand: {brand_name}")
                else:
                    # Update existing brand
                    brand.product_count = count
                    brand.is_active = True
                    brand.last_synced_at = sync_time
                    brand.save()
                    brands_updated += 1
            
            # Deactivate brands that no longer exist in products
            existing_brand_names = set(brand_counts.keys())
            for brand in cls.objects.filter(is_active=True):
                if brand.name not in existing_brand_names:
                    brand.is_active = False
                    brand.product_count = 0
                    brand.last_synced_at = sync_time
                    brand.save()
                    brands_deactivated += 1
                    logger.info(f"Deactivated brand: {brand.name}")
            
            stats = {
                'total_brands_found': len(brand_counts),
                'brands_created': brands_created,
                'brands_updated': brands_updated,
                'brands_deactivated': brands_deactivated,
                'sync_completed_at': sync_time
            }
            
            logger.info(f"Brand sync completed: {stats}")
            return stats
            
        except Exception as e:
            logger.error(f"Error during brand sync: {str(e)}")
            raise
    
    @staticmethod
    def _extract_brands_from_woo_data(woo_data):
        """
        Extract brand names from woo_data attributes.
        
        Args:
            woo_data (dict): Product woo_data containing attributes
            
        Returns:
            list: List of brand names found in the attributes
        """
        brands = []
        
        if not woo_data or 'attributes' not in woo_data:
            return brands
            
        for attr in woo_data['attributes']:
            if (attr.get('name') == 'Brand' or attr.get('slug') == 'pa_brand') and 'options' in attr:
                brands.extend(attr['options'])
        
        return [brand.strip() for brand in brands if brand and brand.strip()]

    @classmethod
    def get_active_brands(cls):
        """
        Get all active brands ordered by name.
        
        Returns:
            QuerySet: Active brands ordered by name
        """
        return cls.objects.filter(is_active=True).order_by('name')

    @classmethod
    def get_popular_brands(cls, limit=10):
        """
        Get most popular brands by product count.
        
        Args:
            limit (int): Maximum number of brands to return
            
        Returns:
            QuerySet: Popular brands ordered by product count
        """
        return cls.objects.filter(is_active=True).order_by('-product_count')[:limit]

    @classmethod
    def get_all_available_brands(cls, include_woocommerce=True):
        """
        Get all available brands from both local database and WooCommerce.
        
        Args:
            include_woocommerce (bool): Whether to include brands from WooCommerce API
            
        Returns:
            list: List of brand names sorted alphabetically
        """
        # Get local brands
        local_brands = set(cls.objects.filter(is_active=True).values_list('name', flat=True))
        
        if not include_woocommerce:
            return sorted(local_brands)
        
        # Get WooCommerce brands
        try:
            from .woocommerce import WooCommerceAPI
            wc_api = WooCommerceAPI()
            wc_brands = set(wc_api.get_all_brands_from_woocommerce())
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to fetch WooCommerce brands: {str(e)}")
            wc_brands = set()
        
        # Combine and return sorted list
        all_brands = local_brands.union(wc_brands)
        return sorted(all_brands)


class POSOrderRefund(models.Model):
    """
    Model to store refunds for POS orders
    """
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
        ('refunded', 'Refunded'),
    )
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name='refunds')
    refund_number = models.CharField(max_length=100, unique=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2)
    refund_reason = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    payment_method = models.JSONField(default=dict, blank=True)
    payment_method_title = models.CharField(max_length=100, blank=True, help_text="Display name for the refund payment method")
    transaction_id = models.CharField(max_length=100, blank=True, help_text="Refund transaction ID from payment processor")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.CharField(max_length=255, blank=True, help_text="User who created the refund")
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    pos_location_id = models.CharField(max_length=100, blank=True, null=True, help_text="Selected POS location ID where refund was processed")
    pos_location_name = models.CharField(max_length=255, blank=True, null=True, help_text="Selected POS location display name")
    assigned_location = models.CharField(max_length=255, blank=True, null=True, help_text="Assigned location for filtering")
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'POS Order Refund'
        verbose_name_plural = 'POS Order Refunds'
        
    def __str__(self):
        return f"Refund {self.refund_number} for Order {self.order.order_number}"


class POSOrderRefundItem(models.Model):
    """
    Model to store refund items for POS order refunds
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    refund = models.ForeignKey(POSOrderRefund, on_delete=models.CASCADE, related_name='items')
    order_item = models.ForeignKey(POSOrderItem, on_delete=models.SET_NULL, related_name='refund_items', null=True, blank=True)
    customer = models.ForeignKey(Contact, on_delete=models.SET_NULL, related_name='refund_items', null=True, blank=True, help_text="Customer associated with this refund item")
    customer_name = models.CharField(max_length=255, blank=True, null=True, help_text="Customer name for quick reference without joins")
    customer_email = models.EmailField(blank=True, null=True, help_text="Customer email for quick reference without joins")
    product_id = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    refund_reason = models.TextField(blank=True, help_text="Reason for the refund")
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['name']
        verbose_name = 'POS Order Refund Item'
        verbose_name_plural = 'POS Order Refund Items'
        
    def __str__(self):
        return f"{self.name} ({self.quantity}) - {self.refund.refund_number}"


class WooCreditedService(models.Model):
    """
    Model to store credited services for customers.
    Tracks product credits available to customers, allowing for add/subtract operations.
    """
    id = models.BigAutoField(primary_key=True)
    contact_ghl_id = models.CharField(max_length=255, null=True, blank=True)
    contact_woo_id = models.CharField(max_length=255, null=True, blank=True)
    contact_fname = models.CharField(max_length=255, null=True, blank=True)
    contact_lname = models.CharField(max_length=255, null=True, blank=True)
    contact_phone = models.CharField(max_length=255, null=True, blank=True)
    contact_email = models.CharField(max_length=255, null=True, blank=True)
    product_id = models.CharField(max_length=255)
    product_name = models.CharField(max_length=255)
    product_points = models.IntegerField(default=0, help_text="Available credits for this product")
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    
    class Meta:
        verbose_name = 'Credited Service'
        verbose_name_plural = 'Credited Services'
        unique_together = ('contact_woo_id', 'product_id')
        indexes = [
            models.Index(fields=['contact_woo_id']),
            models.Index(fields=['product_id']),
            models.Index(fields=['contact_email']),
        ]
    
    def __str__(self):
        return f"{self.contact_fname} {self.contact_lname} - {self.product_name} ({self.product_points} credits)"


class CreditServicePoints(models.Model):
    """
    New model for credited services product points system.
    Only applies to variable products with series.
    Uses proper UUID foreign key relationships.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(
        Contact, 
        on_delete=models.CASCADE, 
        related_name='credit_points',
        help_text="FK → crm_contact"
    )
    product = models.ForeignKey(
        Product, 
        on_delete=models.CASCADE, 
        related_name='credit_points',
        help_text="FK → crm_products"
    )
    contact_ghl_id = models.CharField(
        max_length=100, 
        blank=True, 
        null=True, 
        help_text="From CRM contact"
    )
    contact_woo_id = models.IntegerField(
        blank=True, 
        null=True, 
        help_text="WooCommerce contact ID"
    )
    points = models.IntegerField(
        default=0, 
        help_text="Total points for that product"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Credit Service Points'
        verbose_name_plural = 'Credit Service Points'
        db_table = 'credit_service_points'
        unique_together = ['customer', 'product']
        indexes = [
            models.Index(fields=['customer'], name='csp_customer_idx'),
            models.Index(fields=['product'], name='csp_product_idx'),
            models.Index(fields=['contact_ghl_id'], name='csp_ghl_id_idx'),
            models.Index(fields=['contact_woo_id'], name='csp_woo_id_idx'),
        ]
    
    def __str__(self):
        return f"{self.customer.first_name} {self.customer.last_name} - {self.product.name} ({self.points} points)"
    
    def log_credit_change(self, action, points_change, order_number=None, reason="", notes="", source="POS"):
        """
        Log credit point changes to woo_credit_logs table and sync to GoHighLevel.
        
        Args:
            action (str): Type of action ("add", "redeem", "manual_add", "manual_subtract")
            points_change (int): Points changed (positive for additions, negative for deductions)
            order_number (str): Order number or "MANUAL" for manual adjustments
            reason (str): Business reason for the change
            notes (str): Additional context
            source (str): Origin of the change - "POS", "MANUAL", or "WEB"
        """
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            # Use woo_customer_id if available, otherwise use customer UUID
            # This ensures logs are created even for customers without WooCommerce IDs
            contact_woo_id = str(self.customer.woo_customer_id) if self.customer.woo_customer_id else str(self.customer.id)
            
            # Get or create WooCreditedService record for this customer and product
            # This is the legacy table that woo_credit_logs references
            woo_credited_service, created = WooCreditedService.objects.get_or_create(
                contact_woo_id=contact_woo_id,
                product_id=str(self.product.woo_product_id) if self.product.woo_product_id else str(self.product.id),
                defaults={
                    'contact_ghl_id': self.customer.ghl_contact_id or '',
                    'contact_fname': self.customer.first_name or '',
                    'contact_lname': self.customer.last_name or '',
                    'contact_phone': self.customer.phone or '',
                    'contact_email': self.customer.email,
                    'product_name': self.product.name,
                    'product_points': self.points
                }
            )
            
            # Update points if record already exists to keep both tables in sync
            if not created:
                woo_credited_service.product_points = self.points
                woo_credited_service.save()
            
            # Create log entry with correct foreign key
            WooCreditLog.objects.create(
                woo_credited_service_id=woo_credited_service.id,  # Correct FK to WooCreditedService
                action=action,
                order_number=order_number or "UNKNOWN",
                points_change=points_change,
                updated_points=self.points,
                reason=reason,
                notes=notes,  # Store clean notes without "Customer:" prefix
                source=source
            )
            
            logger.info(f"Credit log created: {action} {points_change} points for {self.customer.email} - {self.product.name}")
            
            # Sync to GoHighLevel if customer has GHL contact ID
            self.sync_to_ghl()
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to create credit log: {str(e)}")
            # Don't raise exception to avoid breaking the main flow
    
    def sync_to_ghl(self):
        """
        Sync current credit points to GoHighLevel custom field.
        """
        try:
            import logging
            logger = logging.getLogger(__name__)
            
            # Check if customer has GHL contact ID
            if not self.customer.ghl_contact_id:
                logger.info(f"Customer {self.customer.email} has no GHL contact ID, skipping GHL sync")
                return False
            
            # Import GHL API function
            from .ghl_api import sync_credit_points_to_ghl
            
            # Sync current points to GHL
            success = sync_credit_points_to_ghl(
                contact_id=self.customer.ghl_contact_id,
                service_name=self.product.name,
                points=self.points
            )
            
            if success:
                logger.info(f"Successfully synced {self.points} points for {self.product.name} to GHL contact {self.customer.ghl_contact_id}")
            else:
                logger.warning(f"Failed to sync points to GHL for {self.customer.email} - {self.product.name}")
            
            return success
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error syncing to GHL: {str(e)}")
            return False
    
    @classmethod
    def is_variable_product_with_series(cls, product):
        """
        Check if a product is a variable product with series.
        
        Args:
            product: Product instance or product data dict
            
        Returns:
            bool: True if product is variable and has series
        """
        if hasattr(product, 'product_type'):
            product_type = product.product_type
        elif isinstance(product, dict):
            product_type = product.get('product_type', product.get('type', ''))
        else:
            return False
            
        # Check if it's a variable product
        if product_type != 'variable':
            return False
            
        # Check if it has series in variations or attributes
        # This would need to be customized based on how series are stored
        # For now, assume all variable products have series
        return True
    
    @classmethod
    def is_product_series_eligible_for_credits(cls, product_name, series_value):
        """
        Check if a specific product name and series combination is eligible for credit services.
        
        Args:
            product_name (str): Name of the product to check
            series_value (int): Series value selected (e.g., 3, 6, 12)
            
        Returns:
            bool: True if the product name exists in woo_service_types and the series is in the allowed list
        """
        try:
            import json
            import re

            logger = logging.getLogger(__name__)

            # region agent log
            try:
                from pathlib import Path
                debug_payload = {
                    "sessionId": "4b57f8",
                    "runId": "credit-eligibility",
                    "hypothesisId": "H1",
                    "location": "crm/models.py:is_product_series_eligible_for_credits:start",
                    "message": "Eligibility check started",
                    "data": {
                        "productName": str(product_name or "")[:120],
                        "seriesValueRaw": series_value,
                    },
                    "timestamp": int(timezone.now().timestamp() * 1000),
                }
                with Path(r"c:\laragon\www\NEWPOS\woo-ghl-contact-db\debug-4b57f8.log").open("a", encoding="utf-8") as _dbg_f:
                    _dbg_f.write(json.dumps(debug_payload, ensure_ascii=True) + "\n")
            except Exception:
                pass
            # endregion

            def _normalize_service_name(value):
                cleaned = ' '.join(str(value or '').split()).lower()
                cleaned = cleaned.replace('&', ' and ')
                cleaned = re.sub(r'[^a-z0-9]+', ' ', cleaned)
                return ' '.join(cleaned.split())

            def _to_int(value):
                if value is None:
                    return None
                try:
                    return int(str(value).strip())
                except (TypeError, ValueError):
                    return None

            # Product labels can include variation suffixes in POS; keep base candidate too.
            raw_product_name = ' '.join(str(product_name or '').split())
            if not raw_product_name:
                return False

            series_target = _to_int(series_value)
            if series_target is None:
                logger.warning(f"Invalid series value for credit eligibility: {series_value}")
                return False

            base_product_name = raw_product_name.split(' - ')[0].strip()
            product_name_candidates = []
            for candidate in [raw_product_name, base_product_name]:
                if candidate and candidate not in product_name_candidates:
                    product_name_candidates.append(candidate)

            normalized_candidates = {_normalize_service_name(name) for name in product_name_candidates}

            # Fast DB-level lookup first, then robust normalized fallback.
            service_type = None
            for candidate in product_name_candidates:
                service_type = WooServiceTypes.objects.filter(name__iexact=candidate).first()
                if service_type:
                    break

            if not service_type:
                for candidate in product_name_candidates:
                    service_type = WooServiceTypes.objects.filter(name__icontains=candidate).first()
                    if service_type:
                        break

            if not service_type:
                for st in WooServiceTypes.objects.all():
                    normalized_db_name = _normalize_service_name(st.name)
                    if any(
                        normalized_candidate == normalized_db_name
                        or normalized_candidate in normalized_db_name
                        or normalized_db_name in normalized_candidate
                        for normalized_candidate in normalized_candidates
                    ):
                        service_type = st
                        break

            if not service_type:
                logger.info(
                    f'Credit eligibility miss: no service type match for "{raw_product_name}" '
                    f"(series={series_target})"
                )
                # region agent log
                try:
                    from pathlib import Path
                    debug_payload = {
                        "sessionId": "4b57f8",
                        "runId": "credit-eligibility",
                        "hypothesisId": "H2",
                        "location": "crm/models.py:is_product_series_eligible_for_credits:no_service_type",
                        "message": "No matching service type",
                        "data": {
                            "rawProductName": raw_product_name[:120],
                            "seriesTarget": series_target,
                        },
                        "timestamp": int(timezone.now().timestamp() * 1000),
                    }
                    with Path(r"c:\laragon\www\NEWPOS\woo-ghl-contact-db\debug-4b57f8.log").open("a", encoding="utf-8") as _dbg_f:
                        _dbg_f.write(json.dumps(debug_payload, ensure_ascii=True) + "\n")
                except Exception:
                    pass
                # endregion
                return False

            raw_series = service_type.series
            if isinstance(raw_series, str):
                try:
                    raw_series = json.loads(raw_series)
                except json.JSONDecodeError:
                    raw_series = [raw_series]
            elif raw_series is None:
                raw_series = []
            elif not isinstance(raw_series, list):
                raw_series = [raw_series]

            normalized_series = {_to_int(value) for value in raw_series}
            normalized_series.discard(None)

            is_eligible = series_target in normalized_series
            # region agent log
            try:
                from pathlib import Path
                debug_payload = {
                    "sessionId": "4b57f8",
                    "runId": "credit-eligibility",
                    "hypothesisId": "H3",
                    "location": "crm/models.py:is_product_series_eligible_for_credits:final",
                    "message": "Eligibility check completed",
                    "data": {
                        "rawProductName": raw_product_name[:120],
                        "matchedServiceName": str(service_type.name or "")[:120],
                        "seriesTarget": series_target,
                        "normalizedSeries": sorted(list(normalized_series)),
                        "isEligible": is_eligible,
                    },
                    "timestamp": int(timezone.now().timestamp() * 1000),
                }
                with Path(r"c:\laragon\www\NEWPOS\woo-ghl-contact-db\debug-4b57f8.log").open("a", encoding="utf-8") as _dbg_f:
                    _dbg_f.write(json.dumps(debug_payload, ensure_ascii=True) + "\n")
            except Exception:
                pass
            # endregion
            if not is_eligible:
                logger.info(
                    f'Credit eligibility miss: product="{raw_product_name}" matched_service="{service_type.name}" '
                    f"series={series_target} allowed={sorted(normalized_series)}"
                )
            return is_eligible

        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error checking series eligibility: {str(e)}")
            return False
    
    @classmethod
    def check_variation_eligibility_for_credits(cls, product, variation_data):
        """
        Check if a specific product variation is eligible for credit services.
        
        Args:
            product: Product instance
            variation_data: Dict containing variation information with series attributes
            
        Returns:
            dict: {
                'eligible': bool,
                'series_value': int or None,
                'service_name': str or None,
                'reason': str
            }
        """
        try:
            # First check if it's a variable product
            if not cls.is_variable_product_with_series(product):
                return {
                    'eligible': False,
                    'series_value': None,
                    'service_name': None,
                    'reason': 'Product is not a variable product with series'
                }
            
            # Extract series value from variation data
            series_value = None
            
            # Check if variation_data has attributes (from frontend)
            if isinstance(variation_data, dict) and 'attributes' in variation_data:
                for attr in variation_data['attributes']:
                    if attr.get('name') in ['Series', 'pa_series']:
                        attr_value = attr.get('value', '')
                        # Parse series value from different formats
                        if attr_value.lower() == 'single':
                            series_value = 1
                        elif attr_value.isdigit():
                            series_value = int(attr_value)
                        else:
                            # Try to extract number from strings like "3 series", "6-series"
                            import re
                            match = re.search(r'(\d+)', attr_value)
                            if match:
                                series_value = int(match.group(1))
                        break
            
            # Also check if variation_data directly contains series info
            elif isinstance(variation_data, dict):
                if 'series_value' in variation_data:
                    series_value = variation_data['series_value']
                elif 'seriesValue' in variation_data:
                    series_value = variation_data['seriesValue']
            
            if series_value is None:
                return {
                    'eligible': False,
                    'series_value': None,
                    'service_name': None,
                    'reason': 'Could not determine series value from variation'
                }
            
            # Check eligibility against woo_service_types
            product_name = product.name if hasattr(product, 'name') else str(product)
            is_eligible = cls.is_product_series_eligible_for_credits(product_name, series_value)
            
            if is_eligible:
                # Find the matching service name
                from .models import CreditServicePoints, Contact, Product, WooServiceTypes
                service_type = WooServiceTypes.objects.filter(
                    name__icontains=product_name
                ).first()
                
                service_name = service_type.name if service_type else product_name
                
                return {
                    'eligible': True,
                    'series_value': series_value,
                    'service_name': service_name,
                    'reason': f'Product "{product_name}" with series {series_value} is eligible for credit services'
                }
            else:
                return {
                    'eligible': False,
                    'series_value': series_value,
                    'service_name': product_name,
                    'reason': f'Series {series_value} is not eligible for credits on product "{product_name}"'
                }
                
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error checking variation eligibility: {str(e)}")
            return {
                'eligible': False,
                'series_value': None,
                'service_name': None,
                'reason': f'Error checking eligibility: {str(e)}'
            }
    
    @classmethod
    def process_order_completion(cls, order_data):
        """
        Process order completion for credit service points.
        
        Args:
            order_data: Dictionary containing order information
            
        Returns:
            dict: Processing results
        """
        results = {
            'processed_items': [],
            'errors': [],
            'total_points_added': 0,
            'total_points_deducted': 0
        }
        
        try:
            customer_id = order_data.get('customer_id')
            if not customer_id:
                results['errors'].append('No customer ID provided')
                return results
                
            # Get customer
            try:
                customer = Contact.objects.get(id=customer_id)
            except Contact.DoesNotExist:
                results['errors'].append(f'Customer not found: {customer_id}')
                return results
            
            # Process each order item
            for item in order_data.get('items', []):
                try:
                    product_id = item.get('product_id')
                    if not product_id:
                        continue
                    
                    # Check if this is a credited service redemption
                    is_credited_service = item.get('is_credited_service', False)
                    
                    if is_credited_service:
                        # Handle credited service redemption
                        credited_service_id = item.get('credited_service_id') or product_id
                        
                        # Find the credited service record
                        try:
                            credit_points = cls.objects.get(id=credited_service_id)
                        except cls.DoesNotExist:
                            results['errors'].append(f'Credited service not found: {credited_service_id}')
                            continue
                        
                        # Deduct one point for redemption
                        if credit_points.points > 0:
                            credit_points.points -= 1
                            credit_points.save()
                            
                            # Log the redemption
                            credit_points.log_credit_change(
                                action="redeem",
                                points_change=-1,
                                order_number=order_data.get('order_id', 'UNKNOWN'),
                                reason="Credited service redemption",
                                notes=f"Customer: {customer.first_name} {customer.last_name} | Service: {credit_points.product.name}"
                            )
                            
                            results['total_points_deducted'] += 1
                            results['processed_items'].append({
                                'product_name': credit_points.product.name,
                                'action': 'redeemed',
                                'points': -1,
                                'remaining_points': credit_points.points,
                                'cost_adjustment': 'free',
                                'reason': 'Credited service redemption'
                            })
                        else:
                            results['errors'].append(f'No credits available for service: {credit_points.product.name}')
                        
                        continue  # Skip regular product processing for credited services
                        
                    # Get product for regular items
                    try:
                        product = Product.objects.get(id=product_id)
                    except Product.DoesNotExist:
                        results['errors'].append(f'Product not found: {product_id}')
                        continue
                    
                    # 🔥 CRITICAL FIX: Only skip bundle items that are NOT credited services
                    is_bundle_item = item.get('metadata', {}).get('is_bundle_item', False)
                    bundle_parent_id = item.get('metadata', {}).get('bundle_parent_id')
                    
                    print(f"🔍 BUNDLE DEBUG - Checking bundle status for {product.name}:")
                    print(f"  - is_bundle_item: {is_bundle_item}")
                    print(f"  - bundle_parent_id: {bundle_parent_id}")
                    print(f"  - metadata: {item.get('metadata', {})}")
                    
                    # Check if this is a variable product with series (i.e., credited service)
                    is_credited_service_product = cls.is_variable_product_with_series(product)
                    print(f"  - is_credited_service_product: {is_credited_service_product}")
                    
                    if (is_bundle_item or bundle_parent_id) and not is_credited_service_product:
                        # Skip bundle items that are NOT credited services
                        print(f"🔍 BUNDLE DEBUG - SKIPPING non-credited-service bundle item: {product.name}")
                        results['processed_items'].append({
                            'product_name': product.name,
                            'action': 'skipped',
                            'points': 0,
                            'remaining_points': 0,
                            'cost_adjustment': 'normal_price',
                            'reason': 'Bundle items are not eligible for credited service processing'
                        })
                        continue
                    elif (is_bundle_item or bundle_parent_id) and is_credited_service_product:
                        # Process bundle items that ARE credited services
                        print(f"🔍 BUNDLE DEBUG - PROCESSING credited service bundle item: {product.name}")
                    else:
                        print(f"🔍 BUNDLE DEBUG - NOT a bundle item, proceeding with processing: {product.name}")
                    
                    # At this point, we know it's a credited service product (already checked above)
                    
                    quantity = item.get('quantity', 1)
                    
                    # 🔥 CRITICAL FIX: Better series count detection
                    # Try multiple sources for series count in order of preference
                    metadata_series_selection = item.get('metadata', {}).get('series_selection')
                    metadata_series_value = item.get('metadata', {}).get('seriesValue')
                    item_series_count = item.get('series_count')
                    item_select_series = item.get('selectSeries')
                    
                    print(f"🔍 CREDITED SERVICES DEBUG - Series count resolution for {product.name}:")
                    print(f"  - series_count from item: {item_series_count}")
                    print(f"  - seriesValue from metadata: {metadata_series_value}")
                    print(f"  - series_selection from metadata: {metadata_series_selection}")
                    print(f"  - selectSeries from item: {item_select_series}")
                    print(f"  - quantity: {quantity}")
                    
                    # 🔥 BUNDLE-AWARE PRIORITY: Different logic for bundle vs standalone items
                    if is_bundle_item or bundle_parent_id:
                        # For BUNDLE items: metadata.series_selection is the correct source
                        if metadata_series_selection is not None:
                            series_count = metadata_series_selection
                            print(f"  ✅ Bundle item - Using metadata.series_selection: {series_count}")
                        elif metadata_series_value is not None:
                            series_count = metadata_series_value
                            print(f"  ✅ Bundle item - Using metadata.seriesValue: {series_count}")
                        elif item_series_count is not None:
                            series_count = item_series_count
                            print(f"  ✅ Bundle item - Using series_count: {series_count}")
                        else:
                            series_count = quantity
                            print(f"  ✅ Bundle item - Using quantity as fallback: {series_count}")
                    else:
                        # For STANDALONE items: metadata.seriesValue and series_count are correct sources
                        if metadata_series_value is not None:
                            series_count = metadata_series_value
                            print(f"  ✅ Standalone - Using metadata.seriesValue: {series_count}")
                        elif item_series_count is not None:
                            series_count = item_series_count
                            print(f"  ✅ Standalone - Using series_count: {series_count}")
                        elif metadata_series_selection is not None:
                            series_count = metadata_series_selection
                            print(f"  ✅ Standalone - Using metadata.series_selection: {series_count}")
                        else:
                            series_count = quantity
                            print(f"  ✅ Standalone - Using quantity as fallback: {series_count}")
                    # selectSeries is NOT used for series count as it's a UI field
                    
                    # Ensure series_count is an integer
                    try:
                        series_count = int(series_count)
                    except (ValueError, TypeError):
                        series_count = quantity
                        print(f"  ⚠️ Failed to convert to int, using quantity: {series_count}")
                    
                    print(f"  🎯 FINAL series_count: {series_count}")
                    
                    # 🔥 CRITICAL FIX: Don't create records for items that will be skipped
                    # Only create/get records when we actually need to process the item
                    
                    # 🎯 NEW LOGIC: Distinguish between "Single" (redemption) and "1 series" (earning)
                    variation_type = item.get('metadata', {}).get('variationType', 'earning')
                    variation_name = item.get('metadata', {}).get('variationName', '')
                    
                    print(f"🎯 CREDITED SERVICES - Variation type: {variation_type}, name: {variation_name}, series_count: {series_count}, quantity: {quantity}")
                    
                    if variation_type == 'redemption' and series_count == 1:
                        # "Single" variation - REDEMPTION ONLY (never earns)
                        print(f"🎯 Processing 'Single' variation for {product.name} - REDEMPTION mode")
                        
                        existing_record = cls.objects.filter(customer=customer, product=product).first()
                        
                        if existing_record and existing_record.points >= 1:
                            # Customer has credits - redeem 1 credit, set price to $0
                            from .models import WooServiceTypes
                            service_type = WooServiceTypes.objects.filter(
                                name__icontains=product.name
                            ).first()
                            
                            if service_type:
                                existing_record.points -= 1
                                existing_record.save()
                                
                                existing_record.log_credit_change(
                                    action="redeem",
                                    points_change=-1,
                                    order_number=order_data.get('order_id', 'UNKNOWN'),
                                    reason="Single series redemption",
                                    notes=f"Variation: Single | Cost set to $0"
                                )
                                
                                results['total_points_deducted'] += 1
                                results['processed_items'].append({
                                    'product_name': product.name,
                                    'action': 'deducted',
                                    'points': 1,
                                    'remaining_points': existing_record.points,
                                    'cost_adjustment': 'set_to_zero'
                                })
                            else:
                                results['processed_items'].append({
                                    'product_name': product.name,
                                    'action': 'skipped',
                                    'points': 0,
                                    'remaining_points': existing_record.points,
                                    'cost_adjustment': 'normal_price',
                                    'reason': f'Product "{product.name}" not eligible for redemption'
                                })
                        else:
                            # Customer has 0 credits - charge normal price, NO earning
                            current_points = existing_record.points if existing_record else 0
                            results['processed_items'].append({
                                'product_name': product.name,
                                'action': 'skipped',
                                'points': 0,
                                'remaining_points': current_points,
                                'cost_adjustment': 'normal_price',
                                'reason': f'Single series - no credits available for redemption'
                            })
                    
                    elif variation_type == 'earning' and series_count == 1:
                        # "1 series" variation - CHECK ELIGIBILITY FIRST
                        print(f"🎯 Processing '1 series' variation for {product.name} - EARNING mode (qty: {quantity})")
                        
                        # 🔥 CRITICAL FIX: Check if product is in woo_service_types with series value 1
                        is_eligible = cls.is_product_series_eligible_for_credits(product.name, 1)
                        
                        if is_eligible:
                            # Product is eligible - earn credits equal to quantity
                            credits_to_earn = quantity * 1
                            
                            credit_points, created = cls.objects.get_or_create(
                                customer=customer,
                                product=product,
                                defaults={
                                    'contact_ghl_id': customer.ghl_contact_id,
                                    'contact_woo_id': customer.woo_customer_id,
                                    'points': 0
                                }
                            )
                            
                            credit_points.points += credits_to_earn
                            credit_points.save()
                            
                            credit_points.log_credit_change(
                                action="add",
                                points_change=credits_to_earn,
                                order_number=order_data.get('order_id', 'UNKNOWN'),
                                reason="1 series purchase (quantity-based earning)",
                                notes=f"Variation: 1 series | Quantity: {quantity} | Credits earned: {credits_to_earn}"
                            )
                            
                            results['total_points_added'] += credits_to_earn
                            results['processed_items'].append({
                                'product_name': product.name,
                                'action': 'added',
                                'points': credits_to_earn,
                                'remaining_points': credit_points.points,
                                'cost_adjustment': 'normal_price'
                            })
                            
                            print(f"✅ '1 series' earned {credits_to_earn} credits for {product.name} (qty: {quantity})")
                        else:
                            # Product not eligible for credits
                            existing_record = cls.objects.filter(customer=customer, product=product).first()
                            current_points = existing_record.points if existing_record else 0
                            results['processed_items'].append({
                                'product_name': product.name,
                                'action': 'skipped',
                                'points': 0,
                                'remaining_points': current_points,
                                'cost_adjustment': 'normal_price',
                                'reason': f'Product "{product.name}" not found in woo_service_types or series 1 not available'
                            })
                            print(f"❌ '1 series' skipped for {product.name} - not eligible for credits")
                    else:
                        # Multi-series purchase - check eligibility before adding points
                        is_eligible = cls.is_product_series_eligible_for_credits(product.name, series_count)
                        
                        if is_eligible:
                            # 🔥 CRITICAL FIX: Only create record when we're actually adding points
                            credit_points, created = cls.objects.get_or_create(
                                customer=customer,
                                product=product,
                                defaults={
                                    'contact_ghl_id': customer.ghl_contact_id,
                                    'contact_woo_id': customer.woo_customer_id,
                                    'points': 0
                                }
                            )
                            
                            # Eligible series - add points equal to series count
                            credit_points.points += series_count
                            credit_points.save()
                            
                            # Log the credit addition
                            credit_points.log_credit_change(
                                action="add",
                                points_change=series_count,
                                order_number=order_data.get('order_id', 'UNKNOWN'),
                                reason="Multi-series purchase",
                                notes=f"Series: {series_count} | Eligible for credits"
                            )
                            
                            results['total_points_added'] += series_count
                            results['processed_items'].append({
                                'product_name': product.name,
                                'action': 'added',
                                'points': series_count,
                                'remaining_points': credit_points.points,
                                'cost_adjustment': 'normal_price'
                            })
                        else:
                            # Not eligible - no points awarded
                            # 🔥 CRITICAL FIX: Don't create record, just skip without database entry
                            existing_record = cls.objects.filter(customer=customer, product=product).first()
                            current_points = existing_record.points if existing_record else 0
                            results['processed_items'].append({
                                'product_name': product.name,
                                'action': 'skipped',
                                'points': 0,
                                'remaining_points': current_points,
                                'cost_adjustment': 'normal_price',
                                'reason': f'Series {series_count} is not eligible for credits on "{product.name}"'
                            })
                        
                except Exception as e:
                    results['errors'].append(f'Error processing item {item}: {str(e)}')
                    
        except Exception as e:
            results['errors'].append(f'Error processing order: {str(e)}')
        
        # 🔥 NEW: Process membership products for GHL sync
        try:
            membership_results = cls._process_membership_products_for_ghl(customer, order_data)
            if membership_results:
                results['membership_sync'] = membership_results
                logger.info(f"Membership GHL sync results: {membership_results}")
        except Exception as e:
            logger.error(f"Error processing membership GHL sync: {str(e)}")
            results['errors'].append(f'Membership GHL sync error: {str(e)}')
            
        return results
    
    @classmethod
    def _process_membership_products_for_ghl(cls, customer, order_data):
        """
        Process membership products and sync to GHL
        """
        from .ghl_membership_sync import (
            is_membership_product, 
            sync_membership_purchase_to_ghl,
            extract_billing_schedule_from_order_data
        )
        
        membership_results = {
            'processed_memberships': [],
            'errors': []
        }
        
        try:
            # Extract billing schedule info from order data
            billing_schedule = extract_billing_schedule_from_order_data(order_data)
            
            # Check each item for membership products
            for item in order_data.get('items', []):
                try:
                    product_name = item.get('product_name', '')
                    
                    # Check if this is a membership product
                    if is_membership_product(product_name):
                        logger.info(f"🎯 Processing membership product for GHL sync: {product_name}")
                        
                        # Prepare membership data for GHL sync
                        membership_data = {
                            'product_name': product_name,
                            'amount_paid': float(item.get('price', 0)) * int(item.get('quantity', 1)),
                            'billing_interval': billing_schedule.get('billing_interval', 1),
                            'billing_period': billing_schedule.get('billing_period', 'month'),
                            'start_date': order_data.get('created_at') or timezone.now(),
                            'subscription_id': order_data.get('subscription_id', 'N/A'),
                            'order_id': order_data.get('order_id', 'N/A')
                        }
                        
                        # Sync to GHL
                        sync_results = sync_membership_purchase_to_ghl(
                            customer.email, 
                            membership_data
                        )
                        
                        membership_results['processed_memberships'].append({
                            'product_name': product_name,
                            'sync_results': sync_results
                        })
                        
                        logger.info(f"✅ Membership GHL sync completed for {product_name}")
                        
                except Exception as e:
                    error_msg = f"Error processing membership item {item}: {str(e)}"
                    logger.error(error_msg)
                    membership_results['errors'].append(error_msg)
            
        except Exception as e:
            error_msg = f"Error in membership GHL processing: {str(e)}"
            logger.error(error_msg)
            membership_results['errors'].append(error_msg)
        
        return membership_results if membership_results['processed_memberships'] or membership_results['errors'] else None


class WebhookLog(models.Model):
    """
    Log table for tracking webhook requests and their processing status
    """
    WEBHOOK_TYPE_CHOICES = [
        ('product', 'Product Update'),
        ('customer', 'Customer Update'),
        ('order', 'Order Update'),
        ('subscription', 'Subscription Update'),
        ('subscription_test', 'Subscription Test'),
        ('ghl_membership', 'GHL Membership Update'),
        ('inventory', 'Inventory Update'),
        ('test', 'Test Webhook'),
    ]
    
    STATUS_CHOICES = [
        ('received', 'Received'),
        ('processing', 'Processing'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    webhook_type = models.CharField(max_length=20, choices=WEBHOOK_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='received')
    
    # Request details
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    request_body = models.TextField(blank=True, help_text="Raw webhook request body")
    
    # Processing details
    woo_product_id = models.IntegerField(null=True, blank=True, help_text="WooCommerce product ID if applicable")
    local_product_id = models.UUIDField(null=True, blank=True, help_text="Local product UUID if applicable")
    action_taken = models.CharField(max_length=50, blank=True, help_text="Action performed (create, update, delete, etc.)")
    updated_fields = models.JSONField(default=list, blank=True, help_text="List of fields that were updated")
    update_analysis = models.JSONField(default=dict, blank=True, help_text="Detailed analysis of what was updated, categorized by type (basic_info, pricing, inventory, etc.)")
    
    # Bundle-specific tracking
    is_bundle_product = models.BooleanField(default=False)
    bundle_sync_attempted = models.BooleanField(default=False)
    bundle_sync_success = models.BooleanField(default=False)
    bundle_items_count = models.IntegerField(null=True, blank=True)
    default_series_updated = models.BooleanField(default=False)
    
    # ATUM inventory-specific tracking
    atum_sync_attempted = models.BooleanField(default=False, help_text="Whether ATUM inventory sync was attempted")
    atum_sync_success = models.BooleanField(default=False, help_text="Whether ATUM inventory sync completed successfully")
    atum_changes_detected = models.BooleanField(default=False, help_text="Whether ATUM inventory changes were detected in webhook")
    atum_variations_processed = models.IntegerField(null=True, blank=True, help_text="Number of variations processed for ATUM sync")
    atum_variations_updated = models.IntegerField(null=True, blank=True, help_text="Number of variations updated during ATUM sync")
    atum_locations_added = models.IntegerField(null=True, blank=True, help_text="Number of ATUM inventory locations added")
    atum_locations_removed = models.IntegerField(null=True, blank=True, help_text="Number of ATUM inventory locations removed")
    atum_product_updated = models.BooleanField(default=False, help_text="Whether the main product ATUM inventory was updated")
    atum_errors = models.JSONField(default=list, blank=True, help_text="List of ATUM sync errors if any")
    
    # Response and error tracking
    response_status_code = models.IntegerField(null=True, blank=True)
    response_message = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    processing_time_ms = models.IntegerField(null=True, blank=True, help_text="Processing time in milliseconds")
    
    # Timestamps
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-received_at']
        indexes = [
            models.Index(fields=['webhook_type', 'status']),
            models.Index(fields=['woo_product_id']),
            models.Index(fields=['received_at']),
            models.Index(fields=['is_bundle_product']),
            models.Index(fields=['atum_sync_attempted']),
            models.Index(fields=['atum_changes_detected']),
        ]
    
    def __str__(self):
        return f"{self.webhook_type} webhook - {self.status} - {self.received_at}"
    
    def mark_processing(self):
        """Mark webhook as being processed"""
        self.status = 'processing'
        self.save(update_fields=['status'])
    
    def mark_success(self, response_message="", processing_time_ms=None):
        """Mark webhook as successfully processed"""
        self.status = 'success'
        self.processed_at = timezone.now()
        self.response_message = response_message
        if processing_time_ms:
            self.processing_time_ms = processing_time_ms
        self.save(update_fields=['status', 'processed_at', 'response_message', 'processing_time_ms'])
    
    def mark_failed(self, error_message="", processing_time_ms=None):
        """Mark webhook as failed"""
        self.status = 'failed'
        self.processed_at = timezone.now()
        self.error_message = error_message
        if processing_time_ms:
            self.processing_time_ms = processing_time_ms
        self.save(update_fields=['status', 'processed_at', 'error_message', 'processing_time_ms'])
    
    def mark_skipped(self, reason=""):
        """Mark webhook as skipped"""
        self.status = 'skipped'
        self.processed_at = timezone.now()
        self.response_message = reason
        self.save(update_fields=['status', 'processed_at', 'response_message'])
    
    def update_atum_sync_info(self, changes_detected=False, sync_attempted=False, sync_results=None):
        """Update ATUM inventory sync information"""
        self.atum_changes_detected = changes_detected
        self.atum_sync_attempted = sync_attempted
        
        if sync_results:
            self.atum_sync_success = sync_results.get('success', False)
            self.atum_variations_processed = sync_results.get('variations_processed', 0)
            self.atum_variations_updated = sync_results.get('variations_updated', 0)
            self.atum_locations_added = sync_results.get('locations_added', 0)
            self.atum_locations_removed = sync_results.get('locations_removed', 0)
            self.atum_product_updated = sync_results.get('product_updated', False)
            self.atum_errors = sync_results.get('errors', [])
        
        update_fields = [
            'atum_changes_detected', 'atum_sync_attempted', 'atum_sync_success',
            'atum_variations_processed', 'atum_variations_updated', 
            'atum_locations_added', 'atum_locations_removed',
            'atum_product_updated', 'atum_errors'
        ]
        self.save(update_fields=update_fields)


class WooServiceTypes(models.Model):
    id = models.BigAutoField(primary_key=True)   # PostgreSQL BIGSERIAL
    name = models.CharField(max_length=255)
    default_index = models.IntegerField(null=True, blank=True)
    series = models.JSONField(null=True, blank=True)        # Native JSONB in PostgreSQL
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "woo_service_types"


class WooCreditLog(models.Model):
    id = models.BigAutoField(primary_key=True)   # PostgreSQL BIGSERIAL
    woo_credited_service_id = models.IntegerField()
    action = models.CharField(max_length=255)
    order_number = models.CharField(max_length=255)
    points_change = models.IntegerField()
    updated_points = models.IntegerField()
    reason = models.CharField(max_length=255)
    notes = models.TextField()
    source = models.CharField(max_length=50, default='POS', help_text="Origin of the credit change: POS, MANUAL, WEB")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "woo_credit_logs"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['woo_credited_service_id']),
            models.Index(fields=['action']),
            models.Index(fields=['order_number']),
            models.Index(fields=['created_at']),
            models.Index(fields=['source'], name='woo_credit__source_98ac46_idx'),
        ]


class SavedCart(models.Model):
    """Model for saving cart items for later use"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, help_text="User-defined name for the saved cart")
    user = models.ForeignKey(User, on_delete=models.CASCADE, help_text="User who saved the cart")
    customer = models.ForeignKey('Contact', on_delete=models.CASCADE, null=True, blank=True, help_text="Customer associated with the saved cart")
    cart_items = models.JSONField(help_text="JSON array of cart items with all their properties")
    order_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Order-level discount amount")
    order_discount_type = models.CharField(max_length=10, choices=[('percentage', 'Percentage'), ('dollar', 'Dollar')], default='dollar', help_text="Type of order discount")
    order_discount_reason = models.CharField(max_length=500, null=True, blank=True, help_text="Reason/note for the order-level discount")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "saved_carts"
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['created_at']),
            models.Index(fields=['updated_at']),
        ]
    
    def __str__(self):
        return f"{self.name} - {self.user.username}"


class CustomerGeneralNote(models.Model):
    """Model for storing general notes about customers"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey('Contact', on_delete=models.CASCADE, related_name='general_notes', help_text="Customer this note belongs to")
    note = models.TextField(help_text="The note content")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, help_text="User who created the note")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Flag to mark important notes that should trigger notifications
    is_important = models.BooleanField(
        default=False, 
        help_text="Whether this note is marked as important and should trigger notifications"
    )
    
    class Meta:
        db_table = "customer_general_notes"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer']),
            models.Index(fields=['created_by']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Note for {self.customer.first_name} {self.customer.last_name} by {self.created_by.username}"


class CustomerPointsAccount(models.Model):
    """
    Fractional points balance (source of truth for POS points redemption)
    This replaces YITH as the primary points storage, allowing decimal precision.
    YITH receives floored integer values for display purposes only.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.OneToOneField(
        'Contact', 
        on_delete=models.CASCADE,
        related_name='points_account'
    )
    
    # Fractional points balance - the source of truth
    points_balance = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        default=0,
        help_text="Current fractional points balance (e.g., 153.60)"
    )
    
    # YITH synchronization tracking
    yith_synced_points = models.IntegerField(
        default=0,
        help_text="Last integer value synced to YITH (e.g., 153)"
    )
    yith_last_sync = models.DateTimeField(null=True, blank=True)
    sync_needed = models.BooleanField(
        default=False,
        help_text="Flag to indicate sync to YITH is needed"
    )
    
    # Audit fields
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'crm_customer_points_account'
        verbose_name = 'Customer Points Account'
        verbose_name_plural = 'Customer Points Accounts'
        indexes = [
            models.Index(fields=['customer']),
            models.Index(fields=['sync_needed']),
        ]
    
    def __str__(self):
        return f"{self.customer.first_name} {self.customer.last_name} - {self.points_balance} points"
    
    def redeem_points(self, amount, order_id=None, description="Points redeemed", user=None):
        """
        Redeem points and create transaction record
        
        Args:
            amount: Decimal amount to redeem (positive number)
            order_id: Optional order ID
            description: Transaction description
            user: User who performed the action
            
        Returns:
            PointsTransaction instance
        """
        from decimal import Decimal
        amount = Decimal(str(amount))
        
        if amount <= 0:
            raise ValueError("Amount must be positive")
        
        if self.points_balance < amount:
            raise ValueError(f"Insufficient points. Available: {self.points_balance}, Requested: {amount}")
        
        self.points_balance -= amount
        self.sync_needed = True
        self.save()
        
        transaction = PointsTransaction.objects.create(
            customer=self.customer,
            account=self,
            transaction_type='redeem',
            amount=-amount,
            balance_after=self.points_balance,
            order_id=order_id,
            description=description,
            created_by=user
        )
        
        return transaction
    
    def add_points(self, amount, order_id=None, description="Points earned", user=None, transaction_type='earn'):
        """
        Add points and create transaction record
        
        Args:
            amount: Decimal amount to add (positive number)
            order_id: Optional order ID
            description: Transaction description
            user: User who performed the action
            transaction_type: Type of transaction ('earn', 'adjust', 'refund', 'sync')
            
        Returns:
            PointsTransaction instance
        """
        from decimal import Decimal
        amount = Decimal(str(amount))
        
        if amount <= 0:
            raise ValueError("Amount must be positive")
        
        self.points_balance += amount
        self.sync_needed = True
        self.save()
        
        transaction = PointsTransaction.objects.create(
            customer=self.customer,
            account=self,
            transaction_type=transaction_type,
            amount=amount,
            balance_after=self.points_balance,
            order_id=order_id,
            description=description,
            created_by=user
        )
        
        return transaction


class PointsTransaction(models.Model):
    """
    Complete audit trail for all points changes
    Every change to CustomerPointsAccount creates a transaction record
    """
    TRANSACTION_TYPES = [
        ('earn', 'Points Earned'),
        ('redeem', 'Points Redeemed'),
        ('adjust', 'Manual Adjustment'),
        ('expire', 'Points Expired'),
        ('refund', 'Order Refund'),
        ('sync', 'YITH Sync Adjustment'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(
        'Contact', 
        on_delete=models.CASCADE,
        related_name='points_transactions'
    )
    account = models.ForeignKey(
        'CustomerPointsAccount', 
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    amount = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Change amount (negative for deductions, positive for additions)"
    )
    balance_after = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        help_text="Balance after this transaction"
    )
    
    # Context
    order_id = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    class Meta:
        db_table = 'crm_points_transaction'
        verbose_name = 'Points Transaction'
        verbose_name_plural = 'Points Transactions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['customer', '-created_at']),
            models.Index(fields=['account', '-created_at']),
            models.Index(fields=['transaction_type']),
            models.Index(fields=['order_id']),
        ]
    
    def __str__(self):
        return f"{self.customer.first_name} {self.customer.last_name} - {self.transaction_type} - {self.amount}"


class EmailLog(models.Model):
    """
    Audit log for every outbound email sent by the POS system.
    Tracks delivery status, backend used, and toggle-skip events.
    """
    EMAIL_TYPE_CHOICES = [
        ('order_receipt', 'Order Receipt'),
        ('refund_receipt', 'Refund Receipt'),
        ('auto_refund_receipt', 'Auto Refund Receipt'),
        ('cancellation', 'Cancellation Email'),
        ('tracking', 'Tracking / Shipped Email'),
        ('partial_tracking', 'Partial Shipped Email'),
        ('installment_receipt', 'Installment Receipt'),
        ('plan_summary', 'Payment Plan Summary'),
        ('membership_onboarding', 'Membership Onboarding'),
        ('membership_cancellation', 'Membership Cancellation'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped (Toggle Off)'),
    ]

    BACKEND_CHOICES = [
        ('mailgun', 'Mailgun'),
        ('smtp', 'SMTP'),
        ('none', 'None'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email_type = models.CharField(max_length=30, choices=EMAIL_TYPE_CHOICES)
    recipient = models.EmailField(help_text="Recipient email address")
    subject = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='sent')
    delivery_backend = models.CharField(max_length=10, choices=BACKEND_CHOICES, default='none')
    error_message = models.TextField(blank=True, help_text="Error details if delivery failed")
    related_order = models.CharField(max_length=100, blank=True, help_text="Order number, subscription ID, or plan number")
    metadata = models.JSONField(default=dict, blank=True, help_text="Extra context (order total, items, etc.)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'crm_email_log'
        verbose_name = 'Email Log'
        verbose_name_plural = 'Email Logs'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email_type', 'status'], name='crm_email_l_email_t_idx'),
            models.Index(fields=['recipient'], name='crm_email_l_recipie_idx'),
            models.Index(fields=['related_order'], name='crm_email_l_related_idx'),
            models.Index(fields=['created_at'], name='crm_email_l_created_idx'),
        ]

    def __str__(self):
        return f"{self.get_email_type_display()} → {self.recipient} [{self.status}] {self.created_at:%Y-%m-%d %H:%M}"


class Invoice(models.Model):
    """
    Persisted invoice record for patient self-service payment via email link.
    Each invoice gets a signed, time-limited payment token.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
        ('locked', 'Locked'),  # Too many failed payment attempts
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_ref = models.CharField(max_length=50, db_index=True, help_text="Human-readable invoice reference (e.g. INV-1234567890)")
    contact = models.ForeignKey(
        'Contact', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='invoices', help_text="Patient / customer this invoice belongs to"
    )
    email = models.EmailField(help_text="Email address the invoice was sent to")
    items = models.JSONField(default=list, help_text="Line items: [{name, quantity, price, sku, brand}]")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')

    # Payment token — signed, expiring, one-time use
    payment_token = models.CharField(max_length=512, unique=True, blank=True, help_text="Signed token for the Pay Now link")
    token_expires_at = models.DateTimeField(help_text="Absolute expiry of the payment token")

    # Payment result
    paid_at = models.DateTimeField(null=True, blank=True)
    transaction_id = models.CharField(max_length=100, blank=True, help_text="Authorize.net transaction ID")
    payment_method_used = models.CharField(max_length=50, blank=True, help_text="e.g. 'saved_card_visa_1234' or 'new_card'")
    payer_ip = models.GenericIPAddressField(null=True, blank=True, help_text="IP of the person who paid")
    woo_order_id = models.IntegerField(null=True, blank=True, help_text="WooCommerce order ID created on payment")

    # Abuse prevention
    failed_attempts = models.IntegerField(default=0, help_text="Consecutive failed payment attempts")

    # Audit
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Staff member who created/sent this invoice"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'crm_invoice'
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status'], name='crm_invoice_status_idx'),
            models.Index(fields=['payment_token'], name='crm_invoice_token_idx'),
            models.Index(fields=['contact'], name='crm_invoice_contact_idx'),
            models.Index(fields=['created_at'], name='crm_invoice_created_idx'),
        ]

    def __str__(self):
        return f"Invoice {self.invoice_ref} — {self.get_status_display()} — ${self.total}"


class GHLOrder(models.Model):
    """
    Locally-cached GoHighLevel order from the Payments/Orders API.
    Synced lazily on Order History page load; served from Redis/PostgreSQL.
    """
    ghl_order_id = models.CharField(max_length=50, unique=True, db_index=True)
    alt_id = models.CharField(max_length=50, help_text="GHL location ID")
    ghl_contact_id = models.CharField(max_length=50, db_index=True, help_text="GHL contact ID")
    contact_name = models.CharField(max_length=255, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    currency = models.CharField(max_length=10, default='USD')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=30)
    payment_status = models.CharField(max_length=30, blank=True, default='')
    fulfillment_status = models.CharField(max_length=30, default='unfulfilled')
    live_mode = models.BooleanField(default=True)
    total_products = models.IntegerField(default=0)
    onetime_products = models.IntegerField(default=0)
    source_type = models.CharField(max_length=50, blank=True, default='')
    source_name = models.CharField(max_length=255, blank=True, default='')
    source_id = models.CharField(max_length=100, blank=True, default='')
    source_meta = models.JSONField(default=dict, blank=True)
    ghl_created_at = models.DateTimeField(db_index=True)
    ghl_updated_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    contact = models.ForeignKey(
        'Contact', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='ghl_orders',
        help_text="Resolved local contact (matched by email or ghl_contact_id)"
    )

    class Meta:
        db_table = 'crm_ghl_order'
        verbose_name = 'GHL Order'
        verbose_name_plural = 'GHL Orders'
        ordering = ['-ghl_created_at']
        indexes = [
            models.Index(fields=['contact_email'], name='crm_ghlorder_email_idx'),
            models.Index(fields=['status'], name='crm_ghlorder_status_idx'),
            models.Index(fields=['ghl_created_at'], name='crm_ghlorder_created_idx'),
        ]

    def __str__(self):
        return f"GHL Order {self.ghl_order_id[:12]} — {self.contact_name} — ${self.amount}"


class FailedOrderQueue(models.Model):
    """
    Durable queue for order steps that fail AFTER payment has already been captured.
    If a post-payment step (e.g. WooCommerce order creation) fails, the order is
    enqueued here so it can be retried without re-charging the customer.
    """
    FAILED_STEP_CHOICES = (
        ('pos_order_save', 'POS Order Save'),
        ('woocommerce_order', 'WooCommerce Order Creation'),
        ('ghl_sync', 'GHL Membership Sync'),
        ('credit_points', 'Credit Service Points'),
        ('receipt', 'Receipt Email'),
    )
    STATUS_CHOICES = (
        ('queued', 'Queued'),
        ('retrying', 'Retrying'),
        ('resolved', 'Resolved'),
        ('failed', 'Permanently Failed'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pos_order = models.ForeignKey(
        POSOrder, on_delete=models.CASCADE, related_name='failed_queue_entries',
        null=True, blank=True
    )
    order_number = models.CharField(max_length=100, db_index=True)
    failed_step = models.CharField(max_length=50, choices=FAILED_STEP_CHOICES)
    error_message = models.TextField(blank=True)
    error_traceback = models.TextField(blank=True)

    payment_taken = models.BooleanField(default=True)
    payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    payment_method = models.CharField(max_length=100, blank=True)
    transaction_id = models.CharField(max_length=255, blank=True)

    request_payload = models.JSONField(default=dict, blank=True,
        help_text="Original request data so the step can be replayed")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='queued')
    retry_count = models.PositiveIntegerField(default=0)
    max_retries = models.PositiveIntegerField(default=5)
    last_retry_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)

    slack_notified = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_failed_orders'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Failed Order Queue'
        verbose_name_plural = 'Failed Order Queue'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['order_number']),
            models.Index(fields=['failed_step']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"[{self.status}] {self.order_number} — {self.get_failed_step_display()}"