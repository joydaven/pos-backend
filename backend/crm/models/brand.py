"""
Brand model for efficient brand management and filtering.

This model stores unique brands extracted from product woo_data to enable
fast brand fetching without processing JSON data on every request.
"""

from django.db import models
from django.utils import timezone


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
            from django.utils.text import slugify
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
        from .product_simple import ProductSimple
        from .product_variation import ProductVariation  
        from .product_bundle import ProductBundle
        from django.utils.text import slugify
        from collections import defaultdict
        import logging
        
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
