from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from instituciones.models import CredencialAPIInstitucion


class ErrorAutenticacionInstitucional(AuthenticationFailed):
    api_code = 'INVALID_CREDENTIAL'


class AutenticacionInstitucionalRequerida(ErrorAutenticacionInstitucional):
    api_code = 'AUTHENTICATION_REQUIRED'
    default_detail = 'La credencial institucional es obligatoria.'


class CredencialInstitucionalInvalida(ErrorAutenticacionInstitucional):
    api_code = 'INVALID_CREDENTIAL'
    default_detail = 'La credencial institucional no es valida.'


class CredencialInstitucionalInactiva(ErrorAutenticacionInstitucional):
    api_code = 'CREDENTIAL_INACTIVE'
    default_detail = 'La credencial institucional esta inactiva o vencida.'


class InstitucionInactiva(ErrorAutenticacionInstitucional):
    api_code = 'INSTITUTION_INACTIVE'
    default_detail = 'La institucion se encuentra inactiva.'


class InstitutionApiKeyAuthentication(BaseAuthentication):
    keyword = 'ApiKey'

    def authenticate(self, request):
        encabezado = get_authorization_header(request).split()
        if not encabezado:
            raise AutenticacionInstitucionalRequerida()
        if len(encabezado) != 2 or encabezado[0].decode('ascii', 'ignore') != self.keyword:
            raise CredencialInstitucionalInvalida()

        try:
            token = encabezado[1].decode('utf-8')
            prefijo, separador, secreto = token.partition('.')
        except UnicodeError as exc:
            raise CredencialInstitucionalInvalida() from exc
        if not separador or not prefijo or not secreto:
            raise CredencialInstitucionalInvalida()

        try:
            credencial = CredencialAPIInstitucion.objects.select_related(
                'institucion'
            ).get(prefijo_clave=prefijo)
        except CredencialAPIInstitucion.DoesNotExist as exc:
            raise CredencialInstitucionalInvalida() from exc

        if not credencial.verificar_secreto(secreto):
            raise CredencialInstitucionalInvalida()
        if not credencial.vigente:
            raise CredencialInstitucionalInactiva()
        if not credencial.institucion.activa:
            raise InstitucionInactiva()

        ahora = timezone.now()
        CredencialAPIInstitucion.objects.filter(pk=credencial.pk).update(
            ultimo_uso_en=ahora
        )
        credencial.ultimo_uso_en = ahora
        return credencial.institucion, credencial

    def authenticate_header(self, request):
        return self.keyword
