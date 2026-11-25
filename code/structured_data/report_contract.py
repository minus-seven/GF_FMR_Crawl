#!/usr/bin/env python
"""基金月报、半年报和年报的报告契约、关键词注册表与纠错闸门。

该模块不替代 MinerU、模型或现有管理人专用解析器，而是在它们前后提供
统一的报告识别和安全校验层：

* 先区分月报、半年报、年报和未知补充来源；
* 同时识别简体、繁体、英文以及 HTML/Markdown/原生文字/OCR 混合格式；
* 以报告原文锚点约束模型值，发现跨基金模板污染时只清空可疑候选；
* 记录修正原因，供 ``*.meta.json``、数据质量页和人工复核使用。
"""

from __future__ import annotations

import calendar
import copy
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable


REPORT_KIND_MONTHLY = "monthly"
REPORT_KIND_SEMIANNUAL = "semiannual"
REPORT_KIND_ANNUAL = "annual"
REPORT_KIND_SUPPLEMENTARY = "supplementary"


# 字段关键词集中管理；每个字段同时覆盖简体、繁体和英文常见变体。
FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "report_date": (
        "截至", "截止", "資料截至", "资料截至", "As at", "As of", "Data as at",
        "For the period ended", "For the six months ended", "For the year ended",
    ),
    "fund_size": (
        "基金规模", "基金規模", "基金总值", "基金總值", "基金资产净值", "基金資產淨值",
        "總資產值", "总资产值", "Total Net Assets", "Net Assets", "Fund Size", "AUM",
    ),
    "inception_date": (
        "成立日期", "成立日", "類別成立日期", "类别成立日期", "Launch Date",
        "Share Class Inception Date", "Inception Date",
    ),
    "listing_date": (
        "上市日期", "上市日", "Listing Date", "Date of Listing",
    ),
    "investment_objective": (
        "投资目标", "投資目標", "投资目标及策略", "投資目標及策略", "Investment objective",
        "Investment objective and strategy",
    ),
    "investment_strategy": (
        "投资策略", "投資策略", "Investment strategy", "Investment approach",
    ),
    "ytm": (
        "到期收益率", "到期孳息率", "到期收益孳息率", "平均到期收益率", "平均到期孳息率",
        "平均最低孳息率", "最低孳息率", "加權平均收益率", "加权平均收益率",
        "Yield to maturity", "Yield to worst", "YTM", "YTW",
    ),
    "ytm_note": (
        "不能代表本子基金的业绩", "不能代表本子基金的業績", "不代表基金的回报",
        "不代表基金的回報", "does not represent the performance", "not representative of performance",
    ),
    "duration": (
        "组合久期", "組合久期", "平均久期", "平均存续期", "平均存續期", "加权平均存续期",
        "加權平均存續期", "Duration", "Average duration", "Weighted average duration",
    ),
    "wam": (
        "加权平均到期日", "加權平均到期日", "加權平均屆滿期", "加权平均届满期", "平均到期日", "平均屆滿期", "平均届满期",
        "加權平均到期天數", "加权平均到期天数", "Weighted average maturity", "Average maturity", "WAM",
    ),
    "wal": (
        "加权平均存续期", "加權平均存續期", "平均存续期", "平均存續期",
        "Weighted average life", "WAL",
    ),
    "duration_distribution": (
        "久期分布", "久期分佈", "存续期分布", "存續期分佈", "期限分布", "期限分佈",
        "到期日分布", "到期日分佈", "到期分布", "到期分佈", "Duration distribution", "Maturity distribution", "Days to maturity",
    ),
    "asset_allocation": (
        "资产类别分布", "資產類別分佈", "资产类别分佈", "資產類別分布", "资产分布", "資產分佈",
        "组合配置", "組合配置", "投资组合结构", "投資組合結構", "Portfolio structure", "Asset allocation", "Asset type allocation",
    ),
    "geographic_allocation": (
        "地区分布", "地區分佈", "地域分布", "國家分佈", "国家分布", "市场分布", "市場分佈",
        "Geographic allocation", "Country allocation", "Market allocation",
    ),
    "top_holdings": (
        "前十大持仓", "前十大持倉", "前五大持仓", "前五大持倉", "五大持仓", "五大持倉",
        "十大投资项目", "十大投資項目", "主要持仓", "主要持倉", "前五大固定收益工具持仓",
        "前五大固定收益工具持倉", "Top 10 holdings", "Top 5 holdings", "Top holdings", "Portfolio holdings",
    ),
    "portfolio_table": (
        "投资组合表", "投資組合表", "投资组合", "投資組合", "Investment portfolio", "Portfolio statement",
    ),
    "registration_place": (
        "注册地", "註冊地", "国家", "國家", "Country of registration", "Registration place",
    ),
    "holding_type": (
        "类型", "類型", "资产类型", "資產類別", "Listed/quoted investments", "Unlisted/quoted investments",
        "Fixed Income Securities", "Debt Securities", "Collective Investment Scheme",
    ),
    "equity_debt_ratio": (
        "股债比", "股債比", "权益/债券", "權益/債券", "Equity/Debt", "Equity debt ratio",
    ),
    "liquidity": ("流动性", "流動性", "Liquidity", "liquidity risk"),
    "daily_liquid_assets": (
        "每日流动资产", "每日流動資產", "日流动资产", "日流動資產", "Daily liquid assets", "Daily liquid asset",
    ),
    "weekly_liquid_assets": (
        "每周流动资产", "每週流動資產", "周流动资产", "週流動資產", "Weekly liquid assets", "Weekly liquid asset",
    ),
    "seven_day_yield": (
        "七日年化收益率", "七日年化收益", "7日年化收益率", "7-day yield", "Seven day annualized yield",
    ),
    "annualized_yield": ("年化收益率", "年化孳息率", "Annualized yield", "Annualised yield"),
    "bank_exposure": ("银行敞口", "銀行敞口", "银行存款敞口", "Bank exposure", "Bank deposits"),
    "dividend": ("派息", "派息说明", "派息成分", "派息成份", "Dividend", "Distribution"),
}


