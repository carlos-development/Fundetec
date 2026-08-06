import uuid
import hashlib
import re
from decimal import Decimal
from datetime import date

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
    CanalEntregaInvitacion,
    EstadoSolicitudFinanciacion,
    EstadoEscaneoDocumento,
    EstadoEntregaInvitacion,
    EstadoEntregaCapturaMovil,
    EstadoEntregaCorreoSolicitud,
    EstadoEnlaceCapturaMovil,
    EstadoEvidenciaMatricula,
    EstadoValidacionDocumento,
    EstadoConfiguracionFinanciera,
    EstadoInvitacionContinuacion,
    EstadoIntentoEscaneoDocumento,
    EstadoValidacionIADocumento,
    EstadoVersionTerminos,
    EstadoArtefactoContractualEducativo,
    EstadoProcesoFirmaEducativa,
    EstadoEventoWebhookFirmaEducativa,
    MetodoCalculoFinanciero,
    MotivoRechazoDocumento,
    OrigenEntregaInvitacion,
    OrigenIntentoEscaneoDocumento,
    OrigenValidacionIADocumento,
    OrigenCapturaDocumento,
    PoliticaCausacionInteres,
    PoliticaRedondeoFinanciero,
    PropositoInvitacionContinuacion,
    RelacionEstudiante,
    RolParticipante,
    TipoEventoParticipante,
    TipoEventoInvitacion,
    TipoEventoEnlaceCapturaMovil,
    TipoEventoSeguridadFinanciacion,
    TipoDecisionRevisionEducativa,
    MotivoDecisionRevisionEducativa,
    RequisitoCorreccionEducativa,
    TipoConsentimiento,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
    TipoArtefactoContractualEducativo,
    TIPOS_DOCUMENTO_IDENTIDAD_CAMARA,
)
from .storage import private_document_storage


hash_sha256_validator = RegexValidator(
    regex=r'^[0-9a-f]{64}$',
    message='La evidencia debe ser un hash SHA-256 hexadecimal.',
)

EXTENSION_DOCUMENTO_POR_MIME = {
    'application/pdf': '.pdf',
    'image/jpeg': '.jpg',
    'image/png': '.png',
}


def ruta_documento_privado(instance, _filename):
    nombre = instance.nombre_seguro or f'{uuid.uuid4().hex}{EXTENSION_DOCUMENTO_POR_MIME.get(instance.content_type, "")}'
    instance.nombre_seguro = nombre
    return f'documentos/{nombre[:2]}/{nombre}'


def ruta_artefacto_contractual_privado(instance, _filename):
    nombre = f'{uuid.uuid4().hex}.pdf'
    return f'contratos/{instance.solicitud_id}/{instance.tipo.lower()}/{nombre}'


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
    tipo_documento_estudiante = models.CharField(
        max_length=20,
        choices=TipoDocumentoIdentidad.choices,
        blank=True,
    )
    numero_documento_estudiante = models.CharField(max_length=40, blank=True)
    fecha_nacimiento_estudiante = models.DateField(null=True, blank=True)
    codigo_matricula = models.CharField(max_length=120, blank=True)
    periodo_academico = models.CharField(max_length=80, blank=True)
    sede = models.CharField(max_length=160, blank=True)
    jornada = models.CharField(max_length=80, blank=True)
    fecha_matricula = models.DateField(null=True, blank=True, editable=False)
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
        permissions = [
            (
                'revisar_solicitud_financiacion',
                'Puede revisar y decidir solicitudes educativas',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['institucion', 'referencia_externa'],
                name='uniq_solicitud_ref_institucion',
            ),
        ]
        indexes = [
            models.Index(fields=['institucion', 'estado'], name='sol_edu_inst_estado_idx'),
            models.Index(fields=['correo'], name='sol_edu_correo_idx'),
            models.Index(
                fields=['tipo_documento_estudiante', 'numero_documento_estudiante'],
                name='sol_edu_doc_est_idx',
            ),
            models.Index(fields=['creada_en'], name='sol_edu_creada_idx'),
        ]

    def clean(self):
        super().clean()
        self.referencia_externa = (self.referencia_externa or '').strip()
        self.correlation_id = (self.correlation_id or '').strip()
        self.tipo_documento_estudiante = (
            self.tipo_documento_estudiante or ''
        ).strip().upper()
        self.numero_documento_estudiante = re.sub(
            r'[^A-Z0-9]',
            '',
            (self.numero_documento_estudiante or '').strip().upper(),
        )
        self.codigo_matricula = (self.codigo_matricula or '').strip()
        self.periodo_academico = (self.periodo_academico or '').strip()
        self.sede = re.sub(r'\s+', ' ', (self.sede or '').strip())
        self.jornada = re.sub(r'\s+', ' ', (self.jornada or '').strip())
        if not self.referencia_externa:
            raise ValidationError({'referencia_externa': 'La referencia externa es obligatoria.'})
        datos_identidad = (
            self.tipo_documento_estudiante,
            self.numero_documento_estudiante,
            self.fecha_nacimiento_estudiante,
        )
        if any(datos_identidad) and not all(datos_identidad):
            raise ValidationError({
                'tipo_documento_estudiante': (
                    'Tipo, numero y fecha de nacimiento deben enviarse juntos.'
                ),
            })
        if (
            self.fecha_nacimiento_estudiante
            and self.fecha_nacimiento_estudiante > timezone.localdate()
        ):
            raise ValidationError({
                'fecha_nacimiento_estudiante': (
                    'La fecha de nacimiento no puede ser futura.'
                ),
            })
        if self.institucion_id and not self.institucion.activa:
            raise ValidationError({'institucion': 'La institucion originadora debe estar activa.'})

    @property
    def identidad_estudiante_completa(self):
        return bool(
            self.tipo_documento_estudiante
            and self.numero_documento_estudiante
            and self.fecha_nacimiento_estudiante
        )

    @property
    def identificacion_estudiante_enmascarada(self):
        if not self.numero_documento_estudiante:
            return ''
        visible = self.numero_documento_estudiante[-4:]
        return f'{"*" * max(4, len(self.numero_documento_estudiante) - 4)}{visible}'

    def __str__(self):
        return f'{self.referencia_externa} - {self.institucion.nombre_comercial}'


class RegistroIdempotenciaSolicitud(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institucion = models.ForeignKey(
        Institucion,
        on_delete=models.PROTECT,
        related_name='registros_idempotencia_financiacion',
    )
    clave_hash = models.CharField(max_length=64, validators=[hash_sha256_validator])
    payload_hash = models.CharField(max_length=64, validators=[hash_sha256_validator])
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='registros_idempotencia',
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    ultimo_reuso_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'Registro de idempotencia de solicitud'
        verbose_name_plural = 'Registros de idempotencia de solicitudes'
        constraints = [
            models.UniqueConstraint(
                fields=['institucion', 'clave_hash'],
                name='uniq_idempotencia_clave_institucion',
            ),
        ]
        indexes = [
            models.Index(
                fields=['institucion', 'creada_en'],
                name='idem_edu_inst_fecha_idx',
            ),
        ]

    def __str__(self):
        return f'{self.institucion_id} - {self.solicitud_id}'


class InvitacionContinuacionSolicitud(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='invitaciones_continuacion',
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        validators=[hash_sha256_validator],
    )
    proposito = models.CharField(
        max_length=40,
        choices=PropositoInvitacionContinuacion.choices,
        default=PropositoInvitacionContinuacion.CONTINUE_APPLICATION,
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoInvitacionContinuacion.choices,
        default=EstadoInvitacionContinuacion.ACTIVE,
    )
    vence_en = models.DateTimeField()
    consumida_en = models.DateTimeField(null=True, blank=True)
    consumida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='invitaciones_educativas_consumidas',
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'Invitacion de continuacion'
        verbose_name_plural = 'Invitaciones de continuacion'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud'],
                condition=models.Q(estado=EstadoInvitacionContinuacion.ACTIVE),
                name='uniq_invitacion_activa_solicitud',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        estado=EstadoInvitacionContinuacion.CONSUMED,
                        consumida_en__isnull=False,
                        consumida_por__isnull=False,
                    )
                    | ~models.Q(estado=EstadoInvitacionContinuacion.CONSUMED)
                ),
                name='invitacion_consumida_con_auditoria',
            ),
        ]
        indexes = [
            models.Index(fields=['solicitud', 'estado'], name='inv_edu_sol_estado_idx'),
            models.Index(fields=['vence_en'], name='inv_edu_vence_idx'),
        ]

    @property
    def esta_vigente(self):
        return (
            self.estado == EstadoInvitacionContinuacion.ACTIVE
            and self.vence_en > timezone.now()
        )

    def __str__(self):
        return f'Invitacion {self.id} - {self.get_estado_display()}'


