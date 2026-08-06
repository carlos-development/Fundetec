from decimal import Decimal, ROUND_HALF_UP


UNIDADES = ('', 'UNO', 'DOS', 'TRES', 'CUATRO', 'CINCO', 'SEIS', 'SIETE', 'OCHO', 'NUEVE')
ESPECIALES = (
    'DIEZ', 'ONCE', 'DOCE', 'TRECE', 'CATORCE', 'QUINCE', 'DIECISEIS',
    'DIECISIETE', 'DIECIOCHO', 'DIECINUEVE',
)
DECENAS = ('', '', 'VEINTE', 'TREINTA', 'CUARENTA', 'CINCUENTA', 'SESENTA', 'SETENTA', 'OCHENTA', 'NOVENTA')
CENTENAS = (
    '', 'CIENTO', 'DOSCIENTOS', 'TRESCIENTOS', 'CUATROCIENTOS',
    'QUINIENTOS', 'SEISCIENTOS', 'SETECIENTOS', 'OCHOCIENTOS', 'NOVECIENTOS',
)


def formatear_cop(valor):
    numero = Decimal(str(valor or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return f'${numero:,.0f}'.replace(',', '.')


def _entero_a_letras(numero):
    numero = int(numero)
    if numero == 0:
        return 'CERO'
    if numero < 10:
        return UNIDADES[numero]
    if numero < 20:
        return ESPECIALES[numero - 10]
    if numero < 100:
        decena, unidad = divmod(numero, 10)
        if unidad == 0:
            return DECENAS[decena]
        if decena == 2:
            return f'VEINTI{UNIDADES[unidad]}'
        return f'{DECENAS[decena]} Y {UNIDADES[unidad]}'
    if numero < 1000:
        centena, resto = divmod(numero, 100)
        if numero == 100:
            return 'CIEN'
        return ' '.join(
            parte for parte in (CENTENAS[centena], _entero_a_letras(resto) if resto else '')
            if parte
        )
    if numero < 1_000_000:
        miles, resto = divmod(numero, 1000)
        prefijo = 'MIL' if miles == 1 else f'{_entero_a_letras(miles)} MIL'
        return ' '.join(
            parte for parte in (prefijo, _entero_a_letras(resto) if resto else '')
            if parte
        )
    if numero < 1_000_000_000:
        millones, resto = divmod(numero, 1_000_000)
        prefijo = 'UN MILLON' if millones == 1 else f'{_entero_a_letras(millones)} MILLONES'
        return ' '.join(
            parte for parte in (prefijo, _entero_a_letras(resto) if resto else '')
            if parte
        )
    miles_millones, resto = divmod(numero, 1_000_000_000)
    prefijo = (
        'MIL MILLONES'
        if miles_millones == 1
        else f'{_entero_a_letras(miles_millones)} MIL MILLONES'
    )
    return ' '.join(
        parte for parte in (prefijo, _entero_a_letras(resto) if resto else '')
        if parte
    )


def numero_cop_a_letras(valor):
    numero = Decimal(str(valor or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return f'{_entero_a_letras(abs(int(numero)))} PESOS M/CTE'
