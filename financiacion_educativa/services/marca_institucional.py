from urllib.parse import urljoin, urlsplit

from django.conf import settings


# Nombre accesible, archivo estático y ancho en píxeles para correo.
LOGOS_JOWALTH = (
    ('FUNDETEC', 'images/simulador/Fundetec_logo.png', 96),
    ('Loopiter', 'images/simulador/Loopiter_logo.png', 88),
)


def logos_de_solicitud(solicitud):
    institucion = getattr(solicitud, 'institucion', None)
    config = getattr(institucion, 'configuracion_anexo_matricula', None)
    return LOGOS_JOWALTH if getattr(config, 'perfil', None) == 'JOWALTH_ACADEMY' else ()


def logos_correo_de_solicitud(solicitud):
    logos = logos_de_solicitud(solicitud)
    if not logos:
        return ()
    # Usar nombres estables en correos que pueden abrirse tras otro despliegue.
    base = urlsplit(settings.BRAND_PUBLIC_BASE_URL)
    if (base.scheme != 'https' or not base.hostname or base.username or base.password
            or base.path not in ('', '/') or base.query or base.fragment):
        return ()
    raiz = urlsplit(urljoin(base.geturl(), settings.STATIC_URL))
    if raiz.scheme != 'https' or not raiz.hostname or raiz.username or raiz.password or raiz.query or raiz.fragment:
        return ()
    return tuple((nombre, urljoin(raiz.geturl(), archivo), ancho) for nombre, archivo, ancho in logos)

