"""MrBit Smart Processing Engine — orchestration and resource management."""

from app.services.smart_processing.model_recommender import recommend_models
from app.services.smart_processing.resource_monitor import collect_resources

__all__ = ["collect_resources", "recommend_models"]
