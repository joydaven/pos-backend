from django.db import migrations, models


def seed_default_roles(apps, schema_editor):
    """Seed Admin, Manager, and Staff roles with preset permission toggles."""
    UserRole = apps.get_model('users', 'UserRole')

    # Admin — full access
    UserRole.objects.update_or_create(
        name='admin',
        defaults={
            'description': 'Full system access. Can manage users, roles, settings, and all features.',
            'can_access_pos': True,
            'can_access_customers': True,
            'can_access_orders': True,
            'can_process_refunds': True,
            'can_access_subscriptions': True,
            'can_access_membership_subscriptions': True,
            'can_access_payment_plans': True,
            'can_manage_users': True,
            'can_manage_products': True,
            'can_manage_inventory': True,
            'can_process_orders': True,
            'can_view_reports': True,
            'can_manage_settings': True,
        },
    )

    # Manager — operational access, no user/settings management
    UserRole.objects.update_or_create(
        name='manager',
        defaults={
            'description': 'Operational access. Day-to-day management without user or system settings control.',
            'can_access_pos': True,
            'can_access_customers': True,
            'can_access_orders': True,
            'can_process_refunds': True,
            'can_access_subscriptions': True,
            'can_access_membership_subscriptions': True,
            'can_access_payment_plans': True,
            'can_manage_users': False,
            'can_manage_products': False,
            'can_manage_inventory': False,
            'can_process_orders': True,
            'can_view_reports': True,
            'can_manage_settings': False,
        },
    )

    # Staff — basic POS access only
    UserRole.objects.update_or_create(
        name='staff',
        defaults={
            'description': 'Basic access. POS transactions and customer lookup only.',
            'can_access_pos': True,
            'can_access_customers': True,
            'can_access_orders': False,
            'can_process_refunds': False,
            'can_access_subscriptions': False,
            'can_access_membership_subscriptions': False,
            'can_access_payment_plans': False,
            'can_manage_users': False,
            'can_manage_products': False,
            'can_manage_inventory': False,
            'can_process_orders': True,
            'can_view_reports': False,
            'can_manage_settings': False,
        },
    )


def reverse_seed(apps, schema_editor):
    """Remove seeded roles."""
    UserRole = apps.get_model('users', 'UserRole')
    UserRole.objects.filter(name__in=['admin', 'manager', 'staff']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0004_add_ghl_user_id_to_userprofile'),
    ]

    operations = [
        # Add new permission fields
        migrations.AddField(
            model_name='userrole',
            name='can_access_pos',
            field=models.BooleanField(default=True, help_text='Access POS view'),
        ),
        migrations.AddField(
            model_name='userrole',
            name='can_access_customers',
            field=models.BooleanField(default=True, help_text='Access Customers view'),
        ),
        migrations.AddField(
            model_name='userrole',
            name='can_access_orders',
            field=models.BooleanField(default=False, help_text='Access Order History view'),
        ),
        migrations.AddField(
            model_name='userrole',
            name='can_process_refunds',
            field=models.BooleanField(default=False, help_text='Access Refund Order view'),
        ),
        migrations.AddField(
            model_name='userrole',
            name='can_access_subscriptions',
            field=models.BooleanField(default=False, help_text='Access Subscriptions view'),
        ),
        migrations.AddField(
            model_name='userrole',
            name='can_access_membership_subscriptions',
            field=models.BooleanField(default=False, help_text='Access Membership Subscriptions view'),
        ),
        migrations.AddField(
            model_name='userrole',
            name='can_access_payment_plans',
            field=models.BooleanField(default=False, help_text='Access Payment Plans view'),
        ),
        # Seed the three default roles
        migrations.RunPython(seed_default_roles, reverse_seed),
    ]
