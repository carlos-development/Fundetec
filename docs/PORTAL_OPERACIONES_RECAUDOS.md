# Operaciones: Recaudos (2026-11-12)

Base: main `37666490f6bc4e079c654a2a21dec84ebcaa3643`.
Rama: `feat/operaciones-recaudos-20261112`. Solo experiencia operativa; no
incluye anulacion, cambios de Estudiante/Institucion/ZapSign, ni despliegues.

## Bandejas y datos

| Bandeja | Fuente existente | Significado |
| --- | --- | --- |
| Por verificar (inicial) | Reporte sin conciliacion ni rechazo | Aun no acredita ingreso en el banco. |
| Por aplicar | Conciliacion sin AplicacionCuotaEducativa | Ingreso confirmado, cuota aun no pagada por ese registro. |
| Aplicados | Conciliacion con AplicacionCuotaEducativa | Cuota especifica pagada por una aplicacion persistente. |

Los no conciliados siguen disponibles en el filtro Estado. Se conservan las
consultas Todos y Todos los confirmados para enlaces existentes. Los contadores
respetan institucion y rango y no cuentan aplicaciones como nuevas transferencias.
El rango del listado usa creacion del reporte; el total bancario usa fecha
efectiva, como antes. No representa deuda, saldo a corte ni paz y salvo.

## Recorrido sin JavaScript

1. Abrir Obligacion y Registrar pago recibido con los permisos existentes.
2. Digitar cuenta, importe, fecha, referencia y finalidad. Cargar evidencia propia.
   Registrar evidencia y Consultar estado conservan los campos y la clave del
   formulario mediante POST/CSRF, sin guardar datos bancarios en cookies/sesion.
   Si el archivo es invalido debe seleccionarse nuevamente; no se finge una carga.
3. Revisar evidencia abre otra pestana solo si es propia, BANK_EVIDENCE, SAFE e
   integra. El endpoint exige acceso al dashboard, consulta de recaudos y permiso
   de registrar o conciliar. Los soportes no seguros no se sirven. No se exponen
   rutas privadas ni se modifica la descarga de evidencia de una conciliacion.
4. Estados: Revision pendiente (esperar), Lista para verificar (inspeccionar y
   seleccionar), Rechazada por seguridad (otro soporte), Error tecnico (reintento
   mediante el mecanismo operativo de escaneo existente). Lista no acredita
   autenticidad bancaria: la confirmacion sigue siendo responsabilidad humana.
5. Registrar valida otra vez mediante el servicio oficial y resuelve duplicados
   hacia el recaudo existente. Aplicar requiere su permiso independiente, vista
   previa firmada, intencion verificada y coincidencia exacta con la primera cuota
   sin aplicar. Un bloqueo conserva el ingreso pendiente; no selecciona otra cuota.
6. El resultado muestra la aplicacion persistente y el interes/capital originales.
   No cambia contrato, calendario, conciliacion ni evidencia. La confirmacion de
   reportes del estudiante reutiliza el mismo componente de evidencia y conserva
   referencia, monto, fecha, observacion y justificacion de diferencias.

## Reparto de intereses

El tablero usa el interés neto aplicado de las aplicaciones históricas y del Libro 2B.
Los períodos mantienen la fecha bancaria de la conciliación original, incluso al
compensar. El dinero sin aplicar y los demás conceptos quedan fuera del reparto.

Un convenio aprobado tiene versión, institución, vigencia inclusiva por fecha
bancaria, porcentaje de Aprobado, referencia documental y usuario que lo registra.
La parte de la institución es el complemento a 100 %. El alta en Django Admin
requiere el permiso específico del convenio. Los registros son inmutables y sus
vigencias dentro de una institución deben ser disjuntas.

Aprobado se redondea al centavo con `ROUND_HALF_UP` por ingreso y la institución
recibe el saldo exacto. Para varias cuotas del mismo ingreso, el redondeo acumulado
en orden de cuota distribuye los centavos sin cambiar el total del movimiento.
Los meses y el período suman movimientos ya redondeados. El tablero informa
«Sin política aprobada» cuando corresponda y muestra el interés sin convenio
por separado en períodos mixtos.

Finanzas debe confirmar institución y alcance, documento y versión aprobados,
responsable autorizado, fechas de inicio y fin inclusive, tratamiento de
contratos e ingresos previos, porcentajes de ambas partes, redondeo y residuo,
y atribución de compensaciones a la fecha bancaria original. La migración crea
la tabla vacía. El reparto es un cálculo de consulta del tablero.

## Validacion y capturas

Validacion local: PostgreSQL 16.13 temporal en Docker, sin bases compartidas ni
credenciales del servidor. Resultados finales de los modulos focalizados:

