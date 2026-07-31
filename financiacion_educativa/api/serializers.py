from collections.abc import Mapping
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from financiacion_educativa.choices import (
    EstadoPublicoSolicitud,
    MotivoDecisionRevisionEducativa,
    TipoDocumentoIdentidad,
)


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            desconocidos = sorted(set(data) - set(self.fields))
            if desconocidos:
                raise serializers.ValidationError({
                    campo: ['Este campo no esta permitido.']
                    for campo in desconocidos
                })
        return super().to_internal_value(data)


class CrearSolicitudSerializer(StrictSerializer):
    external_reference = serializers.CharField(max_length=120)
    first_names = serializers.CharField(max_length=160)
    last_names = serializers.CharField(max_length=160)
    phone = serializers.RegexField(regex=r'^\+?[0-9]{7,20}$', max_length=21)
    email = serializers.EmailField()
    address = serializers.CharField(max_length=255)
    document_type = serializers.ChoiceField(
        choices=TipoDocumentoIdentidad.choices,
        required=False,
        allow_blank=True,
    )
    document_number = serializers.CharField(
        max_length=40,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )
    birth_date = serializers.DateField(required=False, allow_null=True)
    enrollment_code = serializers.CharField(
        max_length=120,
        required=False,
        allow_blank=True,
    )
    academic_period = serializers.CharField(
        max_length=80,
        required=False,
        allow_blank=True,
    )
    campus = serializers.CharField(
        max_length=160,
        required=False,
        allow_blank=True,
    )
    schedule = serializers.CharField(
        max_length=80,
        required=False,
        allow_blank=True,
    )
    plan_value = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        coerce_to_string=True,
    )
    term = serializers.IntegerField(min_value=1, max_value=32767)
    course_type = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=False,
        help_text=(
            'Alias compatible de program_name. Debe enviarse este campo o '
            'program_name; si se envian ambos, deben coincidir.'
        ),
    )
    program_name = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=False,
        help_text=(
            'Nombre canonico del programa. Debe enviarse este campo o '
            'course_type; si se envian ambos, deben coincidir.'
        ),
    )
    enrollment_date = serializers.DateField(required=False, allow_null=True)

    def to_internal_value(self, data):
        if (
            isinstance(data, Mapping)
            and 'plan_value' in data
            and not isinstance(data['plan_value'], str)
        ):
            raise serializers.ValidationError({
                'plan_value': [
                    'Envie el valor monetario como texto decimal.',
                ],
            })
        return super().to_internal_value(data)

    def validate_document_type(self, value):
        return value.strip().upper()

    def validate_document_number(self, value):
        import re

        normalizado = re.sub(r'[^A-Z0-9]', '', value.strip().upper())
        if value and not normalizado:
            raise serializers.ValidationError(
                'El numero de documento no contiene caracteres validos.'
            )
        return normalizado

    def validate_birth_date(self, value):
        if value and value > timezone.localdate():
            raise serializers.ValidationError(
                'La fecha de nacimiento no puede ser futura.'
            )
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        programa = attrs.get('program_name')
        alias = attrs.get('course_type')
        if not programa and not alias:
            raise serializers.ValidationError({
                'program_name': 'Este campo es obligatorio.',
            })
        if programa and alias and programa.strip() != alias.strip():
            raise serializers.ValidationError({
                'program_name': (
                    'program_name y course_type deben representar el mismo programa.'
                ),
            })
        attrs['program_name'] = (programa or alias).strip()

        identidad = (
            attrs.get('document_type'),
            attrs.get('document_number'),
            attrs.get('birth_date'),
        )
        if any(identidad) and not all(identidad):
            raise serializers.ValidationError({
                'document_type': (
                    'document_type, document_number y birth_date '
                    'deben enviarse juntos.'
                ),
            })
        if attrs.get('enrollment_date') is not None:
            raise serializers.ValidationError({
                'enrollment_date': (
                    'La fecha de matricula se registra al confirmar '
                    'la firma valida del pagare.'
                ),
            })
        return attrs


