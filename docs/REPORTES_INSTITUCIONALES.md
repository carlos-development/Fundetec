# Reportes institucionales

Base: `origin/main` en `d35bb58f8ce4f7e7e281b0914edd93498b2ee66f`, merge del PR
#5 confirmado como ancestro. Rama: `feat/reportes-institucion-20261112`.
Se reviso el roadmap antes de implementar. Anulacion y trabajos previos se
conservaron en sus worktrees; no forman parte de esta entrega.

## Definiciones y fuente unica

Ruta GET: `/financiacion-educativa/institucion/reportes/`. Misma membresia activa
validada en servidor, mismos roles de consulta y selectores del portal actual.

Todos los indicadores comparten **fecha de radicacion** (`creada_en`) y **curso**
(`nombre_curso`). Los dos dias del rango estan incluidos y usan la zona horaria
activa de Django (America/Bogota en las pruebas). El rango inicial comprende el
primer dia de hace once meses hasta hoy: doce meses calendario contando el actual.
El rango aplicado siempre se muestra y se incorpora explicitamente en los enlaces.
Las fechas invalidas, vacias o invertidas y los cursos ajenos devuelven 400 sin
cifras. Las opciones de curso provienen exclusivamente de la institucion activa.

| Indicador | Criterio sobre la misma cohorte |
| --- | --- |
| Solicitudes recibidas | Todas las solicitudes radicadas del rango, cualquiera que sea su estado actual. Incluye tramites, rechazadas y canceladas; cada UUID cuenta una vez. |
| Solicitudes aprobadas | Estado actual APPROVED. |
| Cursos autorizados | Fecha efectiva no nula obtenida mediante `firmas_autorizadas_reales()`, reutilizada por el selector institucional. |
| Aprobadas con autorizacion pendiente | APPROVED y sin autorizacion efectiva. No significa todos los tramites pendientes. |

**Aprobadas = autorizadas + aprobadas pendientes de autorizacion.** Una simulacion
Sandbox no fabrica una autorizacion contractual. Se conservan todos los criterios
vigentes de coherencia de firma/pagare/fotografia; no se modifican esos servicios.
Ningun indicador usa la fecha de firma, de decision o de actualizacion para
recortar la cohorte. En Inicio, la etiqueta del contador anterior Recibidas se
aclara a Pendientes de iniciar, sin cambiar su consulta, para no confundirlo con
el total de radicaciones del nuevo reporte.

`dashboards/institucional/reportes.py` concentra los cuatro predicados. El
selector existente aplica esos mismos predicados al listado mediante el parametro
validado `indicador`. El reporte obtiene conteos por mes en **una sola consulta
SQL** y suma esas filas para los totales: no consulta estudiantes, cuotas o
recaudos ni ejecuta un segundo conteo que pueda desalinearse de la grafica.

Cada indicador abre **Solicitudes**, no Seguimiento, porque este ultimo excluye
algunos estados cerrados. Conserva fechas, curso e indicador al paginar. El
resultado se identifica y ofrece Volver a reportes. Los enlaces mensuales
intersectan el mes con el rango original: un filtro desde el dia 15 nunca amplia
el listado al dia 1. El enlace de regreso mantiene esa cohorte mensual.

La grafica muestra solo meses con radicaciones. Las barras tienen la misma escala
visual (el mayor total mensual recibido); los numeros y enlaces son la referencia
exacta. Los subconjuntos se solapan: no se suman las cuatro barras como si fueran
categorias excluyentes. La tabla desplegable expone las mismas cifras sin depender
del color, de canvas ni de JavaScript. Un rango sin datos muestra ceros y estado
vacio; sus cuatro enlaces respaldan cero filas.

## Seguridad y limites

- Institucion tomada del contexto de membresia validado, nunca de un UUID en GET.
  Selectores, filtros, opciones, agregacion y detalle siguen limitados al tenant.
- No se introducen permisos, exportaciones, cuentas compartidas ni acceso por
  correo/cedula. Staff o superusuario sin membresia no obtiene acceso implicito.
- Reportes solo publica conteos; no nombres/contactos/documentos de estudiantes,
  evidencia bancaria, referencias privadas, actores, tokens o diagnosticos.
- Los enlaces abren el listado y detalle existentes con mascaras por rol. Acceso
  directo ajeno: 404; evidencia bancaria de Operaciones: 403. Respuestas no-store.
