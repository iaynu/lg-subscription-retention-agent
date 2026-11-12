# Agent 설계 (1) 입출력 계약 + Rule Engine

> LG전자 AX School Build2 · 구독 이탈 예측 & 유형별 맞춤 방지 AI Agent
> 작성 기준일: 2026-10-06 (2026-10-07 이탈 유형 10개 개정·솔루션 확정, 2026-10-08 점검 반영) / 상태: 초안 v0.7 (팀 검토 필요)
> 관련 파일: `overall_pipeline.md` (3.2 Agent 호출 흐름), `data_schema_design.md`, `schema_postgres.sql`, `churn_types_10.md`, `agent_test_cases.md`, `solution_catalog_design.md`(확정 카탈로그)
> 표기: `[확정]` / `[제안]` / `[가정]` / `[팀 결정 필요]`. 모든 정형 데이터는 가설 기반 synthetic data입니다.

이 문서는 설계 순서 1(입출력 계약)과 2(Rule Engine)를 다룹니다. 3(프롬프트), 4(검증기·폴백), 5(테스트 사례)는 각 문서에, 6(코드 구조)은 `agent_code_design.md`에 있습니다. Rule Engine은 2026-10-07에 v3(`priority`·`eligibility`), 2026-10-08에 v3.1(광고 동의 기본값·필수 조건·설문 쿨다운)로 개정했고 §4.8에 정리했습니다. **현행 API·필드의 정본은 `agent_code_design.md` §4**이며, 이 문서의 §3 예시는 계약의 뼈대를 설명합니다.

---

## 1. 이 문서에서 정하는 것

구독자 1명을 선택했을 때 Agent가 하는 일은 5단계입니다(`overall_pipeline.md` 3.2). 이 중 **데이터가 오가는 형식**과 **Rule Engine의 판정 방식**을 먼저 고정합니다.

```
선택된 구독 1건
   │
   ▼ ① 입력 조립 (DB 조회, LLM 없음)          → AgentInput
   ▼ ② Rule Engine (코드, 결정적)             → RuleResult (후보 + 제외 사유)
   ▼ ③ LLM (후보 안에서 선택·정렬·문구)        → LlmOutput
   ▼ ④ 검증기 (실패 시 재시도 1회 → 템플릿)     → AgentResult
   ▼ ⑤ 화면 표시 → 사용자 확정/반려             → action_log
```

설계 원칙은 `overall_pipeline.md` §1의 여섯 가지를 그대로 따릅니다. 특히 다음 둘이 계약을 좌우합니다.

- **역할 분리**: Rule Engine은 "무엇을 해도 되는가", LLM은 "그중 무엇을 어떻게 말하는가". LLM은 Rule Engine이 넘긴 `candidates` 밖의 솔루션을 만들 수 없습니다.
- **재현성**: Rule Engine은 시스템 시계가 아니라 `as_of_date`(설정값)를 "오늘"로 씁니다. 같은 입력이면 항상 같은 후보가 나와야 테스트할 수 있습니다.

---

## 2. 호출 경로 (mode)

위험 등급과 유형 유무로 경로가 갈립니다. 경로에 따라 LLM을 부를지가 달라집니다 `[확정 + 제안]`.

| mode | 조건 | Rule Engine | LLM | 화면 결과 |
|---|---|---|---|---|
| `ACTION` | HIGH 또는 MID, 유형 1~3개 | 실행 | 호출 | 유형별 솔루션 제안(확정/반려) |
| `NO_TYPE` | HIGH 또는 MID, 유형 없음 | 실행(후보 = 만족도 조사, HIGH는 상담 연결 병행 가능) | 호출(설문 안내 문구) | 만족도 조사 제안 `[제안]` |
| `SURVEY_ONLY` | LOW 중 위험 순위 20~30% 구간 | 만족도 조사(`S-NT-A`)의 활성·쿨다운만 확인(쿨다운이면 '조치 없음') | 호출 안 함(템플릿) | 만족도 조사 자동 추천 `[확정]` |
| `NONE` | 그 밖의 LOW | 호출 안 함 | 호출 안 함 | "해당 없음" |
| (대체) | `ACTION`인데 조건에 맞는 후보가 0건 | 실행 후 `S-NT-A`로 대체(`fallback='NO_ELIGIBLE_CANDIDATE'`, 이후 `NO_TYPE`처럼 처리) | 호출(설문 안내 문구) | 만족도 조사 제안 `[확정 2026-10-07]` |
| (공통) | 설문 수동 발송 | - | - | 어떤 등급이든 사용자가 버튼으로 발송 가능 |

`NO_TYPE`, `SURVEY_ONLY`, 대체 경로의 만족도 조사는 카탈로그의 `S-NT-A` 한 건입니다(시드에 포함, 구분 `의견수집`).

---

## 3. 입출력 계약

모든 계약은 JSON으로 고정하고, 코드에서는 Pydantic 모델로 검증합니다. 아래 예시는 하나의 구독을 끝까지 따라갑니다.

### 3.1 예시 시나리오

- 구독 `S000123`, 에어컨, 월 요금 68,000원, 계약 60개월, 만료까지 81일(기준일 2026-09-30, 만료 2026-12-20)
- 위험 HIGH(확률 0.71, 순위 상위 4%), 유형 2개: `T02` 해지·이사 장벽형(비중 0.53), `T03` 재계약·갈아타기 갈등형(비중 0.35)
- 10일 전에 `S-T02-B`(해지 접수 처리 현황 확인)를 이미 확정했음

(솔루션 ID·비용은 예시용 `[가정]`이며 규칙 검증용 고정값인 `test_cases/catalog_test.json`의 테스트 카탈로그를 씁니다. 확정된 실제 카탈로그는 `solution_catalog.json`(38건)이고, 시뮬레이션(§4.7)은 이쪽을 씁니다. 이 예시는 실제 Rule Engine 프로토타입을 돌린 결과와 같습니다.)

### 3.2 AgentInput (① 입력 조립의 출력)

