# Tablero de recaudos y aplicaciones para Operaciones

Base: `origin/main` en `c7ed9b7`, posterior al merge del PR #6. Esta entrega
es de consulta; no registra ni aplica dinero. No incluye la anulacion pendiente
en otro worktree, el reparto 80/20, cambios contractuales ni migraciones.

## Acceso y recorrido

Ruta: `/financiacion-educativa/operaciones/recaudos/tablero/`.
Acceso desde el menu **Tablero de recaudos** o desde la bandeja **Recaudos**.
Requiere los permisos existentes `acceder_dashboard_operativo` y
`consultar_recaudos_educativos`, y `FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=true`.
Ser staff o miembro de una institucion no concede acceso. No se conceden permisos
de registro, conciliacion o aplicacion por consultar el tablero.

1. Elegir institucion (o todas) y fechas efectivas bancarias inclusivas.
   El rango inicial es desde el primer dia del mes actual hasta hoy.
2. Abrir un indicador para ver sus movimientos con la misma cohorte.
   La seleccion del listado no cambia los indicadores generales de la cohorte.
3. Abrir un movimiento para continuar con el detalle operativo ya existente.
   Sus acciones siguen requiriendo sus permisos especificos.

Todo funciona con formularios GET, enlaces y details nativos, sin JavaScript.
Los filtros se conservan al paginar; la suma del listado corresponde a todas
sus paginas, no solo a la pagina visible. Un filtro invalido devuelve 400 sin
mostrar cifras ni movimientos.

## Definiciones y fuentes

| Indicador | Fuente persistida | Desglose |
| --- | --- | --- |
| Confirmados en banco | Suma `ConciliacionRecaudoEducativo.monto_recibido` | Incluye pendientes y aplicados; no son cuotas pagadas |
| Pendientes de aplicar | Conciliaciones sin `aplicacion` | No se inventa capital ni interes antes de aplicar |
| Aplicados a cuotas | Conciliaciones con `AplicacionCuotaEducativa` | Suma recibida aplicada; debe coincidir con capital + interes aplicados |
| Capital aplicado | `AplicacionCuotaEducativa.capital_aplicado` | Capital de la cuota contractual, no capital solicitado ni saldo |
| Intereses aplicados | `AplicacionCuotaEducativa.interes_aplicado` | No interes proyectado, no reparto entre partes |

La cohorte siempre usa `ConciliacionRecaudoEducativo.fecha_efectiva` y
`reporte.solicitud.institucion_id`. No usa la fecha declarada, radicacion,
creacion del reporte, confirmacion o creacion de la aplicacion. Las instituciones
inactivas siguen siendo filtrables para consultar su historico. Incluye ingresos
registrados por Operaciones y los originados en reportes del estudiante.
Los reportes sin conciliacion y los rechazados no son dinero confirmado.

El resumen se obtiene en una consulta agrupada por mes bancario con relaciones
OneToOne, sin multiplicar importes por cuotas del mismo contrato. Las tarjetas
se suman desde esos mismos meses. Los listados usan el mismo selector y
predicados de aplicacion. Solo se muestran meses con movimientos dentro del
rango; no son cierres contables ni cortes historicos congelados.

Confirmados = pendientes + aplicados; aplicado = capital aplicado + intereses
aplicados, bajo las invariantes del servicio de aplicacion integra vigente.
Una aplicacion registrada hoy para un ingreso de septiembre actualiza la
cohorte bancaria de septiembre. No se recalcula ni modifica el historico.
Si otro operador aplica un ingreso entre consultas, refrescar el tablero y el
listado: son consultas vivas, no una fotografia de conciliacion a corte.

No se descompone el capital financiado en comision, IVA, garantia o seguro,
ni se interpreta el capital como saldo de deuda. No se calcula reparto 80/20,
mora, paz y salvo, caja contable o resultados por devengo.

## Privacidad

El tablero muestra referencia de obligacion, institucion, fecha bancaria,
importes y numero de cuota aplicada. No muestra documentos, correos, rutas
privadas, evidencia bancaria, referencias bancarias internas ni identidad del
operador del movimiento. Los enlaces al detalle conservan su control de acceso.
Las respuestas son privadas y no almacenables en cache.

