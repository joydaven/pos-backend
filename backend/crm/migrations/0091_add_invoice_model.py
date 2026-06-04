import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('crm', '0090_product_purchase_price'),
    ]

    operations = [
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('invoice_ref', models.CharField(db_index=True, help_text='Human-readable invoice reference (e.g. INV-1234567890)', max_length=50)),
                ('email', models.EmailField(help_text='Email address the invoice was sent to', max_length=254)),
                ('items', models.JSONField(default=list, help_text='Line items: [{name, quantity, price, sku, brand}]')),
                ('subtotal', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('total', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('paid', 'Paid'), ('expired', 'Expired'), ('cancelled', 'Cancelled'), ('locked', 'Locked')], default='pending', max_length=10)),
                ('payment_token', models.CharField(blank=True, help_text='Signed token for the Pay Now link', max_length=512, unique=True)),
                ('token_expires_at', models.DateTimeField(help_text='Absolute expiry of the payment token')),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('transaction_id', models.CharField(blank=True, help_text='Authorize.net transaction ID', max_length=100)),
                ('payment_method_used', models.CharField(blank=True, help_text="e.g. 'saved_card_visa_1234' or 'new_card'", max_length=50)),
                ('payer_ip', models.GenericIPAddressField(blank=True, help_text='IP of the person who paid', null=True)),
                ('woo_order_id', models.IntegerField(blank=True, help_text='WooCommerce order ID created on payment', null=True)),
                ('failed_attempts', models.IntegerField(default=0, help_text='Consecutive failed payment attempts')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('contact', models.ForeignKey(blank=True, help_text='Patient / customer this invoice belongs to', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invoices', to='crm.contact')),
                ('created_by', models.ForeignKey(blank=True, help_text='Staff member who created/sent this invoice', null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Invoice',
                'verbose_name_plural': 'Invoices',
                'db_table': 'crm_invoice',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['status'], name='crm_invoice_status_idx'),
                    models.Index(fields=['payment_token'], name='crm_invoice_token_idx'),
                    models.Index(fields=['contact'], name='crm_invoice_contact_idx'),
                    models.Index(fields=['created_at'], name='crm_invoice_created_idx'),
                ],
            },
        ),
    ]