class EventoInvitacionContinuacion(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invitacion = models.ForeignKey(
        InvitacionContinuacionSolicitud,
        on_delete=models.PROTECT,
        related_name='eventos',
    )
    tipo = models.CharField(max_length=20, choices=TipoEventoInvitacion.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_invitacion_educativa',
    )
    metadata = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['creado_en', 'id']
        verbose_name = 'Evento de invitacion'
        verbose_name_plural = 'Eventos de invitaciones'
        indexes = [
            models.Index(fields=['invitacion', 'creado_en'], name='evt_inv_edu_fecha_idx'),
        ]

    def __str__(self):
        return f'{self.invitacion_id} - {self.get_tipo_display()}'


class EntregaInvitacionContinuacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='entregas_invitacion',
    )
    invitacion = models.OneToOneField(
        InvitacionContinuacionSolicitud,
        on_delete=models.PROTECT,
        related_name='entrega',
    )
    secuencia = models.PositiveIntegerField()
    canal = models.CharField(
        max_length=20,
        choices=CanalEntregaInvitacion.choices,
        default=CanalEntregaInvitacion.EMAIL,
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoEntregaInvitacion.choices,
        default=EstadoEntregaInvitacion.PENDING,
    )
    origen = models.CharField(
        max_length=30,
        choices=OrigenEntregaInvitacion.choices,
    )
    destinatario_hmac = models.CharField(
        max_length=64,
        validators=[hash_sha256_validator],
        editable=False,
    )
    reemplaza_a = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reemplazos',
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    codigo_ultimo_error = models.CharField(max_length=60, blank=True)
    programada_en = models.DateTimeField(default=timezone.now, editable=False)
    iniciada_en = models.DateTimeField(null=True, blank=True, editable=False)
    enviada_en = models.DateTimeField(null=True, blank=True, editable=False)
    fallida_en = models.DateTimeField(null=True, blank=True, editable=False)
    cancelada_en = models.DateTimeField(null=True, blank=True, editable=False)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='entregas_invitacion_educativa_creadas',
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['solicitud', '-secuencia']
        verbose_name = 'Entrega de invitacion de continuacion'
        verbose_name_plural = 'Entregas de invitaciones de continuacion'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud', 'secuencia'],
                name='uniq_entrega_inv_edu_secuencia',
            ),
            models.UniqueConstraint(
                fields=['solicitud'],
                condition=models.Q(origen=OrigenEntregaInvitacion.INITIAL),
                name='uniq_entrega_inv_edu_inicial',
            ),
            models.UniqueConstraint(
                fields=['solicitud'],
                condition=models.Q(
                    estado__in=[
                        EstadoEntregaInvitacion.PENDING,
                        EstadoEntregaInvitacion.SENDING,
                    ]
                ),
                name='uniq_entrega_inv_edu_en_curso',
            ),
        ]
        indexes = [
            models.Index(
                fields=['estado', 'programada_en'],
                name='ent_inv_edu_estado_idx',
            ),
        ]

    def clean(self):
        super().clean()
        if self.invitacion_id and self.solicitud_id:
            if self.invitacion.solicitud_id != self.solicitud_id:
                raise ValidationError({
                    'invitacion': 'La invitacion no pertenece a la solicitud.',
                })
        if self.reemplaza_a_id:
            if self.reemplaza_a.solicitud_id != self.solicitud_id:
                raise ValidationError({
                    'reemplaza_a': 'La entrega anterior no pertenece a la solicitud.',
                })
            if self.reemplaza_a_id == self.pk:
                raise ValidationError({
                    'reemplaza_a': 'Una entrega no puede reemplazarse a si misma.',
                })

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Las entregas de invitaciones deben conservarse para auditoria.'
        )

    def __str__(self):
        return f'Entrega {self.solicitud_id} #{self.secuencia}'


class EnlaceCapturaMovil(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='enlaces_captura_movil',
    )
    persona = models.CharField(
        max_length=20,
        choices=(
            (RolParticipante.STUDENT, 'Estudiante'),
            (RolParticipante.GUARDIAN, 'Tutor'),
        ),
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        validators=[hash_sha256_validator],
        editable=False,
    )
    destinatario_hmac = models.CharField(
        max_length=64,
        validators=[hash_sha256_validator],
        editable=False,
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoEnlaceCapturaMovil.choices,
        default=EstadoEnlaceCapturaMovil.ACTIVE,
    )
    estado_entrega = models.CharField(
        max_length=20,
        choices=EstadoEntregaCapturaMovil.choices,
        default=EstadoEntregaCapturaMovil.PENDING,
    )
    vence_en = models.DateTimeField()
    intentos_entrega = models.PositiveSmallIntegerField(default=0)
    codigo_ultimo_error = models.CharField(max_length=60, blank=True)
    entrega_iniciada_en = models.DateTimeField(null=True, blank=True)
    enviada_en = models.DateTimeField(null=True, blank=True)
    fallida_en = models.DateTimeField(null=True, blank=True)
    revocada_en = models.DateTimeField(null=True, blank=True)
    consumida_en = models.DateTimeField(null=True, blank=True)
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='enlaces_captura_movil_creados',
    )
    consumida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='enlaces_captura_movil_consumidos',
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'Enlace de captura movil'
        verbose_name_plural = 'Enlaces de captura movil'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud'],
                condition=models.Q(estado=EstadoEnlaceCapturaMovil.ACTIVE),
                name='uniq_enlace_captura_activo_sol',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        estado=EstadoEnlaceCapturaMovil.CONSUMED,
                        consumida_en__isnull=False,
                        consumida_por__isnull=False,
                    )
                    | ~models.Q(estado=EstadoEnlaceCapturaMovil.CONSUMED)
                ),
                name='enlace_captura_consumido_audit',
            ),
        ]
        indexes = [
            models.Index(
                fields=['solicitud', 'estado'],
                name='enl_cap_sol_estado_idx',
            ),
            models.Index(fields=['vence_en'], name='enl_cap_vence_idx'),
        ]

    @property
    def esta_vigente(self):
        return (
            self.estado == EstadoEnlaceCapturaMovil.ACTIVE
            and self.vence_en > timezone.now()
        )

    def __str__(self):
        return f'Enlace movil {self.id} - {self.get_estado_display()}'


