"""LG 구독 이탈 방지 Agent — Streamlit 화면.

DB 에 직접 붙지 않고 FastAPI 서버(backend/)만 호출한다.
실행 (backend 서버를 먼저 켠 뒤):  streamlit run app.py
API 주소는 환경변수 API_URL 또는 사이드바에서 바꾼다(기본 http://127.0.0.1:8000).
"""
import os

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="LG 구독 이탈 방지 Agent", page_icon="🛡️", layout="wide")

RISK_KO = {"HIGH": "높음", "MID": "중간", "LOW": "낮음"}
RISK_ICON = {"HIGH": "🔴", "MID": "🟠", "LOW": "🟢"}


# ------------------------------------------------------------------ API
with st.sidebar:
    st.header("설정")
    API = st.text_input("API 주소", os.environ.get("API_URL", "http://127.0.0.1:8000")).rstrip("/")
    reviewer = st.text_input("검토자 이름", "관리자")
    st.caption("Agent 는 제안만 하고, 승인·반려는 사람이 합니다. 발송은 시뮬레이션이라 action_log 에만 기록돼요.")


def call(method, path, timeout=60, quiet=False, **kw):
    """API 호출. 실패하면 화면에 이유를 보여 주고 None 을 돌려준다."""
    try:
        r = requests.request(method, API + path, timeout=timeout, **kw)
    except requests.RequestException as exc:
        if not quiet:
            st.error(f"서버에 연결할 수 없어요 ({API}). backend 서버가 켜져 있는지 확인하세요.\n\n{exc}")
        return None
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if not quiet:
            st.error(f"요청 실패 ({r.status_code}): {detail}")
        return None
    return r.json()


@st.cache_data(ttl=30, show_spinner=False)
def cached_get(api, path):
    """읽기 전용 조회(30초 캐시). 실패하면 None — 예외를 캐시하지 않도록 직접 처리한다."""
    try:
        r = requests.get(api + path, timeout=60)
        return r.json() if r.status_code < 400 else None
    except (requests.RequestException, ValueError):
        return None


def get(path):
    return cached_get(API, path)


def won(v):
    return "-" if v is None else f"{int(round(float(v))):,}원"


def pct(v, digits=1):
    return "-" if v is None else f"{float(v) * 100:.{digits}f}%"


def type_label(type_id, names):
    return f"{type_id} {names.get(type_id, '')}".strip() if type_id else "-"


# ------------------------------------------------------------------ 헤더
health = get("/health")
st.title("🛡️ LG 구독 이탈 방지 Agent")
if health is None:
    st.error(f"API 서버({API})에 연결할 수 없어요. backend 에서 `uvicorn api.main:app --port 8000` 을 먼저 실행하세요.")
    st.stop()
llm_on = health.get("llm") == "openai"
st.caption(f"API 연결됨 · DB {health['db']} · 구독 {health['subscriptions']:,}건 · "
           f"문구 생성 {'OpenAI ' + str(health.get('model')) if llm_on else '템플릿(LLM 키 없음 또는 꺼짐)'}")

catalog = get("/catalog") or {"types": [], "solutions": []}
TYPE_NAMES = {t["type_id"]: t["type_name"] for t in catalog["types"]}
DEPTS = get("/departments") or []
DEPT_NAMES = [d["name"] for d in DEPTS]

tab_ov, tab_care, tab_ins, tab_survey, tab_log = st.tabs(
    ["📊 개요", "👤 고객 케어", "📈 인사이트", "📝 설문 · 신규 유형", "🗂 조치 이력"])


