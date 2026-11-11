"""Team Insight(부서별 DCX 리포트) 용 실데이터 집계.

DB 의 일반 테이블(subscription, churn_prediction, voc_contact, care_visit, payment, notice_log,
care_manager)만 읽는다. synth_* (정답 라벨) 테이블은 읽지 않는다.
모든 숫자는 DB 에서 계산하고, 문장은 그 숫자를 끼워 넣는 규칙 기반 템플릿이다(LLM 아님).
문장 안의 **강조** 표시는 화면에서 하이라이트로 바뀐다.
"""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date

PRODUCTS = ['의류관리기', '건조기', '냉장고', '세탁기', '에어컨', '정수기']  # DB 이름 (화면: 스타일러 …)
UI_NAME = {'의류관리기': '스타일러'}
TYPE_NAMES = ['구독료 부담형', '해지·이사 장벽형', '재계약·갈아타기 갈등형', '사용 빈도 저하·방치형', '케어 부실·피해형',
              '방문 일정 불편형', '매니저 품질 불만형', '영업 압박형', '제품 고장·성능 불만형', 'AS·고객센터 불만형']
BILL_CATS = ('요금문의', '총비용문의', '위약금문의', '갱신문의')
MGR_CATS = ('담당자교체요청', '태도불만')
NEG = {'케어결과불만', '태도불만', '응대불만', '영업불만', '담당자교체요청', '재방문요청'}


def _ym(d):
    return str(d)[:7]


