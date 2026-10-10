"""Agent 출력 검증기 v3. 규칙 번호는 agent_prompt_design.md §3과 1:1.

v2: title((광고) 표기)·internal_memo·순수 내부 전달 항목, fee_impact=감소, M4/M6 적용 범위.
v3(2026-10-08 점검 반영):
- 고객에게 가는 글(제목 포함)을 정규화(NFKC, 보이지 않는 문자 제거)한 뒤 검사. 보이지 않는 문자가 있으면 LANG.
- 제목에도 숫자·금지 표현·내부 정보·식별자·혜택 검사를 똑같이 적용.
- 숫자는 단위까지 맞춰 허용: 구독 속성, 고객 공개 값(message_facts), 그 후보의 문구에 있는 숫자만. 한글 숫자(삼만 원, 오천 원) 포함.
- 보고서 형식(▶ 항목) 지원: 본문 400자 이내, ▶ 항목 4줄 이내.
- 압박·보장 표현, 이탈·위험 예측 표현, 혜택 표현(동의어·영문 포함), URL·영문 문장·시스템 용어 검사 확대.
- 형식이 깨진 출력(타입 오류)은 예외 대신 SCHEMA 실패로 기록.
"""
from __future__ import annotations
import json, re, unicodedata, datetime as dt
from ..facts import allowed_pairs_from_message_facts

HARD, SOFT = "hard", "soft"
MAX_MESSAGE_CHARS = 400
MAX_TITLE_CHARS = 40
MAX_BULLETS = 4

INVISIBLE_RE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def clean(s) -> str:
    return INVISIBLE_RE.sub("", unicodedata.normalize("NFKC", s if isinstance(s, str) else ""))


def nospace(s) -> str:
    return re.sub(r"\s+", "", clean(s)).lower()


# ---------- 표현 목록 (공백을 없앤 소문자 문장에 정규식으로 적용) ----------
PRESSURE = [r"무조건", r"100(%|퍼센트|프로)", r"백(퍼센트|프로)", r"반드시해결", r"지금아니면", r"마지막기회", r"오늘만",
            r"놓치면", r"후회", r"서두르|서둘러", r"마감임박", r"선착순", r"보장", r"절대", r"손해", r"해지하지마",
            r"해지할수없", r"해지불가", r"(않으면|안하면|안되면).{0,10}(사라|없어|끝나|손해)"]
INTERNAL_WORDS = [r"이탈", r"위험점수", r"위험등급", r"위험도", r"고위험", r"해지위험", r"해지가능성", r"해지확률",
                  r"해지를막", r"해지방어", r"해지를방지", r"해지방지", r"예측", r"리스크", r"churn", r"shap", r"기여도"]
BENEFIT_GROUPS = [["무료", "무상", "공짜", "free"], ["할인", "감면", "discount"],
                  ["캐시백", "페이백", "환급", "돌려드", "cashback", "refund"],
                  ["포인트", "적립", "마일리지", "리워드", "point", "reward"],
                  ["쿠폰", "상품권", "기프트", "coupon", "voucher"], ["사은품", "증정", "경품", "gift"],
                  ["면제"], ["무이자"]]
CONFIRM_PAY = ["지급됩니다", "지급해드립니다", "지급해 드립니다", "지급받으실", "자동으로적용", "지급완료", "적용완료"]
PROMISE = ["반드시방문", "당일", "내일방문", "교환이확정", "교환해드립니다", "즉시처리"]

