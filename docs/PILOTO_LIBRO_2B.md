# Piloto controlado del Libro 2B

Base: `origin/main`, `7fc37739f5f73f665355b0aafc7a880d601c6d58` (PR #11).
Carlos confirma ese release desplegado en produccion con
`FINANCIACION_EDUCATIVA_LIBRO_ENABLED=false`. Este incremento NO se ha desplegado
ni probado en staging. La evidencia de esta entrega procede exclusivamente de
PostgreSQL temporal local, datos sinteticos, correo simulado y sin proveedores.

## Controles y versiones

Ruta inicial: `/financiacion-educativa/operaciones/politicas-libro/`.
Preparar una propuesta exige seleccionar explicitamente TODAS las reglas, una
version, intervalo de fechas bancarias y respaldo documental verificable. No
hay politica comercial por defecto. Una propuesta es inmutable: para corregir
reglas, respaldo, fechas o clasificacion se prepara otra version.

1. Preparador registra reglas y respaldo.
2. Otro usuario revisa favorablemente o rechaza la propuesta.
3. Un aprobador distinto del preparador confirma con respaldo. Puede ser el
   mismo revisor, pero nunca el preparador. Una politica no sintetica exige
   ademas confirmar expresamente respaldo comercial y uso real.
4. La aprobacion crea la version operable y un evento inmutable. La consulta
   muestra propuesta, reglas y eventos/autores. Admin permanece de solo lectura.

Fecha de seleccion: **fecha efectiva bancaria del ingreso**, no fecha de registro
ni fecha de confirmacion. Desde/hasta son inclusivas; hasta vacio es indefinido.
No se admiten dos intervalos aprobados activos solapados, tampoco con dos
aprobadores concurrentes. Versiones contiguas requieren periodos no solapados.
La aprobacion se serializa mediante lock de Institucion, no por el formulario.

Reemplazar exige una version nueva y conservar el MISMO intervalo completo:
retira la anterior solo para nuevas escrituras y aprueba la nueva atomicamente.
No se implementa el fraccionamiento de un intervalo abierto ni su cierre
retroactivo. Para una sucesion temporal prevista, preparar intervalos finitos
adyacentes desde el principio; si ya existe un intervalo abierto y se necesita
partirlo, detener y revisar ese procedimiento en un incremento posterior.
Retirar no elimina eventos, propuestas, politicas ni movimientos anteriores.
Cada movimiento mantiene FK a su politica original y sello de control/alcance.
El reemplazo no recalcula ni reinterpreta los movimientos existentes.

Las politicas previas a 0035 mantienen `propuesta=NULL` y `es_sintetica=NULL`:
no se reclasifican ni se consideran una aprobacion nueva. Siguen consultables
en Admin/libro historico, pero no habilitan nuevas contabilizaciones. Las
politicas sinteticas solo se preparan/aprueban en `staging` o `test`; produccion
las rechaza tambien al resolver y confirmar, aunque accidentalmente existan.

## Permisos y alcance institucional

Se requieren usuario activo y `acceder_dashboard_operativo`, mas el permiso de
la accion y un alcance institucional explicito vigente. Ni staff ni superusuario
sustituyen el alcance. El servicio recarga los permisos al confirmar.

| Accion | Permiso `financiacion_educativa.*` | Alcance |
| --- | --- | --- |
| Preparar | `preparar_politicas_libro` | PREPARE |
| Revisar | `revisar_politicas_libro` | REVIEW |
| Aprobar/retirar | `aprobar_politicas_libro` | APPROVE |
| Contabilizar | `aplicar_cuotas_educativas` y consulta de recaudos | POST |
| Compensar | `compensar_libro_educativo` y consulta de recaudos | COMPENSATE |
| Activar/detener piloto | `controlar_piloto_libro` | CONTROL |
| Asignar/revocar alcances | `gestionar_alcances_libro` | Administracion global explicita |

Los nombres completos de base y consulta son
`financiacion_educativa.acceder_dashboard_operativo` y
`financiacion_educativa.consultar_recaudos_educativos`. Asignar permisos mediante el
procedimiento administrativo existente. La pantalla `politicas-libro/alcances/`
no concede permisos Django: registra habilitaciones/revocaciones institucionales
con actor y motivo. No crea membresias de clientes ni amplia su portal.

Compensar no exige POST: debe poder autorizarse a un responsable independiente
que no contabilice. Exige ser distinto del actor del original. Solo compensa
TODO el ultimo movimiento nuevo, sin sucesores, sin compensacion previa, motivo
y token firmado de 15 minutos. No compensa aplicaciones integras historicas,
lineas aisladas ni pagos con movimientos posteriores. No es anulacion contractual
ni devolucion bancaria. Original, lineas y fecha bancaria permanecen intactos.

## Matriz de reglas y decisiones

| Tema | Existente en PR #11 | Agregado para piloto | Decision antes de produccion |
| --- | --- | --- | --- |
| Parciales/vencidos | INTEREST_FIRST o CAPITAL_FIRST, OLDEST_FIRST | Seleccion completa versionada, prueba comparativa | Aprobar orden contractual e imputacion de vencidos |
| Excedentes | UNALLOCATED: sin destino | Cuadre por ingreso y superficies | Destino futuro y autoridad; no devolucion automatica |
| Retroactividad | REJECT_BEFORE_LAST | Codigo diagnostico estable y prueba integral | Alcance de revision; no reordenar ni cambiar fecha bancaria |
| Centavos | Banco exacto, contrato HALF_UP | Bloqueos explicitos y prueba 0.01 | Residuales/ultima cuota; sin tolerancia silenciosa |
| Compensaciones | Ultimo nuevo completo sin sucesores | Autorizacion independiente, token, UI y apagado serializado | Responsables, limites, evidencia y efectos fuera del libro |
| Abono | Interes causado DAILY_30, conserva cuota/reduce plazo | Perfil aprobado sintetico y comprobacion entre pantallas | Aprobar causacion y trato de vencidos |
| Pago total | Capital pendiente + causado, excluye futuro | Excedente visible y compensacion ensayada | Cierre legal/constancias fuera del alcance |
| Interes anticipado | BLOCK_REPROJECTION | Revision explicita, no reutiliza ni pierde credito | Acreditar/devolver/conservar; sin regla implicita |
| Gobierno | Politicas inmutables, sin aprobacion operativa | Propuesta/revision/aprobacion, vigencia, alcances, control unico | Respaldo comercial real y responsables |

El abono con vencimientos pendientes se bloquea: no refinancia atrasos.
Intereses anticipados que impidan reproyeccion tambien bloquean abono/total.
Los errores dejan el ingreso confirmado y sin aplicar; no generan otro reporte.
Mora, reparto 80/20, correos y paz y salvo permanecen fuera del piloto.

## Escenarios repetibles y resultados COP

Todos los valores siguientes son sinteticos: solicitado 1.000.000; capital
contractual financiado 1.142.711; 12 meses; 1% mensual; inicio 2026-07-01;
cuota 101.528, vencimientos desde 2026-08-01. Cada escenario comienza de cero,
salvo donde se indica. Fecha bancaria principal 2026-07-16.

| Caso | Recibido | Capital | Intereses | Sin destino | Resultado |
| --- | ---: | ---: | ---: | ---: | --- |
| Cuota integra | 101.528 | 90.101 | 11.427 | 0 | FULL, primera cuota cubierta |
| Parcial 2026-10-05, intereses primero | 100.000 | 88.573 | 11.427 | 0 | OLDEST_FIRST, aun no cuota completa |
| Mismo parcial, capital primero (comparacion) | 100.000 | 90.101 | 9.899 | 0 | Otra distribucion, no mezclar politicas en un asiento |
| Abono | 400.000 | 394.286 | 5.714 | 0 | Reduce plazo, mantiene cuota, ajusta ultima |
| Total con excedente | 1.153.925 | 1.142.711 | 5.714 | 5.500 | Capital proyectado cero, NO cierre legal |
| Total con faltante 0.01 | 1.148.424,99 | 0 | 0 | 1.148.424,99 | Bloqueado; ingreso sigue confirmado |
| Retroactivo 2026-07-19, tras pago del 2026-07-20 | 50.000 | 0 | 0 | 50.000 | Bloqueado; conserva fecha real y movimientos |

Por cada ingreso: **recibido = capital neto + intereses netos + sin destino**.
Tras compensar el ultimo movimiento completo, sus capital/intereses netos son
cero y el dinero vuelve a sin destino; no se duplica recibido. Una aplicacion
integra anterior se incorpora virtualmente UNA vez desde su registro existente.
No hay backfill ni recontabilizacion. El PDF guardado se conserva byte a byte;
las condiciones API, fotografia y calendario firmado permanecen iguales.

Recorrido humano en la BD sintetica: conciliar con evidencia SAFE simulada ->
Libro del ingreso -> vista previa -> politica revisada y aprobada -> recargar
vista previa -> motivo y confirmacion -> consultar Libro/Tablero/Seguimiento/
portal del titular -> otro operador previsualiza y confirma compensacion ->
consultar de nuevo y cuadrar. No volver a reportar ni conciliar el mismo ingreso.
El estudiante no accede a la evidencia bancaria, referencia interna ni actor.

## Repeticion local o en PostgreSQL temporal de staging

No se ha ejecutado este procedimiento en staging. Debe repetirse despues de
autorizar y desplegar el release. No usar `staging_manage test` sobre la BD
compartida ni copiar credenciales/archivos reales a las fixtures.

En un host de pruebas con Docker y dependencias ya disponibles (no instalar ni
activar servicios externos), ejecutar desde el checkout del SHA aprobado:

```bash
bash <<'BASH'
set -euo pipefail
cd /var/www/fundetec-staging/current
PYTHON=/var/www/fundetec-staging/shared/venv/bin/python
CONTAINER=fundetec-libro-piloto-pg-test
docker inspect "$CONTAINER" >/dev/null 2>&1 && { echo 'Nombre ocupado: detener'; exit 1; }
SECRET=$(openssl rand -hex 24)
CID=$(docker run -d --name "$CONTAINER" --publish 127.0.0.1:55454:5432 \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=piloto_test -e POSTGRES_DB=piloto_test \
  -e POSTGRES_PASSWORD="$SECRET" postgres:16-alpine)
trap 'docker rm -f --volumes "$CID" >/dev/null; unset SECRET' EXIT
for n in $(seq 1 30); do
  docker exec "$CID" pg_isready -U piloto_test -d piloto_test && break
  sleep 1
done
docker exec "$CID" pg_isready -U piloto_test -d piloto_test
env -i PATH="$PATH" HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
  DJANGO_LOAD_DOTENV=false DEBUG=true DEPLOYMENT_ENVIRONMENT=test \
  DATABASE_URL="postgresql://piloto_test:$SECRET@127.0.0.1:55454/piloto_test" \
  "$PYTHON" manage.py test \
  financiacion_educativa.tests.test_gobernanza_libro \
  financiacion_educativa.tests.test_piloto_integral_libro \
  financiacion_educativa.tests.test_libro_pagos \
  financiacion_educativa.tests.test_aplicacion_cuotas \
  financiacion_educativa.tests.test_tablero_recaudos \
  financiacion_educativa.tests.test_seguimiento_cartera \
  financiacion_educativa.tests.test_portal_estudiante \
  financiacion_educativa.tests.test_plan_contractual_pdf \
  financiacion_educativa.tests.test_portal_operaciones_recaudos \
  financiacion_educativa.tests.test_recaudos_breb --noinput -v2
BASH
```

El script crea/elimina su propio contenedor/base, no carga staging.env y no
arranca worker/outbox. Los tests proporcionan correo/proveedores/archivos falsos.
Las esperas solo comprueban disponibilidad del contenedor, no resuelven carreras.
Si Docker o permisos de pruebas faltan, parar: no recurrir a la BD compartida.

Para navegador local ya instalado, agregar `test_piloto_libro_browser` y
`test_libro_pagos_browser`; configurar `EDU_PLAYWRIGHT_MODULE` con la ruta del
Playwright existente y `EDU_PILOT_SCREENSHOTS`/`EDU_LEDGER_SCREENSHOTS` fuera de
datos reales. El harness usa Edge headless, bloquea red no local y funciona sin
JavaScript. En Linux sin Edge disponible no afirmar que esa parte fue repetida:
revisar las capturas versionadas y ejecutar el recorrido manual del SHA en
staging con una BD aislada autorizada. Capturas: `capturas/piloto-libro-2b/`.

## Migracion y staging futuro

Unica migracion nueva: **0035_gobernanza_piloto_libro**, depende de 0034,
instituciones.0003 y AUTH_USER_MODEL. Agrega tres campos NULL a politica y cinco
tablas de gobierno con FK PROTECT, unicidades, CHECK e indice de alcances.
Inicializa solo el control global cerrado (pk=1). No asigna permisos/alcances a
usuarios, no crea politicas y no modifica movimientos, contratos ni 0031/0032.
Permisos Django se crean mediante post_migrate. El reverse de la semilla es noop;
deshacer el esquema elimina evidencia de gobierno, por lo que NO es rollback
operativo permitido una vez utilizado.

Antes: respaldo PostgreSQL/privados, SHA y arbol limpio, verificar ambiente,
mantener flag false y los flags/correos existentes sin cambios. Tras autorizacion
del release, revisar y ejecutar con el mecanismo habitual de staging:

```bash
cd /var/www/fundetec-staging/current
staging_manage migrate --plan
staging_manage sqlmigrate financiacion_educativa 0035
staging_manage migrate financiacion_educativa
staging_manage check
staging_manage collectstatic --noinput
```

No ejecutar estas ordenes contra produccion como parte del piloto. No modificar
ni arrancar workers/outbox por este procedimiento. El checkout y reinicio deben
fijarse al SHA aprobado del PR, nunca a una rama movible sin verificar.

## Diagnostico de solo lectura

```bash
staging_manage diagnosticar_libro_2b --institucion-id UUID \
  --desde YYYY-MM-DD --hasta YYYY-MM-DD --actor-id ID_OPERADOR \
  --modo PARTIAL --limit 100
```

El comando usa transaccion PostgreSQL REPEATABLE READ, READ ONLY. Muestra flag,
control/revision, politica vigente para `hasta`, cantidades historicas sin
gobierno, totales de cohorte bancaria y cuadre. Cada caso se evalua con su propia
fecha bancaria y se limita al intervalo/institucion. No imprimir nombres,
correos, cedulas, referencias bancarias, rutas, URL privadas, tokens ni claves.
El UUID de ingreso es tecnico, no una identificacion personal. Con actor verifica
sus gates; con flag apagado puede informar FLAG_DISABLED antes del motivo
financiero. El diagnostico NO abre ni revalida archivos y no sustituye la vista
previa ni la confirmacion. Sin `--actor-id` no certifica permisos operativos.

## Activacion limitada y apagado

1. Completar pruebas en staging aislado y aprobar TODAS las decisiones comerciales
   de la matriz, respaldo, responsables y alcance de compensaciones. No marcar
   una politica sintetica como real para saltar esta revision.
2. Con flag global false: permisos Django y alcances de los responsables solo
   para UNA institucion; preparar, revisar y aprobar politica. En staging puede
   ser sintetica; produccion exige no sintetica y respaldo comercial explicito.
   Antes de abrir, diagnosticar fecha de hoy y fecha bancaria del primer caso.
3. En `politicas-libro/control/`, activar esa institucion con motivo. Requiere
   politica vigente HOY; la contabilizacion revalida la fecha bancaria del caso.
   El flag false sigue impidiendo escrituras. Otra institucion no puede abrirse
   simultaneamente; primero se debe detener la actual.
4. Solo tras autorizacion expresa: cambiar el flag existente a true mediante el
   procedimiento de entorno/reinicio de procesos web aprobado. No activa IA,
   automatizacion, correo ni workers. Diagnosticar nuevamente con el actor real.
5. Primer ingreso autorizado: nueva vista previa, confirmar UNA vez, guardar ID
   del movimiento y comprobar sello de politica/control/alcance, seq, netos,
   tablero, seguimiento, titular y contrato. Reintento exacto conserva ese ID.
   El caso no elegible permanece confirmado, sin una aplicacion inventada.
6. Apagar desde `politicas-libro/control/` (OFF + motivo): serializa con el mismo
   lock de las contabilizaciones. Espera las transacciones anteriores; despues
   de confirmar OFF ningun nuevo POST/compensacion 2B debe entrar. Comprobar con
   vista previa anterior que falla, consultar el movimiento ya contabilizado y
   cuadrar. Este apagado y su carrera se ensayan en los tests PostgreSQL.
7. Dejar tambien el flag false mediante el procedimiento autorizado y reinicio.
   No borrar movimientos ni revertir migraciones 0034/0035. Conservar lector,
   tablero, seguimiento, portal y PDF. Si existen movimientos nuevos NO volver
   a codigo que los ignore. No compensar masivamente para simular un rollback.

El apagado controla escrituras del libro 2B, no elimina la aplicacion integra
legacy para obligaciones que nunca entraron al libro nuevo. En obligaciones
con libro, su guardia existente impide la aplicacion paralela legacy. Un replay
despues de OFF puede quedar bloqueado por el gate: consultar el movimiento
existente, no crear otro ingreso ni volver a conciliar para forzarlo.

## Evidencia de esta entrega

La regresion local incluye politicas, concurrencias de aprobacion/apertura/
apagado, carreras de contabilizacion/compensacion, replays, previews vencidas o
alteradas, revocacion de permisos/alcance, cambio de politica/secuencia/control,
aislamiento institucional, historico virtual unico, contrato/PDF y superficies.
Validacion local del 2026-11-12, PostgreSQL **16.13** temporal y aislado:

- Regresion ampliada: **210/210 OK**, 553.138 segundos, cero omisiones.
- Cierre posterior al ajuste de revocacion de consulta en cache: **68/68 OK**,
  215.249 segundos, cero omisiones. Ambos conjuntos se solapan: no sumarlos
  como pruebas distintas. El cierre incluye 25 pruebas de gobierno, siete
  integrales, 34 del libro y dos recorridos de navegador (piloto y base).
- Incluye ocho concurrencias del libro/gobierno (cinco existentes y tres nuevas),
  ademas de las regresiones PostgreSQL de aplicacion integra y recaudos.
- 24 capturas nuevas completas a 375/390/1440 px, sin JavaScript y sin red
  externa. Revision visual de previa movil y compensacion escritorio realizada.
- `check`: 0 issues. `makemigrations --check --dry-run`: No changes detected.
  `git diff --check` y `node --check`: codigo 0.
- Plan desde 0034: solo 0035. SQL PostgreSQL inspeccionado y migracion aplicada
  exclusivamente en la base temporal. Control cerrado, revision 0, sin
  institucion; cero propuestas, politicas o movimientos creados por migracion.
- Ajuste minimo encontrado durante el cierre: POST/COMPENSATE comprueban tambien
  el permiso de consulta recargado, no el cacheado en el objeto del operador.
  La prueba de revocacion conserva cero movimientos ante permiso retirado.
- Advertencias locales no bloqueantes: WhiteNoise por staticfiles ausente y
  GLib/UWP durante PDF; StaticLiveServer sirvio los assets. No hubo errores,
  pruebas omitidas, correo real ni llamadas a proveedores.

Modulos de la regresion ampliada (prefijo `financiacion_educativa.tests.`):
`test_gobernanza_libro`, `test_piloto_integral_libro`,
`test_piloto_libro_browser`, `test_libro_pagos`, `test_libro_pagos_browser`,
`test_aplicacion_cuotas`, `test_tablero_recaudos`, `test_seguimiento_cartera`,
`test_portal_estudiante`, `test_plan_contractual_pdf`,
`test_portal_operaciones_recaudos` y `test_recaudos_breb`.
El cierre repitio completos los primeros cinco modulos con
`python manage.py test ... --noinput --keepdb -v1`.

Estas pruebas no sustituyen el ensayo de staging ni autorizan pagos reales.
