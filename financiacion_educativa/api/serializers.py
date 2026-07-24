from collections.abc import Mapping
from decimal import Decimal

from rest_framework import serializers


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
    plan_value = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal('0.01'),
        coerce_to_string=True,
    )
    term = serializers.IntegerField(min_value=1, max_value=32767)
    course_type = serializers.CharField(max_length=200)

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


class SolicitudCreadaSerializer(serializers.Serializer):
    application_id = serializers.UUIDField()
    external_reference = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    status_url = serializers.URLField()


class DetalleSolicitudSerializer(SolicitudCreadaSerializer):
    plan_value = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        coerce_to_string=True,
    )
    term = serializers.IntegerField()
    course_type = serializers.CharField()
    updated_at = serializers.DateTimeField()


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.JSONField(required=False)


class ErrorResponseSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()
