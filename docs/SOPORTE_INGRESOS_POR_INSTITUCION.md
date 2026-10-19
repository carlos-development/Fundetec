# Soporte de ingresos por institucion

Configuracion local implementada el 2026-10-19. No configura servidores ni
activa automaticamente a ninguna institucion. Conserva el comportamiento
obligatorio de todos los registros existentes y nuevos hasta un cambio expreso.

## Configurar Jowalth despues de revisar y migrar

1. Aplicar en el ambiente autorizado `instituciones.0003_institucion_requiere_soporte_ingresos`.
   Es un AddField booleano con default=True, sin migracion de solicitudes,
   documentos, firmas ni reglas financieras. No se aplico sobre la base local
   persistente ni servidores durante esta implementacion.

   Comandos para una ejecucion posterior expresamente autorizada:

   ```sh
   python manage.py migrate instituciones --plan
   python manage.py migrate instituciones
   ```

   Migrar solo `financiacion_educativa` no sustituye esta migracion de
   `instituciones`; comprobar ambas aplicaciones antes de arrancar el release.

2. En Django Admin > Instituciones, identificar Jowalth por sus datos legales
   y abrir su registro. Desmarcar **Soporte de ingresos obligatorio en financiacion
   educativa** y guardar. Requiere permiso de cambio de Institucion; no concede
   ningun permiso nuevo. El cambio queda en el log normal de Django Admin.
3. Usar una solicitud sintetica de esa institucion. La pantalla debe indicar
   **Soporte de ingresos - opcional**, permitir enviar sin cargarlo y exigir
   las caras de identidad y consentimientos correspondientes. Comprobar tambien
   otra institucion con la casilla marcada: su ausencia debe seguir bloqueando.

No configurar por nombre de curso, programa, nombre comercial en codigo ni UUID
hardcoded. La fuente es `solicitud.institucion.requiere_soporte_ingresos`; no hay
variable .env. La pantalla usa un guion largo en la etiqueta visible.

## Regla y trazabilidad

`services/politica_documental.py` centraliza la obligatoriedad efectiva, la
matriz de identidad y la evaluacion de documentos presentes. Formularios,
requisitos, envio, revision y ambas orquestaciones consumen esa politica.
Los archivos aportados siguen sujetos a antivirus, clasificacion y validacion:
pendientes pueden entrar al procesamiento existente, pero no permiten aprobar;
bloqueados o rechazados requieren resolucion. No se desactivan validadores.

El codigo INCOME_CERTIFICATE se mantiene compatible. Se admiten las mismas
categorias: certificado laboral, de ingresos, ingresos y retenciones,
desprendible de nomina, extracto bancario y certificacion bancaria de titularidad.
La cedula conserva frente/reverso; pasaporte y demas tipos conservan su matriz.
El menor conserva tutor como deudor y unico firmante, identidad y consentimientos.

El historial inmutable de envio y decision registra `metadata.politica_documental`:
version EDU_DOCUMENTAL_V2, institucion_id y soporte_ingresos_obligatorio.
No se modifican resultados IA, decisiones ni eventos anteriores. Una correccion
anterior que pedia un soporte hoy opcional y nunca aportado no exige una carga
artificial: el usuario debe enviar nuevamente de forma explicita. Los mensajes
actuales omiten ese requerimiento, conservando el texto original en su registro.
Una nueva correccion manual no puede exigir ese documento opcional ausente.

Al pasar a PENDING_PROMISSORY_NOTE se conserva la politica de aprobacion para
contratacion/firma/autorizacion. Si un expediente antiguo ya esta en esas etapas
y no tiene traza de politica, se mantiene la regla historica obligatoria.
Un cambio de configuracion no regenera pagares ni fichas, no cambia fotografias,
no manda correos, no encola procesos ni altera estados. El generador contractual
ya trabaja sobre la aprobacion y no requiere un certificado adicional.

## Verificar y revertir

- Confirmar la casilla y la institucion originadora en Admin sin imprimir PII.
- Abrir documentacion y comprobar ausencia opcional, caras obligatorias, soporte
  aportado protegido y posterior PENDING_SIGNATURE con proveedores simulados.
- Revisar el historial del envio y de la aprobacion para confirmar el booleano
  efectivo; antes de firmar la API sigue sin autorizar ni publicar financial_terms.
- Para revertir, volver a marcar la casilla y guardar en Admin. Las solicitudes
  que aun no pasaron a contratacion vuelven a exigir soporte en su proxima
  accion explicita; avisar a soporte sobre expedientes en curso. Las ya aprobadas
  documentalmente conservan su politica trazada, sin reescribir contratos.
- No revertir la migracion para cambiar una politica: retirar la columna perderia
  la configuracion institucional. No usar SQL ni edicion manual de estados.

## Terminos publicados y limites

Inspeccion local de solo lectura: tres versiones publicadas. La unica referencia
a ingresos encontrada esta en `tratamiento-datos-educativos-v1` (DATA_PROCESSING)
y enumera categorias de datos; no exige su carga en todos los casos. No se hallo
una contradiccion de obligatoriedad ni se editaron textos publicados. Los terminos
de staging/produccion no se consultaron: deben contrastarse antes de activar la
excepcion alli. Si contradicen esta regla, tramitar una nueva version juridica;
no editar una publicada.

No hay cambios de locks, transacciones, concurrencia, API, motor financiero o
proveedores. Las pruebas usan SQLite en memoria y proveedores falsos; PostgreSQL
no es necesario por concurrencia para este cambio aditivo. No equivale a validar
el despliegue ni las versiones juridicas remotas.

## Validacion local del 2026-10-19

Seleccion final: 88 pruebas en 124.953 segundos, OK y cero omisiones. Incluye
los 18 casos de `test_ingresos_por_institucion`, y los modulos completos
`test_politica_documental`, `test_fase4_requisitos`, `test_orquestacion_automatica`,
`test_progreso_procesamiento` y `test_revision_operativa`.
Adicionalmente se ejecutaron cinco casos de `test_captura_identidad_ingresos`:
obligatoriedad adulta por defecto, categorias financieras de la interfaz,
reemplazo trazable, tutor/deudor y rechazo de participante incorrecto.

La cola llega a PENDING_SIGNATURE sin soporte opcional, genera pagare/ficha y
conserva tutor como unico firmante. El webhook falso confirma la autorizacion
sin llamadas reales. Los casos historicos y el cambio de configuracion sin
reprocesamiento estan cubiertos. Check: cero incidencias; makemigrations --check
--dry-run: sin cambios adicionales; git diff --check: correcto. La base de
pruebas en memoria fue destruida. No se ejecuto la suite educativa completa,
PostgreSQL, migraciones persistentes, cambios de destinatarios ni envios reales.
