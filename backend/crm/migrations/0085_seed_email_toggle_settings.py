from django.db import migrations


def seed_email_toggles(apps, schema_editor):
    """Seed the email toggle POSSetting rows (all default to true)."""
    POSSetting = apps.get_model('crm', 'POSSetting')

    toggles = [
        ('email_order_receipt_enabled', 'Enable/disable order receipt emails', 'email'),
        ('email_refund_receipt_enabled', 'Enable/disable refund receipt emails', 'email'),
        ('email_auto_refund_receipt_enabled', 'Enable/disable auto-sent refund receipt emails on refund processing', 'email'),
        ('email_cancellation_enabled', 'Enable/disable subscription cancellation emails', 'email'),
        ('email_tracking_enabled', 'Enable/disable shipment tracking emails', 'email'),
        ('email_partial_tracking_enabled', 'Enable/disable partial shipment emails', 'email'),
        ('email_installment_receipt_enabled', 'Enable/disable payment plan installment receipt emails', 'email'),
        ('email_plan_summary_enabled', 'Enable/disable payment plan summary emails', 'email'),
    ]

    for key, description, category in toggles:
        POSSetting.objects.get_or_create(
            key=key,
            defaults={
                'value': 'true',
                'setting_type': 'boolean',
                'description': description,
                'category': category,
            }
        )


def reverse_seed(apps, schema_editor):
    """Remove seeded email toggle settings."""
    POSSetting = apps.get_model('crm', 'POSSetting')
    POSSetting.objects.filter(key__startswith='email_').filter(category='email').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0084_add_email_log_model'),
    ]

    operations = [
        migrations.RunPython(seed_email_toggles, reverse_seed),
    ]
