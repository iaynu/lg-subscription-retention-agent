"""후보 생성 결과를 숨은 주제 정답(synth_truth_topic)과 맞춰 보는 검증. 후보 생성 쪽 코드는 정답을 읽지 않고, 이 파일에서만 붙인다.
정답이 있는 합성 데이터에서만 쓰며 순환 검증이다(주제를 심은 규칙으로 만든 문장을 다시 찾는 정도)."""
from __future__ import annotations
import pandas as pd


def recovery(result: dict, df: pd.DataFrame, truth: pd.DataFrame, topics=("H-A", "H-B")) -> dict:
    """주제마다 가장 잘 맞는 후보(F1 기준) 하나를 골라 정밀도·재현율을 계산한다.
    정밀도 = 후보 근거 응답 중 그 주제 보유자 비율, 재현율 = 응답자 중 그 주제 보유자 가운데 근거에 들어간 비율.
    기준선 = 응답자 전체의 보유 비율(무작위로 골랐을 때의 정밀도)."""
    sub_of = df.subscription_id.to_dict(); out = {}
    for tp in topics:
        holders = set(truth[truth.topic_id == tp].subscription_id)
        resp_h = {rid for rid, s in sub_of.items() if s in holders}
        d = {"holders_in_responses": len(resp_h), "baseline_precision": round(len(resp_h) / max(len(sub_of), 1), 3),
             "by_cohort": df[df.index.isin(resp_h)].survey_type.value_counts().to_dict(), "best": None}
        best = None
        for p in result["proposals"]:
            ids = set(p["response_ids"]); hit = len(ids & resp_h)
            if not ids or not hit: continue
            pr, rc = hit / len(ids), hit / max(len(resp_h), 1); f1 = 2 * pr * rc / (pr + rc)
            if best is None or f1 > best["f1"]:
                best = {"title": p["title"], "source": p["source"], "evidence": len(ids), "hits": hit, "precision": round(pr, 3), "recall": round(rc, 3), "f1": round(f1, 3)}
        d["best"] = best; out[tp] = d
    for tp, d in out.items():
        holders = set(truth[truth.topic_id == tp].subscription_id)
        d["per_candidate"] = [{"title": p["title"], "evidence": len(p["response_ids"]), "hits": len({sub_of[r] for r in p["response_ids"] if r in sub_of} & holders)} for p in result["proposals"]]
    return out


def report_md(rec: dict, result: dict) -> str:
    L = ["# 후보 생성 검증 (숨은 주제 회수) — 합성 정답 기준, 순환 검증", "",
         f"- 후보 {len(result['proposals'])}개, 경로 `{result['source']}`, 거절 {len(result['rejected'])}개" + (f", LLM 오류: {result['llm_error']}" if result.get("llm_error") else ""), "",
         "| 주제 | 응답자 중 보유 | 기준선 정밀도 | 최고 후보 | 근거 | 정밀도 | 재현율 |", "|---|---|---|---|---|---|---|"]
    for tp, d in rec.items():
        b = d["best"]
        L.append(f"| {tp} | {d['holders_in_responses']} | {d['baseline_precision']:.1%} | " + (f"{b['title']} ({b['source']}) | {b['evidence']} | {b['precision']:.1%} | {b['recall']:.1%} |" if b else "없음 | - | - | - |"))
    L += ["", "후보별 주제 보유자 수(근거 중): "] + [f"- {p['title']} (근거 {p['evidence']}): " + ", ".join(f"{tp} {rec[tp]['per_candidate'][i]['hits']}" for tp in rec) for i, p in enumerate(rec[next(iter(rec))]["per_candidate"])]
    s1 = result.get("stage1")
    if s1: L += ["", f"1단계 분류: 기존 유형과 정확히 일치로 제외 {s1['removed_texts']}문장({s1['removed_responses']}응답) {s1['by_type']}, 부분 일치 {s1['partial']}문장" + (f", 오류 {s1['error']}" if s1["error"] else "")]
    L += ["", "코호트별 보유 응답: " + "; ".join(f"{tp} {d['by_cohort']}" for tp, d in rec.items())]
    return "\n".join(L)