```json
{
  "request_id": "REQ-20260930-0001",
  "as_of_date": "2026-09-30",
  "subscription": {
    "subscription_id": "S000123",
    "product_category": "에어컨",
    "monthly_fee": 68000,
    "contract_months": 60,
    "end_date": "2026-12-20",
    "days_to_end": 81,
    "care_type": "방문관리",
    "care_renewal_date": null,
    "promo_end_date": null,
    "start_date": "2021-12-20"
  },
  "customer": {"customer_id": "C000123", "customer_name": "(가상 이름)", "ad_consent_yn": "Y"},
  "prediction": {
    "prediction_id": 4821,
    "churn_probability": 0.7100,
    "risk_level": "HIGH",
    "risk_rank_pct": 0.04
  },
  "types": [
    {"type_rank": 1, "type_id": "T02", "type_name": "해지·이사 장벽형", "contribution_share": 0.53,
     "top_features": [
       {"feature": "voc_penalty_cnt_90d", "label": "최근 90일 위약금 문의 횟수", "value": 4, "shap": 0.41},
       {"feature": "voc_cancel_cnt_90d",  "label": "최근 90일 해지 문의 횟수", "value": 2, "shap": 0.22}]},
    {"type_rank": 2, "type_id": "T03", "type_name": "재계약·갈아타기 갈등형", "contribution_share": 0.35,
     "top_features": [
       {"feature": "days_to_end", "label": "계약 만료까지 남은 일수", "value": 81, "shap": 0.33},
       {"feature": "new_model_released_yn", "label": "신모델 출시 여부", "value": "Y", "shap": 0.18}]}
  ],
  "context": {
    "voc_summary":   {"window_days": 90, "total": 7, "by_category": {"위약금문의": 4, "해지문의": 2, "갱신문의": 1}, "unresolved": 1},
    "visit_summary": {"window_days": 365, "visits": 1, "delays": 0, "cancels": 0},
    "notice_summary": {"renewal_notice_sent": "Y", "renewal_notice_opened": "N"}
  },
  "recent_actions": [
    {"action_id": 901, "solution_id": "S-T02-B", "action_category": "담당자조치", "action_status": "조치완료", "offer_cost": 0, "created_at": "2026-09-20"}
  ],
  "facts": {"days_to_end": 81, "new_model_released_yn": "Y", "move_signal_yn": "N", "model_discontinued_yn": "N"},
  "message_facts": {"end_date": "2026-12-20", "days_to_end": 81, "monthly_fee": 68000}
}
```

- `context`의 요약은 **규칙으로 만든 집계 숫자**입니다. LLM에게 원본 로그를 주고 요약시키지 않습니다 `[확정]`(없는 사실이 섞이는 것을 막기 위함).
- `top_features`는 `churn_prediction_type.top_features_json`(SHAP 상위 3~5개)에서 가져옵니다. `label`은 피처 정의 파일(`feature_groups.yaml`)의 `display_ko`에서 가져오며(`agent_prompt_design.md` §4.2), LLM이 피처 이름을 임의로 풀어 쓰다 숫자와 어긋나는 것을 막기 위한 것입니다 `[제안]`.
- `risk_rank_pct`는 전체 순위(0~1, 작을수록 위험)이며 `mode` 판정(LOW 20~30%)에 씁니다. `churn_prediction.risk_rank_pct` 컬럼으로 반영했습니다(배치에서 `percent_rank()`로 저장).
- `customer`는 내부 화면·협업 요청서용(고객 ID·이름)과 광고 수신 동의(`ad_consent_yn`)입니다. 광고 동의는 `customer` → `subscription` 순으로 읽고 **값이 없으면 미동의**입니다(2026-10-08). 고객 ID·이름은 LLM에 보내지 않습니다.
- `facts`(v3)는 솔루션 `eligibility` 판정용 내부 값입니다(예: `days_to_end`, `unapplied_discount_yn`, `open_as_yn`). 구독 속성으로 계산할 수 있는 값(만료·프로모션까지 남은 일수, 이용 개월, 체험 종료까지 일수(시작일 + 7일), 계약 연도 종료까지 일수)은 엔진이 채우고, DB 모드에서는 `SqlSource`가 이벤트 테이블에서 계산합니다(`agent_code_design.md` §5). '사건이 있었다'고 말하는 조건(`required`)은 값이 없으면 후보에서 빠지고, 그 밖의 조건은 값이 없으면 통과합니다. LLM에는 보내지 않습니다.
- `message_facts`(2026-10-08)는 고객에게 보여도 되는 본인 계약·이용 정보입니다(남은 케어, 사용 기한, 다음 방문일, 지금까지 낸 구독료 등). 고객 메시지의 ▶ 항목은 이 값만 쓸 수 있습니다.
- `recent_actions`는 최근 90일 `action_log`입니다. 쿨다운은 최대 14일이면 충분하지만 **누적 비용 한도(최근 90일 혜택 합계) 계산에 90일이 필요**합니다. `offer_cost`는 `action_log`에 저장된 값을 씁니다.

### 3.3 RuleResult (② Rule Engine의 출력)

```json
{
  "mode": "ACTION",
  "candidates": [
    {"solution_id": "S-T02-A", "type_id": "T02", "type_rank": 1, "contribution_share": 0.53,
     "category": "정보제공", "name": "해지·이사 절차 및 위약금 안내", "detail": "...", "offer_cost": 0, "fee_impact": "없음", "message_hint": "..."},
    {"solution_id": "S-T03-C", "type_id": "T03", "type_rank": 2, "contribution_share": 0.35,
     "category": "제품조치", "name": "신모델 교체 제안", "detail": "...", "offer_cost": 0, "fee_impact": "증가", "message_hint": "..."}
  ],
  "excluded": [
    {"solution_id": "S-T02-B", "type_id": "T02", "reason": "COOLDOWN_SOLUTION",
     "detail": "같은 솔루션을 10일 전(2026-09-20)에 조치완료. 쿨다운 14일"},
    {"solution_id": "S-T03-A", "type_id": "T03", "reason": "DUP_CATEGORY",
     "detail": "같은 구분(정보제공)에서 유형 기여도가 더 높은 S-T02-A(T02)를 남김"},
    {"solution_id": "S-T03-B", "type_id": "T03", "reason": "MAX_ACTIONS",
     "detail": "동시 조치 2개가 이미 채워짐"}
  ],
  "limits": {"max_actions": 2, "cost_cap": 68000, "cost_used": 0,
             "cumulative_cap": 136000, "spent_90d": 0, "remaining_cumulative": 136000},
  "uncovered_types": []
}
```

- `excluded`는 **화면에 보여 줍니다**("왜 이 솔루션은 안 나왔는지"). 시연에서 Rule Engine이 안전장치로 일한다는 것을 보이는 장면이 됩니다.
- `excluded`는 **LLM에게는 넘기지 않습니다**. 넘기면 제외된 솔루션을 문구에서 언급할 위험이 있습니다.
- `uncovered_types`: 후보가 하나도 남지 않은 유형 목록입니다(예: 유형 T03의 후보가 전부 제외되면 `["T03"]`). 화면에 "이번에는 이 유형으로 제안할 조치 없음"으로 표시합니다.
- `reason` 코드: `COOLDOWN_SOLUTION`, `NO_AD_CONSENT`(광고성 친구톡인데 광고 수신 동의 없음, 2026-10-07 추가), `DUP_CATEGORY`, `COST_CAP`(1회 상한), `COST_CAP_CUM`(90일 누적 한도), `MAX_ACTIONS`. (`INACTIVE`와 전사 개선형(`agent_selectable_yn='N'`)은 후보 생성 단계에서 빠져 `excluded`에 나오지 않음, `agent_test_cases.md` §7.2)

### 3.4 LlmInput / LlmOutput (③ LLM)

