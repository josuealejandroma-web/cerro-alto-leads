"""Send ORIGINAL payloads via HTTP, poll the public contract, and assert business invariants."""
import argparse
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def http(url, payload=None, token=''):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    raw = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(url, data=raw, headers=headers)
    start = time.monotonic()
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.load(response), time.monotonic() - start


def verify(url, timeout=180, token=''):
    since = dt.datetime.now(dt.timezone.utc).isoformat()
    payloads = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((ROOT / 'payloads').glob('*.json'))]
    assert len(payloads) == 10, 'Se requieren los diez payloads originales'
    for index, payload in enumerate(payloads, 1):
        status, _, duration = http(url + '/webhook', payload)
        assert 200 <= status < 300 and duration < 10, f'Recepcion fallo en evento_{index:02}'
    deadline, states = time.monotonic() + timeout, {}
    while time.monotonic() < deadline:
        for payload in payloads:
            event_id = payload['event_id']
            _, states[event_id], _ = http(url + '/leads/estado?' + urllib.parse.urlencode({'event_id': event_id}), token=token)
        if all(s['estado'] in ('procesado', 'duplicado', 'rechazado', 'error') for s in states.values()):
            break
        time.sleep(1)
    values = [states[p['event_id']] for p in payloads]
    required = {'event_id', 'estado', 'ruta', 'lead_id', 'duplicado_de', 'telefono_e164', 'email', 'motivo'}
    for index, state in enumerate(values, 1):
        assert set(state) == required, f'Contrato incorrecto evento_{index:02}'
        assert state['estado'] in ('procesado', 'duplicado'), f'evento_{index:02}: {state["estado"]}, {state["motivo"]}'
    assert values[0]['ruta'] == 'caliente', 'Solicitud de cita no priorizada'
    assert values[0]['lead_id'] == values[1]['lead_id'] == values[2]['lead_id'], 'Duplicado creo otra tarea'
    assert values[2]['duplicado_de'] == payloads[0]['event_id'], 'Falta referencia al evento original'
    assert values[3]['telefono_e164'] == '+13055550142', 'Telefono internacional incorrecto'
    assert values[4]['telefono_e164'] is None and values[4]['email'], 'No se respeto el contacto solo por email'
    assert values[5]['telefono_e164'] == '+593987650021', 'Telefono numerico incorrecto'
    assert values[6]['ruta'] == 'caliente', 'Se perdio la intencion al final del texto largo'
    assert values[7]['ruta'] == 'basura', 'Spam explicito no identificado'
    for index in (3, 4, 5, 8, 9):
        assert values[index]['ruta'] in ('tibio', 'caliente'), f'Interes real descartado en evento_{index+1:02}'
        # Enrichment may legitimately upgrade an informational message, including an injection attempt.
        if values[index]['ruta'] == 'caliente':
            assert values[index]['motivo'] == 'paciente_con_tratamiento_pendiente', f'Caliente sin evidencia en evento_{index+1:02}'
    _, listing, _ = http(url + '/leads/estado?' + urllib.parse.urlencode({'desde': since}), token=token)
    assert isinstance(listing, list), 'desde debe devolver una lista'
    assert {p['event_id'] for p in payloads} <= {item['event_id'] for item in listing}, 'desde no incluye todas las entradas de esta ejecucion'
    try:
        http(url + '/leads/estado?event_id=evt_nunca_recibido_verificacion', token=token)
        raise AssertionError('Un evento desconocido debe devolver 404')
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    return {'resultado': 'OK', 'payloads_enviados': 10, 'eventos_unicos': len(states),
            'rutas': {f'evento_{i:02}': state['ruta'] for i, state in enumerate(values, 1)},
            'nota': 'Verifica avisos reales y tiempos con cli.py avisos. Esto no certifica la entrega de Telegram.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_url')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    try:
        result = verify(args.base_url.rstrip('/'), args.timeout, os.getenv('QUERY_TOKEN', ''))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        # Error messages only contain controlled assertions; never include arbitrary URL/HTTP errors.
        message = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        print(json.dumps({'resultado': 'ERROR', 'detalle': message}, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
