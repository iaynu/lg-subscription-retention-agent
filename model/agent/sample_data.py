"""PostgreSQL 시연·통합 시험용 샘플 데이터(가설 기반 합성 데이터, 8개 구독, 기준일 2026-09-30).

DDL(sql/schema_postgres.sql)과 시드(sql/seed_solution_catalog.sql)가 적재된 DB에 넣는다.
구독마다 의도한 경로가 있다(테스트 tests/test_sql_source.py가 그대로 확인):
- S100001 T04+T01, 남은 케어 1회·미적용 할인·광고 동의 → 남은 케어 안내(알림톡, ▶ 항목) + 놓친 할인(친구톡 광고)
- S100002 T06+T07, 미이행 방문·예정 방문·광고 미동의 → 정보성 알림톡 2건이 한 통으로 합쳐지고 협업 전달 초안 생성
- S100003 T10, 열린 A/S·1차 응대 미해결 → 전담자 배정(내부 전달) + A/S 진행 안내, 예상 수리 기간이 없어 대체품 안내는 제외
- S100004 T07, 3일 전 방문 → 방문 평가 안내 1건(매니저 교체 확정 값이 없어 교체 안내는 제외)
- S100005 MID·유형 없음 → 만족도 조사 / S100006 LOW 25% → 설문 추천 / S100007 T08, 조건 맞는 솔루션 없음 → 만족도 조사로 대체
"""
from __future__ import annotations
import datetime as dt, json
from sqlalchemy import text

AS_OF = dt.date(2026, 9, 30)

