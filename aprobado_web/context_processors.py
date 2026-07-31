from django.conf import settings


def public_whatsapp_processor(request):
    numero_normalizado = _normalizar_whatsapp(
        getattr(settings, 'WHATSAPP_SUPPORT_NUMBER', '')
    )

    return {
        'whatsapp_support_number': numero_normalizado,
        'whatsapp_support_href': (
            f'https://wa.me/{numero_normalizado}' if numero_normalizado else ''
        ),
        'whatsapp_support_display': _formatear_whatsapp(numero_normalizado),
        'whatsapp_default_message': getattr(
            settings,
            'WHATSAPP_DEFAULT_MESSAGE',
            '',
        ),
        'whatsapp_floating_enabled': bool(
            getattr(settings, 'WHATSAPP_FLOATING_ENABLED', False)
            and numero_normalizado
        ),
    }


def _normalizar_whatsapp(value):
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    if not digits:
        return ''
    if len(digits) == 10:
        return f'57{digits}'
    if len(digits) == 12 and digits.startswith('57'):
        return digits
    return digits


def _formatear_whatsapp(digits):
    if not digits:
        return ''
    if len(digits) == 12 and digits.startswith('57'):
        country = digits[:2]
        local = digits[2:]
        if len(local) == 10:
            return f'+{country} {local[:3]} {local[3:6]} {local[6:]}'
    return f'+{digits}'


def brand_processor(request):
    public_base_url = getattr(
        settings,
        'BRAND_PUBLIC_BASE_URL',
        '',
    ).rstrip('/')
    logo_path = getattr(
        settings,
        'BRAND_LOGO',
        'images/fundetec-logo.png',
    )
    logo_dark_path = getattr(settings, 'BRAND_LOGO_DARK', logo_path)
    favicon_path = getattr(settings, 'BRAND_FAVICON', logo_path)
    education_document_max_bytes = getattr(
        settings,
        'FINANCIACION_EDUCATIVA_DOCUMENT_MAX_BYTES',
        10 * 1024 * 1024,
    )

    return {
        'brand': {
            'name': getattr(settings, 'BRAND_NAME', 'FUNDETEC'),
            'legal_name': getattr(
                settings,
                'BRAND_LEGAL_NAME',
                'FUNDETEC',
            ),
            'primary_color': getattr(
                settings,
                'BRAND_PRIMARY_COLOR',
                '#0B4EA2',
            ),
            'secondary_color': getattr(
                settings,
                'BRAND_SECONDARY_COLOR',
                '#FFC400',
            ),
            'accent_color': getattr(
                settings,
                'BRAND_ACCENT_COLOR',
                '#E7191A',
            ),
            'dark_color': getattr(
                settings,
                'BRAND_DARK_COLOR',
                '#083B7A',
            ),
            'logo': logo_path,
            'logo_dark': logo_dark_path,
            'favicon': favicon_path,
            'logo_absolute_url': (
                f'{public_base_url}/static/{logo_path}'
                if public_base_url
                else ''
            ),
        },
        'education_brand': {
            'name': getattr(settings, 'EDUCATION_BRAND_NAME', 'Aprobado'),
            'logo': getattr(
                settings,
                'EDUCATION_BRAND_LOGO',
                'images/logo-dark.png',
            ),
            'logo_inverse': getattr(
                settings,
                'EDUCATION_BRAND_LOGO_INVERSE',
                'images/logo.png',
            ),
            'favicon': getattr(
                settings,
                'EDUCATION_BRAND_FAVICON',
                'images/favicon.png',
            ),
        },
        'education_document_max_mb': max(
            1,
            education_document_max_bytes // (1024 * 1024),
        ),
    }
