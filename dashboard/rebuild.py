"""Rebuild the offline dashboard and its independently readable summary (stdlib only)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import median


def aggregate(rows: list[dict]) -> dict:
    processed = [r for r in rows if r['estado'] == 'procesado']
    hot = [r for r in processed if r['ruta'] == 'caliente']
    valid = [float(r['minutos_hasta_aviso']) for r in hot if r['minutos_hasta_aviso'] != '']
    vals = sorted(valid)
    start, end = min(r['recibido_en'][:10] for r in rows), max(r['recibido_en'][:10] for r in rows)
    days = []
    at = date.fromisoformat(start)
    while at <= date.fromisoformat(end):
        d = at.isoformat()
        dr = [r for r in rows if r['recibido_en'][:10] == d]
        dh = [r for r in dr if r['estado'] == 'procesado' and r['ruta'] == 'caliente']
        days.append({'date': d, 'leads': len(dr), 'platform': dict(Counter(r['plataforma'] for r in dr)),
                     'status': dict(Counter(r['estado'] for r in dr)),
                     'hot': len(dh), 'hot_under_5_min': sum(r['minutos_hasta_aviso'] != '' and float(r['minutos_hasta_aviso']) < 5 for r in dh),
                     'hot_missing_latency': sum(r['minutos_hasta_aviso'] == '' for r in dh)})
        at += timedelta(days=1)
    campaigns = []
    for campaign in sorted({r['campana'] for r in rows}):
        cr = [r for r in rows if r['campana'] == campaign]
        cp = [r for r in cr if r['estado'] == 'procesado']
        ch = [r for r in cp if r['ruta'] == 'caliente']
        booked = sum(r['cita_agendada'] == 'si' for r in ch)
        campaigns.append({'name': campaign, 'leads': len(cr), 'processed': len(cp),
                          'routes': dict(Counter(r['ruta'] for r in cp)), 'hot': len(ch), 'hot_booked': booked,
                          'hot_booking_rate': booked / len(ch) if ch else None,
                          'hot_unknown_booking': sum(r['cita_agendada'] not in ['si', 'no'] for r in ch)})
    failure_by_reason = Counter(r['motivo_fallo'] or '(sin motivo)' for r in rows if r['estado'] == 'fallido')
    reject_by_reason = Counter(r['motivo_fallo'] or '(sin motivo)' for r in rows if r['estado'] == 'rechazado')
    anomalies = []
    a = [r for r in rows if '2026-08-18' <= r['recibido_en'][:10] <= '2026-08-21']
    b = [r for r in rows if '2026-08-11' <= r['recibido_en'][:10] <= '2026-08-14']
    anomalies.append({'id': 'instagram_gap', 'window': ['2026-08-18', '2026-08-21'],
                      'evidence': {'current_platform_counts': dict(Counter(r['plataforma'] for r in a)),
                                   'prior_same_weekdays_window': ['2026-08-11', '2026-08-14'],
                                   'prior_platform_counts': dict(Counter(r['plataforma'] for r in b)),
                                   'instagram_returns_on': '2026-08-22'},
                      'hypothesis': 'Posible interrupción de la entrada de Instagram o pausa de campañas. El CSV no permite distinguirlas ni calcular leads perdidos.',
                      'proposed_alert': 'Con campañas activas, avisar al responsable de integración si un canal lleva 2 horas sin entradas mientras el otro recibe al menos 3. Confirmar en Meta si hay leads y recuperar desde la fuente antes de reactivar; umbral propuesto, no implementado.'})
    a = [r for r in rows if '2026-09-08' <= r['recibido_en'][:10] <= '2026-09-10']
    anomalies.append({'id': 'api_timeout_cluster', 'window': ['2026-09-08', '2026-09-10'],
                      'evidence': {'leads': len(a), 'failed': sum(r['estado'] == 'fallido' for r in a),
                                   'failure_reasons': dict(Counter(r['motivo_fallo'] for r in a if r['estado'] == 'fallido'))},
                      'hypothesis': 'Degradación temporal de la API de pacientes o de su acceso. No hay logs para probar la causa técnica.',
                      'proposed_alert': 'Avisar al responsable técnico ante 3 timeouts consecutivos o más del 5% de errores de API en 15 minutos, con al menos 20 intentos; revisar estado de API, conservar pendientes y reintentar con backoff. Umbrales propuestos.'})
    return {'source': {'file': 'leads_90_dias.csv', 'url': 'https://3-229-72-89.sslip.io/api/downloads/cierre/leads_90_dias',
                        'time_zone': 'America/Guayaquil (UTC-05:00)', 'period_inclusive': [start, end],
                        'calendar_days': len(days), 'row_grain': 'Un registro por lead_id. No se eliminan filas.'},
            'totals': {'rows': len(rows), 'unique_lead_ids': len({r['lead_id'] for r in rows}),
                       'status': dict(Counter(r['estado'] for r in rows)), 'platform': dict(Counter(r['plataforma'] for r in rows)),
                       'processed_routes': dict(Counter(r['ruta'] for r in processed)), 'hot': len(hot),
                       'hot_booked': sum(r['cita_agendada'] == 'si' for r in hot),
                       'hot_missing_booking': sum(r['cita_agendada'] not in ['si', 'no'] for r in hot),
                       'warm_booked': sum(r['ruta'] == 'tibio' and r['cita_agendada'] == 'si' for r in processed),
                       'patient_api_blank': sum(r['ya_es_paciente'] == '' for r in rows),
                       'aligners_outside_valle': sum(r['servicio'] == 'Alineadores' and r['sede'] != 'Valle' for r in rows)},
            'sla': {'rule': 'minutos_hasta_aviso < 5 (estricto)', 'denominator': len(hot),
                    'measured': len(valid), 'under_5': sum(v < 5 for v in valid),
                    'exactly_5': sum(v == 5 for v in valid), 'at_least_5': sum(v >= 5 for v in valid),
                    'missing': len(hot) - len(valid), 'median_min': median(valid) if valid else None,
                    'p90_nearest_rank_min': vals[math.ceil(.9 * len(vals)) - 1] if vals else None,
                    'max_min': max(valid) if valid else None},
            'failure_reasons': dict(failure_by_reason), 'rejection_reasons': dict(reject_by_reason),
            'campaigns': campaigns, 'daily': days, 'anomalies': anomalies}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(__file__).with_name('leads_90_dias.csv'))
    args = parser.parse_args()
    out = Path(__file__).parent
    raw = args.source.read_bytes()
    if args.source.resolve() != out.joinpath('leads_90_dias.csv').resolve():
        out.joinpath('leads_90_dias.csv').write_bytes(raw)
    rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
    expected = ['lead_id', 'recibido_en', 'plataforma', 'campana', 'servicio', 'ciudad', 'ya_es_paciente', 'sede', 'estado', 'motivo_fallo', 'ruta', 'minutos_hasta_aviso', 'cita_agendada']
    assert list(rows[0]) == expected, 'Esquema inesperado'
    assert len({r['lead_id'] for r in rows}) == len(rows), 'lead_id repetido: revisar la granularidad antes de publicar'
    assert all(r['recibido_en'].endswith('-05:00') for r in rows), 'Revisar zona horaria'
    assert all(r['plataforma'] in ['facebook', 'instagram'] for r in rows)
    assert all(r['estado'] in ['procesado', 'fallido', 'rechazado', 'duplicado'] for r in rows)
    assert all((r['ruta'] in ['caliente', 'tibio', 'basura']) == (r['estado'] == 'procesado') for r in rows), 'Estado/ruta inconsistente'
    assert all(r['minutos_hasta_aviso'] == '' or float(r['minutos_hasta_aviso']) >= 0 for r in rows)
    summary = aggregate(rows)
    summary['source']['sha256'] = hashlib.sha256(raw).hexdigest()
    out.joinpath('summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    data = {'columns': expected, 'rows': [[r[c] for c in expected] for r in rows], 'source_sha256': summary['source']['sha256']}
    template = out.joinpath('dashboard.template.html').read_text(encoding='utf-8')
    assert template.count('/*DATA_JSON*/null') == 1
    # Safe for script text even if future source strings contain an HTML closing tag.
    embedded = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    out.joinpath('index.html').write_text(template.replace('/*DATA_JSON*/null', embedded), encoding='utf-8')
    print(json.dumps({'rows': len(rows), 'sha256': summary['source']['sha256'], 'totals': summary['totals'], 'sla': summary['sla']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
