import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0083_add_performance_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('email_type', models.CharField(choices=[
                    ('order_receipt', 'Order Receipt'),
                    ('refund_receipt', 'Refund Receipt'),
                    ('auto_refund_receipt', 'Auto Refund Receipt'),
                    ('cancellation', 'Cancellation Email'),
                    ('tracking', 'Tracking / Shipped Email'),
                    ('partial_tracking', 'Partial Shipped Email'),
                    ('installment_receipt', 'Installment Receipt'),
                    ('plan_summary', 'Payment Plan Summary'),
                    ('membership_onboarding', 'Membership Onboarding'),
                    ('membership_cancellation', 'Membership Cancellation'),
                    ('other', 'Other'),
                ], max_length=30)),
                ('recipient', models.EmailField(help_text='Recipient email address', max_length=254)),
                ('subject', models.CharField(blank=True, max_length=500)),
                ('status', models.CharField(choices=[
                    ('sent', 'Sent'),
                    ('failed', 'Failed'),
                    ('skipped', 'Skipped (Toggle Off)'),
                ], default='sent', max_length=10)),
                ('delivery_backend', models.CharField(choices=[
                    ('mailgun', 'Mailgun'),
                    ('smtp', 'SMTP'),
                    ('none', 'None'),
                ], default='none', max_length=10)),
                ('error_message', models.TextField(blank=True, help_text='Error details if delivery failed')),
                ('related_order', models.CharField(blank=True, help_text='Order number, subscription ID, or plan number', max_length=100)),
                ('metadata', models.JSONField(blank=True, default=dict, help_text='Extra context (order total, items, etc.)')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Email Log',
                'verbose_name_plural': 'Email Logs',
                'db_table': 'crm_email_log',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='emaillog',
            index=models.Index(fields=['email_type', 'status'], name='crm_email_l_email_t_idx'),
        ),
        migrations.AddIndex(
            model_name='emaillog',
            index=models.Index(fields=['recipient'], name='crm_email_l_recipie_idx'),
        ),
        migrations.AddIndex(
            model_name='emaillog',
            index=models.Index(fields=['related_order'], name='crm_email_l_related_idx'),
        ),
        migrations.AddIndex(
            model_name='emaillog',
            index=models.Index(fields=['created_at'], name='crm_email_l_created_idx'),
        ),
    ]
