import copy, sys
from pathlib import Path
import pytest, yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from datagen.config import load_config, validate, expected_counts, as_of, HERE


def base(): return yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))


def test_default_config_is_valid():
    c = load_config(); assert c["run"]["seed"] == 42 and as_of(c).isoformat() == "2026-09-30"


def test_expected_scale_matches_design_doc():
    e = expected_counts(load_config())
    assert (e["total"], e["normal"], e["active"]) == (30000, 24000, 6000)          # 설계서 §6: 정상 24,000 / 활성 6,000
    assert e["by_active_count"] == {1: 2100, 2: 2400, 3: 1500}                        # 설계서 표 (3만 명 기준)
    assert e["by_product"]["정수기"] == 3600                                          # 설계서: 정수기 약 3,600건


def test_confirmed_values_pinned():
    c = load_config(); p = c["population"]["products"]
    assert {k: v["contract_months"] for k, v in p.items()} == {"의류관리기": 60, "건조기": 72, "냉장고": 60, "세탁기": 72, "에어컨": 60, "정수기": 48}
    assert p["정수기"]["visit_interval_months"] == 4 and all(v["visit_interval_months"] == 12 for k, v in p.items() if k != "정수기")
    assert max(int(k) for k in c["types"]["active_count_weights"]) == 3
    assert c["label"]["b0"] == -4.6 and c["label"]["w_scale"] == 1.0 and c["label"]["targets"]["normal_rate"] == 0.01


def test_type_weights_follow_review_plus_uniform_rule():
    c = load_config(); rc = c["types"]["review_counts"]; tot = sum(rc.values())
    for t, w in c["types"]["type_weights"].items():
        assert abs(w - (0.5 * rc[t] / tot + 0.05)) < 0.0006, t                         # 리뷰 50% + 균등 50%


def test_overrides_and_validation_errors():
    assert load_config(run={"n_subscriptions": 5000})["run"]["n_subscriptions"] == 5000
    for mut, msg in [
        (lambda c: c["population"]["products"]["정수기"].update(weight=0.5), "비중 합"),
        (lambda c: c["types"]["active_count_weights"].update({4: 0.0}), "최대 3개"),
        (lambda c: c["types"]["type_weights"].update(T01=0.5), "type_weights 합"),
        (lambda c: c["label"]["type_w"].pop("T10"), "label.type_w"),
        (lambda c: c["signals"]["T01"].update(voc_fee_cnt_90d=[2]), "두 값"),
        (lambda c: c["run"].update(as_of_date="2026-13-01"), "as_of_date"),
        (lambda c: c["label"].update(churn_day_range=[0, 30]), "churn_day_range"),
    ]:
        c = base(); mut(c)
        with pytest.raises(ValueError, match=msg): validate(c)
