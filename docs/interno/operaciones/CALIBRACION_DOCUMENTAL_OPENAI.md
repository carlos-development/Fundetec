# Calibracion documental educativa con OpenAI

Esta herramienta es exclusivamente operativa y local. No usa solicitudes,
participantes, documentos, decisiones, contratos, firmas, correo ni webhooks del
flujo normal. No certifica autenticidad, identidad ni liveness.

## Estado tecnico auditado

- Interfaz: OpenAI Responses API mediante `client.responses.create` y
  `store=False`.
- Modelo: `FINANCIACION_EDUCATIVA_DOCUMENT_AI_MODEL`.
- Identificacion: esquema estricto v3.
- Contenido de ingresos y matricula: esquema `CONTENT_V2`.
- Timeout: 30 segundos por defecto.
- Intentos: 3 para identidad y 3 para contenido.
- Umbrales de identidad: confianza 0.90, calidad 0.80 y legibilidad 0.80.
- Umbrales de contenido: confianza 0.90, legibilidad 0.80 y completitud 0.80.
- Los umbrales productivos no son modificados por la calibracion.

Para identidad se envia la imagen en base64, el tipo y lado esperados y el
contexto privado declarado necesario para comparar datos visibles. Para
contenido se envian hasta las paginas permitidas, imagenes renderizadas y texto
minimizado; los correos se redactan y los numeros de ocho o mas digitos se
reducen a sus ultimos cuatro caracteres. El numero declarado completo se cambia
por un sufijo antes de enviarlo al proveedor.

El flujo productivo persiste resultados estructurados, puntajes, reason codes,
campos visibles controlados y trazas de intentos. No persiste prompts ni base64.
Los logs operativos usan identificadores, etapa, codigo y tipo de excepcion; no
deben incluir contenido, prompts ni respuestas completas. La calibracion solo
persiste un JSON sanitizado fuera del repositorio. Responses API permite recoger
conteos de tokens, pero no se calcula costo monetario sin una tabla de precios
versionada.

Las decisiones vigentes son:

- `ACCEPTED`: todas las dimensiones obligatorias son concluyentes y superan los
  umbrales.
- `CORRECTION_REQUIRED`: documento o lado incorrecto, contradiccion concluyente,
  formato corregible o calidad insuficiente concluyente.
- `RETRYING`: error temporal del proveedor o del procesamiento.
- `MANUAL_EXCEPTION`: incertidumbre, baja confianza o resultado incompleto.
- `FAILED`: error permanente o agotamiento de intentos.

Los backends simulados viven bajo `financiacion_educativa.tests` y solo pueden
usarse cuando los settings de prueba habilitan expresamente esos módulos.

## Manifest versionado

El manifest debe estar fuera del repositorio y usar este formato estricto:

```json
{
  "schema_version": "EDU_CALIBRATION_MANIFEST_V1",
  "cases": [
    {
      "case_id": "CASE_ID_FRONT_VALID_001",
      "relative_path": "identity/front-valid.jpg",
      "expected_document_type": "STUDENT_ID_FRONT",
      "expected_side": "FRONT",
      "expected_outcome": "ACCEPT",
      "expected_reasons": [],
      "format": "JPEG",
      "document_category": "IDENTITY",
      "holder_alias": "ALIAS_HOLDER_001",
      "notes": "Captura autorizada para calibracion"
    }
  ]
}
```

Valores de resultado: `ACCEPT`, `CORRECTION` o `INCONCLUSIVE`. Los formatos
permitidos son `JPEG`, `PNG` y `PDF`; una identificacion nunca admite PDF. El
manifest rechaza claves adicionales, casos duplicados, rutas absolutas,
recorridos `..`, alias no sinteticos, correos y secuencias numericas largas.

Nunca se incluyen nombres, documentos, cuentas, correos, telefonos, direcciones,
tokens, claves ni texto integral. Cuando una comparacion real sea necesaria, se
usa un segundo archivo privado fuera del repositorio:

