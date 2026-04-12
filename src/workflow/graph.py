"""
LangGraph ワークフロー定義
- 状態機械でアービトラージサイクルを管理
- monitor → analyze → risk_check → execute → log
"""
import asyncio
from typing import TypedDict, Optional, List, Annotated
from datetime import datetime
from loguru import logger

from langgraph.graph import StateGraph, END

from src.strategies.arbitrage import ArbOpportunity, find_arbitrage
from src.risk.manager import RiskManager
from src.utils.database import Database
from src.utils.notifier import Notifier
from src.config import AppConfig


class BotState(TypedDict):
    """ワークフロー状態"""
    timestamp: str
    opportunities: List[dict]
    selected_opp: Optional[dict]
    risk_approved: bool
    trade_result: Optional[dict]
    kill_switch: bool
    error: Optional[str]


def build_arb_graph(
    risk_manager: RiskManager,
    db: Database,
    notifier: Notifier,
    config: AppConfig,
    poly_connector,
    kalshi_connector,
    market_pairs: list,
) -> StateGraph:
    """アービトラージワークフローグラフを構築する"""

    # ─── ノード定義 ────────────────────────────────────────────

    async def monitor_node(state: BotState) -> BotState:
        """全市場ペアの価格を並列取得する"""
        logger.info("=== 価格監視開始 ===")
        opportunities = []

        tasks = [
            _fetch_and_analyze(poly_id, kalshi_id, topic, risk_manager, config)
            for poly_id, kalshi_id, topic in market_pairs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"価格取得エラー: {result}")
            elif result is not None:
                opportunities.append(result)

        logger.info(f"機会検出数: {len(opportunities)}")
        return {**state, "opportunities": opportunities, "timestamp": datetime.utcnow().isoformat()}

    async def _fetch_and_analyze(
        poly_id: str, kalshi_id: str, topic: str,
        risk_manager: RiskManager, config: AppConfig
    ) -> Optional[dict]:
        poly_price = await poly_connector.get_market_price(poly_id, topic)
        kalshi_price = await kalshi_connector.get_market_price(kalshi_id, topic)
        if not poly_price or not kalshi_price:
            return None
        opp = find_arbitrage(
            poly=poly_price,
            kalshi=kalshi_price,
            portfolio_usd=risk_manager.state.portfolio_usd,
            fee_pct=config.risk.fee_pct,
            slippage_pct=config.risk.slippage_buffer_pct,
            min_edge_pct=config.risk.min_edge_pct,
        )
        if opp:
            # DBに記録
            db.record_opportunity(
                topic=opp.topic,
                poly_market_id=opp.poly.market_id,
                kalshi_market_id=opp.kalshi.market_id,
                poly_price=opp.poly.yes_price,
                kalshi_price=opp.kalshi.yes_price,
                edge=opp.edge,
                kelly_size=opp.kelly.size_usd,
            )
            return {
                "topic": opp.topic,
                "direction": opp.direction,
                "poly_id": opp.poly.market_id,
                "kalshi_id": opp.kalshi.market_id,
                "edge": opp.edge,
                "size_usd": opp.kelly.size_usd,
                "poly_price": opp.poly.yes_price,
                "kalshi_price": opp.kalshi.yes_price,
            }
        return None

    def select_best_node(state: BotState) -> BotState:
        """最もエッジの高い機会を選択する"""
        opps = state["opportunities"]
        if not opps:
            return {**state, "selected_opp": None}
        best = max(opps, key=lambda x: x["edge"])
        logger.info(f"最良機会選択: {best['topic']} edge={best['edge']:.3f}")
        return {**state, "selected_opp": best}

    def risk_check_node(state: BotState) -> BotState:
        """リスク管理チェック（キルスイッチ・ポジションサイズ）"""
        if risk_manager.check_kill_switch():
            logger.critical("キルスイッチ発動 - 取引停止")
            asyncio.create_task(notifier.alert_kill_switch(risk_manager.current_drawdown))
            return {**state, "kill_switch": True, "risk_approved": False}

        opp = state.get("selected_opp")
        if not opp:
            return {**state, "risk_approved": False}

        approved, reason = risk_manager.can_trade(opp["size_usd"], opp["poly_id"])
        if not approved:
            logger.warning(f"リスクチェック不合格: {reason}")
        else:
            logger.info(f"リスクチェック合格: size=${opp['size_usd']:.2f}")
        return {**state, "risk_approved": approved}

    async def execute_node(state: BotState) -> BotState:
        """取引を実行する（paper / live）"""
        opp = state["selected_opp"]
        if not opp:
            return state

        size_usd = opp["size_usd"]
        fee = size_usd * config.risk.fee_pct * 2  # 両脚分

        # Telegram通知
        await notifier.alert_opportunity(
            topic=opp["topic"],
            edge=opp["edge"],
            kelly_size=size_usd,
            poly_price=opp["poly_price"],
            kalshi_price=opp["kalshi_price"],
            paper=config.paper_trading,
        )

        # 注文発注（両脚同時）
        direction = opp["direction"]
        if direction == "poly_yes_kalshi_no":
            poly_side, kalshi_side = "BUY", "no"
        else:
            poly_side, kalshi_side = "BUY", "yes"

        poly_result, kalshi_result = await asyncio.gather(
            poly_connector.place_order(opp["poly_id"], poly_side, opp["poly_price"], size_usd),
            kalshi_connector.place_order(opp["kalshi_id"], kalshi_side, opp["kalshi_price"], int(size_usd)),
        )

        # リスクマネージャーに登録
        risk_manager.register_trade(opp["poly_id"], size_usd, fee)
        db.update_portfolio(risk_manager.state.portfolio_usd)

        # DBに取引記録
        db.record_trade(opp["poly_id"], "polymarket", poly_side, opp["poly_price"], size_usd, fee/2, config.paper_trading)
        db.record_trade(opp["kalshi_id"], "kalshi", kalshi_side, opp["kalshi_price"], size_usd, fee/2, config.paper_trading)

        logger.info(f"取引完了: {opp['topic']} poly={poly_result} kalshi={kalshi_result}")
        return {**state, "trade_result": {"poly": poly_result, "kalshi": kalshi_result}}

    def log_node(state: BotState) -> BotState:
        """サイクル完了ログ"""
        summary = risk_manager.summary()
        logger.info(
            f"サイクル完了 | 残高=${summary['balance']:.2f} | "
            f"DD={summary['drawdown_pct']:.2f}% | "
            f"累計PnL=${summary['total_pnl']:+.2f} | "
            f"取引数={summary['total_trades']}"
        )
        return state

    # ─── グラフ構築 ────────────────────────────────────────────

    def should_execute(state: BotState) -> str:
        if state.get("kill_switch"):
            return "kill"
        if state.get("risk_approved") and state.get("selected_opp"):
            return "execute"
        return "skip"

    graph = StateGraph(BotState)
    graph.add_node("monitor", monitor_node)
    graph.add_node("select_best", select_best_node)
    graph.add_node("risk_check", risk_check_node)
    graph.add_node("execute", execute_node)
    graph.add_node("log", log_node)

    graph.set_entry_point("monitor")
    graph.add_edge("monitor", "select_best")
    graph.add_edge("select_best", "risk_check")
    graph.add_conditional_edges(
        "risk_check",
        should_execute,
        {"execute": "execute", "skip": "log", "kill": END},
    )
    graph.add_edge("execute", "log")
    graph.add_edge("log", END)

    return graph.compile()
