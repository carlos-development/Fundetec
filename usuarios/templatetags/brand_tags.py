from django import template
from django.conf import settings


register = template.Library()


_BRAND_SETTINGS = {
    'name': ('BRAND_NAME', 'FUNDETEC'),
    'legal_name': ('BRAND_LEGAL_NAME', 'FUNDETEC'),
    'primary_color': ('BRAND_PRIMARY_COLOR', '#0B4EA2'),
    'secondary_color': ('BRAND_SECONDARY_COLOR', '#FFC400'),
    'accent_color': ('BRAND_ACCENT_COLOR', '#E7191A'),
    'logo': ('BRAND_LOGO', 'images/fundetec-logo.png'),
    'logo_dark': ('BRAND_LOGO_DARK', 'images/fundetec-logo.png'),
    'public_base_url': ('BRAND_PUBLIC_BASE_URL', ''),
}


@register.simple_tag
def brand_value(key):
    setting_name, fallback = _BRAND_SETTINGS.get(key, (None, ''))
    if not setting_name:
        return ''
    return getattr(settings, setting_name, fallback)


@register.simple_tag
def brand_static_url(asset_key='logo_dark'):
    asset_path = brand_value(asset_key) or brand_value('logo') or 'images/fundetec-logo.png'
    public_base_url = (brand_value('public_base_url') or '').rstrip('/')
    if public_base_url:
        return f'{public_base_url}/static/{asset_path}'
    return f'/static/{asset_path}'
