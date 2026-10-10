-- ============================================================
-- 구독 이탈 방지 AI Agent : 데이터 스키마 DDL (PostgreSQL 기준)
-- 모든 데이터는 가설 기반 synthetic data (실제 LG전자 데이터 아님)
--
-- 이식성 규칙 (Oracle/PostgreSQL 공용 부분집합)
--  * 이름은 소문자 snake_case, 예약어(date, user, level, comment, number, size) 미사용
--  * ENUM 대신 VARCHAR + CHECK, 플래그는 CHAR(1) 'Y'/'N'
--  * 날짜는 DATE/TIMESTAMP, 기본키 자동생성은 IDENTITY
--  * JSON은 기본 TEXT로 저장(이식성), Agent 결과·AI 제안처럼 통째로 읽고 쓰는 문서는 JSONB. 결측은 항상 NULL (빈 문자열 사용 금지)
-- 2026-10-08: 점검 반영 — solution_catalog.customer_title·message_items(고객용 제목·▶ 항목), action_log.decision_note(반려 사유 등), survey_send.survey_type에 'no_candidate'(유형은 있으나 후보 0건이라 만족도 조사로 대체된 경우) 추가, ai_proposal.related_type_id 길이를 churn_type.type_id와 맞춤.
-- 2026-10-07(5): 외래키(FK) 제약을 모두 제거. 합성 데이터를 자주 다시 만들고 수정하므로 적재 순서·수정 제약을 없애기 위함. 관계는 아래 '논리 관계'로 문서화하고 무결성은 sql/check_integrity.sql(고아 행 검사)로 확인한다. PK·UNIQUE·NOT NULL·CHECK는 유지. 조인 컬럼 인덱스는 FK와 무관하게 직접 둔다.
-- 2026-10-07(4): churn_prediction.risk_rank_pct, agent_result(Agent 결과 저장), 설문 3테이블(survey_send/response/answer)·ai_proposal 추가, action_log.action_batch_id를 agent_result에 연결.
-- 2026-10-07(3): solution_catalog에 priority·eligibility 컬럼 추가(Rule Engine v3); action_log에 title·internal_memo·message_source 추가, action_status에 '반려' 추가; forward_depts·memo_edited_yn 추가(협업 부서 전달 화면).
-- 2026-10-07(2): 솔루션 확정 반영 — 미사용 컬럼 6개 삭제(registration_fee, registration_fee_waived_yn, contract_channel, initial_setup_completed_yn, usage_thinq.distinct_feature_cnt, remote_control_cnt); solution_catalog에 채널·광고성·전달종류 컬럼, customer.ad_consent_yn, action_log.channel 개정 (seed_solution_catalog.sql로 시드).
-- 2026-10-07: 이탈 유형 10개(T01~T10) 기준 개정 (churn_types_10.md). 유형 ID 'T01'~'T10', 유형 없음 'pseudo-type' = '유형없음'.
--   신규 신호 컬럼(care_visit/voc_contact/subscription/customer/care_manager)은 [제안]이며 팀 확정 전.
-- 생성 순서: 참조 테이블 → 핵심 테이블 → 이벤트 테이블 → 결과 테이블 → 합성 정답 테이블
-- ============================================================

