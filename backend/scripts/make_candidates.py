"""설문 응답에서 신규 이탈 유형 후보를 만들어 ai_proposal 에 넣는다(팀원 feedback 단계 사용).

  python scripts/make_candidates.py --in data_in --provider mock            # 키 없이 연습(모의 규칙)
  python scripts/make_candidates.py --in data_in --provider openai --runs 3   # 실제 LLM (OPENAI_API_KEY 필요). 모델은 model/config.yaml 의 llm.candidate_model(gpt-5.4-mini)

필요한 패키지: pandas numpy pyyaml (+ openai, 실제 LLM 쓸 때만).  실행 후 화면의 'New Types' 탭에서 검토·채택한다.
OpenAI 로 3회 실행하면 1회 약 190원 수준이다(팀원 측정). 같은 후보가 2회 이상 반복된 것만 남긴다.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'model'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', default='data_in', help='survey_send/response/answer.csv 가 있는 폴더')
    ap.add_argument('--provider', default='mock', choices=['mock', 'openai'])
    ap.add_argument('--model', default=None, help='생략하면 model/config.yaml 의 llm.candidate_model')
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--min-support', type=int, default=8)
    ap.add_argument('--keep', action='store_true', help='기존 검토중 후보를 지우지 않고 추가')
    a = ap.parse_args()
    inp = Path(a.inp)
    inp = inp if inp.is_absolute() else (ROOT / inp if (ROOT / inp).exists() else Path.cwd() / inp)
    from feedback import candidates as C
    from feedback.cli import run_candidates
    res, _ = run_candidates(str(inp), provider=a.provider, model=a.model, min_support=a.min_support, runs=a.runs, verbose=True)
    rows = C.to_rows(res, res['pack_stats']['n_responses'])
    from api import proposals
    n = proposals.load_proposal_rows(rows, reset=not a.keep)
    print(f'ai_proposal 에 {n}건 저장 — 화면 New Types 탭에서 확인하세요.')


if __name__ == '__main__':
    main()