PRODUCTS = [  # 제품군, 구조, 요금 하한·상한, 계약 개월, 케어 기본, 점검 주기, 고장률, 주당 가동, 비중 [가정]
    ("의류관리기", "구독형", 28000, 42000, 60, "N", 12, 0.010, 5, 0.222),
    ("건조기", "구독형", 30000, 45000, 72, "N", 12, 0.012, 4, 0.179),
    ("냉장고", "구독형", 52000, 78000, 60, "N", 12, 0.008, 7, 0.173),
    ("세탁기", "구독형", 33000, 49000, 72, "N", 12, 0.012, 5, 0.156),
    ("에어컨", "구독형", 57000, 85000, 60, "N", 12, 0.010, 3, 0.150),
    ("정수기", "렌탈형", 31000, 46000, 48, "Y", 4, 0.015, 14, 0.120),
]
CUSTOMERS = [  # id, 가상 이름, 연령대, 지역, 가입일, 앱, 알림 동의, 광고 동의
    ("C100001", "김하늘", "30대", "서울", "2022-11-15", "Y", "Y", "Y"),
    ("C100002", "이도윤", "40대", "경기", "2024-03-01", "Y", "Y", "N"),
    ("C100003", "박서준", "30대", "인천", "2023-06-01", "Y", "Y", "Y"),
    ("C100004", "최유나", "50대", "부산", "2025-02-10", "N", "Y", "N"),
    ("C100005", "정민재", "20대", "서울", "2025-05-01", "Y", "Y", "Y"),
    ("C100006", "한지우", "30대", "대전", "2024-08-01", "Y", "Y", "N"),
    ("C100007", "오세린", "40대", "광주", "2024-01-10", "Y", "Y", "Y"),
    ("C100008", "신아라", "30대", "대구", "2024-05-20", "Y", "Y", "N"),
]
SUBS = [  # id, 고객, 제품, 시작, 계약, 의무, 종료, 월요금, 할인대상, 할인적용, 케어, 자동연장, 케어갱신일
    ("S100001", "C100001", "정수기", "2022-11-15", 48, 36, "2026-11-15", 39900, "Y", "N", "방문관리", "Y", "2026-11-15"),
    ("S100002", "C100002", "에어컨", "2024-03-01", 60, 36, "2029-03-01", 62000, "N", "N", "방문관리", "Y", "2027-03-01"),
    ("S100003", "C100003", "세탁기", "2023-06-01", 72, 36, "2029-06-01", 45000, "N", "N", "방문관리", "Y", "2027-06-01"),
    ("S100004", "C100004", "냉장고", "2025-02-10", 60, 36, "2030-02-10", 58000, "N", "N", "방문관리", "Y", "2027-02-10"),
    ("S100005", "C100005", "건조기", "2025-05-01", 72, 36, "2031-05-01", 36000, "N", "N", "자가관리", "N", None),
    ("S100006", "C100006", "의류관리기", "2024-08-01", 60, 36, "2029-08-01", 31000, "N", "N", "방문관리", "Y", "2026-08-01"),
    ("S100007", "C100007", "정수기", "2024-01-10", 48, 36, "2028-01-10", 34000, "N", "N", "방문관리", "Y", "2027-01-10"),
    ("S100008", "C100008", "에어컨", "2024-05-20", 60, 36, "2029-05-20", 61000, "N", "N", "방문관리", "Y", "2027-05-20"),
]
MANAGER_CHANGE = {"S100004"}                                      # 담당 매니저 교체 확정(S-T07-D 트리거)
INSPECTION_ANOMALY = {("S100008", "2026-09-22")}                  # 점검 이상 감지된 정기점검(S-T09-C 트리거)
AS_DATES = {("S100003", "2026-10-03"): ("2026-09-28", "2026-10-07")}   # A/S 접수일·예상 완료일 → 예상 수리 기간 9일(S-T10-D 트리거)
VISITS = [  # 구독, 유형, 예정일, 완료일, 상태, 소요분, 증상
    ("S100001", "정기점검", "2025-12-10", "2025-12-10", "완료", 35, None),
    ("S100001", "정기점검", "2026-04-12", "2026-04-12", "완료", 32, None),
    ("S100002", "정기점검", "2025-08-20", "2025-08-20", "완료", 40, None),
    ("S100002", "정기점검", "2026-09-12", None, "미이행", None, None),
    ("S100002", "정기점검", "2026-10-06", None, "예정", None, None),
    ("S100003", "AS수리", "2026-09-05", "2026-09-05", "완료", 50, "E01"),
    ("S100003", "AS수리", "2026-09-20", "2026-09-20", "완료", 45, "E01"),
    ("S100003", "AS수리", "2026-10-03", None, "예정", None, "E01"),
    ("S100004", "정기점검", "2026-09-27", "2026-09-27", "완료", 30, None),
    ("S100004", "정기점검", "2026-10-20", None, "예정", None, None),
    ("S100007", "정기점검", "2026-09-10", "2026-09-10", "완료", 28, None),
    ("S100008", "정기점검", "2026-09-22", "2026-09-22", "완료", 40, None),
]
VOCS = [  # 구독, 날짜, 카테고리, 해결, 1차 미해결
    ("S100001", "2026-08-15", "요금문의", "Y", "N"),
    ("S100001", "2026-09-10", "요금문의", "Y", "N"),
    ("S100002", "2026-09-13", "일정변경요청", "Y", "N"),
    ("S100003", "2026-09-22", "응대불만", "N", "Y"),
]
PREDS = [  # 구독, 확률, 등급, 순위, [(순위, 유형, 비중, [(피처, 값, shap)])]
    ("S100001", 0.82, "HIGH", 0.02, [(1, "T04", 0.62, [("zero_use_weeks_12w", 7, 0.41), ("run_count_trend_12w", -0.6, 0.22)]),
                                     (2, "T01", 0.38, [("unapplied_discount_yn", "Y", 0.25), ("voc_fee_cnt_90d", 2, 0.14)])]),
    ("S100002", 0.74, "HIGH", 0.05, [(1, "T06", 0.70, [("visit_noshow_cnt_365d", 1, 0.33), ("reschedule_sum_365d", 3, 0.18)]),
                                     (2, "T07", 0.30, [("satisfaction_avg_180d", 2.8, 0.16)])]),
    ("S100003", 0.79, "HIGH", 0.04, [(1, "T10", 1.00, [("first_response_unresolved_yn", "Y", 0.37), ("repair_days_avg_365d", 9, 0.21)])]),
    ("S100004", 0.41, "MID", 0.13, [(1, "T07", 1.00, [("satisfaction_avg_180d", 2.0, 0.31), ("manager_avg_rating", 3.1, 0.12)])]),
    ("S100005", 0.38, "MID", 0.16, []),
    ("S100006", 0.12, "LOW", 0.25, []),
    ("S100007", 0.71, "HIGH", 0.07, [(1, "T08", 1.00, [("sales_pitch_cnt_180d", 3, 0.28)])]),
    ("S100008", 0.68, "HIGH", 0.09, [(1, "T09", 1.00, [("as_cnt_365d", 3, 0.30)])]),
]


