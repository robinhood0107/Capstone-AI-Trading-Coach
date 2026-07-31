"""GDELT aggregate를 offline fixture로만 수집하는 설명 전용 경계다."""

from app.data.gdelt.collector import GdeltCollector
from app.data.gdelt.policy import QueryDefinition, QueryRegistry

__all__ = ["GdeltCollector", "QueryDefinition", "QueryRegistry"]
