from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template


register = template.Library()


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
