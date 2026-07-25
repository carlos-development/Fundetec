from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from financiacion_educativa.models import SolicitudFinanciacionEducativa
from financiacion_educativa.services.idempotencia import (
    ConflictoIdempotencia,
    ConflictoReferenciaExterna,
)
from financiacion_educativa.services.orquestacion import (
    crear_solicitud_institucional_orquestada,
)
from financiacion_educativa.services.solicitudes import DatosSolicitudFinanciacion
from instituciones.authentication import InstitutionApiKeyAuthentication

from .errors import (
    ConflictoIdempotenciaAPI,
    ConflictoReferenciaExternaAPI,
    institutional_api_exception_handler,
)
from .permissions import IsAuthenticatedInstitution
from .serializers import (
    CrearSolicitudSerializer,
    DetalleSolicitudSerializer,
    ErrorResponseSerializer,
    SolicitudCreadaSerializer,
)


PARAMETRO_IDEMPOTENCIA = OpenApiParameter(
    name='Idempotency-Key',
    type=str,
    location=OpenApiParameter.HEADER,
    required=True,
    description=(
        'Clave opaca de hasta 255 caracteres. Su hash se aisla por institucion '
        'y permite repetir la misma peticion sin crear duplicados.'
    ),
)

EJEMPLO_CREACION = {
    'external_reference': 'CURSO-2026-000123',
    'first_names': 'JUAN DAVID',
    'last_names': 'PEREZ GOMEZ',
    'phone': '3001234567',
    'email': 'juan@example.com',
    'address': 'Calle 10 # 20-30',
    'plan_value': '2500000.00',
    'term': 6,
    'course_type': 'Intensivo de programacion',
}

EJEMPLO_RESPUESTA = {
    'application_id': '9ed3b91b-d97f-4eaf-bff7-95a24dd51d41',
    'external_reference': 'CURSO-2026-000123',
    'status': 'PENDING_USER_REGISTRATION',
    'created_at': '2026-07-23T20:00:00-05:00',
    'status_url': (
        'https://example.com/api/v1/financiacion-educativa/'
        'solicitudes/9ed3b91b-d97f-4eaf-bff7-95a24dd51d41/'
    ),
}


def _url_estado(request, solicitud):
    from django.urls import reverse

    ruta = reverse(
        'financiacion_educativa_api:solicitud-detalle',
        kwargs={'application_id': solicitud.pk},
    )
    return request.build_absolute_uri(ruta)


def _respuesta_creacion(request, solicitud):
    return {
        'application_id': solicitud.pk,
        'external_reference': solicitud.referencia_externa,
        'status': solicitud.estado,
        'created_at': solicitud.creada_en,
        'status_url': _url_estado(request, solicitud),
    }


class InstitutionalAPIView(APIView):
    authentication_classes = [InstitutionApiKeyAuthentication]
    permission_classes = [IsAuthenticatedInstitution]
    parser_classes = [JSONParser]

    def get_exception_handler(self):
        return institutional_api_exception_handler


