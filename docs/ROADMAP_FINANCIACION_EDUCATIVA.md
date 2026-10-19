# Roadmap de financiacion educativa

Actualizado: 2026-10-19. Producto independiente de libranza y gestion_creditos.
El backlog legado se conserva en `archivo/legacy/` y no gobierna este roadmap.

## Estado y criterios de cierre

| Bloque | Estado | Criterio de cierre |
| --- | --- | --- |
| Firma, webhook y API | Cierre verificado en produccion para los dos casos Jowalth | JOW-4FE29332-PROD y JOW-88C00418-PROD devuelven APPROVED, course_authorized=true, fecha de autorizacion y financial_terms. Las fechas de firma del proceso y del artefacto coinciden. Verificacion operativa confirmada por Carlos; no constituye certificacion de otros casos. |
| Dashboard financiero inicial | Desplegado y comprobado en produccion por Carlos | Indicadores y listado reconcilian, solo cuentan contratos realmente autorizados, excluyen simulaciones y muestran el calendario firmado. |
| Fase 2A: reportes BRE-B y conciliacion | Desplegada en staging 317c2c8; ciclo sintetico verificado por Carlos; produccion pendiente | Reporte movil, comprobante y evidencia bancaria SAFE con ClamAV real, confirmacion ficticia COP 157738. No acredita transferencia real ni aplicacion al contrato. |
| Experiencia de recaudos y conciliacion | Implementada y validada localmente; pendiente revision y certificacion staging | Errores por campo sin perder datos, seleccion de evidencia propia SAFE sin UUID manual, revalidacion e integridad al confirmar, observacion opcional sin diferencia y replay. No aplica pagos. |
| Fase 2B: libro de aplicaciones | Siguiente prioridad; reglas pendientes deben explicitarse | Confirmar y aplicar ingreso una sola vez, con cuotas pagadas/parciales, capital e intereses aplicados, saldos/cortes y cierre reproducibles, sin alterar la fotografia firmada. |
| Fase 2C: ePayco | Pendiente, sin adaptadores ni integracion activados | Comercio y credenciales sandbox autorizados; confirmacion servidor a servidor autenticada, importe/moneda/orden, idempotencia, ambiente y reversos. Nunca confirmar por la redireccion del navegador. |
| Saldos y causacion de intereses | Pendiente de pagos | Saldo real y capital/intereses aplicados derivan del libro de pagos conciliados; calculos reproducibles a fecha de corte y separados del calendario programado. |
| Mora | Pendiente de politica aprobada | Aprobar regla, tasas, base y fechas; implementar y probar sobre pagos conciliados. El dashboard inicial no calcula cargos por mora. |
| Abonos y pago total | Pendiente | Pagos anticipados con intereses a la fecha, reduccion de plazo manteniendo cuota, nuevas proyecciones trazables y cierre sin alterar el contrato original. |
| Experiencia movil y revision humana | Implementada; verificacion de dispositivos pendiente | Probar Safari/iPhone y Chrome/Android, captura frente/reverso y recuperacion, permisos y revision humana con trazabilidad. |
| E2E menor con tutor | Cubierto localmente; certificacion staging pendiente | Tutor como unico firmante, evidencias y consentimientos correctos, firma/webhook/API completos e idempotentes. |
| Accesos individuales y notificaciones institucionales | Implementados; comprobacion operativa pendiente | Membresias explicitas y activas, aislamiento, cuentas individuales y matriz de destinatarios/outbox verificada. |
| Respaldos, restauracion y alertas | Verificacion operativa pendiente | Restauracion ensayada de PostgreSQL y archivos privados; alertas de workers, outbox, errores y firmas ambiguas, con responsables. |
| Integracion y jerarquia Institucion -> Programa -> Curso | API implementada; jerarquia pendiente | Certificar integracion y replay; disenar y migrar jerarquia explicita sin inferir relaciones a partir de nombres academicos. |

## Politica financiera aprobada

### Soporte de ingresos institucional (2026-10-19)