class EventoEnlaceCapturaMovil(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enlace = models.ForeignKey(
        EnlaceCapturaMovil,
        on_delete=models.PROTECT,
        related_name='eventos',
    )
    tipo = models.CharField(
        max_length=30,
        choices=TipoEventoEnlaceCapturaMovil.choices,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_enlace_captura_movil',
    )
    metadata = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['creado_en', 'id']
        verbose_name = 'Evento de enlace de captura movil'
        verbose_name_plural = 'Eventos de enlaces de captura movil'
        indexes = [
            models.Index(
                fields=['enlace', 'creado_en'],
                name='evt_enl_cap_fecha_idx',
            ),
        ]

    def __str__(self):
        return f'{self.enlace_id} - {self.get_tipo_display()}'


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
    pais_expedicion = models.CharField(max_length=2, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)
    fecha_nacimiento_confirmada = models.BooleanField(default=False)
    correo = models.EmailField(blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    relacion_estudiante = models.CharField(
        max_length=30,
        choices=RelacionEstudiante.choices,
        blank=True,
    )
    relacion_verificada = models.BooleanField(default=False, editable=False)
    identidad_verificada = models.BooleanField(default=False, editable=False)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='participaciones_financiacion_educativa',
    )
    responsable_contractual = models.BooleanField(default=False)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='participantes_financiacion_creados',
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='participantes_financiacion_actualizados',
    )
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
        self.nombres = re.sub(r'\s+', ' ', (self.nombres or '').strip())
        self.apellidos = re.sub(r'\s+', ' ', (self.apellidos or '').strip())
        self.numero_documento = re.sub(
            r'[^A-Z0-9]',
            '',
            (self.numero_documento or '').strip().upper(),
        )
        self.pais_expedicion = (self.pais_expedicion or '').strip().upper()
        self.correo = (self.correo or '').strip().lower()
        self.telefono = re.sub(r'\s+', '', (self.telefono or '').strip())
        if not self.numero_documento:
            raise ValidationError({'numero_documento': 'El documento es obligatorio.'})
        if self.pais_expedicion and not re.fullmatch(r'[A-Z]{2}', self.pais_expedicion):
            raise ValidationError({
                'pais_expedicion': 'Usa un codigo de pais de dos letras.',
            })
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

    @property
    def identificacion_enmascarada(self):
        if not self.numero_documento:
            return ''
        visible = self.numero_documento[-4:]
        return f'{"*" * max(4, len(self.numero_documento) - 4)}{visible}'

    def __str__(self):
        return f'{self.nombre_completo} - {self.identificacion_enmascarada}'


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
    declarado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='roles_financiacion_declarados',
    )
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
    def __str__(self):
        return f'{self.participante.nombre_completo} - {self.get_rol_display()}'


