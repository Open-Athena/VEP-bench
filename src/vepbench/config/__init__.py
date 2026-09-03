"""Validated human-maintained VEP-bench configuration."""

from .model import ModelProfile, load_model_profile
from .task import TaskProfile, load_task_profile

__all__ = ["ModelProfile", "TaskProfile", "load_model_profile", "load_task_profile"]
