from io import BytesIO
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    TextStringObject,
)

from financiacion_educativa.services.procesamiento_pdf import (
    ErrorProcesamientoPDF,
    procesar_pdf_seguro,
)


def pdf_sintetico(*, textos=('',), cifrado=False):
    writer = PdfWriter()
    for texto in textos:
        pagina = writer.add_blank_page(width=612, height=792)
        if texto:
            fuente = DictionaryObject({
                NameObject('/Type'): NameObject('/Font'),
                NameObject('/Subtype'): NameObject('/Type1'),
                NameObject('/BaseFont'): NameObject('/Helvetica'),
            })
            pagina[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({
                    NameObject('/F1'): writer._add_object(fuente),
                }),
            })
            flujo = DecodedStreamObject()
            seguro = texto.replace('(', '[').replace(')', ']')
            flujo.set_data(
                f'BT /F1 12 Tf 72 720 Td ({seguro}) Tj ET'.encode('latin-1')
            )
            pagina[NameObject('/Contents')] = writer._add_object(flujo)
    if cifrado:
        writer.encrypt('clave-prueba')
    salida = BytesIO()
    writer.write(salida)
    return salida.getvalue()


def pdf_configurado(configurar):
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    configurar(writer)
    salida = BytesIO()
    writer.write(salida)
    return salida.getvalue()


