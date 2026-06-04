from django.core.management.base import BaseCommand
from django.utils import timezone
from crm.models import InventoryLocation
import uuid
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Add missing Boca Raton Clinic location with proper ATUM ID'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without making changes',
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Adding missing Boca Raton Clinic location...'))
        
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))

        # Check if Boca Raton Clinic already exists
        boca_exists = InventoryLocation.objects.filter(name='Boca Raton Clinic').exists()
        
        if boca_exists:
            self.stdout.write(self.style.WARNING('Boca Raton Clinic location already exists'))
            return
            
        # ATUM ID for Boca Raton Clinic - typically this would be 5 or 6 based on other locations
        # We'll use 5 as it's the next available ID after Chicago Clinic (4)
        atum_location_id = 5
        
        # Check if this ATUM ID is already in use
        atum_id_exists = InventoryLocation.objects.filter(atum_location_id=atum_location_id).exists()
        
        if atum_id_exists:
            self.stdout.write(self.style.WARNING(f'ATUM location ID {atum_location_id} is already in use'))
            # Find the next available ATUM ID
            existing_ids = set(InventoryLocation.objects.filter(
                atum_location_id__isnull=False
            ).values_list('atum_location_id', flat=True))
            
            for i in range(1, 20):  # Try IDs 1-20
                if i not in existing_ids:
                    atum_location_id = i
                    self.stdout.write(f'Using next available ATUM ID: {atum_location_id}')
                    break
        
        if dry_run:
            self.stdout.write(f'Would create Boca Raton Clinic location with ATUM ID: {atum_location_id}')
            return
            
        try:
            # Create the Boca Raton Clinic location
            location = InventoryLocation.objects.create(
                id=uuid.uuid4(),
                name='Boca Raton Clinic',
                code='BOCA-RATON-CLINIC',
                description='ATUM inventory location: Boca Raton Clinic',
                is_active=True,
                is_default=False,
                address='',
                atum_location_id=atum_location_id,
                slug='boca-raton-clinic',
                parent_location_id=None,
                barcode='',
                product_count=0
            )
            
            self.stdout.write(
                self.style.SUCCESS(f'Successfully created Boca Raton Clinic location with ATUM ID: {atum_location_id}')
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error creating Boca Raton Clinic location: {str(e)}')
            )
            logger.error(f'Error creating Boca Raton Clinic location: {str(e)}')
