"""Explicit demonstration with LOCAL SIMULATED integrations. No API keys or external traffic."""
import logging
import sys
from pathlib import Path
from app import make_server
from config import ROOT, Settings
from engine import Engine
from store import Store

sys.path.insert(0, str(Path(__file__).with_name('tests')))
from test_flow import FakeProvider


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    provider = FakeProvider()
    settings = Settings(db_path=str(ROOT / 'data' / 'demo.sqlite3'), port=8080,
        patients_url=provider.url, patients_token='simulado', llm_url=provider.url + '/v1',
        llm_key='simulado', llm_model='simulado', telegram_token='simulado', telegram_chat='simulado',
        telegram_url=provider.url, api_interval=.05, mode='test')
    store = Store(settings)
    server = make_server(settings, store)
    engine = Engine(settings, store)
    engine.start()
    print('DEMO SIMULADA en http://127.0.0.1:8080. API, LLM y Telegram son dobles locales.', flush=True)
    print('No envia avisos reales ni usa credenciales. Base separada: data/demo.sqlite3.', flush=True)
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        engine.stop()
        provider.close()


if __name__ == '__main__':
    main()
