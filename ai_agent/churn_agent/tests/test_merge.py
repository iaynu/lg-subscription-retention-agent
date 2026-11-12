from agent.service import merge_messages

def S(sid, prio, ch, msg, btns, title="제목"):
    return dict(solution_id=sid, priority=prio, channel=ch, message_text=msg, buttons=btns, title=title)
def C(ad="N", dk="고객 메시지"): return dict(ad_yn=ad, delivery_kind=dk)

def test_same_channel_merged_in_priority_order():
    sel = [S("B", 2, "알림톡", "둘째 안내입니다.", ["조건 보기", "상담"]), S("A", 1, "알림톡", "첫째 안내입니다.", ["상담", "앱 열기"], "첫 제목")]
    m = merge_messages(sel, {"A": C(), "B": C()})
    assert len(m) == 1 and m[0]["merged"] and m[0]["solution_ids"] == ["A", "B"]
    assert m[0]["message_text"] == "첫째 안내입니다.\n\n둘째 안내입니다." and m[0]["title"] == "첫 제목 외 1건"
    assert m[0]["buttons"] == ["상담", "앱 열기", "조건 보기"]

def test_ad_and_non_ad_never_merged():
    sel = [S("A", 1, "알림톡", "정보 안내입니다.", []), S("B", 2, "친구톡", "혜택 안내입니다.", [], "(광고) 혜택")]
    m = merge_messages(sel, {"A": C("N"), "B": C("Y")})
    assert len(m) == 2 and not any(x["merged"] for x in m)
    assert [x["channel"] for x in m] == ["알림톡", "친구톡"] and m[1]["title"].startswith("(광고)")

def test_two_ads_merged_keep_ad_prefix():
    sel = [S("A", 1, "친구톡", "혜택 하나입니다.", [], "(광고) 하나"), S("B", 2, "친구톡", "혜택 둘입니다.", [], "(광고) 둘")]
    m = merge_messages(sel, {"A": C("Y"), "B": C("Y")})
    assert len(m) == 1 and m[0]["title"].startswith("(광고)")

def test_internal_only_excluded_and_composite_customer_part_merged():
    sel = [S("A", 1, "내부 전달", "", []), S("B", 2, "알림톡", "고객 안내입니다.", ["확인"])]
    m = merge_messages(sel, {"A": C("N", "내부 전달"), "B": C()})
    assert len(m) == 1 and m[0]["solution_ids"] == ["B"] and not m[0]["merged"]

def test_button_limit_and_title_limit():
    sel = [S("A", 1, "알림톡", "가 안내입니다.", [f"b{i}" for i in range(4)], "가" * 40), S("B", 2, "알림톡", "나 안내입니다.", ["x", "y"])]
    m = merge_messages(sel, {"A": C(), "B": C()})[0]
    assert len(m["buttons"]) == 5 and m["buttons_truncated"] and len(m["title"]) <= 40

def test_end_to_end_messages_field(catalog):
    from agent.service import AgentService
    from agent.config import load_config
    from agent.llm.mock import RuleBasedMock
    from agent.repo.memory import MemoryRepo
    import json
    from .conftest import base_input
    ai = base_input([("T02", 0.6), ("T06", 0.4)], facts={"move_signal_yn": "Y", "has_upcoming_visit": "Y"})
    res = AgentService(load_config(), catalog, RuleBasedMock(), MemoryRepo()).run(ai)
    ids = [s["solution_id"] for s in res["selected"]]
    sent = [i for m in res["messages"] for i in m["solution_ids"]]
    assert set(sent) <= set(ids) and len(res["messages"]) <= len(ids)
