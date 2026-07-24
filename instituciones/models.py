import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Institucion(models.Model):
    class TipoIdentificacionTributaria(models.TextChoices):
        NIT = 'NIT', 'NIT'
        OTRO = 'OTRO', 'Otro'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    nombre_comercial = models.CharField(max_length=160)
    razon_social = models.CharField(max_length=200)
    tipo_identificacion_tributaria = models.CharField(
        max_length=20,
        choices=TipoIdentificacionTributaria.choices,
        default=TipoIdentificacionTributaria.NIT,
    )
    numero_identificacion_tributaria = models.CharField(max_length=40)
    activa = models.BooleanField(default=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['nombre_comercial']
        verbose_name = 'Institucion'
        verbose_name_plural = 'Instituciones'
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'tipo_identificacion_tributaria',
                    'numero_identificacion_tributaria',
                ],
                name='uniq_institucion_identificacion',
            ),
        ]
        indexes = [
            models.Index(fields=['activa', 'nombre_comercial'], name='inst_activa_nombre_idx'),
        ]

    def clean(self):
        super().clean()
        self.numero_identificacion_tributaria = (
            self.numero_identificacion_tributaria or ''
        ).strip().upper()
        if not self.numero_identificacion_tributaria:
            raise ValidationError({
                'numero_identificacion_tributaria': 'La identificacion tributaria es obligatoria.',
            })

    def __str__(self):
        return self.nombre_comercial


class CredencialAPIInstitucion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    institucion = models.ForeignKey(
        Institucion,
        on_delete=models.CASCADE,
        related_name='credenciales_api',
    )
    nombre = models.CharField(max_length=100)
    prefijo_clave = models.CharField(max_length=16, db_index=True)
    secreto_hash = models.CharField(max_length=128)
    alcances = models.JSONField(default=list, blank=True)
    activa = models.BooleanField(default=True)
    expira_en = models.DateTimeField(null=True, blank=True)
    ultimo_uso_en = models.DateTimeField(null=True, blank=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['institucion__nombre_comercial', 'nombre']
        verbose_name = 'Credencial API de institucion'
        verbose_name_plural = 'Credenciales API de instituciones'
        constraints = [
            models.UniqueConstraint(
                fields=['institucion', 'nombre'],
                name='uniq_credencial_nombre_institucion',
            ),
            models.UniqueConstraint(
                fields=['prefijo_clave'],
                name='uniq_credencial_prefijo',
            ),
        ]
        indexes = [
            models.Index(fields=['institucion', 'activa'], name='cred_inst_activa_idx'),
        ]

    def clean(self):
        super().clean()
        if self.expira_en and self.expira_en <= timezone.now():
            raise ValidationError({'expira_en': 'La fecha de expiracion debe ser futura.'})
        if self.secreto_hash and not self.secreto_hash.startswith(
            ('pbkdf2_', 'argon2', 'bcrypt', 'scrypt')
        ):
            raise ValidationError({
                'secreto_hash': 'El secreto debe almacenarse mediante el hasher de Django.',
            })

    def establecer_secreto(self, secreto):
        if not secreto:
            raise ValidationError({'secreto': 'El secreto no puede estar vacio.'})
        self.secreto_hash = make_password(secreto)

    def verificar_secreto(self, secreto):
        return bool(self.secreto_hash) and check_password(secreto, self.secreto_hash)

    @property
    def vigente(self):
        return self.activa and (self.expira_en is None or self.expira_en > timezone.now())

    def __str__(self):
        return f'{self.institucion.nombre_comercial} - {self.nombre}'
