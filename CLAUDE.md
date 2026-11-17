# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

SteelQ Report Agent — 철강 생산·품질 CSV/엑셀을 업로드하면 정제 → 집계 → 이상치 탐지 → 규칙 기반 코멘트 → 차트 포함 엑셀 리포트를 생성하는 Streamlit 앱. Python(pandas, openpyxl, Plotly), Streamlit Community Cloud 배포 예정.

- 요구사항 원본: `PRD.md` (기능 ID F1~F8, 코멘트 규칙 C1~C10, 9장 인수 기준). 기능 구현·검토 시 PRD의 ID로 참조한다.
- 단계별 개발 계획: `PROMPTS.md` (1단계 뼈대 → 2단계 기능 → 3단계 UI → 4단계 Playwright 검토 → 배포).
- 현재 상태: 4단계 검토 완료 및 발견된 14개 문제 수정(회귀 테스트 `tests/test_regressions.py`). 다음은 Streamlit Community Cloud 배포.
- 사용자는 모든 답변을 **한국어**로 원함.

## 명령어 (Windows PowerShell)

venv의 Python은 `.venv\Scripts\python` (Git Bash에서는 `.venv/Scripts/python`). 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8`을 설정.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python generate_sample.py          # data/ 에 샘플 CSV(UTF-8-BOM)·XLSX, injected_anomalies.csv 생성
streamlit run app.py               # http://localhost:8501
streamlit run app.py --server.headless true   # Playwright 검토용 백그라운드 실행

pytest                                               # 전체 테스트
pytest tests/test_cleaner.py                         # 파일 단위
pytest tests/test_anomaly.py::test_함수명 -v         # 단일 테스트
pytest -m "not slow"                                 # 10만 행 성능 테스트 제외(빠른 확인)
```

## 아키텍처

단방향 파이프라인이며, pandas 3.x(문자열 기본 dtype, Copy-on-Write) 기준으로 작성한다. Streamlit은 `use_container_width` 대신 `width="stretch"`를 쓴다. `steelq/` 모듈은 Streamlit에 의존하지 않는 순수 pandas 로직으로 유지한다(테스트 용이성·UI 분리). `app.py`만 Streamlit을 import하고, 무거운 계산은 `st.cache_data`로 캐싱한다(캐시된 `LoadedFile`은 수동 매핑 전에 복사해 변경한다). 입력 모드는 `st.session_state["mode"]`(`sample` | `upload`)로 구분한다.

```
loader → cleaner ─→ pipeline.run_analysis(필터 → aggregator · anomaly · commentary) → AnalysisResult
                                                                         ├→ app.py 화면
                                                                         └→ report.build_report → BytesIO
```

- `pipeline.py`: 화면과 리포트가 같은 숫자를 쓰도록 필터·집계·이상치·코멘트를 한 번에 계산해 `AnalysisResult`로 묶는다. 새 집계를 추가하면 여기에 필드를 추가하고 화면·리포트 양쪽에서 사용한다. app.py는 `_clean`/`_analyze`/`_report`를 (병합 데이터, 옵션, `Filters`, `AnalysisParams`) 키로 `st.cache_data` 캐싱한다.

