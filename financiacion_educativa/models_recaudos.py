"""Evidencia de ingresos, independiente de la aplicacion contable (Fase 2B)."""
import uuid
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .storage import private_document_storage


class EvidenciaQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Use el servicio oficial de recaudos; la evidencia no es editable.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('Use el servicio oficial de recaudos; la evidencia no es editable.')

    def bulk_create(self, objs, **kwargs):
        raise ValidationError('La evidencia debe registrarse individualmente por el servicio oficial.')

    def delete(self):
        raise ValidationError('La evidencia de recaudos debe conservarse.')


class EvidenciaManager(models.Manager.from_queryset(EvidenciaQuerySet)):
    pass


class EvidenciaInmutable(models.Model):
    objects = EvidenciaManager()

    class Meta:
        abstract = True
        base_manager_name = 'objects'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('La evidencia registrada no se puede editar.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('La evidencia de recaudos debe conservarse.')


def ruta_archivo_recaudo(instance, _nombre):
    extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'application/pdf': 'pdf'}[instance.content_type]
    return f'financiacion-educativa/recaudos/{instance.pk}/{uuid.uuid4()}.{extension}'


class ArchivoRecaudoEducativo(EvidenciaInmutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    archivo = models.FileField(storage=private_document_storage, upload_to=ruta_archivo_recaudo)
    content_type = models.CharField(max_length=32)
    sha256 = models.CharField(max_length=64)
    tamano_bytes = models.PositiveIntegerField()
    finalidad = models.CharField(max_length=20, choices=[('QR', 'QR oficial'), ('RECEIPT', 'Comprobante del pagador'), ('BANK_EVIDENCE', 'Evidencia bancaria')])
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        permissions = [('escanear_comprobantes_educativos', 'Puede recuperar escaneos de comprobantes educativos')]
        constraints = [models.CheckConstraint(condition=models.Q(tamano_bytes__gt=0), name='recaudo_archivo_no_vacio')]

    @property
    def estado_escaneo(self):
        cache = getattr(self, '_prefetched_objects_cache', {}).get('escaneos')
        ultimo = max(cache, key=lambda intento: intento.numero, default=None) if cache is not None else self.escaneos.order_by('-numero').first()
        if ultimo and ultimo.estado == 'CLEAN' and ultimo.sha256 == self.sha256:
            return 'SAFE'
        if ultimo and ultimo.estado == 'INFECTED':
            return 'BLOCKED'
        if ultimo and ultimo.estado == 'ERROR':
            return 'ERROR'
        return 'PENDING'


class IntentoEscaneoRecaudo(EvidenciaInmutable):
    class Estado(models.TextChoices):
        STARTED = 'STARTED', 'En proceso'
        CLEAN = 'CLEAN', 'Limpio'
        INFECTED = 'INFECTED', 'Bloqueado'
        ERROR = 'ERROR', 'Fallo tecnico'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    archivo = models.ForeignKey(ArchivoRecaudoEducativo, on_delete=models.PROTECT, related_name='escaneos')
    numero = models.PositiveIntegerField()
    estado = models.CharField(max_length=16, choices=Estado.choices, default=Estado.STARTED)
    sha256 = models.CharField(max_length=64)
    iniciado_en = models.DateTimeField(auto_now_add=True)
    finalizado_en = models.DateTimeField(null=True, blank=True)
    codigo_error = models.CharField(max_length=40, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    motivo_reintento = models.CharField(max_length=500, blank=True)

    class Meta(EvidenciaInmutable.Meta):
        constraints = [
            models.UniqueConstraint(fields=['archivo', 'numero'], name='uniq_recaudo_scan_num'),
            models.UniqueConstraint(fields=['archivo'], condition=models.Q(estado='STARTED'), name='uniq_recaudo_scan_activo'),
        ]

    def save(self, *args, **kwargs):
        if not getattr(self, '_transicion_oficial', False):
            if not self._state.adding or self.estado != self.Estado.STARTED:
                raise ValidationError('Solo el servicio de escaneo registra el veredicto.')
        self.full_clean()
        return models.Model.save(self, *args, **kwargs)


class CuentaRecaudoEducativa(EvidenciaInmutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entidad = models.CharField(max_length=80)
    identificador_cuenta = models.CharField(max_length=80)
    creada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        permissions = [('configurar_receptor_breb', 'Puede versionar y activar el receptor BRE-B')]
        constraints = [models.UniqueConstraint(fields=['entidad', 'identificador_cuenta'], name='uniq_cuenta_recaudo_banco')]

    def clean(self):
        self.entidad = normalizar_identificador_cuenta(self.entidad)
        self.identificador_cuenta = normalizar_identificador_cuenta(self.identificador_cuenta)

    def __str__(self):
        return f'{self.entidad} / cuenta terminada en {self.identificador_cuenta[-4:]}'


def normalizar_identificador_cuenta(valor):
    canonico = re.sub(r'[\s-]', '', valor).upper()
    if not re.fullmatch(r'[A-Z0-9]{1,80}', canonico):
        raise ValidationError('Use el identificador bancario estable: letras ASCII y numeros.')
    return canonico


class ReceptorBrebEducativo(EvidenciaInmutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta = models.ForeignKey(CuentaRecaudoEducativa, on_delete=models.PROTECT)
    titular = models.CharField(max_length=180)
    llave = models.CharField(max_length=180)
    qr = models.OneToOneField(ArchivoRecaudoEducativo, on_delete=models.PROTECT)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Version BRE-B {self.pk}'


class EventoReceptorBrebEducativo(EvidenciaInmutable):
    receptor = models.ForeignKey(ReceptorBrebEducativo, on_delete=models.PROTECT, related_name='eventos')
    habilitado = models.BooleanField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    motivo = models.CharField(max_length=500)
    creado_en = models.DateTimeField(auto_now_add=True)


class ReporteRecaudoEducativo(EvidenciaInmutable):
    class Proposito(models.TextChoices):
        INSTALLMENT = 'INSTALLMENT', 'Cuota'
        PRINCIPAL = 'PRINCIPAL', 'Abono a capital'
        FULL_PAYMENT = 'FULL_PAYMENT', 'Intencion de pago total'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    solicitud = models.ForeignKey('financiacion_educativa.SolicitudFinanciacionEducativa', on_delete=models.PROTECT, related_name='reportes_recaudo')
    fotografia = models.ForeignKey('financiacion_educativa.CondicionesFinancieras', on_delete=models.PROTECT)
    receptor = models.ForeignKey(ReceptorBrebEducativo, on_delete=models.PROTECT)
    comprobante = models.OneToOneField(ArchivoRecaudoEducativo, on_delete=models.PROTECT)
    reportado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    clave_idempotencia = models.UUIDField()
    payload_sha256 = models.CharField(max_length=64)
    proposito = models.CharField(max_length=16, choices=Proposito.choices)
    monto_reportado = models.DecimalField(max_digits=16, decimal_places=2)
    fecha_declarada = models.DateField()
    referencia_declarada = models.CharField(max_length=120, blank=True)
    cuota_sugerida = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        permissions = [
            ('consultar_recaudos_educativos', 'Puede consultar reportes y recaudos educativos'),
            ('conciliar_recaudos_educativos', 'Puede confirmar o rechazar un ingreso educativo'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['solicitud', 'reportado_por', 'clave_idempotencia'], name='uniq_reporte_recaudo_payload'),
            models.CheckConstraint(condition=models.Q(monto_reportado__gt=0), name='reporte_recaudo_monto_positivo'),
        ]

    def clean(self):
        if self.fotografia_id and self.fotografia.solicitud_id != self.solicitud_id:
            raise ValidationError('La fotografia no pertenece a la obligacion.')

    @property
    def estado_publico(self):
        if hasattr(self, 'conciliacion'):
            if hasattr(self.conciliacion, 'aplicacion'):
                return 'Pago aplicado a cuota'
            return 'Ingreso confirmado pendiente de aplicacion'
        if hasattr(self, 'rechazo'):
            return 'Reporte no conciliado'
        return 'Revision pendiente'


class ConciliacionRecaudoEducativo(EvidenciaInmutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reporte = models.OneToOneField(ReporteRecaudoEducativo, on_delete=models.PROTECT, related_name='conciliacion')
    cuenta = models.ForeignKey(CuentaRecaudoEducativa, on_delete=models.PROTECT)
    referencia_verificada = models.CharField(max_length=120)
    monto_recibido = models.DecimalField(max_digits=16, decimal_places=2)
    fecha_efectiva = models.DateField()
    evidencia_movimiento = models.ForeignKey(ArchivoRecaudoEducativo, on_delete=models.PROTECT)
    observacion = models.CharField(max_length=1000)
    justificacion_diferencia = models.CharField(max_length=500, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    confirmada_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        constraints = [
            models.UniqueConstraint(fields=['cuenta', 'referencia_verificada'], name='uniq_recaudo_movimiento_banco'),
            models.CheckConstraint(condition=models.Q(monto_recibido__gt=0), name='recaudo_monto_positivo'),
        ]


class RechazoReporteRecaudoEducativo(EvidenciaInmutable):
    reporte = models.OneToOneField(ReporteRecaudoEducativo, on_delete=models.PROTECT, related_name='rechazo')
    motivo_publico = models.CharField(max_length=500)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)


class AplicacionCuotaEducativa(EvidenciaInmutable):
    """Aplicación íntegra a una cuota del calendario firmado."""

    conciliacion = models.OneToOneField(
        ConciliacionRecaudoEducativo, on_delete=models.PROTECT, related_name='aplicacion',
    )
    cuota = models.OneToOneField(
        'financiacion_educativa.CuotaAmortizacionEducativa',
        on_delete=models.PROTECT, related_name='aplicacion_recaudo',
    )
    capital_aplicado = models.DecimalField(max_digits=14, decimal_places=2)
    interes_aplicado = models.DecimalField(max_digits=14, decimal_places=2)
    fecha_efectiva = models.DateField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cuota_verificada = models.BooleanField(default=False)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        permissions = [('aplicar_cuotas_educativas', 'Puede aplicar recaudos conciliados a cuotas educativas')]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capital_aplicado__gt=0, interes_aplicado__gte=0, cuota_verificada=True),
                name='aplicacion_cuota_importes_validos',
            ),
        ]

    def clean(self):
        super().clean()
        if not self.conciliacion_id or not self.cuota_id:
            return
        ingreso, cuota = self.conciliacion, self.cuota
        if (
            ingreso.reporte.fotografia_id != cuota.fotografia_id
            or ingreso.reporte.proposito != ReporteRecaudoEducativo.Proposito.INSTALLMENT
            or ingreso.monto_recibido != cuota.valor_cuota
            or self.capital_aplicado != cuota.capital
            or self.interes_aplicado != cuota.interes
            or self.fecha_efectiva != ingreso.fecha_efectiva
            or cuota.capital + cuota.interes != cuota.valor_cuota
            or not self.cuota_verificada
        ):
            raise ValidationError('La aplicación no coincide con el recaudo y la cuota contractual.')