> **현행(2026-10-08, 프롬프트 v3.0)**: LLM에 보내는 것은 `mode`, `max_actions`, 구독 속성 8개(구독 ID·시작일·광고 동의 제외), `prediction`, `types`, `context`, `message_facts`(라벨·표시 문자열), 후보(고객용 제목·▶ 항목·버튼 포함)입니다. 내부 LlmInput에는 검증기 전용으로 `facts`와 `constraints`의 나머지(`banned_phrases_extra`, `config_numbers`, `max_message_chars`)도 들어 있지만 프롬프트에는 넣지 않고, `customer`·`recent_actions`도 보내지 않습니다(`agent_prompt_design.md` §4.1). LlmOutput 항목에는 아래 필드 외에 `title`, `internal_memo`가 있고, `message_text`는 '첫 문장 → ▶ 항목 → 마무리' 형식입니다. 아래 JSON은 v1 예시입니다.

**LlmInput** = `AgentInput`에서 `recent_actions`를 뺀 것 + `RuleResult.candidates` + 규칙(`constraints`).

```json
{
  "...": "AgentInput의 subscription, prediction, types, context",
  "candidates": ["RuleResult.candidates 그대로"],
  "constraints": {
    "max_actions": 2,
    "must_choose_from_candidates": true,
    "banned_phrases": ["무조건", "100% 보장", "해지하지 마세요"],
    "message_rules": ["월 요금만 강조하지 않는다", "총 비용과 위약금 조건을 함께 안내한다"]
  }
}
```

**LlmOutput** (LLM이 반드시 이 JSON만 출력):

```json
{
  "selected": [
    {
      "priority": 1,
      "solution_id": "S-T02-A",
      "type_id": "T02",
      "selection_reason": "최근 90일 위약금 문의가 4회, 해지 문의가 2회여서 위약금 산정 방식과 절차를 먼저 안내하는 것이 적절합니다.",
      "evidence": [
        {"feature": "voc_penalty_cnt_90d", "value": 4},
        {"feature": "voc_cancel_cnt_90d", "value": 2}
      ],
      "message_text": "고객님, 최근 위약금과 해지 조건에 대해 문의해 주셨습니다. 중도 해지 시 위약금이 산정되는 방식과 총 납부 금액을 함께 안내드립니다. 자세한 내용은 앱 또는 상담으로 확인하실 수 있습니다."
    },
    {
      "priority": 2,
      "solution_id": "S-T03-C",
      "type_id": "T03",
      "selection_reason": "계약 만료가 81일 남았고 신모델이 출시되어 교체 방안을 안내할 시점이며 구독료 변동을 함께 알려야 합니다.",
      "evidence": [
        {"feature": "days_to_end", "value": 81},
        {"feature": "new_model_released_yn", "value": "Y"}
      ],
      "message_text": "계약 만료(12월 20일)가 다가오며 신모델 교체 방안을 접수해 드립니다. 교체 시 구독료가 달라질 수 있어 늘어나는 금액과 총 비용을 안내드리며, 기존 구독 유지 여부도 함께 확인하실 수 있습니다."
    }
  ],
  "summary": "위약금 문의가 이어지고 계약 만료가 가까워 신모델 교체를 고민할 수 있는 고객입니다. 조건 안내를 먼저, 교체 접수를 다음으로 제안합니다."
}
```

- `selected`는 후보 안의 `solution_id`만 허용하고, 개수는 1~`max_actions`입니다. 후보가 있는데 `selected`가 비는 것은 허용하지 않습니다(검증 실패 `R1_EMPTY`). 후보는 Rule Engine이 이미 안전하다고 판정한 목록이라 LLM에 거부권이 없습니다(`agent_validator_design.md` §5.2) `[제안]`.
- `evidence`의 `feature`와 `value`는 입력의 `top_features`/`subscription`에 실제로 있는 값이어야 합니다. 검증기가 숫자를 대조합니다(설계 순서 4).
- LLM이 정하는 것은 `priority`(정렬), `selection_reason`, `evidence`, `title`, `message_text`, `internal_memo`, `summary`입니다. 비용·구분·채널·버튼·솔루션 이름은 카탈로그 값을 쓰므로 LLM 출력에 포함하지 않습니다.

### 3.5 AgentResult (④ 검증을 통과한 최종 결과, 화면 입력)

```json
{
  "request_id": "REQ-3fa9c21b07d4",
  "mode": "ACTION",
  "outcome": "pass",
  "attempts": 1,
  "failed_checks": [],
  "fallback": null,
  "selected": [
    {"priority": 1, "solution_id": "S-T02-A", "type_id": "T02", "selection_reason": "...", "evidence": [],
     "title": "", "message_text": "...", "internal_memo": "", "channel": "상담·앱", "buttons": ["(앱) 계약 정보 보기"], "message_source": "llm"}
  ],
  "messages": [{"channel": "알림톡", "ad_yn": "N", "title": "...", "message_text": "...", "buttons": [], "solution_ids": [], "merged": false}],
  "forward": null,
  "analysis": {"risk_percent": 71, "types": []},
  "summary": "...",
  "excluded": ["RuleResult.excluded 그대로"],
  "uncovered_types": [],
  "limits": {},
  "meta": {"llm_provider": "openai", "llm_model": "(미정)", "llm_status": "ready", "prompt_version": "v3.0", "llm_errors": []}
}
```

- `outcome`: `pass`·`retry_pass`(LLM 문구), `partial_template`·`full_template`(일부·전체 템플릿), `survey_template`(설문 추천 구간), `no_action`.
- `message_source`: `"llm"` 또는 `"template"`. 화면에 구분 표시합니다. mock·템플릿 전용 모드는 `meta.llm_provider`·`llm_status`로 표시합니다.
- 결과 전체는 `agent_result`에 저장합니다(재시작 후에도 결정 가능, 초안과 상담자 수정본 비교에 사용).

### 3.6 확정/반려와 action_log 기록

사용자는 `확정`/`반려`만 합니다(문구 편집 제외). **항목 단위로 선택**하고 "전체 확정" 버튼을 함께 둡니다 `[확정]`(2026-10-06).

| API | 요청 | 동작 |
|---|---|---|
| `POST /agent/run` | `{subscription_id, as_of_date?}` | ①~④ 실행, `AgentResult` 반환, `agent_result`에 저장(요청 ID는 서버가 생성) |
| `POST /agent/decision` | `{request_id, subscription_id, solution_id, status:"조치완료"\|"반려", note?, by?}` | 톡·상담 안내 부분 1건을 `action_log`에 기록 |
| `POST /agent/forward` | `{request_id, subscription_id, solution_ids[], departments[], text, by?}` | 내부 전달 부분을 솔루션마다 1행(`channel='내부 전달'`) 기록 |

"전체 확정" 버튼은 UI가 항목마다 위 API를 호출하는 방식으로 구현합니다.

`action_log` 매핑(항목 1개 = 1행):

