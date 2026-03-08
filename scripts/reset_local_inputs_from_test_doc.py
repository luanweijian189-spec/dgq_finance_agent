from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.database import SessionLocal
from app.main import build_service_for_scheduler
from app.models import (
    AlertSubscription,
    DailyPerformance,
    NewsDiscoveryCandidate,
    Recommendation,
    Recommender,
    Stock,
    StockPrediction,
)

DATA_DIR = ROOT / "data"
STOCK_DIR = DATA_DIR / "stocks"
RESEARCH_NOTES_PATH = DATA_DIR / "research_notes.jsonl"
LLM_USAGE_PATH = DATA_DIR / "llm_usage.jsonl"
REPORT_DIR = ROOT / "reports" / "daily"
DB_PATH = ROOT / "demo.db"
BACKUP_ROOT = DATA_DIR / "backups"

KEEP_STOCKS = {
    "002384": "东山精密",
    "002436": "兴森科技",
}


@dataclass(frozen=True)
class ImportEntry:
    stock_code: str
    stock_name: str
    message: str
    recommender_name: str = "测试文档导入"
    status: str = "tracking"


IMPORT_ENTRIES = [
    ImportEntry(
        stock_code="300092",
        stock_name="科新机电",
        message="燃气轮机板块近期有哪些边际变化：科新机电作为东方电气新增供应链标的，提供燃机辅机配套，当前东电相关在手订单千万级别，并与西门子保持战略合作。",
    ),
    ImportEntry(
        stock_code="600590",
        stock_name="泰豪科技",
        message="燃气轮机板块近期有哪些边际变化：泰豪科技新增布局燃机机组OEM，配合东方电气，外采机头集成整机，有望拿到东气出口订单。",
    ),
    ImportEntry(
        stock_code="603950",
        stock_name="长源东谷",
        message="燃气轮机板块近期有哪些边际变化：长源东谷新增布局燃气轮机机组，预计今年100-200MW订单，主业利润稳定，表观估值偏低。",
    ),
    ImportEntry(
        stock_code="600875",
        stock_name="东方电气",
        message="燃机链里东方电气处于核心位置，科新机电、泰豪科技等均围绕其燃气轮机与出口订单展开。",
    ),
    ImportEntry(
        stock_code="002515",
        stock_name="金字火腿",
        message="【国盛通信】电芯片：建议重点关注 TIA/Driver 方向，其中中晟微电子由金字火腿参股，电芯片国产替代空间大。",
    ),
    ImportEntry(
        stock_code="688981",
        stock_name="中芯国际",
        message="【国盛通信】电芯片：建议重点关注代工与制造方向，Tower/中芯国际等有望受益于电芯片国产替代。",
    ),
    ImportEntry(
        stock_code="301312",
        stock_name="智立方",
        message="【天风电子】智立方：Lumentum 扩产上游核心设备，光芯片中后道刚需明确，看好公司在 AI 光互连扩产周期中的成长空间。",
    ),
    ImportEntry(
        stock_code="603083",
        stock_name="剑桥科技",
        message="【天风电子】智立方：剑桥科技作为收购 Lumentum 原 Oclaro 产线的重要方，属于 Lumentum 制造与封测外协链条。",
    ),
    ImportEntry(
        stock_code="688498",
        stock_name="源杰科技",
        message="【天风电子】智立方：源杰科技与 Lumentum 同处 EML/DFB 高端激光器赛道，中后道工序与工艺需求高度重合。",
    ),
    ImportEntry(
        stock_code="688048",
        stock_name="长光华芯",
        message="【天风电子】智立方：长光华芯与 Lumentum 同处高速光芯片赛道，中后道工艺需求重合，属于扩产受益方向。",
    ),
    ImportEntry(
        stock_code="688809",
        stock_name="强一股份",
        message="【广发机械】强一股份点评：1-2月经营数据大超预期，受益于 AI 算力和半导体景气周期，维持坚定推荐。",
    ),
    ImportEntry(
        stock_code="300604",
        stock_name="长川科技",
        message="【广发机械】强一股份点评：建议积极关注半导体设备去日化主线，首推测试机方向包括长川科技。",
    ),
    ImportEntry(
        stock_code="688200",
        stock_name="华峰测控",
        message="【广发机械】强一股份点评：建议积极关注半导体设备去日化主线，首推测试机方向包括华峰测控。",
    ),
    ImportEntry(
        stock_code="688627",
        stock_name="精智达",
        message="【广发机械】强一股份点评：建议积极关注半导体设备去日化主线，首推测试机方向包括精智达。",
    ),
    ImportEntry(
        stock_code="300782",
        stock_name="卓胜微",
        message="卓胜微：作为国内稀缺的光芯片 Fab 资源方，SiGe 与硅光相关工艺突破叠加主业周期改善，具备持续跟踪价值。",
    ),
]


