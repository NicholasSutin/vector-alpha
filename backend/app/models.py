"""Shared pydantic models (API shapes). Analytics modules return plain dicts matching these;
routes validate with them. Keep in sync with docs/API.md."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

AssetType = Literal["stock", "option", "crypto", "dividend", "interest", "fee", "transfer", "other"]
Side = Literal["buy", "sell", "none"]


class Transaction(BaseModel):
    id: str
    source: str
    account_id: str = ""
    ts: str
    symbol: str = ""
    underlying: str = ""
    asset_type: str = "other"
    side: str = "none"
    open_close: str = ""
    qty: float = 0
    price: float = 0
    fees: float = 0
    amount: float = 0
    description: str = ""
    external_id: str = ""
    strategy_tag: str = ""


class ClosedLot(BaseModel):
    lot_id: str
    symbol: str
    underlying: str
    asset_type: str
    strategy_tag: str = ""
    open_ts: str
    close_ts: str
    qty: float
    open_price: float
    close_price: float
    cost: float
    proceeds: float
    fees: float
    realized_pnl: float
    hold_days: float
    open_txn_ids: list[str] = Field(default_factory=list)
    close_txn_ids: list[str] = Field(default_factory=list)


class Position(BaseModel):
    symbol: str
    underlying: str
    asset_type: str
    qty: float
    avg_cost: float
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    account_id: str = ""
    source: str = ""


class Driver(BaseModel):
    key: str
    realized_pnl: float = 0
    trades: int = 0
    fees: float = 0
    gross: float = 0
    share: float = 0


class PeriodSummary(BaseModel):
    period: str
    start: str
    end: str
    realized_pnl: float = 0
    fees: float = 0
    dividends: float = 0
    interest: float = 0
    net_deposits: float = 0
    net_cash_flow: float = 0
    trade_count: int = 0
    closed_lots: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0
    avg_win: float = 0
    avg_loss: float = 0
    expectancy: float = 0
    profit_factor: float = 0
    gross_bought: float = 0
    gross_sold: float = 0
    turnover: float = 0
    avg_hold_days: float = 0
    options_share: float = 0
    concentration_top_symbol: str = ""
    concentration_top_share: float = 0
    by_underlying: list[Driver] = Field(default_factory=list)
    by_asset_type: list[Driver] = Field(default_factory=list)
    by_strategy: list[Driver] = Field(default_factory=list)
    by_weekday: list[Driver] = Field(default_factory=list)
    largest_wins: list[ClosedLot] = Field(default_factory=list)
    largest_losses: list[ClosedLot] = Field(default_factory=list)
    ending_equity: Optional[float] = None
    ending_cash: Optional[float] = None


class ToplineDelta(BaseModel):
    metric: str
    label: str
    a: float
    b: float
    delta: float
    pct: Optional[float] = None
    format: Literal["usd", "int", "pct", "days"] = "usd"


class VarianceDriver(BaseModel):
    dimension: Literal["underlying", "asset_type", "strategy", "weekday"]
    key: str
    a: float
    b: float
    delta: float
    contribution_pct: float
    evidence_lot_ids: list[str] = Field(default_factory=list)
    note: str = ""


class VarianceReport(BaseModel):
    a: PeriodSummary
    b: PeriodSummary
    topline: list[ToplineDelta] = Field(default_factory=list)
    drivers: list[VarianceDriver] = Field(default_factory=list)
    bridge: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    behaviour_flags: list[str] = Field(default_factory=list)


class IngestResult(BaseModel):
    ok: bool = True
    source: str
    account_ids: list[str] = Field(default_factory=list)
    inserted: int = 0
    skipped_duplicates: int = 0
    date_range: dict[str, Optional[str]] = Field(default_factory=lambda: {"start": None, "end": None})
    asset_types: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ProposedTrade(BaseModel):
    id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: float
    order_type: Literal["MKT", "LMT"] = "MKT"
    limit_price: Optional[float] = None
    tif: Literal["DAY", "GTC"] = "DAY"
    rationale: str = ""


class ReportDriver(BaseModel):
    name: str
    contribution_pct: float = 0
    detail: str = ""
    evidence: list[str] = Field(default_factory=list)


class Advisement(BaseModel):
    title: str
    detail: str
    priority: Literal["high", "medium", "low"] = "medium"
    evidence: list[str] = Field(default_factory=list)


class PriorInsightReview(BaseModel):
    insight_id: str
    text: str
    verdict: Literal["followed", "ignored", "validated", "invalidated", "unclear"] = "unclear"
    note: str = ""


class CompanyChange(BaseModel):
    symbol: str
    text: str                                   # what changed at the company in period B (earnings, guidance, price move)
    sources: list[str] = Field(default_factory=list)


class Report(BaseModel):
    headline: str
    what_changed: list[str] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)
    drivers: list[ReportDriver] = Field(default_factory=list)
    behaviour: list[str] = Field(default_factory=list)
    advisements: list[Advisement] = Field(default_factory=list)
    proposed_trades: list[ProposedTrade] = Field(default_factory=list)
    prior_insight_review: list[PriorInsightReview] = Field(default_factory=list)
    confidence: float = 0.5
    sources: list[str] = Field(default_factory=list)
    market_context: list[str] = Field(default_factory=list)   # macro/market regime bullets (Tavily-sourced)
    company_changes: list[CompanyChange] = Field(default_factory=list)   # per top-driver company context (Tavily-sourced)


class Insight(BaseModel):
    id: str
    run_id: Optional[str] = None
    created_at: str
    kind: str
    text: str
    evidence: list[str] = Field(default_factory=list)
    status: str = "open"


class RunSummary(BaseModel):
    id: str
    created_at: str
    period_a: str
    period_b: str
    status: str
    headline: Optional[str] = None
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    prism_session_id: Optional[str] = None
