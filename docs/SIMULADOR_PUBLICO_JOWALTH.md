# Simulador público y correos educativos de Jowalth

## Direcciones y selección de marca

La URL para compartir después del despliegue es `https://aprobado.com.co/financiacion-educativa/simulador/jowalth/`. La entrada genérica es `/financiacion-educativa/simulador/`. Django elige la presentación pública al resolver la ruta. Ambas rutas usan el formulario público, el cálculo y el límite de consultas existentes. El POST HTML vuelve a su propia ruta.

El simulador del expediente y los correos dirigidos al solicitante usan el perfil `JOWALTH_ACADEMY` de la institución asociada. La identidad se conserva aunque cambie el nombre comercial. Las solicitudes sin ese perfil, los avisos internos, las copias institucionales y las de auditoría usan la presentación genérica. Se mantienen los destinatarios que cada evento ya autoriza.

El encabezado del correo muestra Aprobado. Los dos PNG de los aliados se muestran después del contenido y antes del pie gris, sobre fondo blanco. La URL absoluta de cada aliado usa el origen de `BRAND_PUBLIC_BASE_URL` y el prefijo de archivos estáticos. Los nombres estables permiten abrir correos anteriores después de renovar el manifiesto de estáticos. El bloque requiere HTTPS. La configuración del logo de Aprobado continúa en `EDUCATION_EMAIL_LOGO_URL`.

## Controles y estimaciones

Las dos entradas públicas convierten los controles de Django en barras de monto y plazo. Heredan sus límites y pasos, incluido el centavo del monto. El formulario del expediente define un mínimo de monto, dos decimales y una longitud máxima de almacenamiento, pero no un tope comercial para una barra. Allí se conservan los dos campos numéricos con recálculo al editar. Sin JavaScript, todas las entradas conservan los campos y el envío manual.

Al arrastrar se muestra inmediatamente el valor seleccionado. La primera muestra se solicita tras 300 ms y las siguientes se espacian al menos 2,2 s hasta soltar. Una respuesta intermedia muestra exactamente el monto y plazo consultados. Al soltar o terminar el ajuste de teclado se prioriza la selección final. Si falla la consulta, la última cuota y su escenario permanecen disponibles junto a «Reintentar».

## Despliegue tras aprobación

1. Revisar el diff completo de `diseno/simulador-jowalth` e incluir los dos archivos de `static/images/simulador/` en el artefacto. Comprobar que el perfil ya asignado a Jowalth es correcto. Cambiar el perfil contractual de una institución no forma parte de esta entrega.
2. En el entorno de destino, confirmar el dominio oficial en `BRAND_PUBLIC_BASE_URL`, el prefijo público de estáticos y la URL HTTPS del logo de Aprobado. Ejecutar `python manage.py check --deploy` con la configuración del entorno. Este cambio no introduce migraciones de base de datos.
3. Ejecutar `python manage.py collectstatic --noinput` durante la construcción. Publicar los estáticos conservando los nombres originales además de los que genere el manifiesto. Actualizar el proceso web y los procesos que renderizan o envían el correo educativo con la misma versión.
4. Verificar con GET anónimo que `/static/images/simulador/Fundetec_logo.png` y `/static/images/simulador/Loopiter_logo.png` devuelven 200 e `image/png` por HTTPS. Si se usa CDN, comprobar las URLs exactas del MIME de prueba.

## Verificación funcional

1. Abrir la URL Jowalth en una ventana privada. Confirmar ambas marcas en la primera carga. Abrir también la ruta genérica, un expediente de prueba de Jowalth y uno de otra institución. Comparar la cuota y el total entre ambas entradas públicas con el mismo monto y plazo.
2. Cambiar el plazo con el deslizador y abrir «Ver detalle de los costos». Desactivar JavaScript y enviar el formulario Jowalth. El resultado debe seguir en la URL Jowalth y conservar los valores enviados.
3. En una instancia de pruebas con correo en memoria o buzón de captura, emitir una invitación y un aviso de expediente para cada perfil. Inspeccionar HTML, texto plano, remitente, destinatarios y enlaces. Repetir con decisiones, plan y pagos ya habilitados, y con copias internas. Con imágenes bloqueadas deben leerse los nombres de marca y el mensaje completo.
4. Para revertir después de un despliegue, restaurar la versión anterior del proceso web y de los emisores. Mantener disponibles las dos URLs de imagen para los mensajes que se hayan entregado.

Las suites enfocadas están en `financiacion_educativa.tests.test_simulador_educativo`, `test_simulador_browser` y `test_marca_institucional`. El recorrido de navegador usa `EDU_PLAYWRIGHT_MODULE`. Las variables opcionales `EDU_SIMULATOR_SCREENSHOTS` y `EDU_JOWALTH_EMAIL_PREVIEWS` indican carpetas locales para la evidencia.

