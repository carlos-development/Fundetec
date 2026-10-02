from django.db import migrations, models


REASON_CHOICES = [
    ('SECURITY_SCAN_COMPLETED', 'Escaneo completado'),
    ('MALWARE_DETECTED', 'Amenaza detectada'),
    ('SECURITY_SCAN_TEMPORARY_ERROR', 'Fallo temporal de escaneo'),
    ('DOCUMENT_VALIDATION_COMPLETED', 'Validacion documental completada'),
    ('DOCUMENT_AI_TEMPORARY_ERROR', 'Fallo temporal de IA'),
    ('DOCUMENT_CONTENT_TEMPORARY_ERROR', 'Fallo temporal procesando contenido'),
    ('DOCUMENT_CONTENT_PERMANENT_ERROR', 'Fallo permanente procesando contenido'),
    ('DOCUMENT_CORRECTION_REQUIRED', 'Correccion documental requerida'),
    ('PDF_CONTENT_PROCESSING_REQUIRED', 'Procesamiento de contenido PDF requerido'),
    ('DOCUMENT_VALIDATION_INCONCLUSIVE', 'Validacion documental inconclusa'),
    ('DOCUMENT_RESULT_NOT_CONCLUSIVE', 'Resultado documental no concluyente'),
    ('AUTOMATIC_DECISION_CONTINUE', 'Decision automatica permite continuar'),
    ('FINANCIAL_SNAPSHOT_LOCKED', 'Fotografia financiera bloqueada'),
    ('CONTRACTS_GENERATED', 'Contratos generados'),
    ('CURRENT_TERMS_REQUIRED', 'Aceptacion de terminos vigentes requerida'),
    ('LEGAL_TERMS_NOT_READY', 'Textos juridicos vigentes no disponibles'),
    ('SANDBOX_APPLICATION_CANCELLED', 'Solicitud Sandbox cancelada operativamente'),
    ('PENDING_SIGNATURE', 'Pendiente de firma'),
    ('SIGNATURE_SEND_AMBIGUOUS', 'Envio a firma ambiguo'),
    ('SIGNATURE_SEND_RETRY_REQUIRED', 'Reintento de firma requerido'),
    ('SIGNED_WEBHOOK_CONFIRMED', 'Webhook firmado confirmado'),
    ('SIGNATURE_REFUSED', 'Firma rechazada'),
    ('SIGNATURE_CANCELLED', 'Firma cancelada'),
    ('SIGNATURE_EXPIRED', 'Firma vencida'),
    ('LEASE_EXPIRED', 'Lease vencido'),
    ('MAX_ATTEMPTS_EXCEEDED', 'Intentos agotados'),
    ('SCANNER_TIMEOUT', 'Timeout del escaner'),
    ('SCANNER_UNAVAILABLE', 'Escaner no disponible'),
    ('PROVIDER_TIMEOUT', 'Timeout del proveedor'),
    ('PROVIDER_ERROR', 'Error temporal del proveedor'),
    ('SIGNED_FILE_RECOVERY_FAILED', 'Fallo recuperando archivo firmado'),
    ('SIGNATURE_SEND_NOT_CONFIRMED', 'Envio de firma no confirmado'),
    ('INTERNAL_ERROR', 'Error interno controlado'),
]


class Migration(migrations.Migration):

    dependencies = [
        ('financiacion_educativa', '0027_resultado_publico_sandbox'),
    ]

    operations = [
        migrations.AlterField(
            model_name='etapaprocesoautomatizacioneducativa',
            name='codigo_razon',
            field=models.CharField(
                blank=True,
                choices=REASON_CHOICES,
                max_length=60,
            ),
        ),
        migrations.AlterField(
            model_name='procesoautomatizacioneducativa',
            name='codigo_razon',
            field=models.CharField(
                blank=True,
                choices=REASON_CHOICES,
                max_length=60,
            ),
        ),
    ]
