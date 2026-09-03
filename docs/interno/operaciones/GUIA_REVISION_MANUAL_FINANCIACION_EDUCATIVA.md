# Procedimiento de contingencia manual de financiación educativa

## 1. Objetivo

Esta guía describe el procedimiento excepcional para revisar un expediente
educativo, solicitar correcciones, aprobar la revisión documental, enviar el
pagaré a ZapSign y verificar el cierre del proceso. No representa el recorrido
normal definitivo.

Debe utilizarse únicamente cuando la automatización educativa esté
deshabilitada, haya sido pausada de forma controlada o un expediente termine en
`MANUAL_EXCEPTION`. La intervención humana no autoriza el curso ni reemplaza
las garantías de firma.

La aprobación administrativa **no autoriza todavía el curso**. La autorización
solo ocurre después de que ZapSign confirme una firma válida mediante webhook.

## 2. Flujo resumido

```text
Correo a soporte
      ↓
Solicitud pendiente de revisión manual
      ↓
Revisión de documentos y matrícula
      ↓
Decisión administrativa
      ↓
Generación de fotografía financiera, pagaré y ficha de matrícula
      ↓
Envío manual del pagaré a ZapSign
      ↓
Firma del responsable contractual
      ↓
Webhook válido de ZapSign
      ↓
Solicitud APPROVED y curso autorizado
```

## 3. Acceso y permisos

### 3.1 Dirección del administrador

Abrir:

```text
https://staging-api.aprobado.com.co/admin/
```

Iniciar sesión únicamente con la cuenta personal asignada al operador. No
compartir usuarios, contraseñas ni sesiones.

### 3.2 Permisos necesarios

La cuenta debe ser `staff` y tener, como mínimo, estos permisos:

- ver solicitudes, documentos, evidencias de matrícula y procesos de firma;
- `Puede solicitar escaneos de documentos educativos`;
- `Puede revisar documentos educativos`;
- `Puede revisar y decidir solicitudes educativas`;
- `Puede enviar y recuperar firmas educativas`.

El permiso de procesar validaciones IA es opcional para esta operación manual.
Si falta un botón o una acción, no se debe intentar modificar datos directamente
en PostgreSQL: se debe solicitar el permiso correspondiente al administrador.

## 4. Recepción de una solicitud

Cuando el solicitante pulsa **Enviar expediente**, se envía un correo con el
asunto:

```text
Recibimos tu expediente de financiación educativa | Aprobado
```

El solicitante es el destinatario principal y
`soporte@aprobado.com.co` recibe copia.

El mensaje incluye la referencia externa de la solicitud. No contiene enlaces
de invitación, enlaces de captura ni tokens.

> Importante: el correo **Continúa tu solicitud educativa con Aprobado** es la
> invitación inicial del solicitante. Ese mensaje no se copia a soporte porque
> contiene un enlace personal de un solo uso.

## 5. Localizar la solicitud

1. Entrar en **Financiación educativa**.
2. Abrir **Solicitudes de financiación educativa**.
3. Buscar la referencia externa indicada en el correo.
4. Confirmar que coincidan la institución, el solicitante y el programa.
5. Confirmar que el estado sea **Pendiente de revisión manual**.
6. Abrir la solicitud.
7. Pulsar **Revisar expediente**, en la parte superior derecha.

No buscar únicamente por nombre: siempre validar la referencia externa para no
revisar la solicitud equivocada.

## 6. Revisión del expediente

La pantalla **Revisión de solicitud educativa** muestra:

- referencia e institución;
- estado de la solicitud;
- documentos activos;
- titular de cada documento;
- estado del escaneo antivirus;
- estado de revisión;
- resultado de validación IA, si existe;
- enlace **Previsualizar**;
- condiciones financieras, cuando ya exista una fotografía activa.

### 6.1 Regla principal

No aprobar un documento hasta confirmar simultáneamente:

- escaneo antivirus **Seguro / SAFE**;
- archivo legible y completo;
- tipo documental correcto;
- titular correcto;
- frente o reverso correcto, según corresponda;
- datos consistentes con la solicitud;
- ausencia de manipulación evidente;
- vigencia y contenido razonables para su finalidad.

La IA es una ayuda. Un resultado inconcluso obliga a revisar el archivo, pero no
autoriza a aprobarlo sin inspección humana.

### 6.2 Identificación del estudiante adulto

Revisar:

- identificación del estudiante, frente;
- identificación del estudiante, reverso;
- soporte de ingresos o certificación bancaria del responsable contractual.

Comparar, como mínimo:

- nombres y apellidos;
- tipo y número de documento;
- fecha de nacimiento, cuando sea visible;
- correspondencia entre frente y reverso;
- correspondencia con los datos de la solicitud.

