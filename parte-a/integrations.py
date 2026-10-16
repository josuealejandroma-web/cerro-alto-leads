"""Bounded HTTP adapters. No external response bodies are logged."""
import json
import math
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from jsonschema import Draft202012Validator
import truststore
from rules import email, minimize, name_matches


class StageError(Exception):
    def __init__(self, code, permanent=False, retry_after=None):
        self.code, self.permanent, self.retry_after = code, permanent, retry_after
        super().__init__(code)


class RateWait(Exception):
    def __init__(self, seconds):
        self.seconds = seconds


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_json(url, timeout, token='', body=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
        raise StageError('url_requiere_https', permanent=True)
    if parsed.username or parsed.password:
        raise StageError('credenciales_en_url_no_permitidas', permanent=True)
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        # Use OS certificate trust (including enterprise CAs on Windows), without
        # disabling certificate or hostname verification.
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=context)).open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise StageError('respuesta_demasiado_grande')
            try:
                result = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeDecodeError):
                raise StageError('respuesta_json_invalida') from None
            return result, response.headers
    except urllib.error.HTTPError as exc:
        wait = None
        if exc.code == 429:
            try:
                wait = max(1, float(exc.headers.get('Retry-After', '60')))
            except ValueError:
                wait = 60
        raise StageError(f'http_{exc.code}', permanent=400 <= exc.code < 500 and exc.code not in (408, 429), retry_after=wait) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise StageError('tls_certificado_no_confiable', permanent=True) from None
        raise StageError('red_o_timeout') from None
    except (TimeoutError, OSError):
        raise StageError('red_o_timeout') from None


def patients_step(settings, store, lead, progress):
    if not settings.patients_url or not settings.patients_token:
        raise StageError('config_api_pacientes_ausente', permanent=True)
    progress = dict(progress)
    phase = progress.get('phase', 'patients')
    if phase == 'done':
        return progress, True
    wait = store.api_slot()
    if wait:
        raise RateWait(wait)
    if phase == 'patients':
        params = {key: value for key, value in [('telefono', lead['telefono_e164']), ('email', lead['email'])] if value}
        path = '/v1/pacientes'
    else:
        params = {}
        path = '/v1/pacientes/' + urllib.parse.quote(progress['paciente_id'], safe='') + '/tratamientos'
    if progress.get('cursor'):
        params['cursor'] = progress['cursor']
    url = settings.patients_url.rstrip('/') + path + '?' + urllib.parse.urlencode(params)
    try:
        result, headers = http_json(url, settings.timeout, settings.patients_token)
    except StageError as exc:
        if exc.retry_after is not None:
            store.block_api(exc.retry_after)
            raise RateWait(exc.retry_after) from None
        raise
    if headers.get('X-RateLimit-Remaining') == '0':
        try:
            store.block_api(max(1, float(headers.get('X-RateLimit-Reset', '60'))))
        except ValueError:
            store.block_api(60)
    if not isinstance(result, dict) or not isinstance(result.get('data'), list) or 'next_cursor' not in result:
        raise StageError('esquema_api_invalido')
    cursor = result['next_cursor']
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise StageError('cursor_api_invalido')
    seen = progress.get('seen_cursors', [])
    if cursor is not None and (cursor in seen or len(seen) >= 100):
        raise StageError('paginacion_repetida_o_excesiva', permanent=True)
    if phase == 'patients':
        candidates = progress.get('candidates', [])
        for patient in result['data']:
            if not isinstance(patient, dict) or not isinstance(patient.get('id'), str) or not isinstance(patient.get('nombre_completo'), str):
                raise StageError('paciente_api_invalido')
            if patient.get('telefono') == lead['telefono_e164'] and lead['telefono_e164'] or (email(patient.get('email')) == lead['email'] and lead['email']):
                minimal = {key: patient.get(key) for key in ('id', 'nombre_completo', 'telefono', 'email', 'sede_habitual', 'ultima_visita', 'estado')}
                if not any(p['id'] == minimal['id'] for p in candidates):
                    candidates.append(minimal)
        progress['candidates'] = candidates
        if cursor is None:
            # A single result from an OR search is still not proof: validate name + contact,
            # or both independent contacts when the form has no usable name.
            matches = []
            for candidate in candidates:
                both = bool(lead['telefono_e164'] and lead['email'] and candidate.get('telefono') == lead['telefono_e164'] and email(candidate.get('email')) == lead['email'])
                if name_matches(lead['full_name'], candidate['nombre_completo']) or (not lead['full_name'] and both):
                    matches.append(candidate)
            progress.pop('candidates', None)
            if len(matches) != 1:
                progress.update(phase='done', estado='ambiguo' if candidates else 'no_encontrado', tiene_pendiente=None if candidates else False, cursor=None)
                return progress, True
            patient = matches[0]
            progress.update(phase='treatments', estado='encontrado', paciente_id=patient['id'],
                            sede_habitual=patient.get('sede_habitual'), ultima_visita=patient.get('ultima_visita'),
                            tiene_pendiente=False, cursor=None, seen_cursors=[])
            return progress, False
    else:
        for treatment in result['data']:
            if not isinstance(treatment, dict) or treatment.get('estado') not in ('pendiente', 'en_curso', 'completado', 'cancelado'):
                raise StageError('tratamiento_api_invalido')
            if treatment['estado'] in ('pendiente', 'en_curso'):
                progress['tiene_pendiente'] = True
        if cursor is None:
            progress.update(phase='done', cursor=None)
            return progress, True
    progress['cursor'] = cursor
    if cursor:
        progress['seen_cursors'] = seen + [cursor]
    return progress, False