def _add_month(ym, k):
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) + k
    return '%04d-%02d' % (t // 12, t % 12 + 1)


def _d(x):
    return date.fromisoformat(str(x)[:10])


def _pct(a, b, nd=1):
    return round(100.0 * a / b, nd) if b else 0.0


def _avg(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _x(a, b):
    """배수 표기. 분모가 0 이면 None"""
    return round(a / b, 1) if b else None


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5


def _ui(p):
    return UI_NAME.get(p, p)


def build(s, as_of):
    subs = s.fetchall(
        'SELECT s.subscription_id, s.product_category AS prod, s.start_date, s.contract_months, s.promo_end_date, '
        's.discount_eligible_yn, s.discount_applied_yn, s.care_manager_id, p.churn_probability AS prob, p.risk_level, '
        't.type_id FROM subscription s JOIN churn_prediction p ON p.subscription_id=s.subscription_id '
        'LEFT JOIN churn_prediction_type t ON t.subscription_id=s.subscription_id AND t.type_rank=1')
    voc = s.fetchall('SELECT subscription_id, contact_date, category, repeat_contact_yn, first_response_unresolved_yn '
                     'FROM voc_contact')
    visits = s.fetchall('SELECT subscription_id, manager_id, visit_type, scheduled_date, completed_date, visit_status, '
                        'reschedule_cnt, satisfaction_score, repeat_symptom_yn FROM care_visit')
    pays = s.fetchall('SELECT subscription_id, billing_month, payment_status FROM payment')
    notes = s.fetchall('SELECT subscription_id, notice_type, opened_at FROM notice_log')

    base = {r['subscription_id']: r for r in subs}
    asd = _d(as_of)
    cur = _ym(as_of)
    months = [_add_month(cur, k) for k in range(-5, 1)]
    mlab = [str(int(m[5:])) + '월' for m in months]
    prev = _add_month(cur, -1)
    month_txt = '%d년 %d월' % (int(cur[:4]), int(cur[5:7]))

    hm = lambda r: r['risk_level'] in ('HIGH', 'MID')
    tix = lambda tid: (int(str(tid)[1:]) - 1) if tid else None
    n_all = len(subs)
    hm_all = sum(1 for r in subs if hm(r))
    avg_prob_all = _avg(r['prob'] for r in subs)

    # ── 월별 시계열 ──
    voc_m, cat_m = Counter(), defaultdict(Counter)
    for v in voc:
        m = _ym(v['contact_date'])
        voc_m[m] += 1
        cat_m[v['category']][m] += 1
    voc_by_sub = defaultdict(list)
    for v in voc:
        voc_by_sub[v['subscription_id']].append(str(v['contact_date'])[:10])
    for k in voc_by_sub:
        voc_by_sub[k].sort()

    sat_m = defaultdict(list)
    vis_ok_m, vis_done_m = Counter(), Counter()
    sub_sat = defaultdict(list)
    low_sat = set()
    vt_sat = defaultdict(list)
    mgr_sat = defaultdict(list)
    for x in visits:
        m = _ym(x['scheduled_date'])
        done = x['visit_status'] in ('완료', '지연완료')
        if done:
            vis_done_m[m] += 1
            if x['visit_status'] == '완료':
                vis_ok_m[m] += 1
        sc = x['satisfaction_score']
        if done and sc is not None:
            sat_m[m].append(float(sc))
            sub_sat[x['subscription_id']].append(float(sc))
            vt_sat[x['visit_type']].append(float(sc))
            if x['manager_id']:
                mgr_sat[x['manager_id']].append(float(sc))
            if sc <= 2:
                low_sat.add(x['subscription_id'])
    pay_tot, pay_ok = Counter(), Counter()
    pay_bad = set()
    for p in pays:
        m = _ym(p['billing_month'])
        pay_tot[m] += 1
        if p['payment_status'] == '정상':
            pay_ok[m] += 1
        else:
            pay_bad.add(p['subscription_id'])

    def vm(m):
        return voc_m.get(m, 0)

    def cat_share(cats, m):
        return _pct(sum(cat_m[c].get(m, 0) for c in cats), vm(m))

    # ── 공통 그룹 비교 ──
    def grp(pred):
        a = [r for r in subs if pred(r)]
        b = [r for r in subs if not pred(r)]
        ha, hb = _pct(sum(1 for r in a if hm(r)), len(a)), _pct(sum(1 for r in b if hm(r)), len(b))
        pa, pb = _avg(r['prob'] for r in a) * 100, _avg(r['prob'] for r in b) * 100
        return {'n': len(a), 'hm': ha, 'hm_other': hb, 'p': round(pa, 1), 'p_other': round(pb, 1), 'x': _x(pa, pb)}

    hm_types = Counter(tix(r['type_id']) for r in subs if hm(r) and r['type_id'])
    hm_tot = sum(hm_types.values()) or 1
    tshare = {t: _pct(n, hm_tot) for t, n in hm_types.items()}
    prod_stat = {}
    for p in PRODUCTS:
        rs = [r for r in subs if r['prod'] == p]
        prod_stat[p] = {'n': len(rs), 'risk': _pct(sum(1 for r in rs if hm(r)), len(rs))}
    prod_rank = sorted(PRODUCTS, key=lambda p: -prod_stat[p]['risk'])

    def X(v):
        return ('%.1f배' % v) if v else '비교 불가'

    dept = []

    # ═════ 0. CX혁신부서 ═════
    sat_vals = [round(_avg(sat_m.get(m, [])), 2) for m in months]
    g_low = grp(lambda r: r['subscription_id'] in low_sat)
    xs, ys = [], []
    for sid, v in sub_sat.items():
        b = base.get(sid)
        if b:
            xs.append(_avg(v))
            ys.append(b['prob'])
    r_corr = _pearson(xs, ys)
    vt_rows = sorted(((k, _avg(v), len(v)) for k, v in vt_sat.items() if len(v) >= 30), key=lambda z: z[1])
    care_types = [4, 5, 6]
    care_share = round(sum(tshare.get(t, 0) for t in care_types), 1)
    top_t = hm_types.most_common(3)
    neg_m = {c: (cat_m[c].get(cur, 0), cat_m[c].get(prev, 0)) for c in NEG}
    neg_up = sorted(((c, a, b) for c, (a, b) in neg_m.items()), key=lambda z: -(z[1] - z[2]))
    cat_cur = Counter({c: n.get(cur, 0) for c, n in cat_m.items() if c != '기타'})

    def type_vocs(t):
        c = Counter()
        rep = tot = 0
        for v in voc:
            b = base.get(v['subscription_id'])
            if b and tix(b['type_id']) == t:
                tot += 1
                if v['category'] != '기타':
                    c[v['category']] += 1
                if v['repeat_contact_yn'] == 'Y':
                    rep += 1
        return c, _pct(rep, tot)

    rep_all = _pct(sum(1 for v in voc if v['repeat_contact_yn'] == 'Y'), len(voc))
    persona_lines = []
    for t, n in top_t:
        tr = [r for r in subs if tix(r['type_id']) == t and hm(r)]
        kw, rep = type_vocs(t)
        persona_lines.append('· 유형 %02d %s — 고·중위험 %s명(%.1f%%), 평균 예측 이탈확률 %.1f%%, 주요 문의 %s, 반복 문의 비율 %.1f%%(전체 %.1f%%)' % (
            t + 1, TYPE_NAMES[t], format(n, ','), tshare[t], _avg(r['prob'] for r in tr) * 100,
            '·'.join(k for k, _ in kw.most_common(2)) or '-', rep, rep_all))
    sat_note = ''
    if r_corr is not None:
        sat_note = '고객별 평균 만족도와 이탈 확률의 상관계수는 r=%.2f(고객 %s명)입니다.' % (r_corr, format(len(xs), ','))
    dept.append({
        'g1': {'t': '케어 만족도 vs 해지 문의 비중 추이', 'a': '케어 만족도(점)', 'b': '해지 문의 비중(%)', 'ua': '만족도 점',
               'ub': '해지문의 %', 'da': 2, 'db': 1, 'A': sat_vals, 'B': [cat_share(['해지문의'], m) for m in months]},
        'ins': [
            '케어 만족도 2점 이하를 받은 고객(%s명)의 평균 예측 이탈확률은 %.1f%%로, 그 외 고객(%.1f%%)의 **%s**' % (
                format(g_low['n'], ','), g_low['p'], g_low['p_other'], X(g_low['x'])),
            '케어 서비스 불만군(케어 부실·방문 일정·매니저 품질)이 고·중위험 고객의 **%.1f%%**, 가장 큰 유형은 %s(%.1f%%)' % (
                care_share, TYPE_NAMES[top_t[0][0]], tshare[top_t[0][0]]),
            '위험 고객 비율은 **%s %.1f%%**로 가장 높고 %s %.1f%%로 가장 낮아 격차가 %.1f%%p — 경험 개선 자원을 우선 투입할 제품군' % (
                _ui(prod_rank[0]), prod_stat[prod_rank[0]]['risk'], _ui(prod_rank[-1]), prod_stat[prod_rank[-1]]['risk'],
                prod_stat[prod_rank[0]]['risk'] - prod_stat[prod_rank[-1]]['risk'])],
        'rpt': [
            ['CX 접점별 만족도·이탈 위험 분석', str(as_of), ['접점', '만족도', '케어'],
             '\n'.join(['접점(방문 유형)별 평균 케어 만족도(5점 만점, 최근 1년 방문):'] +
                       ['· %s %.2f점 (%s건)' % (k, a, format(n, ',')) for k, a, n in vt_rows] +
                       [sat_note,
                        '만족도 2점 이하 경험 고객 %s명 중 고·중위험 %.1f%% (그 외 %.1f%%).' % (
                            format(g_low['n'], ','), g_low['hm'], g_low['hm_other'])])],
            ['VOC 키워드 월간 리포트', str(as_of), ['VOC', '키워드'],
             '\n'.join(['%s VOC는 총 %s건(전월 %s건)입니다.' % (month_txt, format(vm(cur), ','), format(vm(prev), ',')),
                        '상위 문의 유형: ' + ', '.join('%s %s건' % (c, format(n, ',')) for c, n in cat_cur.most_common(3)),
                        '부정 성격 유형의 전월 대비 변화:'] +
                       ['· %s %s건 → %s건 (%s건)' % (c, format(b, ','), format(a, ','), format(a - b, '+,')) for c, a, b in neg_up[:3]])],
            ['고객 이탈 유형별 여정 리포트', str(as_of), ['유형', '여정'],
             '\n'.join(['고·중위험 고객(%s명)의 이탈 유형 상위 3개:' % format(hm_all, ',')] + persona_lines)],
        ]})

    # ═════ 1. 구독운영부서 ═════
    promo_soon = lambda r: r['promo_end_date'] and 0 <= (_d(r['promo_end_date']) - asd).days <= 90
    g_promo = grp(promo_soon)
    notice_ren = Counter(n['subscription_id'] for n in notes if n['notice_type'] == '갱신안내')
    ren_opened = Counter(n['subscription_id'] for n in notes if n['notice_type'] == '갱신안내' and n['opened_at'])
    ren_total = sum(notice_ren.values())
    ren_open_pct = _pct(sum(ren_opened.values()), ren_total)
    g_ren2 = grp(lambda r: notice_ren.get(r['subscription_id'], 0) >= 2)
    g_ren1 = grp(lambda r: notice_ren.get(r['subscription_id'], 0) == 1)
    g_pay = grp(lambda r: r['subscription_id'] in pay_bad)
    cont = {}
    for c in sorted({r['contract_months'] for r in subs if r['contract_months']}):
        rs = [r for r in subs if r['contract_months'] == c]
        cont[c] = (len(rs), _pct(sum(1 for r in rs if hm(r)), len(rs)), _avg(r['prob'] for r in rs) * 100)
    g_unapplied = grp(lambda r: r['discount_eligible_yn'] == 'Y' and r['discount_applied_yn'] == 'N')
    bill_cur = sum(cat_m[c].get(cur, 0) for c in BILL_CATS)
    bill_prev = sum(cat_m[c].get(prev, 0) for c in BILL_CATS)
    pay_rate = [_pct(pay_ok.get(m, 0), pay_tot.get(m, 0)) for m in months]
    bills_tot = {c: sum(cat_m[c].values()) for c in BILL_CATS}
    t0share = tshare.get(0, 0)
    buckets = [('30일 이내', 0, 30), ('31~90일', 31, 90)]
    bk_lines = []
    for lab, lo, hi in buckets:
        gg = grp(lambda r, lo=lo, hi=hi: r['promo_end_date'] and lo <= (_d(r['promo_end_date']) - asd).days <= hi)
        bk_lines.append('· 프로모션 종료 %s 도래 %s명 — 고·중위험 %.1f%%' % (lab, format(gg['n'], ','), gg['hm']))
    dept.append({
        'g1': {'t': '정상 납부율 vs 요금·계약 문의 비중 추이', 'a': '정상 납부율(%)', 'b': '요금·계약 문의 비중(%)', 'ua': '납부율 %',
               'ub': '문의 %', 'da': 1, 'db': 1, 'A': pay_rate, 'B': [cat_share(BILL_CATS, m) for m in months]},
        'ins': [
            '프로모션 종료가 90일 내인 고객(%s명)의 고·중위험 비율은 %.1f%%로, 그 외(%.1f%%)의 **%s**' % (
                format(g_promo['n'], ','), g_promo['hm'], g_promo['hm_other'], X(_x(g_promo['hm'], g_promo['hm_other']))),
            '연체·미납 이력 고객(%s명)의 평균 예측 이탈확률이 %.1f%%로 정상 납부 고객(%.1f%%)의 **%s**' % (
                format(g_pay['n'], ','), g_pay['p'], g_pay['p_other'], X(g_pay['x'])),
            '구독료 부담형이 고·중위험 고객의 **%.1f%%**이며 요금·계약 관련 문의는 전월 %s건 → 이번 달 %s건' % (
                t0share, format(bill_prev, ','), format(bill_cur, ','))],
        'rpt': [
            ['약정·프로모션 만료 코호트 분석', str(as_of), ['약정', '프로모션', '코호트'],
             '\n'.join(['계약 기간별 고·중위험 비율:'] +
                       ['· %d개월 계약 %s명 — 고·중위험 %.1f%%, 평균 예측 이탈확률 %.1f%%' % (c, format(n, ','), h, p)
                        for c, (n, h, p) in cont.items()] + bk_lines)],
            ['요금·청구 이슈 리포트', str(as_of), ['요금', '청구'],
             '\n'.join(['요금·계약 관련 문의 누계: ' + ', '.join('%s %s건' % (c, format(n, ',')) for c, n in bills_tot.items()),
                        '이번 달 정상 납부율 %.1f%% (연체·미납 %.1f%%).' % (pay_rate[-1], 100 - pay_rate[-1]),
                        '할인 대상이지만 미적용 고객 %s명 — 고·중위험 %.1f%% (그 외 %.1f%%).' % (
                            format(g_unapplied['n'], ','), g_unapplied['hm'], g_unapplied['hm_other'])])],
            ['재계약(갱신) 안내 피로도 리포트', str(as_of), ['재계약', '안내'],
             '\n'.join(['갱신 안내 발송 %s건, 열람률 %.1f%%.' % (format(ren_total, ','), ren_open_pct),
                        '갱신 안내를 2회 이상 받은 고객 %s명 — 고·중위험 %.1f%% (1회 수신 %.1f%%).' % (
                            format(g_ren2['n'], ','), g_ren2['hm'], g_ren1['hm']),
                        '갱신 문의 VOC 누계 %s건.' % format(sum(cat_m['갱신문의'].values()), ',')])],
        ]})

    # ═════ 2. 고객케어부서 ═════
    ontime = [_pct(vis_ok_m.get(m, 0), vis_done_m.get(m, 0)) for m in months]
    delayed = set(x['subscription_id'] for x in visits if x['visit_status'] in ('지연완료', '미이행', '취소'))
    g_delay = grp(lambda r: r['subscription_id'] in delayed)
    mgr_voc = sum(sum(cat_m[c].values()) for c in MGR_CATS)
    st_tot = Counter()
    resched = defaultdict(list)
    for x in visits:
        if x['visit_type'] == '정기점검':
            st_tot[x['visit_status']] += 1
            resched[x['visit_status']].append(x['reschedule_cnt'] or 0)
    chk_done = sum(st_tot[k] for k in ('완료', '지연완료', '취소', '미이행'))
    mg = sorted(((m, _avg(v), len(v)) for m, v in mgr_sat.items() if len(v) >= 20), key=lambda z: z[1])
    k20 = max(1, len(mg) // 5)
    lo_g, hi_g = mg[:k20], mg[-k20:]
    mgr_gap = (_avg(a for _, a, _ in hi_g) - _avg(a for _, a, _ in lo_g)) if len(mg) >= 5 else 0
    # 케어(정기점검·AS) 완료 후 30일 내 문의 비율
    comp = [x for x in visits if x['visit_status'] in ('완료', '지연완료') and x['visit_type'] in ('정기점검', 'AS수리') and x['completed_date']]
    after = 0
    after_p = Counter()
    comp_p = Counter()
    for x in comp:
        ds = voc_by_sub.get(x['subscription_id'], [])
        c0 = str(x['completed_date'])[:10]
        c1 = str(date.fromordinal(_d(c0).toordinal() + 30))
        hit = bisect_right(ds, c1) - bisect_left(ds, c0) > 0
        bb = base.get(x['subscription_id'])
        pn = bb['prod'] if bb else None
        comp_p[pn] += 1
        if hit:
            after += 1
            after_p[pn] += 1
    as_visits = [x for x in visits if x['visit_type'] == 'AS수리' and x['visit_status'] == '완료']
    rep_rate = _pct(sum(1 for x in as_visits if x['repeat_symptom_yn'] == 'Y'), len(as_visits))
    after_rank = sorted(((p, _pct(after_p[p], comp_p[p])) for p in PRODUCTS if comp_p.get(p)), key=lambda z: -z[1])
    dept.append({
        'g1': {'t': '방문 정시율 vs 재방문 요청 문의 비중 추이', 'a': '방문 정시율(%)', 'b': '재방문 요청 문의 비중(%)', 'ua': '정시율 %',
               'ub': '재방문 %', 'da': 1, 'db': 1, 'A': ontime, 'B': [cat_share(['재방문요청'], m) for m in months]},
        'ins': [
            '방문 정시율이 %s %.1f%% → %s %.1f%%로 %+.1f%%p 변화, 재방문 요청 문의 비중은 %.1f%% → %.1f%%' % (
                mlab[0], ontime[0], mlab[-1], ontime[-1], ontime[-1] - ontime[0],
                cat_share(['재방문요청'], months[0]), cat_share(['재방문요청'], cur)),
            '방문 지연·취소·미이행을 겪은 고객(%s명)의 평균 예측 이탈확률 %.1f%%로 그 외(%.1f%%)의 **%s**' % (
                format(g_delay['n'], ','), g_delay['p'], g_delay['p_other'], X(g_delay['x'])),
            '매니저 관련 문의(담당자교체·태도불만)가 누계 **%s건**으로 전체 VOC의 %.1f%%' % (
                format(mgr_voc, ','), _pct(mgr_voc, len(voc)))],
        'rpt': [
            ['매니저별 케어 품질 편차 리포트', str(as_of), ['매니저', '품질'],
             '\n'.join(['방문 20회 이상 매니저 %d명의 평균 케어 만족도를 비교했습니다.' % len(mg),
                        '상위 20%% 평균 %.2f점, 하위 20%% 평균 %.2f점 — 격차 %.2f점.' % (
                            _avg(a for _, a, _ in hi_g), _avg(a for _, a, _ in lo_g), mgr_gap)] if len(mg) >= 5 else
                       ['매니저별 비교에 필요한 방문 수가 부족합니다.'])],
            ['방문 일정 변경·지연 분석', str(as_of), ['일정', '지연'],
             '\n'.join(['정기점검 방문 상태 (총 %s건 중 종결 건):' % format(chk_done, ',')] +
                       ['· %s %s건 (%.1f%%), 평균 일정 변경 %.1f회' % (
                           k, format(st_tot[k], ','), _pct(st_tot[k], chk_done), _avg(resched[k]))
                        for k in ('완료', '지연완료', '취소', '미이행') if st_tot[k]] +
                       ['일정변경요청 문의는 누계 %s건입니다.' % format(sum(cat_m['일정변경요청'].values()), ',')])],
            ['케어 후 재문의·재방문 리포트', str(as_of), ['재방문', '재문의'],
             '\n'.join(['케어(정기점검·AS) 완료 후 30일 내 문의가 있었던 비율 %.1f%%.' % _pct(after, len(comp)),
                        'AS 수리 중 같은 증상이 반복된 비율 %.1f%%.' % rep_rate] +
                       (['재문의 비율이 높은 제품군: ' + ', '.join('%s %.1f%%' % (_ui(p), v) for p, v in after_rank[:2])]
                        if after_rank else []))],
        ]})

    # ═════ 제품군별 3대 부서 심층 리포트 ═════
    # ── 구독영업 및 전략 ──
    r3m = [_add_month(cur, k) for k in (-2, -1, 0)]
    new3 = {p: sum(1 for r in subs if r['prod'] == p and _ym(r['start_date']) in r3m) for p in PRODUCTS}
    pr_risk = {}
    for p in PRODUCTS:
        rs = [r for r in subs if r['prod'] == p and promo_soon(r)]
        pr_risk[p] = (len(rs), _pct(sum(1 for r in rs if hm(r)), len(rs)))
    best_new = max(PRODUCTS, key=lambda p: new3[p])
    worst_pr = max(PRODUCTS, key=lambda p: pr_risk[p][1])
    avg_pr = _pct(sum(1 for r in subs if promo_soon(r) and hm(r)), sum(1 for r in subs if promo_soon(r)))
    promo_n = g_promo['n']
    new_tot = sum(new3.values())
    tot_new_all = sum(1 for r in subs if _ym(r['start_date']) in r3m)
    bill_x = _x(bill_cur, bill_prev)
    rd0 = {
        'g': {'t': '제품군별 신규 가입 vs 프로모션 종료 임박 고객 위험 비율', 'a': '최근 3개월 신규 가입(건)', 'b': '프로모션 종료 90일 내 고객 중 고·중위험(%)',
              'ua': '가입 건', 'ub': '위험 %', 'da': 0, 'db': 1,
              'B': {p: new3[p] for p in PRODUCTS}, 'L': {p: pr_risk[p][1] for p in PRODUCTS}},
        'ins': [
            '프로모션 종료 90일 내 고객 **%s명** 중 고·중위험이 %.1f%%(전체 평균 %.1f%%)' % (
                format(promo_n, ','), avg_pr, _pct(hm_all, n_all)),
            '종료 임박 고객의 위험 비율 최고는 **%s %.1f%%**, 신규 가입 최다는 %s(%s건)' % (
                _ui(worst_pr), pr_risk[worst_pr][1], _ui(best_new), format(new3[best_new], ',')),
            '요금·계약 관련 문의는 전월 %s건 → 이번 달 %s건(%s)' % (
                format(bill_prev, ','), format(bill_cur, ','), ('**%.1f배**' % bill_x) if bill_x else '비교 불가')],
        'title': '%s 구독영업·전략 심층 분석 리포트' % month_txt,
        'secs': [
            ['1. 요약', '최근 3개월 신규 가입은 %s건이며, 가장 많은 제품군은 %s(%s건)입니다. 프로모션 종료가 90일 안에 도래하는 고객은 %s명이고, 이 중 고·중위험이 %.1f%%로 전체 평균(%.1f%%)과 비교하면 %s.' % (
                format(tot_new_all, ','), _ui(best_new), format(new3[best_new], ','), format(promo_n, ','), avg_pr,
                _pct(hm_all, n_all), '차이가 큽니다' if abs(avg_pr - _pct(hm_all, n_all)) >= 5 else '비슷한 수준입니다')],
            ['2. 요금제·프로모션', '\n'.join(bk_lines + ['할인 대상이지만 미적용 고객은 %s명이며 고·중위험 비율은 %.1f%%(그 외 %.1f%%)입니다.' % (
                format(g_unapplied['n'], ','), g_unapplied['hm'], g_unapplied['hm_other'])])],
            ['3. 계약 구조', '\n'.join('%d개월 계약 %s명 — 고·중위험 %.1f%%' % (c, format(n, ','), h) for c, (n, h, p) in cont.items())],
            ['4. 제안 과제', '① 프로모션 종료 D-45에 요금 변동 사전 고지 + 대안 요금제 안내(대상: %s명) ② 위험 비율이 높은 %s 구간 우선 점검 ③ 할인 미적용 고객(%s명) 자동 적용 안내 ④ 가입 후 총 납부액 리포트 정기 발송 (※ 규칙 기반 권고안)' % (
                format(promo_n, ','), _ui(worst_pr), format(g_unapplied['n'], ','))]]}

    # ── CX(고객경험) 및 VOC 분석 ──
    pvoc = Counter()
    pneg = Counter()
    for v in voc:
        b = base.get(v['subscription_id'])
        if b:
            pvoc[b['prod']] += 1
            if v['category'] in NEG:
                pneg[b['prod']] += 1
    pneg_pct = {p: _pct(pneg[p], pvoc[p]) for p in PRODUCTS}
    top_voc_p = max(PRODUCTS, key=lambda p: pvoc[p])
    top_neg_p = max(PRODUCTS, key=lambda p: pneg_pct[p])
    voc_cur_prod = Counter()
    for v in voc:
        if _ym(v['contact_date']) == cur:
            b = base.get(v['subscription_id'])
            if b:
                voc_cur_prod[b['prod']] += 1
    rep_subs = set(v['subscription_id'] for v in voc if v['repeat_contact_yn'] == 'Y')
    unres_subs = set(v['subscription_id'] for v in voc if v['first_response_unresolved_yn'] == 'Y')
    g_rep = grp(lambda r: r['subscription_id'] in rep_subs)
    g_unres = grp(lambda r: r['subscription_id'] in unres_subs)
    care_voc_cur = sum(cat_m[c].get(cur, 0) for c in ('케어결과불만', '태도불만', '담당자교체요청'))
    care_voc_prev = sum(cat_m[c].get(prev, 0) for c in ('케어결과불만', '태도불만', '담당자교체요청'))
    rd1 = {
        'g': {'t': '제품군별 VOC 접수 건수 vs 부정 유형 비중', 'a': 'VOC 접수 건수(건)', 'b': '부정 유형 비중(%)', 'ua': 'VOC 건',
              'ub': '부정 %', 'da': 0, 'db': 1, 'B': {p: pvoc[p] for p in PRODUCTS}, 'L': {p: pneg_pct[p] for p in PRODUCTS}},
        'ins': [
            '%s VOC는 **%s건**(전월 %s건), 제품군별로는 %s가 %s건으로 최다' % (
                month_txt, format(vm(cur), ','), format(vm(prev), ','), _ui(top_voc_p), format(pvoc[top_voc_p], ',')),
            '부정 유형 비중은 **%s %.1f%%**가 가장 높음(전체 평균 %.1f%%)' % (
                _ui(top_neg_p), pneg_pct[top_neg_p], _pct(sum(pneg.values()), sum(pvoc.values()))),
            '반복 문의 이력 고객(%s명)의 평균 예측 이탈확률 %.1f%%로 그 외(%.1f%%)의 **%s**' % (
                format(g_rep['n'], ','), g_rep['p'], g_rep['p_other'], X(g_rep['x']))],
        'title': '%s CX·VOC 분석 월간 리포트' % month_txt,
        'secs': [
            ['1. 요약', '%s VOC는 총 %s건이며 전월(%s건) 대비 %s건입니다. 제품군별로는 %s(%s건)에 가장 많이 집중되었고, 부정 유형 비중은 %s %.1f%%로 가장 높습니다.' % (
                month_txt, format(vm(cur), ','), format(vm(prev), ','), format(vm(cur) - vm(prev), '+,'), _ui(top_voc_p),
                format(pvoc[top_voc_p], ','), _ui(top_neg_p), pneg_pct[top_neg_p])],
            ['2. 문의 유형 분석', '이번 달 상위 유형: ' + ', '.join('%s %s건' % (c, format(n, ',')) for c, n in cat_cur.most_common(5)) +
             '\n케어 불만 계열(케어결과불만·태도불만·담당자교체요청)은 전월 %s건 → 이번 달 %s건입니다.' % (format(care_voc_prev, ','), format(care_voc_cur, ','))],
            ['3. 이탈 연관성', '반복 문의 이력 고객 %s명은 고·중위험 %.1f%%(그 외 %.1f%%)입니다.\n1차 응대에서 해결되지 않은 이력 고객 %s명은 고·중위험 %.1f%%(그 외 %.1f%%)입니다.' % (
                format(g_rep['n'], ','), g_rep['hm'], g_rep['hm_other'], format(g_unres['n'], ','), g_unres['hm'], g_unres['hm_other'])],
            ['4. 제안 과제', '① 1차 응대 미해결·반복 문의 고객(%s명) 우선 재연락 ② %s 부정 유형 문의 급증 시 부서별 자동 알림 ③ 이탈 유형별 방어 메시지 A/B 테스트 ④ 영업불만 전용 접수 채널 검토 (※ 규칙 기반 권고안)' % (
                format(len(rep_subs | unres_subs), ','), _ui(top_neg_p))]]}

    # ── 서비스 및 케어 운영 ──
    pv_ok, pv_done, pas, prep = Counter(), Counter(), Counter(), Counter()
    for x in visits:
        b = base.get(x['subscription_id'])
        if not b:
            continue
        if x['visit_status'] in ('완료', '지연완료'):
            pv_done[b['prod']] += 1
            if x['visit_status'] == '완료':
                pv_ok[b['prod']] += 1
        if x['visit_type'] == 'AS수리':
            pas[b['prod']] += 1
            if x['repeat_symptom_yn'] == 'Y':
                prep[b['prod']] += 1
    pont = {p: _pct(pv_ok[p], pv_done[p]) for p in PRODUCTS}
    prr = {p: _pct(prep[p], pas[p]) for p in PRODUCTS}
    low_on = min(PRODUCTS, key=lambda p: pont[p])
    hi_rep = max(PRODUCTS, key=lambda p: prr[p])
    avg_on = _pct(sum(pv_ok.values()), sum(pv_done.values()))
    down = sum(1 for i in range(1, 6) if ontime[i] < ontime[i - 1])
    rd2 = {
        'g': {'t': '제품군별 방문 정시율 vs AS 재방문율', 'a': '방문 정시율(%)', 'b': 'AS 재방문율(%)', 'ua': '정시율 %', 'ub': '재방문 %',
              'da': 1, 'db': 1, 'B': pont, 'L': prr},
        'ins': [
            '%s 방문 정시율 **%.1f%%로 최저**, AS 재방문율은 %s %.1f%%로 최고' % (_ui(low_on), pont[low_on], _ui(hi_rep), prr[hi_rep]),
            '방문 지연·취소·미이행 고객의 평균 예측 이탈확률이 정상 고객 대비 **%s**' % X(g_delay['x']),
            '월별 정시율은 최근 6개월 중 **%d개월** 전월 대비 하락(%s %.1f%% → %s %.1f%%)' % (down, mlab[0], ontime[0], mlab[-1], ontime[-1])],
        'title': '%s 서비스·케어 운영 월간 리포트' % month_txt,
        'secs': [
            ['1. 요약', '전 제품군 평균 방문 정시율은 %.1f%%이며 %s(%.1f%%)가 가장 낮습니다. AS 재방문율은 %s %.1f%%로 가장 높습니다. 최근 6개월 정시율은 %s %.1f%% → %s %.1f%%입니다.' % (
                avg_on, _ui(low_on), pont[low_on], _ui(hi_rep), prr[hi_rep], mlab[0], ontime[0], mlab[-1], ontime[-1])],
            ['2. 방문 일정', '\n'.join(['정기점검 방문 상태:'] + ['· %s %s건 (%.1f%%), 평균 일정 변경 %.1f회' % (
                k, format(st_tot[k], ','), _pct(st_tot[k], chk_done), _avg(resched[k])) for k in ('완료', '지연완료', '취소', '미이행') if st_tot[k]] +
                ['지연·취소·미이행 경험 고객 %s명은 고·중위험 %.1f%%(그 외 %.1f%%)입니다.' % (
                    format(g_delay['n'], ','), g_delay['hm'], g_delay['hm_other'])])],
            ['3. 케어 품질', '매니저 상위 20%% 평균 만족도 %.2f점, 하위 20%% %.2f점으로 격차는 %.2f점입니다.\n케어 완료 후 30일 내 문의 비율은 %.1f%%입니다.' % (
                _avg(a for _, a, _ in hi_g) if mg else 0, _avg(a for _, a, _ in lo_g) if mg else 0, mgr_gap, _pct(after, len(comp)))],
            ['4. 제안 과제', '① %s 방문 일정 변경 사전 통보 및 정시율 개선 ② 지연·취소 경험 고객(%s명) 사후 케어 우선 배정 ③ 만족도 하위 매니저 동행평가·재교육 ④ AS 재방문율이 높은 %s 부품·점검 체크리스트 강화 (※ 규칙 기반 권고안)' % (
                _ui(low_on), format(g_delay['n'], ','), _ui(hi_rep))]]}

    return {'month_label': month_txt, 'months': mlab, 'dept': dept, 'rdept': [rd0, rd1, rd2]}
