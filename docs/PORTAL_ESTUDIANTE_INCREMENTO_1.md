# Portal del estudiante: primer incremento

Base: `9747404e971bbddd24d35824c6a945dc05e3639d` (`origin/main` actualizado).
Rama: `feat/portal-estudiante-20261111`. Referencia visual: prototipo de los
tres portales de 2026-11-11 y su LEEME; no se incorporan sus fixtures, selector
de roles, JavaScript ni estados simulados al producto.

## Alcance

- La ruta existente `mis-solicitudes/continuar/` siempre muestra la cuenta,
  incluso con un solo tramite. Secciones En tramite y Financiaciones aprobadas
  mediante enlaces GET y `?seccion=financiaciones`, sin dependencia de JavaScript.
- Consulta exclusivamente `solicitud.usuario`. No asocia por correo/cedula, no
  consume/reemite invitaciones, no cambia estados ni inventa obligaciones.
- Ver financiacion solo se ofrece para una autorizacion contractual real:
  reutiliza `obligaciones_autorizadas()` y por tanto firma/artefacto vigente
  coherentes y fotografia bloqueada no legado. Excluye simulacion activa.
- APPROVED sin ese respaldo permanece explicado en En tramite. Acceso directo
  al GET financiero muestra aviso sin pagos ni obligacion autorizada. Las
  solicitudes cerradas siguen accesibles en un desplegable separado.
- La cuota pendiente se selecciona por numero y ausencia de AplicacionCuotaEducativa,
  nunca por fecha >= hoy. Se incluyen vencidas y se omiten aplicadas. El importe
  inicial sugerido del formulario BRE-B propio usa la misma consulta; no se
  modifica el selector operativo ni se calculan cuotas/importes nuevos.
- Calendario, aplicaciones e historial vienen del backend. Transferencia
  confirmada NO significa pago aplicado. Un ingreso confirmado sin aplicar
  muestra espera y evita ofrecer otra transferencia desde el resumen.
- Todas aplicadas: sin siguiente cuota ni CTA de pagar; aviso expreso de que
  esto no constituye paz y salvo, liquidacion o cierre. APPROVED no cambia a PAID.
- Historial con lista blanca de campos: fecha, monto, estado, cuota y enlace
  propio al reporte. Sin evidencia bancaria, referencia verificada, hash,
  ruta privada o identidad del operador. Las vistas existentes siguen validando
  propiedad y el acceso bancario mantiene sus permisos.

## Compatibilidad y limites

El GET del flujo financiero informativo anterior a la firma y sus servicios
de proyecciones se conservan; no se cambian calculos, configuraciones ni reglas.
El nuevo resumen autorizado no presenta proyecciones como una liquidacion de
pagos reales. Sus condiciones originales quedan en un desplegable nativo.
Canal BRE-B deshabilitado: sin acciones de pago; se mantiene la consulta de
la financiacion y no se ofrecen enlaces a reportes no disponibles.

Las secciones son consultas, no nuevos estados. El GET de cuenta no escribe
registros financieros. No hay modelos, permisos ni migraciones nuevas. No se
modifican Operaciones, Institucion, ZapSign ni gestion_creditos. El unico hunk
en web/recaudos.py corresponde al importe inicial del formulario del estudiante.

No se implementan parciales, excedentes, mora, reversos, cierre de deuda,
distribucion, notificaciones o PDF nuevo. No se consulta QA historico.

## Pruebas y capturas

Pruebas focalizadas: test_portal_estudiante, test_reanudacion_solicitudes,
test_fase5_web, test_ingreso_operaciones, test_aplicacion_cuotas y
test_recaudos_breb. Cubren el listado conjunto, enlace directo ajeno (tambien
con correo coincidente), APPROVED sin evidencia, firma no vigente/incoherente,
simulacion, cuota aplicada omitida, cuota vencida incluida, transferencia
confirmada sin aplicacion, fin de calendario y canal apagado.

El test PortalEstudianteBrowserTests usa StaticLiveServerTestCase, Edge/Playwright,
datos y soportes sinteticos, antivirus simulado y correo locmem. JavaScript
DESHABILITADO durante todo el recorrido. No usa el prototipo ni una maqueta:
consulta las vistas y plantillas Django del producto. Bloquea trafico externo.
La identidad visible en capturas y el dominio example.test son fixtures.

Capturas completas del producto:

| Vista | 375 px | 390 px | 1440 px |
|---|---|---|---|
| En tramite | [Movil](revision/portal_estudiante/tramites-375.png) | [Movil](revision/portal_estudiante/tramites-390.png) | [Escritorio](revision/portal_estudiante/tramites-1440.png) |
| Aprobadas | [Movil](revision/portal_estudiante/financiaciones-375.png) | [Movil](revision/portal_estudiante/financiaciones-390.png) | [Escritorio](revision/portal_estudiante/financiaciones-1440.png) |
| Financiacion e historial | [Movil](revision/portal_estudiante/financiacion-375.png) | [Movil](revision/portal_estudiante/financiacion-390.png) | [Escritorio](revision/portal_estudiante/financiacion-1440.png) |
| Calendario abierto | [Movil](revision/portal_estudiante/calendario-375.png) | [Movil](revision/portal_estudiante/calendario-390.png) | [Escritorio](revision/portal_estudiante/calendario-1440.png) |

Para repetir el navegador en PostgreSQL de pruebas aislado, configurar
EDU_PLAYWRIGHT_MODULE con la ruta local del modulo Playwright y
EDU_PORTAL_SCREENSHOTS con la carpeta de salida. No necesita cuentas externas.

```text
python manage.py test financiacion_educativa.tests.test_portal_estudiante financiacion_educativa.tests.test_reanudacion_solicitudes financiacion_educativa.tests.test_fase5_web financiacion_educativa.tests.test_ingreso_operaciones financiacion_educativa.tests.test_aplicacion_cuotas financiacion_educativa.tests.test_recaudos_breb financiacion_educativa.tests.test_portal_estudiante_browser --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
node --check financiacion_educativa/tests/js/test_portal_estudiante_browser.js
```

### Resultado local

- PostgreSQL 16.13 temporal aislado, sin bases, credenciales o datos de servidores.
- Seleccion completa de los siete modulos anteriores: 111 pruebas OK en
  252.979 s, cero omisiones; incluye navegador sin JavaScript y las regresiones
  concurrentes existentes de recaudos/aplicaciones.
- Revalidacion de los doce casos nuevos y el navegador: 13 pruebas OK en
  42.081 s. Despues del ajuste exclusivamente visual de la fecha (dia/mes y
  ano separados), navegador repetido: 1 prueba OK en 9.755 s.
  Son selecciones solapadas, NO 125 pruebas diferentes.
- 12 capturas finales del producto a 375, 390 y 1440 px. La cuota anterior a
  hoy sigue visible sin aplicar; el calendario abre con HTML nativo.
- Navegador comprueba evidencia bancaria 403 y financiacion ajena 404.
- check: 0 issues; makemigrations --check --dry-run: No changes detected;
  git diff --check y node --check correctos. Sin migraciones nuevas.
- No se repitio toda la suite educativa. No se probaron servicios externos ni
  se accedio a staging/produccion. La validacion aqui reportada es LOCAL.

## Liberacion

PR solo para revision; no merge ni despliegue en esta ejecucion. No requiere
migraciones o variables de entorno nuevas. No activar canales, cambiar permisos
ni manipular solicitudes o pagos para probarlo. Despues de aprobar el PR y
autorizar un despliegue independiente, comprobar en staging con una cuenta de
pruebas las dos secciones, la firma efectiva y el historial; no usar pagos reales.
