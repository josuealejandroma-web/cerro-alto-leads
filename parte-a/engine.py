"""Independent event workers, notifications and watchdog; persistent retry state."""
import json
import logging
import threading
import time
from integrations import RateWait, StageError, classify, notify, patients_step
from rules import draft, route

LOG = logging.getLogger('cerro-alto')


class Engine:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.stop_event = threading.Event()
        self.threads = []
        self.emergency_lock = threading.Lock()
        self.last_emergency = -float('inf')

    def event_once(self):
        event = self.store.claim_event()
        if not event:
            return False
        try:
            lead = json.loads(event['normalized'])
            if event['stage'] == 'enrich':
                progress, done = patients_step(self.settings, self.store, lead, json.loads(event['enrichment']))
                self.store.advance(event['id'], 'classify' if done else 'enrich', enrichment=progress)
            else:
                classification = classify(self.settings, lead)
                route_value, reason = route(lead, classification, json.loads(event['enrichment']))
                message, proposal = draft(lead, route_value, self.settings.agenda_url)
                self.store.finish(event['id'], classification, route_value, reason, message, proposal)
        except RateWait as exc:
            self.store.defer(event['id'], exc.seconds)
        except StageError as exc:
            delay = exc.retry_after or min(120, self.settings.retry_base * 2 ** event['attempts'])
            self.store.defer(event['id'], delay, exc.code, exc.permanent)
            LOG.error('event_ref=%s stage=%s code=%s', event['id'], event['stage'], exc.code)
        except Exception:
            self.store.defer(event['id'], self.settings.retry_base, 'error_interno')
            LOG.error('event_ref=%s code=error_interno', event['id'])
        return True

    def notice_once(self):
        notice = self.store.claim_notice()
        if not notice:
            return False
        try:
            external_id = notify(self.settings, notice['text'])
            self.store.notice_sent(notice['id'], external_id)
        except StageError as exc:
            self.store.notice_failed(notice, exc.code, exc.retry_after or min(120, self.settings.retry_base * 2 ** notice['attempts']), exc.permanent)
            LOG.error('notice_ref=%s code=%s; revisar outbox con CLI', notice['id'], exc.code)
        except Exception:
            self.store.notice_failed(notice, 'error_interno', self.settings.retry_base)
            LOG.error('notice_ref=%s code=error_interno', notice['id'])
        return True

    def loop(self, operation, interval):
        while not self.stop_event.is_set():
            try:
                busy = operation()
            except Exception:
                # Includes database failures: the same database cannot record its own outage.
                # Emit only a fixed code and try the independent notification transport.
                LOG.critical('code=worker_o_almacenamiento_no_disponible')
                with self.emergency_lock:
                    should_notify = time.monotonic() - self.last_emergency >= 60
                    if should_notify:
                        self.last_emergency = time.monotonic()
                if should_notify:
                    try:
                        notify(self.settings, 'Fallo de worker/almacenamiento. Revisar el proceso local y espacio en disco; los eventos recibidos pueden estar demorados.')
                    except Exception:
                        LOG.critical('code=aviso_independiente_no_disponible')
                busy = False
            self.stop_event.wait(.02 if busy else interval)

    def start(self):
        operations = [(self.event_once, .2)] * self.settings.workers + [(self.notice_once, .2), (self.store.monitor, 10)]
        for operation, interval in operations:
            thread = threading.Thread(target=self.loop, args=(operation, interval), daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=self.settings.timeout + 2)
