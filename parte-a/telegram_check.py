"""Verify Telegram delivery and optional round trip with an explicit test code.

Does not change a webhook or acknowledge/discard unrelated updates.
"""
import argparse
import json
import time
import urllib.parse
from config import Settings
from integrations import StageError, http_json, notify


def receive_code(settings, code):
    base = settings.telegram_url.rstrip('/') + '/bot' + settings.telegram_token
    info, _ = http_json(base + '/getWebhookInfo', settings.timeout)
    if info.get('ok') is not True or not isinstance(info.get('result'), dict):
        raise StageError('telegram_webhook_info_invalida')
    if info['result'].get('url'):
        raise StageError('telegram_webhook_activo_no_modificado', permanent=True)
    response, _ = http_json(base + '/getUpdates?' + urllib.parse.urlencode({'timeout': 0, 'limit': 100}), settings.timeout)
    if response.get('ok') is not True or not isinstance(response.get('result'), list):
        raise StageError('telegram_updates_invalidos')
    for update in response['result']:
        message = update.get('message', {})
        if str(message.get('chat', {}).get('id')) != settings.telegram_chat:
            continue
        if message.get('text', '').strip() in (code, '/start ' + code):
            return {'update_id': update['update_id'], 'message_id': message['message_id']}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', required=True, help='Unique test code to send from the configured human chat')
    parser.add_argument('--send', action='store_true', help='Send a test request to the configured chat')
    parser.add_argument('--wait', type=int, default=0, help='Maximum seconds to poll for that exact code')
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.send:
        message_id = notify(settings, 'Prueba de ida y vuelta de Cerro Alto. Responde exactamente: ' + args.code + '. Al recibirlo confirmare la recepcion automaticamente.')
        print(json.dumps({'envio': 'ok', 'message_id': message_id}), flush=True)
    deadline = time.monotonic() + max(args.wait, 0)
    while True:
        receipt = receive_code(settings, args.code)
        if receipt:
            outgoing = notify(settings, 'Confirmado: recibi tu codigo de prueba ' + args.code + '. El envio y la recepcion de Telegram funcionan en esta comprobacion.')
            print(json.dumps({'recepcion': 'ok', **receipt, 'respuesta_message_id': outgoing}), flush=True)
            return
        if time.monotonic() >= deadline:
            print(json.dumps({'recepcion': 'pendiente_de_mensaje_con_codigo'}), flush=True)
            raise SystemExit(2)
        time.sleep(min(3, max(0, deadline - time.monotonic())))


if __name__ == '__main__':
    try:
        main()
    except StageError as exc:
        print(json.dumps({'error': exc.code}))
        raise SystemExit(1)
