"""LG Care Better — 구독 이탈 방어 AI Agent (Streamlit 화면).

DB 에 직접 붙지 않고 FastAPI 서버(backend/)만 호출한다.
실행 (backend 서버를 먼저 켠 뒤):  streamlit run app.py
API 주소는 환경변수 API_URL 또는 왼쪽 위 사이드바에서 바꾼다(기본 http://127.0.0.1:8000).
"""
import base64
import html
import os
import re
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="LG Care Better", page_icon="🛡️", layout="wide", initial_sidebar_state="collapsed")

HERE = Path(__file__).resolve().parent
GR = "linear-gradient(135deg,#8f3447,#d98a9a)"
TYPE_COLORS = ["#b78476", "#8b3a45", "#b24f45", "#74465f", "#d0698a", "#8f3a00", "#946a4a", "#a05577", "#e8a080", "#e8aab8"]
RISK_KO = {"HIGH": "높음", "MID": "중간", "LOW": "낮음"}
PRODUCT_LABEL = {"의류관리기": "스타일러"}          # 화면 표기(데이터 값은 의류관리기)
PRODUCT_IMG = {"의류관리기": "p-styler", "건조기": "p-dryer", "냉장고": "p-fridge", "세탁기": "p-washer",
               "에어컨": "p-aircon", "정수기": "p-purifier"}
DEPT_TABS = ["CX혁신부서", "구독운영부서", "고객케어부서"]

PALETTE = {
    "dark": {"bg": "radial-gradient(900px 520px at 8% -8%,#4a1f2b99,transparent),radial-gradient(800px 500px at 100% 6%,#3a2a4a55,transparent),#0d0809",
             "card": "linear-gradient(145deg,rgba(255,235,240,.07),rgba(255,235,240,.02))", "inner": "rgba(0,0,0,.28)",
             "border": "rgba(217,138,154,.2)", "txt": "#f6eef0", "sub": "rgba(246,238,240,.64)", "track": "rgba(255,255,255,.1)",
             "chip": "rgba(255,255,255,.06)", "acc": "#f4a9ba", "hl": "rgba(217,138,154,.14)"},
    "light": {"bg": "#efe7e7", "card": "#ffffff", "inner": "#f6eff0", "border": "rgba(125,42,59,.28)", "txt": "#241c1e",
              "sub": "rgba(36,28,30,.66)", "track": "rgba(36,28,30,.1)", "chip": "rgba(36,28,30,.05)", "acc": "#8f3447",
              "hl": "#fbe9ee"},
}
# 라이트 모드에서는 Streamlit 기본(다크) 위젯 색을 덮어쓴다
LIGHT_FIX = """
.stApp [data-testid="stTextInputRootElement"],.stApp [data-testid="stTextAreaRootElement"],.stApp [data-testid="stTextInputRootElement"] *,.stApp [data-testid="stTextAreaRootElement"] *,.stApp [data-baseweb="input"] *,.stApp [data-baseweb="textarea"] *{background:#fff!important;color:#241c1e!important;border-color:var(--border)!important}
.stApp input::placeholder,.stApp textarea::placeholder{color:rgba(36,28,30,.45)!important;-webkit-text-fill-color:rgba(36,28,30,.45)!important}
.stApp [data-testid="stSelectbox"] *,.stApp [data-testid="stMultiSelect"] [data-baseweb="select"] *{background:#fff;color:#241c1e}
.stApp [data-testid="stButtonGroup"] button{background:#fff!important;color:#241c1e!important;border:1px solid var(--border)!important}
.stApp [data-testid="stButtonGroup"] button p{color:inherit!important}
.stApp [data-testid="stButtonGroup"] button[aria-checked="true"],.stApp [data-testid="stButtonGroup"] button[aria-pressed="true"],.stApp [data-testid="stButtonGroup"] button[kind$="Active"]{background:#fbe9ee!important;color:#8f3447!important;border-color:#8f3447!important}
.stApp [data-baseweb="input"],.stApp [data-baseweb="textarea"],.stApp [data-baseweb="select"]>div,.stApp [data-baseweb="base-input"]{background:#fff!important;border-color:var(--border)!important}
.stApp input,.stApp textarea{color:#241c1e!important;-webkit-text-fill-color:#241c1e!important}
.stApp [data-testid="stExpander"] details{background:#fff;border-color:var(--border)}
.stApp [data-testid="stExpander"] summary,.stApp [data-testid="stExpander"] summary *{color:#241c1e}
.stApp button[kind="secondary"],.stApp [data-testid="stBaseButton-secondary"]{background:#fff;color:#241c1e;border:1px solid var(--border)}
.stApp [data-testid="stSegmentedControl"] button,.stApp [data-testid="stBaseButton-segmented_control"]{background:#fff!important;color:#241c1e!important;border-color:var(--border)!important}
.stApp [data-testid="stBaseButton-segmented_controlActive"]{background:#fbe9ee!important;color:#8f3447!important;border-color:#8f3447!important}
[data-baseweb="popover"] ul,[data-baseweb="popover"] li,[data-baseweb="menu"]{background:#fff!important;color:#241c1e!important}
.stApp [data-baseweb="tag"]{background:#fbe9ee!important;color:#8f3447!important}
.stApp [data-testid="stCaptionContainer"],.stApp [data-testid="stWidgetLabel"] *{color:var(--sub)!important}
.stApp div[data-testid="stMarkdownContainer"] code{background:#fbe9ee;color:#8f3447}
"""


def build_css(light):
    pal = PALETTE["light" if light else "dark"]
    var = ";".join(f"--{k}:{v}" for k, v in pal.items())
    css = f"<style>:root{{{var}}}{BASE_CSS}{LIGHT_FIX if light else ''}</style>"
    return css.replace("\n", " ")        # 빈 줄이 있으면 마크다운이 HTML 블록을 끝내 버린다


