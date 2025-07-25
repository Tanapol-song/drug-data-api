# File: domain/repository.py

from abc import ABC, abstractmethod
from typing import List, Dict

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
