"""Rule Engine — "무엇을 해도 되는가"를 정한다. LLM 은 여기서 통과한 후보만 고를 수 있다.
각 솔루션마다 '발동 조건' 함수가 있고, 맞으면 (True, 근거문장) 을 돌려준다.
근거문장은 LLM 과 화면에 그대로 보여 주는 '왜 이 조치인가'다. 숫자 기준은 [가정]이며 팀 확정 필요."""
import datetime as dt

from config import settings
from agent_v1.context import RISK_ORDER


def _cat(f, *names):
    return sum(f['voc_by_category_90d'].get(n, 0) for n in names)


def _ok(cond, why):
    return (True, why) if cond else (False, '')


# solution_id -> fn(ctx) -> (bool, 근거)
def _r_t01_2(c):
    d = c['features']['promo_days_to_end']
    return _ok(d is not None and 0 <= d <= 60, f"프로모션 할인이 {d}일 뒤 종료")


def _r_t01_3(c):
    s = c['subscription']
    return _ok(s['discount_eligible_yn'] == 'Y' and s['discount_applied_yn'] == 'N', '받을 수 있는 할인이 아직 적용되지 않음')


def _r_t01_4(c):
    s = c['subscription']
    return _ok(s['care_type'] == '방문관리', f"현재 방문관리 케어(월 {s['monthly_fee']:,}원) 이용 중 — 자가관리로 낮출 수 있음")


def _r_t01_5(c):
    m = c['features']['elapsed_months']
    return _ok(m >= 12, f"구독 {m}개월 유지 고객")


def _r_t02_1(c):
    return _ok(c['subscription']['care_type'] != '없음', '케어 이용 중 — 해지 시 청구 기준 사전 안내 필요')


def _r_t02_2(c):
    n = _cat(c['features'], '해지문의', '이사문의', '위약금문의')
    return _ok(n >= 1, f"최근 90일 해지·이사·위약금 문의 {n}건")


def _r_t03_1(c):
    d = c['features']['days_to_end']
    return _ok(d is not None and 0 <= d <= 150, f"약정 만료까지 {d}일 (4~5개월 전 선택지 안내 시점)")


def _r_t03_2(c):
    d = c['features']['days_to_end']
    return _ok(c['subscription']['new_model_released_yn'] == 'Y' and d is not None and 0 <= d <= 240,
               f"신모델 출시됨, 약정 만료까지 {d}일")


def _r_t03_3(c):
    return _ok(c['subscription']['model_discontinued_yn'] == 'Y', f"이용 모델 {c['subscription']['model_name']} 단종")


def _r_t04_1(c):
    r = c['features']['usage_drop_ratio']
    return _ok(r is not None and r >= 0.3, f"최근 4주 사용량이 직전 4주 대비 {int(r * 100) if r else 0}% 감소")


def _r_t04_2(c):
    r = c['features']['usage_drop_ratio']
    return _ok(r is not None and r >= 0.3, '사용 감소 — 미사용 기능 리포트를 내부 전달')


def _r_t04_3(c):
    f = c['features']
    return _ok(c['subscription']['care_type'] != '없음' and (f['days_since_last_care'] is None or f['days_since_last_care'] > 150),
               '케어 혜택이 있는데 최근 케어 이력이 없음')


def _r_t04_4(c):
    return _ok(c['features']['elapsed_months'] <= 3, f"가입 {c['features']['elapsed_months']}개월차 — 체험 해지 가능 기간 안내")


def _r_t05_1(c):
    f = c['features']
    return _ok(_cat(f, '케어결과불만') >= 1 or f['low_sat_visit_cnt'] >= 1, '케어 결과 불만/저평점 발생 — 표준 체크리스트 점검 대상')


def _r_t05_2(c):
    d = c['features']['last_visit_done_days_ago']
    return _ok(d is not None and d <= 60, f"{d}일 전 케어 완료 — 결과 리포트 발송 가능")


def _r_t05_3(c):
    f = c['features']
    return _ok(f['low_sat_visit_cnt'] >= 1 or _cat(f, '케어결과불만') >= 1,
               f"케어 저평점 {f['low_sat_visit_cnt']}건 / 케어결과불만 {_cat(f, '케어결과불만')}건")


def _r_t06_1(c):
    f = c['features']
    return _ok(f['reschedule_cnt_180d'] >= 1 or _cat(f, '일정변경요청') >= 1,
               f"일정 변경 {f['reschedule_cnt_180d']}회, 변경 요청 문의 {_cat(f, '일정변경요청')}건")


