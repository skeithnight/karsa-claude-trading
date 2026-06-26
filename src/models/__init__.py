# Import all models here so they are registered with Base.metadata
# This ensures init_db() creates all tables.
from src.models.tables import (
    CashBalance,
    PortfolioState,
    Signal,
    PaperPosition,
    ClosedPaperTrade,
    AuditLog,
    OHLCVCache,
    MarketHoliday,
    PendingApproval
)