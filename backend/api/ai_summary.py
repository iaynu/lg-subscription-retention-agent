"""Team Insight 의 '3줄 요약'을 LLM 이 해석해서 다시 쓰게 한다. (숫자는 DB 에서 계산한 값 그대로)

안전장치
  1) LLM 에는 이미 계산된 사실(규칙 문장·리포트 본문)만 준다.
  2) 결과 속 숫자가 사실에 없던 숫자면 폐기(1회 재시도 후 규칙 문장으로 대체).
  3) 키 없음·오류·시간 초과도 규칙 문장으로 대체한다. 화면에는 'AI 해석' 표시로 구분한다.
  4) 같은 사실이면 결과를 .ai_summary_cache.json 에 저장해 다시 부르지 않는다.
미리 만들어 두기:  python -m api.ai_summary
"""
import hashlib
import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout

from config import ROOT, settings

CACHE_FILE = ROOT / '.ai_summary_cache.json'
PROMPT_VERSION = 'v1'
MAX_LINE = 130

SYSTEM = """당신은 구독 서비스 데이터 분석가입니다. 아래 '사실'만 근거로 핵심 요약 3줄을 씁니다.
규칙:
1. 사실에 있는 숫자만 그대로 쓰세요. 새로 계산한 숫자(차이, 합계, 비율 변환)나 추정 숫자는 절대 쓰지 마세요.
2. 원인을 단정하지 마세요. '때문에' 대신 '~로 나타나요', '~가 눈에 띄어요', '~해 볼 만해요'처럼 관찰·제안으로 쓰세요.
3. 1·2번째 줄은 가장 중요한 발견 + 의미, 3번째 줄은 실행 제안 한 줄로 쓰세요.
4. 한 줄은 120자 이내, 쉬운 존댓말(~요)로 쓰세요. 핵심 숫자 1~2개는 **로 감싸세요.
5. JSON 으로만 답하세요: {"insights": ["...", "...", "..."]}"""

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['insights'],
          'properties': {'insights': {'type': 'array', 'minItems': 3, 'maxItems': 3, 'items': {'type': 'string'}}}}

_NUM = re.compile(r'\d[\d,]*(?:\.\d+)?')


def _nums(text):
    out = set()
    for m in _NUM.findall(str(text).replace('**', '')):
        out.add(m.replace(',', '').rstrip('.'))
    return out


def validate(lines, facts):
    """오류 문구 목록(비어 있으면 통과)."""
    errs = []
    if not isinstance(lines, list) or len(lines) != 3 or not all(isinstance(x, str) and x.strip() for x in lines):
        return ['3줄이 아님']
    allowed = _nums(facts)
    for i, ln in enumerate(lines, 1):
        if len(ln.replace('**', '')) > MAX_LINE:
            errs.append(f'{i}번째 줄이 너무 김')
        if ln.count('**') % 2:
            errs.append(f'{i}번째 줄 강조 표시 짝이 안 맞음')
        if '때문' in ln:
            errs.append(f'{i}번째 줄 원인 단정 표현')
        for n in _nums(ln):
            if n not in allowed and not (n.isdigit() and 1 <= int(n) <= 5):
                errs.append(f'{i}번째 줄 숫자 {n} 는 사실에 없음')
    return errs


def facts_for(block):
    """한 부서 블록의 사실 문장: 규칙 3줄 + 리포트 본문."""
    parts = [str(x).replace('**', '') for x in block.get('ins', [])]
    for r in block.get('rpt') or []:
        parts.append(f'[{r[0]}] {r[3]}')
    for sec in block.get('secs') or []:
        parts.append(f'[{sec[0]}] {sec[1]}')
    return '\n'.join(parts)


def _key(facts):
    return hashlib.sha1(f'{PROMPT_VERSION}|{settings.SUMMARY_MODEL}|{facts}'.encode('utf-8')).hexdigest()


def _cache_load():
    try:
        return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _cache_save(c):
    try:
        CACHE_FILE.write_text(json.dumps(c, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


def enabled():
    return (settings.SUMMARY_MODE != 'off' and settings.LLM_MODE != 'template' and bool(settings.OPENAI_API_KEY))


def _call(facts, note=''):
    user = '사실:\n' + facts + (f'\n\n[이전 시도 문제] {note}\n문제를 고쳐서 다시 쓰세요.' if note else '')
    body = {'model': settings.SUMMARY_MODEL,
            'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}],
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'summary', 'strict': True, 'schema': SCHEMA}}}
    req = urllib.request.Request(settings.OPENAI_BASE_URL + '/chat/completions', data=json.dumps(body).encode('utf-8'),
                                 method='POST', headers={'Content-Type': 'application/json',
                                                         'Authorization': f'Bearer {settings.OPENAI_API_KEY}'})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'OpenAI HTTP {e.code}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'OpenAI 연결 실패: {e.reason}')
    msg = ((data.get('choices') or [{}])[0].get('message')) or {}
    if msg.get('refusal'):
        raise RuntimeError('model refusal')
    return json.loads(msg.get('content') or '{}').get('insights')


def summarize(facts):
    """(줄 3개 | None, 메모). 검증 실패 시 1회 재시도."""
    note = ''
    for attempt in range(2):
        try:
            lines = _call(facts, note)
        except Exception as e:
            return None, f'호출 실패: {e}'
        errs = validate(lines, facts)
        if not errs:
            return [x.strip() for x in lines], 'ok' if attempt == 0 else 'retry_ok'
        note = ' / '.join(errs[:3])
    return None, '검증 실패: ' + note


def enhance(team, wait_s=25):
    """team(reports.build 결과)의 각 부서 ins 를 AI 해석으로 바꾼다. 실패한 블록은 규칙 문장 유지."""
    blocks = [b for k in ('dept', 'rdept') for b in (team.get(k) or [])]
    cache = _cache_load()
    todo = []
    for b in blocks:
        b['ai'] = False
        facts = facts_for(b)
        k = _key(facts)
        hit = cache.get(k)
        if hit and not validate(hit, facts):
            b['ins_tpl'], b['ins'], b['ai'] = b['ins'], hit, True
        elif enabled():
            todo.append((b, facts, k))
    if todo:
        ex = ThreadPoolExecutor(max_workers=len(todo))
        futs = [(b, facts, k, ex.submit(summarize, facts)) for b, facts, k in todo]
        try:
            for b, facts, k, f in futs:
                try:
                    lines, memo = f.result(timeout=wait_s)
                except (FutTimeout, Exception):
                    continue
                if lines:
                    b['ins_tpl'], b['ins'], b['ai'] = b['ins'], lines, True
                    cache[k] = lines
        finally:
            ex.shutdown(wait=False)
        _cache_save(cache)
    return team


if __name__ == '__main__':      # 발표 전에 미리 만들어 두기
    from api import reports
    from db.conn import session
    with session() as s:
        as_of = s.scalar('SELECT MAX(as_of_date) FROM churn_prediction') or '2026-09-30'
        team = reports.build(s, as_of)
    if not enabled():
        raise SystemExit('OPENAI_API_KEY 가 없거나 SUMMARY_MODE=off 예요')
    enhance(team, wait_s=90)
    for b in team['dept'] + team['rdept']:
        print('AI ' if b['ai'] else '규칙', '|', ' / '.join(x.replace('**', '') for x in b['ins'])[:150])
