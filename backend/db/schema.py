"""테이블 정의. 한 곳에서 정의하고 DB 종류(postgres/sqlite)에 맞는 DDL 을 만들어 준다.
원칙: synth_* 테이블은 검증용이라 Agent/모델 입력에 절대 쓰지 않는다(누수 방지)."""

# (컬럼명, 타입)  타입: T=텍스트 I=정수 R=실수 D=날짜 TS=시각
TABLES = {
    'care_manager': dict(pk=['manager_id'], cols=[
        ('manager_id', 'T'), ('manager_name', 'T'), ('region_sido', 'T'), ('avg_rating', 'R'), ('active_yn', 'T')]),
    'customer': dict(pk=['customer_id'], cols=[
        ('customer_id', 'T'), ('customer_name', 'T'), ('age_group', 'T'), ('region_sido', 'T'), ('joined_at', 'D'),
        ('thinq_app_installed', 'T'), ('notify_consent_yn', 'T'), ('ad_consent_yn', 'T'),
        ('weekday_daytime_available_yn', 'T')]),
    'subscription': dict(pk=['subscription_id'], cols=[
        ('subscription_id', 'T'), ('customer_id', 'T'), ('product_category', 'T'), ('model_name', 'T'),
        ('model_discontinued_yn', 'T'), ('new_model_released_yn', 'T'), ('start_date', 'D'),
        ('contract_months', 'I'), ('mandatory_months', 'I'), ('end_date', 'D'), ('monthly_fee', 'I'),
        ('discount_eligible_yn', 'T'), ('discount_applied_yn', 'T'), ('care_type', 'T'),
        ('care_auto_renew_yn', 'T'), ('care_renewal_date', 'D'), ('promo_discount_amount', 'I'),
        ('promo_end_date', 'D'), ('care_manager_id', 'T'), ('manager_change_confirmed_yn', 'T'), ('status', 'T')],
        idx=['customer_id']),
    'usage_thinq': dict(pk=[], cols=[
        ('subscription_id', 'T'), ('week_start_date', 'D'), ('run_count', 'I'), ('run_minutes', 'I'),
        ('app_login_days', 'I')], idx=['subscription_id']),
    'care_visit': dict(pk=[], cols=[
        ('subscription_id', 'T'), ('manager_id', 'T'), ('visit_type', 'T'), ('scheduled_date', 'D'),
        ('completed_date', 'D'), ('visit_status', 'T'), ('reschedule_cnt', 'I'), ('duration_min', 'I'),
        ('booking_lead_days', 'I'), ('sales_pitch_yn', 'T'), ('repitch_after_refusal_yn', 'T'),
        ('split_as_receipt_yn', 'T'), ('symptom_code', 'T'), ('repeat_symptom_yn', 'T'), ('parts_delay_yn', 'T'),
        ('inspection_anomaly_yn', 'T'), ('as_received_date', 'D'), ('expected_complete_date', 'D'),
        ('repair_days', 'I'), ('satisfaction_score', 'I')], idx=['subscription_id']),
    'voc_contact': dict(pk=[], cols=[
        ('subscription_id', 'T'), ('contact_date', 'D'), ('channel', 'T'), ('category', 'T'),
        ('repeat_contact_yn', 'T'), ('resolved_yn', 'T'), ('first_response_unresolved_yn', 'T'),
        ('cancel_delay_days', 'I'), ('response_hours', 'R'), ('sentiment_score', 'R'), ('summary_text', 'T')],
        idx=['subscription_id']),
    'payment': dict(pk=[], cols=[
        ('subscription_id', 'T'), ('billing_month', 'D'), ('billed_amount', 'I'), ('discount_amount', 'I'),
        ('paid_amount', 'I'), ('due_date', 'D'), ('paid_date', 'D'), ('payment_status', 'T')],
        idx=['subscription_id']),
    'notice_log': dict(pk=[], cols=[
        ('subscription_id', 'T'), ('notice_type', 'T'), ('sent_at', 'TS'), ('opened_at', 'TS')],
        idx=['subscription_id']),
    'churn_prediction': dict(pk=['subscription_id'], cols=[
        ('subscription_id', 'T'), ('as_of_date', 'D'), ('churn_probability', 'R'), ('risk_level', 'T'),
        ('risk_rank_pct', 'R'), ('model_version', 'T')], idx=['risk_level']),
    'churn_prediction_type': dict(pk=['subscription_id', 'type_rank'], cols=[
        ('subscription_id', 'T'), ('type_rank', 'I'), ('type_id', 'T'), ('contribution_share', 'R'),
        ('top_features_json', 'T')]),
    # --- 마스터 ---
    'churn_type': dict(pk=['type_id'], cols=[
        ('type_id', 'T'), ('type_name', 'T'), ('group_name', 'T'), ('description', 'T')]),
    'solution_catalog': dict(pk=['solution_id'], cols=[
        ('solution_id', 'T'), ('type_id', 'T'), ('solution_name', 'T'), ('solution_category', 'T'),
        ('solution_detail', 'T'), ('target', 'T'), ('message_kind', 'T'), ('offer_cost', 'I'),
        ('sample_message', 'T'), ('priority', 'I'), ('active_yn', 'T')], idx=['type_id']),
    # --- Agent 산출물 ---
    'agent_run': dict(auto='run_id', pk=['run_id'], cols=[
        ('subscription_id', 'T'), ('created_at', 'TS'), ('llm_used', 'T'), ('model', 'T'),
        ('plan_json', 'T')], idx=['subscription_id']),
    'action_log': dict(auto='action_id', pk=['action_id'], cols=[
        ('run_id', 'I'), ('subscription_id', 'T'), ('solution_id', 'T'), ('type_id', 'T'),
        ('target', 'T'), ('message_kind', 'T'), ('offer_cost', 'I'), ('message_text', 'T'),
        ('status', 'T'), ('created_at', 'TS'),
        # 상담자 처리 내용(2026-10): 반려 사유·메모, 협업 전달 부서(쉼표 구분), 상담자가 AI 초안을 고쳤는지 Y/N
        ('decision_note', 'T'), ('forward_depts', 'T'), ('memo_edited_yn', 'T')], idx=['subscription_id', 'solution_id']),
    # --- 신규 유형 후보(설문 기반) · 팀원 feedback 단계 ---
    'survey_send': dict(pk=['survey_id'], cols=[
        ('survey_id', 'I'), ('subscription_id', 'T'), ('action_batch_id', 'T'), ('survey_type', 'T'),
        ('sent_at', 'TS'), ('sent_by', 'T'), ('responded_yn', 'T')], idx=['subscription_id']),
    'survey_response': dict(pk=['response_id'], cols=[
        ('response_id', 'I'), ('survey_id', 'I'), ('completed_yn', 'T'), ('status_code', 'T'),
        ('submitted_at', 'TS')], idx=['survey_id']),
    'survey_answer': dict(pk=[], cols=[
        ('response_id', 'I'), ('question_id', 'T'), ('answer_code', 'T'), ('other_text', 'T')], idx=['response_id']),
    'ai_proposal': dict(auto='proposal_id', pk=['proposal_id'], cols=[
        ('proposal_kind', 'T'), ('related_type_id', 'T'), ('title', 'T'), ('description', 'T'),
        ('evidence_json', 'T'), ('status', 'T'), ('reviewed_by', 'T'), ('reviewed_at', 'TS'),
        ('created_at', 'TS')], idx=['status']),
    'catalog_ext': dict(pk=['solution_id'], cols=[
        ('solution_id', 'T'), ('type_id', 'T'), ('row_json', 'T'), ('active_yn', 'T'), ('created_at', 'TS')]),
    # --- 검증용(Agent 입력 금지) ---
    'synth_churn_label': dict(pk=['subscription_id'], cols=[
        ('subscription_id', 'T'), ('as_of_date', 'D'), ('churn_30d', 'I'), ('cancel_requested_date', 'D')]),
    'synth_truth_type': dict(pk=[], cols=[
        ('subscription_id', 'T'), ('type_id', 'T'), ('activation_strength', 'R'), ('is_primary_yn', 'T')]),
}