| action_log 컬럼 | 값 |
|---|---|
| action_batch_id | `request_id` |
| subscription_id, prediction_id | AgentInput의 값 |
| type_id, solution_id, action_category | 항목의 값 |
| title, message_text, internal_memo, message_source | 항목의 값(내부 전달 행은 상담자가 확정한 협업 요청서 본문) |
| offer_cost | 카탈로그 값(내부 전달 행은 0, 비용 중복 계산 방지) |
| channel | 카탈로그 채널(내부 전달 행은 `내부 전달`) |
| risk_prob_at_action | `prediction.churn_probability` |
| action_status | `조치완료`(발송·전달) 또는 `반려` |
| decision_note | 상담자 메모(반려 사유 등) |
| forward_depts, memo_edited_yn | 협업 전달 수신 부서 코드, AI 초안 수정 여부 |
| created_at | 결정 시각(`clock: simulated`면 기준일 + 현재 시각) |
| created_by | 상담자 이름 |

**쿨다운은 `조치완료`만 센다** `[확정]`: 반려한 조치는 실제로 나가지 않았으므로 같은 제안을 다시 보여 줘도 됩니다. 반려 이력은 남겨서 나중에 "어떤 제안이 자주 반려되는가"를 볼 수 있습니다.

---

## 4. Rule Engine 설계

### 4.1 설정값 (`config.yaml` → `rule_engine`) `[제안]`

시작값입니다. 시연 사례를 돌려 보고 조정합니다(오늘 합의: "돌려 보고 확인").

```yaml
rules:                                   # churn_agent/config.yaml (현행 키 이름)
  cooldown_same_solution_days: 14
  cooldown_count_statuses: ["조치완료"]
  cost_cap_ratio: 1.0                    # 1회 접촉당 offer_cost 합계 ≤ 월 요금 × 1.0 (offer_cost > 0인 솔루션에만 적용)
  cost_cap_cumulative_ratio: 2.0         # 최근 N일 혜택 합계 ≤ 월 요금 × 2.0 [가정]
  cost_cap_cumulative_days: 90
  max_actions: 2
  survey_recommend_rank_range: [0.20, 0.30]   # LOW 중 자동 추천 구간
  fallback_survey_when_empty: true       # 후보 0건이면 만족도 조사로 대체 [확정]
  unknown_required_fact: fail            # 'required' 조건의 값이 없으면 제외(pass면 통과) [제안]
facts:
  trial_days: 7                          # 체험 기간 [확정]
```

기준일은 시스템 시계가 아니라 입력의 `as_of_date`를 씁니다(재현성). 구분당 1개, 유형별 대표 1개 우선(coverage first)은 설정이 아니라 고정 규칙입니다.

### 4.2 판정 순서와 이유 (v2, 시뮬레이션 반영) `[확정]`

초안(v1)은 "쿨다운 → 구분 중복 제거 → 비용 상한 → 동시 조치 수"를 한 단계씩 순서대로 걸렀습니다. 시뮬레이션(§4.7)에서 두 가지 문제가 나와 **v2**로 바꿨습니다.

- **문제 1, 두 번째 유형 누락**: 구분 중복을 먼저 지우면 비용 상한에 막힌 혜택의 자리에 있던 저렴한 대안까지 이미 사라져서, 두 번째 유형이 후보를 못 받습니다(두 번째 유형 미커버 60%).
- **문제 2, 무료여야 할 서비스가 비용 상한에 막힘**: 초안은 제품 교환·점검의 원가(가정 50,000~80,000원)를 비용 상한에 합산해서, 반복 고장·수리 지연 유형이 제안을 거의 못 받았습니다(구 유형 3-2가 주 유형일 때 97% 미커버, 이전 11유형 시뮬레이션). 문제가 있는 경우의 A/S·교환은 고객에게 무료여야 하므로 `offer_cost`에 넣지 않습니다.

| 단계 | 규칙 | 설명 |
|---|---|---|
| 0 | 경로(mode) 결정 | 이후 단계를 건너뛸지 판단 |
| 1 | 후보 생성 | 채택된 유형의 `active_yn='Y'` 솔루션 |
| 2 | **쿨다운(제외)** | 같은 솔루션 14일 이내 `조치완료`가 있으면 제외(같은 구분 쿨다운은 삭제, 2026-10-06) |
| 3 | **선택(그리디)** | 우선순위 순으로 훑으며 아래 세 조건을 **한 번에** 확인하고 모두 통과하면 채택. ① 슬롯이 남아 있음(`max_actions`), ② 이미 뽑은 구분과 겹치지 않음(구분당 1개), ③ 비용 여유가 있음(**`offer_cost > 0`인 솔루션에만 적용**, 1회 상한과 90일 누적 남은 한도 중 작은 쪽) |

선택은 두 라운드로 진행합니다.

1. **1라운드, 유형별 대표 1개**: 유형 순위 순으로, 각 유형에서 우선순위가 가장 높은 통과 후보 1개씩을 채택합니다. 상위 유형 하나가 슬롯을 독점하지 않게 하기 위해서입니다.
2. **2라운드, 남은 슬롯 채우기**: 남은 슬롯이 있으면 남은 후보를 우선순위 순으로 채웁니다.

**비용 상한은 `offer_cost > 0`인 솔루션에만 적용** `[확정]`(2026-10-06): `offer_cost`는 **무료 서비스 범위를 넘어 회사가 추가로 부담하는 금액**입니다. 상한은 월 요금 대비 비율로 둡니다.

- 문제가 있어서 하는 A/S·점검·교환(반복 고장, 수리 지연·단종 대응)은 고객에게 무료이므로 `offer_cost = 0`이고, 비용 상한에 걸리지 않습니다.
- 요금 감면·쿠폰 같은 혜택, 그리고 문제 대응이 아닌 교환·대체 제품처럼 추가 비용이 드는 솔루션은 `offer_cost > 0`으로 기록하고 비용 상한에 맞춥니다.
- 판정은 구분(정보제공·혜택제안 등)이 아니라 솔루션별 `offer_cost` 값으로 하므로, 카탈로그에서 금액만 정하면 규칙을 고치지 않아도 됩니다.

**업그레이드·추가 구독·제품 교체 제안은 비용 상한 대상이 아님** `[확정]`(2026-10-06): 고객이 늘어난 구독료를 부담하는 제안(예: 제품군 변경 제안, 기존 구독을 유지한 채 다른 제품을 추가)은 회사가 추가로 부담하는 금액이 아니므로 `offer_cost = 0`입니다. 그 제안에 할인·쿠폰을 얹는 경우에는 얹은 혜택 금액만 `offer_cost`로 기록합니다. 대신 **고객 부담이 늘어나는 제안은 안내 문구에 늘어나는 금액과 총 비용을 함께 밝혀야** 하므로, 솔루션에 `fee_impact`(`없음`/`증가`)를 두어 프롬프트에서 이 규칙을 적용합니다(§5, 설계 3). 실제 구독 생성·교체는 MVP에서 하지 않고 제안만 기록합니다.

**누적 한도** `[확정: 최근 90일 혜택 합계 기준, 월 요금 비례]`(2026-10-06): 최근 90일 동안 이 구독에 `조치완료`한 혜택(`offer_cost`)의 합계에 한도를 둡니다. 한도는 고정 금액이 아니라 **월 요금에 비례**합니다. 구독을 추가하거나 업그레이드해서 월 요금이 오르면 한도도 커지므로, 구독을 더 하는 고객에게는 혜택을 더 줄 수 있습니다.