- `config.py`: 표준 컬럼, 컬럼 별칭 사전, 결함 5종(스크래치·압연흠·스케일·치수불량·표면균열), 이상치 임계값, 코멘트 템플릿을 모두 여기에 둔다. 다른 모듈에 상수를 하드코딩하지 않는다.
- `loader.py`: CSV 인코딩을 utf-8-sig → cp949 → euc-kr 순서로 시도(윈도우 엑셀 저장 CSV 대응). 파일마다 `LoadedFile`(원본 + `{원본컬럼: 표준컬럼}` 매핑 + 누락 컬럼)을 만들고, 매핑이 끝난 파일만 `merge_files()`로 병합한다. 병합 결과에는 메타 컬럼 `_원본파일`, `_원본행`(엑셀 행 번호, 헤더=1행)이 붙어 정제 로그의 원본 위치 추적에 쓰인다 — 완전 중복 판정 등에서는 메타 컬럼을 제외해야 한다. 사용자에게 보여줄 오류는 `LoaderError`로 던진다.
- `sample_data.py`: 샘플 생성 로직(앱 버튼과 `generate_sample.py` CLI가 공유). 이상치는 라인-일자 단위로 주입하므로 `injected_anomalies.csv`의 강종은 `전체`. 오류 주입 행은 이상치 행과 겹치지 않는다.
- `cleaner.py`: 정제 결과와 함께 **정제 로그**(규칙, 원본 행번호, 변경 전→후)를 반환한다. 이 로그는 화면·리포트·코멘트(C10)에서 공유된다. 단계 순서(표기 통일 → 숫자 변환 → 일자 → 완전 중복 → 음수 → 키 중복 → 라벨 결측 → 생산량 보간 → 결함 0 → 파생)가 결과에 영향을 주므로 바꿀 때 주의. 규칙 이름 상수(`R_*`)와 C10 분류 집합(`DUPLICATE_RULES` 등)을 함께 갱신할 것. pandas 3에서는 문자열이 섞인 숫자 컬럼 전체가 `str` dtype이 되므로, '그대로 숫자로 읽히는 문자열'은 숫자 변환 로그에서 제외한다.
- `anomaly.py`: IQR·Z-score·이동평균은 라인-일자 단위, 목표 초과·점유율 급변은 공장 전체 일별 단위(라인별 목표 초과는 고불량 라인이 매일 걸려 목록이 무의미해짐). 결과의 강종은 항상 `전체`.
- `commentary.py`: 템플릿은 `config.COMMENT_TEMPLATES`. 결함명 뒤 조사는 `josa_ro()`로 받침에 맞춰 붙인다.
- `report.py`: openpyxl 네이티브 차트로 8개 시트 생성, **BytesIO로만 반환**(업로드·결과 데이터를 디스크에 쓰지 않음 — 공개 배포 앱의 보안 요구사항). 내부 불량률·점유율은 퍼센트 숫자(0.79=0.79%)이므로 엑셀에는 ÷100 후 `0.00%` 서식으로 쓴다. 차트는 `_show_axes()`(축 표시)와 `_no_overlay()`(제목·범례 겹침 방지)를 반드시 적용 — 빠뜨리면 Excel에서 축이 사라지거나 제목이 그래프를 가린다. 생성 시각은 Windows에 tz DB가 없어 고정 오프셋 KST 사용.
- 엑셀 렌더링 확인: 로컬에 Excel이 있으므로 PowerShell COM(`Excel.Application` → `ChartObjects().Chart.Export(png)`)으로 차트를 이미지로 뽑아 볼 수 있다. openpyxl 재로딩만으로는 겹침·축 문제를 알 수 없다.

## 놓치기 쉬운 규칙

- 불량률은 항상 **가중 평균**(Σ불량 ÷ Σ생산). 행별 불량률의 단순 평균 금지. 생산량 0인 행은 불량률 계산에서 제외.
- 총불량량은 결함 5종 합계로 프로그램이 계산한다(입력 컬럼 아님).
- 결함 점유율 합계는 각 기간마다 100%(±0.01)여야 하며 테스트로 검증한다.
- 분석 코멘트는 외부 API 없이 규칙 기반(API 키 없음). 모든 코멘트는 수치 근거를 포함한다.
- 샘플 데이터는 seed 42로 재현 가능해야 하며, 의도적으로 주입한 중복·결측·음수·이상치 내역(`data/injected_anomalies.csv`)이 정제 로그 건수와 이상치 탐지율(≥90%) 검증의 기준이 된다. 생성기를 바꾸면 관련 테스트도 함께 확인할 것.
- 로컬은 Windows, 배포는 Linux(Streamlit Community Cloud). 경로는 `pathlib` 사용, OS별 폰트·경로 의존 금지. 저장소 내 파일명은 영문.
- 화면 심각도 색상(정상/주의/경고)과 엑셀 리포트 조건부 서식 색상은 같은 팔레트를 사용한다.

