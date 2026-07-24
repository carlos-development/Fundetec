from drf_spectacular.extensions import OpenApiAuthenticationExtension


class InstitutionApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = 'instituciones.authentication.InstitutionApiKeyAuthentication'
    name = 'InstitutionApiKey'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': (
                'Formato: `Authorization: ApiKey <prefijo>.<secreto>`. '
                'El secreto solo se entrega al emitir o rotar la credencial.'
            ),
        }