- 누적 한도 = 월 요금 × 2.0 (시작값 `[가정]`)
- 남은 누적 한도 = 누적 한도 − 최근 90일 `조치완료` 혜택의 `offer_cost` 합계
- 선택 단계에서는 "1회 상한(월 요금 × 1.0)"과 "남은 누적 한도" 중 **작은 쪽**으로 판정합니다. 1회 상한에 걸리면 `COST_CAP`, 누적 한도에 걸리면 `COST_CAP_CUM`으로 기록합니다.
- 90일이 지난 혜택은 합계에서 빠지므로 한도가 다시 열립니다. 현재 데이터는 고객당 구독이 1개라서 구독 단위 월 요금으로 계산하고, 구독이 여러 개가 되면 고객 단위 월 요금 합계로 확장합니다 `[후속]`.

**우선순위 정렬 키**(결정적 동작): `(-contribution_share, type_rank, offer_cost, solution_id)`. 기여도가 큰 유형이 먼저이고, 같으면 비용이 낮은 쪽, 그래도 같으면 ID 순입니다.

`excluded`의 사유 코드는 후보 거름 단계의 쿨다운(`COOLDOWN_SOLUTION`)·광고 동의(`NO_AD_CONSENT`)와 선택 단계에서의 탈락 이유(`DUP_CATEGORY`, `COST_CAP`, `COST_CAP_CUM`, `MAX_ACTIONS`) 중 **처음 걸린 것**을 기록합니다.

### 4.3 의사 코드 (v2)

```python
def run_rule_engine(inp: AgentInput, catalog: list[Solution], cfg: RuleCfg) -> RuleResult:
    today = date.fromisoformat(inp.as_of_date)          # 시스템 시계 사용 금지
    mode = decide_mode(inp, cfg)
    if mode != "ACTION" and mode != "NO_TYPE":           # SURVEY_ONLY/NONE은 후보 없이 반환
        return RuleResult(mode=mode, candidates=[], excluded=[], limits=..., uncovered_types=[])

    # 1. 후보 생성 (NO_TYPE이면 pseudo-type '유형없음'의 솔루션)
    cands = [Cand(s, t.type_rank, t.contribution_share)
             for t in inp.types for s in catalog if s.type_id == t.type_id and s.active_yn == "Y"]

    # 2. 쿨다운
    done = [a for a in inp.recent_actions if a.action_status in cfg.cooldown_count_statuses]
    excluded, kept = [], []
    for c in cands:
        if any(a.solution_id == c.solution_id and days(today, a.created_at) < cfg.cooldown_same_solution_days for a in done):
            excluded.append(ex(c, "COOLDOWN_SOLUTION")); continue
        kept.append(c)
    kept.sort(key=lambda c: (-c.share, c.type_rank, c.offer_cost, c.solution_id))

    # 3. 선택 (그리디, 두 라운드)
    fee = inp.subscription.monthly_fee
    cap = fee * cfg.cost_cap_ratio                         # 1회 상한
    spent = sum(a.offer_cost for a in done if days(today, a.created_at) < cfg.cost_cap_cumulative_days)
    cum_left = max(fee * cfg.cost_cap_cumulative_ratio - spent, 0)   # 90일 누적 남은 한도
    final, cats, used, why = [], set(), 0, {}

    def try_add(c):
        nonlocal used
        if len(final) >= cfg.max_actions:                  return "MAX_ACTIONS"
        if c.category in cats:                             return "DUP_CATEGORY"
        counts = c.offer_cost > 0                          # 추가 비용이 드는 솔루션만 상한 대상
        if counts and used + c.offer_cost > cap:           return "COST_CAP"
        if counts and used + c.offer_cost > cum_left:      return "COST_CAP_CUM"
        final.append(c); cats.add(c.category)
        if counts: used += c.offer_cost
        return None

    for tid in dict.fromkeys(c.type_id for c in kept):     # 1라운드: 유형 순위 순, 유형당 1개
        for c in (x for x in kept if x.type_id == tid):
            r = try_add(c)
            if r is None: break
            why.setdefault(c.solution_id, r)
    for c in kept:                                         # 2라운드: 남은 슬롯 채우기
        if c not in final:
            r = try_add(c)
            if r: why.setdefault(c.solution_id, r)

    excluded += [ex(c, why.get(c.solution_id, "MAX_ACTIONS")) for c in kept if c not in final]
    covered = {c.type_id for c in final}
    uncovered = [t.type_id for t in inp.types if t.type_id not in covered]
    return RuleResult(mode, final, excluded, limits(cfg.max_actions, cap, used, cum_cap=fee*cfg.cost_cap_cumulative_ratio, spent=spent), uncovered)
```

`decide_mode`는 §2의 표를 따릅니다.

```python
def decide_mode(inp, cfg):
    p = inp.prediction
    if p.risk_level in ("HIGH", "MID"):
        return "ACTION" if inp.types else "NO_TYPE"
    lo, hi = cfg.survey_recommend_rank_range
    return "SURVEY_ONLY" if lo <= p.risk_rank_pct < hi else "NONE"
```

(`risk_rank_pct`는 0~1, 작을수록 위험합니다. HIGH는 0~0.10, MID는 0.10~0.20, LOW는 0.20 이상입니다.)

### 4.4 예시 추적 (§3.1의 구독 S000123)

| 단계 | 남은 후보 | 제외 |
|---|---|---|
| 1 후보 생성 | S-T02-A(정보제공), S-T02-B(담당자조치), S-T03-A(정보제공), S-T03-B(혜택제안), S-T03-C(제품조치) | - |
| 2 쿨다운 | S-T02-A, S-T03-A, S-T03-B, S-T03-C | S-T02-B: 같은 솔루션 10일 전 조치완료(쿨다운 14일). |
| 정렬 | S-T02-A(T02, 0원) → S-T03-A, S-T03-C(T03, 0원) → S-T03-B(T03, 30,000원) | 비용 낮은 순, 같으면 ID 순 |
| 3-1 선택 1라운드: 유형 T02 | S-T02-A 채택(정보제공) | - |
| 3-1 선택 1라운드: 유형 T03 | S-T03-C 채택(제품조치, `offer_cost` 0이라 비용 상한 대상 아님) | S-T03-A: 정보제공 구분이 이미 있어 `DUP_CATEGORY` |
| 3-2 선택 2라운드 | 슬롯 2개가 이미 찼음 | S-T03-B: `MAX_ACTIONS` |

결과는 §3.3의 RuleResult와 같고, LLM은 이 두 후보 안에서 순서를 정하고 문구를 씁니다.

### 4.5 경계 상황 규칙

