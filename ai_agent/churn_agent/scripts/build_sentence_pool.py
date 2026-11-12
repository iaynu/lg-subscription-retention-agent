"""주관식 문장 풀의 변형 문장을 LLM으로 한 번만 만드는 오프라인 스크립트 (설계: survey_simulator_design.md §3.9).

- 시연·합성 중에는 이 스크립트를 쓰지 않는다. 합성기는 feedback/sentence_pool*.yaml 만 읽는다.
- API 키는 환경변수 OPENAI_API_KEY 에서만 읽고 어디에도 쓰지 않는다. 오류 메시지의 키 형태 문자열은 가린다.
- 결과는 후보 파일(feedback/sentence_pool_variants.candidate.yaml)에만 쓴다. 팀이 검토해서 승인한 문장만
  feedback/sentence_pool_variants.yaml 의 variants 아래로 옮기면 합성기에 반영된다.

사용:  python scripts/build_sentence_pool.py --dry-run          # 프롬프트만 출력(호출 없음)
       python scripts/build_sentence_pool.py --per-code 25      # 실제 호출 (코드당 1회)
"""
from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml
from feedback import pool as P

DESC = {
    "cost": "요금·청구 금액이 부담되거나 예상과 다름", "exit": "해지·이전 설치가 어렵거나 위약금이 부담됨", "renew": "재계약·갈아타기 안내와 조건에 불만",
    "idle": "제품을 잘 쓰지 않는데 요금이 나가서 아까움", "care": "케어(관리) 서비스를 받은 뒤 문제가 있거나 효과를 못 느낌",
    "visit": "방문 일정이 미뤄지거나 취소되거나 조율이 어려움", "manager": "담당자가 자주 바뀌거나 태도·설명에 불만", "sales": "방문 중 영업·추가 상품 권유가 부담됨",
    "fail": "같은 고장이 반복되거나 소음·성능에 불만", "cs": "고객센터 연결·응대·해결 확인에 불만",
    "H-A": "가입할 때 들은 서비스 범위·조건과 실제가 달라서 실망함", "H-B": "케어가 직접 관리와 다른지 가치를 모르겠고 굳이 돈을 내야 하나 싶음",
    "sat": "특별한 불편 없이 만족하거나 바라는 것이 없음", "other": "하나로 말하기 어려운 전반적 아쉬움이나 앱·안내 같은 기타 불만",
    "issue_other": "'기타'를 고른 사람이 적는 아주 짧은 직접 입력(5~15자 명사구)",
}
SYSTEM = ("너는 가전 구독 서비스 고객 설문의 자유 서술 답변 예시를 만드는 작성자다. 가상의 고객 말투(구어체 존댓말)로, 시드 문장과 같은 의미를 "
          "표현·문체·길이를 바꿔 다양하게 쓴다. 규칙: 1인칭 한 문장(또는 두 문장), 20~80자, 전화번호·이메일·주소·URL·브랜드명·실명 금지, "
          "시드 문장과 똑같은 문장 금지, 서로 겹치는 문장 금지, 구체적인 금액·날짜 숫자는 쓰지 않는다.")
SCHEMA = {"type": "object", "properties": {"variants": {"type": "array", "items": {"type": "string"}}}, "required": ["variants"], "additionalProperties": False}
_KEY = re.compile("s" "k-[A-Za-z0-9_\\-]{8,}")      # 소스에 키 접두사 문자열을 그대로 두지 않는다(저장소 비밀 검사 테스트)


def scrub(msg: str) -> str: return _KEY.sub("[KEY]", str(msg))


def build_prompt(code: str, seeds: list[str], n: int) -> str:
    return f"[주제] {DESC[code]}\n[시드 문장]\n" + "\n".join(f"- {s}" for s in seeds) + f"\n\n위 시드와 같은 의미의 새 답변 {n}개를 만들어라."


def clean(code: str, seeds: list[str], raw: list[str], n: int) -> tuple[list[str], list[tuple[str, str]]]:
    """검사를 통과하고 시드·서로와 겹치지 않는 문장만 최대 n개. 걸러진 문장과 이유도 돌려준다."""
    out, rejected, seen = [], [], set(seeds)
    for s in raw:
        s = (s or "").strip()
        why = P.check_sentence(s) if code != "issue_other" or len(s) >= 2 else "길이"
        if why is None and s in seen: why = "중복"
        if why: rejected.append((s, why)); continue
        out.append(s); seen.add(s)
        if len(out) >= n: break
    return out, rejected


def generate(client, model: str, code: str, seeds: list[str], n: int, timeout: float = 60.0) -> list[str]:
    try:
        r = client.chat.completions.create(model=model, timeout=timeout,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": build_prompt(code, seeds, n + 5)}],   # 걸러질 몫을 감안해 조금 더 요청
            response_format={"type": "json_schema", "json_schema": {"name": "variants", "strict": True, "schema": SCHEMA}})
    except Exception as e:
        raise RuntimeError(scrub(f"{type(e).__name__}: {e}")) from None
    ch = r.choices[0]
    if getattr(ch.message, "refusal", None) or ch.finish_reason != "stop": raise RuntimeError(f"{code}: 응답이 정상 종료되지 않음({ch.finish_reason})")
    return json.loads(ch.message.content)["variants"]


def run(client, model: str, per_code: int, codes=None) -> dict:
    seeds = yaml.safe_load(P.POOL_FILE.read_text(encoding="utf-8"))["seeds"]
    result, report = {}, {}
    for code in (codes or P.required_keys()):
        n = min(per_code, 12) if code == "issue_other" else per_code
        good, bad = clean(code, seeds[code], generate(client, model, code, seeds[code], n), n)
        result[code] = good; report[code] = {"요청": n, "통과": len(good), "제외": len(bad)}
    return {"variants": result, "report": report}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="생략하면 config.yaml의 llm.sentence_pool_model"); ap.add_argument("--per-code", type=int, default=25)
    ap.add_argument("--out", default=str(P.HERE / "sentence_pool_variants.candidate.yaml")); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    seeds = yaml.safe_load(P.POOL_FILE.read_text(encoding="utf-8"))["seeds"]
    if a.dry_run:
        for code in P.required_keys(): print(f"=== {code} ===\n{build_prompt(code, seeds[code], a.per_code + 5)}\n")
        print("(dry-run: 호출 없음)"); return 0
    key = os.environ.get("OPENAI_API_KEY")
    if not key: print("환경변수 OPENAI_API_KEY가 없습니다."); return 2
    from openai import OpenAI
    client = OpenAI(api_key=key, max_retries=2)
    from feedback.candidates import default_model
    res = run(client, a.model or default_model("sentence_pool_model"), a.per_code)
    header = "# LLM 변형 문장 후보 — 팀 검토 전. 승인한 문장만 sentence_pool_variants.yaml 의 variants 아래로 옮긴다.\n"
    Path(a.out).write_text(header + yaml.safe_dump({"variants": res["variants"]}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(yaml.safe_dump(res["report"], allow_unicode=True)); print(f"후보 저장: {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
