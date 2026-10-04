"""Compatibility import for the misspelled module in the original scaffold."""

from .orchestrator import PipelineOrchestrator

Orchstrator = PipelineOrchestrator

__all__ = ["Orchstrator", "PipelineOrchestrator"]
