"""설정. 값은 환경변수 또는 프로젝트 루트의 .env 파일에서 읽는다(.env.example 참고)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_dotenv():
    p = ROOT / '.env'
    if not p.exists():
        return
    for line in p.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class Settings:
    # --- DB ---
    # PostgreSQL 예: postgresql://postgres:비밀번호@localhost:5432/churn
    # .env 가 없으면 파일 DB(sqlite)로 바로 시작할 수 있다.
    DATABASE_URL = os.environ.get('DATABASE_URL') or f"sqlite:///{ROOT / 'churn.db'}"

    # --- LLM ---
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
    # 비워 두면 model/config.yaml 의 llm.model 을 쓴다(모델명은 거기 한 곳에서 관리). 값을 넣으면 그 값이 우선.
    OPENAI_MODEL = os.environ.get('OPENAI_MODEL', '').strip()
    OPENAI_BASE_URL = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    # auto: 키가 있으면 OpenAI, 없으면 템플릿 / openai: 키 필수 / template: LLM 안 씀
    LLM_MODE = os.environ.get('LLM_MODE', 'auto')
    LLM_TIMEOUT_SEC = _int('LLM_TIMEOUT_SEC', 40)

    # --- 예전(v1) 엔진 전용 수치 ---
    # 현재 조치 엔진(model/)은 이 값들을 쓰지 않는다. 규칙 수치(쿨다운·비용 상한·최대 조치 수 등)는 model/config.yaml 의 rules 에서 바꾼다.
    # agent_v1 의 예전 코드가 import 할 때만 필요해서 남겨 둔다(.env 로 바꿔도 조치 결과에는 영향 없음).
    COOLDOWN_DAYS = _int('COOLDOWN_DAYS', 14)
    COST_CAP = _int('COST_CAP', 50000)
    MAX_ACTIONS = _int('MAX_ACTIONS', 3)
    MAX_CUSTOMER_MSGS = _int('MAX_CUSTOMER_MSGS', 2)
    TYPE_SHARE_MIN = _float('TYPE_SHARE_MIN', 0.20)
    MIN_RISK = os.environ.get('MIN_RISK', 'MID')
    SHORTLIST_PER_TYPE = _int('SHORTLIST_PER_TYPE', 3)

    # --- Team Insight AI 해석 요약 (조치 문구 모델과 별개) ---
    SUMMARY_MODEL = os.environ.get('SUMMARY_MODEL', 'gpt-5.4-mini')
    SUMMARY_MODE = os.environ.get('SUMMARY_MODE', 'auto')   # auto: 키 있으면 AI 해석 / off: 항상 규칙 템플릿
    MESSAGE_MAX_CHARS = _int('MESSAGE_MAX_CHARS', 600)

    # --- API ---
    ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
        'ALLOWED_ORIGINS', 'http://localhost:5173,http://localhost:3000,http://127.0.0.1:5500,http://localhost:8501,null').split(',') if o.strip()]


settings = Settings()