PROMPTS = Path(__file__).with_name('prompts')
SCHEMA = json.loads((PROMPTS / 'clasificacion.schema.json').read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA)


def classify(settings, lead):
    if not settings.llm_url or not settings.llm_key or not settings.llm_model:
        raise StageError('config_llm_ausente', permanent=True)
    response_format = {'type': 'json_object'}
    if settings.llm_format == 'json_schema':
        response_format = {'type': 'json_schema', 'json_schema': {'name': 'clasificacion_lead', 'strict': True, 'schema': SCHEMA}}
    elif settings.llm_format != 'json_object':
        raise StageError('formato_llm_invalido', permanent=True)
    prompt = (PROMPTS / 'clasificar.txt').read_text(encoding='utf-8') + '\nEsquema JSON:\n' + json.dumps(SCHEMA, ensure_ascii=False)
    body = {'model': settings.llm_model, 'messages': [
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': json.dumps(minimize(lead), ensure_ascii=False)}],
        'response_format': response_format}
    result, _ = http_json(settings.llm_url.rstrip('/') + '/chat/completions', settings.timeout, settings.llm_key, body)
    try:
        choice = result['choices'][0]
        if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
            raise ValueError()
        parsed = json.loads(choice['message']['content'], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        VALIDATOR.validate(parsed)
        for value in (parsed['presupuesto_estimado'], parsed['confianza']):
            if value is not None and not math.isfinite(value):
                raise ValueError()
    except Exception:
        raise StageError('salida_llm_invalida') from None
    return parsed


def notify(settings, text):
    if not settings.telegram_token or not settings.telegram_chat:
        raise StageError('config_telegram_ausente', permanent=True)
    url = settings.telegram_url.rstrip('/') + '/bot' + settings.telegram_token + '/sendMessage'
    result, _ = http_json(url, settings.timeout, body={'chat_id': settings.telegram_chat, 'text': text, 'protect_content': True})
    if not isinstance(result, dict) or result.get('ok') is not True or not isinstance(result.get('result'), dict) or not isinstance(result['result'].get('message_id'), int):
        raise StageError('respuesta_telegram_invalida')
    return result['result']['message_id']