# ================================================================== 개요
with tab_ov:
    ov = get("/overview")
    if ov:
        risk = ov["risk"]
        c = st.columns(5)
        c[0].metric("전체 구독", f"{ov['total']:,}")
        c[1].metric("평균 이탈 확률", pct(ov["avg_prob"]))
        c[2].metric("🔴 높음", f"{risk.get('HIGH', 0):,}")
        c[3].metric("🟠 중간", f"{risk.get('MID', 0):,}")
        c[4].metric("🟢 낮음", f"{risk.get('LOW', 0):,}")

        left, right = st.columns(2)
        with left:
            st.subheader("제품군별 위험 고객")
            dfp = pd.DataFrame(ov["products"]).set_index("name")[["high", "mid"]]
            dfp.columns = ["높음", "중간"]
            st.bar_chart(dfp)
        with right:
            st.subheader("1순위 이탈 유형 분포")
            dft = pd.DataFrame(ov["types"])
            dft["유형"] = dft["type_id"].map(lambda t: type_label(t, TYPE_NAMES))
            st.bar_chart(dft.set_index("유형")["n"], horizontal=True)
        st.caption("※ 모든 데이터는 합성 데이터이며, 이탈 확률·유형은 모델 예측 결과입니다.")


# ================================================================== 고객 케어
def render_customer(sub_id):
    d = call("GET", f"/subscriptions/{sub_id}")
    if not d:
        return None
    sb, cu, pr = d["subscription"], d["customer"], d["prediction"]
    st.subheader(f"{cu.get('customer_name', '')} · {sb['subscription_id']}")
    m = st.columns(4)
    m[0].metric("이탈 확률", pct(pr["churn_probability"]))
    m[1].metric("위험도", f"{RISK_ICON.get(pr['risk_level'], '')} {RISK_KO.get(pr['risk_level'], pr['risk_level'])}")
    m[2].metric("월 요금", won(sb["monthly_fee"]))
    m[3].metric("약정 종료까지", f"{d['features'].get('days_to_end', '-')}일")
    st.caption(f"{sb['product_category']} {sb['model_name']} · 케어 {sb['care_type']} · 약정 {sb['contract_months']}개월 "
               f"({sb['start_date']} ~ {sb['end_date']}) · 기준일 {d['today']}")

    a, b = st.columns(2)
    with a:
        st.markdown("**이탈 유형 (기여도 순)**")
        for t in d["types"]:
            feats = ", ".join(f"{f.get('label') or f['feature']}={f.get('value')}" for f in (t.get("top_features") or [])[:3])
            st.write(f"{t['type_rank']}. **{type_label(t['type_id'], TYPE_NAMES)}** — {pct(t['contribution_share'], 0)}")
            if feats:
                st.caption(f"근거: {feats}")
    with b:
        st.markdown("**최근 VOC**")
        if d["recent_voc"]:
            st.dataframe(pd.DataFrame(d["recent_voc"]).rename(
                columns={"contact_date": "접수일", "category": "분류", "summary_text": "요약"}),
                hide_index=True, width="stretch")
        else:
            st.caption("최근 VOC 없음")
    with st.expander("계산된 특징(features) · 조치 이력"):
        st.json(d["features"], expanded=False)
        if d["actions"]:
            st.dataframe(pd.DataFrame(d["actions"])[["action_id", "created_at", "solution_id", "target", "status", "message_text"]],
                         hide_index=True, width="stretch")
    return d


