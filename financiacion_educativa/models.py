import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.utils import timezone

from instituciones.models import Institucion

from .choices import (
    EstadoSolicitudFinanciacion,
    EstadoValidacionDocumento,
    MetodoCalculoFinanciero,
    OrigenCapturaDocumento,
    RolParticipante,
    TipoConsentimiento,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
)


hash_sha256_validator = RegexValidator(
    regex=r'^[0-9a-f]{64}$',
    message='La evidencia debe ser un hash SHA-256 hexadecimal.',
)


class ModeloInmutableMixin:
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Este registro es inmutable y no puede modificarse.')
        return super().save(*args, **kwargs)


class SolicitudFinanciacionEducativa(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institucion = models.ForeignKey(
        Institucion,
        on_delete=models.PROTECT,
        related_name='solicitudes_financiacion_educativa',
    )
    referencia_externa = models.CharField(max_length=120)
    nombres = models.CharField(max_length=160)
    apellidos = models.CharField(max_length=160)
    celular = models.CharField(max_length=40)
    correo = models.EmailField()
    direccion = models.CharField(max_length=255)
    valor_plan = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    plazo_meses = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    nombre_curso = models.CharField(max_length=200)
    tipo_curso = models.CharField(max_length=80, blank=True)
    estado = models.CharField(
        max_length=40,
        choices=EstadoSolicitudFinanciacion.choices,
        default=EstadoSolicitudFinanciacion.PENDING_USER_REGISTRATION,
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_financiacion_educativa',
    )
    canal_origen = models.CharField(max_length=40, default='INSTITUTION_API')
    correlation_id = models.CharField(max_length=100, blank=True)
    ip_origen = models.GenericIPAddressField(null=True, blank=True)
    user_agent_origen = models.CharField(max_length=512, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'Solicitud de financiacion educativa'
        verbose_name_plural = 'Solicitudes de financiacion educativa'
        constraints = [
            models.UniqueConstraint(
                fields=['institucion', 'referencia_externa'],
                name='uniq_solicitud_ref_institucion',
            ),
        ]
        indexes = [
            models.Index(fields=['institucion', 'estado'], name='sol_edu_inst_estado_idx'),
            models.Index(fields=['correo'], name='sol_edu_correo_idx'),
            models.Index(fields=['creada_en'], name='sol_edu_creada_idx'),
        ]

    def clean(self):
        super().clean()
        self.referencia_externa = (self.referencia_externa or '').strip()
        self.correlation_id = (self.correlation_id or '').strip()
        if not self.referencia_externa:
            raise ValidationError({'referencia_externa': 'La referencia externa es obligatoria.'})
        if self.institucion_id and not self.institucion.activa:
            raise ValidationError({'institucion': 'La institucion originadora debe estar activa.'})

    def __str__(self):
        return f'{self.referencia_externa} - {self.institucion.nombre_comercial}'


class ParticipanteFinanciacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.CASCADE,
        related_name='participantes',
    )
    nombres = models.CharField(max_length=160)
    apellidos = models.CharField(max_length=160)
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumentoIdentidad.choices)
    numero_documento = models.CharField(max_length=40)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    fecha_nacimiento_confirmada = models.BooleanField(default=False)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='participaciones_financiacion_educativa',
    )
    responsable_contractual = models.BooleanField(default=False)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['solicitud', 'creado_en']
        verbose_name = 'Participante de financiacion'
        verbose_name_plural = 'Participantes de financiacion'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud', 'tipo_documento', 'numero_documento'],
                name='uniq_participante_doc_solicitud',
            ),
        ]
        indexes = [
            models.Index(fields=['tipo_documento', 'numero_documento'], name='part_edu_documento_idx'),
        ]

    def clean(self):
        super().clean()
        self.numero_documento = (self.numero_documento or '').strip().upper()
        if not self.numero_documento:
            raise ValidationError({'numero_documento': 'El documento es obligatorio.'})
        if self.fecha_nacimiento_confirmada and not self.fecha_nacimiento:
            raise ValidationError({
                'fecha_nacimiento': 'La fecha confirmada debe tener un valor.',
            })
        if self.fecha_nacimiento and self.fecha_nacimiento > timezone.localdate():
            raise ValidationError({
                'fecha_nacimiento': 'La fecha de nacimiento no puede ser futura.',
            })
        if self.pk and self.responsable_contractual and not self.roles.filter(
            rol=RolParticipante.PRINCIPAL_DEBTOR
        ).exists():
            raise ValidationError({
                'responsable_contractual': 'El responsable contractual debe tener rol de deudor principal.',
            })

    @property
    def nombre_completo(self):
        return f'{self.nombres} {self.apellidos}'.strip()

    def __str__(self):
        return f'{self.nombre_completo} - {self.numero_documento}'


class RolParticipanteFinanciacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.CASCADE,
        related_name='roles_participantes',
    )
    participante = models.ForeignKey(
        ParticipanteFinanciacion,
        on_delete=models.CASCADE,
        related_name='roles',
    )
    rol = models.CharField(max_length=30, choices=RolParticipante.choices)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['solicitud', 'rol']
        verbose_name = 'Rol de participante'
        verbose_name_plural = 'Roles de participantes'
        constraints = [
            models.UniqueConstraint(
                fields=['participante', 'rol'],
                name='uniq_rol_por_participante',
            ),
            models.UniqueConstraint(
                fields=['solicitud', 'rol'],
                name='uniq_rol_por_solicitud',
            ),
        ]

    def clean(self):
        super().clean()
        if self.participante_id and self.solicitud_id:
            if self.participante.solicitud_id != self.solicitud_id:
                raise ValidationError({
                    'participante': 'El participante no pertenece a la solicitud.',
                })

        roles_existentes = set(
            self.participante.roles.exclude(pk=self.pk).values_list('rol', flat=True)
        ) if self.participante_id and self.participante.pk else set()

        if self.rol == RolParticipante.STUDENT and RolParticipante.GUARDIAN in roles_existentes:
            raise ValidationError({'rol': 'Un tutor no puede ser estudiante en la misma solicitud.'})
        if self.rol == RolParticipante.GUARDIAN and RolParticipante.STUDENT in roles_existentes:
            raise ValidationError({'rol': 'Un estudiante no puede ser su propio tutor.'})
        if self.rol == RolParticipante.PRINCIPAL_DEBTOR and not roles_existentes.intersection({
            RolParticipante.STUDENT,
            RolParticipante.GUARDIAN,
        }):
            raise ValidationError({
                'rol': 'El deudor principal debe ser el estudiante adulto o su tutor.',
            })

    def __str__(self):
        return f'{self.participante.nombre_completo} - {self.get_rol_display()}'


class Consentimiento(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='consentimientos',
    )
    participante = models.ForeignKey(
        ParticipanteFinanciacion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='consentimientos',
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='consentimientos_financiacion_educativa',
    )
    tipo = models.CharField(max_length=40, choices=TipoConsentimiento.choices)
    version_texto = models.CharField(max_length=40)
    aceptado_en = models.DateTimeField(default=timezone.now, editable=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    evidencia_hash = models.CharField(max_length=64, validators=[hash_sha256_validator])

    class Meta:
        ordering = ['-aceptado_en']
        verbose_name = 'Consentimiento'
        verbose_name_plural = 'Consentimientos'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(participante__isnull=False) | models.Q(usuario__isnull=False),
                name='consentimiento_aceptante_requerido',
            ),
            models.UniqueConstraint(
                fields=['solicitud', 'participante', 'tipo', 'version_texto'],
                name='uniq_consent_part_tipo_version',
            ),
        ]

    def clean(self):
        super().clean()
        if self.participante_id and self.participante.solicitud_id != self.solicitud_id:
            raise ValidationError({
                'participante': 'El participante no pertenece a la solicitud.',
            })
        if not self.participante_id and not self.usuario_id:
            raise ValidationError('El consentimiento debe tener un aceptante.')

    def __str__(self):
        return f'{self.get_tipo_display()} v{self.version_texto}'


class DocumentoFinanciacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.CASCADE,
        related_name='documentos',
    )
    participante = models.ForeignKey(
        ParticipanteFinanciacion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='documentos',
    )
    tipo = models.CharField(max_length=50, choices=TipoDocumentoFinanciacion.choices)
    archivo = models.FileField(
        upload_to='financiacion_educativa/documentos/%Y/%m/',
        null=True,
        blank=True,
    )
    referencia_almacenamiento = models.CharField(max_length=500, blank=True)
    nombre_original = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    tamano_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    estado_validacion = models.CharField(
        max_length=20,
        choices=EstadoValidacionDocumento.choices,
        default=EstadoValidacionDocumento.PENDING,
    )
    origen_captura = models.CharField(
        max_length=30,
        choices=OrigenCapturaDocumento.choices,
    )
    sha256 = models.CharField(
        max_length=64,
        blank=True,
        validators=[hash_sha256_validator],
    )
    resultado_procesamiento = models.JSONField(default=dict, blank=True)
    nivel_confianza = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    cargado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-cargado_en']
        verbose_name = 'Documento de financiacion'
        verbose_name_plural = 'Documentos de financiacion'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud', 'sha256'],
                condition=~models.Q(sha256=''),
                name='uniq_documento_hash_solicitud',
            ),
        ]
        indexes = [
            models.Index(fields=['solicitud', 'tipo'], name='doc_edu_sol_tipo_idx'),
            models.Index(fields=['estado_validacion'], name='doc_edu_estado_idx'),
        ]

    def clean(self):
        super().clean()
        if self.participante_id and self.participante.solicitud_id != self.solicitud_id:
            raise ValidationError({
                'participante': 'El participante no pertenece a la solicitud.',
            })
        tiene_archivo = bool(self.archivo)
        tiene_referencia = bool((self.referencia_almacenamiento or '').strip())
        if tiene_archivo == tiene_referencia:
            raise ValidationError(
                'Debe registrar un archivo o una referencia de almacenamiento, no ambos.'
            )

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.solicitud.referencia_externa}'


class CondicionesFinancieras(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.OneToOneField(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='condiciones_financieras',
    )
    valor_financiado = models.DecimalField(max_digits=14, decimal_places=2)
    plazo_meses = models.PositiveSmallIntegerField()
    tasa_interes_mensual = models.DecimalField(max_digits=7, decimal_places=4)
    tasa_comision = models.DecimalField(max_digits=7, decimal_places=4)
    valor_comision = models.DecimalField(max_digits=14, decimal_places=2)
    tasa_iva_comision = models.DecimalField(max_digits=7, decimal_places=4)
    valor_iva_comision = models.DecimalField(max_digits=14, decimal_places=2)
    capital_financiado = models.DecimalField(max_digits=14, decimal_places=2)
    valor_cuota_estimada = models.DecimalField(max_digits=14, decimal_places=2)
    interes_total_estimado = models.DecimalField(max_digits=14, decimal_places=2)
    total_estimado = models.DecimalField(max_digits=14, decimal_places=2)
    metodo_calculo = models.CharField(
        max_length=40,
        choices=MetodoCalculoFinanciero.choices,
    )
    base_calculo = models.JSONField(default=dict)
    version_regla = models.CharField(max_length=60)
    moneda = models.CharField(max_length=3, default='COP')
    fecha_calculo = models.DateTimeField(default=timezone.now, editable=False)
    fecha_primer_vencimiento = models.DateField(null=True, blank=True)
    fecha_ultimo_vencimiento = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = 'Condiciones financieras'
        verbose_name_plural = 'Condiciones financieras'

    def __str__(self):
        return f'Condiciones {self.solicitud.referencia_externa} - {self.version_regla}'


class HistorialEstadoSolicitud(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.CASCADE,
        related_name='historial_estados',
    )
    estado_anterior = models.CharField(
        max_length=40,
        choices=EstadoSolicitudFinanciacion.choices,
        null=True,
        blank=True,
    )
    estado_nuevo = models.CharField(
        max_length=40,
        choices=EstadoSolicitudFinanciacion.choices,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cambios_estado_financiacion_educativa',
    )
    motivo = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['creado_en', 'id']
        verbose_name = 'Historial de estado de solicitud'
        verbose_name_plural = 'Historiales de estados de solicitudes'
        indexes = [
            models.Index(fields=['solicitud', 'creado_en'], name='hist_edu_sol_fecha_idx'),
        ]

    def __str__(self):
        return f'{self.solicitud.referencia_externa}: {self.estado_anterior or "INICIAL"} -> {self.estado_nuevo}'