ID_RE = re.compile(r"(?i)(?<![a-z0-9])(s-?t\d{2}(?:-[a-z])?|s-?nt(?:-[a-z])?|t\d{2}(?!\d)|s\d{6}|c\d{6})")
SYS_RE = re.compile(r"(?i)candidates?|json|프롬프트|system\s*prompt|시스템\s*(지시|명령)|이전\s*(규칙|지시)|규칙을\s*무시|instruction|assistant")
CONTACT_RE = re.compile(r"\d{2,4}[-.\s]?[\d*]{3,4}[-.\s][\d*]{4}|(?<!\d)\d{4}-\d{4}(?!\d)|[\w.+-]+@[\w-]+\.[\w.]+")
URL_RE = re.compile(r"(?i)https?://|www\.|(?<![a-z0-9.])[a-z0-9-]+\.(com|net|org|io|kr|me|ly)(?![a-z0-9])")
FOREIGN_RE = re.compile(r"[一-鿿぀-ヿЀ-ӿ]")
ENGLISH_RUN_RE = re.compile(r"(?:\b[A-Za-z]{2,}\b[\s,.'!?:;-]+){3,}\b[A-Za-z]{2,}\b")


def _hits(patterns, text):
    ns = nospace(text)
    return [p for p in patterns if re.search(p, ns)]


def _benefit_terms(text):
    ns, low = nospace(text), clean(text).lower()
    found = set()
    for gi, g in enumerate(BENEFIT_GROUPS):
        for term in g:
            hit = re.search(rf"\b{term}s?\b", low) if term.isascii() else term in ns
            if hit: found.add((gi, term))
    return found


# ---------- 숫자 추출(값, 단위) ----------
UNIT_TABLE = [("개월", "month_count"), ("퍼센트", "pct"), ("프로", "pct"), ("%", "pct"), ("시간", "hour"), ("차례", "count"),
              ("가지", "kind"), ("달", "month_count"), ("원", "won"), ("월", "month"), ("일", "day"), ("년", "year"),
              ("주", "week"), ("번", "count"), ("회", "count"), ("건", "count"), ("개", "count"), ("명", "person"),
              ("분", "minute"), ("시", "clock"), ("배", "times"), ("세", "age"), ("탭", "tap"), ("층", "floor"),
              ("P", "point"), ("p", "point"), ("점", "score")]
SINO_D = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
SINO_U = {"십": 10, "백": 100, "천": 1000}
NATIVE = {"두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "스무": 20}


def _unit_at(t, pos):
    p = pos
    while p < len(t) and t[p] == " ": p += 1
    for u, cls in UNIT_TABLE:
        if t.startswith(u, p): return cls
    return ""


def _sino(s):
    total = section = num = 0
    for ch in s:
        if ch in SINO_D: num = SINO_D[ch]
        elif ch in SINO_U: section += (num or 1) * SINO_U[ch]; num = 0
        elif ch == "만": total += ((section + num) or 1) * 10000; section = num = 0
    return total + section + num


def _num(x):
    v = float(x)
    return int(v) if v == int(v) else v


def number_tokens(text, strip_tokens=()):
    """텍스트 속 숫자를 (값, 단위) 목록으로. 날짜는 (년, 'year')·(월, 'month')·(일, 'day')로 쪼갠다."""
    t = clean(text)
    for tok in sorted((x for x in strip_tokens if x), key=len, reverse=True):
        t = t.replace(tok, " " * len(tok))
    out = []

    def scan(pattern, to_vals):
        nonlocal t
        res, last = [], 0
        for m in re.finditer(pattern, t):
            vals = to_vals(m)
            if vals is None: continue
            unit = _unit_at(t, m.end())
            for v, u in vals: out.append((v, u if u is not None else unit))
            res.append(t[last:m.start()]); res.append(" " * (m.end() - m.start())); last = m.end()
        res.append(t[last:]); t = "".join(res)

    t = re.sub(r"(?<!\d)1\s*:\s*1(?!\d)", "   ", t)                                   # '1:1 상담'은 숫자로 보지 않음
    scan(r"(\d{4})\s*(?:년\s*|[.\-/]\s*)(\d{1,2})\s*(?:월\s*|[.\-/]\s*)(\d{1,2})(?:\s*일)?",
         lambda m: [(int(m.group(1)), "year"), (int(m.group(2)), "month"), (int(m.group(3)), "day")])
    scan(r"(?<![\d.])(\d{1,2})/(\d{1,2})(?![\d/])", lambda m: [(int(m.group(1)), "month"), (int(m.group(2)), "day")])
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)

    def amt(m):
        a = float(m.group(1)) * 10000
        if m.group(2): a += int(m.group(2)) * 1000
        if m.group(3): a += int(m.group(3))
        return [(_num(a), None)]
    scan(r"(\d+(?:\.\d+)?)\s*만\s*(?:(\d+)\s*천)?\s*(?:(\d+)(?=\s*원))?", amt)
    scan(r"(\d+)\s*천(?=\s*원)", lambda m: [(int(m.group(1)) * 1000, None)])
    scan(r"(?<![가-힣])([일이삼사오육칠팔구십백천만]*[십백천만][일이삼사오육칠팔구십백천만]*)(?=\s*(?:원|개월|퍼센트|프로|%|번|회|건|차례|명|배|년|주|시간))",
         lambda m: [(_sino(m.group(1)), None)])
    scan(r"(?<![가-힣])(두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무)(?=\s*(?:번|건|회|개|차례|달|주|가지|명|시간|분))",
         lambda m: [(NATIVE[m.group(1)], None)])
    scan(r"\d+(?:\.\d+)?", lambda m: [(_num(m.group(0)), None)])
    return out