ORDER = list(TABLES)

_PG = {'T': 'TEXT', 'I': 'INTEGER', 'R': 'DOUBLE PRECISION', 'D': 'DATE', 'TS': 'TIMESTAMP'}
_SL = {'T': 'TEXT', 'I': 'INTEGER', 'R': 'REAL', 'D': 'TEXT', 'TS': 'TEXT'}

# CSV 로 적재하는 테이블 -> 파일명
CSV_TABLES = {
    'care_manager': 'care_manager.csv', 'customer': 'customer.csv', 'subscription': 'subscription.csv',
    'usage_thinq': 'usage_thinq.csv', 'care_visit': 'care_visit.csv', 'voc_contact': 'voc_contact.csv',
    'payment': 'payment.csv', 'notice_log': 'notice_log.csv', 'churn_prediction': 'churn_prediction.csv',
    'churn_prediction_type': 'churn_prediction_type.csv', 'synth_churn_label': 'synth_churn_label.csv',
    'synth_truth_type': 'synth_truth_type.csv',
}
# 파일이 있으면 적재하고, 없으면 건너뛰는 테이블(설문 합성 데이터)
OPTIONAL_CSV = {'survey_send': 'survey_send.csv', 'survey_response': 'survey_response.csv',
                'survey_answer': 'survey_answer.csv'}
