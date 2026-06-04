from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0092_add_address_line_2_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='productvariation',
            name='purchase_price',
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text='Cost/purchase price from ATUM wp_atum_product_data for this variation',
                max_digits=10,
                null=True,
            ),
        ),
    ]
