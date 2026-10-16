# Parte A

La guía de instalación, pruebas, operación y límites está en el [README principal](../README.md).

- `app.py`: webhook y consulta HTTP.
- `store.py`: inbox, trabajos recuperables, auditoría y outbox SQLite.
- `rules.py`: normalización, señales de prioridad y minimización.
- `integrations.py`: API de pacientes, LLM y Telegram.
- `engine.py`: workers y watchdog.
- `cli.py`: consulta, revisión y recuperación local.
- `prompts/`: prompt y JSON Schema versionados.
- `tests/`: pruebas con servicios locales simulados.
- `demo.py`: demostración explícitamente simulada.
- `verificar_payloads.py`: envío y comprobación HTTP de los diez archivos originales.
- `telegram_check.py`: prueba de envío y recepción mediante un código escrito por la persona, con confirmación automática.