class EventoParticipanteFinanciacion(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    participante = models.ForeignKey(
        ParticipanteFinanciacion,
        on_delete=models.PROTECT,
        related_name='eventos',
    )
    tipo = models.CharField(max_length=20, choices=TipoEventoParticipante.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_participantes_financiacion',
    )
    campos_modificados = models.JSONField(default=list, blank=True)
    creado_en = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['creado_en', 'id']
        verbose_name = 'Evento de participante'
        verbose_name_plural = 'Eventos de participantes'


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
                condition=models.Q(participante__isnull=False),
                name='uniq_consent_part_tipo_version',
            ),
            models.UniqueConstraint(
                fields=['solicitud', 'usuario', 'tipo', 'version_texto'],
                condition=models.Q(usuario__isnull=False),
                name='uniq_consent_user_tipo_version',
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


class VersionTerminosFinanciacion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tipo = models.CharField(max_length=40, choices=TipoConsentimiento.choices)
    version = models.CharField(max_length=40, unique=True)
    titulo = models.CharField(max_length=200)
    contenido = models.TextField()
    hash_integridad = models.CharField(
        max_length=64,
        editable=False,
        validators=[hash_sha256_validator],
    )
    obligatorio = models.BooleanField(default=True)
    estado = models.CharField(
        max_length=20,
        choices=EstadoVersionTerminos.choices,
        default=EstadoVersionTerminos.DRAFT,
    )
    publicada_en = models.DateTimeField(null=True, blank=True)
    vigente_desde = models.DateTimeField(null=True, blank=True)
    retirada_en = models.DateTimeField(null=True, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tipo', '-vigente_desde', '-creada_en']
        verbose_name = 'Version de terminos de financiacion'
        verbose_name_plural = 'Versiones de terminos de financiacion'
        indexes = [
            models.Index(fields=['estado', 'vigente_desde'], name='term_edu_estado_vig_idx'),
            models.Index(fields=['tipo', 'obligatorio'], name='term_edu_tipo_obl_idx'),
        ]

    @staticmethod
    def calcular_hash(contenido):
        return hashlib.sha256(contenido.encode('utf-8')).hexdigest()

    @property
    def esta_vigente(self):
        ahora = timezone.now()
        return (
            self.estado == EstadoVersionTerminos.PUBLISHED
            and self.publicada_en is not None
            and self.vigente_desde is not None
            and self.vigente_desde <= ahora
            and self.retirada_en is None
        )

    def clean(self):
        super().clean()
        if not (self.contenido or '').strip():
            raise ValidationError({'contenido': 'El contenido es obligatorio.'})
        if self.estado == EstadoVersionTerminos.PUBLISHED:
            if not self.publicada_en or not self.vigente_desde:
                raise ValidationError(
                    'Una version publicada requiere fechas de publicacion y vigencia.'
                )
        if self.retirada_en and self.estado != EstadoVersionTerminos.RETIRED:
            raise ValidationError({
                'retirada_en': 'La fecha de retiro requiere estado retirado.',
            })

    def save(self, *args, **kwargs):
        if not self._state.adding:
            anterior = type(self).objects.filter(pk=self.pk).only('estado').first()
            if anterior and anterior.estado != EstadoVersionTerminos.DRAFT:
                raise ValidationError(
                    'Una version publicada o retirada no puede modificarse.'
                )
        self.hash_integridad = self.calcular_hash(self.contenido)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.estado != EstadoVersionTerminos.DRAFT:
            raise ValidationError(
                'Una version publicada o retirada no puede eliminarse.'
            )
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.titulo} - {self.version}'


class DocumentoFinanciacionQuerySet(models.QuerySet):
    CAMPOS_SEGURIDAD_PROTEGIDOS = frozenset({
        'estado_escaneo',
        'ultimo_intento_limpio',
        'ultimo_intento_limpio_id',
        'escaneo_requerido_desde',
    })
    MENSAJE_ACTUALIZACION_PROTEGIDA = (
        'Los campos de seguridad documental deben modificarse mediante '
        'el servicio oficial de escaneo.'
    )

    @classmethod
    def _validar_campos_actualizacion(cls, campos):
        nombres = {getattr(campo, 'name', campo) for campo in campos}
        if nombres & cls.CAMPOS_SEGURIDAD_PROTEGIDOS:
            raise ValidationError(cls.MENSAJE_ACTUALIZACION_PROTEGIDA)

    def update(self, **kwargs):
        self._validar_campos_actualizacion(kwargs)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        fields = tuple(fields)
        self._validar_campos_actualizacion(fields)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(self, objs, **kwargs):
        if kwargs.get('update_conflicts'):
            self._validar_campos_actualizacion(
                kwargs.get('update_fields') or ()
            )
        objs = list(objs)
        for documento in objs:
            if (
                documento.estado_escaneo
                != EstadoEscaneoDocumento.PENDING_SECURITY_SCAN
                or documento.ultimo_intento_limpio_id is not None
                or documento.escaneo_requerido_desde is not None
            ):
                raise ValidationError(self.MENSAJE_ACTUALIZACION_PROTEGIDA)
        return super().bulk_create(objs, **kwargs)


class DocumentoFinanciacionManager(
    models.Manager.from_queryset(DocumentoFinanciacionQuerySet)
):
    pass


class DocumentoFinanciacion(models.Model):
    objects = DocumentoFinanciacionManager()

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
        upload_to=ruta_documento_privado,
        storage=private_document_storage,
        null=True,
        blank=True,
    )
    referencia_almacenamiento = models.CharField(max_length=500, blank=True)
    nombre_seguro = models.CharField(max_length=80, blank=True, editable=False)
    nombre_original = models.CharField(max_length=120, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    tamano_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    cargado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='documentos_financiacion_cargados',
    )
    estado_escaneo = models.CharField(
        max_length=30,
        choices=EstadoEscaneoDocumento.choices,
        default=EstadoEscaneoDocumento.PENDING_SECURITY_SCAN,
    )
    escaneado_en = models.DateTimeField(null=True, blank=True)
    escaneo_requerido_desde = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
    )
    ultimo_intento_limpio = models.ForeignKey(
        'IntentoEscaneoDocumento',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name='documentos_autorizados_safe',
    )
    referencia_escaneo = models.CharField(max_length=120, blank=True)
    estado_validacion = models.CharField(
        max_length=20,
        choices=EstadoValidacionDocumento.choices,
        default=EstadoValidacionDocumento.PENDING,
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='documentos_financiacion_revisados',
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.CharField(
        max_length=30,
        choices=MotivoRechazoDocumento.choices,
        blank=True,
    )
    observacion_revision = models.CharField(max_length=500, blank=True)
    activo = models.BooleanField(default=True)
    reemplaza_a = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reemplazos',
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
        base_manager_name = 'objects'
        ordering = ['-cargado_en']
        verbose_name = 'Documento de financiacion'
        verbose_name_plural = 'Documentos de financiacion'
        permissions = [
            (
                'escanear_documento_financiacion',
                'Puede solicitar escaneos de documentos educativos',
            ),
            (
                'revisar_documento_financiacion',
                'Puede revisar documentos educativos',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud', 'sha256'],
                condition=~models.Q(sha256=''),
                name='uniq_documento_hash_solicitud',
            ),
            models.UniqueConstraint(
                fields=['solicitud', 'participante', 'tipo'],
                condition=models.Q(activo=True, participante__isnull=False),
                name='uniq_doc_activo_part_tipo',
            ),
            models.UniqueConstraint(
                fields=['solicitud', 'tipo'],
                condition=models.Q(activo=True, participante__isnull=True),
                name='uniq_doc_activo_sol_tipo',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(tipo__in=TIPOS_DOCUMENTO_IDENTIDAD_CAMARA)
                    | models.Q(origen_captura=OrigenCapturaDocumento.CAMERA)
                ),
                name='doc_identidad_origen_camara',
            ),
        ]
        indexes = [
            models.Index(fields=['solicitud', 'tipo'], name='doc_edu_sol_tipo_idx'),
            models.Index(fields=['estado_validacion'], name='doc_edu_estado_idx'),
        ]

    def clean(self):
        super().clean()
        if (
            self.tipo in TIPOS_DOCUMENTO_IDENTIDAD_CAMARA
            and self.origen_captura != OrigenCapturaDocumento.CAMERA
        ):
            raise ValidationError({
                'origen_captura': (
                    'La identificacion debe capturarse directamente con la camara.'
                ),
            })
        if (
            self.tipo in TIPOS_DOCUMENTO_IDENTIDAD_CAMARA
            and self.content_type
            and self.content_type not in {'image/jpeg', 'image/png'}
        ):
            raise ValidationError({
                'content_type': 'La captura debe ser una imagen JPEG o PNG.',
            })
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
        if self.reemplaza_a_id:
            anterior = self.reemplaza_a
            if (
                anterior.solicitud_id != self.solicitud_id
                or anterior.participante_id != self.participante_id
                or anterior.tipo != self.tipo
            ):
                raise ValidationError({
                    'reemplaza_a': 'El documento anterior no corresponde al mismo requisito.',
                })
        if (
            self.estado_validacion == EstadoValidacionDocumento.APPROVED
            and self.estado_escaneo != EstadoEscaneoDocumento.SAFE
        ):
            raise ValidationError({
                'estado_validacion': 'No puede aceptarse antes del escaneo de seguridad.',
            })
        if (
            self.estado_validacion == EstadoValidacionDocumento.REJECTED
            and not self.motivo_rechazo
        ):
            raise ValidationError({'motivo_rechazo': 'Selecciona un motivo de rechazo.'})
        if (
            self.estado_validacion != EstadoValidacionDocumento.REJECTED
            and self.motivo_rechazo
        ):
            raise ValidationError({
                'motivo_rechazo': 'El motivo solo aplica a documentos rechazados.',
            })
        self._validar_transicion_a_seguro()

    def _validar_transicion_a_seguro(self):
        if self.estado_escaneo != EstadoEscaneoDocumento.SAFE:
            return
        if not self.pk:
            raise ValidationError({
                'estado_escaneo': (
                    'El estado seguro requiere un escaneo limpio registrado.'
                ),
            })
        anterior = type(self).objects.filter(pk=self.pk).values(
            'estado_escaneo',
            'ultimo_intento_limpio_id',
            'escaneo_requerido_desde',
        ).first()
        desde = self.escaneo_requerido_desde
        if self.ultimo_intento_limpio_id:
            intentos = self.intentos_escaneo.filter(
                pk=self.ultimo_intento_limpio_id,
                estado=EstadoIntentoEscaneoDocumento.CLEAN,
                veredicto=EstadoIntentoEscaneoDocumento.CLEAN,
                finalizado_en__isnull=False,
            )
            if desde is not None:
                intentos = intentos.filter(finalizado_en__gte=desde)
            if not intentos.exists():
                raise ValidationError({
                    'estado_escaneo': (
                        'El estado seguro requiere un intento limpio reciente '
                        'del servicio de escaneo.'
                    ),
                })

        if anterior and anterior['estado_escaneo'] == EstadoEscaneoDocumento.SAFE:
            campos_seguridad_sin_cambios = (
                anterior['ultimo_intento_limpio_id']
                == self.ultimo_intento_limpio_id
                and anterior['escaneo_requerido_desde']
                == self.escaneo_requerido_desde
            )
            if not campos_seguridad_sin_cambios:
                raise ValidationError({
                    'estado_escaneo': (
                        'Los campos de seguridad de un documento seguro no '
                        'pueden modificarse directamente.'
                    ),
                })
            if self.ultimo_intento_limpio_id is None:
                return

        if not self.ultimo_intento_limpio_id:
            raise ValidationError({
                'estado_escaneo': (
                    'El estado seguro requiere un intento limpio reciente '
                    'del servicio de escaneo.'
                ),
            })

    def save(self, *args, **kwargs):
        if self.pk:
            estado_anterior = type(self).objects.filter(pk=self.pk).values_list(
                'estado_escaneo', flat=True
            ).first()
            if (
                estado_anterior == EstadoEscaneoDocumento.SAFE
                and self.estado_escaneo != EstadoEscaneoDocumento.SAFE
            ):
                self.escaneo_requerido_desde = timezone.now()
                self.ultimo_intento_limpio = None
                update_fields = kwargs.get('update_fields')
                if update_fields is not None:
                    kwargs['update_fields'] = set(update_fields) | {
                        'escaneo_requerido_desde',
                        'ultimo_intento_limpio',
                    }
        self._validar_transicion_a_seguro()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.solicitud.referencia_externa}'

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Los documentos deben conservarse para mantener su trazabilidad.'
        )