MOTIVOS_DECISION_PUBLICOS = (
    ('', 'Sin motivo publico'),
    (
        MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION,
        MotivoDecisionRevisionEducativa.INCOMPLETE_INFORMATION.label,
    ),
    (
        MotivoDecisionRevisionEducativa.UNREADABLE_DOCUMENT,
        MotivoDecisionRevisionEducativa.UNREADABLE_DOCUMENT.label,
    ),
    (
        MotivoDecisionRevisionEducativa.IDENTITY_MISMATCH,
        MotivoDecisionRevisionEducativa.IDENTITY_MISMATCH.label,
    ),
    (
        MotivoDecisionRevisionEducativa.GUARDIANSHIP_NOT_VERIFIED,
        MotivoDecisionRevisionEducativa.GUARDIANSHIP_NOT_VERIFIED.label,
    ),
    (
        MotivoDecisionRevisionEducativa.ENROLLMENT_NOT_VERIFIED,
        MotivoDecisionRevisionEducativa.ENROLLMENT_NOT_VERIFIED.label,
    ),
    (
        MotivoDecisionRevisionEducativa.OTHER,
        MotivoDecisionRevisionEducativa.OTHER.label,
    ),
)


class TerminosFinancierosSerializer(serializers.Serializer):
    currency = serializers.ChoiceField(
        choices=(('COP', 'Peso colombiano'),),
        help_text='Moneda de todos los valores financieros.',
    )
    requested_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
        help_text='Valor del plan solicitado, redondeado al peso.',
    )
    financed_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
        help_text='Capital total financiado, incluidos los cargos aplicables.',
    )
    term_months = serializers.IntegerField(
        min_value=1,
        max_value=32767,
    )
    estimated_installment = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
        help_text='Cuota fija informativa calculada por el motor financiero.',
    )


class SolicitudCreadaSerializer(serializers.Serializer):
    application_id = serializers.UUIDField()
    external_reference = serializers.CharField()
    status = serializers.ChoiceField(choices=EstadoPublicoSolicitud.choices)
    course_authorized = serializers.BooleanField(
        help_text=(
            'Solo puede ser true cuando status es APPROVED y existe una '
            'decision contractual aprobada con fotografia financiera.'
        ),
    )
    authorization_effective_at = serializers.DateTimeField(
        allow_null=True,
        help_text=(
            'Fecha efectiva de la autorizacion contractual; null mientras '
            'course_authorized sea false.'
        ),
    )
    decision_reason = serializers.ChoiceField(
        choices=MOTIVOS_DECISION_PUBLICOS,
        allow_blank=True,
        help_text=(
            'Codigo publico de correccion o rechazo. Es una cadena vacia '
            'cuando no existe un motivo publico.'
        ),
    )
    created_at = serializers.DateTimeField()
    status_url = serializers.URLField()
    first_names = serializers.CharField()
    last_names = serializers.CharField()
    phone = serializers.CharField()
    email = serializers.EmailField()
    address = serializers.CharField()
    document_type = serializers.CharField(allow_blank=True)
    document_number = serializers.CharField(allow_blank=True)
    birth_date = serializers.DateField(allow_null=True)
    enrollment_code = serializers.CharField(allow_blank=True)
    academic_period = serializers.CharField(allow_blank=True)
    campus = serializers.CharField(allow_blank=True)
    schedule = serializers.CharField(allow_blank=True)
    program_name = serializers.CharField()
    course_type = serializers.CharField()
    enrollment_date = serializers.DateField(allow_null=True)
    plan_value = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
    )
    term = serializers.IntegerField()
    financial_terms = TerminosFinancierosSerializer(
        allow_null=True,
        help_text=(
            'Condiciones contractuales de la fotografia financiera aprobada; '
            'null mientras course_authorized sea false.'
        ),
    )


class DetalleSolicitudSerializer(SolicitudCreadaSerializer):
    updated_at = serializers.DateTimeField()


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.JSONField(required=False)


class ErrorResponseSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()