```json
{
  "schema_version": "EDU_CALIBRATION_PRIVATE_CONTEXT_V1",
  "cases": {
    "CASE_ID_FRONT_VALID_001": {
      "holder_name": "VALOR PRIVADO",
      "holder_document_number": "VALOR PRIVADO",
      "document_type": "CC",
      "birth_date": "VALOR PRIVADO"
    }
  }
}
```

Este archivo nunca se copia ni se refleja en el reporte.

## Conjunto minimo recomendado

No se deben fabricar documentos oficiales ni descargar datasets desconocidos.
Todas las muestras deben contar con autorizacion verificable.

Identificacion:

- frente y reverso validos;
- rostro sin documento, teclado y pantalla mostrando una identificacion;
- lado incorrecto, recorte, desenfoque, reflejo y baja iluminacion;
- resolucion insuficiente, documento diferente y datos contradictorios.

Ingresos:

- certificado laboral, certificado de ingresos, ingresos y retenciones;
- extracto bancario y desprendible de nomina;
- documento ajeno, titular contradictorio y campos minimos ausentes;
- PDF con texto, escaneado, hibrido, protegido y corrupto.

Matricula:

- soporte coincidente;
- estudiante, institucion o programa diferentes;
- documento ajeno y contenido insuficiente.

## Ejecucion

Primero se valida sin proveedor. Las tres rutas deben ser absolutas, privadas y
estar fuera del repositorio, `static`, `media` y almacenamiento documental del
flujo normal:

```powershell
python manage.py calibrar_documentos_educativos `
  --dataset D:\privado\dataset `
  --manifest D:\privado\manifest.json `
  --output D:\privado\report-dry-run.json `
  --dry-run
```

Una ejecución real futura exige simultaneamente:

1. autorización formal para el dataset;
2. `OPENAI_API_KEY` solo en el entorno;
3. `FINANCIACION_EDUCATIVA_CALIBRATION_OPENAI_ENABLED=true`;
4. `--execute`;
5. `--allow-real-openai`;
6. `--private-context` con una ruta privada.

```powershell
python manage.py calibrar_documentos_educativos `
  --dataset D:\privado\dataset `
  --manifest D:\privado\manifest.json `
  --private-context D:\privado\context.json `
  --output D:\privado\report.json `
  --execute `
  --allow-real-openai
```

No se imprime la clave, el contenido, el prompt ni la respuesta. Cada caso se
procesa de forma independiente. Un fallo no detiene los casos siguientes.

## Reporte y politica

El reporte separa resultado del proveedor, validacion del esquema, decision
determinista, esperado, desviacion, reason codes, confianza por dimension,
latencia, paginas, imagenes, reintentos, error tecnico y uso de tokens. Calcula
verdaderos aceptados, verdaderas correcciones, falsos aceptados, falsas
correcciones, inconclusos, errores tecnicos, tasas por categoria, mediana y p95.

Todo falso aceptado queda marcado como critico. Rostro sin documento, teclado,
documento ajeno, lado incorrecto o datos contradictorios nunca pueden aceptarse.
No se deben bajar umbrales para mejorar artificialmente la tasa de aceptacion.

La propuesta generada es evidencia para revision humana; no cambia settings. La
calibracion debe diferenciar identidad, contenido y formato, pedir una captura
nueva ante defectos concluyentes, reintentar solo errores temporales y usar
`MANUAL_EXCEPTION` para incertidumbre.

## Eliminacion segura

Tras conservar solo la evidencia operativa autorizada, elimina dataset, contexto
y reporte usando el procedimiento de borrado seguro definido para el medio de
almacenamiento. Verifica que no existan copias en sincronizacion, backups no
autorizados, historial de terminal, Git ni papelera. La aplicacion nunca borra
automaticamente esos archivos para evitar eliminar una ruta equivocada.
