from django.db import migrations


def seed_permissions_enabled(apps, schema_editor):
    """Seed the permissions_enabled POSSetting (default false = open for everyone)."""
    POSSetting = apps.get_model('crm', 'POSSetting')
    POSSetting.objects.get_or_create(
        key='permissions_enabled',
        defaults={
            'value': 'false',
            'setting_type': 'boolean',
            'description': 'Global toggle for the role-based permissions system. When false, all users have full access (transition mode). When true, permissions are enforced based on user roles.',
            'category': 'permissions',
        }
    )


def reverse_seed(apps, schema_editor):
    """Remove the permissions_enabled setting."""
    POSSetting = apps.get_model('crm', 'POSSetting')
    POSSetting.objects.filter(key='permissions_enabled').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0087_rename_woo_credit__source_idx_woo_credit__source_98ac46_idx'),
    ]

    operations = [
        migrations.RunPython(seed_permissions_enabled, reverse_seed),
    ]