def _r_t06_2(c):
    return _ok(c['features']['upcoming_visit_cnt'] >= 1, f"예정된 방문 {c['features']['upcoming_visit_cnt']}건 — 예약 확정서 발송")


def _r_t06_3(c):
    f = c['features']
    return _ok(c['customer']['weekday_daytime_available_yn'] == 'N' or _cat(f, '일정변경요청') >= 1,
               '평일 낮 방문이 어렵거나 일정 변경 요청 이력')


def _r_t06_4(c):
    f = c['features']
    return _ok(f['cancel_or_noshow_cnt_180d'] >= 1 or _cat(f, '재방문요청') >= 1,
               f"취소·미이행 {f['cancel_or_noshow_cnt_180d']}건 / 재방문 요청 {_cat(f, '재방문요청')}건")


def _r_t07_1(c):
    f = c['features']
    r = f['manager_avg_rating']
    return _ok(f['low_sat_visit_cnt'] >= 1 or (r is not None and r < 4.0), '낮은 방문 평가 또는 매니저 평점 4.0 미만')


def _r_t07_2(c):
    n = _cat(c['features'], '태도불만')
    return _ok(n >= 1, f"매니저 태도 불만 {n}건")


def _r_t07_3(c):
    s = c['subscription']
    n = _cat(c['features'], '담당자교체요청')
    return _ok(n >= 1 or (s['care_manager_id'] and s['manager_change_confirmed_yn'] == 'N' and c['features']['low_sat_visit_cnt'] >= 1),
               f"담당자 교체 요청 {n}건")


def _r_t07_4(c):
    r = c['features']['manager_avg_rating']
    return _ok(r is not None and r < 4.0, f"담당 매니저 평점 {r}")


def _r_t08_1(c):
    n = c['features']['sales_pitch_cnt_180d']
    return _ok(n >= 1, f"최근 180일 방문 중 판촉 {n}회")


def _r_t08_2(c):
    n = c['features']['sales_pitch_cnt_180d']
    return _ok(n >= 1 or _cat(c['features'], '영업불만') >= 1, '영업 불만 — 추가 혜택은 앱 안내로만 전환')


def _r_t09_1(c):
    return _ok(c['features']['as_visit_cnt_180d'] >= 1, f"최근 180일 A/S {c['features']['as_visit_cnt_180d']}회")


def _r_t09_2(c):
    f = c['features']
    return _ok(f['as_visit_cnt_180d'] >= 1 or f['repeat_symptom_cnt'] >= 1, '제품 이상 이력 — 소프트웨어 업데이트/설정 안내')


def _r_t09_3(c):
    return _ok(c['subscription']['care_type'] == '방문관리', '방문관리 고객 — 다음 방문 때 성능 점검 항목 추가')


def _r_t09_4(c):
    n = c['features']['repeat_symptom_cnt']
    return _ok(n >= 1, f"같은 증상 반복 {n}회")


def _r_t09_5(c):
    f = c['features']
    return _ok(f['repeat_symptom_cnt'] >= 1 or f['parts_delay_cnt'] >= 1, '반복 증상/부품 지연 — 모델 단위 품질 공유')


def _r_t10_1(c):
    return _ok(c['features']['open_as_cnt'] >= 1, f"진행 중 A/S {c['features']['open_as_cnt']}건")


def _r_t10_2(c):
    f = c['features']
    return _ok(f['open_as_cnt'] >= 1 or f['max_repair_days'] >= 7, f"A/S 처리 기간 최대 {f['max_repair_days']}일")


def _r_t10_3(c):
    n = c['features']['first_response_unresolved_cnt']
    return _ok(n >= 1, f"1차 응대 미해결 {n}건")


def _r_t10_4(c):
    f = c['features']
    return _ok(f['open_as_overdue'] or f['max_repair_days'] >= 10, 'A/S 지연(예정일 경과 또는 10일 이상 소요)')


RULES = {
    'S-T01-1': lambda c: (True, '구독 총비용 불신 — 총액 비교 제공'),
    'S-T01-2': _r_t01_2, 'S-T01-3': _r_t01_3, 'S-T01-4': _r_t01_4, 'S-T01-5': _r_t01_5,
    'S-T02-1': _r_t02_1, 'S-T02-2': _r_t02_2,
    'S-T03-1': _r_t03_1, 'S-T03-2': _r_t03_2, 'S-T03-3': _r_t03_3,
    'S-T04-1': _r_t04_1, 'S-T04-2': _r_t04_2, 'S-T04-3': _r_t04_3, 'S-T04-4': _r_t04_4,
    'S-T05-1': _r_t05_1, 'S-T05-2': _r_t05_2, 'S-T05-3': _r_t05_3,
    'S-T06-1': _r_t06_1, 'S-T06-2': _r_t06_2, 'S-T06-3': _r_t06_3, 'S-T06-4': _r_t06_4,
    'S-T07-1': _r_t07_1, 'S-T07-2': _r_t07_2, 'S-T07-3': _r_t07_3, 'S-T07-4': _r_t07_4,
    'S-T08-1': _r_t08_1, 'S-T08-2': _r_t08_2,
    'S-T09-1': _r_t09_1, 'S-T09-2': _r_t09_2, 'S-T09-3': _r_t09_3, 'S-T09-4': _r_t09_4, 'S-T09-5': _r_t09_5,
    'S-T10-1': _r_t10_1, 'S-T10-2': _r_t10_2, 'S-T10-3': _r_t10_3, 'S-T10-4': _r_t10_4,
}