def extract_numbers(text, strip_tokens=()):
    """값만(단위 무시). 상담자용 글(선택 이유·요약·내부 전달서) 검사에 쓴다."""
    return [v for v, _ in number_tokens(text, strip_tokens)]


def numbers_in_value(v):
    if isinstance(v, bool): return []
    if isinstance(v, (int, float)): return [v]
    if isinstance(v, str): return extract_numbers(v)
    return []


def _date(s):
    try: return dt.date.fromisoformat(str(s)[:10])
    except Exception: return None


def date_parts(s):
    d = _date(s)
    return [d.month, d.day, d.year] if d else []


def flatten(o, out=None):
    out = [] if out is None else out
    if isinstance(o, dict):
        for v in o.values(): flatten(v, out)
    elif isinstance(o, list):
        for v in o: flatten(v, out)
    else: out.append(o)
    return out


def pct_variants(x):
    return [round(x * 100), round(x * 100, 1)]


def cand_text(c):
    return " ".join(str(c.get(f) or "") for f in ("name", "detail", "message_hint", "customer_title", "buttons"))


def allowed_numbers(inp, cand_ids, internal):
    """값만 비교하는 허용 집합(상담자용 글). internal=True면 내부 분석값까지 포함."""
    sub, pred = inp["subscription"], inp.get("prediction", {})
    al = set()
    for k in ("monthly_fee", "contract_months", "days_to_end"):
        if isinstance(sub.get(k), (int, float)): al.add(sub[k])
    for k in ("end_date", "care_renewal_date", "promo_end_date"):
        if sub.get(k): al.update(date_parts(sub[k]))
    for v, u in allowed_pairs_from_message_facts(inp.get("message_facts")): al.add(v)
    for c in inp["candidates"]:
        if c["solution_id"] in cand_ids:
            al.update(extract_numbers(cand_text(c), (c["solution_id"],)))
    if internal:
        for t in inp.get("types", []):
            al.add(t["type_rank"]); al.update(extract_numbers(t["type_id"]))
            al.update(pct_variants(t["contribution_share"]))
            for f in t.get("top_features", []):
                al.update(numbers_in_value(f["value"])); al.update(extract_numbers(f.get("label", "")))
                al.add(round(f.get("shap", 0), 2))
        al.update(x for x in flatten(inp.get("context", {})) if isinstance(x, (int, float)) and not isinstance(x, bool))
        al.update(x for x in flatten(inp.get("facts", {})) if isinstance(x, (int, float)) and not isinstance(x, bool))
        if "churn_probability" in pred: al.update(pct_variants(pred["churn_probability"]))
        if "risk_rank_pct" in pred: al.update(pct_variants(pred["risk_rank_pct"])); al.add(pred["risk_rank_pct"])
        al.update(inp["constraints"].get("config_numbers", []))
        al.update(range(1, len(inp["candidates"]) + 1)); al.add(inp["constraints"]["max_actions"])
    return al


