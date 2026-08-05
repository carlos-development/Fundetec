from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
import hmac
import json

from django.conf import settings

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
from financiacion_educativa.services.estado_publico import (
    obtener_resultado_publico,
)
from financiacion_educativa.services.firma_zapsign import (
    procesar_webhook_firma,
)
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

ENCABEZADO_REPLAY = OpenApiParameter(
    name='Idempotent-Replayed',
    type=bool,
    location=OpenApiParameter.HEADER,
    required=False,
    response=[202],
    description=(
        'Se devuelve con valor true unicamente cuando la respuesta corresponde '
        'a una repeticion idempotente.'
    ),
)

EJEMPLO_CREACION = {
    'external_reference': 'MAT-2026-000123',
    'first_names': 'CAMILA ANDREA',
    'last_names': 'ROJAS DIAZ',
    'phone': '3001234567',
    'email': 'camila@example.com',
    'address': 'Calle 10 # 20-30',
    'document_type': 'CC',
    'document_number': '0012345678',
    'birth_date': '2002-08-15',
    'enrollment_code': 'A2D-2026-00123',
    'academic_period': '2026-2',
    'campus': 'Sede Centro',
    'schedule': 'Nocturna',
    'program_name': 'INGLES BASICO A2 DIAMANTE',
    'enrollment_date': None,
    'plan_value': '2500000.00',
    'term': 6,
}

EJEMPLO_FINANCIERO_APROBADO = {
    'currency': 'COP',
    'requested_amount': '2500000.00',
    'financed_amount': '2856778.00',
    'term_months': 6,
    'estimated_installment': '492932.00',
}

EJEMPLO_RESPUESTA = {
    'application_id': '9ed3b91b-d97f-4eaf-bff7-95a24dd51d41',
    'external_reference': EJEMPLO_CREACION['external_reference'],
    'status': 'RECEIVED',
    'course_authorized': False,
    'authorization_effective_at': None,
    'decision_reason': '',
    'created_at': '2026-07-23T20:00:00-05:00',
    'status_url': (
        'https://example.com/api/v1/financiacion-educativa/'
        'solicitudes/9ed3b91b-d97f-4eaf-bff7-95a24dd51d41/'
    ),
    'first_names': EJEMPLO_CREACION['first_names'],
    'last_names': EJEMPLO_CREACION['last_names'],
    'phone': EJEMPLO_CREACION['phone'],
    'email': EJEMPLO_CREACION['email'],
    'address': EJEMPLO_CREACION['address'],
    'document_type': EJEMPLO_CREACION['document_type'],
    'document_number': EJEMPLO_CREACION['document_number'],
    'birth_date': EJEMPLO_CREACION['birth_date'],
    'enrollment_code': EJEMPLO_CREACION['enrollment_code'],
    'academic_period': EJEMPLO_CREACION['academic_period'],
    'campus': EJEMPLO_CREACION['campus'],
    'schedule': EJEMPLO_CREACION['schedule'],
    'program_name': EJEMPLO_CREACION['program_name'],
    'course_type': EJEMPLO_CREACION['program_name'],
    'enrollment_date': None,
    'plan_value': EJEMPLO_CREACION['plan_value'],
    'term': EJEMPLO_CREACION['term'],
    'financial_terms': None,
}


def _url_estado(request, solicitud):
    from django.urls import reverse

    ruta = reverse(
        'financiacion_educativa_api:solicitud-detalle',
        kwargs={'application_id': solicitud.pk},
    )
    return request.build_absolute_uri(ruta)


