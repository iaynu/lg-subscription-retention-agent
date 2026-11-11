"""설문 응답 합성기 (설계: survey_simulator_design.md §3). 가설 기반 synthetic data — 확률은 전부 config.yaml / survey_mapping.yaml 의 [가정] 시작값이다.

단계 A(응답 여부·중도 이탈), B(status), C(issues·top), D(form), E(deepA·deepB; deep.py), F(hesitate~newsol; stage_f.py), G(motive~open; stage_g.py, pool.py). 이후 문항은 아직 없어 `pending`에서 멈춘다.
문항마다 독립 난수(make_rng + 문항 id)를 써서, 나중에 문항을 추가해도 앞 문항의 합성 결과는 바뀌지 않는다.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from . import questionnaire as Q
from .mapping import load_config, load_mapping
from .rng import make_rng, pick

SURVEY_TYPES = ("post_action", "post_churn", "no_type", "no_candidate", "survey_only")
RISKS = ("HIGH", "MID", "LOW")
REQUIRED = frozenset(["status", "issues"])          # 시안에서 req:true — 건너뛸 수 없음


@dataclass
class Subject:
    """합성기 입력. 숨은 정답(type_strengths, hidden_topics)은 응답을 만들 때만 쓰고 모델·후보 생성에는 넘기지 않는다."""
    subscription_id: str
    survey_id: str
    survey_type: str
    risk_level: str = "MID"                                   # 위험 HIGH/MID/LOW (post_churn이면 쓰지 않음)
    type_strengths: dict = field(default_factory=dict)        # {"T06": 0.8, ...} synth_truth_type.activation_strength
    hidden_topics: tuple = ()                                 # ("H-A",) synth_truth_topic
    event_counts: dict = field(default_factory=dict)          # 불편 코드 -> 해당 이벤트 건수 (§3.4)
    repeat_codes: frozenset = frozenset()                     # 반복 플래그 Y인 불편 코드
    action_solution: str | None = None                        # 마지막 '조치완료' 솔루션 ID (없으면 None)
    hidden_strengths: dict = field(default_factory=dict)      # {"H-A": 0.7} 숨은 주제 강도 (저장용 synth_truth_topic; 합성에는 hidden_topics만 사용)
    facts: dict = field(default_factory=dict)                 # 관측 피처 값 (심화 문항용, 키는 deep.py 참고). 없으면 기본 분포

    def __post_init__(self):
        if self.survey_type not in SURVEY_TYPES: raise ValueError(f"survey_type 오류: {self.survey_type}")
        if self.risk_level not in RISKS: raise ValueError(f"risk_level 오류: {self.risk_level}")


@dataclass
class SurveyResult:
    subscription_id: str
    survey_id: str
    survey_type: str
    responded: bool
    answers: dict = field(default_factory=dict)          # 문항 id -> 코드 / 코드 목록 (long 형식으로 풀기 전)
    completed: bool | None = None                        # True 완료 / False 중도 이탈 / None 아직 모든 문항을 합성하지 못함(pending)
    dropped_at: str | None = None                        # 중도 이탈로 멈춘 문항
    pending: str | None = None                           # 아직 구현되지 않아 멈춘 문항
    other_text: dict = field(default_factory=dict)       # 문항 id -> 직접 입력 문구 (issues의 '기타' 문구, interview 연락처)
    truth: dict = field(default_factory=dict)            # 검증 전용: issues 선택 출처 등 (응답 데이터로 저장하지 않음)


class Ctx:
    def __init__(self, subject, cfg, mp, answers, qid):
        self.s, self.cfg, self.mp, self.a = subject, cfg, mp, answers
        self.rng = make_rng(subject.subscription_id, subject.survey_id, f"{cfg['seed_salt']}|{qid}")
        self.truth = {}
        self.other_text = {}


# ---------- 단계 A ----------
def draw_drop_index(rng, n: int) -> int:
    """중도 이탈 위치: 뒤쪽 문항일수록 많도록(밀도 ∝ 위치) 뽑는다. 반환값 i는 'ORDER[i] 이후 문항부터 응답하지 않음'. 1..n-1 (status는 항상 응답)."""
    return 1 + int(math.sqrt(rng.random()) * (n - 1))


def stage_a(subject, cfg):
    rng = make_rng(subject.subscription_id, subject.survey_id, f"{cfg['seed_salt']}|A")
    responded = rng.random() < cfg["response_rate"][subject.survey_type]
    dropout = rng.random() < cfg["dropout_rate"]
    return responded, (draw_drop_index(rng, len(Q.ORDER)) if dropout else None)


# ---------- 단계 B ----------
def gen_status(c: Ctx):
    if c.s.survey_type == "post_churn": return pick(c.rng, c.cfg["status_post_churn"])
    return pick(c.rng, c.cfg["status_given_before_churn"][c.s.risk_level])


# ---------- 단계 C ----------
def _active_types(c: Ctx) -> dict:
    return {t: v for t, v in c.s.type_strengths.items() if v >= c.cfg["active_strength_min"]}


def _source_weights(c: Ctx, has_active: bool, has_hidden: bool) -> dict:
    """설계 §3.3 출처 확률. 활성 유형이 없으면 자기 유형 몫을 숨은 주제(있으면)나 다른 기존 유형에 넘기고,
    숨은 주제가 있으면 그 몫을 최소 issue_source_hidden_boost로 올린 뒤 나머지 출처를 같은 비율로 줄이고,
    숨은 주제가 없으면 그 몫을 다른 기존 유형에 준다 [가정]. 합은 항상 1."""
    w = dict(c.cfg["issue_source"])
    if not has_active:
        w["hidden_topic" if has_hidden else "other_type"] += w["own_type"]; w["own_type"] = 0.0
    if has_hidden:
        if w["hidden_topic"] < c.cfg["issue_source_hidden_boost"]:
            rest = {k: v for k, v in w.items() if k != "hidden_topic"}
            tot = sum(rest.values()); room = 1 - c.cfg["issue_source_hidden_boost"]
            w = {k: (v / tot * room if k != "hidden_topic" else c.cfg["issue_source_hidden_boost"]) for k, v in w.items()} if tot > 0 else w
    else:                                                   # 숨은 주제가 없으면 그 몫(기본 20%)은 다른 기존 유형으로 [가정] — 자기 유형 비율(60%)을 건드리지 않기 위해
        w["other_type"] += w["hidden_topic"]; w["hidden_topic"] = 0.0
    return w


def _other_type_weights(c: Ctx, active: dict) -> dict:
    m = c.mp
    pairs = [set(p) for p in m["confusable_pairs"]]
    pool = [t for t in m["type_to_issue"] if t not in active] or list(m["type_to_issue"])
    return {t: (m["confusable_weight"] if any({t, a} == p for a in active for p in pairs) else 1) for t in pool}


def gen_issues(c: Ctx):
    s, cfg, mp, rng = c.s, c.cfg, c.mp, c.rng
    active = _active_types(c)
    hidden = tuple(s.hidden_topics)
    c.truth["issue_picks"] = []
    if not active and not hidden and rng.random() < cfg["issues_none_rate_no_signal"]:
        return ["none"]
    k = pick(rng, cfg["issues_k"])
    w = _source_weights(c, bool(active), bool(hidden))
    codes, own_strength = set(), {}
    for _ in range(int(k)):
        src = pick(rng, w)
        if src == "own_type":
            t = pick(rng, active); code = mp["type_to_issue"][t]
            own_strength[code] = max(own_strength.get(code, 0), active[t])
        elif src == "other_type":
            t = pick(rng, _other_type_weights(c, active)); code = mp["type_to_issue"][t]
        elif src == "hidden_topic":
            h = hidden[int(rng.random() * len(hidden))]
            cand = mp["hidden_topics"][h]["issue_codes"]; code = cand[int(rng.random() * len(cand))]
        else:
            code = "other"
        codes.add(code); c.truth["issue_picks"].append((src, code))
    c.truth["own_strength"] = own_strength
    out = [x for x in Q.ISSUES if x in codes]
    if "other" in out: c.other_text["issues"] = _pool.sample(rng, "issue_other")      # 기타를 고르면 짧은 직접 입력 문구
    return out


def gen_top(c: Ctx):
    opts = list(Q.real_issues(c.a))
    own = {k: v for k, v in c.truth_prev.get("own_strength", {}).items() if k in opts}
    if own:
        best = max(sorted(own), key=lambda k: own[k])
        rest = [o for o in opts if o != best]
        w = {best: c.cfg["top_own_strongest"], **{o: (1 - c.cfg["top_own_strongest"]) / len(rest) for o in rest}}
        return pick(c.rng, w)
    return opts[int(c.rng.random() * len(opts))]


# ---------- 단계 D ----------
def event_bucket(s: Subject, code: str) -> str:
    """해당 이벤트 건수 구간: 2건 이상(또는 반복 플래그) many / 1건 one / 그 외·idle·other none."""
    if code in ("idle", "other"): return "none"
    n = int(s.event_counts.get(code, 0))
    return "many" if (n >= 2 or code in s.repeat_codes) else ("one" if n == 1 else "none")


def gen_form(c: Ctx):
    return pick(c.rng, c.cfg["form_given_events"][event_bucket(c.s, Q.top_key(c.a))])


from . import deep as _deep, stage_f as _sf, stage_g as _sg, pool as _pool
STAGES = {"status": gen_status, "issues": gen_issues, "top": gen_top, "form": gen_form, "deepA": _deep.gen_deepA, "deepB": _deep.gen_deepB,
          "hesitate": _sf.gen_hesitate, "action": _sf.gen_action, "helped": _sf.gen_helped, "why": _sf.gen_why, "wanted": _sf.gen_wanted, "newsol": _sf.gen_newsol,
          "motive": _sg.gen_motive, "gap": _sg.gen_gap, "firstact": _sg.gen_firstact, "research": _sg.gen_research, "after": _sg.gen_after, "channel": _sg.gen_channel,
          "hh": _sg.gen_hh, "daytime": _sg.gen_daytime, "exp": _sg.gen_exp, "who": _sg.gen_who, "interview": _sg.gen_interview, "open": _sg.gen_open}


# ---------- 진행 ----------
def synthesize(subject: Subject, cfg: dict | None = None, mp: dict | None = None, stages: dict | None = None) -> SurveyResult:
    cfg, mp, stages = cfg or load_config(), mp or load_mapping(), stages or STAGES
    responded, drop_idx = stage_a(subject, cfg)
    res = SurveyResult(subject.subscription_id, subject.survey_id, subject.survey_type, responded)
    if not responded: return res
    a, truth = res.answers, {}
    for i, q in enumerate(Q.ORDER):
        if Q.skipped(q, a): continue
        if drop_idx is not None and i >= drop_idx:
            res.completed, res.dropped_at = False, q; break
        fn = stages.get(q)
        if fn is None:
            res.pending = q; break
        c = Ctx(subject, cfg, mp, a, q); c.truth_prev = truth
        if q not in REQUIRED and c.rng.random() < cfg["item_skip_rate"]:
            continue                                        # 선택 문항 건너뜀: 응답 없음
        v = fn(c)
        if v is not None: a[q] = v                             # None = 작성하지 않은 선택 문항(open)
        res.other_text.update(c.other_text)
        truth.update(c.truth)
    else:
        res.completed = True
    res.truth = truth
    return res
