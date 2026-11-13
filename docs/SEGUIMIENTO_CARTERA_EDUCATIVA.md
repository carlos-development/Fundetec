# Seguimiento operativo de cartera educativa

Base: `origin/main` en `bbddc11`, merge del PR #9. Conserva la anulacion local
en su worktree y no cambia firma, contratos, API, reglas financieras o modelos.
El PR #7 y el PR #8 estan desplegados segun confirmaciones previas de Carlos;
los avisos BRE-B continuan desactivados. El merge del PR #9 se verifico en Git,
sin inferir que una fusion certifica un despliegue o una activacion.

## Recorrido y permisos

Menu Operaciones -> **Seguimiento de cartera** -> indicador -> listado por
cuota o por obligacion -> **Abrir caso** -> recaudo abierto existente o
detalle contractual de obligacion. No hay formulario nuevo para registrar,
conciliar, aplicar, cobrar o contactar.

```text
GET /financiacion-educativa/operaciones/seguimiento-cartera/
GET /financiacion-educativa/operaciones/seguimiento-cartera/<application_id>/
```

Requiere los permisos existentes, simultaneamente:

- `financiacion_educativa.acceder_dashboard_operativo`;
- `financiacion_educativa.consultar_solicitudes_operativas`;
- `financiacion_educativa.consultar_recaudos_educativos`.

No basta is_staff ni una membresia institucional. El alcance operativo es
global por permiso; el filtro de institucion no concede permisos a un tenant.
No se agrega este modulo al portal institucional. Las acciones existentes de
registro/conciliacion/aplicacion mantienen sus permisos especificos y sus
confirmaciones. Con el canal BRE-B apagado se puede consultar seguimiento;
las acciones del canal siguen sujetas a su flag existente.

Los enlaces de vuelta usan rutas fijas y un whitelist de filtros, no un next
arbitrario. Se conserva institucion, referencia, rango contractual, situacion,
bandeja, agrupacion y pagina al volver desde el detalle de seguimiento,
recaudo o contrato. El retorno se conserva en navegacion de consulta GET;
los POST y redirecciones de acciones mantienen su flujo operativo existente.

## Definiciones de las bandejas

La cohorte parte de `obligaciones_autorizadas()`, igual que el detalle actual:
estado contractual realmente autorizado, firma vigente y coherente,
fotografia activa/bloqueada no legada. Excluye Sandbox y APPROVED sin firma.
No se crea una segunda definicion de autorizacion.

| Bandeja | Regla |
| --- | --- |
| Vencidas sin aplicacion | fecha_vencimiento anterior a timezone.localdate(), sin AplicacionCuotaEducativa |
| Primeras cuotas vencidas | La regla anterior y numero=1; no significa primera pendiente |
| Vence hoy | fecha_vencimiento igual al dia local, sin aplicacion |
| Proximos vencimientos | Dia siguiente al actual hasta 30 dias inclusive, sin aplicacion |
| Pagos aplicados | Existe aplicacion persistida a la cuota; importe=capital_aplicado+interes_aplicado |

Primera pendiente siempre es el menor numero del calendario que aun no
tiene aplicacion, aunque este fuera del rango seleccionado. Una obligacion
con cuota 1 aplicada y cuota 2 vencida no cuenta en Primeras cuotas vencidas.
Las vencidas se ordenan del vencimiento mas antiguo al mas reciente; prioridad
de recaudo y claves estables desempatan. Los rangos rapidos Hoy/7/30 dias
sustituyen solo el rango y la bandeja, conservando institucion y situacion.

Todos los filtros de fecha consultan **vencimiento contractual**, inclusive
desde/hasta, no fecha efectiva bancaria, creacion ni radicacion. En Aplicados
la fecha efectiva bancaria se muestra por separado, junto a capital e interes
de la aplicacion. El resumen por obligacion conserva el desglose aplicado;
las fechas bancarias se consultan en la vista por cuota.

Es una consulta actual: si se aplica hoy una cuota de septiembre, deja de
aparecer como pendiente aun filtrando septiembre. No reconstruye un saldo
historico ni un corte contable.

Los indicadores distinguen cantidad de cuotas, cantidad de obligaciones e
importe contractual (o importe aplicado). Cada indicador utiliza exactamente
el mismo selector que su listado de respaldo. Los totales cubren el resultado
completo, no solo las 25 filas de una pagina. La vista por obligacion agrupa
las cuotas seleccionadas y tambien informa cuantas estan vencidas en todo el
contrato y cual es su primera pendiente. Se indica expresamente la diferencia
entre seleccion filtrada y contrato completo.

## Cruce con recaudos

Solo cuenta reportes/ingresos cuya solicitud y fotografia correspondan a la
misma obligacion y version vigente:

1. **Ingreso por aplicar**: conciliacion bancaria sin aplicacion.
2. **Reporte por verificar**: sin conciliacion ni rechazo, si no existe el
   caso anterior.
3. **Sin recaudo abierto**: ninguno; verificar el banco antes de contactar.