## Validacion en staging, despues de aprobar el PR

Sin ejecutar este procedimiento desde el desarrollo local:

1. Revisar el SHA aprobado y actualizar staging por el procedimiento habitual;
   recopilar estaticos y ejecutar `staging_manage check`. No hay dependencias,
   variables nuevas ni migraciones en este bloque. No iniciar proveedores o
   workers adicionales para consultar el tablero.
2. Con un operador autorizado, abrir el tablero. Elegir una institucion y un
   rango con conciliaciones sinteticas conocidas; abrir cada tarjeta. Comprobar
   cantidad y suma del listado completo, capital e intereses de las aplicaciones
   existentes, limites inclusivos y resumen mensual. Un ingreso confirmado sin
   aplicacion debe seguir pendiente y sin desglose. No aplicar un pago real.
3. Cambiar institucion, probar un rango vacio y una fecha invalida. Repetir a
   375/390 px y escritorio sin JavaScript. Un estudiante o miembro institucional
   sin permisos operativos debe recibir 403 por URL directa. Revisar que la
   bandeja y el detalle operativos existentes sigan funcionando.

Reversion: volver al SHA anterior aprobado y recopilar estaticos mediante el
procedimiento habitual. No borrar conciliaciones ni aplicaciones; este bloque
no escribe registros de negocio ni introduce esquema.

## Pruebas y capturas locales

Las pruebas nuevas usan dos instituciones sinteticas, ingresos registrados por
Operaciones, reportes pendientes/rechazados y aplicaciones oficiales simuladas
con antivirus falso local. No hay correos ni llamadas externas. PostgreSQL es
temporal y aislado; nunca se usan staging o sus credenciales.

Modulos nuevos: `test_tablero_recaudos` y `test_tablero_recaudos_browser`.
El segundo utiliza Playwright local mediante `EDU_PLAYWRIGHT_MODULE` y escribe
capturas en `EDU_COLLECTIONS_SCREENSHOTS`. Sin esa herramienta se omite
explicitamente la prueba de navegador, no la validacion PostgreSQL.

Capturas sinteticas completas en `capturas/tablero-recaudos-20261112/`:
tablero, aplicados, pendientes filtrados por institucion, mes bancario, vacio
y filtro invalido, a 375, 390 y 1440 px. El navegador bloquea recursos externos;
fuentes e iconos CDN no forman parte de esta comprobacion.

Validacion local final del 2026-11-12: PostgreSQL 16.13 en contenedor Docker
temporal con almacenamiento tmpfs, eliminado al terminar. **123/123 OK en
221.964 segundos; cero omisiones**, incluidas las diez concurrencias existentes
de registro/conciliacion/escaneo/aplicacion y el recorrido de navegador.
La seleccion fue:

```text
python manage.py test financiacion_educativa.tests.test_tablero_recaudos financiacion_educativa.tests.test_tablero_recaudos_browser financiacion_educativa.tests.test_portal_operaciones_recaudos financiacion_educativa.tests.test_recaudos_breb financiacion_educativa.tests.test_ingreso_operaciones financiacion_educativa.tests.test_aplicacion_cuotas financiacion_educativa.tests.test_dashboard_operativo --noinput --keepdb
```

Se ejecuto con `DJANGO_LOAD_DOTENV=false`, `DEPLOYMENT_ENVIRONMENT=test`,
`DATABASE_URL` del PostgreSQL temporal local, `DEBUG=true` y las dos variables
de Playwright anteriores. Los proveedores/correos estan deshabilitados mediante
los overrides de pruebas. No ejecutar ese comando contra una base compartida.
No se repitio la suite educativa completa. Las 16 pruebas nuevas de consulta
y la prueba nueva de navegador estan incluidas en las 123.

`manage.py check`: 0 issues. `makemigrations --check --dry-run`: No changes
detected. `git diff --check` y `node --check` del test de navegador: salida
vacia y codigo 0. El warning de WhiteNoise por no tener `staticfiles/` en el
worktree no impidio servir y verificar los assets locales con StaticLiveServer.
Estos resultados son locales; no certifican un despliegue en staging.
