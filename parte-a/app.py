"""Python 3.11+ local HTTP service; network work never blocks webhook acknowledgement."""
import argparse
import datetime as dt
import hmac
import json
import logging
import socket
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from config import Settings
from engine import Engine
from integrations import notify
from store import Store

MAX_BODY = 256 * 1024
LOG = logging.getLogger('cerro-alto')


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.timestamp()


def handler_for(settings, store):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'CerroAlto/1'

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format, *args):
            pass  # Default HTTP logging includes query strings with patient contacts.

        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            try:
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Saved event remains durable even if the sender disconnects.

        def authorized(self, token):
            return not token or hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == '/health':
                try:
                    metrics = store.metrics()
                    self.reply(200 if not metrics['avisos_error'] and not metrics['eventos'].get('error') else 503,
                               {'status': 'ok' if not metrics['avisos_error'] and not metrics['eventos'].get('error') else 'degradado', 'modo': settings.mode})
                except Exception:
                    self.reply(503, {'status': 'almacenamiento_no_disponible'})
                return
            if parsed.path != '/leads/estado':
                self.reply(404, {'error': 'no_encontrado'})
                return
            if not self.authorized(settings.query_token):
                self.reply(401, {'error': 'no_autorizado'})
                return
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            try:
                if set(query) == {'event_id'} and len(query['event_id']) == 1 and query['event_id'][0]:
                    result = store.status(event_id=query['event_id'][0])
                    self.reply(200 if result is not None else 404, result if result is not None else {'error': 'evento_no_recibido'})
                elif set(query) == {'desde'} and len(query['desde']) == 1:
                    self.reply(200, store.status(since=timestamp(query['desde'][0])))
                else:
                    self.reply(400, {'error': 'usar_event_id_o_desde'})
            except ValueError:
                self.reply(400, {'error': 'fecha_invalida_requiere_zona_horaria'})
            except Exception:
                self.reply(503, {'error': 'almacenamiento_no_disponible'})

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if path != '/webhook':
                self.reply(404, {'error': 'no_encontrado'})
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    self.reply(400, {'error': 'transfer_encoding_no_soportado'})
                    return
                length = int(self.headers.get('Content-Length', '-1'))
                if length < 0 or length > MAX_BODY:
                    self.reply(413 if length > MAX_BODY else 411, {'error': 'tamano_invalido'})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    self.reply(400, {'error': 'cuerpo_incompleto'})
                    return
                event, replay = store.accept(raw)
                # 2xx means durable reception, including quarantined/rejected payloads.
                self.reply(200 if replay else 202, {'receipt_id': event['id'], 'event_id': event['event_id'], 'estado': event['estado'], 'reintento': replay})
            except (ValueError, socket.timeout):
                self.reply(400, {'error': 'solicitud_invalida'})
            except Exception:
                LOG.critical('code=recepcion_no_persistida')
                self.reply(503, {'error': 'recepcion_no_persistida_reintentar'})
                try:
                    notify(settings, 'Fallo al guardar una entrada. El conector recibio 503; revisar almacenamiento antes de agotar sus reintentos.')
                except Exception:
                    LOG.critical('code=aviso_recepcion_no_disponible')
    return Handler


def make_server(settings, store):
    server = ThreadingHTTPServer((settings.host, settings.port), handler_for(settings, store))
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-config', action='store_true')
    args = parser.parse_args()
    settings = Settings.from_env()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    missing = [name for name, value in [('PATIENTS_API_BASE_URL', settings.patients_url), ('PATIENTS_API_TOKEN', settings.patients_token),
               ('LLM_BASE_URL', settings.llm_url), ('LLM_API_KEY', settings.llm_key), ('LLM_MODEL', settings.llm_model),
               ('TELEGRAM_BOT_TOKEN', settings.telegram_token), ('TELEGRAM_CHAT_ID', settings.telegram_chat)] if not value]
    if not settings.agenda_url:
        LOG.warning('AGENDA_URL ausente: propuestas pendientes de coordinacion, sin enlace ni horario confirmado')
    if missing:
        LOG.warning('Configuracion pendiente: %s', ', '.join(missing))
    if args.check_config:
        print(json.dumps({'modo': settings.mode, 'faltantes': missing, 'agenda_configurada': bool(settings.agenda_url)}, ensure_ascii=False))
        raise SystemExit(1 if missing else 0)
    if settings.host not in ('127.0.0.1', 'localhost', '::1') and not settings.query_token:
        parser.error('Para escuchar fuera de localhost configura QUERY_TOKEN y un proxy HTTPS')
    store = Store(settings)
    engine = Engine(settings, store)
    server = make_server(settings, store)
    engine.start()
    LOG.info('Servicio local puerto=%s modo=%s', settings.port, settings.mode)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.stop()


if __name__ == '__main__':
    main()
