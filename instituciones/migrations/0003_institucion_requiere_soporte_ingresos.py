from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('instituciones', '0002_membresiainstitucion')]

    operations = [
        migrations.AddField(
            model_name='institucion',
            name='requiere_soporte_ingresos',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'Si se desmarca, se permite continuar sin soporte. Todo soporte '
                    'aportado conserva sus controles de seguridad y validacion.'
                ),
                verbose_name='Soporte de ingresos obligatorio en financiacion educativa',
            ),
        ),
    ]
