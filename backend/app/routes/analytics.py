"""Read-only analytics endpoints (docs/API.md, "Portfolio / analytics")."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.analytics import service
from app.models import PeriodSummary, VarianceReport

router = APIRouter(tags=["analytics"])


def _require_data() -> None:
    if not service.has_data():
        raise HTTPException(status_code=400, detail="No transactions loaded. POST /api/ingest/demo first.")


@router.get("/portfolio/overview")
def portfolio_overview() -> dict:
    return service.overview()


@router.get("/performance")
def performance() -> dict:
    _require_data()
    return service.performance()


@router.get("/periods")
def periods() -> dict:
    _require_data()
    return {"periods": [PeriodSummary(**s).model_dump() for s in service.get_period_summaries()]}


# NOTE: /periods/compare must be declared BEFORE /periods/{period} or "compare"
# would be captured as a period path parameter.
@router.get("/periods/compare")
def compare(a: Optional[str] = Query(None), b: Optional[str] = Query(None)) -> dict:
    _require_data()
    if not a or not b:
        pair = service.latest_two_periods()
        if not pair:
            raise HTTPException(status_code=400, detail="Need at least two periods to compare.")
        a, b = a or pair[0], b or pair[1]
    report = service.get_compare(a, b)
    if report is None:
        known = ", ".join(service.get_periods())
        raise HTTPException(status_code=404, detail=f"Unknown period in '{a}' vs '{b}'. Known: {known}")
    return VarianceReport(**report).model_dump()


@router.get("/periods/{period}")
def period_summary(period: str) -> dict:
    _require_data()
    summary = service.get_summary(period)
    if summary is None:
        known = ", ".join(service.get_periods())
        raise HTTPException(status_code=404, detail=f"Unknown period '{period}'. Known: {known}")
    return PeriodSummary(**summary).model_dump()


@router.get("/transactions")
def transactions(period: Optional[str] = None, symbol: Optional[str] = None,
                 underlying: Optional[str] = None, asset_type: Optional[str] = None,
                 limit: int = Query(200, ge=1, le=5000)) -> dict:
    return {"transactions": service.get_transactions(period=period, symbol=symbol,
                                                     underlying=underlying,
                                                     asset_type=asset_type, limit=limit)}


@router.get("/lots")
def lots(period: Optional[str] = None, underlying: Optional[str] = None,
         limit: int = Query(200, ge=1, le=5000)) -> dict:
    return {"lots": service.get_lots(period=period, underlying=underlying, limit=limit)}


@router.get("/positions")
def positions() -> dict:
    return {"positions": service.get_positions()}