-- ---------- 논리 관계 (제약 없음; 검사는 check_integrity.sql) ----------
--   solution_catalog.type_id -> churn_type.type_id
--   subscription.customer_id -> customer.customer_id
--   subscription.product_category -> product_profile.product_category
--   subscription.care_manager_id -> care_manager.manager_id
--   usage_thinq.subscription_id -> subscription.subscription_id
--   care_visit.subscription_id -> subscription.subscription_id
--   care_visit.manager_id -> care_manager.manager_id
--   voc_contact.subscription_id -> subscription.subscription_id
--   payment.subscription_id -> subscription.subscription_id
--   notice_log.subscription_id -> subscription.subscription_id
--   churn_prediction.subscription_id -> subscription.subscription_id
--   churn_prediction_type.prediction_id -> churn_prediction.prediction_id
--   churn_prediction_type.type_id -> churn_type.type_id
--   agent_result.subscription_id -> subscription.subscription_id
--   agent_result.prediction_id -> churn_prediction.prediction_id
--   action_log.action_batch_id -> agent_result.request_id
--   action_log.subscription_id -> subscription.subscription_id
--   action_log.prediction_id -> churn_prediction.prediction_id
--   action_log.type_id -> churn_type.type_id
--   action_log.solution_id -> solution_catalog.solution_id
--   survey_send.subscription_id -> subscription.subscription_id
--   survey_send.action_batch_id -> agent_result.request_id
--   survey_response.survey_id -> survey_send.survey_id
--   survey_answer.response_id -> survey_response.response_id
--   ai_proposal.related_type_id -> churn_type.type_id
--   synth_churn_label.subscription_id -> subscription.subscription_id
--   synth_truth_type.subscription_id -> subscription.subscription_id
--   synth_truth_type.type_id -> churn_type.type_id
--   synth_truth_topic.subscription_id -> subscription.subscription_id

-- ---------- 1. 참조(마스터) 테이블 ----------

CREATE TABLE product_profile (
    product_category        VARCHAR(20)  PRIMARY KEY,           -- 예: 의류관리기, 건조기, 냉장고, 세탁기, 에어컨, 정수기
    contract_structure      VARCHAR(10)  NOT NULL CHECK (contract_structure IN ('구독형', '렌탈형')),
    fee_min                 NUMERIC(10,0) NOT NULL,             -- 월 요금 하한 [가정]
    fee_max                 NUMERIC(10,0) NOT NULL,             -- 월 요금 상한 [가정]
    contract_months         SMALLINT     NOT NULL,              -- 제품별 계약기간(개월): 60/72/60/72/60/48 [확정]
    care_included_default   CHAR(1)      NOT NULL CHECK (care_included_default IN ('Y', 'N')),
    visit_interval_months   SMALLINT,                           -- 정기점검 주기(개월), 방문관리 없으면 NULL
    base_fault_rate_monthly NUMERIC(6,4) NOT NULL,              -- 월 고장 접수 기본 확률 [가정]
    base_usage_per_week     NUMERIC(6,2) NOT NULL,              -- 주당 가동 횟수 기본값 [가정]
    subscriber_weight       NUMERIC(5,4) NOT NULL               -- 더미 생성 시 제품 비중 [가정]
);

CREATE TABLE care_manager (
    manager_id    VARCHAR(10) PRIMARY KEY,
    manager_name  VARCHAR(50) NOT NULL,
    region_sido   VARCHAR(20) NOT NULL,
    avg_rating    NUMERIC(3,2) CHECK (avg_rating BETWEEN 1 AND 5),  -- 담당 매니저 평균 평점 (T07 신호) [제안]
    active_yn     CHAR(1)     NOT NULL DEFAULT 'Y' CHECK (active_yn IN ('Y', 'N'))
);

CREATE TABLE churn_type (
    type_id           VARCHAR(4)   PRIMARY KEY,                 -- 'T01' ... 'T10', 유형 없음 pseudo-type은 '유형없음' (큰 틀 분류 없음, 평면 10개)
    type_name         VARCHAR(60)  NOT NULL,
    description       VARCHAR(300)
);

