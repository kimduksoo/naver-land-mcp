"""네이버 부동산 MCP 서버

엔드포인트 전략:
- new.land.naver.com → 429 시 m.land.naver.com 자동 전환
- 매물 조회는 m.land 우선 사용 (안정적)
"""

import json
from mcp.server.fastmcp import FastMCP
from naver_land import NaverLandClient, REAL_ESTATE_TYPES, TRADE_TYPES
from regions import get_sido_list, get_sigungu_list

mcp = FastMCP("naver-land")
client = NaverLandClient()


def _make_search_url(name: str, lat=None, lng=None) -> str:
    """네이버 검색/지도 URL 생성 (네이버 부동산 SPA 딥링크 미지원 대응)
    - 단지명이 고유한 경우: 네이버 검색 URL
    - 단지명이 '빌라' 등 일반명인 경우: 네이버 지도 URL (좌표 기반)
    """
    from urllib.parse import quote
    generic_names = {"빌라", "주택", "다세대", "연립", "다가구", "원룸", "투룸"}
    if name in generic_names and lat and lng:
        return f"https://map.naver.com/p?c={lng},{lat},17,0,0,0,dh"
    return f"https://search.naver.com/search.naver?query={quote(name + ' 네이버부동산')}"


def _format_article_complex(item: dict) -> dict:
    """단지 기반 매물 응답 포맷 (m.land/complex/getComplexArticleList)"""
    article_no = item.get("atclNo")
    return {
        "articleNo": article_no,
        "articleName": item.get("atclNm"),
        "realEstateType": item.get("rletTpNm"),
        "tradeTypeName": item.get("tradTpNm"),
        "buildingName": item.get("bildNm"),
        "floorInfo": item.get("flrInfo"),
        "deposit": item.get("prc"),
        "rent": item.get("rentPrc"),
        "price": item.get("prcInfo"),
        "supplyArea": item.get("spc1"),
        "exclusiveArea": item.get("spc2"),
        "direction": item.get("direction"),
        "confirmDate": item.get("atclCfmYmd"),
        "realtorName": item.get("rltrNm"),
        "featureDesc": item.get("atclFetrDesc"),
        "tagList": item.get("tagList"),
        "url": _make_search_url(item.get("atclNm", "")) if item.get("atclNm") else None,
    }


def _format_article_coords(item: dict) -> dict:
    """좌표 기반 매물 응답 포맷 (m.land/cluster/ajax/articleList)"""
    article_no = item.get("atclNo")
    name = item.get("atclNm", "")
    return {
        "articleNo": article_no,
        "articleName": name,
        "realEstateType": item.get("rletTpNm"),
        "tradeTypeName": item.get("tradTpNm"),
        "buildingName": item.get("bildNm"),
        "floorInfo": item.get("flrInfo"),
        "deposit": item.get("prc"),
        "rent": item.get("rentPrc"),
        "price": item.get("hanPrc"),
        "supplyArea": item.get("spc1"),
        "exclusiveArea": item.get("spc2"),
        "direction": item.get("direction"),
        "confirmDate": item.get("atclCfmYmd"),
        "realtorName": item.get("rltrNm"),
        "featureDesc": item.get("atclFetrDesc"),
        "tagList": item.get("tagList"),
        "lat": item.get("lat"),
        "lng": item.get("lng"),
        "url": _make_search_url(name, item.get("lat"), item.get("lng")) if name else None,
    }


# ──────────────────────────────────────────────
# 지역 검색
# ──────────────────────────────────────────────

@mcp.tool()
async def search_regions(cortar_no: str = "0000000000") -> str:
    """지역 검색 (시도 → 시군구 → 읍면동 계층 탐색)

    지역 코드를 단계적으로 탐색합니다.
    - 기본값(0000000000): 전국 시도 목록
    - 시도 코드: 시군구 목록
    - 시군구 코드: 읍면동 목록

    예시: "성남시 분당구 정자동"
    1. search_regions() → 경기도 "4100000000"
    2. search_regions("4100000000") → 성남시분당구 "4113500000"
    3. search_regions("4113500000") → 정자동 "4113510300"

    rate limit 시 내장 코드로 자동 fallback됩니다.

    Args:
        cortar_no: 지역 코드 (10자리). 기본값은 전국.
    """
    try:
        data = await client.search_regions(cortar_no)
        regions = data.get("regionList", [])
        return json.dumps(
            [{"cortarNo": r.get("cortarNo"), "cortarNm": r.get("cortarNm")} for r in regions],
            ensure_ascii=False, indent=2)
    except Exception:
        # fallback: 내장 코드
        if cortar_no == "0000000000":
            return json.dumps(get_sido_list(), ensure_ascii=False, indent=2)
        fallback = get_sigungu_list(cortar_no)
        if fallback:
            note = [{"_note": "API rate limit으로 내장 코드 사용. 읍면동 조회는 get_region_info를 사용하세요."}]
            return json.dumps(note + fallback, ensure_ascii=False, indent=2)
        return json.dumps({"error": "API rate limit. 잠시 후 다시 시도하거나, get_region_info에 cortarNo를 입력하세요."}, ensure_ascii=False)


