"""네이버 부동산 비공식 API 클라이언트

엔드포인트 전략:
- 1차: new.land.naver.com (단지/지역 상세 API)
- 2차 (fallback): m.land.naver.com (좌표 기반 API, rate limit 훨씬 느슨)
"""

import asyncio
import random
import httpx
from regions import get_sigungu_coord

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

NEW_LAND_BASE = "https://new.land.naver.com/api"
M_LAND_BASE = "https://m.land.naver.com"

REAL_ESTATE_TYPES = {
    "APT": "아파트",
    "OPST": "오피스텔",
    "VL": "빌라",
    "ABYG": "분양권",
    "JGC": "재건축",
}

TRADE_TYPES = {
    "A1": "매매",
    "B1": "전세",
    "B2": "월세",
}

# 부동산 타입 매핑 (new.land → m.land)
RLET_TYPE_MAP = {
    "APT": "APT",
    "OPST": "OPST",
    "VL": "VL",
    "ABYG": "ABYG",
    "JGC": "JGC",
}


async def _request_with_retry(client: httpx.AsyncClient, method: str, url: str,
                               max_retries: int = 5, **kwargs) -> httpx.Response:
    """rate limit(429) + abuse(302→/error/abuse) 대응 retry with backoff"""
    for attempt in range(max_retries):
        resp = await client.request(method, url, follow_redirects=False, **kwargs)

        # 302 → /error/abuse (IP 기반 abuse 감지)
        if resp.status_code in (301, 302):
            location = resp.headers.get("location", "")
            if "error" in location or "abuse" in location:
                wait = 10 * (attempt + 1) + random.uniform(1, 5)
                await asyncio.sleep(wait)
                continue
            # abuse가 아닌 일반 redirect는 follow
            resp = await client.request(method, url, follow_redirects=True, **kwargs)

        # 429 Too Many Requests
        if resp.status_code == 429:
            wait = 3 ** (attempt + 1) + random.uniform(0, 2)
            await asyncio.sleep(wait)
            continue

        resp.raise_for_status()
        return resp
    # abuse/rate limit으로 모든 retry 소진 시 None 반환 (호출자가 처리)
    location = resp.headers.get("location", "") if resp.status_code in (301, 302) else ""
    if resp.status_code == 429 or "abuse" in location or "error" in location:
        return None
    resp.raise_for_status()
    return resp


# 요청 간 랜덤 딜레이 (인간 수준 속도 시뮬레이션)
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.5


async def _human_delay():
    """요청 사이 랜덤 딜레이 (abuse 방지)"""
    await asyncio.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


