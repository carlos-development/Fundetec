"""Append-only allocation journal. Existing full applications remain virtual entries."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from .models_recaudos import EvidenciaInmutable


class PoliticaLibroEducativo(EvidenciaInmutable):
    institucion = models.ForeignKey('instituciones.Institucion', on_delete=models.PROTECT)
    version = models.CharField(max_length=80)
    reglas = models.JSONField()
    referencia_aprobacion = models.CharField(max_length=200)
    aprobada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)
    es_sintetica = models.BooleanField(null=True, blank=True, default=None)
    propuesta = models.OneToOneField('financiacion_educativa.PropuestaPoliticaLibro', on_delete=models.PROTECT,
        null=True, blank=True, related_name='politica_aprobada')
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        constraints = [models.UniqueConstraint(fields=['institucion', 'version'], name='libro_politica_version_unica')]

    def clean(self):
        from .services.libro_pagos import validar_reglas
        validar_reglas(self.reglas)
        if not (self.referencia_aprobacion or '').strip():
            raise ValidationError('Se requiere evidencia de aprobacion de la politica.')

    def __str__(self):
        return f'{self.version} / desde {self.vigente_desde}'


class MovimientoLibroEducativo(EvidenciaInmutable):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ingreso = models.ForeignKey('financiacion_educativa.ConciliacionRecaudoEducativo', on_delete=models.PROTECT, related_name='movimientos_libro')
    fotografia = models.ForeignKey('financiacion_educativa.CondicionesFinancieras', on_delete=models.PROTECT, related_name='movimientos_libro')
    politica = models.ForeignKey(PoliticaLibroEducativo, on_delete=models.PROTECT)
    secuencia = models.PositiveIntegerField()
    clave = models.UUIDField()
    tipo = models.CharField(max_length=16, choices=[('FULL', 'Cuota completa'), ('PARTIAL', 'Parcial'), ('PRINCIPAL', 'Abono a capital'), ('PAYOFF', 'Pago total'), ('COMPENSATION', 'Compensacion')])
    compensa = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True, related_name='compensacion')
    importe = models.DecimalField(max_digits=16, decimal_places=2)
    capital = models.DecimalField(max_digits=16, decimal_places=2)
    intereses = models.DecimalField(max_digits=16, decimal_places=2)
    sin_destino = models.DecimalField(max_digits=16, decimal_places=2)
    fecha_bancaria = models.DateField()
    registrada_en = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    motivo = models.CharField(max_length=1000)
    payload_sha256 = models.CharField(max_length=64)
    estado_antes = models.JSONField()
    estado_despues = models.JSONField()

    class Meta(EvidenciaInmutable.Meta):
        permissions = [('compensar_libro_educativo', 'Puede compensar el ultimo movimiento del libro educativo')]
        constraints = [
            models.UniqueConstraint(fields=['fotografia', 'secuencia'], name='libro_secuencia_unica'),
            models.UniqueConstraint(fields=['ingreso', 'clave'], name='libro_reintento_unico'),
            models.CheckConstraint(condition=models.Q(importe__gt=0, capital__gte=0, intereses__gte=0, sin_destino__gte=0) & models.Q(importe=models.F('capital') + models.F('intereses') + models.F('sin_destino')), name='libro_importes_cuadran'),
            models.CheckConstraint(condition=(models.Q(tipo='COMPENSATION', compensa__isnull=False) | (~models.Q(tipo='COMPENSATION') & models.Q(compensa__isnull=True))), name='libro_compensacion_referencia'),
        ]

    def clean(self):
        if self.ingreso_id and hasattr(self.ingreso, 'aplicacion'):
            raise ValidationError('El ingreso ya tiene una aplicacion integra; no se copia al libro nuevo.')
        if self.ingreso_id and (self.ingreso.reporte.fotografia_id != self.fotografia_id or self.fecha_bancaria != self.ingreso.fecha_efectiva):
            raise ValidationError('Ingreso, version contractual y fecha bancaria no coinciden.')
        if self.politica_id and self.politica.institucion_id != self.fotografia.solicitud.institucion_id:
            raise ValidationError('La politica pertenece a otra institucion.')
        if self.compensa_id and (self.compensa.ingreso_id != self.ingreso_id or self.compensa.tipo == 'COMPENSATION'):
            raise ValidationError('La compensacion no corresponde al ingreso original.')


class LineaLibroEducativo(EvidenciaInmutable):
    movimiento = models.ForeignKey(MovimientoLibroEducativo, on_delete=models.PROTECT, related_name='lineas')
    cuota = models.ForeignKey('financiacion_educativa.CuotaAmortizacionEducativa', on_delete=models.PROTECT, null=True, blank=True, related_name='lineas_libro')
    concepto = models.CharField(max_length=16, choices=[('CAPITAL', 'Capital'), ('INTEREST', 'Intereses'), ('UNALLOCATED', 'Sin destino')])
    importe = models.DecimalField(max_digits=16, decimal_places=2)
    referencia_periodo = models.CharField(max_length=80)

    class Meta(EvidenciaInmutable.Meta):
        constraints = [models.CheckConstraint(condition=models.Q(importe__gt=0), name='libro_linea_positiva')]

    def clean(self):
        if self.cuota_id and self.cuota.fotografia_id != self.movimiento.fotografia_id:
            raise ValidationError('La cuota no pertenece a esta version contractual.')
        campo = {'CAPITAL': 'capital', 'INTEREST': 'intereses', 'UNALLOCATED': 'sin_destino'}.get(self.concepto)
        if self.movimiento_id and campo and self.importe is not None:
            existente = type(self).objects.filter(movimiento_id=self.movimiento_id, concepto=self.concepto).aggregate(
                total=models.Sum('importe', default=0))['total']
            if existente + self.importe > getattr(self.movimiento, campo):
                raise ValidationError('El asiento ya contiene su distribucion; no se pueden agregar lineas que cambien lo contabilizado.')