Si coinciden ambos, el principal es ingreso por aplicar; el contador de
reportes permanece en el detalle. Un rechazo o un ingreso ya aplicado no
constituyen un recaudo abierto. Una version anterior tampoco crea senales
en la version actual.

Un ingreso y tres cuotas no son tres ingresos. Las senales y sumas bancarias
se agrupan una sola vez por fotografia mediante subconsultas correlacionadas,
sin joins multiplicadores con cuotas. En las filas de cuota se muestra la
situacion de la obligacion, no una imputacion del movimiento a esa cuota.

Si hay varios movimientos, la accion abre el listado **Recaudos abiertos de
esta version**, limitado a esa obligacion; desde cada movimiento se abre el
detalle de recaudos ya existente. Si el importe o la finalidad no corresponden
a la primera pendiente, aparece **Requiere revision**, sin sugerir aplicacion
directa. Esta comprobacion de presentacion no valida antivirus ni garantiza
elegibilidad: el servicio oficial vuelve a validar cuenta, evidencias,
integridad, finalidad, cuota, permisos y confirmacion antes de una aplicacion.

No se presupone que un ingreso confirmado cubra una cuota ni se solicita otra
transferencia mientras exista un reporte/ingreso abierto. El modulo no obtiene
ni presenta evidencia bancaria, referencias internas o identidad de operadores.
El acceso a esa evidencia permanece en el flujo privado original.

## Portal del estudiante

La primera cuota sin aplicar recibe una de tres etiquetas: **Vencida**,
**Vence hoy**, **Proximo vencimiento**. La fecha se evalua en la zona horaria
del sistema. Un reporte abierto muestra **Reporte en revision** y un ingreso
abierto muestra **Transferencia confirmada**, con la segunda senal cuando
ambas coincidan. El texto habla de la financiacion, no afirma que ese movimiento
cubra la cuota mostrada. No se ofrece otra transferencia por esa espera.
Se conservan historial, descarga privada del plan y titularidad existente.
No se expone el nuevo seguimiento operativo ni informacion bancaria interna.

## Limites e invariantes

Sin nuevas tablas, migraciones, permisos o flags. No escribe registros
financieros ni crea outbox al abrir pantallas. No activa avisos BRE-B ni
recordatorios. No calcula intereses de mora, cargos, saldo vigente,
liquidacion, parciales, excedentes, reversos o reparto 80/20. Calendario,
fotografias, aplicaciones, evidencias y firmas existentes quedan intactos.

Las consultas son vivas: si otro operador aplica entre indicador y apertura
del listado, refrescar ambos. No se bloquean escrituras financieras para
congelar un informe. El numero de consultas del listado no crece por fila;
se prueban las variantes por cuota y obligacion. Antes de uso masivo debe
observarse rendimiento con el volumen real, sin inventar resultados de carga.

## Staging y reversion

No ejecutado en esta entrega:

1. Revisar el SHA aprobado y actualizar staging mediante el procedimiento
   habitual. Ejecutar `staging_manage check` y recopilar estaticos. No hay
   dependencias, variables o migraciones nuevas. Reiniciar web, sin activar
   workers, proveedores ni avisos adicionales.
2. Usar contratos sinteticos de dos instituciones: vencimientos ayer/hoy/manana,
   cuota 1 aplicada con cuota 2 vencida, varios vencimientos con un ingreso
   abierto, reporte rechazado y reporte pendiente. Comprobar indicadores frente
   a listados completos, agrupacion, filtros, orden, paginacion y regreso.
3. Abrir el recaudo desde el caso y verificar los permisos originales; no
   registrar/aplicar un pago real. Con estudiante comprobar etiquetas, mensaje
   de seguimiento, historial y plan PDF. Otra cuenta o un miembro institucional
   sin permisos operativos debe recibir 403 en cartera y 404 en finanzas ajenas.
4. Verificar Safari/iPhone y Chrome/Android reales. Las capturas locales son
   Chromium/Edge sin JavaScript a 375, 390 y 1440 px, no certificacion de esos
   dispositivos. Reversion: SHA previo aprobado y estaticos anteriores, sin
   borrar datos ni cambiar flags. No hay esquema que revertir.

## Comprobacion productiva posterior del caso real

El importe productivo indicado por Carlos no se usa como fixture. Las pruebas
emplean importes sinteticos del motor y un ingreso de 125000 COP, sin datos de
ese caso. No se ha accedido a produccion ni se ha alterado su pago.

Despues del despliegue aprobado, ejecutar desde el release/venv y entorno
productivos oficiales `python manage.py shell`, o el wrapper de gestion
productivo ya autorizado. No copiar rutas, credenciales o wrappers de staging.
Pegar este diagnostico **solo de lectura**, introduciendo el UUID de la
solicitud por canal seguro. No imprime correos, documentos, tokens, soportes,
referencias bancarias o nombres del operador:

```python
from getpass import getpass
from uuid import UUID
from django.utils import timezone
from financiacion_educativa.dashboards.operaciones.cartera import obligaciones_autorizadas
from financiacion_educativa.services.seguimiento_cartera import anotar_seguimiento, reportes_abiertos

application_id = UUID(getpass('UUID de la solicitud (no se muestra): '))
hoy = timezone.localdate()
consulta = anotar_seguimiento(obligaciones_autorizadas(), fotografia_ref='pk', solicitud_ref='solicitud_id', hoy=hoy)
foto = consulta.filter(solicitud_id=application_id).first()
print('DIA_LOCAL=', hoy, 'ZONA=', timezone.get_current_timezone_name())
print('CONTRATO_AUTORIZADO=', foto is not None)
if foto:
    print('VERSION=', foto.numero_version, 'VENCIDAS=', foto.vencidas_obligacion,
          'PRIMERA_PENDIENTE=', foto.primera_pendiente, 'FECHA=', foto.primera_fecha)
    print('SITUACION=', foto.estado_recaudo, 'INGRESOS_ABIERTOS=', foto.ingresos_abiertos,
          'REPORTES_ABIERTOS=', foto.reportes_abiertos, 'IMPORTE_RECIBIDO_ABIERTO=', foto.importe_ingresos)
    for cuota in foto.cuotas.order_by('numero').values('numero', 'fecha_vencimiento', 'valor_cuota', 'aplicacion_recaudo__id'):
        print('CUOTA=', cuota['numero'], 'VENCIMIENTO=', cuota['fecha_vencimiento'],
              'IMPORTE_CONTRACTUAL=', cuota['valor_cuota'], 'TIENE_APLICACION=', cuota['aplicacion_recaudo__id'] is not None)
    print('MOVIMIENTOS_ABIERTOS_EN_ESTA_VERSION=', reportes_abiertos(foto).count())
```

Contrastar la salida con la pantalla y abrir el ingreso ya existente para
revisar su elegibilidad. Ese diagnostico no debe invocar registrar, confirmar,
aplicar, reemitir, recuperar outbox o borrar. Si hay un ingreso sin aplicacion,
no solicitar una nueva transferencia; resolverlo en el flujo ya autorizado.

## Evidencia local y pruebas

Capturas completas sinteticas en `capturas/seguimiento-cartera-20261112/`:
resumen, bandeja filtrada por obligacion, detalle, bloque del estudiante,
vacio y filtro invalido, en 375/390/1440 px. JavaScript deshabilitado y red
externa bloqueada en navegador.

Validacion local final del 2026-11-12: **111/111 OK en 188.111 segundos,
cero omisiones**, sobre PostgreSQL 16.13 temporal y aislado con tmpfs. Incluye
24 pruebas nuevas (23 de consulta/portal y un recorrido real de navegador)
y regresiones de portal, obligaciones, Operaciones, recaudos, tablero y PDF.
No se repitio toda la suite educativa ni concurrencias productivas sin cambio
de locks; este incremento solo introduce consultas de lectura.

```text
python manage.py test financiacion_educativa.tests.test_seguimiento_cartera financiacion_educativa.tests.test_seguimiento_cartera_browser financiacion_educativa.tests.test_portal_estudiante financiacion_educativa.tests.test_dashboard_obligaciones financiacion_educativa.tests.test_dashboard_operativo financiacion_educativa.tests.test_portal_operaciones_recaudos financiacion_educativa.tests.test_tablero_recaudos financiacion_educativa.tests.test_plan_contractual_pdf --noinput --keepdb
```

Se uso DJANGO_LOAD_DOTENV=false, DEBUG=true, DEPLOYMENT_ENVIRONMENT=test y
DATABASE_URL del contenedor PostgreSQL local temporal. Las fixtures utilizan
proveedores falsos/correos locmem y mantienen apagados los avisos BRE-B. El
navegador se configura con EDU_PLAYWRIGHT_MODULE y EDU_FOLLOWUP_SCREENSHOTS.
No ejecutar ese comando sobre bases compartidas.

Se probaron dos instituciones, limites de fecha local, cuota 1 aplicada con
cuota 2 vencida, reportes pendientes/rechazados, ingresos confirmados/aplicados,
senales simultaneas, diferencia de importe, otra version contractual,
indicadores/listados, paginacion, consultas constantes sin N+1, permisos por
URL y ausencia de mutaciones financieras/outbox por GET. Se comprobo vuelta
filtrada desde el recaudo y el contrato, y el flujo del estudiante conservando
historial/PDF sin acceso a evidencia interna.

`manage.py check`: 0 issues. `makemigrations --check --dry-run`: No changes
detected. `git diff --check` y `node --check`: codigo 0, sin salida. La
advertencia local de WhiteNoise por staticfiles ausente no impidio servir
los assets mediante StaticLiveServer. Las 18 capturas fueron regeneradas
desde las paginas reales con datos sinteticos y se inspeccionaron movil y
escritorio. El contenedor y sus bases se eliminaron al terminar.

No se usaron datos del caso productivo, servidores, proveedores o correos
reales. La anulacion se conserva fuera de la rama. No hay cambios en modelos,
migraciones, firma, servicios de registro/conciliacion/aplicacion ni outbox.
Los resultados son locales y no certifican un despliegue.