| 상황 | 동작 |
|---|---|
| 후보가 하나도 남지 않음 | 만족도 조사(`S-NT-A`)로 대체해 `NO_TYPE`처럼 처리(`fallback='NO_ELIGIBLE_CANDIDATE'`, 원래 유형은 `uncovered_types`) `[확정 2026-10-07]`. `S-NT-A`도 쿨다운이면 LLM 없이 "제안할 조치 없음" |
| 유형은 있는데 한 유형의 후보만 전부 제외 | `uncovered_types`에 기록하고 화면에 표시. 나머지 유형으로 진행 |
| 비용 상한이 너무 낮아 비용이 드는 솔루션이 전부 제외 | `offer_cost = 0`인 솔루션(정보 안내, 담당자 조치, 문제 대응 A/S·교환)만 제안됨(`COST_CAP`이 사유로 표시). 비율은 설정값으로 조정 |
| `offer_cost`가 0인 정보제공 | 비용 합산에 영향 없음 |
| 90일 누적 한도를 이미 소진 | 비용이 드는 솔루션은 `COST_CAP_CUM`으로 제외, `offer_cost = 0`인 솔루션만 제안 |
| 같은 구독을 같은 날 다시 조회 | 같은 입력 → 같은 RuleResult. LLM 문구는 달라질 수 있어도 후보는 동일 |
| 카탈로그에 활성 솔루션이 없는 유형 | 그 유형은 `uncovered_types`. 승인된 신규 유형·솔루션 제안(`ai_proposal`에서 `채택`)은 카탈로그 반영 후에만 후보가 됨 |

### 4.6 Rule Engine 단위 테스트 목록 (초안)

LLM 없이 Rule Engine만 테스트합니다. 입력·기대 결과는 설계 순서 5(테스트 사례)에서 JSON으로 확정합니다.

1. 유형 1개, 이력 없음 → 해당 유형의 모든 활성 솔루션이 구분별 1개씩 후보
2. 같은 솔루션을 13일 전 조치완료 → `COOLDOWN_SOLUTION`, 15일 전 → 통과
3. 같은 구분을 6일 전 조치완료(다른 솔루션) → 쿨다운 없음, 후보 유지(같은 구분 쿨다운 삭제)
4. 반려 이력만 있음 → 쿨다운에 걸리지 않음
5. 두 유형이 같은 구분 솔루션을 가짐 → 기여도 높은 유형 쪽만 남음
6. 혜택이 비용 상한을 초과 → 해당 혜택은 `COST_CAP`, 상한 안의 다른 유형 혜택이나 다른 구분 솔루션이 선택됨(구분 중복 때문에 대안이 사라지면 안 됨)
7. 후보가 3개 이상 → 상위 2개만 남고 나머지 `MAX_ACTIONS`
8. 유형 없음 + HIGH/MID → `NO_TYPE`, LOW 순위 25% → `SURVEY_ONLY`, LOW 순위 50% → `NONE`
9. `as_of_date`만 바꿔 같은 입력 반복 → 같은 결과(재현성)
10. 문제 대응 A/S·교환(`offer_cost = 0`)은 월 요금과 무관하게 `COST_CAP`에 걸리지 않고, 추가 비용이 드는 솔루션(`offer_cost > 0`)은 구분과 무관하게 상한을 적용
11. 유형 3개, 슬롯 2개 → 유형 1·2가 각각 1개씩 커버되고 유형 3은 `uncovered_types`에 기록(1위 유형이 슬롯 2개를 독점하지 않음)
12. 90일 내 `조치완료` 혜택 합계로 남은 누적 한도가 부족 → `COST_CAP_CUM`, 91일 전 혜택은 합계에서 제외, 반려 이력은 합계에서 제외
13. 월 요금이 높은 구독은 같은 이력에서도 누적 한도가 더 많이 남음(구독 규모 비례)

**정식 목록은 `agent_test_cases.md`(규칙 케이스 38건)이고, 테스트 코드는 `churn_agent/tests/`(전체 249건)입니다.**

### 4.7 시뮬레이션 결과 (2026-10-07, 10유형 + 확정 카탈로그 기준)

> 구 11개 유형·가정 카탈로그로 돌렸던 2026-10-06 결과(v1 vs v2 비교, 쿨다운 7/14/21/30일 등)는 이 절의 결과로 교체했습니다. v1 → v2에서 두 번째 유형 미커버가 60%에서 22%로 줄었다는 결론은 규칙 구조에 관한 것이라 그대로이고, 아래는 확정 카탈로그에서 다시 잰 값입니다.

**조건**: 구독 30,000건(기준일 2026-09-30, 시드 7), 생성식으로 계산한 이탈 확률 상위 20%(6,000건)에 Rule Engine만 실행. 활성 유형은 `type_weights`(`data_schema_design.md` §6)로 뽑았고 기여도는 `w × 강도` 비중으로 근사했습니다(실제 SHAP 아님). 비중 20% 이상 최대 3개를 유형으로 채택하고 월 요금은 제품군별 범위에서 뽑았습니다. 초기 이력은 유형 보유자의 50%에게 과거 1~2건(1~40일 전, 조치완료 80%·반려 20%)을 넣었고, **광고 수신 동의는 50% 가정**입니다. 카탈로그는 `solution_catalog.json`(Agent 후보 35건 + `S-NT-A`). 스크립트는 세션 작업 파일이고 재현 가능한 값은 시드와 위 조건입니다. 규칙 동작을 점검하는 것이 목적이며 숫자는 시연 데이터를 확정할 때 다시 봅니다.

| 지표 | 결과 (시드 7) | 시드 1~3 범위 |
|---|---|---|
| 대상 | ACTION 5,721 + NO_TYPE 279 | - |
| 제안할 조치가 0개 | 0.2% | 0.1~0.2% |
| 주 유형 미커버 | 0.2% | 0.2% |
| 두 번째 유형 미커버 (유형 2개 이상 3,765명) | 6.7% | 6.3~7.0% |
| 유형 평균 커버율 | 90.9% | 90.8% |
| 혜택(`offer_cost > 0`) 포함 비율 | **0.6%** | 0.5~0.8% |
| 평균 오퍼 비용 | **28원** | 27~38원 |
| 고객 접점 없이 내부 전달만 나가는 구독 | 1.0% | - |

선택된 11,151건의 채널은 알림톡 7,586 · 내부 전달 2,508 · 친구톡 553 · 상담·앱 504, 구분은 정보제공 5,489 · 담당자조치 5,092 · 혜택제안 356 · 제품조치 214입니다. 제외 사유는 `MAX_ACTIONS` 20,085 · `DUP_CATEGORY` 3,583 · `NO_AD_CONSENT` 2,781 · `COOLDOWN_SOLUTION` 1,031건(후보 단위)입니다.

**광고 수신 동의율 민감도** (친구톡 6건이 걸림)

| 동의율 | 두 번째 유형 미커버 | 유형 평균 커버율 | 선택된 친구톡 | `NO_AD_CONSENT` 제외 |
|---|---|---|---|---|
| 0% | 12.4% | 89.5% | 0 | 5,398 |
| 30% | 8.7% | 90.4% | 335 | 3,868 |
| **50%** | **6.7%** | **90.9%** | 553 | 2,781 |
| 80% | 3.2% | 91.8% | 899 | 1,150 |
| 100% | 0.3% | 92.3% | 1,126 | 0 |

