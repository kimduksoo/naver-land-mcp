# naver-land-mcp

네이버 부동산 매물 조회 MCP (Model Context Protocol) 서버

네이버 부동산의 비공식 API를 활용하여 현재 매물 정보를 조회합니다.

## 기능

- 지역 검색 (시도 → 시군구 → 읍면동 계층 탐색)
- 좌표 기반 매물 검색 (아파트, 오피스텔, 빌라)
- 단지별 매물 조회 / 상세 정보
- 시세 및 실거래가 추이
- 주변 학군 정보
- 매매 / 전세 / 월세 거래 유형 지원
- API rate limit 시 자동 엔드포인트 전환 (`new.land.naver.com` → `m.land.naver.com`)

## MCP 도구

| 도구 | 설명 |
|------|------|
| `search_regions` | 지역 코드 계층 탐색 (시도 → 시군구 → 읍면동) |
| `get_region_info` | 지역 좌표 + 매물 클러스터 조회 (rate limit에 강건) |
| `get_complexes` | 특정 동의 단지 목록 |
| `search_listings` | 좌표 기반 매물 검색 (가장 안정적) |
| `get_articles` | 단지별 매물 목록 (1페이지) |
| `get_all_articles` | 단지별 매물 전체 (자동 페이징) |
| `get_complex_detail` | 단지 상세 (세대수, 면적, 입주년도, 건설사 등) |
| `get_price_info` | 시세 / 실거래가 추이 |
| `get_school_info` | 주변 학군 (초/중/고) |

## 설치

### 요구사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (패키지 매니저)

### Claude Code에 등록

```bash
claude mcp add -s user naver-land -- uv run --directory /path/to/naver-land-mcp python -m server
```

### 직접 실행

```bash
cd naver-land-mcp
uv sync
uv run python -m server
```

## 사용 예시

### 지역 검색 → 매물 조회 흐름

```
1. search_regions()                          → 전국 시도 목록
2. search_regions("4100000000")              → 경기도 시군구 목록
3. search_regions("4113500000")              → 분당구 읍면동 목록
4. get_region_info("4113510300")             → 정자동 좌표 확인
5. search_listings(lat=37.38, lon=127.12)    → 주변 매물 검색
```

### 단지 상세 조회 흐름

```
1. get_complexes("4113510300")               → 정자동 단지 목록
2. get_complex_detail("12345")               → 단지 상세 (면적, 세대수)
3. get_all_articles("12345", trade_type="B2") → 월세 매물 전체
4. get_price_info("12345", area_no="1")      → 시세 추이
5. get_school_info("12345")                  → 학군 정보
```

### 거래 유형 / 부동산 타입 코드

| 거래 유형 | 코드 |
|-----------|------|
| 매매 | `A1` |
| 전세 | `B1` |
| 월세 | `B2` |

| 부동산 타입 | 코드 |
|-------------|------|
| 아파트 | `APT` |
| 오피스텔 | `OPST` |
| 빌라 | `VL` |
| 아파트분양권 | `ABYG` |
| 재건축 | `JGC` |

여러 타입 동시 검색: 콜론으로 구분 (예: `APT:OPST:VL`)

## 아키텍처

```
server.py        ← FastMCP 서버, 도구 정의
naver_land.py    ← API 클라이언트 (dual endpoint, retry, fallback)
regions.py       ← 시도/시군구 코드 및 좌표 데이터
```

### 엔드포인트 전략

| 엔드포인트 | 용도 | 특징 |
|-----------|------|------|
| `new.land.naver.com` | 지역 검색, 단지 목록 | IP 기반 rate limit 있음 |
| `m.land.naver.com` | 매물 검색, 좌표 기반 조회 | 상대적으로 안정적 |

- `new.land` API 호출 시 429 응답이면 자동으로 `m.land`로 전환
- 지역 검색 rate limit 시 내장 코드(시도/시군구)로 fallback
- 좌표 기반 검색(`search_listings`)이 가장 안정적

## 응답 필드 설명

### 매물 (article)

| 필드 | 설명 |
|------|------|
| `deposit` | 보증금 (만원 단위, 숫자) |
| `rent` | 월세 (만원 단위, 숫자) |
| `price` | 가격 (한글 표기, 예: "3억 5,000") |
| `realEstateType` | 부동산 타입 (아파트, 오피스텔, 빌라) |
| `exclusiveArea` | 전용면적 (㎡) |
| `supplyArea` | 공급면적 (㎡) |
| `floorInfo` | 층 정보 (예: "15/25") |
| `direction` | 향 (남향, 동향 등) |
| `featureDesc` | 매물 설명 |

## 주의사항

- 네이버 부동산의 비공식 API를 사용합니다. API 변경 시 동작하지 않을 수 있습니다.
- 과도한 요청 시 IP 기반 rate limit이 발생할 수 있습니다.
- 매물 데이터의 정확성은 네이버 부동산에 등록된 정보에 의존합니다.

## License

MIT
