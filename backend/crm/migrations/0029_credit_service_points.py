# Generated migration for credit_service_points table

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0028_contact_billing_country_contact_shipping_address_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CreditServicePoints',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('contact_ghl_id', models.CharField(blank=True, max_length=100, null=True, help_text='From CRM contact')),
                ('contact_woo_id', models.IntegerField(blank=True, null=True, help_text='WooCommerce contact ID')),
                ('points', models.IntegerField(default=0, help_text='Total points for that product')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='credit_points', to='crm.contact', help_text='FK → crm_contact')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='credit_points', to='crm.product', help_text='FK → crm_products')),
            ],
            options={
                'verbose_name': 'Credit Service Points',
                'verbose_name_plural': 'Credit Service Points',
                'db_table': 'credit_service_points',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='creditservicepoints',
            constraint=models.UniqueConstraint(fields=('customer', 'product'), name='unique_customer_product_points'),
        ),
    ]