Obligatorio por defecto. Se incorpora el ajuste persistente
`Institucion.requiere_soporte_ingresos`, administrable, con migracion
`instituciones.0003_institucion_requiere_soporte_ingresos`. Ninguna institucion
se configura automaticamente como opcional. La ausencia opcional permite envio,
revision y automatizacion; cualquier archivo aportado conserva sus controles.
Identidad, tutor, consentimiento, fotografia y firma mantienen sus reglas.
La politica efectiva queda trazada en el historial; contratos existentes no se
reinterpretan ni se reprocesan. Procedimiento para configurar Jowalth, verificar
y revertir: `docs/SOPORTE_INGRESOS_POR_INSTITUCION.md`. Pendiente revision local
y posterior certificacion de configuracion/terminos en staging.
Validacion focalizada local: 88 pruebas OK en 124.953 segundos, cero omisiones,
SQLite en memoria y proveedores falsos, incluido avance automatico a firma y
webhook simulado. Check, comprobacion de migraciones y diff --check correctos.

### Parametros financieros

COP, metodo frances, tasa mensual 1%, originacion 10%, IVA de originacion 19%,
garantia 2%, seguro de vida 0,3711%. Los cargos y el calendario de cada contrato
se consultan desde su fotografia versionada, no se recalculan en el dashboard.
Pagos anticipados: intereses a la fecha y reduccion de plazo manteniendo cuota.
Para 2B se conserva causacion diaria base comercial 30 y vencimientos sin
desplazamiento por festivos. Antes de automatizar aplicaciones deben aprobarse
pagos tardios/parciales, excedentes, fechas retroactivas, correcciones y cierre.
No existe una politica de cargos por mora aprobada para este incremento.

## Dashboard inicial y verificacion

Rutas internas: `/financiacion-educativa/operaciones/obligaciones/` y su detalle
por UUID. Requieren los permisos existentes de acceso y consulta operativa.
Los filtros de obligaciones usan fechas de autorizacion efectiva, inclusivas en
la zona horaria de Django; los de tramite usan fechas de creacion. El proximo
vencimiento es la primera fecha contractual desde hoy, inclusive. El indicador
de 30 dias incluye hoy y el dia 30; no afirma que una cuota este impagada.

La elegibilidad reutiliza `firmas_autorizadas_reales` en `estado_publico.py`:
estado APPROVED, firma registrada, pagare firmado vigente con evidencia de
archivo/hash/tamano/fecha y fotografia activa, bloqueada, no legada, todos de
la misma solicitud. Los resultados Sandbox activos se excluyen de los importes
reales y de tramite. Un estado APPROVED sin evidencia coherente no es cartera.
La fecha registrada de firma del proceso debe coincidir con la del artefacto;
el webhook actual guarda ambas con el mismo instante. Los registros historicos
con fechas diferentes requieren comprobacion operativa y no se reparan al consultar.
Cada fotografia elegible se cuenta una vez, independientemente de relaciones
historicas. Las cifras se pueden abrir para consultar todos sus componentes.

En produccion, el dashboard inicial no acredita pagos: capital pagado, intereses
recaudados, mora efectiva y saldo real figuran como no disponibles. Fase 2A agrega
en staging ingresos confirmados pendientes de aplicacion; tampoco son cuotas
pagadas ni capital amortizado. Una suma vacia de contratos elegibles si es cero;
eso no significa una cartera conciliada.

Secuencia: implementacion y pruebas locales -> revision y commit/push por Carlos
-> staging -> verificacion -> produccion.

En staging, Carlos debe contrastar los indicadores con solicitudes reales,
contratos sustituidos y simulaciones; abrir listado/detalle en movil y probar
permisos. El cierre firma/webhook/API de los dos casos Jowalth indicados ya fue
confirmado en produccion; esa comprobacion no sustituye la validacion del nuevo
dashboard en staging. No se hace ninguna llamada a proveedores ni escritura de
contratos al consultar estas pantallas.

### Validacion PostgreSQL local de Fase 1

