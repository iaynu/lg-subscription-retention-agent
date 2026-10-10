-- 고아 행 검사: 각 논리 관계마다 부모가 없는 자식 행 수. 모두 0이어야 한다. (schema_postgres.sql '논리 관계'에서 생성)
SELECT 'solution_catalog.type_id -> churn_type.type_id' AS relation, COUNT(*) AS orphans FROM solution_catalog c LEFT JOIN churn_type p ON c.type_id = p.type_id WHERE c.type_id IS NOT NULL AND p.type_id IS NULL
UNION ALL
SELECT 'subscription.customer_id -> customer.customer_id' AS relation, COUNT(*) AS orphans FROM subscription c LEFT JOIN customer p ON c.customer_id = p.customer_id WHERE c.customer_id IS NOT NULL AND p.customer_id IS NULL
UNION ALL
SELECT 'subscription.product_category -> product_profile.product_category' AS relation, COUNT(*) AS orphans FROM subscription c LEFT JOIN product_profile p ON c.product_category = p.product_category WHERE c.product_category IS NOT NULL AND p.product_category IS NULL
UNION ALL
SELECT 'subscription.care_manager_id -> care_manager.manager_id' AS relation, COUNT(*) AS orphans FROM subscription c LEFT JOIN care_manager p ON c.care_manager_id = p.manager_id WHERE c.care_manager_id IS NOT NULL AND p.manager_id IS NULL
UNION ALL
SELECT 'usage_thinq.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM usage_thinq c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'care_visit.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM care_visit c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'care_visit.manager_id -> care_manager.manager_id' AS relation, COUNT(*) AS orphans FROM care_visit c LEFT JOIN care_manager p ON c.manager_id = p.manager_id WHERE c.manager_id IS NOT NULL AND p.manager_id IS NULL
UNION ALL
SELECT 'voc_contact.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM voc_contact c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'payment.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM payment c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'notice_log.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM notice_log c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'churn_prediction.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM churn_prediction c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'churn_prediction_type.prediction_id -> churn_prediction.prediction_id' AS relation, COUNT(*) AS orphans FROM churn_prediction_type c LEFT JOIN churn_prediction p ON c.prediction_id = p.prediction_id WHERE c.prediction_id IS NOT NULL AND p.prediction_id IS NULL
UNION ALL
SELECT 'churn_prediction_type.type_id -> churn_type.type_id' AS relation, COUNT(*) AS orphans FROM churn_prediction_type c LEFT JOIN churn_type p ON c.type_id = p.type_id WHERE c.type_id IS NOT NULL AND p.type_id IS NULL
UNION ALL
SELECT 'agent_result.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM agent_result c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'agent_result.prediction_id -> churn_prediction.prediction_id' AS relation, COUNT(*) AS orphans FROM agent_result c LEFT JOIN churn_prediction p ON c.prediction_id = p.prediction_id WHERE c.prediction_id IS NOT NULL AND p.prediction_id IS NULL
UNION ALL
SELECT 'action_log.action_batch_id -> agent_result.request_id' AS relation, COUNT(*) AS orphans FROM action_log c LEFT JOIN agent_result p ON c.action_batch_id = p.request_id WHERE c.action_batch_id IS NOT NULL AND p.request_id IS NULL
UNION ALL
SELECT 'action_log.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM action_log c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'action_log.prediction_id -> churn_prediction.prediction_id' AS relation, COUNT(*) AS orphans FROM action_log c LEFT JOIN churn_prediction p ON c.prediction_id = p.prediction_id WHERE c.prediction_id IS NOT NULL AND p.prediction_id IS NULL
UNION ALL
SELECT 'action_log.type_id -> churn_type.type_id' AS relation, COUNT(*) AS orphans FROM action_log c LEFT JOIN churn_type p ON c.type_id = p.type_id WHERE c.type_id IS NOT NULL AND p.type_id IS NULL
UNION ALL
SELECT 'action_log.solution_id -> solution_catalog.solution_id' AS relation, COUNT(*) AS orphans FROM action_log c LEFT JOIN solution_catalog p ON c.solution_id = p.solution_id WHERE c.solution_id IS NOT NULL AND p.solution_id IS NULL
UNION ALL
SELECT 'survey_send.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM survey_send c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'survey_send.action_batch_id -> agent_result.request_id' AS relation, COUNT(*) AS orphans FROM survey_send c LEFT JOIN agent_result p ON c.action_batch_id = p.request_id WHERE c.action_batch_id IS NOT NULL AND p.request_id IS NULL
UNION ALL
SELECT 'survey_response.survey_id -> survey_send.survey_id' AS relation, COUNT(*) AS orphans FROM survey_response c LEFT JOIN survey_send p ON c.survey_id = p.survey_id WHERE c.survey_id IS NOT NULL AND p.survey_id IS NULL
UNION ALL
SELECT 'survey_answer.response_id -> survey_response.response_id' AS relation, COUNT(*) AS orphans FROM survey_answer c LEFT JOIN survey_response p ON c.response_id = p.response_id WHERE c.response_id IS NOT NULL AND p.response_id IS NULL
UNION ALL
SELECT 'ai_proposal.related_type_id -> churn_type.type_id' AS relation, COUNT(*) AS orphans FROM ai_proposal c LEFT JOIN churn_type p ON c.related_type_id = p.type_id WHERE c.related_type_id IS NOT NULL AND p.type_id IS NULL
UNION ALL
SELECT 'synth_churn_label.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM synth_churn_label c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'synth_truth_type.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM synth_truth_type c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
UNION ALL
SELECT 'synth_truth_type.type_id -> churn_type.type_id' AS relation, COUNT(*) AS orphans FROM synth_truth_type c LEFT JOIN churn_type p ON c.type_id = p.type_id WHERE c.type_id IS NOT NULL AND p.type_id IS NULL
UNION ALL
SELECT 'synth_truth_topic.subscription_id -> subscription.subscription_id' AS relation, COUNT(*) AS orphans FROM synth_truth_topic c LEFT JOIN subscription p ON c.subscription_id = p.subscription_id WHERE c.subscription_id IS NOT NULL AND p.subscription_id IS NULL
ORDER BY orphans DESC, relation;
