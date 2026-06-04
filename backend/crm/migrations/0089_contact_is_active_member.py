from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0088_seed_permissions_enabled_setting'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='is_active_member',
            field=models.BooleanField(
                default=False,
                help_text='Whether this customer has an active WooCommerce membership',
            ),
        ),
    ]
