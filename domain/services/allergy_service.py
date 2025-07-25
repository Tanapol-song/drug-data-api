# File: domain/services/allergy_service.py

import time
from typing import List, Dict
from collections import OrderedDict

from domain.repository import DrugRepository
from domain.models import (
    DrugItem,
    AllergyPayload,
    AllergyItem,
    AllergyResponse,
    PageResponse,
    Pagination,
)
from utils.helpers import codes_from_item, fill_codes, enrich_items

class AllergyService:
    """Orchestrates allergy summary workflow."""

    def __init__(self, repo: DrugRepository):
        self.repo = repo

    async def get_allergy(
        self,
        payload: AllergyPayload,
        page: int = 1,
        row: int = 10
    ) -> AllergyResponse:
        # 0) (Optional) start timer
        t0 = time.perf_counter()

        # 1) Combine all items and cache raw codes
        all_items = payload.drug_currents + payload.drug_histories + payload.drug_allergies
        code_cache = { id(it): await codes_from_item(it) for it in all_items }

        # Flatten any nested lists in code_cache
        for key, codes in list(code_cache.items()):
            flat: List[str] = []
            for code in codes:
                if isinstance(code, list):
                    flat.extend(code)
                else:
                    flat.append(code)
            code_cache[key] = flat

        # 2) Resolve names → subs_code for history/allergy items without codes
        name_items = [
            it for it in payload.drug_histories + payload.drug_allergies
            if not code_cache[id(it)] and it.name
        ]
        if name_items:
            name_map = await self.repo.resolve_names([it.name for it in name_items])
            # print(f"Resolved names: {name_map}")
            for it in name_items:
                subs = name_map.get(it.name)
                if subs:
                    if isinstance(subs, list):
                        it.subs_code = subs[0]
                        code_cache[id(it)] = subs
                    else:
                        it.subs_code = subs
                        code_cache[id(it)] = [subs]

        # 3) Collect all unique codes across groups
        curr_codes    = { c for it in payload.drug_currents  for c in code_cache[id(it)] }
        hist_codes    = { c for it in payload.drug_histories for c in code_cache[id(it)] }
        allergy_codes = { c for it in payload.drug_allergies  for c in code_cache[id(it)] }
        all_codes     = list(curr_codes | hist_codes | allergy_codes)

        # 4) Resolve SUBS sets and fetch detailed info
        subs_map   = await self.repo.resolve_subs(all_codes)
        detail_map = await self.repo.query_details(all_codes)

        # 5) Enrich each DrugItem with full hierarchy codes & names
        await enrich_items(self.repo.driver, payload.drug_currents,  detail_map)
        await enrich_items(self.repo.driver, payload.drug_histories, detail_map)
        await enrich_items(self.repo.driver, payload.drug_allergies, detail_map)

        # 6) Build sets of active SUBS from currents and histories
        subs_curr_set = { sid for code in curr_codes    for sid in subs_map.get(code, []) }
        subs_hist_set = { sid for code in hist_codes    for sid in subs_map.get(code, []) }
        active_subs   = subs_curr_set | subs_hist_set

        # 7) Fetch human names only for allergy‐relevant SUBS
        subs_name_map = await self.repo.fetch_subs_name_map(list(active_subs))
        # print(subs_name_map)

        # 8) For each allergy item, emit only if it shares a SUBS with current/history
        rows: List[AllergyItem] = []
        for allergy in payload.drug_allergies:
            its_codes = code_cache[id(allergy)]
            # collect all subs IDs for this allergy
            its_subs: set = set()
            for code in its_codes:
                info = detail_map.get(code, {})
                its_subs |= set(info.get("subs_codes", []))

            # find intersection with active
            common = its_subs & active_subs
            if not common:
                continue

            input_fields = await fill_codes("input", allergy)
            in_curr = bool(common & subs_curr_set)
            in_hist = bool(common & subs_hist_set)

            rows.append(AllergyItem(
                **input_fields,
                is_allergy=True,
                allergy_type=(
                    2 if (in_curr and in_hist)
                    else 0 if in_curr
                    else 1
                ),
                allergy_substances=[
                    {"code": sid, "name": subs_name_map.get(sid, "")}
                    for sid in sorted(common)
                ]
            ))

        # 9) Paginate
        total = len(rows)
        start = (page - 1) * row
        end   = start + row
        page_data = rows[start:end]

        # (Optional) log total time
        # print(f"⏱ TOTAL get_allergy() took {time.perf_counter() - t0:.3f} sec")

        return AllergyResponse(
            status=True,
            code=200,
            message="get success",
            data=PageResponse(
                pagination=Pagination(page=page, row=len(page_data), total=total),
                data=page_data
            )
        )
