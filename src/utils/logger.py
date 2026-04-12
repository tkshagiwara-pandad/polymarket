"""
ロギングユーティリティ
- loguru による構造化ログ
- ファイル・コンソール同時出力
- ローテーション対応
"""
import sys
from pathlib import Path
from loguru import logger


def setup_logger(log_level: str = "INFO") -> None:
    """ロガーを初期化する"""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # デフォルトのハンドラを削除
    logger.remove()

    # コンソール出力（カラー付き）
    logger.add(
        sys.stdout,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # ファイル出力（JSON形式、日次ローテーション）
    logger.add(
        "logs/arb_bot_{time:YYYY-MM-DD}.log",
        level=log_level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} | {message}",
        rotation="00:00",      # 深夜0時にローテーション
        retention="30 days",   # 30日保持
        compression="zip",
        encoding="utf-8",
    )

    # エラー専用ログ
    logger.add(
        "logs/errors.log",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} | {message}\n{exception}",
        rotation="10 MB",
        retention="90 days",
        encoding="utf-8",
    )


def get_logger(name: str):
    """名前付きロガーを返す"""
    return logger.bind(name=name)
