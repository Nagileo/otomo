"""萌娘百科 RAG 工具（设定/梗/考据）。自建 thin client，按需取+缓存，回答须挂来源。"""
from .lore import build_moegirl_tools

__all__ = ["build_moegirl_tools"]
