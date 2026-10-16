"""SQLite inbox + recoverable jobs + transactional notification outbox."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from rules import draft as make_draft, name_key, name_matches, normalize, signals


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, settings):
        self.settings = settings
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, event_id TEXT UNIQUE, received_at REAL NOT NULL,
                    raw TEXT NOT NULL, fingerprint TEXT NOT NULL, normalized TEXT,
                    lead_id TEXT, duplicado_de TEXT, estado TEXT NOT NULL,
                    ruta TEXT, motivo TEXT, stage TEXT NOT NULL DEFAULT 'enrich',
                    attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
                    lease REAL NOT NULL DEFAULT 0, enrichment TEXT NOT NULL DEFAULT '{}',
                    classification TEXT, draft TEXT, proposal TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs ON events(stage,estado,due,lease);
                CREATE TABLE IF NOT EXISTS leads (
                    id TEXT PRIMARY KEY, name_key TEXT NOT NULL, phone TEXT, email TEXT,
                    created_at REAL NOT NULL, primary_event TEXT NOT NULL, route TEXT
                );
                CREATE INDEX IF NOT EXISTS lead_contacts ON leads(phone,email,created_at);
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, event_ref TEXT NOT NULL, received_at REAL NOT NULL,
                    kind TEXT NOT NULL, raw TEXT
                );
                CREATE TABLE IF NOT EXISTS errors (
                    id TEXT PRIMARY KEY, event_ref TEXT, stage TEXT NOT NULL,
                    code TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL,
                    event_ref TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
                    estado TEXT NOT NULL DEFAULT 'pendiente', attempts INTEGER NOT NULL DEFAULT 0,
                    due REAL NOT NULL DEFAULT 0, lease REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, sent_at REAL, external_id TEXT
                );
                CREATE TABLE IF NOT EXISTS api_limit (
                    id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL
                );
                INSERT OR IGNORE INTO api_limit VALUES(1,0);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.settings.db_path, timeout=2, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA busy_timeout=2000')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def enqueue(self, db, key, event_ref, kind, text):
        db.execute('INSERT OR IGNORE INTO outbox(id,dedupe_key,event_ref,kind,text,created_at) VALUES(?,?,?,?,?,?)',
                   (uid(), key, event_ref, kind, text, time.time()))

    def record_error(self, db, event_ref, stage, code):
        # Only controlled error codes, never exception strings, payloads or URLs with credentials.
        db.execute('INSERT INTO errors VALUES(?,?,?,?,?)', (uid(), event_ref, stage, code, time.time()))
        self.enqueue(db, f'error:{event_ref}:{stage}:{code}', event_ref, 'error',
                     f'Fallo del flujo. Referencia {event_ref or "sistema"}; etapa {stage}; codigo {code}. Revisar y recuperar desde la herramienta local.')

    def accept(self, raw):
        now, record_id = time.time(), uid()
        try:
            payload = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non_finite')))
        except (ValueError, UnicodeDecodeError):
            payload = None
        event_id = payload.get('event_id') if isinstance(payload, dict) else None
        event_id = event_id.strip() if isinstance(event_id, str) and event_id.strip() else None
        if event_id and len(event_id) > 200:
            event_id = None
        canonical = encode(payload) if payload is not None else raw.decode('utf-8', 'replace')
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        normalized, reasons = normalize(payload, self.settings.default_region)
        if not event_id:
            reasons.append('event_id_ausente_o_invalido')
        with self.transaction() as db:
            existing = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone() if event_id else None
            if existing:
                conflict = existing['fingerprint'] != fingerprint
                db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)',
                           (record_id, existing['id'], now, 'conflicto' if conflict else 'reintento', canonical if conflict else None))
                if conflict:
                    self.record_error(db, existing['id'], 'ingest', 'event_id_con_contenido_distinto')
                return dict(existing), True
            contact_id, duplicate_of = None, None
            if normalized and not reasons:
                candidates = db.execute('SELECT * FROM leads WHERE created_at>=? AND ((phone IS NOT NULL AND phone=?) OR (email IS NOT NULL AND email=?))',
                                        (now - 30 * 86400, normalized['telefono_e164'], normalized['email'])).fetchall()
                # Contact alone does not establish identity: households may share it.
                candidates = [c for c in candidates if name_matches(normalized['full_name'], c['name_key'])]
                if len(candidates) == 1:
                    contact_id = candidates[0]['id']
                    duplicate_of = candidates[0]['primary_event']
                else:
                    contact_id = uid()
                    db.execute('INSERT INTO leads VALUES(?,?,?,?,?,?,?)',
                               (contact_id, name_key(normalized['full_name']), normalized['telefono_e164'], normalized['email'], now, event_id, None))
            state = 'rechazado' if reasons else 'pendiente'
            db.execute('INSERT INTO events(id,event_id,received_at,raw,fingerprint,normalized,lead_id,duplicado_de,estado,ruta,motivo,stage) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (record_id, event_id, now, canonical, fingerprint, encode(normalized) if normalized else None,
                        contact_id, duplicate_of, state, 'basura' if reasons == ['sin_contacto_valido'] else None,
                        ','.join(reasons) if reasons else None, 'done' if reasons else 'enrich'))
            db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?)', (uid(), record_id, now, 'nuevo', None))
            if reasons:
                self.record_error(db, record_id, 'ingest', 'lead_rechazado_revisable')
            else:
                flags = signals(normalized)
                if flags['urgente'] or flags['agendar'] or flags['alto_valor']:
                    preliminary_draft, preliminary_proposal = make_draft(normalized, 'caliente', self.settings.agenda_url)
                    db.execute('UPDATE events SET ruta=?,draft=?,proposal=? WHERE id=?',
                               ('caliente', preliminary_draft, encode(preliminary_proposal), record_id))
                    # Early operational notice does not wait for rate-limited enrichment/LLM.
                    self.enqueue(db, f'hot:{contact_id}', record_id, 'caliente',
                                 f'Lead prioritario {contact_id}. Solicitud de contacto/agendamiento pendiente de revision por recepcion. Consultar el registro local; no hay cita confirmada.')
            return dict(db.execute('SELECT * FROM events WHERE id=?', (record_id,)).fetchone()), False

    def claim_event(self):
        now = time.time()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM events WHERE stage!='done' AND estado IN ('pendiente','procesando') AND due<=? AND lease<=? ORDER BY received_at LIMIT 1", (now, now)).fetchone()
            if row:
                db.execute("UPDATE events SET estado='procesando',lease=? WHERE id=?", (now + self.settings.lease_seconds, row['id']))
                return dict(row)

    def advance(self, record_id, stage, enrichment=None, classification=None):
        with self.transaction() as db:
            db.execute("UPDATE events SET stage=?,estado='pendiente',attempts=0,due=0,lease=0,enrichment=COALESCE(?,enrichment),classification=COALESCE(?,classification) WHERE id=?",
                       (stage, encode(enrichment) if enrichment is not None else None,
                        encode(classification) if classification is not None else None, record_id))

    def defer(self, record_id, delay, code=None, permanent=False):
        with self.transaction() as db:
            row = db.execute('SELECT * FROM events WHERE id=?', (record_id,)).fetchone()
            attempts = row['attempts'] + (1 if code else 0)
            exhausted = permanent or attempts >= self.settings.max_attempts
            db.execute('UPDATE events SET estado=?,attempts=?,due=?,lease=0,motivo=? WHERE id=?',
                       ('error' if exhausted else 'pendiente', attempts, time.time() + delay, code, record_id))
            if code:
                self.record_error(db, record_id, row['stage'], code)

    def finish(self, record_id, classification, route_value, reason, draft, proposal):
        ranking = {None: -1, 'basura': 0, 'tibio': 1, 'caliente': 2}
        with self.transaction() as db:
            row = db.execute('SELECT * FROM events WHERE id=?', (record_id,)).fetchone()
            lead = db.execute('SELECT * FROM leads WHERE id=?', (row['lead_id'],)).fetchone()
            effective_route = max((route_value, lead['route']), key=lambda r: ranking[r])
            if effective_route != route_value:
                draft, proposal = make_draft(json.loads(row['normalized']), effective_route, self.settings.agenda_url)
            db.execute('UPDATE leads SET route=? WHERE id=?', (effective_route, lead['id']))
            db.execute("UPDATE events SET stage='done',estado=?,ruta=?,motivo=?,classification=?,draft=?,proposal=?,lease=0,due=0 WHERE id=?",
                       ('duplicado' if row['duplicado_de'] else 'procesado', effective_route, reason,
                        encode(classification), draft, encode(proposal) if proposal else None, record_id))
            # Upgrade the existing task and previously processed duplicate events; never create another contact task.
            db.execute("UPDATE events SET ruta=? WHERE lead_id=? AND stage='done' AND estado IN ('procesado','duplicado')", (effective_route, lead['id']))
            if effective_route == 'caliente':
                self.enqueue(db, f'hot:{lead["id"]}', record_id, 'caliente',
                             f'Lead caliente {lead["id"]}. Recepcion: revisar el registro local y coordinar valoracion. Propuesta sin confirmar; validar disponibilidad y datos obligatorios.')

    def api_slot(self):
        now = time.time()
        with self.transaction() as db:
            next_at = db.execute('SELECT next_at FROM api_limit WHERE id=1').fetchone()[0]
            if next_at > now:
                return next_at - now
            db.execute('UPDATE api_limit SET next_at=? WHERE id=1', (now + self.settings.api_interval,))
        return 0

    def block_api(self, delay):
        with self.transaction() as db:
            db.execute('UPDATE api_limit SET next_at=MAX(next_at,?) WHERE id=1', (time.time() + delay,))

    def claim_notice(self):
        now = time.time()
        with self.transaction() as db:
            row = db.execute("SELECT * FROM outbox WHERE estado IN ('pendiente','intentando') AND due<=? AND lease<=? ORDER BY CASE kind WHEN 'caliente' THEN 0 ELSE 1 END,created_at LIMIT 1", (now, now)).fetchone()
            if row:
                db.execute("UPDATE outbox SET estado='intentando',lease=? WHERE id=?", (now + self.settings.lease_seconds, row['id']))
                return dict(row)

    def notice_sent(self, notice_id, external_id):
        with self.transaction() as db:
            db.execute("UPDATE outbox SET estado='enviado',sent_at=?,external_id=?,lease=0 WHERE id=?", (time.time(), str(external_id), notice_id))

    def notice_failed(self, notice, code, delay, permanent=False):
        with self.transaction() as db:
            attempts = notice['attempts'] + 1
            db.execute('UPDATE outbox SET estado=?,attempts=?,due=?,lease=0 WHERE id=?',
                       ('error' if permanent or attempts >= self.settings.max_attempts else 'pendiente', attempts, time.time() + delay, notice['id']))
            # Do not recursively notify failure to notify through the same broken channel.
            db.execute('INSERT INTO errors VALUES(?,?,?,?,?)', (uid(), notice['event_ref'], 'telegram', code, time.time()))

    def retry(self, event_id=None, receipt_id=None, notices=False):
        with self.transaction() as db:
            if notices:
                return db.execute("UPDATE outbox SET estado='pendiente',attempts=0,due=0,lease=0 WHERE estado='error'").rowcount
            row = db.execute('SELECT * FROM events WHERE event_id=? OR id=?', (event_id, receipt_id)).fetchone()
            if not row or row['estado'] != 'error':
                return 0
            return db.execute("UPDATE events SET estado='pendiente',attempts=0,due=0,lease=0,motivo=NULL WHERE id=?", (row['id'],)).rowcount

    @staticmethod
    def public(row):
        lead = json.loads(row['normalized'] or '{}')
        return {'event_id': row['event_id'], 'estado': row['estado'], 'ruta': row['ruta'],
                'lead_id': row['lead_id'], 'duplicado_de': row['duplicado_de'],
                'telefono_e164': lead.get('telefono_e164'), 'email': lead.get('email'),
                'motivo': row['motivo']}

    def status(self, event_id=None, since=None):
        with self.connect() as db:
            if event_id is not None:
                row = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
                return self.public(row) if row else None
            return [self.public(r) for r in db.execute('SELECT e.* FROM events e WHERE EXISTS(SELECT 1 FROM deliveries d WHERE d.event_ref=e.id AND d.received_at>=?) ORDER BY e.received_at,e.id', (since,))]

    def detail(self, event_id=None, receipt_id=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM events WHERE event_id=? OR id=?', (event_id, receipt_id)).fetchone()
            if not row:
                return None
            result = dict(row)
            result.pop('raw')  # Raw is available for operator review directly in SQLite, not printed by default.
            for key in ('normalized', 'enrichment', 'classification', 'proposal'):
                result[key] = json.loads(result[key]) if result[key] else None
            result['avisos'] = [dict(r) for r in db.execute('SELECT kind,estado,attempts,created_at,sent_at FROM outbox WHERE event_ref=?', (row['id'],))]
            return result

    def metrics(self):
        with self.connect() as db:
            states = {r[0]: r[1] for r in db.execute('SELECT estado,COUNT(*) FROM events GROUP BY estado')}
            oldest = db.execute("SELECT MIN(received_at) FROM events WHERE estado IN ('pendiente','procesando','error')").fetchone()[0]
            return {'eventos': states, 'entregas': db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0],
                    'leads': db.execute('SELECT COUNT(*) FROM leads').fetchone()[0],
                    'avisos_error': db.execute("SELECT COUNT(*) FROM outbox WHERE estado='error'").fetchone()[0],
                    'antiguedad_pendiente_segundos': round(time.time() - oldest, 1) if oldest else 0}

    def monitor(self):
        now = time.time()
        with self.transaction() as db:
            stale = db.execute("SELECT COUNT(*) FROM events WHERE estado IN ('pendiente','procesando') AND received_at<?", (now - self.settings.stale_seconds,)).fetchone()[0]
            late_notices = db.execute("SELECT COUNT(*) FROM outbox WHERE kind='caliente' AND estado!='enviado' AND created_at<?", (now - 240,)).fetchone()[0]
            if stale or late_notices:
                self.enqueue(db, f'backlog:{int(now // 300)}', None, 'error',
                             f'Revisar flujo: {stale} eventos demorados y {late_notices} avisos prioritarios cerca/fuera del plazo de cinco minutos.')
            if self.settings.silence_seconds:
                last = db.execute('SELECT MAX(received_at) FROM deliveries').fetchone()[0]
                if last and now - last > self.settings.silence_seconds:
                    self.enqueue(db, f'silencio:{int(now // self.settings.silence_seconds)}', None, 'error',
                                 'Ausencia de entradas: revisar conector y campanas; no implica por si sola una falla.')
