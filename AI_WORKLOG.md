# Uso de IA

Asistente utilizado: Codex, con las instrucciones humanas de esta conversación. Sin subagentes.

## Contexto y encargo recibido

- Caso de negocio de Clínica Dental Cerro Alto, ficha textual disponible, anticipación FALLAS y PLAN_PRUEBAS_BOT.
- Brief de Parte A, documentación del conector y de API de pacientes, captura de acceso y repositorio base.
- Elección humana inicial de Gemini `gemini-3.8-flash` y posterior autorización para cambiar el modelo ante saturación, conservando la misma clave. Modelo validado: `gemini-3.5-flash-lite`, mediante el endpoint compatible de Google. Canal: Telegram. Fecha de ejemplo de agenda: 18/10/2026.
- Credenciales: solo en `.env` local excluido de Git; nunca en prompts versionados.

## Instrucción de construcción aplicada

Construir en `parte-a/` recepción persistente antes del 2xx, validación tolerante según el brief, idempotencia por evento, deduplicación conservadora de personas, enriquecimiento paginado y limitado, clasificación LLM con validación estricta, rutas y borradores, avisos por Telegram y recuperación auditable. Priorizar pérdidas silenciosas, urgencias, duplicados, contactos y privacidad. No inventar horarios, precios ni disponibilidad. Mantener el material base y documentar todo lo pendiente.

## Instrucción de verificación aplicada

Usar servicios locales simulados mediante HTTP para probar los diez payloads y fallos de dependencias, carreras, 429, paginación, errores de modelo, inyección y recuperación. Ejecutar un verificador separado que mande los originales por webhook y consulte el contrato del evaluador. Diferenciar evidencia simulada de conectividad real y no publicar secretos.

Con las credenciales entregadas por el usuario, verificar también dos pasadas con proveedores reales, revisar errores recuperados y tiempos de avisos, reprocesar el registro fallido y comprobar Telegram con respuesta humana y confirmación automática. El usuario autorizó expresamente el envío y la recepción de esas pruebas.

## Prompts de ejecución

El prompt del clasificador está versionado en `parte-a/prompts/clasificar.txt`; su contrato está en `parte-a/prompts/clasificacion.schema.json`. El mensaje de usuario se crea exclusivamente con `rules.minimize`, sin resultados de la API de pacientes. Las reglas críticas de ruta se implementan fuera del modelo.