class IntentoEscaneoDocumento(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoFinanciacion,
        on_delete=models.PROTECT,
        related_name='intentos_escaneo',
    )
    numero = models.PositiveSmallIntegerField()
    estado = models.CharField(
        max_length=20,
        choices=EstadoIntentoEscaneoDocumento.choices,
        default=EstadoIntentoEscaneoDocumento.STARTED,
    )
    origen = models.CharField(
        max_length=20,
        choices=OrigenIntentoEscaneoDocumento.choices,
    )
    proveedor = models.CharField(max_length=60, blank=True)
    veredicto = models.CharField(max_length=30, blank=True)
    firma_amenaza = models.CharField(max_length=120, blank=True)
    codigo_error = models.CharField(max_length=60, blank=True)
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='escaneos_documentales_solicitados',
    )
    iniciado_en = models.DateTimeField(default=timezone.now, editable=False)
    finalizado_en = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['documento', 'numero']
        verbose_name = 'Intento de escaneo documental'
        verbose_name_plural = 'Intentos de escaneo documental'
        constraints = [
            models.UniqueConstraint(
                fields=['documento', 'numero'],
                name='uniq_intento_scan_doc_num',
            ),
            models.UniqueConstraint(
                fields=['documento'],
                condition=models.Q(
                    estado=EstadoIntentoEscaneoDocumento.STARTED
                ),
                name='uniq_intento_scan_doc_activo',
            ),
        ]
        indexes = [
            models.Index(
                fields=['estado', 'iniciado_en'],
                name='scan_doc_estado_fecha_idx',
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Los intentos de escaneo deben conservarse para auditoria.'
        )

    def _validar_cambio_estado_directo(self):
        if self._state.adding:
            if self.estado != EstadoIntentoEscaneoDocumento.STARTED:
                raise ValidationError(
                    'Los intentos deben crearse en estado iniciado.'
                )
            return
        anterior = type(self).objects.filter(pk=self.pk).values_list(
            'estado', flat=True
        ).first()
        if anterior is not None and anterior != self.estado:
            raise ValidationError(
                'El resultado del intento solo puede registrarlo el servicio de escaneo.'
            )

    def clean(self):
        super().clean()
        self._validar_cambio_estado_directo()

    def save(self, *args, **kwargs):
        self._validar_cambio_estado_directo()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.documento_id} - intento {self.numero}'


class ReaperturaEscaneoDocumento(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoFinanciacion,
        on_delete=models.PROTECT,
        related_name='reaperturas_escaneo',
    )
    autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='reaperturas_escaneo_autorizadas',
    )
    motivo = models.CharField(max_length=500)
    intentos_adicionales = models.PositiveSmallIntegerField()
    creado_en = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['documento', 'creado_en']
        verbose_name = 'Reapertura de escaneo documental'
        verbose_name_plural = 'Reaperturas de escaneo documental'
        indexes = [
            models.Index(
                fields=['documento', 'creado_en'],
                name='scan_reopen_doc_fecha_idx',
            ),
        ]

    def clean(self):
        super().clean()
        if not (self.motivo or '').strip():
            raise ValidationError({'motivo': 'El motivo operativo es obligatorio.'})
        if self.intentos_adicionales <= 0:
            raise ValidationError({
                'intentos_adicionales': 'El presupuesto debe ser positivo.',
            })

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Las reaperturas deben conservarse para auditoria.'
        )

    def __str__(self):
        return f'Reapertura {self.documento_id} - {self.creado_en:%Y-%m-%d %H:%M}'