CREATE TABLE solution_catalog (
    solution_id        VARCHAR(10)  PRIMARY KEY,                -- 'S-T01-A' 형식(유형 + 알림톡 보고서 순서 알파벳), 만족도 조사 'S-NT-A' [제안]
    seq_no             SMALLINT     NOT NULL,                   -- 알림톡 보고서의 솔루션 번호(1~38), 유형없음 항목은 0
    type_id            VARCHAR(4)   NOT NULL,
    solution_category  VARCHAR(10)  NOT NULL CHECK (solution_category IN ('정보제공', '담당자조치', '혜택제안', '제품조치', '의견수집')),
    solution_name      VARCHAR(80)  NOT NULL,
    solution_detail    VARCHAR(300),
    channel            VARCHAR(10)  NOT NULL CHECK (channel IN ('알림톡', '친구톡', '내부 전달', '상담·앱')),  -- 주 채널. 알림톡+내부 전달 복합은 알림톡 + internal_forward_yn='Y'
    ad_yn              CHAR(1)      NOT NULL DEFAULT 'N' CHECK (ad_yn IN ('Y', 'N')),     -- 광고성(친구톡): (광고) 표기 + customer.ad_consent_yn='Y'인 고객만 [제안]
    internal_forward_yn CHAR(1)     NOT NULL DEFAULT 'N' CHECK (internal_forward_yn IN ('Y', 'N')),  -- 담당 부서 전달 동반 여부
    forward_dept       VARCHAR(60),                             -- 전달 부서(내부 전달 동반 시)
    delivery_kind      VARCHAR(10)  NOT NULL CHECK (delivery_kind IN ('고객 메시지', '내부 전달', '상담·앱 안내', '전사 개선')),
    agent_selectable_yn CHAR(1)     NOT NULL DEFAULT 'Y' CHECK (agent_selectable_yn IN ('Y', 'N')),  -- 'N' = 전사 집계 리포트(구독자 단위 후보 아님)
    offer_cost         NUMERIC(10,0) NOT NULL DEFAULT 0,        -- 무료 서비스 범위를 넘어 회사가 추가 부담하는 금액. 0보다 큰 솔루션만 비용 상한 대상 [가정]
    fee_impact         VARCHAR(10)  NOT NULL DEFAULT '없음' CHECK (fee_impact IN ('없음', '증가', '감소')),  -- 고객 월 요금 변동 방향
    trigger_note       VARCHAR(200),                            -- 권장 발송 시점(운영 시점 조건). Agent는 판정하지 않고 직원 화면·문구에 표시 [제안]
    buttons            VARCHAR(100),                            -- 알림톡 버튼 1~3개(' / ' 구분)
    message_hint       VARCHAR(300),                            -- LLM 메시지(또는 내부 전달서) 작성 시 참고 문구
    new_policy_yn      CHAR(1)      NOT NULL DEFAULT 'N' CHECK (new_policy_yn IN ('Y', 'N')),  -- 현행 정책이 아닌 '신규 정책' 제안
    priority           SMALLINT     NOT NULL DEFAULT 50,   -- Rule Engine 정렬 순서(작을수록 먼저): 10 직접해결 20 정보 30 광고·혜택 40 내부보고 50 유형없음 90 전사개선
    eligibility        TEXT,                  -- 후보 자격 조건(JSON, [[fact,op,value(,'required')],...] AND). NULL이면 항상 후보. 'required'면 값이 없을 때 후보 제외
    customer_title     VARCHAR(60),           -- 고객용 제목(알림톡 보고서 제목을 일반화). 광고성이면 '(광고)'로 시작. 내부 전달·전사 개선은 NULL [제안]
    message_items      VARCHAR(300),          -- ▶ 항목으로 보여 줄 고객 공개 값 키(JSON 배열, agent/facts.py MESSAGE_FACT_DEFS) [제안]
    active_yn          CHAR(1)      NOT NULL DEFAULT 'Y' CHECK (active_yn IN ('Y', 'N'))
);

-- ---------- 2. 핵심 테이블 ----------

CREATE TABLE customer (
    customer_id          VARCHAR(10) PRIMARY KEY,               -- 예: C000001
    customer_name        VARCHAR(50) NOT NULL,                  -- 가상 이름
    age_group            VARCHAR(10) NOT NULL CHECK (age_group IN ('20대', '30대', '40대', '50대', '60대이상')),
    region_sido          VARCHAR(20) NOT NULL,
    joined_at            DATE        NOT NULL,                  -- 최초 가입일
    thinq_app_installed  CHAR(1)     NOT NULL CHECK (thinq_app_installed IN ('Y', 'N')),  -- 피처 미사용(구 4-1 삭제), 앱 미설치자는 app_login_days=0
    notify_consent_yn    CHAR(1)     NOT NULL CHECK (notify_consent_yn IN ('Y', 'N')),    -- 알림톡·문자 수신 동의 (구 push_consent_yn 이름 변경), 피처 미사용
    ad_consent_yn        CHAR(1)     NOT NULL DEFAULT 'N' CHECK (ad_consent_yn IN ('Y', 'N')),  -- 카카오 채널 친구 + 광고 수신 동의. 친구톡(광고성) 솔루션 발송 조건 [제안], 피처 아님
    weekday_daytime_available_yn CHAR(1) NOT NULL DEFAULT 'Y' CHECK (weekday_daytime_available_yn IN ('Y', 'N'))  -- 평일 낮 방문 가능 여부 (T06 신호) [제안]
);

