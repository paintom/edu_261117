# SteelQ Report Agent

철강 생산·품질 CSV/엑셀을 업로드하면 데이터 정제 → 일별·월별 집계 → 결함 유형 분석 → 이상치 탐지 → 규칙 기반 분석 코멘트 → 차트 포함 엑셀 리포트까지 자동으로 처리하는 Streamlit 웹 앱입니다. 요구사항은 [PRD.md](PRD.md)를 참고하세요.

> 현재 진행 단계: **4단계(종합 검토·수정)** 완료. 다음은 Streamlit Community Cloud 배포입니다.
>
> Python **3.12**에서 검증했습니다(pandas 3.x는 3.11 이상 필요). Community Cloud 배포 시 Advanced settings에서 Python 3.12를 선택하세요.

## 로컬 실행 (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python generate_sample.py      # data/ 에 샘플 데이터 생성
streamlit run app.py           # http://localhost:8501
pytest                         # 테스트
```

## 입력 데이터 형식

| 컬럼 | 설명 |
|---|---|
| 일자 | YYYY-MM-DD |
| 라인 | 생산 라인 |
| 강종 | 강종 코드 |
| 생산량 | 톤 |
| 스크래치, 압연흠, 스케일, 치수불량, 표면균열 | 결함 유형별 불량량(톤) |

- 1행 = 일자 × 라인 × 강종
- 일자 형식: `2026-10-01`(권장), `2026/10/01`, `2026.10.01`, `20261001`, `2026년 10월 1일`, 엑셀 날짜 모두 인식. `10/02/2026`처럼 연도가 뒤에 오면 월/일 순으로 해석하고 정제 로그에 경고를 남깁니다.
- 결함 컬럼이 파일에 없으면 화면에서 '0으로 처리'를 선택할 수 있습니다.
- CSV는 UTF-8 / CP949(엑셀 기본 저장) 모두 지원, XLSX·XLS는 시트 선택 가능
- 컬럼명이 달라도(예: `Date`, `Line`, `Qty`, `생산량(톤)`) 자동 인식하며, 인식하지 못한 컬럼은 화면에서 직접 지정

## 샘플 데이터

`python generate_sample.py` (옵션: `--seed`, `--start`, `--end`, `--out`)

- 2026-08-01 ~ 2026-10-31, 4개 라인 × 2~3개 강종, seed 42로 재현 가능
- 테스트를 위해 중복·결측·음수·콤마 문자열과 이상치(불량률 급등, 생산량 급감)를 의도적으로 주입
- 주입 내역은 `data/injected_anomalies.csv`에 기록 (정제·이상치 탐지 검증 기준)

## 주의

공개 배포 URL에서는 실제 사내 생산·품질 데이터를 업로드하지 마세요. 업로드 파일은 서버에 저장되지 않고 세션 메모리에서만 처리됩니다.
