from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0085_seed_email_toggle_settings'),
    ]

    operations = [
        migrations.AddField(
            model_name='savedcart',
            name='order_discount_reason',
            field=models.CharField(blank=True, help_text='Reason/note for the order-level discount', max_length=500, null=True),
        ),
    ]
