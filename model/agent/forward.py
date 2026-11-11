"""협업 부서 전달 초안. 머리말·수신·기한은 코드가 만들고, LLM은 항목별 internal_memo(요청 내용)만 쓴다."""
from __future__ import annotations
import json
from pathlib import Path
from .config import ROOT

def load_departments(path=None) -> dict:
    return json.loads(Path(path or ROOT / "data" / "departments.json").read_text(encoding="utf-8"))

def build_analysis(ai: dict) -> dict:
    p = ai["prediction"]
    return dict(risk_percent=round(p.get("churn_probability", 0) * 100), risk_level=p["risk_level"], risk_rank_pct=p["risk_rank_pct"],
                types=[dict(type_rank=t["type_rank"], type_id=t["type_id"], type_name=t.get("type_name", ""),
                            share_percent=round(t["contribution_share"] * 100),
                            features=[dict(feature=f["feature"], label=f.get("label", ""), value=f["value"], shap=f.get("shap", 0)) for f in t.get("top_features", [])])
                       for t in ai.get("types", [])],
                persona_quote=(ai.get("context") or {}).get("persona_quote"))

def build_forward(ai: dict, selected: list[dict], by_id: dict, dep: dict) -> dict | None:
    items = [s for s in sorted(selected, key=lambda x: x["priority"]) if by_id[s["solution_id"]].get("internal_forward_yn") == "Y" and s.get("internal_memo")]
    if not items: return None
    names = {d["code"]: d["name"] for d in dep["departments"]}
    codes, unmapped = [], []
    for s in items:
        raw = by_id[s["solution_id"]].get("forward_dept") or ""
        for part in [x.strip() for x in raw.split(",") if x.strip()]:
            if part in dep["mapping"]:
                codes += [c for c in dep["mapping"][part] if c not in codes]
            elif part not in unmapped: unmapped.append(part)
    cust, sub = ai.get("customer") or {}, ai["subscription"]
    who = " ".join(x for x in (cust.get("customer_id"), cust.get("customer_name")) if x) or f"구독 {sub.get('subscription_id', '')}"
    risk = round(ai["prediction"].get("churn_probability", 0) * 100)
    fi = [dict(role="요청 조치" if i == 0 else "보조 조치", solution_id=s["solution_id"], name=by_id[s["solution_id"]]["name"], memo=s["internal_memo"]) for i, s in enumerate(items)]
    days = dep.get("deadline_business_days", 3)
    text = f"[협업 요청] {who} 고객 ({sub.get('product_category', '')} · 위험도 {risk}%)\n수신: {', '.join(names[c] for c in codes) or '(부서를 선택해 주세요)'}\n"
    for it in fi: text += f"\n■ {it['role']}: {it['name']}\n{it['memo']}\n"
    text += f"\n■ 처리 기한: 영업일 {days}일 이내, 완료 후 회신 부탁드립니다"
    return dict(departments=codes, unmapped=unmapped, items=fi, draft_text=text, deadline_business_days=days)