**설정 민감도** (동의율 50%)

| 설정 | 제안 0개 | 주 유형 미커버 | 두 번째 유형 미커버 | 평균 커버율 | 혜택 포함 | 평균 비용 |
|---|---|---|---|---|---|---|
| 같은 솔루션 쿨다운 7일 | 0.0% | 0.0% | 6.5% | 91.1% | 0.3% | 13원 |
| **14일** | 0.2% | 0.2% | 6.7% | 90.9% | 0.6% | 28원 |
| 30일 | 0.4% | 0.5% | 7.2% | 90.5% | 1.4% | 71원 |
| 비용 상한 비율 0.5 | 14일과 동일 | | | | | |
| 동시 조치 1개 | 0.2% | 0.2% | 99.9% | 63.2% | 0.0% | 0원 |
| **동시 조치 2개** | 0.2% | 0.2% | 6.7% | 90.9% | 0.6% | 28원 |
| 동시 조치 3개 | 0.2% | 0.2% | 6.7% | 96.4% | 29.4% | 1,479원 |

**해석과 발견**

- **유형 커버는 구 카탈로그보다 좋습니다**(평균 커버율 86.3% → 90.9%, 주 유형 미커버 5.5% → 0.2%). 유형마다 솔루션이 2~5건 있고 대부분 비용이 0이어서 상한에 안 걸리기 때문입니다.
- **쿨다운 14일·동시 조치 2개·비용 상한 1.0은 그대로 둬도 됩니다.** 동시 조치 1개는 두 번째 유형을 거의 못 덮고, 3개는 혜택 포함이 29%로 늘지만 시작값이 아니라 선택지로 남깁니다.
- **비용 상한은 사실상 작동하지 않습니다**: 비용이 있는 활성 솔루션이 2건(`S-T01-E`, `S-T06-D`; `S-T07-C`는 2026-10-08 비활성)뿐이고, 정렬 키에 `offer_cost`가 들어 있어 비용 0인 안내·조치가 항상 먼저 뽑히기 때문입니다(혜택 포함 0.6%). 상한을 0.5로 낮춰도 결과가 같습니다. 비용 상한 규칙은 안전장치로 유지하되, 시연에서 혜택이 보이게 하려면 카탈로그 쪽 결정이 필요합니다.
- **정렬 키의 마지막(`solution_id`)이 편중을 만듭니다**: 비용·구분이 같으면 알파벳이 앞선 솔루션이 이깁니다. T05에서 `S-T05-A`(내부 전달서)가 1,709건으로 최다이고 문제 직접 대응인 `S-T05-C`(48시간 무상 재케어)는 68건뿐입니다.
- **시점 조건(`trigger_note`)을 규칙이 보지 않아** 조건에 맞지 않는 솔루션이 후보가 될 수 있습니다(예: 유지 36개월 대상인 `S-T01-E`).
- 위 두 가지는 `priority` 컬럼과 `eligibility`(입력 `facts` 기준)로 풀었습니다. 설계 (6)에서 반영 완료: §4.8, `solution_catalog_design.md` §4. (초안에서 `eligibility_flags`라 부르던 입력은 `facts`로 바뀜)

---

### 4.8 Rule Engine v3 (2026-10-07) `[제안]`

v2에서 바뀐 것은 세 가지입니다(근거: 4.7의 F1~F3, 수치는 `solution_catalog_design.md` §4).

1. **정렬 키**: `(-contribution_share, type_rank, priority, offer_cost, solution_id)`. `priority`는 카탈로그 값(작을수록 먼저, 기본 50)이라 `solution_id` 알파벳이 순서를 정하던 편향이 사라집니다.
2. **자격 조건**: 후보 생성 직후 `광고 동의 → eligibility → 쿨다운` 순으로 거릅니다. 입력 `facts`(예: `days_to_end`, `unapplied_discount_yn`)가 카탈로그 `eligibility` 조건 `[fact, op, value]`(AND)을 만족해야 합니다. `facts`에 없거나 `null`이면 통과이며, 불충족이면 `excluded`에 `NOT_ELIGIBLE`과 첫 실패 조건을 남깁니다. 비활성(`INACTIVE`)·전사 개선(`agent_selectable_yn='N'`)은 v2처럼 조용히 제외합니다.

3. **만족도 조사 대체(2026-10-07 팀 결정)**: ACTION인데 최종 후보가 0건이면(자격 미충족·쿨다운·광고 동의 없음 등) `S-NT-A` 만족도 조사를 후보로 삼아 NO_TYPE 경로로 처리합니다(`fallback='NO_ELIGIBLE_CANDIDATE'`, `uncovered_types`에는 원래 유형을 남김). `S-NT-A`도 14일 쿨다운이면 '제안할 조치 없음'입니다. 설정 `fallback_survey_when_empty`로 끌 수 있습니다.

4. **v3.1 점검 반영(2026-10-08)**:
   - 광고 수신 동의는 `customer.ad_consent_yn` → `subscription.ad_consent_yn` 순으로 읽고 값이 없으면 **미동의**(광고성 정보는 사전 동의가 원칙). 이전에는 값이 없으면 동의로 봤고 데모에서 광고 친구톡이 나갔습니다.
   - `eligibility` 조건에 4번째 값 `"required"`가 붙은 조건(프로모션 종료, 미적용 할인, 예정 방문, 매니저 교체 확정, 열린 A/S 등 '사건'을 말하는 조건. 조건이 있는 솔루션 32개의 조건 43개 중 35개)은 값이 없으면 `NOT_ELIGIBLE`. 확인되지 않은 일을 고객에게 단정해 안내하지 않기 위함입니다. `unknown_required_fact: pass`로 이전 동작으로 되돌릴 수 있습니다 `[제안]`.
   - facts를 구독 속성으로 보충(만료·프로모션 남은 일수, 이용 개월, 체험 종료(시작일 + 7일), 계약 연도 종료).
   - `SURVEY_ONLY`도 `S-NT-A`의 활성·14일 쿨다운을 확인. 숫자 문자열 fact("3")는 숫자로 비교.
   - 기준일보다 뒤 날짜의 조치 이력은 '오늘 한 조치'로 봄(쿨다운·누적 비용 포함, 표시 일수 0).

AgentInput에 `customer`·`facts`·`message_facts` 필드가 추가되었고(§3.2), 나머지 입출력·한도·쿨다운·구분 중복 규칙은 v2와 같습니다. 호환성은 v2의 38개 규칙 테스트가 v3 엔진에서 그대로 통과하는 것으로 확인했고(`priority` 없는 카탈로그는 모두 50), v3 전용 테스트는 `churn_agent/tests/test_rules_v3.py`입니다.

## 5. 스키마·설정에 미치는 영향 (2026-10-08 기준 모두 반영)