### 6.3 Solicitud de menor de edad

Además de la identificación del estudiante, revisar:

- existencia del tutor adulto;
- identificación del tutor, frente y reverso;
- relación declarada entre tutor y estudiante;
- soporte de ingresos o certificación bancaria del tutor o responsable contractual;
- consistencia de nombres y documentos.

El tutor adulto es el responsable contractual y el único firmante del pagaré en
el flujo actual.

### 6.4 Evidencia o ficha de matrícula

Los datos de matrícula pueden existir sin archivo adjunto. El soporte documental
es opcional mientras no haya sido aportado.

Si existe archivo de matrícula:

1. Confirmar que el escaneo antivirus sea seguro.
2. Previsualizar el archivo.
3. Confirmar institución, programa, periodo y referencia.
4. Aceptar tanto el documento como la evidencia de matrícula asociada.

Un soporte adjunto que permanezca pendiente, bloqueado o rechazado impide la
aprobación del expediente.

## 7. Procesar documentos

### 7.1 Escaneo pendiente

1. Abrir **Documentos de financiación**.
2. Filtrar o buscar por referencia de la solicitud.
3. Seleccionar exclusivamente los documentos de esa solicitud.
4. Elegir **Solicitar escaneo antivirus**.
5. Pulsar **Ir**.
6. Confirmar que el resultado sea seguro antes de continuar.

No aceptar un documento con escaneo pendiente, bloqueado, infectado o con error.

### 7.2 Aceptar documentos

1. En **Documentos de financiación**, buscar la referencia externa.
2. Previsualizar cada archivo.
3. Seleccionar solo los documentos verificados.
4. Elegir **Aceptar documentos seleccionados**.
5. Pulsar **Ir**.
6. Confirmar el mensaje **Documentos aceptados**.

### 7.3 Documento incorrecto

Si el archivo no corresponde al tipo solicitado:

1. Seleccionarlo.
2. Elegir **Rechazar por tipo documental incorrecto**.
3. Pulsar **Ir**.
4. Continuar con una decisión de corrección para informar al solicitante.

No utilizar “Aceptar” para desbloquear artificialmente el flujo.

### 7.4 Evidencia de matrícula adjunta

1. Entrar en **Evidencias de matrícula**.
2. Buscar por referencia externa.
3. Revisar el soporte asociado.
4. Seleccionar **Aceptar evidencias seleccionadas** o **Rechazar por soporte incorrecto**.

## 8. Registrar la decisión

Regresar a la solicitud y pulsar **Revisar expediente**.

La decisión solo está disponible cuando el estado es **Pendiente de revisión
manual**.

### 8.1 Aprobar

Usar exclusivamente cuando todo el expediente esté verificado:

- **Decisión:** `Aprobar expediente y continuar a pagaré`;
- **Motivo controlado:** `Requisitos verificados`;
- **Requisitos por corregir:** ninguno;
- **Mensaje para el solicitante:** puede dejarse vacío para usar el mensaje estándar;
- **Observación interna:** registrar únicamente información operativa necesaria.

Pulsar **Registrar decisión**.

El sistema debe:

1. crear y bloquear la fotografía financiera definitiva;
2. cambiar a pendiente de pagaré;
3. generar el pagaré educativo;
4. generar la ficha de matrícula;
5. crear un proceso de firma pendiente de envío;
6. notificar al solicitante que debe continuar a firma.

Esto todavía no cambia la solicitud a `APPROVED` ni autoriza el curso.

### 8.2 Solicitar correcciones

Usar cuando el expediente puede corregirse:

- **Decisión:** `Solicitar correcciones`;
- elegir un motivo distinto de `Requisitos verificados`;
- escribir una instrucción clara para el solicitante;
- seleccionar todos los requisitos que deben corregirse.

Ejemplo de mensaje:

```text
La fotografía del reverso de la identificación no es legible. Repite la
captura con buena iluminación, sin reflejos y mostrando los cuatro bordes.
```

No incluir diagnósticos internos, resultados técnicos, secretos ni información
de otras solicitudes.

### 8.3 Rechazar

Usar únicamente cuando exista una causa definitiva y autorizada:

- **Decisión:** `Rechazar`;
- seleccionar el motivo correspondiente;
- escribir un mensaje comprensible para el solicitante;
- no seleccionar requisitos por corregir.

Ante dudas, solicitar corrección o escalar el caso; no rechazar automáticamente
por un resultado incierto de IA.

## 9. Enviar el pagaré a ZapSign

Con la automatización desactivada, este paso es manual:

