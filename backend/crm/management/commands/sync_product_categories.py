from django.core.management.base import BaseCommand
from crm.models import Product
from crm.woocommerce import WooCommerceAPI
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sync product categories from WooCommerce to fix outdated category names/slugs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes',
        )
        parser.add_argument(
            '--product-id',
            type=str,
            help='Sync specific product by ID',
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit number of products to process',
        )
        parser.add_argument(
            '--show-mapping',
            action='store_true',
            help='Show category mapping and exit',
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.wc_api = WooCommerceAPI()
        self.category_name_map = {}
        self.stats = {
            'total_products': 0,
            'products_updated': 0,
            'categories_mapped': 0,
            'errors': 0
        }

        # Load WooCommerce categories
        if not self.load_woocommerce_categories():
            self.stdout.write(self.style.ERROR('Failed to load categories from WooCommerce'))
            return

        if options['show_mapping']:
            self.show_category_mapping()
            return

        # Sync product categories
        self.sync_product_categories(
            product_id=options['product_id'],
            limit=options['limit']
        )

    def load_woocommerce_categories(self):
        """Load all categories from WooCommerce and create mapping dictionaries."""
        self.stdout.write("Loading categories from WooCommerce...")
        
        categories = self.wc_api.get_all_product_categories()
        if not categories:
            self.stdout.write(self.style.ERROR("Failed to load categories from WooCommerce"))
            return False
        
        # Create mappings: slug -> name and name -> name (for exact matches)
        for cat in categories:
            cat_name = cat.get('name', '')
            cat_slug = cat.get('slug', '')
            
            # Map slug to name (precision-path -> Precision Path)
            if cat_slug:
                self.category_name_map[cat_slug] = cat_name
            
            # Map name to name (for exact matches)
            if cat_name:
                self.category_name_map[cat_name] = cat_name
        
        self.stats['categories_mapped'] = len(categories)
        self.stdout.write(f"Loaded {len(categories)} categories from WooCommerce")
        return True

    def normalize_category_name(self, category):
        """
        Normalize a category name using WooCommerce data.
        
        Args:
            category: Category name or slug from local database
            
        Returns:
            Normalized category name from WooCommerce, or original if not found
        """
        if not category:
            return category
        
        # Direct lookup in mapping
        if category in self.category_name_map:
            return self.category_name_map[category]
        
        # Case-insensitive lookup
        category_lower = category.lower()
        for key, value in self.category_name_map.items():
            if key.lower() == category_lower:
                return value
        
        # If not found, return original
        self.stdout.write(self.style.WARNING(f"Category '{category}' not found in WooCommerce, keeping original"))
        return category

    def sync_product_categories(self, product_id=None, limit=None):
        """
        Sync product categories from WooCommerce.
        
        Args:
            product_id: Specific product ID to sync (optional)
            limit: Maximum number of products to process (optional)
        """
        self.stdout.write("Starting product category sync...")
        
        # Get products to process
        if product_id:
            products = Product.objects.filter(id=product_id)
            self.stdout.write(f"Processing specific product: {product_id}")
        else:
            products = Product.objects.all()
            if limit:
                products = products[:limit]
            self.stdout.write(f"Processing {products.count()} products")
        
        self.stats['total_products'] = products.count()
        
        for i, product in enumerate(products, 1):
            try:
                if i % 100 == 0:
                    self.stdout.write(f"Processed {i}/{self.stats['total_products']} products...")
                
                # Get current categories
                current_categories = product.categories or []
                if not current_categories:
                    continue
                
                # Normalize each category
                updated_categories = []
                categories_changed = False
                
                for category in current_categories:
                    normalized = self.normalize_category_name(category)
                    updated_categories.append(normalized)
                    
                    if normalized != category:
                        categories_changed = True
                        self.stdout.write(f"Product {product.id} ({product.name}): '{category}' -> '{normalized}'")
                
                # Update product if categories changed
                if categories_changed and not self.dry_run:
                    product.categories = updated_categories
                    product.save(update_fields=['categories'])
                    self.stats['products_updated'] += 1
                elif categories_changed:
                    self.stdout.write(self.style.WARNING(f"[DRY RUN] Would update product {product.id}: {current_categories} -> {updated_categories}"))
                    self.stats['products_updated'] += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing product {product.id}: {str(e)}"))
                self.stats['errors'] += 1
        
        # Print summary
        self.stdout.write(self.style.SUCCESS("=== SYNC SUMMARY ==="))
        self.stdout.write(f"Total products processed: {self.stats['total_products']}")
        self.stdout.write(f"Products updated: {self.stats['products_updated']}")
        self.stdout.write(f"Categories mapped: {self.stats['categories_mapped']}")
        self.stdout.write(f"Errors: {self.stats['errors']}")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes were made to the database"))
        else:
            self.stdout.write(self.style.SUCCESS("Category sync completed successfully"))

    def show_category_mapping(self):
        """Display the category mapping for review."""
        self.stdout.write(self.style.SUCCESS("=== CATEGORY MAPPING ==="))
        mappings_found = False
        for slug_or_name, wc_name in sorted(self.category_name_map.items()):
            if slug_or_name != wc_name:  # Only show mappings that would change
                self.stdout.write(f"'{slug_or_name}' -> '{wc_name}'")
                mappings_found = True
        
        if not mappings_found:
            self.stdout.write("No category mappings found that would change names")