- GET no cambia solicitudes, contratos, calendarios o decisiones. POST no decide
  ni escribe (405 para una cuenta autorizada de consulta).
- Los conteos son una lectura del **estado actual** de la cohorte. No son historia
  de aprobaciones, una fotografia persistente ni un cierre contable. Entre el
  reporte y un clic posterior pueden ocurrir cambios legitimos; actualizar ambos
  antes de comparar. No se bloquean filas ni se inventa un corte historico.
- Fuera de alcance: reparto 80/20, saldos, mora, pagos, reglas financieras/firma,
  notificaciones, workers, API y simulador. Sin modelos ni migraciones nuevas.

## Validacion local

Resultado final: **66/66 pruebas OK**, sin omisiones, en **98.085 s**, sobre
PostgreSQL **16.13** temporal aislado. Incluye las 14 pruebas nuevas, la regresion
de acceso/consulta/portal institucional y el navegador real sin JavaScript.
Se generaron y revisaron 18 capturas a 375/390/1440 px. `check`: 0 issues;
`makemigrations --check --dry-run`: No changes detected; `git diff --check` y
`node --check`: sin errores. El contenedor y sus bases se eliminaron al finalizar.

La primera regresion encontro una expectativa antigua de cuatro textos
Proximamente, que incluia Reportes aun deshabilitado; se actualizo a las dos
apariciones restantes de Integracion y se agrego una asercion del enlace activo.
La seleccion completa se repitio correctamente. Advertencia local no bloqueante
de WhiteNoise por worktree sin collectstatic; los assets son servidos por el
servidor de pruebas. No se ejecuto toda la suite educativa ni se valido staging.
No se cambiaron transacciones o locks y no se ampliaron pruebas concurrentes de
otros bloques. No hubo llamadas a proveedores ni envios reales.

Datos sinteticos de dos instituciones con cursos compartidos y exclusivos,
radicaciones a ambos lados del corte local, firmas posteriores al rango,
aprobadas sin firma, canceladas/rechazadas y varias paginas de respaldo.

La cohorte sintetica de septiembre de 2026 contiene **6 recibidas / 3 aprobadas /
2 autorizadas / 1 aprobada pendiente**. Al filtrar Curso Analisis & Datos:
**5 / 2 / 1 / 1**. La otra institucion devuelve **2 / 1 / 1 / 0** sin heredar
registros u opciones de la primera. Cada enlace, incluidos los ceros y meses
parciales, se contrasta contra `pagina.paginator.count`; tambien se comprueban
los UUID de las filas y la conservacion de parametros al paginar.

Reproduccion: PostgreSQL **temporal aislado**, `DJANGO_LOAD_DOTENV=false`,
`DEPLOYMENT_ENVIRONMENT=test`, `DATABASE_URL` exclusivamente de pruebas.
Los tests deshabilitan automatizacion, IA, PDF y correo real; no usan proveedores.

```powershell
python manage.py test financiacion_educativa.tests.test_reportes_institucionales financiacion_educativa.tests.test_dashboard_institucional_acceso financiacion_educativa.tests.test_dashboard_institucional_consulta financiacion_educativa.tests.test_portal_institucion --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
node --check financiacion_educativa/tests/js/test_reportes_institucionales_browser.js
```

Navegador: configurar `EDU_PLAYWRIGHT_MODULE` con el modulo Playwright instalado,
`EDU_REPORT_SCREENSHOTS` con la carpeta de salida y disponer de Edge local.

```powershell
python manage.py test financiacion_educativa.tests.test_reportes_institucionales_browser --noinput
```

El navegador deshabilita JavaScript y bloquea trafico fuera del servidor local.
Comprueba los cuatro listados de respaldo, un mes individual, filtro por curso,
tabla accesible, vacio, error, segunda cuenta institucional y denegaciones.
Las capturas son del producto Django real con datos sinteticos, no de produccion.

## Capturas