Agent 설계로 인해 `data_schema_design.md`와 `schema_postgres.sql`에 아래 변경이 필요합니다. 유형 10개(2026-10-07)와 솔루션 38건(2026-10-07) 확정에 따라 아래 표의 `solution_catalog`·`customer`·`action_log.channel` 항목은 `schema_postgres.sql`에 반영했고(PG16 검증), 설문·후보 테이블과 나머지는 설계 (6) 이후 반영합니다 `[제안]`.

| 대상 | 변경 | 이유 |
|---|---|---|
| `action_log.action_status` | `('제안됨','조치완료','반려')`로 반영(코드는 `조치완료`·`반려`만 기록, `제안됨`은 DDL 기본값으로 남김) | 사용자는 확정/반려만 함. 제안 단계는 `agent_result`에 저장 |
| `action_log` | `title`, `internal_memo`, `message_source`, `decision_note`, `forward_depts`, `memo_edited_yn` 반영. 선택 근거(`selection_reason`)는 `agent_result.result_json`에 있어 컬럼을 두지 않음 | 발송·전달 내용, LLM/템플릿 구분, 반려 사유 |
| `churn_prediction` | `risk_rank_pct NUMERIC(6,5)` 반영 | LOW 순위 20~30% 판정 |
| `churn_type` / `solution_catalog` | pseudo-type `유형없음`과 만족도 조사(`S-NT-A`)·상담 연결 솔루션 행 (시드 완료: `churn_type` 11행, `S-NT-A` 포함 `solution_catalog` 39행. 상담 연결은 `S-T02-B`) | `NO_TYPE`, `SURVEY_ONLY` 후보 |
| `solution_catalog` | 확정 반영 완료: `fee_impact`(`없음`/`증가`/`감소`), `channel`, `ad_yn`, `internal_forward_yn`, `forward_dept`, `delivery_kind`, `agent_selectable_yn`, `trigger_note`, `buttons`, `new_policy_yn`. 시드는 `seed_solution_catalog.sql` | 채널·광고성·내부 전달·전사 개선을 Rule Engine이 구분하고, 요금이 변하는 제안에 총 비용 병기 규칙을 적용 |
| `customer` / `action_log` | `customer.ad_consent_yn` 추가(광고 수신 동의), `action_log.channel`을 4종 CHECK로 개정 | 친구톡 발송 조건, 조치 이력의 채널 구분 |
| `solution_catalog` | 고객 부담이 늘어나는 제안(업그레이드·추가 구독·제품 교체)의 `offer_cost`는 0(얹는 할인만 별도 금액). 확정 카탈로그에서는 `S-T03-B` 신모델 갈아타기(같은 요금)가 해당 | 비용 상한 대상이 아님 |
| `config.yaml` | `rules`·`facts` 블록(§4.1) 반영 | 수치를 한곳에서 관리 |

## 6. 다음 단계

1. 이 문서(입출력 계약 + Rule Engine) 팀 검토 → 수정 사항 반영
2. 설계 (3) LLM 프롬프트: **`agent_prompt_design.md`로 작성함**(시스템 프롬프트, 출력 JSON 강제 방식, 문구 규칙, `NO_TYPE` 설문 안내 문구)
3. 설계 (4) 검증기·폴백: 검증 항목, 숫자 대조 방식, 재시도·템플릿 폴백, 응답 시간·비용 한도
4. 설계 (5) 테스트 사례: **`agent_test_cases.md`로 작성함**(Rule Engine 단위 테스트 38건 + 시연 구독자 8명, 모두 통과)
5. ~~설계 (6) 코드 구조~~ → `agent_code_design.md`, `churn_agent/` 완료(2026-10-07~08)
6. ~~만족도 조사 항목·솔루션 구분 체계 확정 후 카탈로그 값 반영~~ → 완료. 내부 전달서 출력, `(광고)`·`fee_impact='감소'` 규칙, `priority`·`eligibility`(`facts`) 모두 반영

## 7. 결정 상태 (2026-10-06, 10유형·솔루션 확정 반영 2026-10-07)

| 항목 | 상태 |
|---|---|
| 확정/반려는 항목 단위 + "전체 확정" 버튼 | `[확정]` |
| 반려한 조치는 쿨다운에서 제외(`조치완료`만 셈), 반려 이력은 저장 | `[확정]` |
| ~~후보가 0개이면 "제안할 조치 없음"만 표시~~ → 후보 0건이면 만족도 조사로 대체 | `[확정 2026-10-07]` (번복) |
| 쿨다운 14일, 비용 상한 월 요금 × 1.0, 동시 조치 2개 | 시뮬레이션으로 타당성 확인(§4.7). 시연 데이터 확정 시 재확인 |
| 선택 방식 v2(그리디 + 유형별 대표 1개 우선) | `[확정]` (2026-10-06, 시뮬레이션 성능 근거) |
| 비용 상한은 월 요금 대비 비율(1회 상한 × 1.0) | `[확정]` |
| 업그레이드·추가 구독·제품 교체 제안은 고객 부담이라 `offer_cost = 0`(상한 제외), 늘어나는 금액·총 비용은 문구에 병기 | `[확정]` (2026-10-06) |
| 90일 누적 한도: 최근 90일 혜택 합계 ≤ 월 요금 × 2.0 (구독 규모에 비례) | 방식 `[확정]`, 2.0은 `[가정]` 시작값 |
| `offer_cost` = 무료 서비스 범위를 넘어 회사가 추가 부담하는 금액. 문제 대응 A/S·교환은 0원이라 상한 제외, 추가 비용이 드는 솔루션은 상한 적용 | `[확정]` (2026-10-06) |
| 같은 구분 쿨다운(7일) | 삭제 `[확정]` (2026-10-06, 영향 없음) |
| 솔루션 38건과 채널 4종 | `[확정]`(2026-10-07, `solution_catalog_design.md`). 구분 배정·`offer_cost`는 `[제안]`·`[가정]` |
| 광고성(친구톡) 솔루션은 `ad_consent_yn='Y'` 고객에게만, 값이 없으면 미동의로 보고 `NO_AD_CONSENT` 제외 | `[제안]` (2026-10-08 기본값 수정, 동의율 50%는 `[가정]`) |
| 전사 개선 3건은 Agent 후보 제외(집계 리포트로 운영), 내부 전달 4건은 후보 포함 | `[제안]` |
| `priority`·`eligibility`(`facts`) 도입 (정렬 편중·시점 조건 반영) | `[제안]` 반영 완료 (§4.8), 후보 0건이면 만족도 조사로 대체 `[확정 2026-10-07]` |
| 만족도 조사 항목(`S-NT-A`) | 기존 설계 유지 `[제안]`, 엑셀 밖 항목 |
| '사건' 조건(`required`)의 값이 없으면 후보 제외 | `[제안]` (2026-10-08, 설정으로 해제 가능) |
| 고객 메시지는 알림톡 보고서 형식(제목 + ▶ 항목 + 버튼), ▶ 값은 고객 공개 값만 | `[확정 2026-10-08]` |
