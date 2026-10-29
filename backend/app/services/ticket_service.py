"""门票信息查询服务 — 通过 Tavily Search + LLM 提取门票价格和购票方式"""

import json
from typing import Optional, List

from ..config import get_settings
from ..models.schemas import TicketInfo
from ..services.http_client import ExternalRequestError, request_json
from ..services.logger import logger


# Tavily 不可用的标记（额度耗尽 / key 无效等）
_tavily_unavailable = False


def _tavily_search(query: str, max_results: int = 3) -> list[dict]:
    """
    Call Tavily Search API.
    """
    global _tavily_unavailable

    if _tavily_unavailable:
        return []

    settings = get_settings()
    api_key = settings.tavily_api_key if hasattr(settings, 'tavily_api_key') else ""

    if not api_key:
        logger.warning("Tavily API Key 未配置，跳过门票搜索")
        _tavily_unavailable = True
        return []

    try:
        data = request_json(
            "https://api.tavily.com/search",
            method="POST",
            payload={
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": True,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )

        # 检查 Tavily 返回的错误（额度耗尽、key 无效等）
        if "error" in data or "detail" in data:
            err_msg = data.get("error") or data.get("detail", "")
            if any(kw in str(err_msg).lower() for kw in ["quota", "credit", "exceeded", "limit", "usage"]):
                logger.warning(f"⚠️ Tavily 额度耗尽: {err_msg}，切换到 LLM 兜底估价")
                _tavily_unavailable = True
            else:
                logger.warning(f"Tavily API 错误: {err_msg}")
            return []

        results = []
        answer = data.get("answer", "")
        if answer:
            results.append({"title": "AI摘要", "url": "", "content": answer})

        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
            })

        return results
    except ExternalRequestError as error:
        if error.status_code in (429, 402, 403, 432):
            logger.warning(f"⚠️ Tavily HTTP {error.status_code}（疑似额度耗尽）: {error.body}，切换到 LLM 兜底估价")
            _tavily_unavailable = True
        else:
            logger.warning(f"Tavily 请求失败: {error}")
        return []
    except Exception as e:
        logger.warning(f"Tavily 搜索失败: {e}")
        return []


def _extract_with_llm(name: str, search_results: List[dict]) -> tuple:
    """
    用 LLM 从 Tavily 搜索结果中提取门票价格和购票方式。
    """
    if not search_results:
        return 0, ""

    context_parts = []
    for r in search_results[:3]:
        content = r["content"][:400]
        context_parts.append(f"【{r['title']}】{content}")
    context = "\n".join(context_parts)

    prompt = f"""从以下搜索结果中，提取"{name}"景区大门票的价格和购票方式。

搜索结果：
{context}

请严格返回JSON（不要markdown代码块，不要解释）：
{{"price":0,"purchase_method":""}}

重要规则：
1. price: 景区**大门票**常规价格（整数，元）。先找"门票XX元"、"票价XX元"等明确价格信息
2. **只有**当搜索结果明确说"免费开放"、"免费参观"、"不收门票"且是对整个景区而言时，price才=0
3. **注意区分**: "早上X点前不收门票"≠全天免费；"套票/联票"≠大门票；"XX元起"取最低价
4. 忽略：内部小景点、特展、讲解团、旅行社套餐、导游费、钟点免费时段
5. purchase_method: 购票/预约方式（如"微信公众号中山陵预约"、"美团可购"、"现场购票"）
6. 如果免费且无需购票写"免费开放，无需购票"；免费但需预约写"免费，需提前预约"
7. **价格必须来自搜索结果**，不要用你的训练数据猜测"""

    try:
        from ..services.llm_service import get_llm
        from langchain_core.messages import HumanMessage

        llm = get_llm()
        response = llm.invoke([HumanMessage(content=prompt)])

        # ChatOpenAI 返回 AIMessage，取 .content
        text = response.content if hasattr(response, 'content') else str(response)

        # Extract JSON
        json_str = text.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()
        elif "{" in json_str:
            json_str = json_str[json_str.find("{"):json_str.rfind("}")+1]

        data = json.loads(json_str)
        price = int(data.get("price", 0))
        purchase_method = data.get("purchase_method", "")

        return price, purchase_method

    except Exception as e:
        logger.warning(f"LLM提取失败: {e}，回退到正则")
        return _extract_with_regex(name, search_results)