El 2026-10-06 se ejecutaron exclusivamente estos modulos sobre PostgreSQL 16.13
temporal y aislado en Docker, sin cargar `.env` ni utilizar bases o credenciales
del servidor:

```text
financiacion_educativa.tests.test_dashboard_obligaciones
financiacion_educativa.tests.test_dashboard_operativo
financiacion_educativa.tests.test_api_institucional
financiacion_educativa.tests.test_firma_educativa
financiacion_educativa.tests.test_resultado_sandbox
```

Resultado: 87 pruebas en 145.571 segundos, OK, cero omisiones. La prueba
`ConcurrenciaSandboxTests.test_dos_operadores_generan_un_snapshot` se ejecuto y
paso sobre PostgreSQL real. No quedo omitida ninguna prueba concurrente de estos
modulos. Esta ejecucion no incluye otros modulos ni repite el QA visual.
La base de pruebas fue destruida por Django y el contenedor temporal se elimino
al finalizar. No hubo llamadas reales a proveedores, commit, push ni despliegue.


## Validacion de recaudos en staging: 2026-10-06

Evidencia operativa aportada por Carlos mediante salida del servidor y capturas:

- Release 317c2c8, migracion 0029 aplicada, check sin incidencias, estaticos
  recolectados y health HTTP 200. Servicios existentes reactivados.
- Unidad receipts-scan verificada con systemd-analyze en el VPS, instalada y
  activada tras configurar el canal. Acceso del usuario fundetec-staging al
  socket ClamAV comprobado mediante PONG.
- Receptor sintetico NO TRANSFERIR, sin cuenta ni QR bancario real, con imagen
  escaneada SAFE. Firma de staging conserva SIGNATURE_SEND_PAUSED=True.
- Caso QA E2E-APROBADO-20260925-001: reporte desde iPhone por COP 157738,
  comprobante SAFE/CLEAN, evidencia bancaria independiente y conciliacion con
  referencia QA-BANCO-20261006-001. Pantalla final: ingreso confirmado pendiente
  de aplicacion. No hubo transferencia bancaria real.
- La prueba manual no certifica todos los dispositivos ni lectura bancaria de
  un QR oficial. No se ha implementado aplicacion a capital/intereses.
- Produccion permanece en 57607cc con las obligaciones reales de Jowalth;
  este release BRE-B aun no se ha desplegado ni activado alli.

## Correccion inmediata: experiencia de recaudos

1. Conservar campos ante POST invalido y mostrar el error especifico; el mensaje
   generico y la redireccion actual impiden identificar el campo que fallo.
   Mantener funcionamiento sin JavaScript y explicar la reseleccion de archivos
   cuando el navegador no pueda conservarlos.
2. Seleccionar evidencia bancaria propia y SAFE desde la interfaz, sin copiar
   UUID. Permitir evidencia elegible existente; conservar validacion de acceso,
   finalidad, hash, antivirus y revalidacion al confirmar.
3. Precargar monto/fecha para verificacion; cuenta, actor y receptor se resuelven
   internamente. La referencia bancaria verificada conserva su obligatoriedad.
4. Observacion opcional en el caso sin diferencia de monto, con registro
   determinista de la accion; justificacion explicita obligatoria ante diferencia
   y motivo publico obligatorio al rechazar. Preservar replay e historicos.
5. Mejorar jerarquia, tarjetas, campos, botones, contraste, mensajes y carga de
   soportes en pagador y operador. Filtro institucional por nombre y seleccion,
   estados humanos y tabla contenida en movil. Trazas tecnicas en detalle.
6. Mantener la leyenda pendiente de aplicacion hasta implementar el libro real.
   Validar parsing COP y POST del navegador sin conversiones ambiguas ni floats.

## Fase 2B: cierre funcional del pago

Propuesta concreta de modelos, servicios, migracion aditiva, invariantes y
decisiones pendientes: `docs/DISENO_RECAUDOS_FASE_2B.md`. Es diseno, no un libro
implementado ni una aprobacion de politica financiera pendiente.

