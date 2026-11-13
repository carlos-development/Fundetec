"""Explicit policy approvals and a closed, single-institution pilot."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from .models_recaudos import EvidenciaInmutable, EvidenciaManager


class PropuestaPoliticaLibro(EvidenciaInmutable):
    institucion = models.ForeignKey('instituciones.Institucion', on_delete=models.PROTECT)
    version = models.CharField(max_length=80, validators=[RegexValidator(r'^[A-Z0-9._-]+$')])
    reglas = models.JSONField()
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField(null=True, blank=True)
    es_sintetica = models.BooleanField(default=True)
    respaldo = models.CharField(max_length=1000)
    preparada_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    creada_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        constraints = [
            models.UniqueConstraint(fields=['institucion', 'version'], name='libro_propuesta_version_unica'),
            models.CheckConstraint(condition=models.Q(vigente_hasta__isnull=True) | models.Q(vigente_hasta__gte=models.F('vigente_desde')), name='libro_intervalo_propuesta_valido'),
        ]
        permissions = [
            ('preparar_politicas_libro', 'Puede preparar politicas del libro'),
            ('revisar_politicas_libro', 'Puede revisar politicas del libro'),
            ('aprobar_politicas_libro', 'Puede aprobar o retirar politicas del libro'),
            ('controlar_piloto_libro', 'Puede activar o apagar el piloto del libro'),
            ('gestionar_alcances_libro', 'Puede asignar alcances institucionales del libro'),
        ]

    def clean(self):
        from .services.libro_pagos import validar_reglas
        validar_reglas(self.reglas)

    def __str__(self):
        return self.version


class EventoPoliticaLibro(EvidenciaInmutable):
    propuesta = models.ForeignKey(PropuestaPoliticaLibro, on_delete=models.PROTECT, related_name='eventos')
    tipo = models.CharField(max_length=16, choices=[('REVIEWED', 'Revisada'), ('APPROVED', 'Aprobada'), ('REJECTED', 'Rechazada'), ('RETIRED', 'Retirada para nuevas escrituras')])
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    respaldo = models.CharField(max_length=1000)
    payload_sha256 = models.CharField(max_length=64)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        constraints = [models.UniqueConstraint(fields=['propuesta', 'tipo'], name='libro_evento_politica_unico')]


class EventoAlcanceLibro(EvidenciaInmutable):
    class Accion(models.TextChoices):
        PREPARE = 'PREPARE', 'Preparar'
        REVIEW = 'REVIEW', 'Revisar'
        APPROVE = 'APPROVE', 'Aprobar'
        POST = 'POST', 'Contabilizar'
        COMPENSATE = 'COMPENSATE', 'Compensar'
        CONTROL = 'CONTROL', 'Controlar piloto'

    institucion = models.ForeignKey('instituciones.Institucion', on_delete=models.PROTECT)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    accion = models.CharField(max_length=16, choices=Accion.choices)
    habilitado = models.BooleanField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    motivo = models.CharField(max_length=1000)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        indexes = [models.Index(fields=['institucion', 'usuario', 'accion', '-id'], name='libro_alcance_actual_idx')]


class ControlPilotoLibro(models.Model):
    """Singleton lock/projection. Only the official control service may change it."""
    objects = EvidenciaManager()
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    institucion = models.ForeignKey('instituciones.Institucion', on_delete=models.PROTECT, null=True, blank=True)
    activo = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        base_manager_name = 'objects'
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name='libro_control_unico'),
            models.CheckConstraint(condition=models.Q(activo=False) | models.Q(institucion__isnull=False), name='libro_piloto_requiere_institucion'),
        ]

    def save(self, *args, **kwargs):
        if not getattr(self, '_servicio_control', False):
            raise ValidationError('Use el control oficial del piloto.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('El control y su auditoria deben conservarse.')


class EventoControlPilotoLibro(EvidenciaInmutable):
    institucion = models.ForeignKey('instituciones.Institucion', on_delete=models.PROTECT)
    revision = models.PositiveIntegerField(unique=True)
    activo = models.BooleanField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    motivo = models.CharField(max_length=1000)
    creado_en = models.DateTimeField(auto_now_add=True)
