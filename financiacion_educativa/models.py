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
    EstadoSolicitudFinanciacion,
    EstadoEscaneoDocumento,
    EstadoEvidenciaMatricula,
    EstadoValidacionDocumento,
    EstadoConfiguracionFinanciera,
    EstadoInvitacionContinuacion,
    EstadoVersionTerminos,
    MetodoCalculoFinanciero,
    MotivoRechazoDocumento,
    OrigenCapturaDocumento,
    PoliticaCausacionInteres,
    PoliticaRedondeoFinanciero,
    PropositoInvitacionContinuacion,
    RelacionEstudiante,
    RolParticipante,
    TipoEventoParticipante,
    TipoEventoInvitacion,
    TipoConsentimiento,
    TipoDocumentoFinanciacion,
    TipoDocumentoIdentidad,
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
        ordering = ['-cargado_en']
        verbose_name = 'Documento de financiacion'
        verbose_name_plural = 'Documentos de financiacion'
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

    def __str__(self):
        return f'{self.get_tipo_display()} - {self.solicitud.referencia_externa}'

    def delete(self, *args, **kwargs):
        raise ValidationError(
            'Los documentos deben conservarse para mantener su trazabilidad.'
        )


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
