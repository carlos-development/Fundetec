def extraer_metricas_uso(respuesta):
    """Extrae solo contadores no sensibles expuestos por Responses API."""
    uso = getattr(respuesta, 'usage', None)
    if uso is None:
        return {}
    metricas = {}
    for nombre in ('input_tokens', 'output_tokens', 'total_tokens'):
        valor = getattr(uso, nombre, None)
        if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0:
            metricas[nombre] = valor
    return metricas
