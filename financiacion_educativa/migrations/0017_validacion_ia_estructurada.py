from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('financiacion_educativa', '0016_artefactocontractualeducativo_archivo_firmado_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='validacioniadocumento',
            name='estado',
            field=models.CharField(
                choices=[
                    ('STARTED', 'Iniciada'),
                    ('AUTO_APPROVED', 'Aceptacion automatica concluyente'),
                    ('AUTO_REJECTED', 'Rechazo automatico concluyente'),
                    ('MANUAL_REVIEW', 'Requiere revision manual'),
                    ('ERROR', 'Fallo tecnico'),
                ],
                default='STARTED',
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name='validacioniadocumento',
            name='version_esquema',
            field=models.CharField(default='2', max_length=30),
        ),
        migrations.AddField(
            model_name='validacioniadocumento',
            name='resultado_estructurado',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AlterField(
            model_name='intentoescaneodocumento',
            name='origen',
            field=models.CharField(
                choices=[
                    ('ADMIN', 'Administrador'),
                    ('COMMAND', 'Comando de recuperacion'),
                    ('AUTOMATIC', 'Orquestacion automatica'),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='procesofirmaeducativa',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pendiente de envio'),
                    ('SENDING', 'Enviando'),
                    ('SENT', 'Pendiente de firma'),
                    ('SIGNED', 'Firmado'),
                    ('REFUSED', 'Firma rechazada'),
                    ('FAILED', 'Envio fallido'),
                    ('CANCELLED', 'Cancelado'),
                    ('EXPIRED', 'Vencido'),
                ],
                default='PENDING',
                max_length=30,
            ),
        ),
    ]