_SEMI_MARKER = re.compile(
    r"半年報|半年报|半年度報告|半年度报告|中期報告|中期报告|中報|中报|"
    r"interim\s+report|semi[- ]?annual|half[- ]year",
    re.I,
)
_ANNUAL_MARKER = re.compile(
    r"年報|年报|年度報告|年度报告|年度財務報告|年度财务报告|"
    r"(?<!semi[- ])annual\s+(?:financial\s+)?report|year[- ]end\s+report",
    re.I,
)
_MONTHLY_MARKER = re.compile(
    r"月報|月报|基金月報|基金月报|monthly\s+(?:fund\s+)?(?:report|factsheet)|monthly\s+newsletter",
    re.I,
)


@dataclass(frozen=True)
class ReportContract:
    """一次报告识别的稳定结果；参数字段为识别结果及审计信息。"""

    report_kind: str
    language: str
    layout: str
    cutoff_date: str | None
    confidence: float
    matched_markers: tuple[str, ...] = ()
    matched_fields: tuple[str, ...] = ()
    multi_fund: bool = False

    def to_dict(self) -> dict[str, Any]:
        """把契约转为可写入 JSON 的字典；参数为空。"""

        return {
            "report_kind": self.report_kind,
            "language": self.language,
            "layout": self.layout,
            "cutoff_date": self.cutoff_date,
            "confidence": self.confidence,
            "matched_markers": list(self.matched_markers),
            "matched_fields": list(self.matched_fields),
            "multi_fund": self.multi_fund,
        }


