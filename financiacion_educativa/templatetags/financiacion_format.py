from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template


register = template.Library()


ETIQUETAS_ROL_PROGRAMA = {
    'INSTITUTION_ADMIN': 'Administrador de programa',
    'INSTITUTION_ANALYST': 'Analista de programa',
    'INSTITUTION_READ_ONLY': 'Consulta del programa',
}


@register.filter
def cop(valor):
    try:
        numero = Decimal(valor).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return ''
    return f'${numero:,.0f}'.replace(',', '.')


@register.filter
def porcentaje(valor):
    try:
        numero = Decimal(valor)
    except (InvalidOperation, TypeError, ValueError):
        return ''
    texto = format(numero, 'f').rstrip('0').rstrip('.')
    return texto.replace('.', ',')


@register.filter
def rol_programa(valor):
    return ETIQUETAS_ROL_PROGRAMA.get(str(valor), str(valor))