class NaverLandClient:
    def __init__(self):
        self._new_land = httpx.AsyncClient(
            headers={**HEADERS, "Referer": "https://new.land.naver.com/"},
            timeout=10.0,
        )
        self._m_land = httpx.AsyncClient(
            headers={**HEADERS, "Referer": "https://m.land.naver.com/"},
            timeout=10.0,
        )

    async def close(self):
        await self._new_land.aclose()
        await self._m_land.aclose()

    # ──────────────────────────────────────────────
    # new.land.naver.com API (1차)
    # ──────────────────────────────────────────────

    async def search_regions(self, cortar_no: str = "0000000000") -> dict:
        """지역 검색 (시도 → 시군구 → 읍면동)"""
        resp = await _request_with_retry(
            self._new_land, "GET",
            f"{NEW_LAND_BASE}/regions/list",
            params={"cortarNo": cortar_no},
        )
        if resp is None:
            raise Exception("API rate limit")
        return resp.json()

    async def get_complexes(self, cortar_no: str, real_estate_type: str = "APT",
                            order: str = "") -> dict:
        """특정 동의 단지 목록 조회"""
        resp = await _request_with_retry(
            self._new_land, "GET",
            f"{NEW_LAND_BASE}/regions/complexes",
            params={"cortarNo": cortar_no, "realEstateType": real_estate_type, "order": order},
        )
        if resp is None:
            raise Exception("API rate limit")
        return resp.json()

    async def get_complex_detail(self, complex_no: str) -> dict:
        """단지 상세 정보"""
        resp = await _request_with_retry(
            self._new_land, "GET",
            f"{NEW_LAND_BASE}/complexes/{complex_no}",
            params={"sameAddressGroup": "false"},
        )
        if resp is None:
            raise Exception("API rate limit")
        return resp.json()

    async def get_price_info(self, complex_no: str, area_no: str,
                             trade_type: str = "A1", year: int = 5) -> dict:
        """시세/실거래가 추이"""
        resp = await _request_with_retry(
            self._new_land, "GET",
            f"{NEW_LAND_BASE}/complexes/{complex_no}/prices",
            params={"complexNo": complex_no, "tradeType": trade_type,
                     "year": year, "areaNo": area_no, "type": "table"},
        )
        if resp is None:
            raise Exception("API rate limit")
        return resp.json()

    async def get_school_info(self, complex_no: str) -> dict:
        """학군 정보"""
        resp = await _request_with_retry(
            self._new_land, "GET",
            f"{NEW_LAND_BASE}/complexes/{complex_no}/schools",
        )
        if resp is None:
            raise Exception("API rate limit")
        return resp.json()

    # ──────────────────────────────────────────────
    # m.land.naver.com API (fallback + 매물 조회)
    # ──────────────────────────────────────────────

    async def get_articles_by_complex(self, complex_no: str, trade_type: str = "A1",
                                      order: str = "prc_", page: int = 1) -> dict:
        """단지 코드 기반 매물 조회 (m.land)"""
        resp = await _request_with_retry(
            self._m_land, "GET",
            f"{M_LAND_BASE}/complex/getComplexArticleList",
            params={"hscpNo": complex_no, "tradTpCd": trade_type,
                     "order": order, "showR0": "N", "page": page},
        )
        if resp is None:
            return {"result": {"list": [], "moreDataYn": "N"}}
        return resp.json()

    async def get_all_articles_by_complex(self, complex_no: str, trade_type: str = "A1",
                                          order: str = "prc_", max_pages: int = 10) -> list[dict]:
        """단지 코드 기반 매물 전체 조회"""
        all_items = []
        for page in range(1, max_pages + 1):
            data = await self.get_articles_by_complex(complex_no, trade_type, order, page)
            result = data.get("result", {})
            items = result.get("list", [])
            if not items:
                break
            all_items.extend(items)
            if result.get("moreDataYn", "N") == "N":
                break
            await _human_delay()
        return all_items

    async def get_region_info(self, cortar_no: str, real_estate_type: str = "APT",
                              trade_type: str = "A1") -> dict:
        """좌표 기반 지역 정보 + 클러스터 조회 (m.land)

        cortarNo로 해당 지역의 좌표와 매물 클러스터를 반환.
        2단계: 1) cortarNo로 좌표 획득 → 2) 정확한 좌표로 클러스터 조회
        """
        # 시군구 좌표 우선 사용 (시도 좌표보다 훨씬 정확)
        sigungu_coord = get_sigungu_coord(cortar_no)
        if sigungu_coord:
            init_lat, init_lon = sigungu_coord
        else:
            # fallback: 시도 코드(앞 2자리) 기반 대략적 중심 좌표
            sido_coords = {
                "11": (37.5665, 126.9780),  # 서울
                "26": (35.1796, 129.0756),  # 부산
                "27": (35.8714, 128.6014),  # 대구
                "28": (37.4563, 126.7052),  # 인천
                "29": (35.1595, 126.8526),  # 광주
                "30": (36.3504, 127.3845),  # 대전
                "31": (35.5384, 129.3114),  # 울산
                "36": (36.4800, 127.2890),  # 세종
                "41": (37.4138, 127.5183),  # 경기
                "42": (37.8228, 128.1555),  # 강원
                "43": (36.6357, 127.4914),  # 충북
                "44": (36.6588, 126.6728),  # 충남
                "45": (35.8203, 127.1088),  # 전북
                "46": (34.8161, 126.4629),  # 전남
                "47": (36.4919, 128.8889),  # 경북
                "48": (35.4606, 128.2132),  # 경남
                "50": (33.4996, 126.5312),  # 제주
            }
            sido_prefix = cortar_no[:2]
            init_lat, init_lon = sido_coords.get(sido_prefix, (37.5, 127.0))

        # 1단계: cortarNo로 정확한 좌표 획득
        resp = await _request_with_retry(
            self._m_land, "GET",
            f"{M_LAND_BASE}/cluster/clusterList",
            params={
                "view": "atcl", "rletTpCd": real_estate_type, "tradTpCd": trade_type,
                "z": "15", "lat": str(init_lat), "lon": str(init_lon),
                "btm": str(init_lat - 0.1), "lft": str(init_lon - 0.1),
                "top": str(init_lat + 0.1), "rgt": str(init_lon + 0.1),
                "cortarNo": cortar_no, "isOnlyIsale": "false",
            },
        )
        if resp is None:
            return {"cortar": {}, "data": {"ARTICLE": []}}
        data = resp.json()
        detail = data.get("cortar", {}).get("detail", {})
        real_lat = float(detail.get("mapYCrdn", init_lat))
        real_lon = float(detail.get("mapXCrdn", init_lon))

        await _human_delay()

        # 2단계: 정확한 좌표로 클러스터 재조회
        resp2 = await _request_with_retry(
            self._m_land, "GET",
            f"{M_LAND_BASE}/cluster/clusterList",
            params={
                "view": "atcl", "rletTpCd": real_estate_type, "tradTpCd": trade_type,
                "z": "15", "lat": str(real_lat), "lon": str(real_lon),
                "btm": str(real_lat - 0.015), "lft": str(real_lon - 0.015),
                "top": str(real_lat + 0.015), "rgt": str(real_lon + 0.015),
                "cortarNo": cortar_no, "isOnlyIsale": "false",
            },
        )
        if resp2 is None:
            return data  # 1단계 결과라도 반환
        return resp2.json()

    async def get_articles_by_coords(self, lat: float, lon: float,
                                     cortar_no: str = "",
                                     real_estate_type: str = "APT",
                                     trade_type: str = "A1",
                                     delta: float = 0.015,
                                     page: int = 1) -> dict:
        """좌표 기반 매물 목록 조회 (m.land)

        단지 코드 없이 좌표만으로 매물 검색 가능.
        """
        params = {
            "rletTpCd": real_estate_type,
            "tradTpCd": trade_type,
            "z": "15",
            "lat": str(lat),
            "lon": str(lon),
            "btm": str(lat - delta),
            "lft": str(lon - delta),
            "top": str(lat + delta),
            "rgt": str(lon + delta),
            "page": str(page),
        }
        if cortar_no:
            params["cortarNo"] = cortar_no
        resp = await _request_with_retry(
            self._m_land, "GET",
            f"{M_LAND_BASE}/cluster/ajax/articleList",
            params=params,
        )
        if resp is None:
            return {"body": []}
        return resp.json()

    async def get_all_articles_by_coords(self, lat: float, lon: float,
                                         cortar_no: str = "",
                                         real_estate_type: str = "APT",
                                         trade_type: str = "A1",
                                         delta: float = 0.015,
                                         max_pages: int = 5) -> list[dict]:
        """좌표 기반 매물 전체 조회"""
        all_items = []
        for page in range(1, max_pages + 1):
            data = await self.get_articles_by_coords(
                lat, lon, cortar_no, real_estate_type, trade_type, delta, page)
            items = data.get("body", [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < 20:  # 한 페이지 최대 20건
                break
            await _human_delay()
        return all_items