def normalize_match_text(value: Any) -> str:
    """归一化关键词匹配文本；参数 value 为 Markdown、HTML 或原生文字。"""

    text = unicodedata.normalize("NFKC", str(value or ""))
    # 只删除真正的 HTML 标签，保留 “<1周”“>3个月” 这类期限区间。
    text = re.sub(r"</?[A-Za-z][^>]*>", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _last_day(year: int, month: int) -> str:
    """返回月份最后一天；参数 year/month 为有效公历年月。"""

    return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"


def extract_cutoff_date(text: str | None) -> str | None:
    """提取报告原文截止日；参数 text 为完整报告文字。"""

    source = unicodedata.normalize("NFKC", str(text or ""))
    month_names = (
        "January|February|March|April|May|June|July|August|September|October|"
        "November|December"
    )
    english = re.search(
        rf"(?:For\s+the\s+(?:(?:six|three|nine|twelve)\s+months?|year|period|quarter|month)\s+ended|"
        rf"As\s+(?:at|of)|Data\s+as\s+at)\s+(\d{{1,2}})\s+({month_names})\s+(20\d{{2}})",
        source,
        re.I,
    )
    if english:
        parsed = datetime.strptime(
            f"{english.group(1)} {english.group(2)} {english.group(3)}", "%d %B %Y"
        )
        return parsed.strftime("%Y-%m-%d")

    chinese = re.search(
        r"(?:截至|截止|資料截至|资料截至|As\s+(?:at|of)|Data\s+as\s+at).{0,45}?"
        r"(20\d{2})\s*[年/-]\s*(\d{1,2})(?:\s*[月/-]\s*(\d{1,2}))?",
        source,
        re.I,
    )
    if chinese:
        year, month = int(chinese.group(1)), int(chinese.group(2))
        day = chinese.group(3)
        return f"{year:04d}-{month:02d}-{int(day):02d}" if day else _last_day(year, month)

    day_first = re.search(
        r"(?:截至|截止|資料截至|资料截至|As\s+(?:at|of)|Data\s+as\s+at).{0,45}?"
        r"(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(20\d{2})",
        source,
        re.I,
    )
    if day_first:
        return f"{int(day_first.group(3)):04d}-{int(day_first.group(2)):02d}-{int(day_first.group(1)):02d}"

    # 明确月报标题只有年月时，使用该月最后一天；此规则不覆盖半年报/年报。
    # 没有“截至/As at”时只在标题区域回退；不能在整份持仓表中把债券
    # 到期日、成立日期或比较期日期误识别为报告期。
    title_month = re.search(
        r"(?<!\d)(20\d{2})\s*[年/-]\s*(1[0-2]|0?[1-9])\s*月?",
        source[:2500],
    )
    if title_month:
        return _last_day(int(title_month.group(1)), int(title_month.group(2)))
    return None


def detect_language(text: str | None) -> str:
    """识别简体、繁体、英文或混合语言；参数 text 为报告文字。"""

    source = normalize_match_text(text)
    traditional = sum(source.count(token.casefold()) for token in ("投資", "資產", "報告", "註冊", "屆滿", "分佈"))
    simplified = sum(source.count(token) for token in ("投资", "资产", "报告", "注册", "届满", "分布"))
    latin = len(re.findall(r"[a-z]", source))
    cjk = len(re.findall(r"[\u3400-\u9fff]", source))
    if cjk == 0 and latin > 20:
        return "en"
    if traditional >= simplified + 1 and traditional > 0 and latin > 20:
        return "zh-Hant+en"
    if traditional >= simplified + 1 and traditional > 0:
        return "zh-Hant"
    if simplified > 0 and latin > 20:
        return "zh-Hans+en"
    if simplified > 0:
        return "zh-Hans"
    return "en" if latin else "unknown"


def detect_layout(text: str | None) -> str:
    """识别 MinerU/原生文本的版式组合；参数 text 为合并后的上下文。"""

    source = str(text or "")
    html_table = bool(re.search(r"<table\b|<tr\b|<td\b", source, re.I))
    markdown_table = bool(re.search(r"^\s*\|.+\|\s*$", source, re.M))
    image_ocr = "[IMAGE_OCR]" in source or bool(re.search(r"image\s+ocr", source, re.I))
    native = "[PDF_TEXT_LAYER]" in source or "Native PDF text fallback" in source
    kinds = sum((html_table, markdown_table, image_ocr, native))
    if kinds > 1:
        return "mixed"
    if html_table:
        return "html_table"
    if markdown_table:
        return "markdown_table"
    if image_ocr:
        return "image_ocr"
    return "native_text"


def detect_report_contract(
    text: str | None,
    *,
    filename: str | None = None,
    category: str | None = None,
) -> ReportContract:
    """识别报告类型、语言、版式和多基金状态；参数为报告文字、文件名和业务类别。"""

    source = " ".join((str(text or ""), str(filename or "")))
    compact = normalize_match_text(source)
    markers: list[str] = []
    if _SEMI_MARKER.search(compact):
        kind = REPORT_KIND_SEMIANNUAL
        markers.append("semiannual_marker")
    elif _ANNUAL_MARKER.search(compact):
        kind = REPORT_KIND_ANNUAL
        markers.append("annual_marker")
    elif _MONTHLY_MARKER.search(compact):
        kind = REPORT_KIND_MONTHLY
        markers.append("monthly_marker")
    else:
        year_month = re.search(r"20\d{2}\s*[年/-]\s*(1[0-2]|0?[1-9])\s*月?", compact)
        compact_yyyymm = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?=[^\d]|$)", compact)
        if compact_yyyymm and ("monthly" in compact or category in {"money", "货币"}):
            kind = REPORT_KIND_MONTHLY
            markers.append("yyyymm_monthly_heading")
        elif year_month and (category in {"money", "货币"} or int(year_month.group(1)) not in {6, 12}):
            kind = REPORT_KIND_MONTHLY
            markers.append("year_month_heading")
        else:
            kind = REPORT_KIND_SUPPLEMENTARY

    matched_fields = tuple(
        name for name, aliases in FIELD_KEYWORDS.items()
        if any(normalize_match_text(alias) in compact for alias in aliases)
    )
    # “Investment portfolio (continued)” 只是同一基金跨页，不足以证明是
    # 多基金；只有出现多个基金/子基金标记，或明确的基金标题重复，才触发
    # 多基金保护闸门。
    multi_fund = bool(
        re.search(r"(?:fund\s*1|fund\s*2|基金\s*[一二三两]|子基金\s*[一二三两]|sub[- ]fund\s+[a-z0-9])", compact, re.I)
    )
    confidence = 0.98 if len(markers) else 0.72 if kind != REPORT_KIND_SUPPLEMENTARY else 0.45
    return ReportContract(
        report_kind=kind,
        language=detect_language(source),
        layout=detect_layout(text),
        cutoff_date=extract_cutoff_date(text),
        confidence=confidence,
        matched_markers=tuple(markers),
        matched_fields=matched_fields,
        multi_fund=multi_fund,
    )


