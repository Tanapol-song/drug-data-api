# File: infrastructure/neo4j_repository.py

import ast
from collections import Counter
from typing import List, Dict

from neo4j import AsyncGraphDatabase
from domain.repository import DrugRepository
from utils.cypher import (
    DRUGSEARCH_CYPHER,
    RESOLVE_SUBS_FALLBACK,
    SEARCHSUBS_CYPHER,
    CONTRAST_CYPHER,
    SUBS_NAME_CYPHER,
    STRING_SEARCH_CYPHER,
    STRING_SEARCH_EXTERNAL_CYPHER
)
from utils.helpers import normalize_query, sanitize_for_lucene

class Neo4jDrugRepository(DrugRepository):
    """Neo4j adapter implementing the DrugRepository interface."""

    def __init__(self, driver: AsyncGraphDatabase):
        self.driver = driver

    async def resolve_names(self, names: List[str]) -> Dict[str, List[str]]:
        name_map: Dict[str, List[str]] = {}
        unresolved = set(names)
    
        async with self.driver.session() as session:
            # Step 1: Try STRING_SEARCH_CYPHER
            for name in list(unresolved):
                query = normalize_query(name).lower()
                result = await session.run(STRING_SEARCH_CYPHER, {"q": query})
                record = await result.single()
                if record:
                    subs_codes = record["subs_codes"] or []
                    if isinstance(subs_codes, str):
                        try:
                            subs_codes = ast.literal_eval(subs_codes)
                        except:
                            subs_codes = []
                    if subs_codes:
                        name_map[name] = list(sorted(set(subs_codes)))  # remove duplicates
                        unresolved.discard(name)
    
            # Step 2: Fallback to DRUGSEARCH_CYPHER
            if unresolved:
                sanitized_qs = [sanitize_for_lucene(normalize_query(name)) for name in unresolved]
                query_to_name = {
                    sanitize_for_lucene(normalize_query(name)): name for name in unresolved
                }
    
                result = await session.run(DRUGSEARCH_CYPHER, {"qs": sanitized_qs})
                async for record in result:
                    q = record["code"]
                    best = record["best"]
                    if not best:
                        continue
                    subs_codes = best.get("subs_codes") or []
                    if isinstance(subs_codes, str):
                        try:
                            subs_codes = ast.literal_eval(subs_codes)
                        except:
                            subs_codes = []
                    if subs_codes:
                        original_name = query_to_name.get(q, q)
                        name_map[original_name] = list(sorted(set(subs_codes)))
    
        return name_map


    async def query_details(self, codes: List[str]) -> Dict[str, dict]:
        details: Dict[str, dict] = {}
        found = set()

        async with self.driver.session() as session:
            # primary detail lookup
            result = await session.run(DRUGSEARCH_CYPHER, {"qs": codes})
            async for record in result:
                code = record["code"]
                best = record["best"]
                if not best:
                    continue

                subs_codes = best.get("subs_codes") or []
                subs_names = best.get("subs_names") or []
                if isinstance(subs_codes, str):
                    try:
                        subs_codes = ast.literal_eval(subs_codes)
                    except:
                        subs_codes = []
                if isinstance(subs_names, str):
                    try:
                        subs_names = ast.literal_eval(subs_names)
                    except:
                        subs_names = []

                best["subs_codes"] = subs_codes
                best["subs_names"] = subs_names
                details[code] = best
                found.add(code)

            # fallback for codes without details
            missing = [c for c in codes if c not in found]
            if missing:
                fb = await session.run(RESOLVE_SUBS_FALLBACK, {"codes": missing})
                async for record in fb:
                    sid = record.get("sid")
                    if sid:
                        details[sid] = {
                            "subs_codes": [sid],
                            "subs_names": [],
                            **{f"{lvl}_code": "" for lvl in ["tpu", "tp", "gpu", "gp", "vtm"]},
                            **{f"{lvl}_name": "" for lvl in ["tpu", "tp", "gpu", "gp", "vtm"]},
                        }

        return details

    # async def resolve_subs(self, codes: List[str]) -> Dict[str, List[str]]:
    #     mapping: Dict[str, List[str]] = {}

    #     async with self.driver.session() as session:
    #         result = await session.run(SEARCHSUBS_CYPHER, {"codes": codes})
    #         async for record in result:
    #             raw = record["raw_code"]
    #             subs_ids = record["subs_ids"] or []
    #             mapping[raw] = subs_ids

    #     # ensure every code appears in the map
    #     for code in codes:
    #         mapping.setdefault(code, [])
    #     return mapping
    async def resolve_subs(self, codes: List[str]) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}

        async with self.driver.session() as session:
            result = await session.run(DRUGSEARCH_CYPHER, {"qs": codes})
            async for record in result:
                code = record["code"]
                best = record["best"]
                if not best:
                    continue

                subs_codes = best.get("subs_codes") or []
                if isinstance(subs_codes, str):
                    try:
                        subs_codes = ast.literal_eval(subs_codes)
                    except:
                        subs_codes = []

                mapping[code] = subs_codes

        # Ensure every code appears in the result
        for code in codes:
            mapping.setdefault(code, [])

        return mapping

    async def fetch_contrasts(self, pairs: List[List[str]]) -> List[dict]:
        async with self.driver.session() as session:
            result = await session.run(CONTRAST_CYPHER, {"pairs": pairs})
            return [record.data() async for record in result]

    async def fetch_subs_name_map(self, subs_ids: List[str]) -> Dict[str, str]:
        name_map: Dict[str, str] = {}

        async with self.driver.session() as session:
            result = await session.run(SUBS_NAME_CYPHER, {"subs_ids": subs_ids})
            async for record in result:
                name_map[record["code"]] = record["name"]

        return name_map
    
    async def fetch_external_flags(self, tpu_codes: List[str]) -> Dict[str, str]:
        external_map: Dict[str, str] = {}

        async with self.driver.session() as session:
            for code in tpu_codes:
                result = await session.run(STRING_SEARCH_EXTERNAL_CYPHER, {"q": code.lower()})
            # เก็บ external ทุกค่าใน list
                externals = []
                async for record in result:
                    val = record.get("external", "false")
                    externals.append(str(val).strip().lower() == "true") 
                    # externals.append(str(val).strip().lower())

                # ถ้ามี 'true' อย่างน้อย 1 ตัว ให้เป็น 'true' ไม่งั้น 'false'
                combined_external = any(externals)
                # combined_external = "true" if "true" in externals else "false"
                external_map[code] = combined_external

        return external_map