## UI · 차트 규칙

- 화면 구성: 테마는 `.streamlit/config.toml`(Pretendard 웹폰트, 강조색 주황 하나, 표 머리글 네이비), 보조 CSS는 `assets/style.css`, 색 상수는 `steelq/config.py`의 `BRAND_*`·`SEVERITY_*`·`DEFECT_COLORS`. 세 곳의 색을 함께 맞춘다.
- **보조축(이중 y축) 차트 금지** — 화면·엑셀 모두 생산량과 불량률은 별도 패널/차트로 그린다(PRD F6·F7의 "이중축" 문구보다 우선). Pareto도 누적선 대신 라벨로 표기.
- 결함 유형 색은 유형에 고정(`DEFECT_COLORS`, 색각 검증 통과한 범주 팔레트 1~5번). 순위나 필터에 따라 색을 바꾸지 않는다. 상태 색(정상/주의/경고)은 상태 의미일 때만, 항상 라벨·아이콘과 함께.
- `charts.py`는 Streamlit 비의존. Plotly 축은 `automargin=True`가 없으면 한글 라벨이 잘리므로 `_base_layout()`을 반드시 거친다.
- app.py: 같은 차트가 여러 탭에 나오므로 `plot(fig, key=...)`에 고유 key 필수(StreamlitDuplicateElementId). `unsafe_allow_html` 마크업에 넣는 사용자 입력(파일명 등)은 `html.escape` 처리. 조건식 한 줄(`a() if x else b()`)은 Streamlit magic이 반환값을 화면에 그리므로 if/else 문으로 쓴다.
- 표는 `show_table()`(Styler로 천 단위·% 형식, 20만 셀 초과 시 column_config로 대체).
- 화면 확인 시 Streamlit은 `steelq/` 모듈 변경을 즉시 반영하지 않을 수 있으니 서버를 재시작하고 확인한다.

## 4단계 검토에서 얻은 주의사항

- 일자 파싱은 `cleaner.parse_dates()` 한곳에서만 한다. 숫자 일자를 `pd.to_datetime`에 그대로 넘기면 1970-01-01이 되므로 8자리(YYYYMMDD)·엑셀 일련번호(20000~80000)를 먼저 변환한다.
- 라인·강종이 결측('미상')인 행은 키 중복 병합 대상이 아니다(서로 다른 라인일 수 있음) — 경고 로그만 남긴다.
- 캐시: `st.cache_data`는 5만 행 이상 DataFrame을 표본으로만 해시한다. 정제·분석 캐시는 `_merged`(해시 제외) + `data_key()`(전체 내용 해시)로 키를 잡고, 모든 캐시에 `ttl`·`max_entries`를 둔다(모든 세션이 공유하는 서버 메모리).
- 엑셀 리포트는 `download_button`에 함수를 넘겨 **클릭할 때만** 생성한다. 탭 내용은 매 재실행마다 모두 실행되므로 무거운 작업을 탭 안에서 미리 하지 말 것.
- 브라우저 부담: 차트 막대는 `charts.MAX_BARS`(400) 초과 시 면적 선으로, 화면 표는 `TABLE_PREVIEW_ROWS`(5,000)행까지만 보낸다.
- 리포트 쓰기 속도는 openpyxl XML 직렬화가 병목이다. `lxml`을 설치하면 더 빨라지지만 현재 requirements에는 없다. 빈 셀은 쓰지 않는다.
- 문자열은 엑셀 제어문자(`ILLEGAL_CHARACTERS_RE`)를 제거하고 쓴다.