def render_plan(res):
    """조치안 + 승인/반려 UI."""
    run_id = res["run_id"]
    plan, meta = res["plan"], res["meta"]
    st.divider()
    st.subheader(f"🤖 Agent 조치안 (run #{run_id})")
    src = f"LLM {meta['model']}" if meta["llm_used"] == "Y" else "템플릿 문구"
    st.caption(f"{src} · 모드 {meta['mode']} · 결과 {meta['outcome']}"
               + (f" · {meta['fallback_reason']}" if meta.get("fallback_reason") else ""))
    lim = plan.get("limits") or {}
    st.write(plan.get("summary") or "")
    if lim:
        st.caption(f"조치 최대 {lim.get('max_actions')}개 · 비용 한도 {won(lim.get('cost_limit'))} · "
                   f"90일 누적 {won(lim.get('spent_90d'))} · 이번 제안 합계 {won(plan.get('total_cost'))}")

    done = {(a["solution_id"]) for a in res.get("logged_actions", []) if a["status"] != "반려"}
    if not plan["actions"]:
        st.info("조건을 만족하는 조치가 없어요. 아래 '규칙 판단 근거'에서 이유를 확인하세요.")

    for act in plan["actions"]:
        sid = act["solution_id"]
        key = f"{run_id}_{sid}"
        with st.container(border=True):
            st.markdown(f"**{act['solution_name']}** `{sid}` · {type_label(act['type_id'], TYPE_NAMES)} · "
                        f"대상 **{act['target']}** · 비용 {won(act['offer_cost'])}"
                        + (" · 📣 광고성" if act["message_kind"] == "ad" else ""))
            st.caption(f"선정 이유: {act['why']}")
            cust, memo, depts = act.get("customer_message"), act.get("internal_note"), []
            if cust is not None:
                cust_edit = st.text_area("고객 발송 문구 (수정 가능)", cust, key=f"c_{key}", height=140)
                if act.get("buttons"):
                    st.caption("버튼: " + " · ".join(act["buttons"]))
            if memo is not None:
                memo_edit = st.text_area("협업 전달 메모 (수정 가능)", memo, key=f"m_{key}", height=100)
                defaults = [d for d in (res.get("forward") or {}).get("depts", []) if d in DEPT_NAMES] \
                    if isinstance(res.get("forward"), dict) else []
                depts = st.multiselect("전달 부서", DEPT_NAMES, default=defaults, key=f"d_{key}")
            note = st.text_input("결정 메모 (선택)", key=f"n_{key}")

            b1, b2, b3 = st.columns([1, 1, 2])
            if sid in done:
                b1.success("기록됨")
            elif b1.button("✅ 승인", key=f"ok_{key}", type="primary"):
                msgs = []
                if cust is not None:
                    r = call("POST", f"/runs/{run_id}/approve", json={
                        "solution_ids": [sid], "part": "customer", "message_text": cust_edit,
                        "decision_note": note or None, "edited": cust_edit != cust})
                    msgs.append(r)
                if memo is not None:
                    r = call("POST", f"/runs/{run_id}/approve", json={
                        "solution_ids": [sid], "part": "internal", "internal_memo": memo_edit,
                        "forward_depts": depts, "decision_note": note or None, "edited": memo_edit != memo})
                    msgs.append(r)
                if msgs and all(msgs):
                    st.session_state["flash"] = ("success" if any(m["logged_action_ids"] for m in msgs) else "warning",
                                                 " / ".join(m["note"] for m in msgs))
                    st.rerun()
            reason = b3.text_input("반려 사유", key=f"r_{key}", label_visibility="collapsed", placeholder="반려 사유 (필수)")
            if b2.button("⛔ 반려", key=f"no_{key}"):
                if not reason.strip():
                    st.warning("반려 사유를 입력해 주세요.")
                else:
                    r = call("POST", f"/runs/{run_id}/reject", json={"reason": reason, "solution_ids": [sid]})
                    if r:
                        st.session_state["flash"] = ("info", r["note"])
                        st.rerun()

    with st.expander("규칙 판단 근거 (후보 · 제외 사유)"):
        rt = res["rule_trace"]
        st.write(f"모드 **{rt['mode']}** · 사용한 유형 {', '.join(rt['types_used'])}"
                 + (f" · {rt['gate_reason']}" if rt.get("gate_reason") else ""))
        if rt["eligible"]:
            st.markdown("**후보**")
            st.dataframe(pd.DataFrame(rt["eligible"])[["solution_id", "solution_name", "priority", "target", "offer_cost", "why"]],
                         hide_index=True, width="stretch")
        if rt["excluded"]:
            st.markdown("**제외**")
            st.dataframe(pd.DataFrame(rt["excluded"]), hide_index=True, width="stretch")
    if res.get("messages"):
        with st.expander("고객에게 가는 알림톡 (합본 미리보기)"):
            for m in res["messages"]:
                st.markdown(f"**{m['title']}** · {m['channel']}" + (" · 광고" if m["ad_yn"] == "Y" else ""))
                st.text(m["message_text"])