El objetivo aprobado es una accion de confirmar y aplicar el ingreso con
previsualizacion de su distribucion y resultado persistente. No basta cambiar
el texto de exito o descontar manualmente una cuota del calendario firmado.

- Una cuota cubierta completamente queda pagada; una parcialmente cubierta
  conserva el remanente. Disminuir cuotas pendientes solo al cubrirlas.
- Separar capital aplicado e intereses cobrados. El monto recibido completo no
  se resta del capital. Saldo real y calendario pendiente derivan del libro.
- Mantener los contratos y fotografias firmadas inmutables, junto con sus
  condiciones financieras publicas; almacenar aplicaciones y proyecciones aparte.
- Idempotencia y bloqueo de obligacion: un ingreso no puede aplicarse dos veces,
  incluso con doble clic, reintentos o operadores concurrentes.
- Abonos anticipados: intereses a la fecha, reduccion de plazo conservando cuota.
  Pago total: liquidacion reproducible y cierre coherente a la fecha efectiva.
- Preservar autorizacion del curso al cerrar financieramente; revisar el mapeo
  publico antes de reutilizar estados existentes. No reabrir matricula ni firma.
- Aplicar ingresos 2A previos solo mediante procedimiento explicito, acotado e
  idempotente. Correcciones/reversiones con movimientos compensatorios trazados.
- Dashboard: cuotas pagadas/parciales/pendientes, capital pagado, intereses
  recaudados y saldos a corte, todos conciliables con sus movimientos.

Decisiones pendientes antes de automatizar: orden de imputacion de parciales y
atrasados; excedentes; fechas retroactivas/cortes; tolerancias de cierre;
correcciones/reversiones; y cualquier cargo por mora. No inventar estas reglas.
El diseno debe mostrar ejemplos COP y las consecuencias de cada decision.

Orden de trabajo: corregir UX local y disenar 2B -> resolver reglas pendientes
-> implementar aplicaciones -> pruebas focalizadas y concurrencia PostgreSQL
-> staging con pago completo/parcial, abono y pago total -> produccion con
receptor oficial verificado y permisos individuales del representante legal.
No repetir toda la suite por rutina; ampliar solo si cambia el alcance o hay
fallos que lo justifiquen. La produccion de recaudos aun no queda certificada.

### Cierre local de experiencia de recaudos: 2026-10-08

Ejecucion propia, con datos sinteticos, sin cargar `.env`, sin credenciales del
servidor y sin llamadas a proveedores. Solo los modulos
`test_recaudos_ux` y `test_recaudos_breb`: PostgreSQL 16.13 temporal en Docker,
64 pruebas en 137.857 segundos, OK y cero omisiones. Incluye las cinco
concurrencias: mismo reporte, mismo movimiento global, escaneo unico,
observacion vacia con replay y dos reportes reutilizando evidencia con distintas
referencias. Se conservan locks del reporte/cuenta y se bloquean soportes en
orden estable al revalidar escaneo, contenido, tamano y hash. Django destruyo
la base de pruebas; se verifico ausencia de conexiones y se elimino el contenedor.

El recorrido `test_recaudos_breb_browser` termino OK: una prueba en 26.725 segundos
en Edge headless, locale es-CO, 375x667, 390x844 y escritorio 1280x900.
Incluye POST decimal realmente serializado, tabla contenida, errores conservados
sin JavaScript y confirmacion bancaria con nota vacia. No certifica Safari/iPhone
o Chrome/Android reales. Muestras sinteticas fuera del repositorio:
`C:/Users/User/AppData/Local/Temp/fundetec-breb-movil-smij5538`.

Una prueba adicional detecto un error que se ocultaba tras una confirmacion
inmutable con payload diferente. Se corrigio solo el resumen de errores; el
ingreso historico no cambia. Check sin incidencias, makemigrations sin cambios,
diff --check y node --check del test modificado correctos. No se ejecuto la
suite educativa completa ni se implementaron aplicaciones contables, saldos
reales, politicas pendientes, migraciones nuevas o cambios de la API contractual.
