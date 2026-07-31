# Auditoria de la Fase 6.6

Estado revisado el 28 de julio de 2026. Esta matriz distingue implementacion,
prueba automatizada y validacion que aun requiere navegador o hardware real.

| Requisito | Componente | Prueba o evidencia | Estado | Resultado |
| --- | --- | --- | --- | --- |
| Fondo discreto y responsive | `static/css/financiacion_educativa.css`, plantillas de cuenta | Capturas `phase66-login-pattern-1440x900.png` y `phase66-login-pattern-390x844.png` | Completo | Patron monocromatico, separado y sin acentos amarillos/azules de fondo. |
| Logo oficial a color | `_base.html`, `login.html`, `static/images/logo.png` | Capturas de login | Completo | El patron no sustituye el logo. |
| Porcentajes sin ceros | `financiacion_format.porcentaje`, `finanzas.html` | `test_fase5_web` | Completo | Presenta 10 %, 19 %, 2 % y 0,3711 % sin cambiar decimales almacenados. |
| Retirar bloque institucional redundante | `documentacion.html` | `test_fase4_web` | Completo | Los datos siguen en modelo, resumen y API. |
| Resumen financiero compacto | `documentacion.html`, CSS educativo | Flujos integrales adulto/menor | Completo | Sustituye la grilla con espacios vacios. |
| Pendientes exactos | `requisitos_documentales.py` | `test_fase4_requisitos`, `test_expediente_iteracion` | Completo | Lista cada requisito activo; no oculta faltantes. |
| Notificaciones accesibles | `_base.html`, JS y vistas web | `test_fase5_web`, `test_expediente_iteracion` | Completo | Toasts con `aria-live`; errores de campo permanecen en el formulario. |
| Visor privado PDF/JPEG/PNG | `previsualizar_documento_view`, modal en `documentacion.html` | `test_fase4_documentos` | Completo | Propiedad, IDOR, MIME, inline, no-store, nosniff, SAMEORIGIN y CSP sin `sandbox`. |
| Descarga secundaria protegida | `descargar_documento_view` | `test_fase4_documentos` | Completo | Mantiene attachment y no expone ruta privada. |
| Proyeccion de abono | `proyectar_abono_view`, `finanzas.html` | `test_fase5_web`, `test_reglas_financieras` | Completo | Calcula y renderiza; no crea pagos ni modifica saldos. |
| Proyeccion de pago total | `proyectar_pago_total_view`, servicios financieros | Pruebas financieras y de interfaz | Completo | Resultado informativo separado de pagos reales. |
| Administracion existente | `financiacion_educativa/admin.py` | Checks y pruebas de servicios administrativos | Completo | Django Admin gestiona versiones, terminos y revision; no existe panel operativo propio. |
| Flujo adulto | API, invitacion, terminos, expediente y finanzas | `test_flujo_integral_iteracion` | Completo | Estudiante adulto es deudor y no requiere tutor. |
| Flujo menor | Participantes, tutor, expediente y finanzas | `test_flujo_integral_iteracion`, `test_captura_identidad_ingresos` | Completo | Tutor separado y deudor principal. |
| Camara frente/reverso | `captura_identidad.html`, JS, `capturar_identidad_view` | `test_captura_identidad_ingresos` | Completo en codigo; requiere validacion manual de hardware | Sin file input; getUserMedia, vista viva, repeticion, lados separados y cierre de tracks. |
| Permiso denegado/sin camara/contexto inseguro | `static/js/financiacion_educativa.js` | Inspeccion automatizada de ramas `NotAllowedError` y `NotFoundError` | Parcial | Mensajes implementados; falta repetir en dispositivos reales antes de aprobar. |
| Certificado de ingresos | choices, formulario, servicio y requisitos | `test_captura_identidad_ingresos`, `test_fase4_requisitos` | Completo | Obligatorio, privado, reemplazable y pendiente de revision. |
| Seguridad CSRF/Origin/IDOR | middleware y vistas protegidas | suites `test_login_csrf`, `test_fase4_web`, `test_captura_identidad_ingresos` | Completo | No usa `csrf_exempt`; POST con Origin null falla. |
| Politica ausente/activa | selector, comando inicial y comando diagnostico | `test_reglas_financieras`, `test_fase5_web` | Completo | Falla cerrado y una activacion es visible sin reiniciar. |
| Idempotencia institucional | API y servicios de idempotencia/invitacion | `test_api_institucional`, `test_fase6_orquestacion` | Completo | Replay conserva solicitud y no reenvia invitacion. |
| OpenAPI | serializers, schema `/api/v1/schema/` | pruebas de API/schema | Completo | Contrato institucional no cambia por rutas web documentales. |
| Postman | `docs/api-aliados/postman/aprobado-financiacion-educativa.postman_collection.json` | Parseo JSON y pruebas contra serializers | Completo | Conserva payload y endpoints vigentes. |
| Guia del aliado | `docs/api-aliados/GUIA_INTEGRACION_API.md` | Revision contra API y serializers | Completo | Distingue disponible y fases futuras. |
| Guia de reanudacion | `docs/interno/operaciones/REANUDAR_PROYECTO.md` | Comandos ejecutados localmente | Completo | Incluye diagnostico seguro de base y politica. |

## Limites pendientes

- Los criterios administrativos para aceptar o rechazar un certificado de
  ingresos pertenecen a la fase de revision manual.
- No hay antivirus integrado; el dominio conserva el puerto y los estados para
  registrar el resultado tecnico de un escaner externo.
- Capturar una imagen no verifica autenticidad, titularidad, edad ni identidad.
- Un cliente manipulado podria construir una peticion HTTP fuera de la interfaz;
  el origen fisico de los bytes no puede demostrarse solo con `getUserMedia`.
- Pagares, firma, pagos y panel operativo integral siguen fuera de esta fase.
- La concurrencia y restricciones deben repetirse en PostgreSQL durante staging.