def backup_current_state() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / f"reset_input_import_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup_dir / "demo.db")
    if RESEARCH_NOTES_PATH.exists():
        shutil.copy2(RESEARCH_NOTES_PATH, backup_dir / "research_notes.jsonl")
    if LLM_USAGE_PATH.exists():
        shutil.copy2(LLM_USAGE_PATH, backup_dir / "llm_usage.jsonl")
    if STOCK_DIR.exists():
        shutil.copytree(STOCK_DIR, backup_dir / "stocks", dirs_exist_ok=True)
    if REPORT_DIR.exists():
        shutil.copytree(REPORT_DIR, backup_dir / "reports_daily", dirs_exist_ok=True)
    return backup_dir


def filter_research_notes() -> int:
    if not RESEARCH_NOTES_PATH.exists():
        return 0

    keep_keywords = set(KEEP_STOCKS.keys()) | set(KEEP_STOCKS.values())
    kept_lines: list[str] = []
    for line in RESEARCH_NOTES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = json.dumps(payload, ensure_ascii=False)
        if any(keyword in text for keyword in keep_keywords):
            kept_lines.append(json.dumps(payload, ensure_ascii=False))

    if kept_lines:
        RESEARCH_NOTES_PATH.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
    else:
        RESEARCH_NOTES_PATH.write_text("", encoding="utf-8")
    return len(kept_lines)


def cleanup_stock_files() -> tuple[int, int]:
    STOCK_DIR.mkdir(parents=True, exist_ok=True)
    kept = 0
    removed = 0
    for file_path in STOCK_DIR.glob("*.jsonl"):
        if any(file_path.name.startswith(code) for code in KEEP_STOCKS):
            kept += 1
            continue
        file_path.unlink()
        removed += 1
    return kept, removed


def cleanup_reports_and_usage() -> tuple[int, bool]:
    removed_reports = 0
    if REPORT_DIR.exists():
        for file_path in REPORT_DIR.glob("*.md"):
            file_path.unlink()
            removed_reports += 1
    if LLM_USAGE_PATH.exists():
        LLM_USAGE_PATH.unlink()
        return removed_reports, True
    return removed_reports, False


def cleanup_database() -> dict[str, int]:
    session = SessionLocal()
    try:
        keep_ids = {
            stock.id
            for stock in session.scalars(select(Stock).where(Stock.stock_code.in_(tuple(KEEP_STOCKS.keys())))).all()
        }

        deleted_daily = session.query(DailyPerformance).delete(synchronize_session=False)
        deleted_predictions = session.query(StockPrediction).delete(synchronize_session=False)
        deleted_news = session.query(NewsDiscoveryCandidate).delete(synchronize_session=False)
        deleted_alerts = session.query(AlertSubscription).delete(synchronize_session=False)

        deleted_recommendations = 0
        if keep_ids:
            deleted_recommendations = (
                session.query(Recommendation)
                .filter(~Recommendation.stock_id.in_(keep_ids))
                .delete(synchronize_session=False)
            )
            deleted_stocks = session.query(Stock).filter(~Stock.id.in_(keep_ids)).delete(synchronize_session=False)
        else:
            deleted_recommendations = session.query(Recommendation).delete(synchronize_session=False)
            deleted_stocks = session.query(Stock).delete(synchronize_session=False)

        active_recommender_ids = {
            row[0]
            for row in session.execute(select(Recommendation.recommender_id).distinct()).all()
            if row[0] is not None
        }
        if active_recommender_ids:
            deleted_recommenders = (
                session.query(Recommender)
                .filter(~Recommender.id.in_(active_recommender_ids))
                .delete(synchronize_session=False)
            )
        else:
            deleted_recommenders = session.query(Recommender).delete(synchronize_session=False)

        session.commit()
        return {
            "deleted_daily": deleted_daily,
            "deleted_predictions": deleted_predictions,
            "deleted_news": deleted_news,
            "deleted_alerts": deleted_alerts,
            "deleted_recommendations": deleted_recommendations,
            "deleted_stocks": deleted_stocks,
            "deleted_recommenders": deleted_recommenders,
        }
    finally:
        session.close()


