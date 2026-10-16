"""Deterministic contact validation, safety rules and data minimization."""
import re
import unicodedata
import phonenumbers


def fold(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value or ''))
                   if not unicodedata.combining(c)).casefold()


def name_key(value):
    return ' '.join(re.findall(r'[^\W\d_]+', fold(value)))


def name_matches(a, b):
    a, b = set(name_key(a).split()), set(name_key(b).split())
    return len(a) >= 2 and len(b) >= 2 and (a <= b or b <= a)


def phone(value, region='EC'):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    value = str(value).strip()
    if not value or re.search(r'[A-Za-z]', value):
        return None
    digits = re.sub(r'\D', '', value)
    if len(set(digits)) <= 2:
        return None
    if value.startswith('00'):
        value = '+' + value[2:]
    elif not value.startswith('+') and digits.startswith('593') and len(digits) in (11, 12):
        value = '+' + digits
    try:
        parsed = phonenumbers.parse(value, region)
        if parsed.extension or not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return None


def email(value):
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>.]+(?:\.[^\s@<>.]+)+', value):
        return None
    return value


def normalize(payload, region='EC'):
    warnings = []
    if not isinstance(payload, dict) or not isinstance(payload.get('lead'), dict):
        return None, ['estructura_invalida']
    if payload.get('event_type') != 'lead.created':
        return None, ['event_type_invalido']
    lead = payload['lead']
    result = {}
    for field in ('full_name', 'city', 'service_interest', 'message'):
        value = lead.get(field, '')
        if field == 'city' and isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            value = value[0]
            warnings.append('city_lista_normalizada')
        if not isinstance(value, str):
            warnings.append(field + '_tipo_invalido')
            value = ''
        result[field] = value.strip()
    result['telefono_e164'] = phone(lead.get('phone'), region)
    result['email'] = email(lead.get('email'))
    for field, valid in [('phone', result['telefono_e164']), ('email', result['email'])]:
        if not valid:
            warnings.append(field + '_invalido_o_ausente')
    if not result['full_name']:
        warnings.append('nombre_ausente')
    result['warnings'] = warnings
    return result, ([] if result['telefono_e164'] or result['email'] else ['sin_contacto_valido'])


def signals(lead):
    text = fold(lead.get('message', ''))
    injection = bool(re.search(r'ignora.{0,70}instrucciones|importante para el sistema|clasifica este lead|system prompt', text))
    # Remove explicit control requests; do not treat 'urgencia alta' as a symptom.
    clean = re.sub(r'importante para el sistema:.*?fin de la instruccion\.?', '', text, flags=re.S)
    clean = re.sub(r'\bno (?:quiero|deseo|necesito) (?:agendar|reservar|una cita|que me llamen)', '', clean)
    urgent = bool(re.search(r'dolor (?:muy |dental )?(?:intenso|insoportable|fuerte)|dolor.{0,35}no (?:cede|se (?:me )?pasa)|hinchazon|cara hinchada|encia hinchada|sangrado.{0,30}no (?:para|se detiene)|golpe.{0,35}diente|(?:fractura|fracturado).{0,35}diente|diente.{0,25}(?:roto|fracturado)|fiebre.{0,40}dolor (?:dental|de (?:muela|diente))', clean))
    booking = bool(re.search(r'\bagendar\b|\breservar\b|(?:quiero|quisiera|necesito|pueden dar|me dan).{0,30}(?:una cita|un turno)|(?:llamen|llamame|llamarme).{0,30}(?:pronto|hoy|ahora)|(?:llamenme|me llamen)\b', clean))
    high_value = fold(lead.get('service_interest')) in ('ortodoncia', 'alineadores') and bool(
        re.search(r'decidid[ao]|empezar|comenzar|iniciar|quiero (?:ponerme|hacerme)|ya llego el momento', clean))
    spam = bool(re.search(r'gana.{0,15}(?:dinero|\$\d+)|multiplica tu dinero|vende?mos (?:seguidores|bases de datos)|servicios? (?:de )?seo', clean))
    return {'urgente': urgent, 'agendar': booking, 'alto_valor': high_value,
            'inyeccion': injection, 'spam_evidente': spam}


def minimize(lead):
    text = lead.get('message', '')
    name = lead.get('full_name', '')
    if name:
        text = re.sub(re.escape(name), '[NOMBRE]', text, flags=re.I)
    text = re.sub(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', '[EMAIL]', text)
    text = re.sub(r'(?<!\w)\+?\d(?:[\d ()-]{5,}\d)(?!\w)', '[CONTACTO_O_ID]', text)
    text = re.sub(r'(?i)(?:cedula|pasaporte|tarjeta)\s*[:#]?\s*[A-Z0-9-]{5,}', '[IDENTIFICADOR]', text)
    if len(text) > 12000:
        text = text[:6000] + '\n[TEXTO RECORTADO; CONSERVAMOS EL FINAL]\n' + text[-6000:]
    return {'servicio_declarado': lead.get('service_interest', ''), 'mensaje_no_confiable': text}


def route(lead, classification, enrichment):
    flags = signals(lead)
    if flags['urgente']:
        return 'caliente', 'senales_de_urgencia'
    if enrichment.get('tiene_pendiente') is True:
        return 'caliente', 'paciente_con_tratamiento_pendiente'
    if flags['agendar'] or flags['alto_valor']:
        return 'caliente', 'intencion_explicita'
    if not flags['inyeccion'] and (classification['urgencia'] == 'alta' or classification['intencion'] in ('agendar', 'contacto', 'tratamiento_alto_valor')):
        return 'caliente', 'intencion_detectada_por_llm'
    if flags['spam_evidente'] and classification['intencion'] == 'spam' and classification['confianza'] >= .9:
        return 'basura', 'spam_evidente_confirmado'
    return 'tibio', 'interes_o_duda_conservado'


def draft(lead, route_value, agenda_url=''):
    if route_value == 'basura':
        return None, None
    if route_value == 'caliente':
        proposal = {'tipo': 'enlace_agenda' if agenda_url else 'solicitud_de_agendamiento',
                    'url': agenda_url or None, 'estado': 'sin_confirmar',
                    'nota': 'Confirmar sede, disponibilidad y datos obligatorios antes de reservar.'}
        message = 'Hola, somos Clínica Dental Cerro Alto. Recepción revisará tu solicitud para coordinar una valoración presencial. ¿Qué sede prefieres, Norte o Valle, y en qué horario podrías asistir?'
        if agenda_url:
            message += ' Puedes solicitar tu cita aquí: ' + agenda_url
        return message, proposal
    return 'Hola, somos Clínica Dental Cerro Alto. Gracias por tu interés. ¿Qué información necesitas para ayudarte con tu consulta?', None
