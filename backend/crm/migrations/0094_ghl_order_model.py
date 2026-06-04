import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0093_productvariation_purchase_price'),
    ]

    operations = [
        migrations.CreateModel(
            name='GHLOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ghl_order_id', models.CharField(db_index=True, max_length=50, unique=True)),
                ('alt_id', models.CharField(help_text='GHL location ID', max_length=50)),
                ('ghl_contact_id', models.CharField(db_index=True, help_text='GHL contact ID', max_length=50)),
                ('contact_name', models.CharField(blank=True, default='', max_length=255)),
                ('contact_email', models.EmailField(blank=True, default='', max_length=254)),
                ('currency', models.CharField(default='USD', max_length=10)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=10)),
                ('subtotal', models.DecimalField(decimal_places=2, max_digits=10)),
                ('discount', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('status', models.CharField(max_length=30)),
                ('payment_status', models.CharField(blank=True, default='', max_length=30)),
                ('fulfillment_status', models.CharField(default='unfulfilled', max_length=30)),
                ('live_mode', models.BooleanField(default=True)),
                ('total_products', models.IntegerField(default=0)),
                ('onetime_products', models.IntegerField(default=0)),
                ('source_type', models.CharField(blank=True, default='', max_length=50)),
                ('source_name', models.CharField(blank=True, default='', max_length=255)),
                ('source_id', models.CharField(blank=True, default='', max_length=100)),
                ('source_meta', models.JSONField(blank=True, default=dict)),
                ('ghl_created_at', models.DateTimeField(db_index=True)),
                ('ghl_updated_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('contact', models.ForeignKey(
                    blank=True,
                    help_text='Resolved local contact (matched by email or ghl_contact_id)',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ghl_orders',
                    to='crm.contact',
                )),
            ],
            options={
                'verbose_name': 'GHL Order',
                'verbose_name_plural': 'GHL Orders',
                'db_table': 'crm_ghl_order',
                'ordering': ['-ghl_created_at'],
                'indexes': [
                    models.Index(fields=['contact_email'], name='crm_ghlorder_email_idx'),
                    models.Index(fields=['status'], name='crm_ghlorder_status_idx'),
                    models.Index(fields=['ghl_created_at'], name='crm_ghlorder_created_idx'),
                ],
            },
        ),
    ]