class ValidacionIADocumento(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    documento = models.ForeignKey(
        DocumentoFinanciacion,
        on_delete=models.PROTECT,
        related_name='validaciones_ia',
    )
    numero = models.PositiveSmallIntegerField()
    estado = models.CharField(
        max_length=30,
        choices=EstadoValidacionIADocumento.choices,
        default=EstadoValidacionIADocumento.STARTED,
    )
    origen = models.CharField(
        max_length=20,
        choices=OrigenValidacionIADocumento.choices,
    )
    proveedor = models.CharField(max_length=60, blank=True)
    modelo = models.CharField(max_length=80, blank=True)
    version_esquema = models.CharField(max_length=30, default='2')
    calidad = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    legibilidad = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    confianza = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    corresponde_tipo = models.BooleanField(null=True, blank=True)
    indicios_imagen_real = models.BooleanField(null=True, blank=True)
    datos_consistentes = models.BooleanField(null=True, blank=True)
    hallazgos = models.JSONField(default=list, blank=True)
    resultado_estructurado = models.JSONField(default=dict, blank=True)
    codigo_error = models.CharField(max_length=60, blank=True)
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='validaciones_ia_documentales_solicitadas',
    )
    iniciado_en = models.DateTimeField(default=timezone.now, editable=False)
    finalizado_en = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['documento', 'numero']
        verbose_name = 'Validacion IA documental'
        verbose_name_plural = 'Validaciones IA documentales'
        permissions = [
            (
                'procesar_validacion_ia_documento',
                'Puede procesar validaciones IA de documentos educativos',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['documento', 'numero'],
                name='uniq_validacion_ia_doc_num',
            ),
            models.UniqueConstraint(
                fields=['documento'],
                condition=models.Q(estado=EstadoValidacionIADocumento.STARTED),
                name='uniq_validacion_ia_doc_activa',
            ),
        ]
        indexes = [
            models.Index(
                fields=['estado', 'iniciado_en'],
                name='val_ia_doc_estado_fecha_idx',
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Las validaciones IA deben conservarse para auditoria.'
        )

    def __str__(self):
        return f'{self.documento_id} - validacion IA {self.numero}'


class EvidenciaMatricula(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.OneToOneField(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='evidencia_matricula',
    )
    institucion_declarada = models.CharField(max_length=200)
    programa_curso = models.CharField(max_length=200)
    periodo_academico = models.CharField(max_length=80)
    referencia_matricula = models.CharField(max_length=120, blank=True)
    documento_soporte = models.ForeignKey(
        DocumentoFinanciacion,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='evidencias_matricula',
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoEvidenciaMatricula.choices,
        default=EstadoEvidenciaMatricula.PENDING,
    )
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='evidencias_matricula_registradas',
    )
    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='evidencias_matricula_revisadas',
    )
    revisado_en = models.DateTimeField(null=True, blank=True)
    motivo_rechazo = models.CharField(
        max_length=30,
        choices=MotivoRechazoDocumento.choices,
        blank=True,
    )
    observacion_revision = models.CharField(max_length=500, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Evidencia de matricula'
        verbose_name_plural = 'Evidencias de matricula'

    def clean(self):
        super().clean()
        if (
            self.documento_soporte_id
            and self.documento_soporte.solicitud_id != self.solicitud_id
        ):
            raise ValidationError({
                'documento_soporte': 'El soporte no pertenece a la solicitud.',
            })
        if (
            self.documento_soporte_id
            and self.documento_soporte.tipo
            != TipoDocumentoFinanciacion.ENROLLMENT_EVIDENCE
        ):
            raise ValidationError({
                'documento_soporte': 'El soporte no es evidencia de matricula.',
            })
        if self.estado == EstadoEvidenciaMatricula.REJECTED and not self.motivo_rechazo:
            raise ValidationError({'motivo_rechazo': 'Selecciona un motivo de rechazo.'})
        if (
            self.estado == EstadoEvidenciaMatricula.ACCEPTED
            and not self.documento_soporte_id
        ):
            raise ValidationError({
                'documento_soporte': (
                    'Solo un soporte adjunto puede marcarse como aceptado.'
                ),
            })

    def __str__(self):
        return f'Matricula {self.solicitud.referencia_externa}'


class ConfiguracionFinancieraEducativa(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    codigo = models.CharField(max_length=60)
    version = models.PositiveIntegerField()
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=20,
        choices=EstadoConfiguracionFinanciera.choices,
        default=EstadoConfiguracionFinanciera.DRAFT,
    )
    porcentaje_originacion = models.DecimalField(max_digits=9, decimal_places=6)
    porcentaje_iva_originacion = models.DecimalField(max_digits=9, decimal_places=6)
    porcentaje_fondo_garantias = models.DecimalField(max_digits=9, decimal_places=6)
    proveedor_fondo_garantias = models.CharField(max_length=120)
    porcentaje_seguro_vida = models.DecimalField(max_digits=9, decimal_places=6)
    proveedor_seguro_vida = models.CharField(max_length=120)
    tasa_interes_mensual = models.DecimalField(max_digits=9, decimal_places=6)
    moneda = models.CharField(max_length=3, default='COP')
    metodo_calculo = models.CharField(
        max_length=40,
        choices=MetodoCalculoFinanciero.choices,
        default=MetodoCalculoFinanciero.FRENCH_AMORTIZATION,
    )
    politica_redondeo = models.CharField(
        max_length=40,
        choices=PoliticaRedondeoFinanciero.choices,
        default=PoliticaRedondeoFinanciero.COP_PESO_HALF_UP,
    )
    politica_causacion = models.CharField(
        max_length=30,
        choices=PoliticaCausacionInteres.choices,
        default=PoliticaCausacionInteres.DAILY_30,
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='configuraciones_financieras_educativas_creadas',
    )
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='configuraciones_financieras_educativas_actualizadas',
    )
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['codigo', '-version']
        verbose_name = 'Configuracion financiera educativa'
        verbose_name_plural = 'Configuraciones financieras educativas'
        constraints = [
            models.UniqueConstraint(
                fields=['codigo', 'version'],
                name='uniq_config_fin_edu_codigo_version',
            ),
        ]
        indexes = [
            models.Index(
                fields=['estado', 'vigente_desde', 'vigente_hasta'],
                name='cfg_fin_edu_vigencia_idx',
            ),
        ]

    def clean(self):
        super().clean()
        if self.vigente_hasta and self.vigente_hasta < self.vigente_desde:
            raise ValidationError({
                'vigente_hasta': 'La vigencia final no puede ser anterior a la inicial.',
            })
        porcentajes = (
            'porcentaje_originacion',
            'porcentaje_iva_originacion',
            'porcentaje_fondo_garantias',
            'porcentaje_seguro_vida',
            'tasa_interes_mensual',
        )
        for campo in porcentajes:
            if getattr(self, campo) < 0:
                raise ValidationError({campo: 'El porcentaje no puede ser negativo.'})
        if self.moneda != 'COP':
            raise ValidationError({'moneda': 'La financiacion educativa usa COP.'})
        if (
            self.porcentaje_fondo_garantias > 0
            and not self.proveedor_fondo_garantias.strip()
        ):
            raise ValidationError({
                'proveedor_fondo_garantias': 'Indica el proveedor del fondo.',
            })
        if self.porcentaje_seguro_vida > 0 and not self.proveedor_seguro_vida.strip():
            raise ValidationError({
                'proveedor_seguro_vida': 'Indica el proveedor del seguro.',
            })
        if self.estado == EstadoConfiguracionFinanciera.ACTIVE:
            superpuestas = type(self).objects.filter(
                codigo=self.codigo,
                estado=EstadoConfiguracionFinanciera.ACTIVE,
                vigente_desde__lte=self.vigente_hasta or date.max,
            ).filter(
                models.Q(vigente_hasta__isnull=True)
                | models.Q(vigente_hasta__gte=self.vigente_desde)
            ).exclude(pk=self.pk)
            if superpuestas.exists():
                raise ValidationError('Existe una configuracion activa superpuesta.')

    def save(self, *args, **kwargs):
        if not self._state.adding:
            anterior = type(self).objects.filter(pk=self.pk).first()
            if anterior and (
                anterior.estado != EstadoConfiguracionFinanciera.DRAFT
                or anterior.fotografias.exists()
            ):
                raise ValidationError(
                    'Una configuracion activa, retirada o aplicada no puede modificarse.'
                )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.estado != EstadoConfiguracionFinanciera.DRAFT or self.fotografias.exists():
            raise ValidationError('La configuracion no puede eliminarse.')
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f'{self.codigo} v{self.version}'


