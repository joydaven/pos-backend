"""
Seed default payment plan templates.
Usage: python manage.py seed_payment_plan_templates
"""
from django.core.management.base import BaseCommand
from payment_plans.models import PaymentPlanTemplate


class Command(BaseCommand):
    help = 'Create default payment plan templates'

    def handle(self, *args, **options):
        templates = [
            {
                'name': '50/25/25 — 3 Monthly Installments',
                'description': '50% down payment, then 25% each of the following 2 months',
                'num_installments': 3,
                'installments_config': [
                    {'percentage': 50, 'offset_days': 0},
                    {'percentage': 25, 'offset_days': 30},
                    {'percentage': 25, 'offset_days': 60},
                ],
            },
        ]

        for t in templates:
            obj, created = PaymentPlanTemplate.objects.get_or_create(
                name=t['name'],
                defaults=t,
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created template: {obj.name}'))
            else:
                self.stdout.write(f'Template already exists: {obj.name}')