def import_entries() -> list[tuple[str, str, int]]:
    session = SessionLocal()
    try:
        service = build_service_for_scheduler(session)
        imported: list[tuple[str, str, int]] = []
        for entry in IMPORT_ENTRIES:
            stock = service._get_or_create_stock(entry.stock_code, stock_name=entry.stock_name)
            recommender = service._get_or_create_recommender(entry.recommender_name, "")

            existing = session.scalar(
                select(Recommendation.id).where(
                    Recommendation.stock_id == stock.id,
                    Recommendation.recommender_id == recommender.id,
                    Recommendation.original_message == entry.message,
                )
            )
            if existing is not None:
                imported.append((entry.stock_code, entry.stock_name, existing))
                continue

            recommendation = Recommendation(
                stock_id=stock.id,
                recommender_id=recommender.id,
                recommend_ts=datetime.utcnow(),
                initial_price=None,
                original_message=entry.message,
                extracted_logic=entry.message,
                status=entry.status,
                source="manual_bulk",
            )
            session.add(recommendation)
            session.commit()
            session.refresh(recommendation)

            service.stock_knowledge_store.append_entry(
                stock_code=entry.stock_code,
                stock_name=entry.stock_name,
                source="manual_bulk",
                operator=entry.recommender_name,
                entry_type="recommendation",
                content=entry.message,
                ts=recommendation.recommend_ts,
            )
            imported.append((entry.stock_code, entry.stock_name, recommendation.id))

        session.commit()
        return imported
    finally:
        session.close()


def summarize_state() -> dict[str, object]:
    session = SessionLocal()
    try:
        stocks = session.scalars(select(Stock).order_by(Stock.stock_code.asc())).all()
        recommendations = session.scalars(select(Recommendation)).all()
        return {
            "stock_count": len(stocks),
            "recommendation_count": len(recommendations),
            "stocks": [(stock.stock_code, stock.stock_name) for stock in stocks],
            "keep_recommendation_count": {
                code: session.scalar(
                    select(Recommendation).join(Stock, Stock.id == Recommendation.stock_id).where(Stock.stock_code == code)
                )
                is not None
                for code in KEEP_STOCKS
            },
        }
    finally:
        session.close()


def main() -> None:
    backup_dir = backup_current_state()
    db_cleanup = cleanup_database()
    kept_research = filter_research_notes()
    kept_files, removed_files = cleanup_stock_files()
    removed_reports, removed_usage = cleanup_reports_and_usage()
    imported = import_entries()
    summary = summarize_state()

    print(f"backup_dir={backup_dir}")
    print(f"db_cleanup={db_cleanup}")
    print(f"kept_research_notes={kept_research}")
    print(f"stock_files_kept={kept_files} stock_files_removed={removed_files}")
    print(f"reports_removed={removed_reports} llm_usage_removed={removed_usage}")
    print(f"imported_count={len(imported)}")
    for code, name, recommendation_id in imported:
        print(f"imported: {code} {name} recommendation_id={recommendation_id}")
    print(f"summary_stock_count={summary['stock_count']}")
    print(f"summary_recommendation_count={summary['recommendation_count']}")
    for code, name in summary["stocks"]:
        print(f"stock: {code} {name}")


if __name__ == "__main__":
    main()