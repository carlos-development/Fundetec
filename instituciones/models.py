import uuid

from django.conf import settings
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


class MembresiaInstitucion(models.Model):
    class Rol(models.TextChoices):
        INSTITUTION_ADMIN = 'INSTITUTION_ADMIN', 'Administrador institucional'
        INSTITUTION_ANALYST = 'INSTITUTION_ANALYST', 'Analista institucional'
        INSTITUTION_READ_ONLY = 'INSTITUTION_READ_ONLY', 'Solo lectura'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='membresias_institucionales',
    )
    institucion = models.ForeignKey(
        Institucion,
        on_delete=models.PROTECT,
        related_name='membresias',
    )
    rol = models.CharField(max_length=30, choices=Rol.choices)
    activa = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='membresias_institucionales_creadas',
    )
    invitado_en = models.DateTimeField(default=timezone.now, editable=False)
    activado_en = models.DateTimeField(
        default=timezone.now,
        null=True,
        blank=True,
        editable=False,
    )
    desactivado_en = models.DateTimeField(null=True, blank=True, editable=False)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['institucion__nombre_comercial', 'usuario__email']
        verbose_name = 'Membresia institucional'
        verbose_name_plural = 'Membresias institucionales'
        constraints = [
            models.UniqueConstraint(
                fields=['usuario', 'institucion'],
                name='uniq_membresia_usuario_institucion',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        activa=True,
                        activado_en__isnull=False,
                        desactivado_en__isnull=True,
                    )
                    | models.Q(activa=False)
                ),
                name='membresia_activa_fechas_consistentes',
            ),
        ]
        indexes = [
            models.Index(
                fields=['institucion', 'activa', 'rol'],
                name='memb_inst_activa_rol_idx',
            ),
            models.Index(
                fields=['usuario', 'activa'],
                name='memb_usuario_activa_idx',
            ),
        ]

    def clean(self):
        super().clean()
        if self.activa and self.institucion_id and not self.institucion.activa:
            raise ValidationError({
                'institucion': (
                    'Una membresia activa requiere una institucion activa.'
                ),
            })

    def __str__(self):
        return (
            f'{self.usuario} - {self.institucion.nombre_comercial} '
            f'({self.get_rol_display()})'
        )


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