class CondicionesFinancieras(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='fotografias_financieras',
    )
    configuracion = models.ForeignKey(
        ConfiguracionFinancieraEducativa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fotografias',
    )
    numero_version = models.PositiveIntegerField(default=1)
    activa = models.BooleanField(default=False)
    bloqueada = models.BooleanField(default=False)
    es_legado = models.BooleanField(default=False)
    valor_financiado = models.DecimalField(max_digits=14, decimal_places=2)
    plazo_meses = models.PositiveSmallIntegerField()
    tasa_interes_mensual = models.DecimalField(max_digits=7, decimal_places=4)
    tasa_comision = models.DecimalField(max_digits=7, decimal_places=4)
    valor_comision = models.DecimalField(max_digits=14, decimal_places=2)
    tasa_iva_comision = models.DecimalField(max_digits=7, decimal_places=4)
    valor_iva_comision = models.DecimalField(max_digits=14, decimal_places=2)
    tasa_fondo_garantias = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        default=Decimal('0'),
    )
    valor_fondo_garantias = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0'),
    )
    proveedor_fondo_garantias = models.CharField(max_length=120, blank=True)
    tasa_seguro_vida = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        default=Decimal('0'),
    )
    valor_seguro_vida = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0'),
    )
    proveedor_seguro_vida = models.CharField(max_length=120, blank=True)
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
    politica_redondeo = models.CharField(
        max_length=40,
        choices=PoliticaRedondeoFinanciero.choices,
        default=PoliticaRedondeoFinanciero.COP_PESO_HALF_UP,
    )
    politica_causacion = models.CharField(
        max_length=30,
        choices=PoliticaCausacionInteres.choices,
        default=PoliticaCausacionInteres.DAILY_30,
    )
    fecha_calculo = models.DateTimeField(default=timezone.now, editable=False)
    fecha_inicio_plan = models.DateField(null=True, blank=True)
    fecha_primer_vencimiento = models.DateField(null=True, blank=True)
    fecha_ultimo_vencimiento = models.DateField(null=True, blank=True)
    huella_determinantes = models.CharField(
        max_length=64,
        blank=True,
        validators=[hash_sha256_validator],
    )
    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='fotografias_financieras_educativas_creadas',
    )

    class Meta:
        verbose_name = 'Condiciones financieras'
        verbose_name_plural = 'Condiciones financieras'
        ordering = ['solicitud', '-numero_version']
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud', 'numero_version'],
                name='uniq_foto_fin_edu_version',
            ),
            models.UniqueConstraint(
                fields=['solicitud'],
                condition=models.Q(activa=True),
                name='uniq_foto_fin_edu_activa',
            ),
        ]
        indexes = [
            models.Index(
                fields=['solicitud', 'activa'],
                name='foto_fin_edu_activa_idx',
            ),
        ]

    CAMPOS_CALCULO = (
        'solicitud_id',
        'configuracion_id',
        'numero_version',
        'es_legado',
        'valor_financiado',
        'plazo_meses',
        'tasa_interes_mensual',
        'tasa_comision',
        'valor_comision',
        'tasa_iva_comision',
        'valor_iva_comision',
        'tasa_fondo_garantias',
        'valor_fondo_garantias',
        'proveedor_fondo_garantias',
        'tasa_seguro_vida',
        'valor_seguro_vida',
        'proveedor_seguro_vida',
        'capital_financiado',
        'valor_cuota_estimada',
        'interes_total_estimado',
        'total_estimado',
        'metodo_calculo',
        'base_calculo',
        'version_regla',
        'moneda',
        'politica_redondeo',
        'politica_causacion',
        'fecha_calculo',
        'fecha_inicio_plan',
        'fecha_primer_vencimiento',
        'fecha_ultimo_vencimiento',
        'huella_determinantes',
    )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            anterior = type(self).objects.filter(pk=self.pk).first()
            if anterior and anterior.bloqueada:
                raise ValidationError('La fotografia financiera esta bloqueada.')
            if anterior:
                modificados = [
                    campo
                    for campo in self.CAMPOS_CALCULO
                    if getattr(anterior, campo) != getattr(self, campo)
                ]
                if modificados:
                    raise ValidationError(
                        'Los datos calculados no pueden modificarse; crea una nueva version.'
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Las fotografias financieras no pueden eliminarse.')

    def __str__(self):
        legado = ' legado' if self.es_legado else ''
        return (
            f'Condiciones {self.solicitud.referencia_externa} '
            f'v{self.numero_version}{legado}'
        )


class CuotaAmortizacionEducativa(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    fotografia = models.ForeignKey(
        CondicionesFinancieras,
        on_delete=models.PROTECT,
        related_name='cuotas',
    )
    numero = models.PositiveSmallIntegerField()
    fecha_vencimiento = models.DateField()
    saldo_inicial = models.DecimalField(max_digits=14, decimal_places=2)
    interes = models.DecimalField(max_digits=14, decimal_places=2)
    capital = models.DecimalField(max_digits=14, decimal_places=2)
    valor_cuota = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_final = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ['fotografia', 'numero']
        verbose_name = 'Cuota de amortizacion educativa'
        verbose_name_plural = 'Cuotas de amortizacion educativa'
        constraints = [
            models.UniqueConstraint(
                fields=['fotografia', 'numero'],
                name='uniq_cuota_fin_edu_numero',
            ),
        ]

    def __str__(self):
        return f'{self.fotografia_id} - cuota {self.numero}'


class ArtefactoContractualEducativo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='artefactos_contractuales',
    )
    fotografia_financiera = models.ForeignKey(
        CondicionesFinancieras,
        on_delete=models.PROTECT,
        related_name='artefactos_contractuales',
    )
    tipo = models.CharField(
        max_length=30,
        choices=TipoArtefactoContractualEducativo.choices,
    )
    numero_version = models.PositiveIntegerField()
    vigente = models.BooleanField(default=True)
    estado = models.CharField(
        max_length=30,
        choices=EstadoArtefactoContractualEducativo.choices,
        default=EstadoArtefactoContractualEducativo.GENERATED,
    )
    numero_documento = models.CharField(max_length=80)
    version_plantilla = models.CharField(max_length=40)
    archivo = models.FileField(
        upload_to=ruta_artefacto_contractual_privado,
        storage=private_document_storage,
        max_length=500,
    )
    hash_sha256 = models.CharField(
        max_length=64,
        validators=[hash_sha256_validator],
    )
    tamano_bytes = models.PositiveBigIntegerField()
    archivo_firmado = models.FileField(
        upload_to=ruta_artefacto_contractual_privado,
        storage=private_document_storage,
        max_length=500,
        null=True,
        blank=True,
    )
    hash_firmado_sha256 = models.CharField(
        max_length=64,
        validators=[hash_sha256_validator],
        blank=True,
    )
    tamano_firmado_bytes = models.PositiveBigIntegerField(
        null=True,
        blank=True,
    )
    firmado_en = models.DateTimeField(null=True, blank=True, editable=False)
    generado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='artefactos_contractuales_educativos_generados',
    )
    generado_en = models.DateTimeField(default=timezone.now, editable=False)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['solicitud', 'tipo', '-numero_version']
        verbose_name = 'Artefacto contractual educativo'
        verbose_name_plural = 'Artefactos contractuales educativos'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud', 'tipo', 'numero_version'],
                name='uniq_art_edu_sol_tipo_ver',
            ),
            models.UniqueConstraint(
                fields=['solicitud', 'tipo'],
                condition=models.Q(vigente=True),
                name='uniq_art_edu_vigente_tipo',
            ),
            models.CheckConstraint(
                condition=models.Q(tamano_bytes__gt=0),
                name='art_edu_tamano_positivo',
            ),
        ]
        indexes = [
            models.Index(
                fields=['solicitud', 'tipo', 'estado'],
                name='art_edu_sol_tipo_estado',
            ),
        ]

    def clean(self):
        super().clean()
        if not self.fotografia_financiera_id or not self.solicitud_id:
            return
        fotografia = self.fotografia_financiera
        if fotografia.solicitud_id != self.solicitud_id:
            raise ValidationError({
                'fotografia_financiera': (
                    'La fotografia financiera no pertenece a la solicitud.'
                ),
            })
        if (
            fotografia.es_legado
            or not fotografia.activa
            or not fotografia.bloqueada
        ):
            raise ValidationError({
                'fotografia_financiera': (
                    'El artefacto requiere condiciones vigentes y bloqueadas.'
                ),
            })
        tiene_firmado = bool(
            self.archivo_firmado
            or self.hash_firmado_sha256
            or self.tamano_firmado_bytes
            or self.firmado_en
        )
        if self.estado == EstadoArtefactoContractualEducativo.SIGNED:
            if self.tipo != TipoArtefactoContractualEducativo.PROMISSORY_NOTE:
                raise ValidationError({
                    'estado': 'Solo el pagare se completa mediante firma.',
                })
            if not all((
                self.archivo_firmado,
                self.hash_firmado_sha256,
                self.tamano_firmado_bytes,
                self.firmado_en,
            )):
                raise ValidationError({
                    'archivo_firmado': (
                        'Un artefacto firmado requiere archivo, hash, tamano y fecha.'
                    ),
                })
        elif tiene_firmado:
            raise ValidationError({
                'archivo_firmado': (
                    'La evidencia firmada solo corresponde al estado firmado.'
                ),
            })

    def delete(self, *args, **kwargs):
        raise ValidationError('Los artefactos contractuales no pueden eliminarse.')

    def __str__(self):
        return f'{self.numero_documento} - {self.get_tipo_display()}'