CREATE TABLE subscription (
    subscription_id            VARCHAR(12)  PRIMARY KEY,        -- 예: S000001
    customer_id                VARCHAR(10)  NOT NULL,
    product_category           VARCHAR(20)  NOT NULL,
    model_name                 VARCHAR(40)  NOT NULL,
    model_discontinued_yn      CHAR(1)      NOT NULL DEFAULT 'N' CHECK (model_discontinued_yn IN ('Y', 'N')),  -- T03
    new_model_released_yn      CHAR(1)      NOT NULL DEFAULT 'N' CHECK (new_model_released_yn IN ('Y', 'N')),  -- 후속 신모델 출시 여부 (T03 신호) [제안]
    start_date                 DATE         NOT NULL,
    contract_months            SMALLINT     NOT NULL,
    mandatory_months           SMALLINT     NOT NULL,           -- 의무사용기간(개월)
    end_date                   DATE         NOT NULL,
    monthly_fee                NUMERIC(10,0) NOT NULL,
    discount_eligible_yn       CHAR(1)      NOT NULL DEFAULT 'N' CHECK (discount_eligible_yn IN ('Y', 'N')),  -- 결합·선납·제휴카드 등 할인 대상 여부 (T01 신호) [제안]
    discount_applied_yn        CHAR(1)      NOT NULL DEFAULT 'N' CHECK (discount_applied_yn IN ('Y', 'N')),   -- 할인 실제 적용 여부. 미적용 할인 = eligible Y and applied N
    care_type                  VARCHAR(10)  NOT NULL CHECK (care_type IN ('방문관리', '자가관리', '없음')),
    care_auto_renew_yn         CHAR(1)      NOT NULL CHECK (care_auto_renew_yn IN ('Y', 'N')),  -- 케어 자동 연장 여부. S-T02-A(해지 시 잔여 청구 사전 고지)에 사용, 피처는 아님
    care_renewal_date          DATE,                            -- 케어 자동갱신일, 케어 없으면 NULL. S-T02-A 고지에 사용, 피처는 아님
    promo_discount_amount      NUMERIC(10,0) NOT NULL DEFAULT 0,
    promo_end_date             DATE,                            -- 프로모션 없으면 NULL
    care_manager_id            VARCHAR(10) ,
    manager_change_confirmed_yn CHAR(1)     NOT NULL DEFAULT 'N' CHECK (manager_change_confirmed_yn IN ('Y', 'N')),  -- 담당 매니저 교체 확정 여부 (S-T07-D 트리거) [제안]
    status                     VARCHAR(10)  NOT NULL DEFAULT '활성' CHECK (status IN ('활성', '해지접수', '해지완료'))
);
CREATE INDEX ix_subscription_customer ON subscription (customer_id);
CREATE INDEX ix_subscription_product  ON subscription (product_category);

-- ---------- 3. 이벤트 테이블 ----------

CREATE TABLE usage_thinq (                                      -- 주 단위 집계 (일 단위 로그 아님)
    usage_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id     VARCHAR(12) NOT NULL,
    week_start_date     DATE        NOT NULL,
    run_count           SMALLINT    NOT NULL DEFAULT 0,         -- 제품 가동 횟수
    run_minutes         INTEGER     NOT NULL DEFAULT 0,
    app_login_days      SMALLINT    NOT NULL DEFAULT 0 CHECK (app_login_days BETWEEN 0 AND 7),
    CONSTRAINT uq_usage_week UNIQUE (subscription_id, week_start_date)
);

