# Generated manually for adding location tracking fields to POSOrder
# NOTE: These fields were already added in 0020, so this migration is now a no-op

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0020_remove_product_brand_posorder_assigned_location_and_more'),
    ]

    operations = [
        # Fields pos_location_id and pos_location_name already added in migration 0020
        # This migration is kept for dependency chain integrity
    ]