BASE_CSS = """
.stApp{background:var(--bg);color:var(--txt)}
header[data-testid="stHeader"],#MainMenu,footer{display:none!important}
.block-container{max-width:1320px;padding-top:1.1rem;padding-bottom:4rem}
h1,h2,h3{letter-spacing:-.01em}
/* 상단 브랜드 바 */
.brand{display:flex;align-items:center;justify-content:space-between;padding:6px 4px 10px}
.brand .l{display:flex;align-items:center;gap:12px}
.logo{width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,#8f3447,#d98a9a);display:grid;place-items:center;font-weight:800;color:#fff}
.brand b{font-size:17px}.brand small{display:block;color:var(--sub);font-size:12px;margin-top:-2px}
.pill{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border-radius:999px;background:var(--chip);border:1px solid var(--border);font-size:12px;color:var(--txt)}
.dot{width:8px;height:8px;border-radius:50%;background:#34d399;display:inline-block}
/* 탭 = 상단 내비게이션 */
div[data-baseweb="tab-list"]{gap:6px;border-bottom:1px solid var(--border)}
[role="tab"]{font-weight:700;color:var(--sub)!important;padding:12px 18px}[role="tab"] *{color:inherit!important}
[role="tab"][aria-selected="true"]{color:var(--txt)!important}
div[data-baseweb="tab-highlight"]{background:var(--acc)!important;height:3px}
/* 카드 */
div[class*="st-key-card"]{background:var(--card)!important;border:1px solid var(--border)!important;border-radius:22px!important}
.eyebrow{color:var(--acc);font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;margin:14px 0 2px}
.title{font-size:36px;font-weight:800;line-height:1.2;margin:0}
.title.c{text-align:center;font-size:38px}.eyebrow.c{text-align:center}
.sub{color:var(--sub);font-size:14px;margin:6px 0 14px}
.chip{display:inline-block;padding:6px 14px;border-radius:999px;background:linear-gradient(135deg,#8f3447,#b8697a);color:#fff;font-size:12px;font-weight:700;margin:0 6px 6px 0}
.chip.o{background:var(--chip);border:1px solid var(--border);color:var(--txt)}
.kv{background:var(--inner);border-radius:14px;padding:10px 14px}
.kv small{display:block;color:var(--sub);font-size:11px}.kv b{font-size:15px}
.track{height:8px;border-radius:99px;background:var(--track);overflow:hidden}
.fill{height:100%;border-radius:99px;background:linear-gradient(90deg,#8f3447,#d98a9a)}
.brow{margin:9px 0}.brow .t{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px}.brow .t span:last-child{font-weight:700}
.big{font-size:44px;font-weight:800;color:var(--acc);line-height:1}
.avatar{width:52px;height:52px;border-radius:16px;background:linear-gradient(135deg,#8f3447,#b8697a);display:grid;place-items:center;font-size:22px;font-weight:800;color:#fff}
.persona{padding:6px 4px}.persona q{display:block;font-size:26px;font-weight:800;line-height:1.35;margin:10px 0 8px;quotes:"“" "”"}
.insights{background:linear-gradient(135deg,#8f3447cc,#b8697acc);border-radius:22px;padding:22px 26px;margin-top:14px}
.insights .e{font-size:12px;font-weight:700;letter-spacing:.12em;margin-bottom:8px}
.insights .r{display:flex;gap:18px;align-items:baseline;padding:12px 0;border-top:1px solid rgba(255,255,255,.25);font-size:16px;font-weight:600}
.insights .r:first-of-type{border-top:0}.insights .n{font-size:20px;font-weight:800}
.legend .it{display:flex;align-items:center;gap:10px;padding:6px 10px;border-radius:10px;font-size:14px}
.legend .it i{width:12px;height:12px;border-radius:4px;display:inline-block}.legend .it span.v{margin-left:auto;color:var(--sub)}
.share{display:flex;height:16px;border-radius:99px;overflow:hidden;margin:10px 0}
.pimg{width:76px;height:76px;border-radius:18px;object-fit:cover;background:#f3e8d6}
.kw{display:inline-block;margin:3px 8px;font-weight:700;color:var(--acc)}
table.log{width:100%;border-collapse:collapse;font-size:13px}table.log th{color:var(--sub);text-align:left;font-weight:600;padding:8px 10px}
table.log td{padding:11px 10px;border-top:1px solid var(--border)}.ok{color:#34d399;font-weight:700}.ng{color:#e06a6a;font-weight:700}
.hl{background:var(--hl);border-radius:12px}
/* 버튼 */
button[kind="primary"]{background:linear-gradient(135deg,#8f3447,#b8697a)!important;border:0!important;color:#fff!important;font-weight:700}
"""



LIGHT = bool(st.session_state.get("light", False))
st.markdown(build_css(LIGHT), unsafe_allow_html=True)


# ------------------------------------------------------------------ API
with st.sidebar:
    st.header("설정")
    API = st.text_input("API 주소", os.environ.get("API_URL", "http://127.0.0.1:8000")).rstrip("/")
    reviewer = st.text_input("검토자 이름", "관리자")
    st.caption("Agent 는 제안만 하고 승인·반려는 사람이 합니다. 발송은 시뮬레이션이라 action_log 에만 기록돼요.")


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


@st.cache_data(show_spinner=False)
def img_uri(name):
    p = HERE / "assets" / f"{name}.png"
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


def won(v):
    return "-" if v is None else f"{int(round(float(v))):,}원"


def pct(v, digits=1):
    return "-" if v is None else f"{float(v) * 100:.{digits}f}%"


def esc(v):
    return html.escape(str(v if v is not None else ""))