CREATE TABLE care_visit (                                       -- 방문관리·A/S·설치 이벤트 통합
    visit_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id     VARCHAR(12) NOT NULL,
    manager_id          VARCHAR(10),
    visit_type          VARCHAR(10) NOT NULL CHECK (visit_type IN ('정기점검', 'AS수리', '이전설치', '최초설치')),
    scheduled_date      DATE        NOT NULL,
    completed_date      DATE,                                   -- 미완료면 NULL
    visit_status        VARCHAR(10) NOT NULL CHECK (visit_status IN ('완료', '지연완료', '취소', '미이행', '예정')),
    reschedule_cnt      SMALLINT    NOT NULL DEFAULT 0,
    duration_min        SMALLINT,                               -- 방문 소요시간(분), 케어 방문 (T05 신호) [제안]
    booking_lead_days   SMALLINT,                               -- 예약 접수~방문 예정일 대기일 (T06 신호) [제안]
    sales_pitch_yn      CHAR(1)     NOT NULL DEFAULT 'N' CHECK (sales_pitch_yn IN ('Y', 'N')),          -- 방문 중 영업 권유 여부 (T08 신호) [제안]
    repitch_after_refusal_yn CHAR(1) NOT NULL DEFAULT 'N' CHECK (repitch_after_refusal_yn IN ('Y', 'N')),  -- 거절 후 재권유 여부 (T08 신호) [제안]
    split_as_receipt_yn CHAR(1)     NOT NULL DEFAULT 'N' CHECK (split_as_receipt_yn IN ('Y', 'N')),     -- A/S가 여러 번 나뉘어 접수됨 (T10 신호) [제안]
    symptom_code        VARCHAR(10),                            -- AS수리일 때만 값 존재
    repeat_symptom_yn   CHAR(1)     NOT NULL DEFAULT 'N' CHECK (repeat_symptom_yn IN ('Y', 'N')),
    parts_delay_yn      CHAR(1)     NOT NULL DEFAULT 'N' CHECK (parts_delay_yn IN ('Y', 'N')),
    inspection_anomaly_yn CHAR(1)   NOT NULL DEFAULT 'N' CHECK (inspection_anomaly_yn IN ('Y', 'N')),  -- 정기점검 중 이상 감지 (S-T09-C 트리거) [제안]
    as_received_date    DATE,                                   -- A/S 접수일, AS수리만 (S-T10-D 트리거) [제안]
    expected_complete_date DATE,                                -- A/S 예상 완료일, AS수리만. 예상 수리 기간 = 예상 완료일 - 접수일 [제안]
    repair_days         SMALLINT,                               -- 접수~수리 완료 소요일, AS수리만
    satisfaction_score  SMALLINT CHECK (satisfaction_score BETWEEN 1 AND 5)  -- 응답 안 한 경우 NULL
);
CREATE INDEX ix_care_visit_sub ON care_visit (subscription_id, scheduled_date);

CREATE TABLE voc_contact (
    voc_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id     VARCHAR(12) NOT NULL,
    contact_date        DATE        NOT NULL,
    channel             VARCHAR(10) NOT NULL CHECK (channel IN ('앱', '전화', '챗봇')),
    category            VARCHAR(20) NOT NULL CHECK (category IN (
                            '요금문의', '총비용문의', '위약금문의', '해지문의', '이사문의',
                            '갱신문의', '일정변경요청', '재방문요청', '케어결과불만', '담당자교체요청',
                            '태도불만', '영업불만', '응대불만', '사용법문의', '기타')),   -- 15개 (10유형 개정: 등록비문의·자동갱신문의 삭제, 일정변경요청·케어결과불만·태도불만·영업불만 추가)
    repeat_contact_yn   CHAR(1)     NOT NULL DEFAULT 'N' CHECK (repeat_contact_yn IN ('Y', 'N')),  -- 동일 카테고리 14일 내 재문의
    resolved_yn         CHAR(1)     NOT NULL CHECK (resolved_yn IN ('Y', 'N')),
    first_response_unresolved_yn CHAR(1) NOT NULL DEFAULT 'N' CHECK (first_response_unresolved_yn IN ('Y', 'N')),  -- 1차 응대에서 해결되지 않음 (T10 신호) [제안]
    cancel_delay_days   SMALLINT,                               -- 해지문의 접수~처리 지연일, 해지문의만 (T02 신호) [제안]
    response_hours      NUMERIC(6,1),                           -- 최초 응답까지 시간, 미응답이면 NULL
    sentiment_score     NUMERIC(3,2) CHECK (sentiment_score BETWEEN -1 AND 1),  -- 선택 컬럼
    summary_text        VARCHAR(500)                            -- 선택 컬럼 (가상 문의 요약)
);
CREATE INDEX ix_voc_sub ON voc_contact (subscription_id, contact_date);

