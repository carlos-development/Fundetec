# Descarga de plan contractual en PDF

Base: `origin/main` en `dd09e8c`, merge del PR #8. Carlos confirmo que esa
base esta desplegada y que las notificaciones BRE-B siguen desactivadas.
Este incremento no cambia ese flag ni activa correo/proveedores.

## Recorrido y acceso

Mis solicitudes -> Financiaciones aprobadas -> Ver financiacion ->
**Descargar plan contractual en PDF**. Enlace HTML nativo, utilizable sin JS.

Ruta bajo `financiacion_educativa_web`:

```text
GET /financiacion-educativa/solicitudes/<solicitud_id>/finanzas/plan/<fotografia_id>/pdf/
name: plan-contractual-pdf
```

Se reutiliza `_solicitud_del_usuario`: cuenta autenticada ya vinculada a la
solicitud y coincidencia del correo segun las reglas del portal. Otra cuenta
recibe 404, incluso si es superusuario o comparte correo. No hay accesos
nuevos por cedula, parentesco, rol de Operaciones o membresia institucional.
Un tutor conserva exactamente el acceso que ya tenga por la cuenta vinculada;
el criterio de destinatario de correos no agrega permisos de descarga.

`obligaciones_autorizadas()` comprueba firma vigente y coherente del pagare,
fotografia activa, bloqueada y no legada, y autorizacion efectiva del estado
APPROVED; excluye simulacion Sandbox. APPROVED aislado no habilita el PDF.
La fotografia indicada debe pertenecer a esa misma solicitud y autorizacion.
Un enlace antiguo o de otra version no se sustituye por la version actual:
devuelve 404; el titular debe volver a su financiacion para obtener el enlace
vigente. El nombre de archivo es `plan-contractual-vN.pdf`, sin datos personales.

## Fuente de verdad y significado

El documento se titula **Plan contractual de pagos**. Explica expresamente
que no es saldo vigente ni liquidacion y que no incorpora pagos, mora,
abonos o ajustes posteriores. No reemplaza ni modifica el pagare firmado.

Todas las cifras, cargos, proveedores, moneda, regla, tasa, metodo y fechas
proceden de `CondicionesFinancieras` guardada. Todas las cuotas proceden de
sus `CuotaAmortizacionEducativa`, ordenadas por numero. No se consultan la
configuracion financiera activa, el simulador o el motor para recalcular el
plan. La referencia externa sirve solo para identificar la solicitud.

Se conserva la ultima cuota real guardada, aunque sea distinta de la cuota
de referencia por redondeos contractuales. Se muestran todas las cuotas,
incluidas vencidas y aplicadas, sin estados de pago ni saldos actuales. Los
totales son sumas de las filas persistidas y se contrastan con la fotografia;
no se introduce una regla de calculo financiero nueva.

Si faltan cuotas, la numeracion/fechas no concuerdan o los importes/totales
no coinciden, la respuesta es 409 con una pantalla privada de no disponible.
No se genera una proyeccion alternativa ni se corrige la base automaticamente.

## Implementacion y privacidad

- Servicio de lectura `services/plan_contractual_pdf.py` y plantilla propia.
- Reutiliza `_renderizar_pdf` (WeasyPrint ya instalado en el proyecto),
  formato COP existente y logo local. No agrega dependencias ni recursos remotos.
- El PDF se genera en memoria, fuera de una transaccion de request, y se
  devuelve como attachment con no-store, nosniff, no-referrer y CSP sandbox.
- No se guardan artefactos contractuales nuevos ni se regeneran los firmados.
- No hay correo, outbox, eventos de pago ni transiciones causados por descargar.
- No hay evidencia, referencia bancaria, operador, cedula, correo o enlaces
  privados dentro del documento. Se conserva la auditoria de acceso no
  autorizado que ya aplica el helper del portal.

## Validacion en staging despues de aprobar el PR

No se ejecuto este procedimiento en servidores:

1. Actualizar al SHA aprobado por el procedimiento habitual. No hay migraciones,
   variables o instalaciones nuevas para este incremento. Ejecutar check y
   recopilar estaticos/reiniciar web por ese procedimiento. No cambiar el flag
   BRE-B ni arrancar workers para esta descarga.
2. Con un titular sintetico autorizado, abrir su financiacion desde movil y
   escritorio y descargar. Contrastar cada cargo, cuota y vencimiento con la
   fotografia y calendario guardados; revisar ambas paginas y la ultima cuota.
3. Probar otra cuenta por enlace directo (404), una aprobacion sin firma
   coherente (sin enlace / 404) y version ajena o retirada (404, sin reemplazo).
   Probar sin JS. Si hay pagos aplicados, confirmar que el PDF sigue mostrando
   el mismo calendario completo, no un saldo ni paz y salvo.

La verificacion de Safari/iPhone o Chrome/Android reales queda para staging;
la comprobacion automatizada local usa Chromium/Edge a 375/390/1440 px.
La reversion es volver al SHA previo por el procedimiento de releases; no
hay esquema o datos que borrar ni registros financieros que revertir.

## Evidencia sintetica local

PDF de ejemplo: `../output/pdf/plan-contractual-sintetico.pdf`. No contiene
documentos ni datos de estudiantes reales. Capturas completas del portal y
de todas las paginas del PDF en `capturas/plan-contractual-pdf-20261112/`.
El PDF se inspecciona con pypdf y se renderiza con PDFium ya disponible.

Validacion local del 2026-11-12: **79/79 pruebas OK en 140.482 segundos,
sin omisiones**, sobre PostgreSQL 16.13 temporal y aislado con almacenamiento
tmpfs. Incluye 18 pruebas nuevas, descarga real en Chromium/Edge sin JavaScript
a 375/390/1440 px y regresiones del portal, autorizacion, artefactos y reglas
financieras existentes. No se ejecuto toda la suite educativa por rutina.

```text
python manage.py test financiacion_educativa.tests.test_plan_contractual_pdf financiacion_educativa.tests.test_plan_contractual_pdf_browser financiacion_educativa.tests.test_portal_estudiante financiacion_educativa.tests.test_dashboard_obligaciones financiacion_educativa.tests.test_artefactos_contractuales financiacion_educativa.tests.test_reglas_financieras --noinput --keepdb
```

Se uso DJANGO_LOAD_DOTENV=false, DEPLOYMENT_ENVIRONMENT=test, DEBUG=true y
DATABASE_URL del contenedor local temporal; nunca bases ni credenciales de
servidores. Los proveedores y correos permanecieron deshabilitados por los
overrides de pruebas. Para las capturas se configuraron EDU_PLAYWRIGHT_MODULE,
EDU_PLAN_PDF_SAMPLE y EDU_PLAN_SCREENSHOTS con rutas locales de prueba.

`manage.py check`: 0 issues. `makemigrations --check --dry-run`: No changes
detected. `git diff --check` y `node --check` del test de navegador: codigo 0.
Las dos paginas del PDF se renderizaron e inspeccionaron visualmente, asi como
el portal movil. Los warnings locales de WhiteNoise y GLib/UWP no afectaron
el render o las pruebas. El contenedor y sus bases se eliminaron al finalizar.

No se accedio a servidores, no se enviaron correos ni se hicieron llamadas
a proveedores. No se modificaron modelos, migraciones, configuracion,
servicios financieros ni gestion_creditos. Estas pruebas locales no sustituyen
la comprobacion posterior en dispositivos reales de staging.