# ──────────────────────────────────────────────
# 지역 정보 (m.land fallback)
# ──────────────────────────────────────────────

@mcp.tool()
async def get_region_info(cortar_no: str, real_estate_type: str = "APT",
                          trade_type: str = "A1") -> str:
    """지역 정보 + 좌표 조회 (m.land 기반, rate limit 강건)

    cortarNo를 입력하면 해당 지역의 정확한 좌표, 지역명, 매물 클러스터 수를 반환합니다.
    search_regions가 rate limit에 걸릴 때 대안으로 사용하세요.
    반환된 좌표는 search_listings에서 바로 사용 가능합니다.

    Args:
        cortar_no: 지역 코드 (읍면동 10자리)
        real_estate_type: APT, OPST, VL 등
        trade_type: A1(매매), B1(전세), B2(월세)
    """
    data = await client.get_region_info(cortar_no, real_estate_type, trade_type)
    cortar = data.get("cortar", {})
    detail = cortar.get("detail", {})
    clusters = data.get("data", {}).get("ARTICLE", [])
    total_count = sum(c.get("count", 0) for c in clusters)

    return json.dumps({
        "region": {
            "cortarNo": detail.get("cortarNo"),
            "regionName": detail.get("regionName"),
            "city": detail.get("cityNm"),
            "district": detail.get("dvsnNm"),
            "dong": detail.get("secNm"),
            "lat": float(detail.get("mapYCrdn", 0)),
            "lon": float(detail.get("mapXCrdn", 0)),
        },
        "clusterCount": len(clusters),
        "totalListingCount": total_count,
    }, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 단지 목록 (new.land → m.land fallback)
# ──────────────────────────────────────────────

@mcp.tool()
async def get_complexes(cortar_no: str, real_estate_type: str = "APT") -> str:
    """특정 동의 단지 목록 조회

    rate limit 시 좌표 기반 매물 검색으로 자동 전환됩니다.
    전환 시 search_listings 사용을 안내합니다.

    Args:
        cortar_no: 읍면동 코드 (10자리)
        real_estate_type: APT, OPST, VL 등
    """
    try:
        data = await client.get_complexes(cortar_no, real_estate_type)
        complexes = data.get("complexList", [])
        result = []
        for c in complexes:
            result.append({
                "complexNo": c.get("complexNo"),
                "complexName": c.get("complexName"),
                "totalHouseholdCount": c.get("totalHouseholdCount"),
                "realEstateTypeName": c.get("realEstateTypeName"),
                "cortarAddress": c.get("cortarAddress"),
                "detailAddress": c.get("detailAddress"),
                "dealCount": c.get("dealCount"),
                "leaseCount": c.get("leaseCount"),
                "rentCount": c.get("rentCount"),
            })
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception:
        # fallback: m.land 좌표 기반으로 전환 안내
        try:
            region_data = await client.get_region_info(cortar_no, real_estate_type)
            detail = region_data.get("cortar", {}).get("detail", {})
            lat = float(detail.get("mapYCrdn", 0))
            lon = float(detail.get("mapXCrdn", 0))
            region_name = detail.get("regionName", "")
            return json.dumps({
                "_fallback": True,
                "_message": f"API rate limit. search_listings로 매물을 직접 조회하세요.",
                "region": region_name,
                "cortarNo": cortar_no,
                "lat": lat,
                "lon": lon,
                "hint": f'search_listings(lat={lat}, lon={lon}, cortar_no="{cortar_no}")',
            }, ensure_ascii=False, indent=2)
        except Exception:
            return json.dumps({"error": "API rate limit. 잠시 후 재시도해주세요."}, ensure_ascii=False)


# ──────────────────────────────────────────────
# 매물 조회 — 좌표 기반 (m.land, 핵심 도구)
# ──────────────────────────────────────────────

@mcp.tool()
async def search_listings(
    lat: float,
    lon: float,
    cortar_no: str = "",
    real_estate_type: str = "APT",
    trade_type: str = "A1",
    delta: float = 0.015,
    max_pages: int = 3,
) -> str:
    """좌표 기반 매물 검색 (가장 안정적인 방법)

    단지 코드 없이 좌표만으로 해당 지역의 모든 매물을 검색합니다.
    get_region_info로 좌표를 먼저 확인하거나, 알고 있는 좌표를 직접 입력하세요.

    거래 유형: A1(매매), B1(전세), B2(월세) — 여러 개는 콜론으로 구분 (A1:B1:B2)
    부동산 타입: APT(아파트), OPST(오피스텔), VL(빌라) — 여러 개는 콜론으로 구분

    Args:
        lat: 위도 (예: 37.3675)
        lon: 경도 (예: 127.1127)
        cortar_no: 지역 코드 (선택, 입력 시 더 정확한 결과)
        real_estate_type: 부동산 타입 (기본: APT)
        trade_type: 거래 유형 (기본: A1=매매)
        delta: 검색 반경 (기본: 0.015 ≈ 약 1.5km)
        max_pages: 최대 페이지 수 (기본: 3, 페이지당 20건)
    """
    items = await client.get_all_articles_by_coords(
        lat, lon, cortar_no, real_estate_type, trade_type, delta, max_pages)

    # 단지별 그룹핑
    by_complex: dict[str, list] = {}
    for item in items:
        name = item.get("atclNm", "기타")
        by_complex.setdefault(name, []).append(item)

    result = {
        "totalCount": len(items),
        "complexCount": len(by_complex),
        "listings": {},
    }
    for name, articles in sorted(by_complex.items(), key=lambda x: -len(x[1])):
        result["listings"][name] = {
            "count": len(articles),
            "articles": [_format_article_coords(a) for a in articles],
        }

    return json.dumps(result, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 매물 조회 — 단지 코드 기반 (m.land)
# ──────────────────────────────────────────────

@mcp.tool()
async def get_articles(complex_no: str, trade_type: str = "A1",
                       order: str = "prc_", page: int = 1) -> str:
    """단지별 매물 목록 (1페이지)

    거래 유형: A1(매매), B1(전세), B2(월세)
    정렬: prc_(가격순), spc_(면적순), date_(최신순)

    Args:
        complex_no: 단지 코드 (get_complexes에서 확인)
        trade_type: 거래 유형
        order: 정렬
        page: 페이지 번호
    """
    data = await client.get_articles_by_complex(complex_no, trade_type, order, page)
    result_data = data.get("result", {})
    items = result_data.get("list", [])
    return json.dumps({
        "articles": [_format_article_complex(item) for item in items],
        "totalCount": result_data.get("totAtclCnt"),
        "moreData": result_data.get("moreDataYn") == "Y",
        "page": page,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_all_articles(complex_no: str, trade_type: str = "A1",
                           order: str = "prc_", max_pages: int = 5) -> str:
    """단지별 매물 전체 (자동 페이징)

    Args:
        complex_no: 단지 코드
        trade_type: A1(매매), B1(전세), B2(월세)
        order: prc_(가격순), spc_(면적순), date_(최신순)
        max_pages: 최대 페이지 수
    """
    items = await client.get_all_articles_by_complex(complex_no, trade_type, order, max_pages)
    return json.dumps({
        "articles": [_format_article_complex(item) for item in items],
        "totalCount": len(items),
    }, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
# 단지 상세 / 시세 / 학군
# ──────────────────────────────────────────────

@mcp.tool()
async def get_complex_detail(complex_no: str) -> str:
    """단지 상세 정보 (세대수, 면적, 입주년도, 건설사 등)

    Args:
        complex_no: 단지 코드
    """
    data = await client.get_complex_detail(complex_no)
    detail = data.get("complexDetail", {})
    pyeong_list = data.get("complexPyeongDetailList", [])

    pyeongs = []
    for p in pyeong_list:
        pyeongs.append({
            "pyeongNo": p.get("pyeongNo"),
            "pyeongName": p.get("pyeongNm"),
            "supplyArea": p.get("supplyArea"),
            "exclusiveArea": p.get("exclusiveArea"),
            "roomCount": p.get("roomCnt"),
            "bathroomCount": p.get("bathroomCnt"),
            "householdCount": p.get("householdCountByPyeong"),
        })

    return json.dumps({
        "complexNo": detail.get("complexNo"),
        "complexName": detail.get("complexName"),
        "realEstateTypeName": detail.get("realEstateTypeName"),
        "cortarAddress": detail.get("cortarAddress"),
        "detailAddress": detail.get("detailAddress"),
        "totalHouseholdCount": detail.get("totalHouseholdCount"),
        "totalBuildingCount": detail.get("totalBuildingCount"),
        "highFloor": detail.get("highFloor"),
        "lowFloor": detail.get("lowFloor"),
        "useApproveYmd": detail.get("useApproveYmd"),
        "constructionCompanyName": detail.get("constructionCompanyName"),
        "pyeongDetailList": pyeongs,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_price_info(complex_no: str, area_no: str,
                         trade_type: str = "A1", year: int = 5) -> str:
    """시세/실거래가 추이

    Args:
        complex_no: 단지 코드
        area_no: 면적 번호 (get_complex_detail → pyeongNo)
        trade_type: A1(매매), B1(전세), B2(월세)
        year: 조회 기간 (기본 5년)
    """
    data = await client.get_price_info(complex_no, area_no, trade_type, year)
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_school_info(complex_no: str) -> str:
    """단지 주변 학군 (초/중/고)

    Args:
        complex_no: 단지 코드
    """
    data = await client.get_school_info(complex_no)
    return json.dumps(data, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
