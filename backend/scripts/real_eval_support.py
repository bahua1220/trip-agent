"""Pure, offline helpers for the opt-in real-provider evaluation runner."""

import html
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


SHANGHAI = timezone(timedelta(hours=8))
BACKEND = Path(__file__).resolve().parents[1]
CASE_FILE = BACKEND / "tests" / "fixtures" / "real_trip_cases.json"
SCOPE = (
    "真实模型/地图等外部服务的规划器抽测；直接调用生产 plan_trip，"
    "不覆盖HTTP、登录、数据库、微信页面或真实班次。自动检查只依据返回数据，"
    "不是营业信息、列车时刻或景点质量的真实性证明。"
)


def now():
    return datetime.now(SHANGHAI).isoformat()


def load_cases(start_date, selected=()):
    from app.models.schemas import TripRequest

    base = date.fromisoformat(start_date)
    definitions = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    known = {case["id"] for case in definitions}
    if len(known) != len(definitions) or not known:
        raise ValueError("真实测试用例编号重复或为空")
    unknown = set(selected) - known
    if unknown:
        raise ValueError("未知用例：" + ", ".join(sorted(unknown)))
    result = []
    for definition in definitions:
        if not selected and definition.get("default", True) is False:
            continue
        if selected and definition["id"] not in selected:
            continue
        payload = dict(definition["request"])
        payload.update(start_date=base.isoformat(), end_date=(
            base + timedelta(days=payload["travel_days"] - 1)
        ).isoformat())
        request = TripRequest.model_validate(payload)
        result.append({**definition, "request": request.model_dump(mode="json")})
    if not 1 <= len(result) <= 3:
        raise ValueError("每批只允许1至3条真实请求")
    return result


