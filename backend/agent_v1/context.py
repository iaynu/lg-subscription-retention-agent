"""한 구독(subscription)에 대한 Agent 입력 컨텍스트를 만든다.
- 기준일(today)은 예측 테이블의 as_of_date (더미데이터는 시간이 고정이라 실제 오늘 날짜를 쓰면 안 됨)
- synth_* 테이블은 절대 읽지 않는다(누수 방지)."""
import datetime as dt
import json

RISK_ORDER = {'LOW': 0, 'MID': 1, 'HIGH': 2}


def _d(v):
    if not v:
        return None
    return dt.date.fromisoformat(str(v)[:10])


def _days(a, b):
    return None if a is None or b is None else (a - b).days


def build_context(s, subscription_id):
    sub = s.fetchone('SELECT * FROM subscription WHERE subscription_id=?', (subscription_id,))
    if not sub:
        return None
    cust = s.fetchone('SELECT * FROM customer WHERE customer_id=?', (sub['customer_id'],))
    pred = s.fetchone('SELECT * FROM churn_prediction WHERE subscription_id=?', (subscription_id,))
    types = s.fetchall('SELECT p.type_rank, p.type_id, t.type_name, p.contribution_share, p.top_features_json '
                       'FROM churn_prediction_type p JOIN churn_type t ON t.type_id=p.type_id '
                       'WHERE p.subscription_id=? ORDER BY p.type_rank', (subscription_id,))
    for t in types:
        try:
            t['top_features'] = json.loads(t.pop('top_features_json') or '[]')
        except ValueError:
            t['top_features'] = []
    today = _d(pred['as_of_date']) if pred else dt.date.today()

    visits = s.fetchall('SELECT * FROM care_visit WHERE subscription_id=? ORDER BY scheduled_date', (subscription_id,))
    vocs = s.fetchall('SELECT * FROM voc_contact WHERE subscription_id=? ORDER BY contact_date', (subscription_id,))
    usage = s.fetchall('SELECT * FROM usage_thinq WHERE subscription_id=? ORDER BY week_start_date', (subscription_id,))
    notices = s.fetchall('SELECT * FROM notice_log WHERE subscription_id=? ORDER BY sent_at DESC', (subscription_id,))
    mgr = None
    if sub.get('care_manager_id'):
        mgr = s.fetchone('SELECT * FROM care_manager WHERE manager_id=?', (sub['care_manager_id'],))
    recent_actions = s.fetchall('SELECT solution_id, created_at, status FROM action_log WHERE subscription_id=? '
                                'ORDER BY created_at DESC', (subscription_id,))

    def within(rows, key, days):
        lo = today - dt.timedelta(days=days)
        return [r for r in rows if _d(r[key]) and lo <= _d(r[key]) <= today]

    v_done = [v for v in visits if v['visit_status'] in ('완료', '지연완료')]
    v180 = within(v_done, 'scheduled_date', 180)
    voc90 = within(vocs, 'contact_date', 90)
    voc_cat = {}
    for v in voc90:
        voc_cat[v['category']] = voc_cat.get(v['category'], 0) + 1
    upcoming = [v for v in visits if v['visit_status'] == '예정' and _d(v['scheduled_date']) and _d(v['scheduled_date']) >= today]
    as_open = [v for v in visits if v['visit_type'] == 'AS수리' and v['visit_status'] == '예정']
    sat = [v['satisfaction_score'] for v in v_done if v['satisfaction_score'] is not None]
    last_done = max((_d(v['completed_date']) or _d(v['scheduled_date']) for v in v_done), default=None)

    # 사용량: 최근 4주 vs 그 전 4주
    u_recent = [u['run_count'] or 0 for u in usage[-4:]]
    u_prev = [u['run_count'] or 0 for u in usage[-8:-4]]
    run_recent, run_prev = sum(u_recent), sum(u_prev)

    f = {
        'days_to_end': _days(_d(sub['end_date']), today),
        'elapsed_months': round(_days(today, _d(sub['start_date'])) / 30.4, 1),
        'days_to_care_renewal': _days(_d(sub['care_renewal_date']), today),
        'promo_days_to_end': _days(_d(sub['promo_end_date']), today),
        'voc_cnt_90d': len(voc90),
        'voc_by_category_90d': voc_cat,
        'unresolved_voc_cnt': sum(1 for v in voc90 if v['resolved_yn'] == 'N'),
        'first_response_unresolved_cnt': sum(1 for v in voc90 if v['first_response_unresolved_yn'] == 'Y'),
        'sales_pitch_cnt_180d': sum(1 for v in v180 if v['sales_pitch_yn'] == 'Y'),
        'repitch_after_refusal_cnt': sum(1 for v in v180 if v['repitch_after_refusal_yn'] == 'Y'),
        'visit_cnt_180d': len(v180),
        'days_since_last_care': _days(today, last_done),
        'reschedule_cnt_180d': sum((v['reschedule_cnt'] or 0) for v in within(visits, 'scheduled_date', 180)),
        'cancel_or_noshow_cnt_180d': sum(1 for v in within(visits, 'scheduled_date', 180) if v['visit_status'] in ('취소', '미이행')),
        'low_sat_visit_cnt': sum(1 for x in sat if x <= 2),
        'min_satisfaction': min(sat) if sat else None,
        'last_visit_done_days_ago': _days(today, last_done),
        'as_visit_cnt_180d': sum(1 for v in v180 if v['visit_type'] == 'AS수리'),
        'repeat_symptom_cnt': sum(1 for v in v180 if v['repeat_symptom_yn'] == 'Y'),
        'parts_delay_cnt': sum(1 for v in v180 if v['parts_delay_yn'] == 'Y'),
        'max_repair_days': max((v['repair_days'] or 0 for v in v180), default=0),
        'open_as_cnt': len(as_open),
        'open_as_overdue': any(_d(v['expected_complete_date']) and _d(v['expected_complete_date']) < today for v in as_open),
        'upcoming_visit_cnt': len(upcoming),
        'manager_avg_rating': mgr['avg_rating'] if mgr else None,
        'run_count_recent4w': run_recent,
        'run_count_prev4w': run_prev,
        'usage_drop_ratio': (round(1 - run_recent / run_prev, 2) if run_prev else None),
        'last_notice_days_ago': _days(today, _d(notices[0]['sent_at'])) if notices else None,
    }
    return {
        'today': today.isoformat(),
        'subscription': sub, 'customer': cust, 'prediction': pred, 'types': types,
        'manager': mgr, 'features': f, 'recent_actions': recent_actions,
        'recent_voc': [{'contact_date': v['contact_date'], 'category': v['category'], 'summary_text': v['summary_text']}
                       for v in vocs[-3:][::-1]],
    }