with tab_care:
    if "flash" in st.session_state:
        kind, text = st.session_state.pop("flash")
        getattr(st, kind)(text)

    f1, f2, f3 = st.columns([2, 2, 3])
    risk_sel = f1.multiselect("위험도", ["HIGH", "MID", "LOW"], default=["HIGH", "MID"], format_func=lambda x: RISK_KO[x])
    type_sel = f2.selectbox("1순위 유형", [""] + list(TYPE_NAMES), format_func=lambda t: type_label(t, TYPE_NAMES) if t else "전체")
    q = f3.text_input("검색 (이름 · 구독 ID · 고객 ID)")
    page_size = 30
    page = st.number_input("페이지", min_value=1, value=1, step=1)
    params = {"limit": page_size, "offset": (page - 1) * page_size}
    if risk_sel:
        params["risk"] = ",".join(risk_sel)
    if type_sel:
        params["type_id"] = type_sel
    if q.strip():
        params["q"] = q.strip()
    lst = call("GET", "/subscriptions", params=params)

    picked = None
    if lst:
        st.caption(f"총 {lst['total']:,}건 중 {len(lst['items'])}건 표시 · 행을 클릭하면 상세가 열려요")
        df = pd.DataFrame(lst["items"])
        if len(df):
            view = df[["subscription_id", "customer_name", "product_category", "model_name", "monthly_fee",
                       "churn_probability", "risk_level", "top_type_name"]].rename(columns={
                "subscription_id": "구독 ID", "customer_name": "고객", "product_category": "제품군", "model_name": "모델",
                "monthly_fee": "월 요금", "churn_probability": "이탈 확률", "risk_level": "위험도", "top_type_name": "1순위 유형"})
            ev = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun",
                              selection_mode="single-row", key="cust_table",
                              column_config={"이탈 확률": st.column_config.ProgressColumn(
                                  format="%.2f", min_value=0, max_value=1),
                                  "월 요금": st.column_config.NumberColumn(format="%d원")})
            if ev.selection.rows:
                picked = df.iloc[ev.selection.rows[0]]["subscription_id"]
        else:
            st.info("조건에 맞는 구독이 없어요.")
    manual = st.text_input("또는 구독 ID 직접 입력", placeholder="예: S028507")
    sub_id = manual.strip() or picked

    if sub_id:
        st.divider()
        d = render_customer(sub_id)
        if d:
            plans = st.session_state.setdefault("plans", {})
            p1, p2, p3 = st.columns([1, 1, 2])
            use_llm = p3.toggle("LLM 으로 문구 작성", value=llm_on, disabled=not llm_on,
                                help="꺼져 있으면 템플릿 문구를 써요. 서버에 OpenAI 키가 있어야 켤 수 있어요.")
            if p1.button("🤖 AI 조치안 생성", type="primary"):
                with st.spinner("Rule Engine → 문구 작성 → 검증 중..."):
                    res = call("POST", f"/subscriptions/{sub_id}/plan", timeout=180, json={"use_llm": use_llm})
                if res:
                    plans[sub_id] = res
            if d["runs"] and p2.button("최근 조치안 불러오기"):
                res = call("GET", f"/runs/{d['runs'][0]['run_id']}")
                if res:
                    plans[sub_id] = res
            if sub_id in plans:
                # 승인 기록을 반영하기 위해 서버에서 다시 읽는다
                fresh = call("GET", f"/runs/{plans[sub_id]['run_id']}", quiet=True)
                render_plan(fresh or plans[sub_id])

            with st.expander("🤝 협업 부서에 직접 전달 (Agent 제안 없이 상담자가 요청)"):
                fd = st.multiselect("전달 부서", DEPT_NAMES, key=f"fd_{sub_id}")
                fn = st.text_input("메모", key=f"fn_{sub_id}")
                if st.button("전달 기록", key=f"fb_{sub_id}"):
                    r = call("POST", f"/subscriptions/{sub_id}/forward", json={
                        "depts": fd, "note": fn, "run_id": plans[sub_id]["run_id"] if sub_id in plans else None})
                    if r:
                        st.success(r["note"])


