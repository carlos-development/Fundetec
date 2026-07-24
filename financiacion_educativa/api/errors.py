import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler


logger = logging.getLogger(__name__)


class ConflictoAPI(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = 'La peticion entra en conflicto con un recurso existente.'
    api_code = 'CONFLICT'


class ConflictoIdempotenciaAPI(ConflictoAPI):
    default_detail = 'La clave de idempotencia ya fue usada con otros datos.'
    api_code = 'IDEMPOTENCY_CONFLICT'


class ConflictoReferenciaExternaAPI(ConflictoAPI):
    default_detail = 'La referencia externa ya existe con otros datos.'
    api_code = 'EXTERNAL_REFERENCE_CONFLICT'


def _valor_json(valor):
    if isinstance(valor, dict):
        return {clave: _valor_json(detalle) for clave, detalle in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_valor_json(detalle) for detalle in valor]
    return str(valor)


def _respuesta_error(*, code, message, status_code, fields=None):
    error = {'code': code, 'message': message}
    if fields:
        error['fields'] = _valor_json(fields)
    return Response({'error': error}, status=status_code)


def institutional_api_exception_handler(exc, context):
    if isinstance(exc, DjangoValidationError):
        fields = getattr(exc, 'message_dict', None)
        if fields is None:
            fields = {'non_field_errors': exc.messages}
        return _respuesta_error(
            code='VALIDATION_ERROR',
            message='La solicitud contiene datos invalidos.',
            fields=fields,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    response = exception_handler(exc, context)
    if response is None:
        logger.error(
            'Error interno no controlado en API institucional: %s',
            exc.__class__.__name__,
        )
        return _respuesta_error(
            code='INTERNAL_ERROR',
            message='Ocurrio un error interno.',
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    api_code = getattr(exc, 'api_code', None)
    if api_code:
        return _respuesta_error(
            code=api_code,
            message=str(exc.detail),
            status_code=response.status_code,
        )
    if isinstance(exc, (ValidationError, DjangoValidationError)):
        return _respuesta_error(
            code='VALIDATION_ERROR',
            message='La solicitud contiene datos invalidos.',
            fields=response.data,
            status_code=response.status_code,
        )
    if isinstance(exc, NotFound):
        return _respuesta_error(
            code='NOT_FOUND',
            message='El recurso solicitado no existe.',
            status_code=response.status_code,
        )

    codigos = {
        status.HTTP_401_UNAUTHORIZED: 'INVALID_CREDENTIAL',
        status.HTTP_403_FORBIDDEN: 'FORBIDDEN',
        status.HTTP_404_NOT_FOUND: 'NOT_FOUND',
        status.HTTP_405_METHOD_NOT_ALLOWED: 'METHOD_NOT_ALLOWED',
    }
    return _respuesta_error(
        code=codigos.get(response.status_code, 'API_ERROR'),
        message=str(getattr(exc, 'detail', 'La peticion no pudo procesarse.')),
        status_code=response.status_code,
    )
