"""AgentService: Rule Engine → LLM → 검증/폴백 → 결과 조립, 상담자 결정 기록."""
from __future__ import annotations
import datetime as dt, json, re, uuid
from .facts import merged_facts
from .forward import build_analysis, build_forward, load_departments
from .llm.prompt import PROMPT_VERSION
from .rules.engine import run_rule_engine
from .validation.fallback import run_with_fallback, template_item
from .validation.validator import validate

CONFIG_NUMBERS = [20, 30]


def status_label(row: dict) -> str:
    """화면 표시용 상태. DB의 action_status('조치완료'/'반려')는 그대로 두고 채널로 문구를 나눈다."""
    if row["action_status"] == "반려": return "반려"
    if row["action_status"] == "조치완료":
        ch = row.get("channel")
        return "부서 전달 완료" if ch == "내부 전달" else "상담 안내 완료" if ch == "상담·앱" else "발송 완료"
    return row["action_status"]


def split_buttons(s):
    """'[A] / [B]' → ['A', 'B']. 대괄호가 없으면 ' / '로 나눈다(예: '설문 참여하기')."""
    s = (s or "").strip()
    found = re.findall(r"\[([^\]]+)\]", s)
    if found: return [b.strip() for b in found]
    return [b.strip() for b in s.split("/") if b.strip()]


def merge_messages(selected, by_id, max_buttons=5, max_title=40):
    """같은 채널·같은 광고 여부의 고객 메시지를 한 통으로 묶는다(검증은 항목별로 이미 끝난 뒤).
    광고성(친구톡)과 정보성(알림톡)은 섞지 않는다. 내부 전달서·상담 안내는 묶지 않는다."""
    groups = {}
    for s in selected:
        c = by_id[s["solution_id"]]
        if c.get("delivery_kind", "고객 메시지") != "고객 메시지" or not s["message_text"]: continue
        groups.setdefault((s["channel"], c.get("ad_yn", "N")), []).append(s)
    out = []
    for (ch, ad), items in groups.items():
        items.sort(key=lambda x: x["priority"])
        btn = list(dict.fromkeys(b for s in items for b in s["buttons"]))
        title = items[0]["title"]
        if len(items) > 1:
            suffix = f" 외 {len(items) - 1}건"
            title = title[:max_title - len(suffix)] + suffix
        out.append(dict(channel=ch, ad_yn=ad, title=title, message_text="\n\n".join(s["message_text"] for s in items),
                        buttons=btn[:max_buttons], solution_ids=[s["solution_id"] for s in items], merged=len(items) > 1,
                        buttons_truncated=len(btn) > max_buttons))
    return out


