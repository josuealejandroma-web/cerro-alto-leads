"""Configuration. Importing this module does not read secrets or make requests."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path=None):
    path = Path(path or ROOT / '.env')
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


@dataclass
class Settings:
    db_path: str = str(ROOT / 'data' / 'leads.sqlite3')
    host: str = '127.0.0.1'
    port: int = 8080
    patients_url: str = ''
    patients_token: str = ''
    llm_url: str = ''
    llm_key: str = ''
    llm_model: str = ''
    llm_format: str = 'json_schema'
    telegram_token: str = ''
    telegram_chat: str = ''
    telegram_url: str = 'https://api.telegram.org'
    agenda_url: str = ''
    query_token: str = ''
    default_region: str = 'EC'
    workers: int = 3
    api_interval: float = 2.1
    timeout: float = 8
    max_attempts: int = 4
    retry_base: float = 5
    lease_seconds: float = 45
    stale_seconds: float = 120
    silence_seconds: float = 0
    mode: str = 'live'

    @classmethod
    def from_env(cls):
        load_env()
        names = {
            'db_path': 'DB_PATH', 'host': 'HOST', 'port': 'PORT',
            'patients_url': 'PATIENTS_API_BASE_URL', 'patients_token': 'PATIENTS_API_TOKEN',
            'llm_url': 'LLM_BASE_URL', 'llm_key': 'LLM_API_KEY', 'llm_model': 'LLM_MODEL',
            'llm_format': 'LLM_RESPONSE_FORMAT', 'telegram_token': 'TELEGRAM_BOT_TOKEN',
            'telegram_chat': 'TELEGRAM_CHAT_ID', 'telegram_url': 'TELEGRAM_API_BASE_URL',
            'agenda_url': 'AGENDA_URL', 'query_token': 'QUERY_TOKEN',
            'default_region': 'DEFAULT_PHONE_REGION', 'workers': 'WORKERS',
            'api_interval': 'PATIENTS_MIN_INTERVAL', 'timeout': 'HTTP_TIMEOUT',
            'max_attempts': 'MAX_ATTEMPTS', 'retry_base': 'RETRY_BASE_SECONDS',
            'lease_seconds': 'LEASE_SECONDS', 'stale_seconds': 'STALE_SECONDS',
            'silence_seconds': 'SILENCE_SECONDS', 'mode': 'APP_MODE',
        }
        defaults = cls()
        values = {}
        for field, name in names.items():
            default = getattr(defaults, field)
            value = os.getenv(name, str(default))
            values[field] = type(default)(value)
        result = cls(**values)
        if result.mode not in ('live', 'test'):
            raise ValueError('APP_MODE debe ser live o test')
        if result.workers < 1 or result.timeout <= 0 or result.lease_seconds <= result.timeout + 5:
            raise ValueError('Configuracion de workers, timeout o lease invalida')
        return result
