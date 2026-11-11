"""엑셀 '유형요약' 시트 -> data/catalog/solution_catalog.json (한 번만 실행, 결과 JSON은 저장소에 포함).
사용: python tools/build_catalog.py 엑셀파일.xlsx
offer_cost 와 solution_category 는 팀 확정 전 [가정] 값이다(아래 ANNOT)."""
import json, re, sys
from pathlib import Path
import openpyxl

TYPE_ID = {'구독료 부담형':'T01','해지·이사 장벽형':'T02','재계약·갈아타기 갈등형':'T03','사용 빈도 저하·방치형':'T04',
           '케어 부실·피해형':'T05','방문 일정 불편형':'T06','매니저 품질 불만형':'T07','영업 압박형':'T08',
           '제품 고장·성능 불만형':'T09','AS·고객센터 불만형':'T10'}
GROUP = {'T05':'케어 서비스 불만','T06':'케어 서비스 불만','T07':'케어 서비스 불만',
         'T09':'제품·사후처리 불만','T10':'제품·사후처리 불만','T03':'제품·사후처리 불만',
         'T01':'구독료 부담','T08':'영업·해지 장벽','T02':'영업·해지 장벽','T04':'사용 저하'}
# solution_id: (구분, 오퍼비용[가정], 고객에게도 가는 내부전달 병행 여부)
ANNOT = {
 'S-T01-1':('정보제공',0),'S-T01-2':('정보제공',0),'S-T01-3':('혜택제안',0),'S-T01-4':('혜택제안',5000),'S-T01-5':('혜택제안',50000),
 'S-T02-1':('정보제공',0),'S-T02-2':('담당자조치',3000),
 'S-T03-1':('정보제공',0),'S-T03-2':('혜택제안',30000),'S-T03-3':('혜택제안',20000),
 'S-T04-1':('정보제공',0),'S-T04-2':('정보제공',0),'S-T04-3':('정보제공',0),'S-T04-4':('혜택제안',0),
 'S-T05-1':('담당자조치',0),'S-T05-2':('정보제공',0),'S-T05-3':('담당자조치',15000),
 'S-T06-1':('정보제공',0),'S-T06-2':('정보제공',0),'S-T06-3':('담당자조치',0),'S-T06-4':('혜택제안',5000),
 'S-T07-1':('담당자조치',0),'S-T07-2':('정보제공',0),'S-T07-3':('담당자조치',0),'S-T07-4':('담당자조치',20000),
 'S-T08-1':('담당자조치',0),'S-T08-2':('혜택제안',0),
 'S-T09-1':('정보제공',0),'S-T09-2':('정보제공',0),'S-T09-3':('제품조치',0),'S-T09-4':('제품조치',30000),'S-T09-5':('제품조치',0),
 'S-T10-1':('정보제공',0),'S-T10-2':('정보제공',0),'S-T10-3':('담당자조치',5000),'S-T10-4':('제품조치',40000),
}

def main(xlsx, out):
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    rows = list(wb['유형요약'].iter_rows(values_only=True))
    cur, n, types, sols = None, 0, {}, []
    for r in rows[2:]:
        if r[0] and str(r[0]).strip():
            cur = TYPE_ID[str(r[0]).strip()]; n = 0
            types[cur] = {'type_id':cur,'type_name':str(r[0]).strip(),'group_name':GROUP[cur],
                          'description':(r[1] or '').strip()}
        if not r[2]: continue
        n += 1; sid = f'S-{cur}-{n}'
        name = re.sub(r'\s*\n\s*', ' ', str(r[2]).strip())
        tgt = str(r[4] or '').replace('\n', ' ').strip()
        sample = (r[5] or '').strip()
        if sample.startswith('(내부 전달서)'): kind = 'internal'
        elif sample.startswith('(광고)'): kind = 'ad'
        else: kind = 'info'
        target = '둘 다' if ('사용자' in tgt and '내부' in tgt) else ('LG 내부 전달' if ('내부' in tgt or not tgt) else '사용자')
        if target == 'LG 내부 전달': kind = 'internal'
        cat, cost = ANNOT[sid]
        sols.append({'solution_id':sid,'type_id':cur,'solution_name':name,'solution_category':cat,
                     'solution_detail':(r[3] or '').strip(),'target':target,'message_kind':kind,
                     'offer_cost':cost,'sample_message':sample,'priority':n,'active_yn':'Y'})
    assert len(sols) == len(ANNOT), (len(sols), len(ANNOT))
    Path(out).write_text(json.dumps({'types':list(types.values()),'solutions':sols}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('types', len(types), 'solutions', len(sols))
    for s in sols: print(s['solution_id'], s['target'], s['message_kind'], s['solution_category'], s['offer_cost'], s['solution_name'][:30])

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else 'data/catalog/solution_catalog.json')
