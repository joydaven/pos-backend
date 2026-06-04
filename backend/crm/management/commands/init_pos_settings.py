from django.core.management.base import BaseCommand
from crm.models import POSSetting

class Command(BaseCommand):
    help = 'Initialize default POS settings in database'

    def handle(self, *args, **options):
        """Initialize default POS settings"""
        
        # Default settings to create
        default_settings = [
            {
                'key': 'enable_shipping',
                'value': False,
                'setting_type': 'boolean',
                'description': 'Enable shipping options during checkout',
                'category': 'shipping'
            },
            {
                'key': 'prevent_out_of_stock_cart',
                'value': True,
                'setting_type': 'boolean',
                'description': 'Prevent adding out-of-stock products to cart',
                'category': 'inventory'
            }
        ]
        
        created_count = 0
        updated_count = 0
        
        for setting_data in default_settings:
            setting, created = POSSetting.objects.get_or_create(
                key=setting_data['key'],
                defaults={
                    'value': 'true' if setting_data['value'] else 'false',
                    'setting_type': setting_data['setting_type'],
                    'description': setting_data['description'],
                    'category': setting_data['category']
                }
            )
            
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'Created setting: {setting.key} = {setting.value}')
                )
            else:
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(f'Setting already exists: {setting.key} = {setting.value}')
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Settings initialization complete. Created: {created_count}, Existing: {updated_count}'
            )
        )
