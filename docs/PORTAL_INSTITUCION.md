# Portal de Institucion: seguimiento y detalle

Base exacta: `99b8d36a266b94ce8128a36d50f9fa99dd99216c` (main).
Rama: `feat/portal-institucion-20261112`.

## Alcance

Consulta institucional de solo lectura: bandeja de Seguimiento, listado de
Solicitudes y detalle. Conserva rutas, contexto por membresia activa, roles y
mascaras existentes. No incluye anulacion, Operaciones, portal del estudiante,
ZapSign, reglas financieras, modelos, migraciones ni cambios en la API.

### Dos resultados independientes

| Indicador | Fuente | Interpretacion |
| --- | --- | --- |
| Solicitud aprobada | `SolicitudFinanciacionEducativa.estado == APPROVED` | Aprobacion de la solicitud; no implica autorizacion academica. |
| Curso autorizado | `firmas_autorizadas_reales()`, correlacionada por solicitud | Autorizacion contractual efectiva existente; no una nueva decision del portal. |
| Desde | `firmado_en` de la firma coherente vigente | La misma fecha efectiva de la regla publica, no la ultima actualizacion ni una fecha inferida. |

Se reutiliza el predicado contractual existente: solicitud APPROVED, proceso y
pagare SIGNED coherentes, pagare vigente, archivo/hash/tamano/fecha de firma
registrados y fotografia activa, bloqueada, no legada, de la misma solicitud.
No se consultan proveedores ni se descarga un contrato para calcular el indicador.
Una simulacion Sandbox no sustituye evidencia contractual real.

La fecha se obtiene con una subconsulta SQL en el selector ya limitado al tenant.
No hay consultas por fila ni carga de tokens/URLs/documentos firmados para el
listado. El presenter recibe exclusivamente la fecha y deriva el indicador.

Seguimiento ahora conserva visibles las solicitudes aprobadas, tanto sin
autorizacion como con autorizacion efectiva. Rechazadas, canceladas y finalizadas
siguen en Todas las solicitudes. El filtro APPROVED incluye ambas variantes;
no se cambian los valores de estados ni sus transiciones.

La etiqueta institucional APPROVED dice Aprobada, no Aprobada; curso autorizado.
Se corrige tambien el texto del indicador existente en Inicio para evitar esa
misma equivalencia; no se redisenan ni se cambian sus calculos. El enum publico
y las respuestas de la API permanecen intactos. El historial describe la
transicion a APPROVED como Solicitud aprobada, sin inferir una autorizacion.

## Pantallas y datos permitidos

- Bandeja: referencia, solicitante, curso, periodo/sede, valor del plan, plazo,
  estado, aprobacion, autorizacion efectiva y actualizacion. Boton textual Ver
  detalle; filtros nativos compartidos y paginacion con parametros conservados.
- Detalle: resultados separados primero; aviso explicito si esta aprobada pero
  no autorizada. Contacto con mascaras por rol y documento siempre parcial.
  Informacion academica, resumen documental/contractual y condiciones existentes.
  Historial secundario en un desplegable HTML nativo.
- Importes: plan contractual, no saldos actuales ni afirmaciones de pagos.
- Estados vacios y errores: sin resultados o filtro invalido (HTTP 400 sin filas).
  No se incluyen datos de otros tenants en opciones, contadores, filas o detalle.

No se cargan ni publican evidencias bancarias, referencias del banco, operadores,
observaciones privadas, motivos internos del historial, resultados crudos de IA,
rutas privadas, tokens o enlaces de firma. La institucion no recibe permisos de
registro, conciliacion, aplicacion o decisiones operativas. Los enlaces directos
a evidencia de Operaciones siguen devolviendo 403; el detalle ajeno devuelve 404.
Una membresia revocada pierde acceso en el siguiente request. Las paginas usan
no-store y no ofrecen acciones de escritura. No se concede acceso por correo,
cedula, is_staff, UUID enviado por GET ni una sesion de membresia manipulada.

## Pruebas y reproduccion local

Validacion realizada en PostgreSQL **16.13** temporal y aislado:

- **52/52 OK**, sin omisiones, en 100.075 s: diez pruebas nuevas del portal,
  regresion de acceso/consulta institucional y navegador real sin JavaScript.
- Tras el ultimo pulido visual del detalle: navegador **1/1 OK** (8.161 s) y
  regresiones de enmascaramiento, finanzas y privacidad **3/3 OK** (5.204 s).
- Aprobada sin autorizacion: Si / No, con explicacion visible. Autorizacion
  efectiva: Si / Si, con fecha contractual. Firma no vigente y simulacion sin
  firma no autorizan. Acceso cruzado: 404; evidencia bancaria: 403.