class ProcesoFirmaEducativa(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='procesos_firma',
    )
    artefacto = models.OneToOneField(
        ArtefactoContractualEducativo,
        on_delete=models.PROTECT,
        related_name='proceso_firma',
    )
    proveedor = models.CharField(max_length=30, default='ZAPSIGN')
    estado = models.CharField(
        max_length=30,
        choices=EstadoProcesoFirmaEducativa.choices,
        default=EstadoProcesoFirmaEducativa.PENDING,
    )
    external_id = models.CharField(max_length=80, unique=True)
    token_documento_externo = models.CharField(
        max_length=160,
        unique=True,
        null=True,
        blank=True,
    )
    destinatario_hmac = models.CharField(
        max_length=64,
        validators=[hash_sha256_validator],
    )
    intentos_envio = models.PositiveSmallIntegerField(default=0)
    codigo_ultimo_error = models.CharField(max_length=60, blank=True)
    envio_iniciado_en = models.DateTimeField(null=True, blank=True)
    enviado_en = models.DateTimeField(null=True, blank=True)
    firmado_en = models.DateTimeField(null=True, blank=True)
    rechazado_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Proceso de firma educativa'
        verbose_name_plural = 'Procesos de firma educativa'
        permissions = [
            (
                'gestionar_firma_educativa',
                'Puede enviar y recuperar firmas educativas',
            ),
        ]
        indexes = [
            models.Index(
                fields=['solicitud', 'estado'],
                name='firma_edu_sol_estado',
            ),
        ]

    def clean(self):
        super().clean()
        if self.artefacto_id and self.solicitud_id:
            if self.artefacto.solicitud_id != self.solicitud_id:
                raise ValidationError({
                    'artefacto': 'El pagare no pertenece a la solicitud.',
                })
            if (
                self.artefacto.tipo
                != TipoArtefactoContractualEducativo.PROMISSORY_NOTE
            ):
                raise ValidationError({
                    'artefacto': 'Solo un pagare puede enviarse a firma.',
                })

    def delete(self, *args, **kwargs):
        raise ValidationError('Los procesos de firma no pueden eliminarse.')

    def __str__(self):
        return f'{self.external_id} - {self.get_estado_display()}'


class EventoWebhookFirmaEducativa(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payload_hash = models.CharField(
        max_length=64,
        unique=True,
        validators=[hash_sha256_validator],
    )
    tipo_evento = models.CharField(max_length=50)
    proceso = models.ForeignKey(
        ProcesoFirmaEducativa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='eventos_webhook',
    )
    estado = models.CharField(
        max_length=30,
        choices=EstadoEventoWebhookFirmaEducativa.choices,
        default=EstadoEventoWebhookFirmaEducativa.RECEIVED,
    )
    codigo_resultado = models.CharField(max_length=60, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    recibido_en = models.DateTimeField(auto_now_add=True)
    procesado_en = models.DateTimeField(null=True, blank=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-recibido_en']
        verbose_name = 'Evento de webhook de firma educativa'
        verbose_name_plural = 'Eventos de webhook de firma educativa'
        indexes = [
            models.Index(
                fields=['tipo_evento', 'estado'],
                name='evt_firma_edu_tipo_estado',
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError('Los eventos de firma no pueden eliminarse.')

    def __str__(self):
        return f'{self.tipo_evento} - {self.get_estado_display()}'


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


class EventoSeguridadFinanciacion(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='eventos_seguridad',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='eventos_seguridad_financiacion',
    )
    tipo = models.CharField(
        max_length=50,
        choices=TipoEventoSeguridadFinanciacion.choices,
    )
    endpoint = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-creado_en', 'id']
        verbose_name = 'Evento de seguridad de financiacion'
        verbose_name_plural = 'Eventos de seguridad de financiacion'
        indexes = [
            models.Index(
                fields=['solicitud', 'creado_en'],
                name='evt_seg_edu_sol_fecha',
            ),
            models.Index(
                fields=['tipo', 'creado_en'],
                name='evt_seg_edu_tipo_fecha',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.creado_en.isoformat()}'


class DecisionRevisionEducativa(ModeloInmutableMixin, models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='decisiones_revision',
    )
    tipo = models.CharField(
        max_length=30,
        choices=TipoDecisionRevisionEducativa.choices,
    )
    motivo = models.CharField(
        max_length=40,
        choices=MotivoDecisionRevisionEducativa.choices,
    )
    mensaje_solicitante = models.CharField(max_length=500, blank=True)
    observacion_interna = models.CharField(max_length=1000, blank=True)
    requisitos_pendientes = models.JSONField(default=list, blank=True)
    fotografia_financiera = models.ForeignKey(
        CondicionesFinancieras,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='decisiones_revision',
    )
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='decisiones_revision_educativa',
    )
    creada_en = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ['-creada_en', 'id']
        verbose_name = 'Decision de revision educativa'
        verbose_name_plural = 'Decisiones de revision educativa'
        constraints = [
            models.UniqueConstraint(
                fields=['solicitud'],
                condition=models.Q(
                    tipo__in=[
                        TipoDecisionRevisionEducativa.APPROVED,
                        TipoDecisionRevisionEducativa.REJECTED,
                    ]
                ),
                name='uniq_dec_fin_edu_final',
            ),
        ]
        indexes = [
            models.Index(
                fields=['solicitud', 'creada_en'],
                name='dec_rev_edu_sol_fecha',
            ),
        ]

    def clean(self):
        super().clean()
        if self.tipo == TipoDecisionRevisionEducativa.APPROVED:
            if not self.fotografia_financiera_id:
                raise ValidationError({
                    'fotografia_financiera': (
                        'La aprobacion requiere una fotografia financiera.'
                    ),
                })
            if (
                self.fotografia_financiera.solicitud_id
                != self.solicitud_id
            ):
                raise ValidationError({
                    'fotografia_financiera': (
                        'La fotografia no pertenece a la solicitud.'
                    ),
                })
        elif self.fotografia_financiera_id:
            raise ValidationError({
                'fotografia_financiera': (
                    'La fotografia solo se fija en una aprobacion.'
                ),
            })
        requisitos = self.requisitos_pendientes or []
        if (
            not isinstance(requisitos, list)
            or any(
                requisito not in RequisitoCorreccionEducativa.values
                for requisito in requisitos
            )
        ):
            raise ValidationError({
                'requisitos_pendientes': (
                    'Los requisitos de correccion no son validos.'
                ),
            })
        if (
            self.tipo == TipoDecisionRevisionEducativa.CORRECTION_REQUESTED
            and not requisitos
        ):
            raise ValidationError({
                'requisitos_pendientes': (
                    'Selecciona al menos un requisito por corregir.'
                ),
            })
        if (
            self.tipo != TipoDecisionRevisionEducativa.CORRECTION_REQUESTED
            and requisitos
        ):
            raise ValidationError({
                'requisitos_pendientes': (
                    'Los requisitos solo aplican a una correccion.'
                ),
            })

    def __str__(self):
        return f'{self.solicitud.referencia_externa} - {self.get_tipo_display()}'


class EntregaCorreoEstadoSolicitud(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey(
        SolicitudFinanciacionEducativa,
        on_delete=models.PROTECT,
        related_name='entregas_correo_estado',
    )
    decision = models.OneToOneField(
        DecisionRevisionEducativa,
        on_delete=models.PROTECT,
        related_name='entrega_correo',
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoEntregaCorreoSolicitud.choices,
        default=EstadoEntregaCorreoSolicitud.PENDING,
    )
    destinatario_hmac = models.CharField(
        max_length=64,
        validators=[hash_sha256_validator],
        editable=False,
    )
    intentos = models.PositiveSmallIntegerField(default=0)
    codigo_ultimo_error = models.CharField(max_length=60, blank=True)
    iniciada_en = models.DateTimeField(null=True, blank=True)
    enviada_en = models.DateTimeField(null=True, blank=True)
    fallida_en = models.DateTimeField(null=True, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-creada_en']
        verbose_name = 'Entrega de correo de estado'
        verbose_name_plural = 'Entregas de correo de estado'

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Las entregas de correo deben conservarse para auditoria.'
        )

    def __str__(self):
        return f'{self.solicitud_id} - {self.get_estado_display()}'