- Registro, aplicacion y recaudos: **82/82 OK** (426.111 s), incluidas las **10
  concurrencias**, ninguna omitida (registro/conciliacion simultaneos, movimiento
  global unico, una aplicacion por ingreso/cuota, escaneo y replay concurrentes).
- UX de recaudos: **38/38 OK** (182.287 s).
- Portal de Operaciones: **11/11 OK** (51.626 s).
- Navegador real sin JavaScript: **1/1 OK** (18.883 s), recorrido completo y
  capturas 375/390/1440 px.
  En total se cubren **132 casos distintos**, incluido el caso de navegador.
- `check`: 0 issues; `makemigrations --check --dry-run`: No changes detected;
  `git diff --check` y `node --check`: sin errores.

Durante las ejecuciones iniciales se corrigio una colision de nombre en un
helper nuevo y se actualizo una asercion de texto anterior; las pruebas afectadas
y sus modulos se repitieron correctamente. No se modificaron servicios para
hacerlas pasar. Advertencia no bloqueante de WhiteNoise: no existe `staticfiles/`
en el worktree sin collectstatic; el servidor de pruebas sirve los assets locales.
No se repitio la suite educativa completa. Esta validacion no certifica staging.
El contenedor PostgreSQL temporal y sus bases se eliminaron al finalizar.

Las capturas del navegador corresponden exclusivamente a datos sinteticos, con
JavaScript deshabilitado, proveedores simulados y sin trafico externo. No son
evidencia de un pago real ni de pruebas en staging.

| Pantalla | 375 px | 390 px | Escritorio 1440 px |
| --- | --- | --- | --- |
| Por verificar | [Ver](capturas/operaciones-recaudos-20261112/01-por-verificar-375.png) | [Ver](capturas/operaciones-recaudos-20261112/01-por-verificar-390.png) | [Ver](capturas/operaciones-recaudos-20261112/01-por-verificar-1440.png) |
| Datos y estados de evidencia | [Ver](capturas/operaciones-recaudos-20261112/02-evidencia-y-datos-375.png) | [Ver](capturas/operaciones-recaudos-20261112/02-evidencia-y-datos-390.png) | [Ver](capturas/operaciones-recaudos-20261112/02-evidencia-y-datos-1440.png) |
| Vista previa y confirmacion | [Ver](capturas/operaciones-recaudos-20261112/03-aplicar-a-cuota-375.png) | [Ver](capturas/operaciones-recaudos-20261112/03-aplicar-a-cuota-390.png) | [Ver](capturas/operaciones-recaudos-20261112/03-aplicar-a-cuota-1440.png) |
| Por aplicar | [Ver](capturas/operaciones-recaudos-20261112/04-por-aplicar-375.png) | [Ver](capturas/operaciones-recaudos-20261112/04-por-aplicar-390.png) | [Ver](capturas/operaciones-recaudos-20261112/04-por-aplicar-1440.png) |
| Resultado | [Ver](capturas/operaciones-recaudos-20261112/05-resultado-375.png) | [Ver](capturas/operaciones-recaudos-20261112/05-resultado-390.png) | [Ver](capturas/operaciones-recaudos-20261112/05-resultado-1440.png) |
| Aplicados | [Ver](capturas/operaciones-recaudos-20261112/06-aplicados-375.png) | [Ver](capturas/operaciones-recaudos-20261112/06-aplicados-390.png) | [Ver](capturas/operaciones-recaudos-20261112/06-aplicados-1440.png) |

Para reproducir: PostgreSQL temporal aislado, `DJANGO_LOAD_DOTENV=false`,
`DEPLOYMENT_ENVIRONMENT=test`, `DATABASE_URL` temporal, sin credenciales reales.

```powershell
python manage.py test financiacion_educativa.tests.test_portal_operaciones_recaudos financiacion_educativa.tests.test_recaudos_ux financiacion_educativa.tests.test_ingreso_operaciones financiacion_educativa.tests.test_aplicacion_cuotas financiacion_educativa.tests.test_recaudos_breb --noinput
```

Navegador opcional: `EDU_PLAYWRIGHT_MODULE` apunta al modulo Playwright instalado;
`EDU_OPS_SCREENSHOTS` a la carpeta de salida elegida. Requiere Edge local.

```powershell
python manage.py test financiacion_educativa.tests.test_portal_operaciones_recaudos_browser --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

No hay nuevas variables productivas, permisos ni migraciones. Antes de liberar:
revisar PR, probar con cuentas sinteticas y permisos separados en staging,
mantener los proveedores/worker fuera de esta validacion y no aplicar pagos reales.
Reversion del incremento: volver al release anterior sin revertir 0031/0032;
no eliminar ni modificar aplicaciones/conciliaciones existentes.
