# Generated manually for adding source field to WooCreditLog

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0072_customerpointsaccount_pointstransaction_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='woocreditlog',
            name='source',
            field=models.CharField(default='POS', help_text='Origin of the credit change: POS, MANUAL, WEB', max_length=50),
        ),
        migrations.AddIndex(
            model_name='woocreditlog',
            index=models.Index(fields=['source'], name='woo_credit__source_idx'),
        ),
    ]