- Filtros, paginacion, revocacion inmediata, roles, datos privados, consultas
  sin N+1 y ausencia de mutaciones verificados. No se modificaron locks ni
  transacciones, por lo que no se amplio la validacion a concurrencias ajenas.
- `check`: 0 issues. `makemigrations --check --dry-run`: No changes detected.
  `git diff --check` y `node --check`: sin errores.

La primera ejecucion detecto una asercion antigua de `<th>` sin el nuevo
`scope="col"`; se adapto para conservar la cobertura de la etiqueta Curso y
se repitio toda la seleccion correctamente. Advertencia no bloqueante de
WhiteNoise: worktree sin collectstatic; el servidor de pruebas sirve los assets
locales. No se ejecuto toda la suite educativa. Contenedor y base temporal
eliminados al finalizar. Sin proveedores, correos, migraciones en servidores,
pagos reales ni cambios de configuracion externa.

Usar exclusivamente PostgreSQL temporal aislado y datos sinteticos. Establecer
`DJANGO_LOAD_DOTENV=false`, `DEPLOYMENT_ENVIRONMENT=test` y `DATABASE_URL` de la
base de pruebas. Los tests deshabilitan automatizacion, IA, PDF y correo real;
antivirus simulado, sin proveedores. No usar staging para estas pruebas.

```powershell
python manage.py test financiacion_educativa.tests.test_portal_institucion financiacion_educativa.tests.test_dashboard_institucional_acceso financiacion_educativa.tests.test_dashboard_institucional_consulta --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
node --check financiacion_educativa/tests/js/test_portal_institucion_browser.js
```

Para navegador: `EDU_PLAYWRIGHT_MODULE` apunta al Playwright instalado y
`EDU_INST_SCREENSHOTS` a la carpeta de capturas. Requiere Edge local.

```powershell
python manage.py test financiacion_educativa.tests.test_portal_institucion_browser --noinput
```

El navegador usa JavaScript deshabilitado y bloquea trafico fuera del servidor
local. Las capturas son del producto Django real, no del prototipo ni de datos
productivos. No se agrega un selector de roles ni fixtures sinteticos al producto.

## Capturas de revision

| Pantalla | 375 px | 390 px | Escritorio 1440 px |
| --- | --- | --- | --- |
| Seguimiento | [Ver](capturas/portal-institucion-20261112/01-seguimiento-375.png) | [Ver](capturas/portal-institucion-20261112/01-seguimiento-390.png) | [Ver](capturas/portal-institucion-20261112/01-seguimiento-1440.png) |
| Aprobada sin autorizacion | [Ver](capturas/portal-institucion-20261112/02-aprobada-sin-autorizacion-375.png) | [Ver](capturas/portal-institucion-20261112/02-aprobada-sin-autorizacion-390.png) | [Ver](capturas/portal-institucion-20261112/02-aprobada-sin-autorizacion-1440.png) |
| Autorizacion efectiva | [Ver](capturas/portal-institucion-20261112/03-autorizacion-efectiva-375.png) | [Ver](capturas/portal-institucion-20261112/03-autorizacion-efectiva-390.png) | [Ver](capturas/portal-institucion-20261112/03-autorizacion-efectiva-1440.png) |
| Sin resultados | [Ver](capturas/portal-institucion-20261112/04-sin-resultados-375.png) | [Ver](capturas/portal-institucion-20261112/04-sin-resultados-390.png) | [Ver](capturas/portal-institucion-20261112/04-sin-resultados-1440.png) |
| Filtro invalido | [Ver](capturas/portal-institucion-20261112/05-filtro-invalido-375.png) | [Ver](capturas/portal-institucion-20261112/05-filtro-invalido-390.png) | [Ver](capturas/portal-institucion-20261112/05-filtro-invalido-1440.png) |

## Revision antes de liberar

1. Con una membresia de consulta activa, abrir Seguimiento y los dos detalles
   sinteticos: aprobada/no autorizada y aprobada/autorizada con fecha efectiva.
2. Consultar, limpiar y abrir filtros avanzados/historial sin JavaScript;
   verificar parametros al paginar y el enmascaramiento del contacto.
3. Con otra cuenta institucional, intentar el enlace directo ajeno: 404. La
   evidencia bancaria de Operaciones debe seguir inaccesible: 403.

No hay variables de entorno, permisos ni migraciones nuevas. No se ha desplegado
ni certificado en staging. Liberacion sujeta a revision del PR; no fusionar ni
desplegar automaticamente. Una reversion del frontend no requiere revertir
0031/0032 ni modificar solicitudes, conciliaciones, aplicaciones o contratos.
