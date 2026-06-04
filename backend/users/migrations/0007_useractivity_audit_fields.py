from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0006_alter_userrole_can_manage_inventory_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='useractivity',
            name='category',
            field=models.CharField(
                choices=[
                    ('user_management', 'User Management'),
                    ('pos_order', 'POS Order'),
                    ('refund', 'Refund'),
                    ('payment', 'Payment'),
                    ('payment_plan', 'Payment Plan'),
                    ('customer', 'Customer'),
                    ('product', 'Product'),
                    ('settings', 'Settings'),
                    ('credit_bank', 'Credit Bank'),
                    ('shipping', 'Shipping'),
                    ('subscription', 'Subscription'),
                    ('membership', 'Membership'),
                    ('inventory', 'Inventory'),
                    ('auth', 'Authentication'),
                    ('sync', 'Sync'),
                    ('other', 'Other'),
                ],
                db_index=True,
                default='other',
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='method',
            field=models.CharField(blank=True, default='', max_length=10),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='endpoint',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='status_code',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='useractivity',
            name='source',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('middleware', 'Middleware')],
                default='manual',
                max_length=15,
            ),
        ),
        migrations.AddIndex(
            model_name='useractivity',
            index=models.Index(fields=['-action_time', 'category'], name='users_usera_action__idx_audit1'),
        ),
        migrations.AddIndex(
            model_name='useractivity',
            index=models.Index(fields=['user', '-action_time'], name='users_usera_user_id_idx_audit2'),
        ),
    ]