class SolicitudListCreateAPIView(InstitutionalAPIView):
    @extend_schema(
        tags=['Financiacion educativa'],
        operation_id='crear_solicitud_financiacion_educativa',
        summary='Crear una solicitud institucional',
        description=(
            'Crea una solicitud en estado PENDING_USER_REGISTRATION. Repetir la '
            'misma clave y payload devuelve la misma solicitud con 202 y el '
            'encabezado Idempotent-Replayed: true. Para una solicitud nueva se '
            'programa de forma privada el envio de una invitacion al correo '
            'registrado. El enlace nunca se incluye en la respuesta.'
        ),
        parameters=[PARAMETRO_IDEMPOTENCIA],
        request=CrearSolicitudSerializer,
        responses={
            202: OpenApiResponse(
                response=SolicitudCreadaSerializer,
                description='Solicitud aceptada o repeticion idempotente.',
                examples=[
                    OpenApiExample(
                        'Solicitud aceptada',
                        value=EJEMPLO_RESPUESTA,
                        response_only=True,
                    ),
                    OpenApiExample(
                        'Repeticion idempotente',
                        value=EJEMPLO_RESPUESTA,
                        description=(
                            'No crea registros adicionales y agrega '
                            'Idempotent-Replayed: true.'
                        ),
                        response_only=True,
                    ),
                ],
            ),
            400: ErrorResponseSerializer,
            401: ErrorResponseSerializer,
            409: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Conflicto por reutilizar la clave con otro payload o por '
                    'referencia externa incompatible.'
                ),
                examples=[
                    OpenApiExample(
                        'Conflicto de idempotencia',
                        value={
                            'error': {
                                'code': 'IDEMPOTENCY_CONFLICT',
                                'message': (
                                    'La clave de idempotencia ya fue usada '
                                    'con otros datos.'
                                ),
                            }
                        },
                        response_only=True,
                    ),
                ],
            ),
        },
        examples=[
            OpenApiExample(
                'Creacion institucional',
                value=EJEMPLO_CREACION,
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = CrearSolicitudSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        clave_idempotencia = request.headers.get('Idempotency-Key', '').strip()
        if not clave_idempotencia:
            raise ValidationError({
                'Idempotency-Key': ['Este encabezado es obligatorio.'],
            })

        datos_validados = serializer.validated_data
        datos = DatosSolicitudFinanciacion(
            referencia_externa=datos_validados['external_reference'],
            nombres=datos_validados['first_names'],
            apellidos=datos_validados['last_names'],
            celular=datos_validados['phone'],
            correo=datos_validados['email'],
            direccion=datos_validados['address'],
            valor_plan=datos_validados['plan_value'],
            plazo_meses=datos_validados['term'],
            nombre_curso=datos_validados['course_type'],
            tipo_curso='',
            canal_origen='INSTITUTION_API',
            correlation_id='',
            ip_origen=request.META.get('REMOTE_ADDR'),
            user_agent_origen=request.META.get('HTTP_USER_AGENT', ''),
        )
        try:
            resultado = crear_solicitud_institucional_orquestada(
                institucion=request.user,
                clave_idempotencia=clave_idempotencia,
                datos=datos,
            )
        except ConflictoIdempotencia as exc:
            raise ConflictoIdempotenciaAPI() from exc
        except ConflictoReferenciaExterna as exc:
            raise ConflictoReferenciaExternaAPI() from exc

        headers = {}
        if resultado.repetida:
            headers['Idempotent-Replayed'] = 'true'
        return Response(
            _respuesta_creacion(request, resultado.solicitud),
            status=status.HTTP_202_ACCEPTED,
            headers=headers,
        )


class SolicitudDetalleAPIView(InstitutionalAPIView):
    @extend_schema(
        tags=['Financiacion educativa'],
        operation_id='consultar_solicitud_financiacion_educativa',
        summary='Consultar una solicitud propia',
        description=(
            'Devuelve solo el estado y datos operativos mínimos. Solicitudes de '
            'otra institucion se responden como recurso no encontrado.'
        ),
        responses={
            200: OpenApiResponse(
                response=DetalleSolicitudSerializer,
                examples=[
                    OpenApiExample(
                        'Consulta',
                        value={
                            **EJEMPLO_RESPUESTA,
                            'plan_value': '2500000.00',
                            'term': 6,
                            'course_type': 'Intensivo de programacion',
                            'updated_at': '2026-07-23T20:00:00-05:00',
                        },
                        response_only=True,
                    ),
                ],
            ),
            401: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
        },
    )
    def get(self, request, application_id):
        solicitud = get_object_or_404(
            SolicitudFinanciacionEducativa.objects.only(
                'id',
                'institucion_id',
                'referencia_externa',
                'estado',
                'valor_plan',
                'plazo_meses',
                'nombre_curso',
                'creada_en',
                'actualizada_en',
            ),
            pk=application_id,
            institucion=request.user,
        )
        return Response({
            **_respuesta_creacion(request, solicitud),
            'plan_value': format(solicitud.valor_plan, '.2f'),
            'term': solicitud.plazo_meses,
            'course_type': solicitud.nombre_curso,
            'updated_at': solicitud.actualizada_en,
        })
