from pathlib import Path
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import Base
from app.input_parser_agent import LLMInputParserAgent
from app.llm_usage_store import LLMUsageStore
from app.models import Recommendation, Stock
from app.notifier import StdoutNotifier
from app.providers import MockMarketDataProvider, MockNewsDataProvider
from app.rag_store import ResearchNoteStore
from app.services import FinanceAgentService
from app.stock_knowledge_store import StockKnowledgeStore


SEED_STOCKS = {
    "科新机电": "300092",
    "泰豪科技": "600590",
    "长源东谷": "603950",
    "东方电气": "600875",
    "金字火腿": "002515",
    "中芯国际": "688981",
    "智立方": "301312",
    "剑桥科技": "603083",
    "源杰科技": "688498",
    "长光华芯": "688048",
    "强一股份": "688809",
    "长川科技": "300604",
    "华峰测控": "688200",
    "精智达": "688627",
    "卓胜微": "300782",
}


def build_service() -> tuple[FinanceAgentService, Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    for name, code in SEED_STOCKS.items():
        db.add(Stock(stock_code=code, stock_name=name))
    db.commit()

    service = FinanceAgentService(
        db=db,
        market_provider=MockMarketDataProvider(),
        news_provider=MockNewsDataProvider(),
        notifier=StdoutNotifier(),
        rag_store=ResearchNoteStore("tests/.tmp_input_rag.jsonl"),
        stock_knowledge_store=StockKnowledgeStore("tests/.tmp_input_stocks"),
        llm_usage_store=LLMUsageStore("tests/.tmp_input_usage.jsonl"),
        input_parser_agent=LLMInputParserAgent(
            model_name="qwen2.5:3b",
            api_key="",
            api_base="http://127.0.0.1:11434/v1",
        ),
    )
    return service, db


def main() -> None:
    service, db = build_service()

    cases = [
        (
            "燃机链推荐",
            """燃气轮机板块近期有哪些边际变化：
1、科新机电：东方电气新增供应链标的，提供燃机辅机配套。
2、泰豪科技：新增布局燃机机组OEM，配合东方电气，外采机头集成整机。
3、长源东谷：新增布局燃气轮机机组，预计今年100-200MW订单。""",
        ),
        (
            "霍尔木兹宏观",
            """霍尔木兹海峡是全球22%液化天然气（LNG）的唯一运输通道，这一咽喉要道直接掐住日韩经济命脉。
韩国供应了全球超60%的存储芯片，若封锁持续超过14天，全球芯片供应链会在两周内直接断裂。""",
        ),
        (
            "电芯片推荐",
            """【国盛通信】电芯片：光通信核心枢纽，国产份额有望提升。
建议重点关注：TIA/Driver：优迅股份、中晟微电子（金字火腿参股）；代工与制造：Tower/中芯国际等。""",
        ),
        (
            "科新机电深度",
            """【科新机电】：未被发掘的东方电气核心配套燃机零部件厂商。
随着东方电气G级、J级燃机订单的放量，科新作为同城核心配套商，业绩边际增量已来。""",
        ),
        (
            "智立方扩产链",
            """【天风电子】智立方：Lumentum扩产上游核心设备，光芯片中后道刚需明确。
Fabrinet、剑桥科技均是Lumentum核心制造与封测外协方。源杰科技、长光华芯等国内高速光芯片龙头，与Lumentum同处EML/DFB高端激光器赛道。""",
        ),
        (
            "强一股份国产替代",
            """【广发机械】强一股份点评：1-2月经营数据大超预期！国产算力核心标的。
建议积极关注半导体设备的去日化主线；首推测试机（长川科技、华峰测控、精智达），以及探针卡（强一股份）。""",
        ),
        (
            "卓胜微硅光",
            """卓胜微：稀缺光芯片Fab资源中国tower，主业大周期拐点。
卓胜微作为国内唯一拥有自己Fab的射频大厂，投资近100亿元建设了芯卓产线。""",
        ),
    ]

    print(f"case_count={len(cases)}")
    for index, (title, text) in enumerate(cases, start=1):
        print(f"\n[case#{index}] {title}")
        understood = service.input_parser_agent.understand_message(text)
        if understood is None:
            print("understood=None")
        else:
            print(
                f"understood: type={understood.message_type} confidence={understood.confidence:.3f} "
                f"stocks={[(item.stock_code, item.stock_name) for item in understood.stocks]}"
            )
        parsed = service._parse_recommendations(text, "测试研究员", None)
        print(
            "parsed=",
            [
                {
                    "stock_code": item.get("stock_code", "") if isinstance(item, dict) else getattr(item, "stock_code", ""),
                    "stock_name": item.get("stock_name", "") if isinstance(item, dict) else getattr(item, "stock_name", ""),
                    "status": item.get("status", "") if isinstance(item, dict) else getattr(item, "status", ""),
                }
                for item in parsed
            ],
        )
        created = service.ingest_message(
            message=text,
            recommender_name="测试研究员",
            source="manual_bulk",
            deduplicate=True,
        )
        print(
            "created=",
            [
                {
                    "stock_code": item.stock.stock_code,
                    "stock_name": item.stock.stock_name,
                    "status": item.status,
                }
                for item in created
            ],
        )

    rows = db.execute(select(Recommendation, Stock).join(Stock, Stock.id == Recommendation.stock_id).order_by(Recommendation.id.asc())).all()
    print("\n[summary]")
    print(f"recommendation_count={len(rows)}")
    print(
        "unique_stocks=",
        sorted({(stock.stock_code, stock.stock_name, recommendation.status) for recommendation, stock in rows}),
    )
    pending = [(stock.stock_code, stock.stock_name) for recommendation, stock in rows if recommendation.status == "pending_mapping"]
    print(f"pending_mapping={pending}")

    db.close()


if __name__ == "__main__":
    main()
