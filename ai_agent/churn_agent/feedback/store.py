"""합성 결과 -> 저장 형식(long). 설계서 §4: survey_send / survey_response / survey_answer / synth_truth_topic. 파일(CSV) 또는 PostgreSQL로 쓴다."""
from __future__ import annotations
import datetime as dt
from pathlib import Path
import pandas as pd
from . import questionnaire as Q

COLS = {
    "survey_send": ["survey_id", "subscription_id", "action_batch_id", "survey_type", "sent_at", "sent_by", "responded_yn"],
    "survey_response": ["response_id", "survey_id", "completed_yn", "status_code", "submitted_at"],
    "survey_answer": ["response_id", "question_id", "answer_code", "other_text"],
    "synth_truth_topic": ["subscription_id", "topic_id", "strength"],
}


def to_rows(subjects, results, sent_at: dt.datetime) -> dict[str, list[dict]]:
    """설문 1건 = survey_send 1행. 응답이 있으면 survey_response 1행과 survey_answer 여러 행. 모든 문항이 합성된 결과(completed가 None이 아닌)만 받는다."""
    rows = {k: [] for k in COLS}; seen_topics = set()
    for i, (s, r) in enumerate(zip(subjects, results), start=1):
        if r.responded and r.completed is None:
            raise ValueError(f"{s.subscription_id}: 아직 합성하지 못한 문항이 있음({r.pending})")
        rows["survey_send"].append(dict(survey_id=i, subscription_id=s.subscription_id, action_batch_id=None, survey_type=s.survey_type, sent_at=sent_at,
                                        sent_by="auto", responded_yn="Y" if r.responded else "N"))
        for t, strength in getattr(s, "hidden_strengths", {}).items():
            if (s.subscription_id, t) not in seen_topics:
                seen_topics.add((s.subscription_id, t)); rows["synth_truth_topic"].append(dict(subscription_id=s.subscription_id, topic_id=t, strength=strength))
        if not r.responded: continue
        rid = len(rows["survey_response"]) + 1
        rows["survey_response"].append(dict(response_id=rid, survey_id=i, completed_yn="Y" if r.completed else "N", status_code=r.answers.get("status"),
                                            submitted_at=sent_at + dt.timedelta(hours=1 + (i * 7) % 47)))
        for q in Q.ORDER:
            if q not in r.answers: continue
            v = r.answers[q]
            if Q.QUESTIONS[q][0] == "text":
                rows["survey_answer"].append(dict(response_id=rid, question_id=q, answer_code=None, other_text=v)); continue
            for code in (v if isinstance(v, (list, tuple)) else [v]):
                txt = None
                if q == "issues" and code == "other": txt = r.other_text.get("issues")
                if q == "interview" and code == "yes": txt = r.other_text.get("interview")
                rows["survey_answer"].append(dict(response_id=rid, question_id=q, answer_code=code, other_text=txt))
    return rows


def write_csv(rows: dict, folder) -> dict:
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True); out = {}
    for t, cols in COLS.items():
        df = pd.DataFrame(rows[t], columns=cols); p = folder / f"{t}.csv"; df.to_csv(p, index=False, encoding="utf-8"); out[t] = len(df)
    return out


def write_sql(engine, rows: dict, reset: bool = False) -> dict:
    """PostgreSQL에 넣는다. survey_send·survey_response는 합성기가 정한 ID를 그대로 쓰므로 OVERRIDING SYSTEM VALUE로 넣고, ID 시퀀스를 맞춘다."""
    from sqlalchemy import text
    out = {}
    with engine.begin() as c:
        if reset:
            for t in ("survey_answer", "survey_response", "survey_send", "synth_truth_topic"): c.execute(text(f"DELETE FROM {t}"))
        for t in ("survey_send", "survey_response", "synth_truth_topic"):
            cols = COLS[t]; data = rows[t]
            if data:
                over = " OVERRIDING SYSTEM VALUE" if t != "synth_truth_topic" else ""
                c.execute(text(f"INSERT INTO {t} ({', '.join(cols)}){over} VALUES ({', '.join(':' + x for x in cols)})"), data)
            out[t] = len(data)
        cols = COLS["survey_answer"]
        if rows["survey_answer"]:
            c.execute(text(f"INSERT INTO survey_answer ({', '.join(cols)}) VALUES ({', '.join(':' + x for x in cols)})"), rows["survey_answer"])
        out["survey_answer"] = len(rows["survey_answer"])
        for t, col in (("survey_send", "survey_id"), ("survey_response", "response_id")):
            c.execute(text(f"SELECT setval(pg_get_serial_sequence('{t}', '{col}'), COALESCE((SELECT max({col}) FROM {t}), 1))"))
    return out
