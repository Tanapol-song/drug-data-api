# File: domain/repository.py

from abc import ABC, abstractmethod
from typing import List, Dict, Any


class DrugRepository(ABC):
    """Port interface for drug-related data operations."""

    @abstractmethod
    async def resolve_names(self, names: List[str]) -> Dict[str, str]:
        """Map drug names to their primary SUBS ID."""
        ...

    @abstractmethod
    async def query_details(self, codes: List[str]) -> Dict[str, dict]:
        """Fetch detailed drug attributes and SUBS mappings for given codes."""
        ...

    @abstractmethod
    async def resolve_subs(self, codes: List[str]) -> Dict[str, List[str]]:
        """Retrieve all SUBS IDs associated with a list of codes."""
        ...

    @abstractmethod
    async def fetch_contrasts(self, pairs: List[List[str]]) -> List[dict]:
        """Execute contrast queries for SUBS ID pairs and return raw records."""
        ...

    @abstractmethod
    async def fetch_subs_name_map(self, subs_ids: List[str]) -> Dict[str, str]:
        """Obtain human-readable SUBS names for a list of SUBS IDs."""
        ...

    @abstractmethod
    async def fetch_external_flags(self, tpu_codes: List[str]) -> Dict[str, str]:
        """Return mapping of tpu_code to external flag (True/False)."""
        ...

    @abstractmethod
    async def get_all_tpu_data(self) -> list[dict]: ...

    @abstractmethod
    async def get_limit_tpu_data(self, limit: int) -> list[dict]: ...


class ElasticDrugRepository(ABC):
    # @abstractmethod
    # async def get_embedding(self, text: str) -> Any:
    #     """Return embedding vector for the given text."""
    #     ...

    @abstractmethod
    async def check_exists_by_tpu(self, tpu_name: str) -> bool:
        """Check if TPU exists in Elasticsearch by TPU name"""
        ...

    @abstractmethod
    async def insert_embedding(
        self, tpu_name: str, embedding: list[float], tpu_code: str | None = None
    ):
        """Insert new TPU embedding into Elasticsearch"""
        ...