def field_has_anchor(text: str | None, field: str) -> bool:
    """判断原文是否有字段锚点；参数 text/field 为上下文和注册字段名。"""

    compact = normalize_match_text(text)
    if field == "ytm" and re.search(
        r"(?:投资组合特点|投資組合特點)\s*[0-9]+(?:\.[0-9]+)?\s*%\s*"
        r"(?:股份类别|股份類別|类别信息|類別資訊)",
        compact,
        re.I,
    ):
        return True
    aliases = FIELD_KEYWORDS.get(field, ())
    return any(normalize_match_text(alias) in compact for alias in aliases)


def report_contract_prompt(contract: ReportContract) -> str:
    """生成注入模型的报告契约约束；参数 contract 为识别结果。"""

    kind_label = {
        REPORT_KIND_MONTHLY: "月报/事实资料表",
        REPORT_KIND_SEMIANNUAL: "半年报/中期报告",
        REPORT_KIND_ANNUAL: "年报/年度报告",
        REPORT_KIND_SUPPLEMENTARY: "补充来源/未确定类型",
    }.get(contract.report_kind, "未确定类型")
    return (
        "\n\n[REPORT_CONTRACT]\n"
        f"报告类型：{kind_label}（{contract.report_kind}）；语言：{contract.language}；"
        f"版式：{contract.layout}；原文截止日：{contract.cutoff_date or '未识别'}；"
        f"可能是多基金合并报告：{'是' if contract.multi_fund else '否'}。\n"
        "必须优先使用本报告的明确标签与同页/同行数字；不得从文件名、基金名称、同类基金、"
        "法规常识或其他报告复制数值。月报重点核对 WAM/WAL、收益率、流动性、资产/期限分布和前五大；"
        "半年报/年报重点核对投资组合表、持仓份额/市值、注册地、资产类型、前十大、股债比和组合结构。"
        "原文未披露必须返回 null/[]，不要用 0 代替。"
    )


def _clean_items(items: Any, name_key: str, *, max_items: int = 10) -> tuple[list[dict[str, Any]], list[str]]:
    """清理列表候选并记录修正；参数 items/name_key/max_items 为列表、名称键和上限。"""

    if not isinstance(items, list):
        return [], [f"{name_key}:not_list"] if items not in (None, []) else []
    cleaned: list[dict[str, Any]] = []
    corrections: list[str] = []
    seen: set[tuple[str, float | None]] = set()
    placeholders = ("货币市场工具a", "货币市场工具b", "holding a", "holding b", "example bank", "sample")
    for item in items:
        if not isinstance(item, dict):
            corrections.append(f"{name_key}:drop_non_object")
            continue
        label = str(item.get(name_key) or item.get("name") or item.get("region") or "").strip()
        number = item.get("weight_percent")
        try:
            numeric = float(number) if number is not None else None
        except (TypeError, ValueError):
            match = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", str(number or ""))
            numeric = float(match.group(0).replace(",", "")) if match else None
        if not label or numeric is None or not 0 <= numeric <= 100:
            corrections.append(f"{name_key}:drop_invalid_value")
            continue
        if any(token in label.casefold() for token in placeholders):
            corrections.append(f"{name_key}:drop_placeholder")
            continue
        marker = (re.sub(r"\s+", "", label).casefold(), round(numeric, 8))
        if marker in seen:
            corrections.append(f"{name_key}:drop_duplicate")
            continue
        seen.add(marker)
        normalized = dict(item)
        normalized[name_key] = label
        normalized["weight_percent"] = numeric
        cleaned.append(normalized)
    if len(cleaned) > max_items:
        corrections.append(f"{name_key}:truncate_to_{max_items}")
        cleaned = sorted(cleaned, key=lambda item: float(item["weight_percent"]), reverse=True)[:max_items]
    return cleaned, corrections


