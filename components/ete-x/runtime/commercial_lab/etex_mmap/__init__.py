"""Exact indexed matrix storage for the isolated commercial feasibility lab."""
from .store import MappedMatrix, StoreCorruptionError, StoreError, build_store

__all__ = ["MappedMatrix", "StoreCorruptionError", "StoreError", "build_store"]