def risk_gate(ctx):
    p = ctx['prediction']
    if not p:
        return False, '예측 결과 없음'
    ok = RISK_ORDER.get(p['risk_level'], 0) >= RISK_ORDER.get(settings.MIN_RISK, 1)
    return ok, ('' if ok else f"위험도 {p['risk_level']} — {settings.MIN_RISK} 이상만 조치")


def _blocked_by_consent(sol, cust):
    """고객에게 가는 메시지인데 동의가 없으면 막는다. (내부 전달은 해당 없음)"""
    if sol['message_kind'] == 'internal' or sol['target'] == 'LG 내부 전달':
        return None
    if cust['notify_consent_yn'] != 'Y':
        return '알림 수신 미동의'
    if sol['message_kind'] == 'ad' and cust['ad_consent_yn'] != 'Y':
        return '광고성 정보 수신 미동의'
    return None


def select_types(ctx):
    """기여 비중이 임계 이상인 유형(최소 1개는 항상: 1순위)."""
    ts = ctx['types']
    chosen = [t for t in ts if t['contribution_share'] >= settings.TYPE_SHARE_MIN]
    if not chosen and ts:
        chosen = ts[:1]
    return chosen


def _in_cooldown(sol_id, recent, today):
    # action_log 의 created_at 은 '실제 기록 시각'이므로 기준일은 데이터 기준일이 아니라 실제 오늘이다.
    t = dt.date.today()
    for a in recent:
        if a['solution_id'] == sol_id and a['status'] != 'cancelled' and a.get('created_at'):
            d = (t - dt.date.fromisoformat(str(a['created_at'])[:10])).days
            if 0 <= d < settings.COOLDOWN_DAYS:
                return True
    return False


def build_candidates(ctx, catalog):
    """catalog: solution 리스트(active). 반환: {eligible:[...], excluded:[{solution_id, reason}], gate:..}"""
    ok, why = risk_gate(ctx)
    out = {'gate_ok': ok, 'gate_reason': why, 'eligible': [], 'excluded': [], 'types_used': []}
    if not ok:
        return out
    types = select_types(ctx)
    out['types_used'] = [t['type_id'] for t in types]
    shares = {t['type_id']: t['contribution_share'] for t in types}
    for sol in catalog:
        if sol['type_id'] not in shares or sol['active_yn'] != 'Y':
            continue
        fn = RULES.get(sol['solution_id'])
        hit, reason = fn(ctx) if fn else (False, '')
        if not hit:
            out['excluded'].append({'solution_id': sol['solution_id'], 'reason': '발동 조건 아님'})
            continue
        blk = _blocked_by_consent(sol, ctx['customer'])
        if blk:
            out['excluded'].append({'solution_id': sol['solution_id'], 'reason': blk})
            continue
        if _in_cooldown(sol['solution_id'], ctx['recent_actions'], ctx['today']):
            out['excluded'].append({'solution_id': sol['solution_id'], 'reason': f'최근 {settings.COOLDOWN_DAYS}일 내 이미 제안'})
            continue
        out['eligible'].append({**{k: sol[k] for k in ('solution_id', 'type_id', 'solution_name', 'solution_category',
                                                     'target', 'message_kind', 'offer_cost', 'priority')},
                                'why': reason, 'type_share': shares[sol['type_id']]})
    # 우선순위: 유형 기여 비중 → 솔루션 우선순위 → 비용 낮은 순
    out['eligible'].sort(key=lambda x: (-x['type_share'], x['priority'], x['offer_cost']))
    # 유형별 후보 수 제한
    cnt, short = {}, []
    for e in out['eligible']:
        cnt[e['type_id']] = cnt.get(e['type_id'], 0) + 1
        if cnt[e['type_id']] <= settings.SHORTLIST_PER_TYPE:
            short.append(e)
    out['eligible'] = short
    return out