def _extract_with_regex(name: str, search_results: List[dict]) -> tuple:
    """正则回退方案"""
    import re
    if not search_results:
        return 0, ""

    all_text = " ".join(r["content"] for r in search_results)

    is_free = bool(re.search(
        r"免费[开放参观]|免票|不[收需]门票|免费入场|免费对外开放|不收门票|街区[不免费收]",
        all_text
    ))

    if is_free:
        need_reservation = bool(re.search(r"需[要]?预约|提前预约|实名[制]?预约", all_text))
        if need_reservation:
            return 0, "免费，需提前预约"
        return 0, "免费开放，无需购票"

    price = 0
    price_patterns = [
        r"门票[价格]?\s*[:：]?\s*(\d+)\s*元",
        r"票价\s*[:：]?\s*(\d+)\s*元",
        r"成人票\s*[:：]?\s*(\d+)\s*元",
        r"(\d+)\s*元/\s*人",
    ]
    for pat in price_patterns:
        m = re.search(pat, all_text)
        if m:
            p = int(m.group(1))
            if 5 <= p <= 500:
                price = p
                break

    methods = []
    if re.search(r"微信.*?小程序", all_text):
        methods.append("微信小程序预约")
    elif re.search(r"微信公众号", all_text):
        methods.append("微信公众号预约")
    if re.search(r"官网", all_text):
        methods.append("官网可购票")
    if re.search(r"美团|携程|大众点评|飞猪|去哪儿", all_text):
        methods.append("OTA平台可购票")
    if re.search(r"需[要]?预约|提前预约", all_text):
        if not any("预约" in m for m in methods):
            methods.append("需提前预约")

    purchase_method = "；".join(methods[:2]) if methods else ""
    return price, purchase_method


def _estimate_with_llm_only(name: str, city: str = "") -> tuple:
    """
    Tavily 不可用时，用 LLM 训练数据直接估算门票价格。
    返回 (price, purchase_method)。
    """
    location_hint = f"位于{city}的" if city else ""

    prompt = f"""请根据你的知识，估算{location_hint}景点"{name}"的**大门票常规价格**和购票方式。

请严格返回JSON（不要markdown代码块，不要解释）：
{{"price":0,"purchase_method":""}}

规则：
1. price: 大门票常规成人价（整数，元）。知名景点按你知道的实际价格填
2. 如果该景点确实是免费开放的（如城市公园、开放式街区、免费博物馆），price=0
3. 不确定时给出合理估计，宁可略低不要虚高
4. 忽略：内部小景点、特展、缆车、讲解费等附加项目
5. purchase_method: 购票/预约方式简述（如"微信公众号预约"、"现场购票"、"OTA平台可购"）
6. 如果免费且无需购票写"免费开放，无需购票"；免费需预约写"免费，需提前预约"
7. 不要编造具体公众号名，统一写"官方微信公众号预约"即可"""

    try:
        from ..services.llm_service import get_llm
        from langchain_core.messages import HumanMessage

        llm = get_llm()
        response = llm.invoke([HumanMessage(content=prompt)])

        text = response.content if hasattr(response, 'content') else str(response)

        json_str = text.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()
        elif "{" in json_str:
            json_str = json_str[json_str.find("{"):json_str.rfind("}")+1]

        data = json.loads(json_str)
        price = int(data.get("price", 0))
        purchase_method = data.get("purchase_method", "")

        return price, purchase_method

    except Exception as e:
        logger.warning(f"LLM兜底估价失败: {e}")
        return 0, ""


def _select_source_url(search_results: List[dict], attraction_name: str = "") -> str:
    """只保留正文或标题确实匹配当前景点名称的网页链接。"""
    import re

    normalized = re.sub(r"[\s·•\-—_（）()]+", "", attraction_name or "")
    base = re.sub(r"(?:风景名胜区|风景区|景区|博物馆|公园)$", "", normalized)
    identities = {value for value in (normalized, base) if len(value) >= 2}
    matched = []
    for item in search_results:
        url = str(item.get("url", "") or "")
        if not url.startswith(("https://", "http://")):
            continue
        evidence = re.sub(
            r"[\s·•\-—_（）()]+",
            "",
            f"{item.get('title', '')} {item.get('content', '')}",
        )
        if not identities or any(identity in evidence for identity in identities):
            matched.append(url)
    if not matched:
        return ""
    official = next((url for url in matched if ".gov.cn" in url or "official" in url.lower()), None)
    return official or matched[0]


def query_ticket_info(attraction_name: str, city: str = "") -> TicketInfo:
    """
    Query ticket price and purchase method for an attraction.
    Uses Tavily search + LLM extraction; falls back to LLM-only estimate
    when Tavily is unavailable (quota exhausted, key invalid, etc.).
    """
    query = f"{attraction_name} 门票价格 是否需要预约"
    if city:
        query = f"{city} {query}"

    logger.debug(f"Tavily 搜索: {query[:60]}...")
    results = _tavily_search(query, max_results=3)

    if results:
        price, purchase_method = _extract_with_llm(attraction_name, results)
        source = "tavily+llm"
        source_url = _select_source_url(results, attraction_name)
        confidence = "medium" if source_url else "low"
    else:
        price, purchase_method = _estimate_with_llm_only(attraction_name, city)
        source = "llm-estimate"
        source_url = ""
        confidence = "low"

    label = "免费" if price == 0 else f"¥{price}"
    logger.debug(f"门票: {label} | 购票: {purchase_method[:60]} | 来源: {source} | 可信度: {confidence}")

    return TicketInfo(
        price=price,
        opening_hours="",
        purchase_method=purchase_method,
        source=source,
        source_url=source_url,
        confidence=confidence,
    )
