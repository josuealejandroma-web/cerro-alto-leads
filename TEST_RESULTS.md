# Verificación realizada

## Entorno

Windows, Python 3.12.14. La guía exige Python 3.11+ por la dependencia transitiva fijada `rpds-py`. Dependencias fijadas instaladas; `pip check` sin conflictos. Compilación de los archivos Python sin errores. Material original de `payloads/` y `enviar_payloads.py` sin modificaciones.

## Suite automatizada

Comando: `python -m unittest discover -s parte-a/tests -v`.

**37 tests aprobados, 0 fallos; 29,038 segundos en la ejecución final.**

Incluye el envío de los diez payloads originales a un servidor HTTP real con terceros simulados; duplicados concurrentes; contacto solo por correo; teléfono numérico e internacional; JSON y esquema LLM inválidos; paginación; asociación ambigua de pacientes; errores 401/429/503; recuperación; inyección; minimización; avisos y vigilancia de pendientes. También comprueba TLS y la recepción de Telegram: chat autorizado, código exacto, rechazo de actualizaciones inválidas y preservación de un webhook existente.

## Demo con workers en ejecución

Se arrancó `python parte-a/demo.py` y se ejecutó dos veces seguidas:

`python parte-a/verificar_payloads.py http://127.0.0.1:8080`

Ambas ejecuciones devolvieron `resultado: OK`. Resultado acumulado en la base de demostración:

| Comprobación | Resultado |
|---|---|
| Entregas recibidas | 20 |
| Eventos únicos | 9 |
| Leads identificados | 8 |
| Eventos procesados | 8 |
| Evento duplicado de contacto | 1 |
| Avisos calientes enviados al doble de Telegram | 4 |
| Errores persistidos | 0 |
| Avisos adicionales en la segunda pasada | 0 |

Las rutas de esa demo fueron: 01/02/03 calientes; 04/05 tibios; 06/07 calientes; 08 basura; 09 caliente; 10 tibio. Los resultados de pacientes en esta demostración son fixtures locales diseñados para probar los casos; no describen lo que devolvió la API real.

## Verificación con proveedores reales · 16/10/2026

Con autorización del usuario se sustituyó Gemini 3.8 Flash, que devolvía 503 por alta demanda, por **Gemini 3.5 Flash-Lite**, conservando la misma clave y endpoint de Google.

Primero se probaron tres clasificaciones reales: contacto solo por correo, texto largo con intención final y mensaje con inyección. Las tres devolvieron JSON válido y la ruta esperada, aproximadamente en dos segundos cada una. Después se ejecutaron los diez payloads originales por HTTP, dos veces, con API de pacientes, LLM y Telegram reales. Ambas verificaciones HTTP devolvieron `OK`.

| Comprobación real | Resultado |
|---|---|
| Entregas recibidas | 20 |
| Eventos únicos | 9 |
| Leads identificados | 8 |
| Estados finales | 8 procesados y 1 duplicado de contacto |
| Clasificaciones reales validadas | 9 |
| Errores del LLM con el modelo nuevo | 0; ningún 503 |
| Avisos calientes entregados por Telegram | 4 |
| Mayor demora de un aviso caliente desde su creación | 21,66 segundos |
| Avisos nuevos al repetir los diez payloads | 0 |
| Leads pendientes o en error al finalizar | 0 |
| Avisos pendientes o fallidos al finalizar | 0 |

Durante el enriquecimiento la API real produjo **dos respuestas HTTP 500 y una respuesta JSON inválida**. Los tres casos se recuperaron con reintentos y sus tres avisos de error fueron enviados a Telegram. Se conservó el historial de errores: un flujo recuperado no se presenta como si los proveedores nunca hubieran fallado. La auditoría comprobó estados finales, nueve clasificaciones válidas, avisos entregados y ausencia de incidencias sin resolver.

El lead que antes había fallado con Gemini 3.8 se reencoló desde su base persistente, sin reenviar el webhook, y quedó `procesado` como tibio con Gemini 3.5 Flash-Lite.

## Telegram: envío y recepción reales

Se envió un mensaje con un código de prueba al chat configurado. El usuario escribió ese código al bot; `getUpdates` lo recibió desde ese mismo chat y el bot devolvió una confirmación automática. Esto comprueba el recorrido completo de envío, respuesta humana, recepción por API y nueva respuesta del bot.

No se modificó ningún webhook ni se descartaron actualizaciones ajenas. Los tokens, identificadores del chat y conversaciones no se incluyen en esta evidencia ni en el ZIP.

## Conectividad y pendientes

- `/v1/health` de la API de pacientes: `status=ok`.
- Consulta autenticada: correcta tras recibir el token exacto del usuario. La transcripción inicial de la captura había devuelto 401.
- Enriquecimiento real: siete ejemplos completados. Eventos 01/04/05/07/10 sin coincidencia; evento 06 con coincidencia y tratamiento pendiente, tres páginas leídas; evento 09 con coincidencia y tratamiento pendiente, dos páginas leídas. No se incluyen identidades o historial en esta evidencia.
- LLM real: Gemini `gemini-3.5-flash-lite`, con generación y validación comprobadas en los diez payloads; sin errores del LLM en esta ejecución.
- Telegram real: aviso técnico de prueba entregado. Se corrigió el problema local de confianza TLS usando el almacén nativo mediante `truststore`, con validación de certificados y host activas.
- Intento real controlado del evento 05: conservado en `data/live-check.sqlite3`, recuperado y finalizado como `procesado` / `tibio`. Sus errores y avisos anteriores siguen en auditoría.
- Agenda: pendiente de enlace válido o ficha completa de horarios/duraciones y disponibilidad. El 18/10/2026 se trató como fecha solicitada de ejemplo y no como reserva confirmada.
- Publicación: código en el fork `https://github.com/josuealejandroma-web/cerro-alto-leads`; sin envío de la etapa desde la plataforma.

Esta evidencia distingue la suite simulada de la prueba de punta a punta con proveedores reales, que ya se completó para los diez payloads. No certifica reserva de citas ni disponibilidad permanente de servicios externos.
