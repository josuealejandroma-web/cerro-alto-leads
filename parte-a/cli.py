"""Local operator commands. Read access to the database implies access to patient data."""
import argparse
import json
from app import timestamp
from config import Settings
from store import Store


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    status = sub.add_parser('estado')
    group = status.add_mutually_exclusive_group(required=True)
    group.add_argument('--event-id')
    group.add_argument('--desde')
    for name in ('detalle', 'reintentar'):
        command = sub.add_parser(name)
        target = command.add_mutually_exclusive_group(required=True)
        target.add_argument('--event-id')
        target.add_argument('--receipt-id')
    sub.add_parser('metricas')
    sub.add_parser('reintentar-avisos')
    sub.add_parser('avisos')
    sub.add_parser('revision')
    sub.add_parser('tareas')
    args = parser.parse_args()
    store = Store(Settings.from_env())
    if args.command == 'estado':
        result = store.status(event_id=args.event_id) if args.event_id else store.status(since=timestamp(args.desde))
    elif args.command == 'detalle':
        result = store.detail(event_id=args.event_id, receipt_id=args.receipt_id)
    elif args.command == 'reintentar':
        result = {'reencolados': store.retry(event_id=args.event_id, receipt_id=args.receipt_id)}
    elif args.command == 'reintentar-avisos':
        result = {'reencolados': store.retry(notices=True)}
    elif args.command == 'avisos':
        with store.connect() as db:
            result = [dict(r) for r in db.execute('SELECT id,event_ref,kind,estado,attempts,created_at,sent_at FROM outbox ORDER BY created_at')]
    elif args.command == 'revision':
        with store.connect() as db:
            result = [dict(r) for r in db.execute("SELECT id AS receipt_id,event_id,estado,motivo,stage FROM events WHERE estado IN ('rechazado','error') ORDER BY received_at")]
    elif args.command == 'tareas':
        with store.connect() as db:
            result = []
            for lead in db.execute("SELECT * FROM leads WHERE route IN ('caliente','tibio') ORDER BY CASE route WHEN 'caliente' THEN 0 ELSE 1 END,created_at"):
                item = db.execute("SELECT * FROM events WHERE lead_id=? AND stage='done' AND estado IN ('procesado','duplicado') ORDER BY received_at DESC LIMIT 1", (lead['id'],)).fetchone()
                if item:
                    normalized = json.loads(item['normalized'])
                    result.append({'lead_id': lead['id'], 'ruta': lead['route'], 'event_id': item['event_id'],
                        'nombre': normalized['full_name'], 'telefono_e164': normalized['telefono_e164'],
                        'email': normalized['email'], 'primer_mensaje': item['draft'],
                        'propuesta': json.loads(item['proposal']) if item['proposal'] else None})
    else:
        result = store.metrics()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