def customer_pairs(inp, c):
    """고객 문구에서 허용하는 (값, 단위). '*'는 단위 무관(후보 문구에 단위 없이 나온 숫자)."""
    sub = inp["subscription"]
    pairs = set()
    if isinstance(sub.get("monthly_fee"), (int, float)): pairs.add((sub["monthly_fee"], "won"))
    cm = sub.get("contract_months")
    if isinstance(cm, int):
        pairs.add((cm, "month_count"))
        if cm % 12 == 0: pairs.add((cm // 12, "year"))
    if isinstance(sub.get("days_to_end"), (int, float)): pairs.add((sub["days_to_end"], "day"))
    for k in ("end_date", "care_renewal_date", "promo_end_date"):
        d = _date(sub.get(k)) if sub.get(k) else None
        if d: pairs |= {(d.year, "year"), (d.month, "month"), (d.day, "day")}
    pairs |= allowed_pairs_from_message_facts(inp.get("message_facts"))
    for v, u in number_tokens(cand_text(c), (c["solution_id"],)):
        pairs.add((v, u or "*"))
    return pairs


def _num_ok(n, al): return any(abs(n - a) < 1e-9 for a in al)


def _pair_ok(v, u, pairs):
    for a, b in pairs:
        if abs(v - a) < 1e-9 and (b == u or b == "*" or u == ""): return True
    return u == "kind" and v <= 4                                    # '두 가지 방법' 같은 일반 열거


def reason_numbers(inp, s, c):
    """selection_reason(E3): evidence 값 + 그 피처의 label 안 숫자(기간) + 구독 고정 속성 + 후보 문구 + 유형 번호"""
    al = allowed_numbers(inp, {c["solution_id"]}, internal=False)
    labels = {f["feature"]: f.get("label", "") for t in inp.get("types", []) for f in t.get("top_features", [])}
    for e in s.get("evidence", []) if isinstance(s.get("evidence"), list) else []:
        if not isinstance(e, dict): continue
        al.update(numbers_in_value(e.get("value")))
        if isinstance(e.get("value"), (int, float)) and not isinstance(e.get("value"), bool) and 0 < e["value"] < 1: al.update(pct_variants(e["value"]))
        al.update(extract_numbers(labels.get(e.get("feature"), "")))
    for t in inp.get("types", []):
        if t["type_id"] == s.get("type_id"): al.update(pct_variants(t["contribution_share"])); al.add(t["type_rank"])
    al.update(inp["constraints"].get("config_numbers", [])); al.add(1)
    return al


_DATE_RES = (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$"), re.compile(r"^(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?$"),
             re.compile(r"^(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?$"))
_NUM_TOKEN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _norm_value(x):
    """evidence 값 비교용 정규화: 숫자('45,600원'·'48일 남음' → 숫자), 날짜('2027년 9월 29일'·'2027.09.29' → date). 그 밖은 문자열 그대로."""
    if isinstance(x, bool): return ("s", x)
    if isinstance(x, (int, float)): return ("n", float(x))
    if isinstance(x, str):
        t = x.strip()
        for rx in _DATE_RES:
            m = rx.match(t)
            if m:
                try: return ("d", dt.date(int(m[1]), int(m[2]), int(m[3])))
                except ValueError: break
        toks = _NUM_TOKEN.findall(t)
        if len(toks) == 1 and not re.search(r"[A-Za-z]", _NUM_TOKEN.sub("", t)) and not re.search(r"\d", _NUM_TOKEN.sub("", t)):
            try: return ("n", float(toks[0].replace(",", "")))
            except ValueError: pass
        return ("s", t)
    return ("s", x)


def _same_value(a, b):
    na, nb = _norm_value(a), _norm_value(b)
    if na[0] == nb[0] == "n": return abs(na[1] - nb[1]) < 1e-6
    return na == nb


# ---------- 문장 ----------
def sentences(t): return [s for s in re.split(r"(?<=[.!?])\s+", (t or "").strip()) if s]
def polite(s): return bool(re.search(r"(니다|요|세요|까요|십시오)[.!?]?\s*$", s.strip()))


# ---------- 메인 ----------
def parse(raw):
    s = (raw or "").strip(); repaired = []
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s); repaired.append("R5_FENCE_STRIPPED")
    if not s.startswith("{"):
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j < 0: return None, ["R5"], repaired
        s = s[i:j + 1]; repaired.append("R5_TEXT_STRIPPED")
    try: return json.loads(s), [], repaired
    except Exception: return None, ["R5"], repaired


def _item_schema_errors(s):
    if not isinstance(s, dict): return ["항목이 객체가 아님"]
    errs = []
    if not isinstance(s.get("priority"), int) or isinstance(s.get("priority"), bool): errs.append("priority는 정수")
    for k in ("solution_id", "type_id", "selection_reason", "message_text"):
        if not isinstance(s.get(k), str): errs.append(f"{k}는 문자열(누락 포함)")
    for k in ("title", "internal_memo"):
        if k in s and s[k] is not None and not isinstance(s[k], str): errs.append(f"{k}는 문자열")
    ev = s.get("evidence")
    if not isinstance(ev, list) or not all(isinstance(e, dict) and isinstance(e.get("feature"), str) for e in ev):
        errs.append("evidence는 {feature, value} 목록")
    return errs


def _customer_text_checks(text, field, i, add, inp, c, sid, extra_banned=()):
    """고객에게 가는 글(본문·제목) 공통 검사: 문자·식별자·연락처·URL·내부 표현·압박 표현·숫자·혜택."""
    if INVISIBLE_RE.search(text or ""): add("LANG", i, f"{field}: 보이지 않는 문자 포함")
    ct = clean(text)
    if FOREIGN_RE.search(ct): add("LANG", i, f"{field}: 한글·영숫자 외 문자 혼입")
    if ENGLISH_RUN_RE.search(ct): add("LANG", i, f"{field}: 영어 문장")
    if ID_RE.search(ct) or SYS_RE.search(ct): add("LEAK", i, f"{field}: 내부 식별자·시스템 용어")
    if CONTACT_RE.search(ct): add("LEAK", i, f"{field}: 연락처 형태 문자열")
    if URL_RE.search(ct): add("LEAK", i, f"{field}: 링크·도메인")
    for p in _hits(INTERNAL_WORDS, ct): add("M3", i, f"{field}: 내부 정보 표현({p})")
    for p in _hits(PRESSURE + [re.escape(nospace(b)) for b in extra_banned], ct): add("M7", i, f"{field}: 금지 표현({p})")
    pairs = customer_pairs(inp, c)
    internal = allowed_numbers(inp, {sid}, internal=True)
    for v, u in number_tokens(ct, (c["solution_id"], c["type_id"])):
        if _pair_ok(v, u, pairs): continue
        if _num_ok(v, internal): add("M3", i, f"{field}의 숫자 {v}{('(' + u + ')') if u else ''}: 내부 정보 수치 노출")
        else: add("R2", i, f"{field}의 숫자 {v}{('(' + u + ')') if u else ''}: 입력·고객 공개 값에 없음")
    have = {gi for gi, _ in _benefit_terms(cand_text(c))}
    bad = sorted({term for gi, term in _benefit_terms(ct) if gi not in have})
    if bad: add("M2", i, f"{field}: 후보 정보에 없는 혜택 표현 {bad}")


def validate(raw, inp):
    """반환: dict(ok, failures[(rule, idx, msg, severity)], repaired[], data)"""
    F = []
    def add(rule, idx, msg, sev=HARD): F.append((rule, idx, msg, sev))
    data, errs, repaired = parse(raw)
    if not isinstance(data, dict):
        return dict(ok=False, failures=[("R5", None, "JSON 파싱 실패", HARD)], repaired=repaired, data=None)
    cands = {c["solution_id"]: c for c in inp["candidates"]}
    cons = inp["constraints"]; mode = inp.get("mode", "ACTION")
    max_chars = cons.get("max_message_chars", MAX_MESSAGE_CHARS)
    sel = data.get("selected")
    if not isinstance(sel, list) or not isinstance(data.get("summary"), str):
        add("SCHEMA", None, "selected(list)/summary(str) 필수"); return dict(ok=False, failures=F, repaired=repaired, data=data)
    if len(sel) == 0: add("R1_EMPTY", None, "후보가 있는데 selected가 비어 있음")
    if len(sel) > cons["max_actions"]: add("R1_COUNT", None, f"선택 {len(sel)}개 > max_actions {cons['max_actions']}")
    good = []
    for i, s in enumerate(sel):
        e = _item_schema_errors(s)
        if e: add("SCHEMA", i, "; ".join(e))
        else: good.append((i, s))
    pr = [s["priority"] for _, s in good]
    if len(good) == len(sel) and sorted(pr) != list(range(1, len(sel) + 1)): add("SCHEMA", None, "priority가 1..n 연속이 아님")
    ids = [s.get("solution_id") if isinstance(s, dict) else None for s in sel]
    if len(set(map(str, ids))) != len(ids): add("R1_DUP", None, "solution_id 중복")
    feats = {}
    for t in inp.get("types", []):
        for f in t.get("top_features", []): feats[f["feature"]] = f["value"]
    for k, v in inp["subscription"].items(): feats.setdefault(k, v)
    if mode == "NO_TYPE":
        feats = {"risk_level": inp["prediction"]["risk_level"], "risk_rank_pct": inp["prediction"]["risk_rank_pct"]}
    cat_seen = set()
    for i, s in good:
        sid = s["solution_id"]
        if sid not in cands:
            add("R1", i, f"후보에 없는 solution_id: {sid}"); continue
        c = cands[sid]
        kind = c.get("delivery_kind", "고객 메시지")
        internal_only = kind == "내부 전달"
        ad_eff = (c.get("ad_yn") == "Y") if "ad_yn" in c else (c["category"] == "혜택제안")   # 구버전 호환: ad_yn 없으면 혜택제안=광고성
        if s["type_id"] != c["type_id"]: add("R1_TYPE", i, f"type_id가 후보와 다름({s['type_id']} ≠ {c['type_id']})")
        if c["category"] in cat_seen: add("R1_CAT", i, "같은 구분 중복 선택")
        cat_seen.add(c["category"])
        msg, rsn = s["message_text"], s["selection_reason"]
        title, memo = s.get("title") or "", s.get("internal_memo") or ""
        strip = [c["solution_id"], c["type_id"]]
        # --- 선택 이유(E1, E3)·근거(E2) ---
        al_r = reason_numbers(inp, s, c)
        for n in extract_numbers(rsn, strip):
            if not _num_ok(n, al_r): add("E3", i, f"selection_reason의 숫자 {n}: evidence 값·라벨·구독 속성과 일치하지 않음")
        if len(rsn) > 100 or not (1 <= len(sentences(rsn)) <= 2):
            add("E1", i, f"선택 이유 {len(rsn)}자/{len(sentences(rsn))}문장 (100자·2문장 이내)", HARD if len(rsn) > 140 else SOFT)
        for p in _hits(PRESSURE, rsn): add("M7", i, f"선택 이유: 금지 표현({p})")
        ev = s["evidence"]
        if not (1 <= len(ev) <= 3): add("E2", i, f"evidence {len(ev)}개 (1~3개여야 함)")
        for e in ev:
            f, v = e.get("feature"), e.get("value")
            if f not in feats: add("E2", i, f"evidence feature 입력에 없음: {f}")
            elif not _same_value(feats[f], v): add("E2", i, f"evidence 값 불일치: {f}={v} (입력 {feats[f]})")
        # --- 제목·내부 전달서 (T1, T2, D1, D2) ---
        if "ad_yn" in c and kind == "고객 메시지":
            if not title.strip(): add("T1", i, "title 누락")
            elif len(title) > MAX_TITLE_CHARS: add("T1", i, f"title {len(title)}자 > {MAX_TITLE_CHARS}자")
            if ad_eff and title and not title.startswith("(광고)"): add("T2", i, "광고성 메시지의 title이 (광고)로 시작하지 않음")
            if not ad_eff and "광고" in nospace(title): add("T2", i, "광고성이 아닌데 제목에 광고 표기")
        if internal_only and (msg.strip() or title.strip()): add("D1", i, "내부 전달 항목에 고객 메시지·제목이 있음(비워야 함)")
        if c.get("internal_forward_yn") == "Y":
            if not memo.strip(): add("D2", i, "internal_memo 누락(내부 전달 필요 솔루션)")
            else:
                if len(memo) > 300: add("D2", i, f"internal_memo {len(memo)}자 > 300자", SOFT)
                al_m = allowed_numbers(inp, {sid}, True)
                for n in extract_numbers(memo, strip):
                    if not _num_ok(n, al_m): add("E3", i, f"internal_memo의 숫자 {n}: 입력에 없음")
                if CONTACT_RE.search(clean(memo)): add("LEAK", i, "internal_memo에 연락처 형태 문자열")
        elif memo.strip(): add("D2", i, "내부 전달이 필요 없는 솔루션에 internal_memo가 있음", SOFT)
        if internal_only: continue
        # --- 고객에게 가는 글: 본문·제목 공통 검사 ---
        extra = cons.get("banned_phrases_extra", [])
        _customer_text_checks(msg, "고객 문구", i, add, inp, c, sid, extra)
        if title.strip(): _customer_text_checks(title, "제목", i, add, inp, c, sid, extra)
        text = clean(msg)
        # --- 길이·형식 (M1) ---
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        bullets = [l for l in lines if l.startswith("▶")]
        prose = " ".join(l for l in lines if not l.startswith("▶"))
        ss = sentences(prose)
        if len(msg) > max_chars: add("M1", i, f"고객 문구 {len(msg)}자 > {max_chars}자")
        if len(bullets) > MAX_BULLETS: add("M1_BULLET", i, f"▶ 항목 {len(bullets)}줄 > {MAX_BULLETS}줄", SOFT)
        if any(len(b) > 60 for b in bullets): add("M1_BULLET", i, "▶ 항목 한 줄이 60자 초과", SOFT)
        lo, hi = (1, 3) if bullets else (2, 4)
        if c.get("delivery_kind") != "상담·앱 안내" and not (lo <= len(ss) <= hi): add("M1_SENT", i, f"고객 문구 문장 {len(ss)}개({lo}~{hi})", SOFT)   # 상담·앱 안내는 발송하지 않는 상담 고지문이라 문장 수 검사 제외(팀 결정 2026-10-10)
        if not all(polite(x) for x in ss): add("M1_POLITE", i, "존댓말 종결이 아닌 문장", SOFT)
        # --- 구분별 필수 요소 (M4~M6, M8, N) ---
        cat, btn = c["category"], str(c.get("buttons") or "")
        ns = nospace(text)
        if cat == "정보제공" and not (re.search(r"앱|상담|확인", text) or btn): add("M8", i, "정보제공: 확인 방법(앱·상담·버튼) 없음")
        if cat == "담당자조치" and not re.search(r"담당자|매니저|상담사|기사|전담", text): add("M8", i, "담당자조치: 담당자 언급 없음")
        if cat == "혜택제안":
            if not (re.search(r"신청|말씀|의사|검토|문의|확인", text) or re.search(r"신청|받기|확인", btn)):
                add("M8", i, "혜택제안: 신청·확인 안내 없음")
            if ad_eff and any(nospace(p) in ns for p in CONFIRM_PAY): add("M6", i, "혜택을 확정 지급처럼 서술")
        if cat == "제품조치" and not (re.search(r"점검|교환|접수|신청|예약|변경|방문|선택", text) or btn):
            add("M8", i, "제품조치: 다음 행동(접수·신청·예약) 안내 없음")
        if cat == "의견수집" and not re.search(r"설문|의견", text): add("N2", i, "설문·의견 안내 없음")
        if any(nospace(p) in ns for p in PROMISE): add("M8", i, "일정·결과를 약속하는 표현")
        strict = (ad_eff and cat == "혜택제안") or c.get("fee_impact", "없음") != "없음"
        has_cost = re.search(r"총\s?비용|총\s?납부|위약금", text)
        if strict and not has_cost:
            add("M4", i, "혜택·요금 변동 안내에 총 비용/위약금 조건 안내 필요")
        elif re.search(r"요금|할인|감면|구독료", text) and not (has_cost or re.search(r"(앱|상담).{0,15}확인", text)):
            add("M4", i, "요금을 언급하면 총 비용·위약금 또는 확인 방법을 함께 안내")
        if c.get("fee_impact") == "증가":
            for pat, nm in [("구독료|요금", "구독료 변동"), ("달라질|증가|늘어", "변동 가능성"), (r"총\s?비용", "총 비용 확인"), ("기존", "기존 구독 유지")]:
                if not re.search(pat, text): add("M5", i, f"fee_impact=증가: '{nm}' 안내 없음")
        if c.get("fee_impact") == "감소":
            if not re.search(r"구독료|요금", text): add("M5", i, "fee_impact=감소: 구독료·요금 언급 없음")
            if not re.search(r"신청|조건", text): add("M5", i, "fee_impact=감소: 신청·조건 안내 없음")
        if mode == "NO_TYPE":
            if s["type_id"] != "유형없음": add("N1", i, "type_id가 유형없음이 아님")
            if re.search(r"불만|불편이\s?많|해지를\s?고민|이탈", text): add("N1", i, "불만·이탈 가능성을 단정")
            if not re.search(r"주시면|있었는지|있으셨는지|\?", text): add("N2", i, "열린·질문형 표현이 아님")
    # --- summary(상담자용) ---
    sm = data["summary"]
    for p in _hits(PRESSURE, sm): add("M7", None, f"summary 금지 표현({p})")
    al_s = set(allowed_numbers(inp, set(cands), internal=False))
    for _, s_ in good:
        if s_["solution_id"] in cands: al_s |= reason_numbers(inp, s_, cands[s_["solution_id"]])
    al_s.update(range(1, len(cands) + 1))
    for n in extract_numbers(sm, [c["solution_id"] for c in cands.values()] + [c["type_id"] for c in cands.values()]):
        if not _num_ok(n, al_s): add("E3", None, f"summary의 숫자 {n}: 입력에 없음")
    if len(sentences(sm)) > 2 or len(sm) > 150: add("E1_SUM", None, "summary 2문장·150자 초과", SOFT)
    hard = [f for f in F if f[3] == HARD]
    return dict(ok=not hard, failures=F, repaired=repaired, data=data)


def item_failures(res):
    out = {}
    for r, i, m, sev in res["failures"]:
        if sev == HARD: out.setdefault(i, []).append(r)
    return out
