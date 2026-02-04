"""Report API endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import rate_limit
from app.core.security import get_current_user
from app.db.database import get_db
from app.models.report import Report, ReportSchedule, ReportFormat, ReportStatus
from app.models.user import User
from app.schemas.report import (
    MessageResponse,
    ReportDownloadResponse,
    ReportListResponse,
    ReportResponse,
    ReportScheduleCreate,
    ReportScheduleListResponse,
    ReportScheduleResponse,
    ReportScheduleUpdate,
)
from app.services.report_service import (
    ReportGenerator,
    ReportService,
    generate_html_report,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Reports"])


async def get_schedule_or_404(
    schedule_id: str,
    user_id: int,
    db: AsyncSession,
) -> ReportSchedule:
    """Get schedule by ID or raise 404."""
    service = ReportService(db)
    schedule = await service.get_schedule_by_id(schedule_id, user_id)

    if schedule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule not found",
        )

    return schedule


async def get_report_or_404(
    report_id: str,
    user_id: int,
    db: AsyncSession,
) -> Report:
    """Get report by ID or raise 404."""
    service = ReportService(db)
    report = await service.get_report_by_id(report_id, user_id)

    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    return report


# ============== Schedule Endpoints ==============


@router.get(
    "/schedules",
    response_model=ReportScheduleListResponse,
    summary="List report schedules",
    description="Get all report schedules for the current user.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def list_schedules(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all report schedules for the current user.

    Maximum 5 schedules per user.
    """
    service = ReportService(db)
    schedules = await service.get_user_schedules(current_user.id)

    schedule_responses = [
        ReportScheduleResponse(
            id=s.id,
            user_id=s.user_id,
            name=s.name,
            frequency=s.frequency,
            time_of_day=s.time_of_day,
            day_of_week=s.day_of_week,
            day_of_month=s.day_of_month,
            symbols=s.symbols or [],
            include_portfolio=s.include_portfolio,
            include_news=s.include_news,
            is_active=s.is_active,
            last_run_at=s.last_run_at,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in schedules
    ]

    return ReportScheduleListResponse(
        schedules=schedule_responses,
        total=len(schedule_responses),
    )


@router.post(
    "/schedules",
    response_model=ReportScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create report schedule",
    description="Create a new report schedule.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def create_schedule(
    data: ReportScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new report schedule.

    - **name**: Schedule name
    - **frequency**: daily, weekly, or monthly
    - **time_of_day**: Time to generate report (UTC)
    - **day_of_week**: Required for weekly (0=Monday, 6=Sunday)
    - **day_of_month**: Required for monthly (1-31)
    - **symbols**: Stock symbols to include (empty for all watchlist)
    - **include_portfolio**: Include portfolio summary
    - **include_news**: Include news summary

    Maximum 5 schedules per user.
    """
    service = ReportService(db)

    try:
        schedule = await service.create_schedule(current_user.id, data)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return ReportScheduleResponse(
        id=schedule.id,
        user_id=schedule.user_id,
        name=schedule.name,
        frequency=schedule.frequency,
        time_of_day=schedule.time_of_day,
        day_of_week=schedule.day_of_week,
        day_of_month=schedule.day_of_month,
        symbols=schedule.symbols or [],
        include_portfolio=schedule.include_portfolio,
        include_news=schedule.include_news,
        is_active=schedule.is_active,
        last_run_at=schedule.last_run_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


@router.get(
    "/schedules/{schedule_id}",
    response_model=ReportScheduleResponse,
    summary="Get schedule detail",
    description="Get a specific report schedule.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a specific report schedule.

    - **schedule_id**: UUID of the schedule
    """
    schedule = await get_schedule_or_404(schedule_id, current_user.id, db)

    return ReportScheduleResponse(
        id=schedule.id,
        user_id=schedule.user_id,
        name=schedule.name,
        frequency=schedule.frequency,
        time_of_day=schedule.time_of_day,
        day_of_week=schedule.day_of_week,
        day_of_month=schedule.day_of_month,
        symbols=schedule.symbols or [],
        include_portfolio=schedule.include_portfolio,
        include_news=schedule.include_news,
        is_active=schedule.is_active,
        last_run_at=schedule.last_run_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


@router.put(
    "/schedules/{schedule_id}",
    response_model=ReportScheduleResponse,
    summary="Update schedule",
    description="Update a report schedule.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def update_schedule(
    schedule_id: str,
    data: ReportScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update a report schedule.

    - **schedule_id**: UUID of the schedule
    """
    schedule = await get_schedule_or_404(schedule_id, current_user.id, db)

    service = ReportService(db)
    schedule = await service.update_schedule(schedule, data)

    return ReportScheduleResponse(
        id=schedule.id,
        user_id=schedule.user_id,
        name=schedule.name,
        frequency=schedule.frequency,
        time_of_day=schedule.time_of_day,
        day_of_week=schedule.day_of_week,
        day_of_month=schedule.day_of_month,
        symbols=schedule.symbols or [],
        include_portfolio=schedule.include_portfolio,
        include_news=schedule.include_news,
        is_active=schedule.is_active,
        last_run_at=schedule.last_run_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


@router.delete(
    "/schedules/{schedule_id}",
    response_model=MessageResponse,
    summary="Delete schedule",
    description="Delete a report schedule.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a report schedule.

    This will also delete all reports generated by this schedule.

    - **schedule_id**: UUID of the schedule
    """
    schedule = await get_schedule_or_404(schedule_id, current_user.id, db)

    service = ReportService(db)
    await service.delete_schedule(schedule)

    return MessageResponse(message="Schedule deleted successfully")


@router.post(
    "/schedules/{schedule_id}/run",
    response_model=ReportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run schedule manually",
    description="Manually trigger a report generation for a schedule.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def run_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually trigger a report generation for a schedule.

    This creates a pending report that will be generated in the background.

    - **schedule_id**: UUID of the schedule
    """
    schedule = await get_schedule_or_404(schedule_id, current_user.id, db)

    service = ReportService(db)
    generator = ReportGenerator(db)

    # Get symbols
    symbols = await generator.get_symbols_for_schedule(schedule)
    if not symbols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No symbols to include in report. Add symbols to schedule or watchlist.",
        )

    # Create report
    from datetime import datetime, timezone

    title = f"{schedule.name} - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    report = await service.create_report(
        user_id=current_user.id,
        title=title,
        schedule_id=schedule.id,
        format=ReportFormat.JSON.value,
    )

    # Trigger async generation
    from worker.tasks.report_generator import generate_report

    generate_report.delay(str(report.id))

    return ReportResponse(
        id=report.id,
        schedule_id=report.schedule_id,
        user_id=report.user_id,
        title=report.title,
        content=report.content,
        format=report.format,
        status=report.status,
        error_message=report.error_message,
        created_at=report.created_at,
        completed_at=report.completed_at,
    )


# ============== Report Endpoints ==============


class GenerateReportRequest(BaseModel):
    """Request to generate a report immediately."""
    symbols: list[str]


@router.post(
    "/generate",
    response_model=ReportResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate report immediately",
    description="Generate a report immediately for given symbols without a schedule.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def generate_report_immediately(
    data: GenerateReportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a report immediately for given symbols.

    This creates a one-time report without needing a schedule.
    The report will be generated asynchronously.

    - **symbols**: List of stock symbols to include in the report
    """
    if not data.symbols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No symbols provided for report generation.",
        )

    service = ReportService(db)

    # Create report
    from datetime import datetime, timezone

    title = f"Quick Report - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    report = await service.create_report(
        user_id=current_user.id,
        title=title,
        schedule_id=None,  # No schedule for immediate generation
        format=ReportFormat.JSON.value,
    )

    # Store symbols in report content temporarily
    report.content = {"symbols": data.symbols}
    await db.commit()

    # Trigger async generation
    from worker.tasks.report_generator import generate_report

    generate_report.delay(str(report.id))

    return ReportResponse(
        id=report.id,
        schedule_id=report.schedule_id,
        user_id=report.user_id,
        title=report.title,
        content=report.content,
        format=report.format,
        status=report.status,
        error_message=report.error_message,
        created_at=report.created_at,
        completed_at=report.completed_at,
    )


@router.get(
    "",
    response_model=ReportListResponse,
    summary="List reports",
    description="Get all reports for the current user (paginated).",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def list_reports(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    schedule_id: Optional[str] = Query(None, description="Filter by schedule"),
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all reports for the current user.

    - **page**: Page number (starts at 1)
    - **page_size**: Items per page (max 100)
    - **schedule_id**: Filter by schedule UUID
    - **status**: Filter by status (pending, generating, completed, failed)
    """
    service = ReportService(db)
    reports, total = await service.get_user_reports(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        schedule_id=schedule_id,
        status=status,
    )

    report_responses = [
        ReportResponse(
            id=r.id,
            schedule_id=r.schedule_id,
            user_id=r.user_id,
            title=r.title,
            content=r.content,
            format=r.format,
            status=r.status,
            error_message=r.error_message,
            created_at=r.created_at,
            completed_at=r.completed_at,
        )
        for r in reports
    ]

    return ReportListResponse(
        reports=report_responses,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
    )


@router.get(
    "/{report_id}",
    response_model=ReportResponse,
    summary="Get report",
    description="Get a specific report.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a specific report.

    - **report_id**: UUID of the report
    """
    report = await get_report_or_404(report_id, current_user.id, db)

    return ReportResponse(
        id=report.id,
        schedule_id=report.schedule_id,
        user_id=report.user_id,
        title=report.title,
        content=report.content,
        format=report.format,
        status=report.status,
        error_message=report.error_message,
        created_at=report.created_at,
        completed_at=report.completed_at,
    )


@router.get(
    "/{report_id}/download",
    summary="Download report",
    description="Download report in HTML format.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def download_report(
    report_id: str,
    format: str = Query("html", description="Download format (html)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Download a report in HTML format.

    - **report_id**: UUID of the report
    - **format**: Download format (currently only 'html' is supported)
    """
    report = await get_report_or_404(report_id, current_user.id, db)

    if report.status != ReportStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Report is not completed yet",
        )

    if format.lower() == "html":
        html_content = generate_html_report(report)
        filename = f"report_{report.id[:8]}_{report.created_at.strftime('%Y%m%d')}.html"

        return Response(
            content=html_content,
            media_type="text/html",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {format}. Supported formats: html",
        )


@router.delete(
    "/{report_id}",
    response_model=MessageResponse,
    summary="Delete report",
    description="Delete a report.",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def delete_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a report.

    - **report_id**: UUID of the report
    """
    report = await get_report_or_404(report_id, current_user.id, db)

    service = ReportService(db)
    await service.delete_report(report)

    return MessageResponse(message="Report deleted successfully")