def _distribution_complete(items: Iterable[dict[str, Any]]) -> bool:
    """判断百分比分布是否完整；参数 items 为含 weight_percent 的分布项。"""

    values = [float(item["weight_percent"]) for item in items if item.get("weight_percent") is not None]
    return bool(values) and 95 <= sum(values) <= 105


def correct_structured_record(
    record: dict[str, Any],
    text: str | None,
    contract: ReportContract,
    *,
    category: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """执行安全纠错并返回记录、审计；参数为模型记录、原文、报告契约和类别。"""

    corrected = copy.deepcopy(record or {})
    issues: list[str] = []
    corrections: list[str] = []
    source = str(text or "")

    if contract.cutoff_date and corrected.get("report_date") != contract.cutoff_date:
        if corrected.get("report_date") not in (None, ""):
            corrections.append("report_date:replace_with_source_cutoff")
        corrected["report_date"] = contract.cutoff_date

    # 报告期不可能晚于结构化运行日。模型容易把持仓的到期日（例如
    # 2027-02-24）误当成报告期；无明确原文截止日时清空该候选，交给
    # 本地规则提取或人工复核，不能让未来日期进入规模趋势。
    report_date_value = corrected.get("report_date")
    if report_date_value not in (None, ""):
        try:
            parsed_report_date = date.fromisoformat(str(report_date_value)[:10])
        except ValueError:
            parsed_report_date = None
        if parsed_report_date is not None and parsed_report_date > date.today():
            issues.append("report_date:future_date_without_valid_cutoff")
            corrections.append("report_date:clear_future_date")
            corrected["report_date"] = contract.cutoff_date

    for field, aliases in (("portfolio_duration_years", "duration"), ("portfolio_ytm_percent", "ytm")):
        if corrected.get(field) not in (None, "") and not field_has_anchor(source, aliases):
            issues.append(f"{field}:value_without_source_anchor")
            corrected[field] = None

    if category in {"money", "货币"} or isinstance(corrected.get("money_market_metrics"), dict):
        metrics = corrected.get("money_market_metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        metric_aliases = {
            "weighted_average_maturity_days": "wam",
            "weighted_average_life_days": "wal",
            "daily_liquid_assets_percent": "daily_liquid_assets",
            "weekly_liquid_assets_percent": "weekly_liquid_assets",
            "seven_day_yield_percent": "seven_day_yield",
            "annualized_yield_percent": "annualized_yield",
        }
        for field, keyword_field in metric_aliases.items():
            value = metrics.get(field)
            if value in (None, ""):
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                numeric = None
            upper = 100 if "percent" in field else 500
            if numeric is None or not 0 <= numeric <= upper or not field_has_anchor(source, keyword_field):
                issues.append(f"money_market_metrics.{field}:untrusted_candidate")
                metrics[field] = None
                corrections.append(f"money_market_metrics.{field}:clear_without_anchor_or_range")
        corrected["money_market_metrics"] = metrics

    for field, name_key in (("top_holdings", "name"), ("duration_distribution", "bucket"), ("geographic_allocation", "region")):
        cleaned, item_corrections = _clean_items(corrected.get(field), name_key)
        corrected[field] = cleaned
        corrections.extend(f"{field}:{item}" for item in item_corrections)
        if field != "top_holdings" and cleaned:
            units = {str(item.get("unit") or "percent").casefold() for item in cleaned}
            total = sum(float(item["weight_percent"]) for item in cleaned)
            complete = _distribution_complete(cleaned) if units <= {"percent"} else 0 < total <= 30
            if not complete:
                issues.append(f"{field}:distribution_total_out_of_range")
                corrected[field] = []
                corrections.append(f"{field}:clear_incomplete_distribution")

    duration = corrected.get("portfolio_duration_years")
    ytm = corrected.get("portfolio_ytm_percent")
    if isinstance(duration, (int, float)) and isinstance(ytm, (int, float)) and abs(float(duration) - float(ytm)) < 1e-9:
        issues.append("portfolio_duration_years_equals_portfolio_ytm_percent")

    if contract.report_kind in {REPORT_KIND_SEMIANNUAL, REPORT_KIND_ANNUAL} and contract.multi_fund:
        if field_has_anchor(source, "portfolio_table") and not corrected.get("top_holdings"):
            issues.append("multi_fund_portfolio_without_target_holdings")

    audit = {
        "report_contract": contract.to_dict(),
        "issues": issues,
        "corrections": corrections,
        "correction_count": len(corrections),
    }
    return corrected, audit

