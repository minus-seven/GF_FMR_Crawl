"""SQL 优先问答编排与证据组装。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Iterable
from uuid import UUID

from sqlalchemy import and_, extract, func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.knowledge.search import search_knowledge
from app.models import (
    DatasetBatch,
    Document,
    DocumentVersion,
    Fund,
    FundHolding,
    FundMetric,
    FundReportSource,
)
from app.schemas.chat import (
    ChatClaim,
    ChatEvidence,
    ChatRequest,
    ChatResponseData,
    ChatStructuredValue,
)
from app.services.queries import (
    _document_source_label,
    get_current_snapshot,
)
from app.services.intent import classify_intent_envelope


_FUND_CODE_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{3}\d{3}(?![A-Z0-9])")
_PERIOD_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.]\d{1,2})?"
)


@dataclass(frozen=True)
class QuestionIntent:
    """确定性问题意图。"""

    kind: str
    metric_name: str | None = None
    top_n: int = 10


_METRIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("annualized_volatility_percent", ("年化波动率", "波动率")),
    ("annualized_return_percent", ("年化收益率", "年化回报")),
    ("daily_liquid_assets_percent", ("日流动资产", "每日流动资产")),
    ("weekly_liquid_assets_percent", ("周流动资产", "每周流动资产")),
    ("weighted_average_life_days", ("加权平均存续期", "平均存续期", "WAL")),
    ("weighted_average_maturity_days", ("加权平均到期日", "平均到期日", "WAM")),
    ("portfolio_ytm_percent", ("到期收益率", "到期孳息率", "YTM")),
    ("portfolio_duration_years", ("组合久期", "投资组合久期", "久期")),
    ("equity_debt_ratio", ("股债比", "权益债券比")),
    ("fund_size_hkd_100m", ("基金规模", "资产规模", "规模")),
    ("inception_date", ("成立日期", "成立时间", "成立日")),
    ("investment_objective", ("投资目标", "投資目標", "investment objective")),
    ("investment_strategy", ("投资策略", "投資策略", "investment strategy")),
)


def extract_fund_codes(question: str) -> list[str]:
    """从问题中提取基金代码；参数 question 为用户原始问题。"""

    return list(dict.fromkeys(_FUND_CODE_RE.findall(question.upper())))


def extract_report_period(question: str) -> date | None:
    """从问题提取月份；参数 question 为用户原始问题。"""

    match = _PERIOD_RE.search(question)
    if not match:
        return None
    return date(int(match.group(1)), int(match.group(2)), 1)


def detect_question_intent(question: str) -> QuestionIntent:
    """识别确定性 SQL 字段或检索问题；参数 question 为用户原始问题。"""

    normalized = question.casefold()
    for metric_name, aliases in _METRIC_ALIASES:
        if any(alias.casefold() in normalized for alias in aliases):
            return QuestionIntent(kind="metric", metric_name=metric_name)
    if any(token in normalized for token in ("前五大", "前五", "五大", "top 5")):
        return QuestionIntent(kind="holding", top_n=5)
    if any(token in normalized for token in ("前十大", "前十", "top 10", "主要持仓", "持仓")):
        return QuestionIntent(kind="holding", top_n=10)
    return QuestionIntent(kind="search")


def _period_clause(column, report_period: date):
    """构造按年月匹配的 SQL 条件；参数 column/report_period 为日期列和目标月份。"""

    return and_(
        extract("year", column) == report_period.year,
        extract("month", column) == report_period.month,
    )


def _period_text(value: date | None) -> str:
    """格式化报告期；参数 value 为数据库报告日期。"""

    return value.isoformat() if value else "未知报告期"


def _value_text(value: float | str | None, unit: str | None) -> str:
    """格式化 SQL 值；参数 value/unit 为数值和单位。"""

    if value is None:
        return "未披露"
    if isinstance(value, float):
        rendered = f"{value:.10g}"
    else:
        rendered = str(value)
    unit_label = {
        "percent": "%",
        "HKD_100M": "亿港币",
        "days": "天",
        "years": "年",
    }.get(unit or "", unit or "")
    return f"{rendered}{unit_label}"


def _quality_status(metric: FundMetric) -> str:
    """返回结构化记录质量状态；参数 metric 为指标行。"""

    if metric.review_status == "rejected":
        return "review_required"
    if metric.confidence == "review" or metric.review_status == "pending":
        return "validation_warning"
    return "complete"


def _hidden_version_ids(session: Session, fund_ids: Iterable[UUID]) -> set[UUID]:
    """读取隐藏来源绑定的文档版本；参数 session/fund_ids 为数据库会话和基金范围。"""

    rows = session.scalars(
        select(FundReportSource).where(
            FundReportSource.fund_id.in_(list(fund_ids)),
            FundReportSource.is_active.is_(True),
            FundReportSource.is_visible.is_(False),
        )
    )
    hidden: set[UUID] = set()
    for source in rows:
        raw = (source.metadata_json or {}).get("document_version_id")
        if raw:
            try:
                hidden.add(UUID(str(raw)))
            except (TypeError, ValueError):
                continue
    return hidden


def _visible_sources(session: Session, fund_ids: Iterable[UUID]) -> list[FundReportSource]:
    """读取基金可见来源；参数 session/fund_ids 为数据库会话和基金范围。"""

    return list(
        session.scalars(
            select(FundReportSource).where(
                FundReportSource.fund_id.in_(list(fund_ids)),
                FundReportSource.is_active.is_(True),
                FundReportSource.is_visible.is_(True),
            )
        )
    )


def _version_context(
    session: Session,
    version_ids: Iterable[UUID],
    sources: list[FundReportSource],
) -> dict[UUID, tuple[DocumentVersion, str | None, str | None]]:
    """加载文档来源上下文；参数为数据库会话、版本 ID 和可见来源。"""

    ids = list(dict.fromkeys(version_ids))
    if not ids:
        return {}
    rows = session.execute(
        select(DocumentVersion, Document.document_type)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(DocumentVersion.id.in_(ids))
    ).all()
    return {
        version.id: (
            version,
            _document_source_label(version, sources, document_type),
            document_type,
        )
        for version, document_type in rows
    }


def _structured_evidence(
    *,
    fund: Fund,
    metric: FundMetric,
    context: tuple[DocumentVersion, str | None, str | None],
    supports: str,
) -> ChatEvidence:
    """将指标转换为结构化证据；参数为基金、指标、文档上下文和支持字段。"""

    version, source_label, _ = context
    value = metric.text_value if metric.text_value is not None else metric.numeric_value
    return ChatEvidence(
        evidence_id=f"metric:{metric.id}",
        evidence_type="structured_record",
        supports=supports,
        fund_code=fund.fund_code,
        fund_name=fund.name_cn,
        report_period=metric.report_period,
        document_version_id=metric.document_version_id,
        page_number=metric.source_page,
        source_label=source_label,
        source_path=version.file_path,
        source_url=version.source_url,
        content=f"{metric.metric_name}={_value_text(value, metric.unit)}",
    )


def _holding_evidence(
    *,
    fund: Fund,
    holding: FundHolding,
    context: tuple[DocumentVersion, str | None, str | None],
) -> ChatEvidence:
    """将持仓转换为结构化证据；参数为基金、持仓和文档上下文。"""

    version, source_label, _ = context
    weight = _value_text(float(holding.weight) if holding.weight is not None else None, "%")
    return ChatEvidence(
        evidence_id=f"holding:{holding.id}",
        evidence_type="structured_record",
        supports="holding_claim",
        fund_code=fund.fund_code,
        fund_name=fund.name_cn,
        report_period=holding.report_period,
        document_version_id=holding.document_version_id,
        page_number=holding.source_page,
        source_label=source_label,
        source_path=version.file_path,
        source_url=version.source_url,
        content=f"{holding.security_name}={weight}",
    )


def _claim(claim_id: str, text: str, evidence: list[ChatEvidence]) -> ChatClaim:
    """创建绑定证据的 claim；参数为 ID、文本和证据。"""

    return ChatClaim(
        claim_id=claim_id,
        text=text,
        evidence_ids=[item.evidence_id for item in evidence],
    )


def _resolve_funds(
    session: Session,
    question: str,
    requested_codes: list[str],
) -> tuple[list[Fund], list[str]]:
    """解析基金范围；参数为数据库会话、问题和显式基金代码。"""

    codes = list(dict.fromkeys([code.upper() for code in requested_codes] + extract_fund_codes(question)))
    if codes:
        funds = list(session.scalars(select(Fund).where(Fund.fund_code.in_(codes))))
        found = {fund.fund_code for fund in funds}
        return funds, [code for code in codes if code not in found]
    # 允许直接输入完整基金名称，但名称必须唯一命中。
    candidates = list(session.scalars(select(Fund).where(Fund.name_cn != "")))
    matches = [fund for fund in candidates if fund.name_cn in question]
    if len(matches) == 1:
        return matches, []
    return [], []


def _period_for_question(request: ChatRequest) -> date | None:
    """确定请求报告月份；参数 request 为问答请求。"""

    return request.report_period or extract_report_period(request.question)


def _insufficient(
    *,
    request: ChatRequest,
    snapshot_id: UUID,
    route: str,
    limitations: list[str],
    answer: str = "当前发布快照没有足够证据支持该问题。",
) -> ChatResponseData:
    """构造拒答响应；参数为请求、快照、路由、限制和说明。"""

    return ChatResponseData(
        question=request.question,
        status="insufficient_evidence",
        route=route,  # type: ignore[arg-type]
        answer=answer,
        snapshot_id=snapshot_id,
        limitations=list(dict.fromkeys(limitations)),
    )


def _metric_answer(
    session: Session,
    *,
    request: ChatRequest,
    snapshot_id: UUID,
    funds: list[Fund],
    intent: QuestionIntent,
    period: date | None,
    hidden_version_ids: set[UUID],
    sources: list[FundReportSource],
) -> ChatResponseData:
    """执行 SQL 指标问答；参数为请求、基金范围、意图和来源过滤。"""

    stmt = (
        select(FundMetric, Fund, DocumentVersion, Document.document_type)
        .join(Fund, Fund.id == FundMetric.fund_id)
        .join(DocumentVersion, DocumentVersion.id == FundMetric.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            FundMetric.batch_id == snapshot_id,
            FundMetric.fund_id.in_([fund.id for fund in funds]),
            FundMetric.metric_name == intent.metric_name,
            FundMetric.review_status != "rejected",
        )
    )
    if period:
        stmt = stmt.where(_period_clause(FundMetric.report_period, period))
    if hidden_version_ids:
        stmt = stmt.where(~FundMetric.document_version_id.in_(hidden_version_ids))
    rows = session.execute(stmt).all()
    by_fund: dict[UUID, tuple[FundMetric, Fund, DocumentVersion, str]] = {}
    for metric, fund, version, document_type in rows:
        current = by_fund.get(fund.id)
        if current is None or metric.report_period > current[0].report_period:
            by_fund[fund.id] = (metric, fund, version, document_type)
    contexts = {
        item[0].document_version_id: (item[2], _document_source_label(item[2], sources, item[3]), item[3])
        for item in by_fund.values()
    }
    evidence: list[ChatEvidence] = []
    values: list[ChatStructuredValue] = []
    claims: list[ChatClaim] = []
    limitations: list[str] = []
    answer_parts: list[str] = []
    for metric, fund, _, _ in by_fund.values():
        context = contexts[metric.document_version_id]
        value = metric.text_value if metric.text_value is not None else (
            float(metric.numeric_value) if metric.numeric_value is not None else None
        )
        evidence_item = _structured_evidence(
            fund=fund,
            metric=metric,
            context=context,
            supports=f"{intent.metric_name}_claim",
        )
        evidence.append(evidence_item)
        quality = _quality_status(metric)
        values.append(
            ChatStructuredValue(
                fund_code=fund.fund_code,
                fund_name=fund.name_cn,
                report_period=metric.report_period,
                field=intent.metric_name or "",
                value=value,
                unit=metric.unit,
                review_status=metric.review_status,
                quality_status=quality,
                document_version_id=metric.document_version_id,
            )
        )
        rendered = _value_text(value, metric.unit)
        sentence = f"{fund.fund_code}（{fund.name_cn}）{rendered}，报告期 {_period_text(metric.report_period)}。"
        answer_parts.append(sentence)
        claims.append(_claim(f"claim:{metric.id}", sentence, [evidence_item]))
        if quality != "complete":
            limitations.append(f"{fund.fund_code}:structured_record_{quality}")
    missing = [fund.fund_code for fund in funds if fund.id not in by_fund]
    if missing:
        limitations.append(f"missing_field:{intent.metric_name}:{','.join(missing)}")
    if not by_fund:
        return _insufficient(
            request=request,
            snapshot_id=snapshot_id,
            route="sql",
            limitations=limitations or [f"field_not_disclosed:{intent.metric_name}"],
        )
    if missing:
        status = "insufficient_evidence"
        answer = "已找到部分基金的结构化值，但以下范围没有可用记录：" + ", ".join(missing) + "。" + " ".join(answer_parts)
    else:
        status = "answered"
        answer = " ".join(answer_parts)
        if len(values) > 1 and all(isinstance(item.value, (int, float)) for item in values):
            ordered = sorted(values, key=lambda item: float(item.value), reverse=True)
            if ordered[0].value != ordered[-1].value:
                answer += f" 数值最高为 {ordered[0].fund_code}。"
    return ChatResponseData(
        question=request.question,
        status=status,  # type: ignore[arg-type]
        route="sql",
        answer=answer,
        snapshot_id=snapshot_id,
        structured_values=values,
        claims=claims,
        evidence=evidence,
        limitations=list(dict.fromkeys(limitations)),
        metadata={"field": intent.metric_name, "period_requested": period},
    )


def _holding_answer(
    session: Session,
    *,
    request: ChatRequest,
    snapshot_id: UUID,
    funds: list[Fund],
    intent: QuestionIntent,
    period: date | None,
    hidden_version_ids: set[UUID],
    sources: list[FundReportSource],
) -> ChatResponseData:
    """执行 SQL 持仓问答；参数为请求、基金范围、意图和来源过滤。"""

    stmt = (
        select(FundHolding, Fund, DocumentVersion, Document.document_type)
        .join(Fund, Fund.id == FundHolding.fund_id)
        .join(DocumentVersion, DocumentVersion.id == FundHolding.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            FundHolding.batch_id == snapshot_id,
            FundHolding.fund_id.in_([fund.id for fund in funds]),
        )
    )
    if period:
        stmt = stmt.where(_period_clause(FundHolding.report_period, period))
    if hidden_version_ids:
        stmt = stmt.where(~FundHolding.document_version_id.in_(hidden_version_ids))
    rows = session.execute(stmt).all()
    grouped: dict[UUID, list[tuple[FundHolding, Fund, DocumentVersion, str]]] = {}
    for row in rows:
        grouped.setdefault(row[1].id, []).append(row)
    evidence: list[ChatEvidence] = []
    claims: list[ChatClaim] = []
    limitations: list[str] = []
    answer_parts: list[str] = []
    for fund in funds:
        candidates = grouped.get(fund.id, [])
        if not candidates:
            limitations.append(f"missing_holdings:{fund.fund_code}")
            continue
        latest_period = max(item[0].report_period for item in candidates)
        selected = [item for item in candidates if item[0].report_period == latest_period]
        selected.sort(key=lambda item: float(item[0].weight or 0), reverse=True)
        selected = selected[: intent.top_n]
        fund_evidence: list[ChatEvidence] = []
        holding_parts: list[str] = []
        for holding, holding_fund, version, document_type in selected:
            context = (version, _document_source_label(version, sources, document_type), document_type)
            item = _holding_evidence(fund=holding_fund, holding=holding, context=context)
            evidence.append(item)
            fund_evidence.append(item)
            weight = _value_text(float(holding.weight) if holding.weight is not None else None, "%")
            holding_parts.append(f"{holding.security_name}（{weight}）")
        sentence = f"{fund.fund_code}（{fund.name_cn}）报告期 {_period_text(latest_period)} 的前{intent.top_n}大持仓：" + "；".join(holding_parts) + "。"
        answer_parts.append(sentence)
        claims.append(_claim(f"claim:holdings:{fund.fund_code}:{latest_period}", sentence, fund_evidence))
    missing = [fund.fund_code for fund in funds if fund.id not in grouped]
    if not answer_parts:
        return _insufficient(
            request=request,
            snapshot_id=snapshot_id,
            route="sql",
            limitations=limitations or ["holdings_not_disclosed"],
        )
    status = "insufficient_evidence" if missing else "answered"
    answer = " ".join(answer_parts)
    if missing:
        answer = "以下基金没有可用持仓记录：" + ", ".join(missing) + "。" + answer
    return ChatResponseData(
        question=request.question,
        status=status,  # type: ignore[arg-type]
        route="sql",
        answer=answer,
        snapshot_id=snapshot_id,
        claims=claims,
        evidence=evidence,
        limitations=list(dict.fromkeys(limitations)),
        metadata={"top_n": intent.top_n, "period_requested": period},
    )


def _knowledge_answer(
    session: Session,
    *,
    request: ChatRequest,
    snapshot_id: UUID,
    funds: list[Fund],
    period: date | None,
    hidden_version_ids: set[UUID],
) -> ChatResponseData:
    """执行页级证据检索；参数为请求、快照、基金范围、报告期和隐藏版本。"""

    result = search_knowledge(
        session,
        query=request.question,
        mode=request.mode,
        fund_codes=[fund.fund_code for fund in funds],
        report_periods=[period] if period else [],
        limit=request.limit,
        snapshot_id=snapshot_id,
        query_vector=request.query_vector or None,
    )
    items = [item for item in result["items"] if item["document_version_id"] not in hidden_version_ids]
    if not items:
        return _insufficient(
            request=request,
            snapshot_id=snapshot_id,
            route="knowledge_search",
            limitations=list(result.get("limitations") or []) + ["no_citation_ready_evidence"],
        )
    evidence = [
        ChatEvidence(
            evidence_id=f"chunk:{item['chunk_id']}",
            evidence_type="document_chunk",
            supports="retrieved_evidence",
            fund_code=item["fund_code"],
            fund_name=item["fund_name"],
            report_period=item["report_period"],
            document_version_id=item["document_version_id"],
            page_number=item["page_number"],
            section_title=item["section_title"],
            citation_tier=item["citation_tier"],
            source_path=item["source_path"],
            content=item["content"],
            content_hash=item["content_hash"],
        )
        for item in items
    ]
    claim = _claim(
        "claim:retrieved-evidence",
        "已检索到与问题相关的原文证据片段；当前确定性后端不对未定义字段进行模型推断。",
        evidence,
    )
    limitations = list(result.get("limitations") or []) + ["model_summarization_not_enabled"]
    return ChatResponseData(
        question=request.question,
        status="evidence_only",
        route="knowledge_search",
        answer="已找到相关原文证据，但当前后端仅返回可核验片段，未对其进行未经契约约束的模型归纳。",
        snapshot_id=snapshot_id,
        claims=[claim],
        evidence=evidence,
        limitations=list(dict.fromkeys(limitations)),
        metadata={"result_count": len(evidence), "mode": result.get("mode")},
    )


def answer_question(session: Session, request: ChatRequest) -> ChatResponseData:
    """执行 SQL 优先问答；参数 session/request 为数据库会话和问答请求。"""

    current = get_current_snapshot(session)
    snapshot_id = current.id
    if request.snapshot_id is not None and request.snapshot_id != snapshot_id:
        published = session.scalar(
            select(func.count()).select_from(DatasetBatch).where(
                DatasetBatch.id == request.snapshot_id,
                DatasetBatch.status == "published",
            )
        )
        if not published:
            raise ApiError(403, "E_KNOWLEDGE_SNAPSHOT_NOT_PUBLISHED", "问答只能读取已发布快照")
        snapshot_id = request.snapshot_id
    funds, unknown_codes = _resolve_funds(session, request.question, request.fund_codes)
    limitations: list[str] = []
    if unknown_codes:
        limitations.append(f"fund_not_found:{','.join(unknown_codes)}")
    if not funds:
        return _insufficient(
            request=request,
            snapshot_id=snapshot_id,
            route="sql",
            limitations=limitations or ["fund_scope_required"],
            answer="无法确定唯一基金范围，或基金代码不存在；请提供基金代码。",
        )
    period = _period_for_question(request)
    hidden_version_ids = _hidden_version_ids(session, [fund.id for fund in funds])
    sources = _visible_sources(session, [fund.id for fund in funds])
    intent = detect_question_intent(request.question)
    if intent.kind == "metric":
        response = _metric_answer(
            session,
            request=request,
            snapshot_id=snapshot_id,
            funds=funds,
            intent=intent,
            period=period,
            hidden_version_ids=hidden_version_ids,
            sources=sources,
        )
    elif intent.kind == "holding":
        response = _holding_answer(
            session,
            request=request,
            snapshot_id=snapshot_id,
            funds=funds,
            intent=intent,
            period=period,
            hidden_version_ids=hidden_version_ids,
            sources=sources,
        )
    else:
        response = _knowledge_answer(
            session,
            request=request,
            snapshot_id=snapshot_id,
            funds=funds,
            period=period,
            hidden_version_ids=hidden_version_ids,
        )
    response.limitations = list(dict.fromkeys([*limitations, *response.limitations]))
    response.metadata = {
        **response.metadata,
        "intent_envelope": classify_intent_envelope(request.question).model_dump(mode="json"),
    }
    return response