# ================================================================== 인사이트
with tab_ins:
    ins = get("/insights")
    if not ins:
        st.warning("인사이트를 불러오지 못했어요.")
    else:
        st.caption(f"기준일 {ins['as_of']}")
        l, r = st.columns(2)
        with l:
            st.subheader("신규 구독 vs 해지 문의 (최근 6개월)")
            tr = ins["trend"]
            st.line_chart(pd.DataFrame({"신규 구독": tr["new"], "해지 문의": tr["cancel_voc"]}, index=tr["labels"]))
        with r:
            st.subheader("VOC 분류 상위")
            st.bar_chart(pd.DataFrame(ins["voc_dist"], columns=["분류", "건수"]).set_index("분류"))

        st.subheader("제품군별 인사이트")
        prod = st.selectbox("제품군", list(ins["product"]))
        pd_ = ins["product"][prod]
        c = st.columns(5)
        c[0].metric("구독 수", f"{pd_['n']:,}")
        c[1].metric("고·중위험 비율", f"{pd_['risk_pct']}%")
        c[2].metric("VOC 부정 비중", f"{pd_['voc_neg_pct']}%")
        c[3].metric("방문 정시율", f"{pd_['ontime_pct']}%")
        c[4].metric("AS 재방문율", f"{pd_['revisit_pct']}%")
        a, b = st.columns(2)
        with a:
            st.markdown("**주요 이탈 유형**")
            for t, share in pd_["top_types"]:
                tid = f"T{t + 1:02d}"
                st.write(f"- {type_label(tid, TYPE_NAMES)} — {share}%")
            st.markdown("**VOC 키워드**")
            st.dataframe(pd.DataFrame(pd_["kw"], columns=["분류", "건수"]), hide_index=True, width="stretch")
        with b:
            st.markdown("**최근 3개월 신규 vs 해지 문의**")
            mm = pd.DataFrame(pd_["months"]).rename(columns={"new": "신규 구독", "cancel_voc": "해지 문의"}).set_index("m")
            st.bar_chart(mm)

        team = ins.get("team")
        if team:
            st.subheader(f"Team Insight · {team['month_label']}")
            if team["dept"] and team["dept"][0].get("ai"):
                st.caption("✨ AI 가 규칙 문장을 해석한 요약이에요(검증 실패 시 규칙 문장 그대로).")
            for blk in team["dept"]:
                with st.container(border=True):
                    g = blk["g1"]
                    st.markdown(f"**{g['t']}**")
                    st.line_chart(pd.DataFrame({g["a"]: g["A"], g["b"]: g["B"]}, index=team["months"]))
                    for line in blk["ins"]:
                        st.markdown(f"- {line}")
                    with st.expander("관련 리포트"):
                        for title, day, tags, body in blk["rpt"]:
                            st.markdown(f"**{title}** · {day} · {' '.join('`' + t + '`' for t in tags)}")
                            st.text(body)
            for blk in team["rdept"]:
                with st.container(border=True):
                    st.markdown(f"**{blk['title']}**")
                    for line in blk["ins"]:
                        st.markdown(f"- {line}")