def load_sample(engine, model_version: str = "v0-sample"):
    with engine.begin() as c:
        for p in PRODUCTS:
            c.execute(text("""INSERT INTO product_profile VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i,:j) ON CONFLICT DO NOTHING"""),
                      dict(zip("abcdefghij", p)))
        c.execute(text("INSERT INTO care_manager (manager_id, manager_name, region_sido, avg_rating) VALUES ('M1001','홍길동','서울',4.2) ON CONFLICT DO NOTHING"))
        for cid, name, age, region, joined, app, notify, ad in CUSTOMERS:
            c.execute(text("""INSERT INTO customer (customer_id, customer_name, age_group, region_sido, joined_at, thinq_app_installed,
                               notify_consent_yn, ad_consent_yn) VALUES (:a,:b,:c,:d,:e,:f,:g,:h)"""),
                      dict(a=cid, b=name, c=age, d=region, e=joined, f=app, g=notify, h=ad))
        for sid, cid, prod, start, cm, mm, end, fee, de, da, care, auto, renew in SUBS:
            c.execute(text("""INSERT INTO subscription (subscription_id, customer_id, product_category, model_name, start_date, contract_months,
                               mandatory_months, end_date, monthly_fee, discount_eligible_yn, discount_applied_yn, care_type,
                               care_auto_renew_yn, care_renewal_date, care_manager_id, manager_change_confirmed_yn)
                               VALUES (:sid,:cid,:prod,'MODEL-1',:start,:cm,:mm,:end,:fee,:de,:da,:care,:auto,:renew,'M1001',:mc)"""),
                      dict(mc='Y' if sid in MANAGER_CHANGE else 'N', sid=sid, cid=cid, prod=prod, start=start, cm=cm, mm=mm, end=end, fee=fee, de=de, da=da, care=care, auto=auto, renew=renew))
            for k in range(12):                                   # 최근 12개월 납부
                bm = dt.date(2025, 10, 1) if k == 0 else _add_month(dt.date(2025, 10, 1), k)
                if bm < dt.date.fromisoformat(start).replace(day=1): continue
                c.execute(text("""INSERT INTO payment (subscription_id, billing_month, billed_amount, paid_amount, due_date, paid_date, payment_status)
                                   VALUES (:s,:b,:f,:f,:b,:b,'정상')"""), dict(s=sid, b=bm, f=fee))
        for sid, vt, sch, done, st, dur, sym in VISITS:
            c.execute(text("""INSERT INTO care_visit (subscription_id, manager_id, visit_type, scheduled_date, completed_date, visit_status,
                               duration_min, symptom_code, repeat_symptom_yn, inspection_anomaly_yn, as_received_date, expected_complete_date)
                               VALUES (:s,'M1001',:vt,:sch,:done,:st,:dur,:sym,:rep,:an,:rc,:ec)"""),
                      dict(an="Y" if (sid, sch) in INSPECTION_ANOMALY else "N", rc=AS_DATES.get((sid, sch), (None, None))[0],
                           ec=AS_DATES.get((sid, sch), (None, None))[1], s=sid, vt=vt, sch=sch, done=done, st=st, dur=dur, sym=sym, rep="Y" if sym else "N"))
        for sid, d, cat, res, fru in VOCS:
            c.execute(text("""INSERT INTO voc_contact (subscription_id, contact_date, channel, category, resolved_yn, first_response_unresolved_yn)
                               VALUES (:s,:d,'전화',:c,:r,:f)"""), dict(s=sid, d=d, c=cat, r=res, f=fru))
        week = dt.date(2026, 7, 6)                                 # S100001: 7월 27일 주 이후 가동 없음
        while week <= AS_OF:
            c.execute(text("INSERT INTO usage_thinq (subscription_id, week_start_date, run_count, run_minutes) VALUES ('S100001', :w, :r, :m)"),
                      dict(w=week, r=10 if week <= dt.date(2026, 7, 27) else 0, m=120 if week <= dt.date(2026, 7, 27) else 0))
            week += dt.timedelta(days=7)
        for sid, prob, level, rank, types in PREDS:
            pid = c.execute(text("""INSERT INTO churn_prediction (subscription_id, as_of_date, churn_probability, risk_level, risk_rank_pct, model_version)
                                     VALUES (:s, :a, :p, :l, :r, :v) RETURNING prediction_id"""),
                            dict(s=sid, a=AS_OF, p=prob, l=level, r=rank, v=model_version)).scalar_one()
            for tr, tid, share, feats in types:
                c.execute(text("""INSERT INTO churn_prediction_type (prediction_id, type_rank, type_id, contribution_share, top_features_json)
                                   VALUES (:p, :r, :t, :s, :j)"""),
                          dict(p=pid, r=tr, t=tid, s=share, j=json.dumps([{"feature": f, "value": v, "shap": sh} for f, v, sh in feats], ensure_ascii=False)))


def _add_month(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)