# 예전 폴더(스텁 예측)도 읽을 수 있게 대체 파일명
CSV_FALLBACK = {'churn_prediction': 'churn_prediction_stub.csv', 'churn_prediction_type': 'churn_prediction_type_stub.csv'}


def ddl(kind):
    """kind: 'postgres' | 'sqlite' -> CREATE 문 목록"""
    tmap = _PG if kind == 'postgres' else _SL
    out = []
    for name in ORDER:
        t = TABLES[name]
        lines = []
        auto = t.get('auto')
        if auto:
            lines.append(f'{auto} ' + ('BIGSERIAL' if kind == 'postgres' else 'INTEGER') + ' PRIMARY KEY' +
                         ('' if kind == 'postgres' else ' AUTOINCREMENT'))
        for c, ty in t['cols']:
            lines.append(f'{c} {tmap[ty]}')
        if t['pk'] and not auto:
            lines.append('PRIMARY KEY (' + ', '.join(t['pk']) + ')')
        out.append(f'CREATE TABLE IF NOT EXISTS {name} (\n  ' + ',\n  '.join(lines) + '\n)')
        for col in t.get('idx', []):
            out.append(f'CREATE INDEX IF NOT EXISTS ix_{name}_{col} ON {name}({col})')
    return out


def columns(name):
    return [c for c, _ in TABLES[name]['cols']]


def coltype(name, col):
    return dict(TABLES[name]['cols'])[col]


def create_all(s):
    for stmt in ddl(s.kind):
        s.execute(stmt)


def existing_columns(s, name):
    if s.kind == 'sqlite':
        return [r[1] for r in s.execute(f'PRAGMA table_info({name})').fetchall()]
    return [r['column_name'] for r in s.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?",
        (name,)).fetchall()]


def migrate(s):
    """이미 만들어진 DB 에 새로 생긴 컬럼만 덧붙인다(데이터는 그대로). 없는 테이블은 건드리지 않는다. 추가한 컬럼 목록을 돌려준다."""
    tmap = _PG if s.kind == 'postgres' else _SL
    added = []
    for name in ORDER:
        have = existing_columns(s, name)
        if not have:
            continue
        for c, ty in TABLES[name]['cols']:
            if c not in have:
                s.execute(f'ALTER TABLE {name} ADD COLUMN {c} {tmap[ty]}')
                added.append(f'{name}.{c}')
    return added


def drop_all(s):
    for name in reversed(ORDER):
        s.execute(f'DROP TABLE IF EXISTS {name}')