1. Entrar en **Procesos de firma educativa**.
2. Buscar la referencia externa de la solicitud.
3. Confirmar que el estado sea **Pendiente de envío**.
4. Seleccionar únicamente ese proceso.
5. Elegir **Enviar pagarés educativos seleccionados**.
6. Pulsar **Ir**.
7. Confirmar el mensaje `Enviados: 1. Fallidos: 0.`
8. Actualizar la página.
9. Confirmar que el estado sea **Pendiente de firma**.

ZapSign enviará el enlace al correo del responsable contractual. No copiar ni
reenviar manualmente enlaces obtenidos desde el panel de ZapSign.

### 9.1 Si aparece “Documento bloqueado”

Confirmar que el firmante esté entrando desde el correo automático de ZapSign,
no desde la vista administrativa del documento. La vista del autor puede exigir
permisos diferentes y no es el enlace de firma del solicitante.

### 9.2 Si el envío falla

1. No repetir múltiples veces de forma inmediata.
2. Revisar el estado y el código del último error del proceso.
3. Confirmar si ZapSign creó o no el documento.
4. Si el resultado es ambiguo, escalar para conciliación antes de reenviar.

Un reenvío ciego puede crear documentos duplicados.

## 10. Confirmar la firma y el cierre

Después de la firma, ZapSign envía el webhook `doc_signed`.

Verificar en **Procesos de firma educativa**:

- estado **Firmado**;
- fecha de firma registrada;
- ausencia de error pendiente.

Verificar en la solicitud:

- estado interno `APPROVED`;
- fecha de matrícula registrada;
- pagaré vigente marcado como firmado;
- documento firmado almacenado;
- fotografía financiera activa y bloqueada.

La consulta institucional debe devolver:

```json
{
  "status": "APPROVED",
  "course_authorized": true,
  "authorization_effective_at": "fecha-hora",
  "financial_terms": {
    "currency": "COP",
    "requested_amount": "valor",
    "financed_amount": "valor",
    "term_months": 6,
    "estimated_installment": "valor"
  }
}
```

No considerar cerrado el caso si aparece aprobado en una pantalla administrativa
pero `course_authorized` sigue en `false` o `financial_terms` continúa en `null`.

## 11. Firma rechazada

Si ZapSign informa `doc_refused`:

1. confirmar el estado **Firma rechazada**;
2. no autorizar el curso;
3. no cambiar manualmente la solicitud a `APPROVED`;
4. registrar la razón conocida;
5. escalar para decidir si corresponde corregir datos y regenerar el pagaré.

Un pagaré rechazado, cancelado, vencido o reemplazado no puede autorizar el
curso.

## 12. Lista de control rápida

Antes de aprobar:

- [ ] Referencia externa correcta.
- [ ] Solicitud en revisión manual.
- [ ] Todos los archivos pertenecen a la solicitud.
- [ ] Todos los archivos tienen escaneo seguro.
- [ ] Identificación frontal y reverso legibles.
- [ ] Datos de identidad coinciden.
- [ ] Soporte de ingresos o certificación bancaria del responsable contractual aceptado.
- [ ] Tutor verificado cuando el estudiante es menor.
- [ ] Matrícula revisada si se adjuntó soporte.
- [ ] No quedan documentos pendientes, bloqueados o rechazados.

Después de aprobar:

- [ ] Fotografía financiera creada y bloqueada.
- [ ] Pagaré y ficha de matrícula generados.
- [ ] Proceso de firma pendiente de envío.
- [ ] Pagaré enviado una sola vez a ZapSign.
- [ ] Solicitud en pendiente de firma.
- [ ] Webhook firmado procesado.
- [ ] Solicitud en `APPROVED`.
- [ ] `course_authorized=true`.
- [ ] Condiciones financieras visibles en la API.

## 13. Reglas de seguridad

- No descargar documentos en equipos personales o no autorizados.
- No compartir capturas con números de documento, direcciones o correos.
- No enviar documentos por WhatsApp ni por cuentas personales.
- No copiar tokens de invitación, captura, API o ZapSign.
- No editar estados directamente en la base de datos.
- No aprobar documentos cuyo escaneo no sea seguro.
- No usar la IA como única autoridad para validar identidad.
- No aprobar por presión operativa si faltan requisitos.
- Cerrar sesión al terminar y no dejar el administrador abierto.

## 14. Información para escalar un incidente

Reportar únicamente:

- referencia externa;
- UUID de la solicitud;
- estado actual;
- paso exacto que falló;
- fecha y hora aproximada;
- código de error visible;
- resultado esperado y resultado obtenido.

No enviar contraseñas, tokens, secretos, archivos de identidad ni el contenido
completo de `staging.env`.