| Pantalla | 375 px | 390 px | Escritorio 1440 px |
| --- | --- | --- | --- |
| Reporte y grafica mensual | [Ver](capturas/reportes-institucion-20261112/01-reporte-375.png) | [Ver](capturas/reportes-institucion-20261112/01-reporte-390.png) | [Ver](capturas/reportes-institucion-20261112/01-reporte-1440.png) |
| Cifras mensuales desplegadas | [Ver](capturas/reportes-institucion-20261112/02-cifras-mensuales-375.png) | [Ver](capturas/reportes-institucion-20261112/02-cifras-mensuales-390.png) | [Ver](capturas/reportes-institucion-20261112/02-cifras-mensuales-1440.png) |
| Filtro de curso y rango | [Ver](capturas/reportes-institucion-20261112/03-curso-filtrado-375.png) | [Ver](capturas/reportes-institucion-20261112/03-curso-filtrado-390.png) | [Ver](capturas/reportes-institucion-20261112/03-curso-filtrado-1440.png) |
| Listado de respaldo | [Ver](capturas/reportes-institucion-20261112/04-listado-respaldo-375.png) | [Ver](capturas/reportes-institucion-20261112/04-listado-respaldo-390.png) | [Ver](capturas/reportes-institucion-20261112/04-listado-respaldo-1440.png) |
| Sin radicaciones | [Ver](capturas/reportes-institucion-20261112/05-vacio-375.png) | [Ver](capturas/reportes-institucion-20261112/05-vacio-390.png) | [Ver](capturas/reportes-institucion-20261112/05-vacio-1440.png) |
| Filtro invalido | [Ver](capturas/reportes-institucion-20261112/06-filtro-invalido-375.png) | [Ver](capturas/reportes-institucion-20261112/06-filtro-invalido-390.png) | [Ver](capturas/reportes-institucion-20261112/06-filtro-invalido-1440.png) |

## Pasos de validacion en staging (no ejecutados)

Solo despues de revisar el PR y autorizar el despliegue por el procedimiento
existente. Esta entrega no fusiona, despliega ni modifica el servidor.
No requiere variables, permisos o migraciones nuevas; los estaticos se publican
por el procedimiento habitual, no arrancando workers o enviando contratos.

1. Verificar el SHA aprobado y ejecutar los checks de solo lectura del release:

   ```bash
   cd /var/www/fundetec-staging/current
   sudo -u fundetec-staging git rev-parse HEAD
   staging_manage check
   staging_manage makemigrations --check --dry-run
   ```

2. Usar dos instituciones **QA sinteticas** con membresias individuales activas,
   una cuenta por institucion. Necesitan solicitudes de cursos compartidos y uno
   exclusivo; una aprobada sin autorizacion y otra con firma contractual coherente
   de una prueba sandbox previamente autorizada. No usar aprobaciones publicas
   Sandbox para fingir autorizacion efectiva; no modificar estados/fechas ni
   generar o reenviar contratos reales para probar el dashboard.
3. Con la primera cuenta, abrir Reportes desde el menu. Seleccionar un rango que
   incluya las fechas originales de radicacion QA y anotar curso, rango y hora.
   Abrir cada indicador y comparar su numero con el total del listado; recorrer
   todas las paginas. Para Recibidas deben incluirse tambien las cerradas de esa
   cohorte. Si cambian datos mientras se revisa, refrescar ambas consultas.
4. Repetir con un curso y un rango parcial de mes. Abrir las barras mensuales y
   comprobar que no amplian los dias seleccionados. Confirmar aprobadas =
   autorizadas + aprobadas pendientes; una firma posterior al rango no excluye
   una solicitud radicada dentro de el.
5. Con la segunda cuenta, comprobar cifras/opciones propias y abrir el enlace a
   un detalle de la primera: 404. Intentar evidencia bancaria con una cuenta
   institucional sin permisos operativos: 403. No adjuntar archivos ni mostrar
   secretos para estas verificaciones.
6. Probar un rango sin solicitudes (cuatro ceros), fecha invalida/invertida y
   navegacion de filtros, enlaces y tabla mensual sin JavaScript. Revisar a
   375/390 px y escritorio sin desbordamientos. No activar proveedores para ello.

Las pruebas automatizadas solo se ejecutan en una base PostgreSQL de pruebas
separada, nunca contra la base compartida que atiende staging. Registrar las
evidencias y omisiones de staging cuando realmente se ejecuten; lo validado aqui
es local. Ante regresion de UI, volver al release anterior por el procedimiento
aprobado, sin revertir 0031/0032 ni editar solicitudes o datos financieros.