class AgentService:
    def __init__(self, cfg, catalog, llm, repo, clock=None):
        self.cfg, self.catalog, self.llm, self.repo = cfg, catalog, llm, repo
        self.by_id = {c["solution_id"]: c for c in catalog}; self.dep = load_departments()
        self.clock = clock or dt.datetime.now

    # ---------- 조립 ----------
    def _rules_cfg(self):
        return {**self.cfg["rules"], "facts_cfg": self.cfg.get("facts", {})}

    def _llm_input(self, ai, rr, facts, mfacts):
        action = rr["mode"] == "ACTION"
        return {"mode": rr["mode"], "subscription": ai["subscription"], "prediction": ai["prediction"],
                "types": ai["types"] if action else [], "context": ai.get("context", {}) if action else {},
                "message_facts": mfacts, "facts": facts,          # facts는 검증기 전용(프롬프트에는 넣지 않음)
                "candidates": rr["candidates"],
                "constraints": {"max_actions": rr["limits"]["max_actions"], "banned_phrases_extra": list(self.cfg["llm"].get("banned_phrases_extra") or []),
                                "config_numbers": CONFIG_NUMBERS, "max_message_chars": self.cfg["llm"].get("max_message_chars", 400)}}

    def _decorate(self, data):
        out = []
        for s in data["selected"]:
            c = self.by_id[s["solution_id"]]
            out.append({**{"title": "", "message_text": "", "internal_memo": ""}, **s,
                        "channel": c.get("channel", ""), "buttons": split_buttons(c.get("buttons"))})
        return sorted(out, key=lambda s: s["priority"])    # LLM이 순서를 섞어 내도 화면·저장은 우선순위 순(검증기가 1..n 연속을 보장)

    def _meta(self, log=None):
        l = self.cfg["llm"]
        return dict(llm_provider=l["provider"], llm_model=l["model"] if l["provider"] != "mock" else None,
                    llm_status=getattr(self.llm, "status", None), prompt_version=PROMPT_VERSION,
                    llm_errors=(log or {}).get("llm_errors", []))

    def run(self, ai: dict) -> dict:
        ai = dict(ai)
        ai["request_id"] = ai.get("request_id") or f"REQ-{uuid.uuid4().hex[:12]}"
        if self.repo.get_result(ai["request_id"]): raise ValueError("이미 있는 request_id")
        facts, mfacts = merged_facts(ai, self.cfg.get("facts"))
        rr = run_rule_engine(ai, self.catalog, self._rules_cfg())
        base = dict(request_id=ai["request_id"], mode=rr["mode"], excluded=rr["excluded"],
                    uncovered_types=rr["uncovered_types"], limits=rr["limits"], fallback=rr.get("fallback"))
        log = None
        if rr["mode"] == "SURVEY_ONLY":
            res = dict(base, outcome="no_action", attempts=0, failed_checks=[], selected=[], summary="")
            if rr.get("survey_solution"):
                nt = self.by_id[rr["survey_solution"]]
                li = self._llm_input(ai, dict(rr, mode="NO_TYPE", candidates=[nt], limits=dict(rr["limits"], max_actions=1)), facts, mfacts)
                data = {"selected": [template_item(nt, 1, li)], "summary": "만족도 조사를 안내합니다."}
                if validate(json.dumps(data, ensure_ascii=False), li)["ok"]:
                    res = dict(base, outcome="survey_template", attempts=0, failed_checks=[], selected=self._decorate(data), summary=data["summary"])
        elif not rr["candidates"]:
            res = dict(base, outcome="no_action", attempts=0, failed_checks=[], selected=[], summary="")
        else:
            li = self._llm_input(ai, rr, facts, mfacts); l = self.cfg["llm"]
            data, log = run_with_fallback(li, self.llm.generate, l["deadline_s"], l["first_timeout_s"], l["retry_timeout_s"])
            res = dict(base, outcome=log["outcome"], attempts=log["attempts"], failed_checks=log["failed_checks"],
                       selected=self._decorate(data), summary=data["summary"])
        res["messages"] = merge_messages(res["selected"], self.by_id)
        res["analysis"] = build_analysis(ai); res["forward"] = build_forward(ai, res["selected"], self.by_id, self.dep)
        res["meta"] = self._meta(log)
        self.repo.put_result(res["request_id"], dict(res, subscription_id=ai["subscription"].get("subscription_id"),
                                                      as_of_date=ai["as_of_date"], risk_prob=ai["prediction"].get("churn_probability")))
        return res

    # ---------- 결정 기록 ----------
    def _now(self, as_of: str) -> str:
        """결정 시각. simulated(기본): 기준일 날짜 + 현재 시각(시뮬레이션의 '오늘'이 기준일), real: 실제 시각."""
        now = self.clock()
        if self.cfg.get("clock", "simulated") == "real": return now.replace(microsecond=0).isoformat()
        return f"{as_of}T{now.strftime('%H:%M:%S')}"

    def _result(self, request_id, subscription_id):
        r = self.repo.get_result(request_id)
        if not r: raise KeyError("request_id를 찾을 수 없음")
        if r.get("subscription_id") and r["subscription_id"] != subscription_id: raise ValueError("subscription_id 불일치")
        return r

    def decide(self, request_id: str, subscription_id: str, solution_id: str, status: str, note: str = "", by: str | None = None, now: str | None = None):
        """고객 메시지(톡)·상담 안내 부분의 발송/반려. 내부 전달 부분은 forward()로 따로 기록한다."""
        if status not in ("조치완료", "반려"): raise ValueError("status는 조치완료 또는 반려")
        r = self._result(request_id, subscription_id)
        item = next((s for s in r["selected"] if s["solution_id"] == solution_id), None)
        if not item: raise ValueError("이 요청의 선택 항목에 없는 solution_id")
        c = self.by_id[solution_id]
        if c.get("delivery_kind") == "내부 전달": raise ValueError("순수 내부 전달 항목은 /agent/forward로 결정")
        if any(a["action_batch_id"] == request_id and a["solution_id"] == solution_id and a["channel"] != "내부 전달"
               for a in self.repo.list_actions(subscription_id)):
            raise ValueError("이미 결정된 항목")
        row = dict(action_batch_id=request_id, subscription_id=subscription_id, type_id=item["type_id"], solution_id=solution_id,
                   action_category=c["category"], title=item["title"] or None, message_text=item["message_text"] or None,
                   internal_memo=item["internal_memo"] or None, message_source=item["message_source"],
                   offer_cost=c["offer_cost"], risk_prob_at_action=r.get("risk_prob"), action_status=status, channel=c["channel"],
                   decision_note=(note or None), created_at=now or self._now(r["as_of_date"]), created_by=by)
        return self._labeled(self.repo.log_action(row))

    def forward(self, request_id, subscription_id, solution_ids, departments, text, by=None, now=None):
        """협업 부서 전달 확정: 항목마다 action_log 1행(channel='내부 전달'), 수신 부서·수정 여부를 함께 기록."""
        r = self._result(request_id, subscription_id)
        fw = r.get("forward")
        if not fw: raise ValueError("이 요청에는 협업 전달 항목이 없음")
        if not solution_ids or len(set(solution_ids)) != len(solution_ids): raise ValueError("전달 대상 solution_id가 비었거나 중복됨")
        valid = {i["solution_id"] for i in fw["items"]}
        if not set(solution_ids) <= valid: raise ValueError("전달 대상 solution_id가 올바르지 않음")
        names = {d["code"] for d in self.dep["departments"]}
        if not departments or len(set(departments)) != len(departments) or not set(departments) <= names:
            raise ValueError("수신 부서가 올바르지 않음")
        if not text.strip(): raise ValueError("전달 내용이 비어 있음")
        done = {a["solution_id"] for a in self.repo.list_actions(subscription_id) if a["action_batch_id"] == request_id and a["channel"] == "내부 전달"}
        if done & set(solution_ids): raise ValueError("이미 전달된 항목")
        out, when = [], now or self._now(r["as_of_date"])
        for sid in solution_ids:
            item = next(s for s in r["selected"] if s["solution_id"] == sid); c = self.by_id[sid]
            out.append(self._labeled(self.repo.log_action(dict(
                action_batch_id=request_id, subscription_id=subscription_id, type_id=item["type_id"], solution_id=sid,
                action_category=c["category"], internal_memo=text, message_source=item["message_source"], offer_cost=0,
                risk_prob_at_action=r.get("risk_prob"), action_status="조치완료", channel="내부 전달", created_by=by,
                forward_depts=",".join(departments), memo_edited_yn="Y" if text.strip() != fw["draft_text"].strip() else "N",
                created_at=when))))
        return out

    @staticmethod
    def _labeled(row): return dict(row, status_label=status_label(row))

    def list_actions(self, subscription_id):
        return [self._labeled(a) for a in self.repo.list_actions(subscription_id)]
