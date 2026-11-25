from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, router, transaction

from instituciones.models import Institucion

from .models_recaudos import EvidenciaInmutable


class ConvenioRepartoInteresEducativo(EvidenciaInmutable):
    institucion = models.ForeignKey(Institucion, on_delete=models.PROTECT)
    version = models.CharField(max_length=80)
    vigente_desde = models.DateField()
    vigente_hasta = models.DateField()
    porcentaje_aprobado = models.DecimalField(max_digits=7, decimal_places=4)
    referencia_aprobacion = models.CharField(max_length=200)
    aprobado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta(EvidenciaInmutable.Meta):
        verbose_name = 'convenio de reparto de intereses educativos'
        verbose_name_plural = 'convenios de reparto de intereses educativos'
        constraints = [
            models.UniqueConstraint(fields=['institucion', 'version'], name='reparto_interes_version_unica'),
            models.CheckConstraint(condition=models.Q(vigente_desde__lte=models.F('vigente_hasta')),
                                   name='reparto_interes_vigencia_valida'),
            models.CheckConstraint(condition=models.Q(porcentaje_aprobado__gte=0, porcentaje_aprobado__lte=100),
                                   name='reparto_interes_porcentaje_valido'),
        ]
        indexes = [models.Index(fields=['institucion', 'vigente_desde', 'vigente_hasta'], name='reparto_interes_vigencia_idx')]

    @property
    def porcentaje_institucion(self):
        return Decimal('100') - self.porcentaje_aprobado

    def clean(self):
        super().clean()
        self.version = (self.version or '').strip()
        self.referencia_aprobacion = (self.referencia_aprobacion or '').strip()
        if not self.version or not self.referencia_aprobacion:
            raise ValidationError('Se requieren la versión y la referencia de aprobación del convenio.')
        if self.institucion_id and self.vigente_desde and self.vigente_hasta and type(self).objects.filter(
            institucion_id=self.institucion_id, vigente_desde__lte=self.vigente_hasta,
            vigente_hasta__gte=self.vigente_desde,
        ).exclude(pk=self.pk).exists():
            raise ValidationError('La institución ya tiene un convenio de reparto que se solapa con la vigencia.')

    def save(self, *args, **kwargs):
        using = kwargs.get('using') or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            if self.institucion_id:
                Institucion.objects.using(using).select_for_update().get(pk=self.institucion_id)
            return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.institucion} · {self.version}'