@override_settings(
    FINANCIACION_EDUCATIVA_PDF_MAX_BYTES=1024 * 1024,
    FINANCIACION_EDUCATIVA_PDF_MAX_PAGES=4,
    FINANCIACION_EDUCATIVA_PDF_MAX_OBJECTS=1000,
    FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES=512 * 1024,
    FINANCIACION_EDUCATIVA_PDF_MAX_PIXELS_PER_PAGE=1_000_000,
    FINANCIACION_EDUCATIVA_PDF_MAX_AI_PAGES=3,
    FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS=4000,
    FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS=10,
    FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=False,
)
class ProcesamientoPDFSeguroTests(SimpleTestCase):
    def test_pdf_con_texto_se_extrae_y_renderiza_solo_paginas_necesarias(self):
        resultado = procesar_pdf_seguro(pdf_sintetico(textos=(
            'CERTIFICADO LABORAL DE PERSONA PRUEBA PERIODO 2026 CON VALORES',
        )))

        self.assertEqual(resultado.numero_paginas, 1)
        self.assertEqual(resultado.metodo_extraccion, 'PDF_HYBRID')
        self.assertIn('CERTIFICADO LABORAL', resultado.paginas[0].texto)
        self.assertTrue(resultado.paginas[0].imagen_png.startswith(b'\x89PNG'))

    def test_pdf_escaneado_se_renderiza_sin_inventar_texto(self):
        resultado = procesar_pdf_seguro(pdf_sintetico())

        self.assertEqual(resultado.metodo_extraccion, 'PDF_RENDER')
        self.assertEqual(resultado.paginas[0].texto, '')
        self.assertTrue(resultado.paginas[0].imagen_png)

    def test_pdf_hibrido_conserva_texto_y_renderiza_paginas_sin_texto(self):
        resultado = procesar_pdf_seguro(pdf_sintetico(textos=(
            'CERTIFICADO DE INGRESOS PERSONA PRUEBA PERIODO 2026 VALOR PRESENTE',
            '',
        )))

        self.assertEqual(resultado.numero_paginas, 2)
        self.assertEqual(resultado.metodo_extraccion, 'PDF_HYBRID')
        self.assertEqual(resultado.paginas_analizadas, (1, 2))

    def assert_codigo(self, contenido, codigo):
        with self.assertRaises(ErrorProcesamientoPDF) as contexto:
            procesar_pdf_seguro(contenido)
        self.assertEqual(contexto.exception.codigo, codigo)
        return contexto.exception

    def test_rechaza_firma_magica_falsa(self):
        self.assert_codigo(b'NO-PDF\n%%EOF', 'PDF_INVALID_SIGNATURE')

    def test_rechaza_pdf_corrupto_o_truncado(self):
        self.assert_codigo(b'%PDF-1.7\nobjeto truncado', 'PDF_CORRUPT')

    def test_rechaza_pdf_cifrado_y_lo_identifica(self):
        error = self.assert_codigo(
            pdf_sintetico(cifrado=True),
            'PDF_ENCRYPTED',
        )
        self.assertTrue(error.metadata['pdf_cifrado'])

    def test_rechaza_pdf_sin_paginas(self):
        self.assert_codigo(pdf_sintetico(textos=()), 'PDF_NO_PAGES')

    @override_settings(FINANCIACION_EDUCATIVA_PDF_MAX_PAGES=1)
    def test_rechaza_demasiadas_paginas(self):
        self.assert_codigo(pdf_sintetico(textos=('', '')), 'PDF_TOO_MANY_PAGES')

    @override_settings(FINANCIACION_EDUCATIVA_PDF_MAX_BYTES=20)
    def test_rechaza_archivo_demasiado_grande(self):
        self.assert_codigo(pdf_sintetico(), 'PDF_TOO_LARGE')

    def test_tokens_accidentales_en_stream_no_son_contenido_activo(self):
        resultado = procesar_pdf_seguro(
            pdf_sintetico(textos=('/JavaScript /OpenAction /Launch',))
        )

        self.assertEqual(resultado.numero_paginas, 1)
        self.assertFalse(resultado.contenido_activo_detectado)

    def test_enlace_uri_pasivo_continua(self):
        resultado = procesar_pdf_seguro(pdf_configurado(
            lambda writer: writer.add_uri(
                0,
                'https://example.invalid/documento',
                (10, 10, 100, 30),
            )
        ))

        self.assertEqual(resultado.numero_paginas, 1)

    def test_formulario_pasivo_sin_javascript_continua(self):
        def configurar(writer):
            writer._root_object[NameObject('/AcroForm')] = writer._add_object(
                DictionaryObject({NameObject('/Fields'): ArrayObject()})
            )

        resultado = procesar_pdf_seguro(pdf_configurado(configurar))

        self.assertEqual(resultado.numero_paginas, 1)

    def test_rechaza_javascript_estructural_con_codigo_exacto(self):
        error = self.assert_codigo(
            pdf_configurado(lambda writer: writer.add_js('void(0);')),
            'PDF_JAVASCRIPT',
        )
        self.assertEqual(
            error.metadata['caracteristicas_seguridad'],
            ['JAVASCRIPT'],
        )

    def test_rechaza_open_action_estructural_con_codigo_exacto(self):
        def configurar(writer):
            writer._root_object[NameObject('/OpenAction')] = DictionaryObject({
                NameObject('/S'): NameObject('/Named'),
                NameObject('/N'): NameObject('/Print'),
            })

        self.assert_codigo(
            pdf_configurado(configurar),
            'PDF_OPEN_ACTION',
        )

    def test_rechaza_launch_estructural_con_codigo_exacto(self):
        def configurar(writer):
            writer._root_object[NameObject('/OpenAction')] = DictionaryObject({
                NameObject('/S'): NameObject('/Launch'),
                NameObject('/F'): TextStringObject('programa-no-ejecutable'),
            })

        self.assert_codigo(
            pdf_configurado(configurar),
            'PDF_LAUNCH_ACTION',
        )

    def test_rechaza_accion_adicional_con_codigo_exacto(self):
        def configurar(writer):
            writer._root_object[NameObject('/AA')] = DictionaryObject({
                NameObject('/WC'): DictionaryObject({
                    NameObject('/S'): NameObject('/Named'),
                    NameObject('/N'): NameObject('/Print'),
                }),
            })

        self.assert_codigo(
            pdf_configurado(configurar),
            'PDF_ADDITIONAL_ACTION',
        )

    def test_rechaza_xfa_con_codigo_exacto(self):
        def configurar(writer):
            writer._root_object[NameObject('/AcroForm')] = writer._add_object(
                DictionaryObject({
                    NameObject('/Fields'): ArrayObject(),
                    NameObject('/XFA'): TextStringObject('xfa-sintetico'),
                })
            )

        self.assert_codigo(
            pdf_configurado(configurar),
            'PDF_XFA_ACTIVE_CONTENT',
        )

    def test_rechaza_adjunto_embebido(self):
        self.assert_codigo(pdf_configurado(
            lambda writer: writer.add_attachment('prueba.txt', b'inofensivo')
        ), 'PDF_EMBEDDED_FILE')

    @override_settings(FINANCIACION_EDUCATIVA_PDF_MAX_OBJECT_BYTES=10)
    def test_rechaza_objeto_declarado_anormalmente_grande(self):
        self.assert_codigo(
            b'%PDF-1.7\n1 0 obj <</Length 999999>>\n%%EOF',
            'PDF_OBJECT_TOO_LARGE',
        )

    @override_settings(FINANCIACION_EDUCATIVA_PDF_MAX_OBJECTS=1)
    def test_rechaza_demasiados_objetos(self):
        self.assert_codigo(pdf_sintetico(), 'PDF_TOO_MANY_OBJECTS')

    @override_settings(FINANCIACION_EDUCATIVA_PDF_MAX_EXTRACTED_CHARACTERS=10)
    def test_rechaza_texto_excesivo(self):
        self.assert_codigo(
            pdf_sintetico(textos=('TEXTO EXTENSO PARA SUPERAR LIMITE',)),
            'PDF_TEXT_LIMIT_EXCEEDED',
        )

    @patch(
        'financiacion_educativa.services.procesamiento_pdf.time.monotonic',
        side_effect=(0, 20),
    )
    def test_timeout_es_error_temporal(self, _monotonic):
        error = self.assert_codigo(pdf_sintetico(), 'PDF_PROCESSING_TIMEOUT')
        self.assertTrue(error.temporal)

    @patch('pypdfium2.PdfDocument', side_effect=RuntimeError('render-failure'))
    def test_fallo_renderizado_es_temporal_y_controlado(self, _documento):
        error = self.assert_codigo(pdf_sintetico(), 'PDF_RENDER_ERROR')
        self.assertTrue(error.temporal)

    @override_settings(
        FINANCIACION_EDUCATIVA_PDF_USE_SUBPROCESS=True,
        FINANCIACION_EDUCATIVA_PDF_PROCESSING_TIMEOUT_SECONDS=15,
    )
    def test_subproceso_descartable_devuelve_resultado_controlado(self):
        resultado = procesar_pdf_seguro(pdf_sintetico())

        self.assertEqual(resultado.numero_paginas, 1)
        self.assertEqual(resultado.metodo_extraccion, 'PDF_RENDER')