class Redactor:
    """Scrub configured credentials and common credential patterns before storage."""

    def __init__(self, environment):
        self.secrets = sorted({str(value) for key, value in environment.items()
                               if value and len(str(value)) >= 4
                               and any(word in key.upper() for word in
                                       ("KEY", "SECRET", "PASSWORD", "TOKEN"))},
                              key=len, reverse=True)

    def __call__(self, value):
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)([?&](?:key|api_key|access_token|token|secret|password)=)[^&\s\"'<>]+",
                      r"\1[REDACTED]", text)
        text = re.sub(r"(?i)(bearer\s+)[a-z0-9._~+/-]+", r"\1[REDACTED]", text)
        text = re.sub(r"(https?://)[^/@\s:]+:[^/@\s]+@", r"\1[REDACTED]@", text)
        return text

    def data(self, value):
        if isinstance(value, dict):
            return {key: self.data(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.data(item) for item in value]
        return self(value) if isinstance(value, str) else value


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        return None
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute if hour < 24 and minute < 60 else None


def hours_cover(hours, start, end):
    """Independent numeric-hours check; do not guess weekday/season semantics."""
    text = str(hours or "")
    if not text.strip():
        return None
    if re.search(r"暂停营业|永久停业|已停业", text):
        return False
    # Such strings may contain a time range for a different weekday or season.
    if re.search(r"周|星期|礼拜|\d{4}[-年]|\d+月|节假日|工作日|闭馆|休息|季", text):
        return None
    if re.search(r"24小时|全天开放|全天营业", text):
        return True
    ranges = re.findall(r"(\d{1,2}):(\d{2})\s*[-—–~～至]\s*(\d{1,2}):(\d{2})", text)
    if not ranges:
        return None
    for h1, m1, h2, m2 in ranges:
        if int(h1) > 23 or int(h2) > 24 or int(m1) > 59 or int(m2) > 59 or (int(h2) == 24 and int(m2)):
            return None
        left, right = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
        if right <= left:
            right += 1440
        shift = 1440 if right > 1440 and start < left else 0
        if left <= start + shift and end + shift <= right:
            return True
    return False


def audit_plan(plan, request):
    """Independent assertions plus explicitly labelled production-validator findings."""
    findings, statistics = [], []

    def issue(code, severity, message, day=None, origin="independent"):
        findings.append(dict(code=code, severity=severity, message=message,
                             day_index=day, origin=origin))

    days = plan.get("days", [])
    if not days:
        issue("empty_plan", "error", "没有每日行程")
    for key in ("city", "start_date", "end_date", "trip_mode"):
        if plan.get(key, "single_city" if key == "trip_mode" else None) != request[key]:
            issue("request_mismatch", "error", f"结果的{key}与请求不一致")
    if len(days) != request["travel_days"]:
        issue("day_count", "error", "天数与请求不一致")
    if request["trip_mode"] != "single_city" and plan.get("route_nodes") != request["destinations"]:
        issue("route_nodes", "error", "跨城路线顺序与请求不一致")
    seen = set()
    scenic_names = {}
    for index, day in enumerate(days):
        expected_date = (date.fromisoformat(request["start_date"]) + timedelta(days=index)).isoformat()
        if day.get("date") != expected_date or day.get("day_index") != index:
            issue("day_identity", "error", "每日日期或序号不一致", index)
        timeline = day.get("timeline", [])
        attractions, meals = day.get("attractions", []), day.get("meals", [])
        meal_times, attraction_refs, meal_refs = {}, Counter(), Counter()
        previous = None
        visited = False
        for item in timeline:
            start, end = minutes(item.get("start_time")), minutes(item.get("end_time"))
            if (start is not None and item.get("end_time") is None
                    and item.get("kind") in {"hotel", "hub", "note"}
                    and item.get("duration_minutes", 0) == 0):
                end = start  # Production encodes instantaneous hotel markers this way.
            if start is None or end is None:
                untimed = item.get("kind") in {"note", "hub"} and not item.get("duration_minutes")
                if not untimed:
                    issue("untimed_event", "error", f"{item.get('name')}缺少有效起止时间", index)
            else:
                if end < start or end - start != item.get("duration_minutes", 0):
                    issue("duration_mismatch", "error", f"{item.get('name')}的起止时间和时长不一致", index)
                if previous is not None and start < previous:
                    issue("overlap", "error", f"{item.get('name')}与上一节点重叠", index)
                previous = max(previous or 0, end)
                if end >= 23 * 60 + 59:
                    issue("midnight_clamp", "error", "存在23:59节点，疑似时间溢出钳制", index)
            kind = item.get("kind")
            if (not visited and kind == "note" and item.get("source") == "planned_break"
                    and item.get("duration_minutes", 0) >= 90 and start is not None and start < 12*60):
                issue("long_morning_idle", "warning", f"首次游览前安排了{item['duration_minutes']}分钟休整，请核对是否确有开放时间/抵离限制", index)
            if kind == "attraction":
                visited = True
            if kind == "transport":
                if not item.get("from_name") or not item.get("to_name"):
                    issue("transport_endpoint", "error", "交通段缺少起点或终点名称", index)
                if item.get("duration_minutes", 0) > 90 and not item.get("is_intercity"):
                    issue("long_local_transport", "warning", f"市内交通{item.get('duration_minutes')}分钟：{item.get('from_name')}→{item.get('to_name')}", index)
            if kind not in {"meal", "attraction"}:
                continue
            pool = meals if kind == "meal" else attractions
            ref = item.get("reference_index")
            if not isinstance(ref, int) or isinstance(ref, bool) or not 0 <= ref < len(pool):
                issue("broken_reference", "error", f"{item.get('name')}关联对象缺失", index)
                continue
            target = pool[ref]
            (meal_refs if kind == "meal" else attraction_refs)[ref] += 1
            if item.get("name") != target.get("name"):
                issue("stale_timeline_name", "error", "时间轴名称与地点对象不同步", index)
            hours = target.get("opening_hours", "") if kind == "meal" else (target.get("ticket_info") or {}).get("opening_hours", "")
            if start is not None and end is not None:
                covered = hours_cover(hours, start, end)
                if covered is False:
                    issue("opening_conflict", "error", f"{target.get('name')} {item.get('start_time')}-{item.get('end_time')}不在返回的营业时段{hours}内", index)
                elif covered is None:
                    issue("hours_unverified", "warning", f"{target.get('name')}营业时间缺失或含日期/星期规则，需人工核验：{hours or '未返回'}", index)
                if kind == "meal":
                    meal_type = target.get("type")
                    meal_times[meal_type] = item.get("start_time")
                    limits = {"breakfast": (6 * 60, 10 * 60 + 30), "lunch": (11 * 60, 14 * 60), "dinner": (17 * 60, 20 * 60 + 30)}
                    if meal_type in limits and not limits[meal_type][0] <= start <= limits[meal_type][1]:
                        issue("meal_window", "error", f"{meal_type}安排在{item.get('start_time')}，超出正常餐段", index)
        for ref, attraction in enumerate(attractions):
            key = attraction.get("poi_id") or attraction.get("name")
            if key in seen:
                issue("duplicate_attraction", "error", f"景点重复：{attraction.get('name')}", index)
            seen.add(key)
            # Independently detect the narrow parent/repeated-leaf alias form.
            # Distinct subareas, e.g. 钟山景区-中山陵 and 钟山景区-明孝陵,
            # must not be collapsed just because they share a prefix.
            label = re.sub(r"[（(].*?[)）]|\s+", "", attraction.get("name", ""))
            parts = re.split(r"[-—－]", label)
            stripped = [re.sub(r"(?:风景名胜区|风景区|景区|旅游区)$", "", part) for part in parts]
            identity = stripped[0] if len(stripped[0]) >= 2 and all(part == stripped[0] for part in stripped) else label
            previous_label = scenic_names.get(identity)
            if previous_label is not None and previous_label != label:
                issue("duplicate_scenic_entity", "error", f"疑似同一景区重复安排：{previous_label}、{label}", index)
            scenic_names[identity] = label
            if not attraction.get("poi_id"):
                issue("attraction_no_poi", "warning", f"{attraction.get('name')}缺少可追溯POI", index)
            if attraction_refs[ref] != 1:
                issue("attraction_unscheduled", "error", f"{attraction.get('name')}没有且仅有一个时间轴节点", index)
        for ref, meal in enumerate(meals):
            if meal_refs[ref] != 1:
                issue("meal_unscheduled", "error", f"{meal.get('name')}没有且仅有一个时间轴节点", index)
            if meal.get("data_source") in {"logistics_advice", "travel_packed"} or not meal.get("poi_id"):
                issue("meal_not_restaurant", "warning", f"{meal.get('type')}为现场用餐建议/自备餐或无POI，而非已核验餐厅：{meal.get('name')}", index)
            if any(word in meal.get("name", "") for word in ("仅外卖", "外卖专营", "无堂食", "不提供堂食", "中央厨房")):
                issue("takeaway_only", "error", f"疑似非堂食：{meal.get('name')}", index)
        expected_meals = {"breakfast", "lunch", "dinner"}
        if index == 0 and request.get("arrival_time"):
            arrival = minutes(request["arrival_time"])
            if arrival >= 10 * 60:
                expected_meals.discard("breakfast")
            ready = next((minutes(item.get("end_time")) for item in timeline if item.get("kind") == "hotel"), None)
            if arrival > 14 * 60 or ready is not None and ready > 14 * 60:
                expected_meals.discard("lunch")
            if arrival > 20 * 60 + 30:
                expected_meals.discard("dinner")
        if index == len(days)-1 and request.get("departure_time"):
            departure = minutes(request["departure_time"])
            if departure < 8 * 60:
                expected_meals.discard("breakfast")
            if departure < 14 * 60:
                expected_meals.discard("lunch")
            if departure < 19 * 60:
                expected_meals.discard("dinner")
        missing = expected_meals - {meal.get("type") for meal in meals}
        if missing:
            issue("missing_meals", "error", "可用时段缺少餐次：" + ", ".join(sorted(missing)), index)
        if not timeline:
            issue("missing_timeline", "error", "全天没有时间轴", index)
        if not attractions:
            issue("no_attractions", "warning", "全天没有景点，需确认是否确为赶路/抵离日", index)
        elif len(attractions) == 1 and not day.get("route_segment") and not (
            index == 0 and request.get("arrival_time") or index == len(days)-1 and request.get("departure_time")
        ):
            issue("sparse_day", "warning", "完整日只有一个景点，需核对是否为大景区或过度裁剪", index)
        afternoon = any(item.get("kind") == "attraction" and (minutes(item.get("end_time")) or 0) > 14*60 for item in timeline)
        if not afternoon and not day.get("route_segment") and not (
            index == len(days)-1 and request.get("departure_time")
        ):
            issue("empty_afternoon", "warning", "下午无游览，需检查是否被餐饮/交通挤占", index)
        segment = day.get("route_segment")
        intercity = [item for item in timeline if item.get("is_intercity")]
        if segment and segment.get("origin") != segment.get("destination"):
            if not intercity:
                issue("missing_intercity", "error", "跨城日缺少显式跨城交通段", index)
            rail = any("铁" in item.get("transport_mode", "") or "动车" in item.get("transport_mode", "") for item in intercity)
            if rail:
                roles = {item.get("hub_role") for item in timeline}
                if not {"intercity_departure", "intercity_arrival"} <= roles:
                    issue("unsplit_intercity", "error", "铁路跨城未分别显示两端车站", index)
                for role, endpoint in (("intercity_departure", "to_name"), ("intercity_arrival", "from_name")):
                    hub_names = {item.get("name") for item in timeline if item.get("hub_role") == role}
                    if not any(item.get("kind") == "transport" and not item.get("is_intercity")
                               and item.get(endpoint) in hub_names for item in timeline):
                        issue("missing_station_access", "error", f"{role}缺少市内接驳段", index)
                issue("rail_reference", "warning", "铁路耗时仅供参考，未验证12306真实班次", index)
        statistics.append(dict(date=day.get("date"), city=day.get("overnight_city") or day.get("end_location") or request["city"],
                               attractions=[a.get("name") for a in attractions], meal_times=meal_times,
                               transport_minutes=sum(item.get("duration_minutes", 0) for item in timeline if item.get("kind") == "transport"),
                               removed_count=sum(a.get("original_day_index") == index for a in plan.get("removed_attractions", []))))
    for side, position in (("arrival", 0), ("departure", -1)):
        if not days or not request.get(f"{side}_time"):
            continue
        role = f"trip_{side}"
        hubs = [item for item in days[position].get("timeline", []) if item.get("hub_role") == role]
        if side == "departure" and days[position].get("hotel"):
            hotel_nodes = [item for item in days[position].get("timeline", []) if item.get("kind") == "hotel"]
            if not hotel_nodes or hotel_nodes[-1].get("duration_minutes", 0) < 20:
                issue("missing_checkout_buffer", "error", "离开日酒店出发前没有预留20分钟退房/整理行李", position % len(days))
        if not hubs:
            issue("missing_trip_hub", "error", f"没有{side}交通枢纽节点", position % len(days))
        for hub in hubs:
            selected = request.get(f"{side}_location")
            if selected and selected not in hub.get("name", "") and hub.get("name", "") not in selected:
                issue("wrong_trip_hub", "error", f"所选{selected}被替换为{hub.get('name')}", position % len(days))
            actual = minutes(hub.get("start_time"))
            target = minutes(request[f"{side}_time"])
            if actual is None or (side == "arrival" and actual < target) or (side == "departure" and actual > target):
                issue("hub_time", "error", f"{side}枢纽时间不满足请求", position % len(days))
            if side == "departure" and actual is not None:
                buffer = 120 if request.get("departure_transport") == "飞机" else 60
                if actual > target-buffer:
                    issue("departure_buffer", "error", f"到达枢纽未预留{buffer}分钟安检/候车缓冲", position % len(days))
            endpoint = "from_name" if side == "arrival" else "to_name"
            if not any(item.get("kind") == "transport" and item.get(endpoint) == hub.get("name")
                       for item in days[position].get("timeline", [])):
                issue("missing_hub_access", "error", f"{side}枢纽没有显式市内接驳", position % len(days))
    budget = plan.get("budget")
    if not budget or budget.get("total") != sum(budget.get(key, 0) for key in (
        "total_attractions", "total_hotels", "total_meals", "total_transportation"
    )):
        issue("budget_sum", "error", "预算缺失或分项加总不一致")
    quality = plan.get("quality_report")
    if not quality:
        issue("missing_quality", "error", "没有生产质量校验报告")
    else:
        if not quality.get("passed"):
            issue("production_quality_failed", "error", "生产校验报告未通过")
        for item in quality.get("issues", []):
            issue(item["code"], item["severity"], item["message"], item.get("day_index"), "production_validator")
    return dict(passed=not any(item["severity"] == "error" for item in findings),
                errors=sum(item["severity"] == "error" for item in findings),
                warnings=sum(item["severity"] == "warning" for item in findings),
                findings=findings, days=statistics,
                human_review="pending", human_review_note="自动无硬冲突不等于推荐质量已通过")


def render_report(output, report):
    """Standalone HTML with local links and full per-day timeline, no remote assets."""
    esc = lambda value: html.escape(str(value if value is not None else ""))
    labels = {"not_run": "未调用真实接口", "passed": "自动约束通过·待人工审阅",
              "failed": "自动检查未通过", "error": "执行异常", "timeout": "超时已终止", "degraded": "生成降级", "running": "执行中"}
    sections = []
    for case in report["cases"]:
        folder = output / case["id"]
        links = [f'<a href="{esc(case["id"])}/{name}">{label}</a>' for name, label in (
            ("request.json", "请求"), ("tripPlan.json", "完整行程"), ("run.log", "生成日志"),
            ("progress.jsonl", "阶段进度"), ("checks.json", "自动检查"), ("result.json", "执行结果")
        ) if (folder / name).is_file()]
        checks = case.get("checks") or {}
        findings = "".join(f'<li>{esc(item["severity"])} · {esc(item.get("origin"))} · {esc(item["code"])} · {esc(item["message"])}</li>' for item in checks.get("findings", []))
        days = []
        plan_file = folder / "tripPlan.json"
        if plan_file.is_file():
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            for day in plan.get("days", []):
                rows = []
                for item in day.get("timeline", []):
                    rows.append("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in (
                        item.get("start_time") or "未定", item.get("end_time"), item.get("kind"),
                        item.get("name"), item.get("duration_minutes"),
                        " → ".join(filter(None, (item.get("from_name"), item.get("to_name"))))
                    )) + "</tr>")
                days.append(f'<h4>{esc(day.get("date"))} · {esc(day.get("overnight_city") or day.get("end_location"))} · {len(day.get("attractions", []))}个景点</h4><p>{esc(day.get("description"))}</p><table><tr><th>开始</th><th>结束</th><th>类型</th><th>内容</th><th>分钟</th><th>起终点</th></tr>{"".join(rows)}</table>')
        manual = "".join(f"<li>{esc(item)}</li>" for item in case.get("review_questions", []))
        sections.append(f'<section><h2>{esc(case["title"])}</h2><p>{esc(labels.get(case["status"], case["status"]))} · {esc(case.get("seconds", 0))}秒</p><p>{" · ".join(links)}</p><p>{esc(case.get("error", ""))}</p><p>自动错误 {checks.get("errors", 0)}，警告 {checks.get("warnings", 0)}。人工质量审阅：待完成。</p><ul>{findings}</ul><details><summary>逐日时间轴</summary>{"".join(days) or "尚未生成"}</details><h3>人工审阅</h3><ul>{manual}</ul></section>')
    summary = Counter(case["status"] for case in report["cases"])
    report["summary"] = dict(summary)
    write_json(output / "report.json", report)
    body = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>真实接口小批量评测</title><style>body{font:16px/1.6 system-ui;max-width:1150px;margin:30px auto;padding:0 20px;color:#223}section{border-top:1px solid #ddd;margin:24px 0}h2{font-size:21px}table{border-collapse:collapse;width:100%}td,th{padding:6px;border:1px solid #ddd;text-align:left}summary{cursor:pointer}li{overflow-wrap:anywhere}</style>'
    body += f'<h1>真实接口小批量评测</h1><p>{esc(report["generated_at"])}</p><p>{esc(report["scope"])}</p><p>模式：{esc(report["mode"])}；统计：{esc(dict(summary))}</p><p>未运行不代表通过。未设置真实费用硬上限；单条旅行生成包含多次服务调用。已脱敏日志仍建议分享前检查。</p>'
    (output / "report.html").write_text(body + "".join(sections) + "</html>", encoding="utf-8")