# ================================================================== 설문 · 신규 유형
with tab_survey:
    eda = get("/survey/eda")
    if eda and eda.get("ready"):
        st.subheader("설문 현황")
        c = st.columns(4)
        c[0].metric("발송", f"{eda['sent']:,}")
        c[1].metric("응답", f"{eda['responded']:,}")
        c[2].metric("응답률", f"{eda['response_rate']}%")
        c[3].metric("완료율", f"{eda['completion_rate']}%")
        a, b = st.columns(2)
        with a:
            st.markdown("**설문 종류별 응답률**")
            st.dataframe(pd.DataFrame(eda["by_type"]).rename(columns={"label": "종류", "n": "발송", "resp": "응답", "pct": "응답률(%)"}),
                         hide_index=True, width="stretch")
            st.markdown("**현재 상태**")
            st.bar_chart(pd.DataFrame(eda["status"]["items"]).set_index("label")["n"])
        with b:
            for key, title in (("issues", "불편했던 점"), ("wanted", "원하는 조치"), ("newsol", "새 솔루션 아이디어")):
                st.markdown(f"**{title}** (응답 {eda[key]['n']:,})")
                st.bar_chart(pd.DataFrame(eda[key]["items"]).set_index("label")["pct"], horizontal=True)
        st.markdown("**자주 나온 주관식 응답**")
        st.dataframe(pd.DataFrame(eda["open_texts"]).rename(columns={"text": "응답", "n": "건수"}),
                     hide_index=True, width="stretch")
    else:
        st.info("설문 데이터가 아직 적재되지 않았어요.")

    st.divider()
    st.subheader("신규 유형 후보 검토")
    props = call("GET", "/proposals", quiet=True)
    if props:
        cn = props["counts"]
        st.caption(f"검토중 {cn['검토중']} · 채택 {cn['채택']} · 기각 {cn['기각']}")
        if not props["proposals"]:
            st.info("후보가 없어요. backend 에서 `python scripts/make_candidates.py --in data_in/data_out --provider mock` 으로 만들 수 있어요.")
        for p in props["proposals"]:
            with st.container(border=True):
                st.markdown(f"**[{p['kind']}] {p['title']}** — 상태 `{p['status']}` · 근거 {p['support']}건({pct(p['share'], 0)}) · "
                            f"{p['votes']}/{p['n_runs']}회 등장")
                st.caption(f"{p['description']} · 가장 가까운 기존 유형: {type_label(p.get('closest_type_id'), TYPE_NAMES)}")
                for qt in p["quotes"][:3]:
                    st.markdown(f"> {qt}")
                for s_ in p["solutions"]:
                    st.write(f"💡 {s_['title']} ({s_['category']}) — {s_['message_hint']}")
                k = f"prop_{p['proposal_id']}"
                x1, x2, x3 = st.columns(3)
                if p["status"] == "검토중":
                    if x1.button("채택", key=k + "_a", type="primary"):
                        if call("POST", f"/proposals/{p['proposal_id']}/approve", json={"reviewer": reviewer}):
                            st.rerun()
                    why = x3.text_input("기각 사유", key=k + "_w", label_visibility="collapsed", placeholder="기각 사유")
                    if x2.button("기각", key=k + "_r"):
                        if call("POST", f"/proposals/{p['proposal_id']}/reject", json={"reviewer": reviewer, "reason": why}):
                            st.rerun()
                elif x1.button("검토중으로 되돌리기", key=k + "_u"):
                    if call("POST", f"/proposals/{p['proposal_id']}/restore"):
                        st.rerun()

        if props["new_solutions"]:
            st.subheader("채택된 신규 솔루션 활성화")
            for s_ in props["new_solutions"]:
                with st.container(border=True):
                    st.json(s_, expanded=False)
                    sid = s_.get("solution_id")
                    if sid and s_.get("active_yn") != "Y":
                        cost = st.number_input("1회 오퍼 비용(혜택제안만 필수)", min_value=0, step=1000, key=f"cost_{sid}")
                        fee = st.selectbox("요금 영향", ["없음", "증가", "감소"], key=f"fee_{sid}")
                        if st.button("활성화", key=f"act_{sid}"):
                            if call("POST", f"/solutions/{sid}/activate", json={"offer_cost": int(cost), "fee_impact": fee}):
                                st.rerun()


# ================================================================== 조치 이력
with tab_log:
    n = st.slider("표시 건수", 10, 200, 50, step=10)
    if st.button("새로고침"):
        st.rerun()
    acts = call("GET", "/actions", params={"limit": n})
    if acts:
        df = pd.DataFrame(acts)
        st.dataframe(df[["action_id", "created_at", "customer_name", "subscription_id", "solution_id", "target", "message_kind",
                         "offer_cost", "status", "forward_depts", "memo_edited_yn", "message_text"]].rename(columns={
            "action_id": "ID", "created_at": "시각", "customer_name": "고객", "subscription_id": "구독", "solution_id": "솔루션",
            "target": "대상", "message_kind": "종류", "offer_cost": "비용", "status": "상태", "forward_depts": "전달 부서",
            "memo_edited_yn": "수정", "message_text": "내용"}), hide_index=True, width="stretch")
        c = st.columns(3)
        c[0].metric("기록", len(df))
        c[1].metric("승인(시뮬레이션)", int((df["status"] == "simulated").sum()))
        c[2].metric("반려", int((df["status"] == "반려").sum()))
    elif acts == []:
        st.info("아직 기록된 조치가 없어요. 고객 케어 탭에서 조치안을 승인해 보세요.")
