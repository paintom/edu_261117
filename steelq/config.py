"""SteelQ Report Agent 전역 설정.

컬럼 정의, 별칭 사전, 이상치 임계값 등 모든 상수를 이곳에서 관리한다.
다른 모듈에서는 상수를 하드코딩하지 말고 이 모듈을 참조한다.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SAMPLE_CSV = DATA_DIR / "sample_steel_quality.csv"
SAMPLE_XLSX = DATA_DIR / "sample_steel_quality.xlsx"
INJECTED_ANOMALIES_CSV = DATA_DIR / "injected_anomalies.csv"

# ---------------------------------------------------------------------------
# 컬럼 정의
# ---------------------------------------------------------------------------
COL_DATE = "일자"
COL_LINE = "라인"
COL_GRADE = "강종"
COL_PRODUCTION = "생산량"

DEFECT_TYPES: list[str] = ["스크래치", "압연흠", "스케일", "치수불량", "표면균열"]

KEY_COLUMNS: list[str] = [COL_DATE, COL_LINE, COL_GRADE]
NUMERIC_COLUMNS: list[str] = [COL_PRODUCTION, *DEFECT_TYPES]
REQUIRED_COLUMNS: list[str] = [*KEY_COLUMNS, *NUMERIC_COLUMNS]

# 프로그램이 계산하는 파생 컬럼 (입력 불필요)
COL_TOTAL_DEFECT = "총불량량"
COL_DEFECT_RATE = "불량률"

# ---------------------------------------------------------------------------
# 컬럼 별칭 사전 (표준 컬럼명 -> 허용 별칭 목록)
# 비교 시 대소문자·공백·밑줄·괄호 안 단위를 무시한다 (loader.normalize_header 참고).
# ---------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, list[str]] = {
    COL_DATE: ["일자", "날짜", "생산일자", "생산일", "작업일자", "date", "day", "prod_date", "production_date"],
    COL_LINE: ["라인", "생산라인", "라인명", "공정", "line", "line_name", "prod_line"],
    COL_GRADE: ["강종", "강종명", "강종코드", "제품", "grade", "steel_grade", "steelgrade", "product"],
    COL_PRODUCTION: ["생산량", "생산", "생산실적", "실적", "production", "prod", "qty", "quantity", "output", "ton", "tons"],
    "스크래치": ["스크래치", "scratch", "scratches"],
    "압연흠": ["압연흠", "압연결함", "롤마크", "rolling_defect", "rolling", "roll_mark", "rollmark"],
    "스케일": ["스케일", "scale", "scale_defect"],
    "치수불량": ["치수불량", "치수", "dimension", "dimensional", "dimension_defect", "size"],
    "표면균열": ["표면균열", "균열", "크랙", "crack", "surface_crack", "cracks"],
}

# ---------------------------------------------------------------------------
# 파일 입력
# ---------------------------------------------------------------------------
CSV_ENCODINGS: list[str] = ["utf-8-sig", "cp949", "euc-kr"]
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".csv", ".xlsx", ".xls")
PREVIEW_ROWS = 20

# ---------------------------------------------------------------------------
# 정제 옵션 기본값 (PRD F2)
# ---------------------------------------------------------------------------
UNKNOWN_LABEL = "미상"
DUPLICATE_KEY_STRATEGIES: dict[str, str] = {"last": "마지막 값 유지", "sum": "합산", "mean": "평균"}
NEGATIVE_STRATEGIES: dict[str, str] = {"zero": "0으로 보정", "abs": "절댓값", "nan": "결측 처리"}
DEFAULT_DUPLICATE_KEY_STRATEGY = "last"
DEFAULT_NEGATIVE_STRATEGY = "zero"

# ---------------------------------------------------------------------------
# 이상치·분석 임계값 기본값 (PRD F4)
# ---------------------------------------------------------------------------
TARGET_DEFECT_RATE = 1.0          # 목표 불량률(%)
IQR_MULTIPLIER = 1.5              # Q3 + k*IQR
ZSCORE_THRESHOLD = 3.0            # |z| >= 3
MOVING_AVG_WINDOW = 7             # 이동평균 기간(일)
MOVING_AVG_MULTIPLIER = 2.0       # 이동평균 대비 배수
SHARE_SHIFT_THRESHOLD = 15.0      # 결함 점유율 급변(%p)
MONTHLY_SHARE_CHANGE_ALERT = 5.0  # 코멘트 C5: 월간 점유율 변화(%p)

SEVERITY_NORMAL = "정상"
SEVERITY_CAUTION = "주의"
SEVERITY_WARNING = "경고"
SEVERITY_ORDER = {SEVERITY_WARNING: 0, SEVERITY_CAUTION: 1, SEVERITY_NORMAL: 2}
SEVERITY_ICONS = {SEVERITY_NORMAL: "🟢", SEVERITY_CAUTION: "🟡", SEVERITY_WARNING: "🔴"}
# 화면과 엑셀 리포트가 공유하는 심각도 팔레트 (배경, 글자) — RGB hex, '#' 없음
SEVERITY_COLORS = {
    SEVERITY_NORMAL: ("E6F4EA", "1E7B34"),
    SEVERITY_CAUTION: ("FFF4CE", "8A6100"),
    SEVERITY_WARNING: ("FDE2E1", "B42318"),
}

# 차트 마커·강조선용 상태 색 (배경 틴트와 별개). 항상 아이콘·라벨과 함께 사용
SEVERITY_MARK_COLORS = {SEVERITY_NORMAL: "#0ca30c", SEVERITY_CAUTION: "#fab219", SEVERITY_WARNING: "#d03b3b"}

# ---------------------------------------------------------------------------
# 화면·리포트 공용 팔레트
# ---------------------------------------------------------------------------
BRAND_NAVY = "#1F3A5F"          # 헤더·표 머리글
BRAND_STEEL = "#5B6B7F"         # 보조 텍스트
BRAND_ACCENT = "#E8590C"        # 강조색(하나만): 주요 버튼·선택 상태
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID_COLOR = "#e1e0d9"
AXIS_COLOR = "#c3c2b7"
SERIES_PRIMARY = "#2a78d6"      # 단일 계열 차트 색 (범주 팔레트 1번)
# 결함 유형은 '엔티티'이므로 순위가 아니라 유형에 색을 고정한다 (색각 검증 통과한 범주 팔레트 1~5번)
DEFECT_COLORS = {
    "스크래치": "#2a78d6",
    "압연흠": "#eb6834",
    "스케일": "#1baf7a",
    "치수불량": "#eda100",
    "표면균열": "#e87ba4",
}
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

# 이상치 심각도 '경고' 승격 기준
IQR_WARNING_MULTIPLIER = 3.0      # Q3 + 3*IQR 초과 시 경고 (그 외 주의)
ZSCORE_WARNING_MARGIN = 1.0       # |z| >= 임계값+1 이면 경고
MA_WARNING_FACTOR = 1.5           # 이동평균 배수 × 1.5 이상이면 경고
TARGET_WARNING_FACTOR = 1.5       # 목표 불량률 × 1.5 초과 시 경고
SHARE_WARNING_FACTOR = 2.0        # 점유율 급변 기준 × 2 이상이면 경고

# ---------------------------------------------------------------------------
# 규칙 기반 코멘트 (PRD F5, C1~C10)
# ---------------------------------------------------------------------------
COMMENT_MAX_LINES = 8
COMMENT_DROP_PRIORITY = ["C7", "C9"]   # 8줄 초과 시 먼저 생략할 규칙
GROUP_RATIO_ALERT = 1.2                # C6/C7: 평균 대비 배수가 이 이상일 때만 언급
GROUP_RATIO_WARNING = 1.5
TOP_DEFECT_CAUTION_SHARE = 40.0        # C4: 1위 결함 점유율이 이 이상이면 주의
MONTH_RATE_CAUTION = 0.1               # C3: 전월 대비 상승 %p
MONTH_RATE_WARNING = 0.3
TARGET_CAUTION_RATIO = 0.9             # C2: 목표의 90% 이상이면 주의
TREND_DAYS = 30                        # C9: 추세 판단 기간(일)
TREND_THRESHOLD = 0.1                  # C9: 기간 중 변화량(%p) 기준

COMMENT_CATEGORIES = {
    "C1": "요약", "C2": "요약", "C3": "요약", "C9": "요약",
    "C4": "이슈", "C5": "이슈", "C6": "이슈", "C7": "이슈", "C8": "이슈",
    "C10": "데이터",
}
COMMENT_ORDER = ["C1", "C2", "C3", "C9", "C4", "C5", "C6", "C7", "C8", "C10"]

COMMENT_TEMPLATES = {
    "C1": "분석 기간 {start}~{end}({days}일) 총 생산량 {production}톤, 총 불량 {defect}톤, 평균 불량률 {rate}%입니다.",
    "C2": "평균 불량률이 목표({target}%) 대비 {diff}%p {direction}했습니다.",
    "C3": "{month} 불량률은 {rate}%로 전월({prev_month}) 대비 {diff}%p {direction}했습니다.",
    "C4": "주요 결함은 '{defect}'{ro} 전체 불량의 {share}%를 차지합니다.",
    "C5": "'{defect}' 점유율이 {prev}% → {curr}%로 {diff}%p 증가하여 집중 관리가 필요합니다.",
    "C6": "'{line}'의 불량률이 {rate}%로 전체 평균({avg}%) 대비 {ratio}배 높아 점검이 필요합니다.",
    "C7": "강종 '{grade}'의 불량률이 {rate}%로 가장 높습니다(평균 대비 {ratio}배).",
    "C8": "이상치 {count}건(경고 {warn}건)이 탐지되었으며, 가장 심각한 건은 {date} {line} {metric} {value}입니다.",
    "C9": "최근 {days}일 불량률은 {trend} 추세입니다(기간 중 {change:+.2f}%p).",
    "C10": "데이터 정제 과정(필터 적용 전 전체 데이터)에서 중복 {dup}건, 결측 {missing}건, 음수 {negative}건을 보정했습니다.",
}

# ---------------------------------------------------------------------------
# 표시 형식
# ---------------------------------------------------------------------------
TON_DECIMALS = 1
RATE_DECIMALS = 2

# ---------------------------------------------------------------------------
# 샘플 데이터
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