def md_bold(s):
    """'**강조**' 를 <b> 로(나머지는 이스케이프)."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(s))


def head(eyebrow, title, sub="", center=False):
    c = " c" if center else ""
    st.markdown(f'<div class="eyebrow{c}">{esc(eyebrow)}</div><div class="title{c}">{esc(title)}</div>'
                + (f'<div class="sub{c}">{esc(sub)}</div>' if sub else ""), unsafe_allow_html=True)


def bar_rows(items, color_fn=None):
    """items: [(라벨, 값 문자열, 0~1 비율)] → 막대 목록 HTML"""
    out = []
    for label, val, ratio in items:
        out.append(f'<div class="brow"><div class="t"><span>{esc(label)}</span><span>{esc(val)}</span></div>'
                   f'<div class="track"><div class="fill" style="width:{max(2, min(100, ratio * 100)):.0f}%"></div></div></div>')
    return "".join(out)


_CARD_N = [0]


def card():
    """테마 색을 따르는 카드(고유 key 가 붙은 border 컨테이너 → CSS 로 스타일)."""
    _CARD_N[0] += 1
    return st.container(border=True, key=f"card{_CARD_N[0]}")


def dual_line(labels, a, b, name_a, name_b, w=900, h=250):
    """두 지표를 각자 눈금으로 그리는 이중 축 선 그래프(SVG, 테마 색 변수 사용)."""
    pl, pr, pt, pb = 52, 52, 24, 36
    n = len(labels)
    xs = [pl + i * (w - pl - pr) / max(n - 1, 1) for i in range(n)]

    def scale(v):
        lo, hi = min(v), max(v)
        span = (hi - lo) or 1
        return [pt + (h - pt - pb) * (1 - (x - lo) / span) for x in v], lo, hi

    ya, la, ha = scale(a)
    yb, lb, hb = scale(b)
    grid = "".join(f'<line x1="{pl}" x2="{w - pr}" y1="{pt + (h - pt - pb) * k / 3:.0f}" y2="{pt + (h - pt - pb) * k / 3:.0f}" '
                   f'style="stroke:var(--track)"/>' for k in range(4))
    pa = " ".join(f"{x:.0f},{y:.0f}" for x, y in zip(xs, ya))
    pb_ = " ".join(f"{x:.0f},{y:.0f}" for x, y in zip(xs, yb))
    dots = "".join(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="4" style="fill:var(--acc)"/>' for x, y in zip(xs, ya))
    xl = "".join(f'<text x="{x:.0f}" y="{h - 12}" text-anchor="middle" font-size="12" style="fill:var(--sub)">{esc(lab)}</text>'
                 for x, lab in zip(xs, labels))
    axis = (f'<text x="{pl - 8}" y="{pt + 4}" text-anchor="end" font-size="11" style="fill:var(--sub)">{ha:g}</text>'
            f'<text x="{pl - 8}" y="{h - pb + 4}" text-anchor="end" font-size="11" style="fill:var(--sub)">{la:g}</text>'
            f'<text x="{w - pr + 8}" y="{pt + 4}" font-size="11" style="fill:var(--sub)">{hb:g}</text>'
            f'<text x="{w - pr + 8}" y="{h - pb + 4}" font-size="11" style="fill:var(--sub)">{lb:g}</text>')
    legend = (f'<span style="margin-right:18px"><b style="color:var(--acc)">━</b> {esc(name_a)} (왼쪽 축)</span>'
              f'<span><b style="color:var(--txt)">┅</b> {esc(name_b)} (오른쪽 축)</span>')
    return (f'<svg viewBox="0 0 {w} {h}" width="100%">{grid}'
            f'<polyline points="{pb_}" fill="none" stroke-width="2.5" stroke-dasharray="6 5" style="stroke:var(--txt)"/>'
            f'<polyline points="{pa}" fill="none" stroke-width="3" style="stroke:var(--acc)"/>{dots}{xl}{axis}</svg>'
            f'<div style="font-size:13px;color:var(--sub);margin-top:4px">{legend}</div>')


def html_table(df, max_rows=200):
    """DataFrame 을 테마 색을 따르는 HTML 표로(st.dataframe 은 라이트/다크 전환이 안 돼서)."""
    head_ = "".join(f"<th>{esc(c)}</th>" for c in df.columns)
    rows = "".join("<tr>" + "".join(f"<td>{esc(v)}</td>" for v in r) + "</tr>" for r in df.head(max_rows).itertuples(index=False))
    st.markdown(f'<div style="overflow-x:auto"><table class="log"><tr>{head_}</tr>{rows}</table></div>', unsafe_allow_html=True)


def pre(obj):
    import json
    st.markdown(f'<pre style="background:var(--inner);color:var(--txt);border-radius:12px;padding:12px;font-size:12px;'
                f'overflow:auto;max-height:260px">{esc(json.dumps(obj, ensure_ascii=False, indent=1))}</pre>', unsafe_allow_html=True)


def type_label(type_id, names):
    return f"{type_id} {names.get(type_id, '')}".strip() if type_id else "-"


# ------------------------------------------------------------------ 헤더
health = get("/health")
if health is None:
    st.error(f"API 서버({API})에 연결할 수 없어요. backend 에서 `uvicorn api.main:app --port 8000` 을 먼저 실행하세요.")
    st.stop()
llm_on = health.get("llm") == "openai"
ins = get("/insights") or {}
hb, hs, ht = st.columns([5, 4, 1.4], vertical_alignment="center")
hb.markdown('<div class="brand"><div class="l"><div class="logo">C</div><div><b>LG Care Better</b>'
            '<small>구독 이탈 방어 AI Agent</small></div></div></div>', unsafe_allow_html=True)
hs.markdown(f'<div style="text-align:right"><span class="pill"><span class="dot"></span>API 연결됨 · 기준일 {esc(ins.get("as_of", "-"))} · '
            f'{esc(health["db"])} · 문구 {"AI" if llm_on else "템플릿"}</span></div>', unsafe_allow_html=True)
ht.toggle("☀️ 라이트", key="light")

catalog = get("/catalog") or {"types": [], "solutions": []}
TYPE_NAMES = {t["type_id"]: t["type_name"] for t in catalog["types"]}
TYPE_ORDER = [t["type_id"] for t in catalog["types"]][:10] or [f"T{i:02d}" for i in range(1, 11)]
DEPTS = get("/departments") or []
DEPT_NAMES = [d["name"] for d in DEPTS]

tabs = st.tabs(["전체 현황", "고객 케어", "제품 분석", "팀 분석", "설문 분석", "신규 유형", "조치 이력"])
tab_ov, tab_care, tab_prod, tab_team, tab_survey, tab_new, tab_log = tabs


# ================================================================== 전체 현황
def donut_svg(counts, labels, size=460):
    total = sum(counts) or 1
    r, w, cx = size / 2 - 30, 58, size / 2
    circ = 2 * 3.14159265 * r
    segs, off = [], 0.0
    for i in sorted(range(len(counts)), key=lambda i: -counts[i]):
        ln = counts[i] / total * circ
        segs.append(f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="{TYPE_COLORS[i % 10]}" stroke-width="{w}" '
                    f'stroke-dasharray="{max(ln - 3, 0):.1f} {circ - max(ln - 3, 0):.1f}" stroke-dashoffset="{-off:.1f}" '
                    f'transform="rotate(-90 {cx} {cx})"><title>{esc(labels[i])} {counts[i]:,}명</title></circle>')
        off += ln
    top = max(range(len(counts)), key=lambda i: counts[i])
    center = (f'<text x="{cx}" y="{cx - 46}" text-anchor="middle" style="fill:var(--acc)" font-size="13">고위험 고객 분포</text>'
              f'<text x="{cx}" y="{cx - 12}" text-anchor="middle" style="fill:var(--txt)" font-size="24" font-weight="800">{esc(labels[top])}</text>'
              f'<text x="{cx}" y="{cx + 36}" text-anchor="middle" style="fill:var(--txt)" font-size="44" font-weight="800">{counts[top]:,}'
              f'<tspan font-size="16"> 명</tspan></text>'
              f'<text x="{cx}" y="{cx + 62}" text-anchor="middle" style="fill:var(--sub)" font-size="13">가장 큰 유형 · {counts[top] / total * 100:.1f}%</text>')
    return f'<svg viewBox="0 0 {size} {size}" width="100%" style="max-width:{size}px">{"".join(segs)}{center}</svg>'


with tab_ov:
    head("더 나은 LG 베스트 케어, LG Care Better", "어떤 고객이, 어떤 이유로 떠날까", center=True)
    yt = ins.get("ytypes") or {}
    if yt:
        year = st.segmented_control("가입 연도", ["전체"] + sorted(yt), default="전체", label_visibility="collapsed")
        counts = [sum(v[i] for v in yt.values()) for i in range(10)] if year in (None, "전체") else yt[year]
        names = [TYPE_NAMES.get(t, t) for t in TYPE_ORDER] + [""] * 10
        total = sum(counts) or 1
        left, right = st.columns([3, 2], vertical_alignment="center")
        left.markdown(donut_svg(counts, names), unsafe_allow_html=True)
        legend = "".join(
            f'<div class="it"><i style="background:{TYPE_COLORS[i % 10]}"></i>{esc(names[i])}'
            f'<span class="v">{counts[i]:,}명 · {counts[i] / total * 100:.1f}%</span></div>'
            for i in sorted(range(10), key=lambda i: -counts[i]))
        right.markdown(f'<div class="legend">{legend}</div>', unsafe_allow_html=True)
        st.caption("고·중위험 고객의 1순위 이탈 유형 분포 · 도넛 위에 마우스를 올리면 유형별 인원을 볼 수 있어요.")
    ov = get("/overview")
    if ov:
        c = st.columns(5)
        for col, (k, v) in zip(c, [("전체 구독", f"{ov['total']:,}"), ("평균 이탈 확률", pct(ov["avg_prob"])),
                                   ("🔴 높음", f"{ov['risk'].get('HIGH', 0):,}"), ("🟠 중간", f"{ov['risk'].get('MID', 0):,}"),
                                   ("🟢 낮음", f"{ov['risk'].get('LOW', 0):,}")]):
            col.markdown(f'<div class="kv"><small>{k}</small><b style="font-size:24px">{v}</b></div>', unsafe_allow_html=True)
        st.caption("※ 모든 데이터는 합성 데이터이며, 이탈 확률·유형은 모델 예측 결과입니다.")


# ================================================================== 고객 케어
def render_plan(res, d):
    """AI 추천 솔루션 패널: 조치안 + 승인/반려."""
    run_id = res["run_id"]
    plan, meta = res["plan"], res["meta"]
    lim = plan.get("limits") or {}
    src = f"AI {meta['model']}" if meta["llm_used"] == "Y" else "기본 문구(템플릿)"
    st.caption(f"run #{run_id} · {src} · 모드 {meta['mode']} · 결과 {meta['outcome']}"
               + (f" · {meta['fallback_reason']}" if meta.get("fallback_reason") else ""))
    st.markdown(f'<div class="sub">{esc(plan.get("summary"))} · 합계 {won(plan.get("total_cost"))} / 상한 {won(lim.get("cost_limit"))} · '
                f'최대 {lim.get("max_actions")}개 · 90일 누적 {won(lim.get("spent_90d"))}</div>', unsafe_allow_html=True)
    done = {a["solution_id"] for a in res.get("logged_actions", []) if a["status"] != "반려"}
    if not plan["actions"]:
        st.info("조건을 만족하는 조치가 없어요. 아래 '규칙 판단 근거'에서 이유를 확인하세요.")
    for act in plan["actions"]:
        sid = act["solution_id"]
        key = f"{run_id}_{sid}"
        with card():
            st.markdown(f"**{act['solution_name']}** `{sid}` · {type_label(act['type_id'], TYPE_NAMES)} · "
                        f"대상 **{act['target']}** · 비용 {won(act['offer_cost'])}" + (" · 📣 광고성" if act["message_kind"] == "ad" else ""))
            st.caption(f"선정 이유: {act['why']}")
            cust, memo, depts = act.get("customer_message"), act.get("internal_note"), []
            if cust is not None:
                cust_edit = st.text_area("고객 알림톡 문구 (수정 가능)", cust, key=f"c_{key}", height=140)
                if act.get("buttons"):
                    st.caption("버튼: " + " · ".join(act["buttons"]))
            if memo is not None:
                memo_edit = st.text_area("협업 전달 메모 (수정 가능)", memo, key=f"m_{key}", height=100)
                fwd = res.get("forward")
                defaults = [x for x in (fwd.get("depts", []) if isinstance(fwd, dict) else []) if x in DEPT_NAMES]
                depts = st.multiselect("전달 부서", DEPT_NAMES, default=defaults, key=f"d_{key}")
            note = st.text_input("결정 메모 (선택)", key=f"n_{key}")
            b1, b2, b3 = st.columns([1, 1, 2])
            if sid in done:
                b1.success("기록됨")
            elif b1.button("✅ 승인", key=f"ok_{key}", type="primary"):
                msgs = []
                if cust is not None:
                    msgs.append(call("POST", f"/runs/{run_id}/approve", json={
                        "solution_ids": [sid], "part": "customer", "message_text": cust_edit,
                        "decision_note": note or None, "edited": cust_edit != cust}))
                if memo is not None:
                    msgs.append(call("POST", f"/runs/{run_id}/approve", json={
                        "solution_ids": [sid], "part": "internal", "internal_memo": memo_edit,
                        "forward_depts": depts, "decision_note": note or None, "edited": memo_edit != memo}))
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
            html_table(pd.DataFrame(rt["eligible"])[["solution_id", "solution_name", "priority", "target", "offer_cost", "why"]])
        if rt["excluded"]:
            html_table(pd.DataFrame(rt["excluded"]))
    if res.get("messages"):
        with st.expander("고객에게 가는 알림톡 (합본 미리보기)"):
            for m in res["messages"]:
                st.markdown(f"**{m['title']}** · {m['channel']}" + (" · 광고" if m["ad_yn"] == "Y" else ""))
                st.text(m["message_text"])


def render_customer(sub_id):
    d = call("GET", f"/subscriptions/{sub_id}")
    if not d:
        return
    sb, cu, pr, ft = d["subscription"], d["customer"], d["prediction"], d["features"]
    types = d["types"]
    voc = d["recent_voc"]
    quote = voc[0]["summary_text"] if voc else "최근 접수된 문의가 없어요."
    top_name = type_label(types[0]["type_id"], TYPE_NAMES) if types else "유형 없음"
    with card():
        st.markdown(f'<div class="persona"><span class="eyebrow" style="margin:0">PERSONA</span> '
                    f'<span class="chip o">{esc(top_name)}</span><q>{esc(quote)}</q>'
                    f'<span class="sub">{esc(cu.get("customer_name"))} · {esc(sb["subscription_id"])} · 최근 VOC 기준</span></div>',
                    unsafe_allow_html=True)
    with card():
        name = cu.get("customer_name") or "?"
        st.markdown(
            f'<div style="display:flex;gap:16px;align-items:center"><div class="avatar">{esc(name[0])}</div>'
            f'<div style="flex:1"><div style="font-size:24px;font-weight:800">{esc(name)}</div>'
            f'<div class="sub" style="margin:0">{esc(sb["subscription_id"])} · {esc(cu.get("age_group"))} · {esc(cu.get("region_sido"))}</div></div>'
            f'<div style="text-align:right"><small style="color:var(--sub)">30일 내 이탈 위험도</small>'
            f'<div class="big">{round(pr["churn_probability"] * 100)}%</div></div></div>'
            f'<div class="track" style="margin:14px 0"><div class="fill" style="width:{pr["churn_probability"] * 100:.0f}%"></div></div>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px">'
            f'<div class="kv"><small>가입 상품</small><b>{esc(sb["product_category"])} {esc(sb["model_name"])}</b></div>'
            f'<div class="kv"><small>월 요금</small><b>{won(sb["monthly_fee"])}</b></div>'
            f'<div class="kv"><small>잔여 약정</small><b>{esc(ft.get("days_to_end", "-"))}일</b></div>'
            f'<div class="kv"><small>케어 유형</small><b>{esc(sb["care_type"])}</b></div>'
            f'<div class="kv"><small>알림·광고 동의</small><b>{esc(cu.get("notify_consent_yn"))} · {esc(cu.get("ad_consent_yn"))}</b></div></div>'
            f'<div style="margin-top:12px">' + "".join(
                f'<span class="chip">유형 {esc(t["type_id"][1:])} · {esc(TYPE_NAMES.get(t["type_id"], ""))} · {round(t["contribution_share"] * 100)}%</span>'
                for t in types) + '</div>', unsafe_allow_html=True)

    shap_col, ai_col = st.columns([1, 1])
    with shap_col, card():
        st.markdown("**판단 근거 · SHAP**")
        feats = [(t["type_id"], f) for t in types for f in (t.get("top_features") or [])]
        mx = max([abs(f.get("shap") or 0) for _, f in feats] or [1])
        st.markdown(bar_rows([(f"{f.get('label') or f['feature']}  ·  유형 {tid[1:]} · 값 {f.get('value')}",
                               f"{f.get('shap', 0):+.2f}", abs(f.get("shap") or 0) / mx) for tid, f in feats]),
                    unsafe_allow_html=True)
        if voc:
            st.markdown("**최근 VOC**")
            html_table(pd.DataFrame(voc).rename(columns={"contact_date": "접수일", "category": "분류", "summary_text": "요약"}))
    with ai_col, card():
        plans = st.session_state.setdefault("plans", {})
        st.markdown("**AI 추천 솔루션**")
        p1, p2 = st.columns(2)
        use_llm = st.toggle("AI 로 문구 작성", value=llm_on, disabled=not llm_on,
                            help="꺼져 있으면 기본(템플릿) 문구를 써요. 서버에 OpenAI 키가 있어야 켤 수 있어요.")
        if p1.button("AI 추천 생성", type="primary", width="stretch"):
            with st.spinner("Rule Engine → 문구 작성 → 검증 중..."):
                res = call("POST", f"/subscriptions/{sub_id}/plan", timeout=180, json={"use_llm": use_llm})
            if res:
                plans[sub_id] = res
        if d["runs"] and p2.button("최근 조치안 불러오기", width="stretch"):
            res = call("GET", f"/runs/{d['runs'][0]['run_id']}")
            if res:
                plans[sub_id] = res
        if sub_id in plans:
            fresh = call("GET", f"/runs/{plans[sub_id]['run_id']}", quiet=True)    # 승인 기록 반영
            render_plan(fresh or plans[sub_id], d)
        else:
            st.caption("위 버튼을 누르면 Rule Engine 후보 안에서 Agent 가 조치와 문구를 만들어요.")
        with st.expander("🤝 협업 부서에 직접 전달 (Agent 제안 없이 상담자가 요청)"):
            fd = st.multiselect("전달 부서", DEPT_NAMES, key=f"fd_{sub_id}")
            fn = st.text_input("메모", key=f"fn_{sub_id}")
            if st.button("전달 기록", key=f"fb_{sub_id}"):
                r = call("POST", f"/subscriptions/{sub_id}/forward", json={
                    "depts": fd, "note": fn, "run_id": plans[sub_id]["run_id"] if sub_id in plans else None})
                if r:
                    st.success(r["note"])
    with st.expander("계산된 특징(features) · 조치 이력"):
        pre(ft)
        if d["actions"]:
            html_table(pd.DataFrame(d["actions"])[["action_id", "created_at", "solution_id", "target", "status", "message_text"]])


with tab_care:
    if "flash" in st.session_state:
        kind, text = st.session_state.pop("flash")
        getattr(st, kind)(text)
    head("CUSTOMER CARE", "고객별 DCX 리포트 · 원스톱 조치")
    left, right = st.columns([1, 3], gap="large")
    with left:
        with card():
            risk = st.segmented_control("위험도", ["전체", "HIGH", "MID", "LOW"], default="전체", label_visibility="collapsed")
            q = st.text_input("검색", placeholder="이름 · 고객번호 · 구독 ID", label_visibility="collapsed")
            params = {"limit": 60}
            if risk and risk != "전체":
                params["risk"] = risk
            else:
                params["risk"] = "HIGH,MID,LOW"
            if q.strip():
                params["q"] = q.strip()
            lst = call("GET", "/subscriptions", params=params) or {"total": 0, "items": []}
            st.caption(f"고객 목록 · {len(lst['items'])} / {lst['total']:,}")
            if "sel" not in st.session_state and lst["items"]:
                st.session_state["sel"] = lst["items"][0]["subscription_id"]
            with st.container(height=640, border=False):
                for it in lst["items"]:
                    on = it["subscription_id"] == st.session_state.get("sel")
                    label = f"{it['customer_name']}  ·  {round(it['churn_probability'] * 100)}%\n{(it.get('top_type_id') or '-')[1:]} {it.get('top_type_name') or '유형 없음'}"
                    if st.button(label, key=f"pick_{it['subscription_id']}", type="primary" if on else "secondary", width="stretch"):
                        st.session_state["sel"] = it["subscription_id"]
                        st.rerun()
            manual = st.text_input("구독 ID 직접 입력", placeholder="예: S028507")
            if manual.strip():
                st.session_state["sel"] = manual.strip()
    with right:
        if st.session_state.get("sel"):
            render_customer(st.session_state["sel"])


# ================================================================== 제품 분석
with tab_prod:
    head("PRODUCT INSIGHT", "제품별 DCX 리포트", "6대 제품군의 구독 점유율 · 이탈 위험 분포 · 최근 3개월 · 주요 이탈 유형 · VOC 키워드를 한 화면에서 비교합니다.")
    prods = (ins.get("product") or {})
    ovp = {p["name"]: p for p in ((get("/overview") or {}).get("products") or [])}
    tot = sum(p["n"] for p in prods.values()) or 1
    if prods:
        order = sorted(prods, key=lambda n: -prods[n]["n"])
        shade = ["#7d2a3b", "#9a3d50", "#b8697a", "#d98a9a", "#e8aab8", "#f4c3cd"]
        seg = "".join(f'<div style="width:{prods[n]["n"] / tot * 100:.1f}%;background:{shade[i % 6]}"></div>' for i, n in enumerate(order))
        lg = "".join(f'<span style="margin-right:16px;font-size:13px"><i style="display:inline-block;width:9px;height:9px;border-radius:50%;'
                     f'background:{shade[i % 6]};margin-right:6px"></i>{esc(PRODUCT_LABEL.get(n, n))} <b>{prods[n]["n"] / tot * 100:.0f}%</b></span>'
                     for i, n in enumerate(order))
        with card():
            st.markdown(f'<b>구독 점유율</b><div class="share">{seg}</div>{lg}', unsafe_allow_html=True)
        for row in [order[i:i + 3] for i in range(0, len(order), 3)]:
            cols = st.columns(3)
            for col, n in zip(cols, row):
                p, o = prods[n], ovp.get(n, {})
                with col, card():
                    hi, mid = (o.get("high", 0) / p["n"] * 100 if p["n"] else 0), (o.get("mid", 0) / p["n"] * 100 if p["n"] else 0)
                    months = "".join(f'<div style="display:flex;justify-content:space-between;font-size:13px;margin:4px 0">'
                                     f'<span style="color:var(--sub)">{esc(m["m"])}</span><span>신규 <b>{m["new"]}명</b></span>'
                                     f'<span style="color:var(--acc)">해지문의 <b>{m["cancel_voc"]}건</b></span></div>' for m in p["months"])
                    tt = bar_rows([(f"{t + 1}. {TYPE_NAMES.get(f'T{t + 1:02d}', '')}", f"{s}%", s / 100) for t, s in p["top_types"]])
                    mxk = max([c for _, c in p["kw"]] or [1])
                    kws = "".join(f'<span class="kw" style="font-size:{12 + 12 * c / mxk:.0f}px;opacity:{.55 + .45 * c / mxk:.2f}">{esc(k)}</span>'
                                  for k, c in p["kw"][:10])
                    st.markdown(
                        f'<div style="display:flex;gap:14px;align-items:center"><img class="pimg" src="{img_uri(PRODUCT_IMG.get(n, ""))}">'
                        f'<div style="flex:1"><b style="font-size:22px">{esc(PRODUCT_LABEL.get(n, n))}</b>'
                        f'<div class="sub" style="margin:0">구독 {p["n"]:,}건 · 점유율</div></div>'
                        f'<b style="font-size:22px;color:var(--acc)">{p["n"] / tot * 100:.0f}%</b></div>'
                        f'<div class="track" style="margin:12px 0 4px;display:flex"><div style="width:{hi:.1f}%;background:#8f3447"></div>'
                        f'<div style="width:{mid:.1f}%;background:#d98a9a"></div></div>'
                        f'<small style="color:var(--sub)">위험 HIGH {hi:.0f}% · MID {mid:.0f}% · 방문 정시율 {p["ontime_pct"]}% · 재방문 {p["revisit_pct"]}%</small>'
                        f'<div style="margin-top:14px;font-size:13px;font-weight:700">최근 3개월 신규 구독 / 해지 문의(VOC)</div>{months}'
                        f'<div style="margin-top:12px;font-size:13px;font-weight:700">주요 이탈 유형</div>{tt}'
                        f'<div style="margin-top:12px;font-size:13px;font-weight:700">VOC 문의 유형 (크기 = 접수 건수)</div><div>{kws}</div>',
                        unsafe_allow_html=True)


# ================================================================== 팀 분석
with tab_team:
    team = ins.get("team")
    head("TEAM INSIGHT · DEPARTMENT DCX REPORT", "부서별 DCX 리포트", "① 부서 맞춤 인사이트 → ② 핵심 부서 심층 리포트 순서로 이어집니다.")
    if not team:
        st.info("팀 분석 데이터를 불러오지 못했어요.")
    else:
        dept = st.segmented_control("부서", DEPT_TABS, default=DEPT_TABS[0], label_visibility="collapsed") or DEPT_TABS[0]
        blk = team["dept"][DEPT_TABS.index(dept)]
        g = blk["g1"]
        with card():
            st.markdown(f"**{esc(dept)} · 맞춤 분석**  ·  {esc(g['t'])}")
            st.markdown(dual_line(team["months"], g["A"], g["B"], g["a"], g["b"]), unsafe_allow_html=True)
        ai_tag = " · ✨ AI 해석" if blk.get("ai") else ""
        st.markdown('<div class="insights"><div class="e">KEY INSIGHTS · 3줄 요약' + ai_tag + '</div>' + "".join(
            f'<div class="r"><span class="n">{i}</span><span>{md_bold(line)}</span></div>' for i, line in enumerate(blk["ins"], 1))
            + "</div>", unsafe_allow_html=True)
        st.markdown(f'<div class="eyebrow">{esc(team["month_label"])} · {esc(dept)}</div><b style="font-size:20px">이번 달 부서별 심층 분석 리포트</b>',
                    unsafe_allow_html=True)
        for title, day, tags, body in blk["rpt"]:
            with st.expander(f"{title}  ·  {day}"):
                st.markdown(" ".join(f"`{t}`" for t in tags))
                st.text(body)
        with st.expander("구독영업·전략 등 심층 리포트 (전체)"):
            for rb in team["rdept"]:
                st.markdown(f"**{rb['title']}**")
                for line in rb["ins"]:
                    st.markdown(f"- {line}")
                for sec in rb.get("secs") or []:
                    st.markdown(f"**{sec[0]}**")
                    st.text(sec[1])


# ================================================================== 설문 분석
with tab_survey:
    head("SURVEY INSIGHT", "설문 분석", "발송 · 응답 현황과 불편했던 점, 원하는 조치, 새 솔루션 아이디어를 모아 봅니다.")
    eda = get("/survey/eda")
    if eda and eda.get("ready"):
        c = st.columns(4)
        for col, (k, v, hero) in zip(c, [("발송", f"{eda['sent']:,}", False), ("응답", f"{eda['responded']:,}", False),
                                         ("응답률", f"{eda['response_rate']}%", True), ("완료율", f"{eda['completion_rate']}%", False)]):
            bg = "linear-gradient(135deg,#fbd9e1,#f4a9ba)" if hero else "var(--inner)"
            fg = "#2a1018" if hero else "var(--txt)"
            col.markdown(f'<div class="kv" style="background:{bg};color:{fg}"><small style="color:{fg}">{k}</small>'
                         f'<b style="font-size:26px">{v}</b></div>', unsafe_allow_html=True)
        a, b, c3 = st.columns(3)
        for col, key, title in ((a, "issues", "불편했던 점"), (b, "wanted", "원하는 조치"), (c3, "newsol", "새 솔루션 아이디어")):
            with col, card():
                items = sorted(eda[key]["items"], key=lambda x: -x["n"])
                mx = items[0]["pct"] if items else 1
                st.markdown(f"**{title}** · 응답 {eda[key]['n']:,}", unsafe_allow_html=True)
                st.markdown(bar_rows([(x["label"], f"{x['pct']}% · {x['n']}명", x["pct"] / mx) for x in items[:8]]), unsafe_allow_html=True)
        d1, d2 = st.columns(2)
        with d1, card():
            st.markdown("**설문 종류별 응답률**")
            st.markdown(bar_rows([(x["label"], f"{x['pct']}% ({x['resp']}/{x['n']})", x["pct"] / 40) for x in eda["by_type"]]),
                        unsafe_allow_html=True)
            st.markdown("**현재 상태**")
            st.markdown(bar_rows([(x["label"], f"{x['pct']}% · {x['n']}명", x["pct"] / 40) for x in eda["status"]["items"]]),
                        unsafe_allow_html=True)
        with d2, card():
            st.markdown("**자주 나온 주관식 응답**")
            for i, x in enumerate(eda["open_texts"], 1):
                cls = ' class="hl"' if i <= 3 else ""
                st.markdown(f'<div{cls} style="padding:8px 12px;margin:4px 0;font-size:{15 if i <= 3 else 13}px"><b style="color:var(--acc)">{i}</b> '
                            f'&nbsp; {esc(x["text"])} <span style="float:right;opacity:.7">{x["n"]}건</span></div>', unsafe_allow_html=True)
    else:
        st.info("설문 데이터가 아직 적재되지 않았어요.")


# ================================================================== 신규 유형
with tab_new:
    head("NEW TYPES", "신규 이탈 유형 후보", "설문에서 반복해 나타난 불만을 후보로 만들고, 관리자가 채택하면 비활성 상태로 등록됩니다.")
    props = call("GET", "/proposals", quiet=True)
    if props:
        cn = props["counts"]
        status = st.segmented_control("상태", ["검토중", "채택", "기각"], default="검토중", label_visibility="collapsed",
                                      format_func=lambda s: f"{s} {cn.get(s, 0)}") or "검토중"
        shown = [p for p in props["proposals"] if p["status"] == status]
        if not shown:
            st.info("해당 상태의 후보가 없어요. 후보는 backend 에서 `python scripts/make_candidates.py --in data_in/data_out --provider mock` 으로 만들 수 있어요.")
        for p in shown:
            with card():
                st.markdown(f'<span class="chip">{esc(p["kind"])}</span> <b style="font-size:20px">{esc(p["title"])}</b>', unsafe_allow_html=True)
                st.caption(f"근거 {p['support']}건({pct(p['share'], 0)}) · {p['votes']}/{p['n_runs']}회 등장 · "
                           f"가장 가까운 기존 유형 {type_label(p.get('closest_type_id'), TYPE_NAMES)} · {p['description']}")
                for qt in p["quotes"][:3]:
                    st.markdown(f"> {qt}")
                for s_ in p["solutions"]:
                    st.write(f"💡 {s_['title']} ({s_['category']}) — {s_['message_hint']}")
                k = f"prop_{p['proposal_id']}"
                x1, x2, x3 = st.columns([1, 1, 2])
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
        if status == "채택" and props["new_solutions"]:
            st.markdown("#### 채택된 신규 솔루션 활성화")
            for s_ in props["new_solutions"]:
                with card():
                    pre(s_)
                    sid = s_.get("solution_id")
                    if sid and s_.get("active_yn") != "Y":
                        cost = st.number_input("1회 오퍼 비용(혜택제안만 필수)", min_value=0, step=1000, key=f"cost_{sid}")
                        fee = st.selectbox("요금 영향", ["없음", "증가", "감소"], key=f"fee_{sid}")
                        if st.button("활성화", key=f"act_{sid}"):
                            if call("POST", f"/solutions/{sid}/activate", json={"offer_cost": int(cost), "fee_impact": fee}):
                                st.rerun()


# ================================================================== 조치 이력
with tab_log:
    head("ACTION HISTORY", "조치 이력", "상담자가 승인·반려·전달한 모든 조치가 시간순으로 기록됩니다. 모든 발송은 시뮬레이션입니다.")
    acts = call("GET", "/actions", params={"limit": 200})
    if acts is not None:
        def kind(a):
            if a["status"] == "반려":
                return "반려"
            return "부서 전달" if a["target"] == "LG 내부 전달" else "알림톡 발송"
        for a in acts:
            a["_k"] = kind(a)
        cnt = {k: sum(1 for a in acts if a["_k"] == k) for k in ("알림톡 발송", "부서 전달", "반려")}
        flt = st.segmented_control("필터", ["전체", "알림톡 발송", "부서 전달", "반려"], default="전체", label_visibility="collapsed",
                                   format_func=lambda k: f"{k} {len(acts) if k == '전체' else cnt[k]}건") or "전체"
        rows = [a for a in acts if flt == "전체" or a["_k"] == flt]
        with card():
            if not rows:
                st.caption("아직 기록된 조치가 없어요. 고객 케어 탭에서 조치안을 승인해 보세요.")
            else:
                body = "".join(
                    f'<tr><td>{esc(str(a["created_at"])[5:16].replace("-", "."))}</td>'
                    f'<td><b>{esc(a.get("customer_name"))}</b> ({esc(a["subscription_id"])})</td><td>{esc(a["_k"])}</td>'
                    f'<td>{esc(a["solution_id"])}</td><td>{esc(str(a["message_text"])[:46])}…</td><td>{esc(a.get("forward_depts") or "-")}</td>'
                    f'<td>{won(a["offer_cost"])}</td><td class="{"ng" if a["_k"] == "반려" else "ok"}">'
                    f'{"반려" if a["_k"] == "반려" else "완료 (시뮬레이션)"}</td></tr>' for a in rows)
                st.markdown('<table class="log"><tr><th>일시</th><th>고객</th><th>조치 종류</th><th>솔루션</th><th>내용</th>'
                            f'<th>전달 부서</th><th>비용</th><th>상태</th></tr>{body}</table>', unsafe_allow_html=True)
