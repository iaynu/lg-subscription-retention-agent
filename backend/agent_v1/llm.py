"""OpenAI Chat Completions REST 호출 (표준 라이브러리 urllib 만 사용 — 별도 패키지 설치 불필요)."""
import json
import urllib.error
import urllib.request

from config import settings


class LLMError(Exception):
    pass


def llm_enabled():
    m = settings.LLM_MODE
    if m == 'template':
        return False
    if m == 'openai':
        return True
    return bool(settings.OPENAI_API_KEY)  # auto


def chat_json(system, user, model=None):
    """JSON 객체를 돌려주는 호출. 실패하면 LLMError."""
    if not settings.OPENAI_API_KEY:
        raise LLMError('OPENAI_API_KEY 가 없어요(.env 확인)')
    body = {
        'model': model or settings.OPENAI_MODEL,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'response_format': {'type': 'json_object'},
        'temperature': 0.4,
    }
    req = urllib.request.Request(
        settings.OPENAI_BASE_URL + '/chat/completions', data=json.dumps(body).encode('utf-8'), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {settings.OPENAI_API_KEY}'})
    try:
        with urllib.request.urlopen(req, timeout=settings.LLM_TIMEOUT_SEC) as r:
            data = json.loads(r.read().decode('utf-8'))
        text = data['choices'][0]['message']['content']
        return json.loads(text)
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'ignore')[:300]
        raise LLMError(f'OpenAI HTTP {e.code}: {detail}')
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMError(f'OpenAI 연결 실패: {e}')
    except (KeyError, ValueError) as e:
        raise LLMError(f'OpenAI 응답 해석 실패: {e}')
