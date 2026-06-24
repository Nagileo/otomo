"""在线内容推荐（B-online）：多策略召回（标签 + 图谱 + 冷门）+ LLM 提名验证。"""
from .tool import RecommendTool
from .verify import CheckSubjectsTool

from ..bangumi.client import BangumiClient


def build_recommend_tools(client: BangumiClient):
    return [RecommendTool(client), CheckSubjectsTool(client)]


__all__ = ["build_recommend_tools"]
