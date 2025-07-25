# File: utils/helpers.py

import re
from typing import List, Dict
from collections import Counter

from neo4j import AsyncGraphDatabase
from domain.models import DrugItem
from utils.cypher import RESOLVE_SUBS_FALLBACK

LEVELS = ['tpu', 'tp', 'gpu', 'gp', 'vtm']

async def fallback_resolve_subs(tx, codes: List[str]) -> List[str]:
    """
    Given a Neo4j transaction and a list of raw codes, return the list of
    SUBS IDs by running the RESOLVE_SUBS_FALLBACK query.
    """
    result = await tx.run(RESOLVE_SUBS_FALLBACK, {"codes": codes})
    return [record["sid"] async for record in result]


def sanitize_for_lucene(q: str) -> str:
    """
    Escapes special characters in a Lucene query string.
    """
    special_chars = r'+-!():^[]\"{}~*?|&;/'
    return ''.join(['\\' + c if c in special_chars else c for c in q])


def normalize_query(text: str) -> str:
    """
    Clean and normalize a drug name or query string by removing non-alphanumeric characters
    and collapsing whitespace.
    """
    if text:
        cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', text)
        return re.sub(r'\s+', ' ', cleaned).strip()
    return text

async def codes_from_item(it: DrugItem) -> List[str]:
    """
    Extract all non-empty code attributes from a DrugItem.
    Ensure returned list is flat and contains only strings.
    """
    codes = [
        it.tpu_code, it.tp_code, it.gpu_code,
        it.gp_code, it.vtm_code, it.subs_code
    ]

    # Handle case where subs_code might be a list from resolve_names
    flattened: List[str] = []
    for c in codes:
        if isinstance(c, list):
            flattened.extend([x for x in c if x])
        elif isinstance(c, str) and c:
            flattened.append(c)
    return flattened

async def highest_idx(it: DrugItem) -> int:
    """
    Determine the highest-level code present on the item (tpu, tp, gpu, gp, vtm).
    Returns the index in LEVELS where a code first appears.
    """
    for i, lvl in enumerate(LEVELS):
        if getattr(it, f"{lvl}_code", None):
            return i
    return len(LEVELS)

async def fill_codes(prefix: str, it: DrugItem) -> Dict[str, str]:
    """
    Given a prefix ('input' or 'contrast') and a DrugItem,
    populate a dict of '{prefix}_{level}_code' and '{prefix}_{level}_name' fields,
    only including fields at or above the item's highest-code level.
    Also sets '{prefix}_description' to an empty string.
    """
    top = await highest_idx(it)
    out: Dict[str, str] = {}
    for i, lvl in enumerate(LEVELS):
        code_attr = f"{lvl}_code"
        name_attr = f"{lvl}_name"
        out[f"{prefix}_{lvl}_code"] = getattr(it, code_attr) if i >= top else ""
        out[f"{prefix}_{lvl}_name"] = getattr(it, name_attr) if i >= top else ""
    out[f"{prefix}_description"] = ""
    return out

async def enrich_items(
    driver: AsyncGraphDatabase,
    items: List[DrugItem],
    detail_map: Dict[str, dict]
) -> Dict[str, List[DrugItem]]:
    """
    Mutates each DrugItem in 'items' to fill in its hierarchy codes/names
    from detail_map, and returns a mapping SUBS_ID -> list of DrugItems.
    """
    subs_to_items: Dict[str, List[DrugItem]] = {}
    codes_fb: List[str] = []

    async with driver.session() as session:
        for it in items:
            codes = await codes_from_item(it)
            matched = False
            for code in codes:
                info = detail_map.get(code)
                if not info:
                    continue
                matched = True
                for lvl in LEVELS:
                    c_field = f"{lvl}_code"
                    n_field = f"{lvl}_name"
                    if info.get(c_field):
                        setattr(it, c_field, info[c_field])
                    if info.get(n_field):
                        setattr(it, n_field, info[n_field])
                for sid in info.get("subs_codes", []):
                    subs_to_items.setdefault(sid, []).append(it)

            if not matched:
                codes_fb.extend(codes)

        if codes_fb:
            subs_ids = await session.execute_read(fallback_resolve_subs, codes_fb)
            for sid in subs_ids:
                subs_to_items.setdefault(sid, []).append(items[0])

    return subs_to_items