def _respuesta_creacion(request, solicitud):
    resultado_publico = obtener_resultado_publico(solicitud)
    return {
        'application_id': solicitud.pk,
        'external_reference': solicitud.referencia_externa,
        'status': resultado_publico.estado,
        'course_authorized': resultado_publico.curso_autorizado,
        'authorization_effective_at': (
            resultado_publico.autorizacion_efectiva_en
        ),
        'decision_reason': resultado_publico.motivo_decision,
        'created_at': solicitud.creada_en,
        'status_url': _url_estado(request, solicitud),
        'first_names': solicitud.nombres,
        'last_names': solicitud.apellidos,
        'phone': solicitud.celular,
        'email': solicitud.correo,
        'address': solicitud.direccion,
        'document_type': solicitud.tipo_documento_estudiante,
        'document_number': solicitud.numero_documento_estudiante,
        'birth_date': solicitud.fecha_nacimiento_estudiante,
        'enrollment_code': solicitud.codigo_matricula,
        'academic_period': solicitud.periodo_academico,
        'campus': solicitud.sede,
        'schedule': solicitud.jornada,
        'program_name': solicitud.nombre_curso,
        'course_type': solicitud.nombre_curso,
        'enrollment_date': solicitud.fecha_matricula,
        'plan_value': format(solicitud.valor_plan, '.2f'),
        'term': solicitud.plazo_meses,
        'financial_terms': resultado_publico.condiciones_financieras,
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
            'Una solicitud nueva se persiste con el estado publico RECEIVED. '
            'program_name es el nombre canonico del programa; course_type es '
            'su alias compatible. Debe enviarse al menos uno y, si se envian '
            'ambos, deben coincidir. '
            'Repetir la misma clave y payload devuelve la misma solicitud con '
            'su estado publico actual, 202 y el encabezado '
            'Idempotent-Replayed: true. Para una solicitud nueva se '
            'interpreta la persona del payload siempre como estudiante. Si su '
            'fecha de nacimiento indica minoria de edad, el flujo privado '
            'exigira un tutor adulto como persona relacionada. '
            'enrollment_date permanece nulo hasta una futura firma valida. Se '
            'programa de forma privada el envio de una invitacion al correo '
            'registrado. El enlace nunca se incluye en la respuesta. El 202 '
            'confirma recepcion de la solicitud, no entrega final del correo.'
        ),
        parameters=[PARAMETRO_IDEMPOTENCIA, ENCABEZADO_REPLAY],
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
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Payload o encabezado Idempotency-Key invalido.',
                examples=[
                    OpenApiExample(
                        'Error de validacion',
                        value={
                            'error': {
                                'code': 'VALIDATION_ERROR',
                                'message': (
                                    'La solicitud contiene datos invalidos.'
                                ),
                                'fields': {
                                    'email': ['Este campo es requerido.'],
                                },
                            },
                        },
                        response_only=True,
                    ),
                ],
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Credencial ausente, invalida, inactiva o asociada a una '
                    'institucion inactiva.'
                ),
                examples=[
                    OpenApiExample(
                        'Credencial ausente',
                        value={
                            'error': {
                                'code': 'AUTHENTICATION_REQUIRED',
                                'message': (
                                    'La credencial institucional es obligatoria.'
                                ),
                            },
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        'Credencial invalida',
                        value={
                            'error': {
                                'code': 'INVALID_CREDENTIAL',
                                'message': (
                                    'La credencial institucional no es valida.'
                                ),
                            },
                        },
                        response_only=True,
                    ),
                ],
            ),
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
                    OpenApiExample(
                        'Conflicto de referencia externa',
                        value={
                            'error': {
                                'code': 'EXTERNAL_REFERENCE_CONFLICT',
                                'message': (
                                    'La referencia externa ya existe con '
                                    'otros datos.'
                                ),
                            },
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
            tipo_documento_estudiante=datos_validados.get('document_type', ''),
            numero_documento_estudiante=datos_validados.get('document_number', ''),
            fecha_nacimiento_estudiante=datos_validados.get('birth_date'),
            codigo_matricula=datos_validados.get('enrollment_code', ''),
            periodo_academico=datos_validados.get('academic_period', ''),
            sede=datos_validados.get('campus', ''),
            jornada=datos_validados.get('schedule', ''),
            valor_plan=datos_validados['plan_value'],
            plazo_meses=datos_validados['term'],
            nombre_curso=datos_validados['program_name'],
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
            'Devuelve un estado publico estable y los datos recibidos. '
            'APPROVED junto con course_authorized=true es el resultado que '
            'autoriza a la institucion a activar el curso. '
            'financial_terms solo contiene la fotografia contractual cuando '
            'course_authorized es true; de lo contrario es null. '
            'Solo la institucion propietaria puede consultarla; una credencial '
            'de otra institucion recibe 404 para prevenir IDOR.'
        ),
        responses={
            200: OpenApiResponse(
                response=DetalleSolicitudSerializer,
                examples=[
                    OpenApiExample(
                        'Consulta',
                        value={
                            **EJEMPLO_RESPUESTA,
                            'first_names': 'CAMILA ANDREA',
                            'last_names': 'ROJAS DIAZ',
                            'phone': '3001234567',
                            'email': 'camila@example.com',
                            'address': 'Calle 10 # 20-30',
                            'document_type': 'CC',
                            'document_number': '0012345678',
                            'birth_date': '2002-08-15',
                            'enrollment_code': 'A2D-2026-00123',
                            'academic_period': '2026-2',
                            'campus': 'Sede Centro',
                            'schedule': 'Nocturna',
                            'program_name': 'INGLES BASICO A2 DIAMANTE',
                            'enrollment_date': None,
                            'plan_value': '2500000.00',
                            'term': 6,
                            'course_type': 'INGLES BASICO A2 DIAMANTE',
                            'updated_at': '2026-07-23T20:00:00-05:00',
                        },
                        response_only=True,
                    ),
                    OpenApiExample(
                        'Solicitud aprobada y curso autorizado',
                        value={
                            **EJEMPLO_RESPUESTA,
                            'status': 'APPROVED',
                            'course_authorized': True,
                            'authorization_effective_at': (
                                '2026-07-30T15:45:00-05:00'
                            ),
                            'financial_terms': EJEMPLO_FINANCIERO_APROBADO,
                            'updated_at': '2026-07-30T15:45:00-05:00',
                        },
                        response_only=True,
                    ),
                ],
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Credencial ausente, invalida, inactiva o asociada a una '
                    'institucion inactiva.'
                ),
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'La solicitud no existe o pertenece a otra institucion.'
                ),
                examples=[
                    OpenApiExample(
                        'Recurso no visible',
                        value={
                            'error': {
                                'code': 'NOT_FOUND',
                                'message': (
                                    'El recurso solicitado no existe.'
                                ),
                            },
                        },
                        response_only=True,
                    ),
                ],
            ),
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
                'nombres',
                'apellidos',
                'celular',
                'correo',
                'direccion',
                'tipo_documento_estudiante',
                'numero_documento_estudiante',
                'fecha_nacimiento_estudiante',
                'codigo_matricula',
                'periodo_academico',
                'sede',
                'jornada',
                'fecha_matricula',
                'creada_en',
                'actualizada_en',
            ),
            pk=application_id,
            institucion=request.user,
        )
        return Response({
            **_respuesta_creacion(request, solicitud),
            'updated_at': solicitud.actualizada_en,
        })


@extend_schema(exclude=True)
class ZapSignEducationalWebhookAPIView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        secreto_esperado = str(
            settings.FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET
        )
        if not secreto_esperado:
            return Response(
                {'status': 'unavailable'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        header = str(
            settings.FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_HEADER
        ).strip()
        recibido = request.headers.get(header, '')
        if header.lower() == 'authorization' and recibido.lower().startswith(
            'bearer '
        ):
            recibido = recibido[7:]
        if not hmac.compare_digest(recibido, secreto_esperado):
            return Response(
                {'status': 'unauthorized'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        raw_body = request.body
        if not raw_body or len(raw_body) > 262144:
            return Response(
                {'status': 'invalid'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response(
                {'status': 'invalid'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(payload, dict):
            return Response(
                {'status': 'invalid'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            resultado = procesar_webhook_firma(
                payload=payload,
                raw_body=raw_body,
            )
        except Exception:
            return Response(
                {'status': 'retry'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {'status': resultado.estado},
            status=status.HTTP_200_OK,
        )