CREATE TABLE payment (
    payment_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id     VARCHAR(12) NOT NULL,
    billing_month       DATE        NOT NULL,                   -- 해당 월 1일
    billed_amount       NUMERIC(10,0) NOT NULL,
    discount_amount     NUMERIC(10,0) NOT NULL DEFAULT 0,
    paid_amount         NUMERIC(10,0),                          -- 미납이면 NULL
    due_date            DATE        NOT NULL,
    paid_date           DATE,                                   -- 미납이면 NULL
    payment_status      VARCHAR(10) NOT NULL CHECK (payment_status IN ('정상', '연체', '미납')),
    CONSTRAINT uq_payment_month UNIQUE (subscription_id, billing_month)
);

CREATE TABLE notice_log (                                       -- 안내 발송·열람 이력 (갱신안내 = T03 신호, 자동갱신고지·등록비안내는 구 유형 6 삭제로 제외)
    notice_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id     VARCHAR(12) NOT NULL,
    notice_type         VARCHAR(10) NOT NULL CHECK (notice_type IN ('갱신안내', '사용팁', '계약조건안내')),
    sent_at             TIMESTAMP   NOT NULL,
    opened_at           TIMESTAMP                               -- 미열람이면 NULL
);
CREATE INDEX ix_notice_sub ON notice_log (subscription_id, sent_at);

-- ---------- 4. 예측·조치 결과 테이블 ----------

CREATE TABLE churn_prediction (                                 -- 전 구독 사전 계산 결과 (목록 화면용)
    prediction_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id     VARCHAR(12) NOT NULL,
    as_of_date          DATE        NOT NULL,
    churn_probability   NUMERIC(5,4) NOT NULL CHECK (churn_probability BETWEEN 0 AND 1),
    risk_level          VARCHAR(4)  NOT NULL CHECK (risk_level IN ('HIGH', 'MID', 'LOW')),
    risk_rank_pct       NUMERIC(6,5) NOT NULL CHECK (risk_rank_pct >= 0 AND risk_rank_pct < 1),  -- 같은 기준일·모델버전 내 위험 순위 백분위(0=가장 높음). 배치에서 한 번 계산해 risk_level과 같은 시점에 저장
    model_version       VARCHAR(20) NOT NULL,
    created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_prediction UNIQUE (subscription_id, as_of_date, model_version)
);
CREATE INDEX ix_prediction_risk ON churn_prediction (risk_level, churn_probability);
CREATE INDEX ix_prediction_rank ON churn_prediction (as_of_date, model_version, risk_rank_pct);

CREATE TABLE churn_prediction_type (                            -- 예측 1건당 이탈 유형 1~3개
    prediction_id       BIGINT      NOT NULL,
    type_rank           SMALLINT    NOT NULL CHECK (type_rank BETWEEN 1 AND 3),
    type_id             VARCHAR(4)  NOT NULL,
    contribution_share  NUMERIC(5,4) NOT NULL CHECK (contribution_share BETWEEN 0 AND 1),
    top_features_json   TEXT,                                   -- [{"feature":..,"value":..,"shap":..}, ...]
    PRIMARY KEY (prediction_id, type_rank),
    CONSTRAINT uq_prediction_type UNIQUE (prediction_id, type_id)
);

CREATE TABLE agent_result (                                     -- Agent 1회 실행 결과(상담자에게 보여 준 초안 포함). 재시작·감사·프롬프트 평가용
    request_id          VARCHAR(20) PRIMARY KEY,                -- API가 만드는 요청 ID(20자 이내)
    subscription_id     VARCHAR(12) NOT NULL,
    prediction_id       BIGINT     ,
    as_of_date          DATE        NOT NULL,
    mode                VARCHAR(12) NOT NULL CHECK (mode IN ('ACTION', 'NO_TYPE', 'SURVEY_ONLY', 'NONE')),
    outcome             VARCHAR(20) NOT NULL CHECK (outcome IN ('pass', 'retry_pass', 'partial_template', 'full_template', 'survey_template', 'no_action')),
    fallback            VARCHAR(30),                            -- NO_ELIGIBLE_CANDIDATE 등(후보 0건이라 만족도 조사로 대체)
    attempts            SMALLINT    NOT NULL DEFAULT 0,
    failed_checks       VARCHAR(200),                           -- 검증 실패 규칙 번호(쉼표 구분)
    llm_provider        VARCHAR(20),
    llm_model           VARCHAR(40),
    prompt_version      VARCHAR(10),
    result_json         JSONB       NOT NULL,                   -- AgentResult 전체(선택 항목·메시지·전달 초안·분석)
    created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_agent_result_sub ON agent_result (subscription_id, created_at);

CREATE TABLE action_log (                                       -- 조치 시뮬레이션 이력 (쿨다운 판정 기준)
    action_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_batch_id     VARCHAR(20) NOT NULL,  -- 조치가 나온 Agent 요청(= agent_result.request_id)
    subscription_id     VARCHAR(12) NOT NULL,
    prediction_id       BIGINT     ,
    type_id             VARCHAR(4)  NOT NULL,
    solution_id         VARCHAR(10) NOT NULL,
    action_category     VARCHAR(10) NOT NULL CHECK (action_category IN ('정보제공', '담당자조치', '혜택제안', '제품조치', '의견수집')),
    title               VARCHAR(100),                           -- 알림톡·친구톡 제목 (광고성이면 '(광고)' 시작)
    message_text        VARCHAR(1000),
    internal_memo       VARCHAR(1000),                          -- 내부 전달서 본문 (internal_forward_yn='Y'인 솔루션)
    message_source      VARCHAR(10) CHECK (message_source IN ('llm', 'template')),
    decision_note       VARCHAR(300),                           -- 상담자 메모(반려 사유 등)
    forward_depts       VARCHAR(100),                           -- 내부 전달 수신 부서 코드(쉼표 구분, 예: CS,PRICING). 부서 마스터는 churn_agent/data/departments.json
    memo_edited_yn      CHAR(1)     NOT NULL DEFAULT 'N' CHECK (memo_edited_yn IN ('Y', 'N')),  -- 상담자가 AI 초안을 고쳐 전달했는지
    offer_cost          NUMERIC(10,0) NOT NULL DEFAULT 0,
    risk_prob_at_action NUMERIC(5,4),
    action_status       VARCHAR(10) NOT NULL DEFAULT '제안됨' CHECK (action_status IN ('제안됨', '조치완료', '반려')),  -- 반려는 쿨다운·비용 누계에 세지 않음
    channel             VARCHAR(10) NOT NULL CHECK (channel IN ('알림톡', '친구톡', '내부 전달', '상담·앱')),  -- solution_catalog.channel에서 복사
    created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by          VARCHAR(30),
    completed_at        TIMESTAMP
);
CREATE INDEX ix_action_sub ON action_log (subscription_id, created_at);
CREATE INDEX ix_action_batch ON action_log (action_batch_id);
CREATE INDEX ix_action_solution ON action_log (solution_id);

-- ---------- 4-1. 설문·AI 제안 (survey_simulator_design.md §4; 응답은 시뮬레이터가 생성) ----------

CREATE TABLE survey_send (                                      -- 설문 발송 이력
    survey_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subscription_id     VARCHAR(12) NOT NULL,
    action_batch_id     VARCHAR(20),   -- 조치 후 설문이면 그 요청
    survey_type         VARCHAR(14) NOT NULL CHECK (survey_type IN ('post_action', 'post_churn', 'no_type', 'no_candidate', 'survey_only')),
    sent_at             TIMESTAMP   NOT NULL,
    sent_by             VARCHAR(6)  NOT NULL CHECK (sent_by IN ('auto', 'manual')),
    responded_yn        CHAR(1)     NOT NULL DEFAULT 'N' CHECK (responded_yn IN ('Y', 'N'))
);
CREATE INDEX ix_survey_send_sub ON survey_send (subscription_id);
CREATE INDEX ix_survey_send_batch ON survey_send (action_batch_id);

CREATE TABLE survey_response (                                  -- 응답 1건 = 1행
    response_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    survey_id           BIGINT      NOT NULL UNIQUE,
    completed_yn        CHAR(1)     NOT NULL CHECK (completed_yn IN ('Y', 'N')),
    status_code         VARCHAR(10),                            -- 설문 status 문항(done/ing/think/sat)
    submitted_at        TIMESTAMP   NOT NULL
);

CREATE TABLE survey_answer (                                    -- long 형식: 문항·보기 단위(복수 선택은 여러 행). 문항이 바뀌어도 테이블은 그대로
    answer_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    response_id         BIGINT      NOT NULL,
    question_id         VARCHAR(20) NOT NULL,                   -- 설문 시안의 코드(issues, top, deepA, ...)
    answer_code         VARCHAR(20),                            -- 보기 코드. 주관식은 NULL
    other_text          VARCHAR(500)
);
CREATE INDEX ix_survey_answer_q ON survey_answer (question_id, answer_code);
CREATE INDEX ix_survey_answer_resp ON survey_answer (response_id);

CREATE TABLE ai_proposal (                                      -- 설문 집계를 본 AI의 신규 유형·솔루션 제안(사람이 검토해 채택/기각)
    proposal_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proposal_kind       VARCHAR(12) NOT NULL CHECK (proposal_kind IN ('new_type', 'new_solution')),
    related_type_id     VARCHAR(4),        -- 새 솔루션 제안이 붙을 기존 유형(없으면 NULL)
    title               VARCHAR(100) NOT NULL,
    description         VARCHAR(1000) NOT NULL,
    evidence_json       JSONB,                                  -- 근거 집계(문항 코드·비율·응답 수·필터)
    status              VARCHAR(6)  NOT NULL DEFAULT '검토중' CHECK (status IN ('검토중', '채택', '기각')),
    reviewed_by         VARCHAR(30),
    created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at         TIMESTAMP
);

-- ---------- 5. 합성 정답 테이블 (모델 입력에 사용 금지 영역 포함) ----------

CREATE TABLE synth_churn_label (                                -- 학습용 정답: 기준일 후 30일 내 해지 접수 여부
    subscription_id       VARCHAR(12) PRIMARY KEY,
    as_of_date            DATE        NOT NULL,
    churn_30d             SMALLINT    NOT NULL CHECK (churn_30d IN (0, 1)),
    cancel_requested_date DATE                                  -- 이탈이면 기준일 다음날~30일 사이 날짜
);

CREATE TABLE synth_truth_type (                                 -- 검증용 정답: 생성 시 활성화한 유형 (학습·추론에 사용 금지)
    subscription_id     VARCHAR(12) NOT NULL,
    type_id             VARCHAR(4)  NOT NULL,
    activation_strength NUMERIC(3,2) NOT NULL CHECK (activation_strength BETWEEN 0 AND 1),
    is_primary_yn       CHAR(1)     NOT NULL CHECK (is_primary_yn IN ('Y', 'N')),
    PRIMARY KEY (subscription_id, type_id)
);

CREATE TABLE synth_truth_topic (                                -- 검증용 정답: 설문 응답 합성에 쓴 숨은 주제(H-A/H-B 등). 학습·추론 입력에 사용 금지
    subscription_id     VARCHAR(12) NOT NULL,
    topic_id            VARCHAR(8)  NOT NULL,
    strength            NUMERIC(3,2) NOT NULL CHECK (strength BETWEEN 0 AND 1),
    PRIMARY KEY (subscription_id, topic_id)
);
