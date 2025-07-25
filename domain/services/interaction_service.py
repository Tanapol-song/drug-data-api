# File: domain/services/interaction_service.py

import uuid
from itertools import combinations
from typing import List, Dict

from domain.repository import DrugRepository
from domain.models import (
    DrugItem,
    DrugPayload,
    ContrastItem,
    DrugsResponse,
    PageResponse,
    Pagination,
)
from utils.helpers import (
    codes_from_item,
    fill_codes,
    enrich_items,
)

class InteractionService:
    """Orchestrates drug interaction contrast workflow."""

    def __init__(self, repo: DrugRepository):
        self.repo = repo

    async def get_interactions(
        self,
        payload: DrugPayload,
        page: int = 1,
        row: int = 10
    ) -> DrugsResponse:
        # 1) Resolve any history names to SUBS IDs
        names_to_resolve: List[str] = []
        for it in payload.drug_histories:
            if not await codes_from_item(it) and it.name:
                names_to_resolve.append(it.name)

        if names_to_resolve:
            name_map = await self.repo.resolve_names(names_to_resolve)
            for it in payload.drug_histories:
                if it.name in name_map:
                    it.subs_code = name_map[it.name]

        # 2) Collect all unique codes
        curr_codes = {c for it in payload.drug_currents  for c in await codes_from_item(it)}
        hist_codes = {c for it in payload.drug_histories for c in await codes_from_item(it)}
        all_codes  = list(curr_codes | hist_codes)

        # 3) Fetch detailed drug info (including SUBS mappings)
        detail_map: Dict[str, dict] = await self.repo.query_details(all_codes)

        # ── ENRICH each DrugItem with full hierarchy codes & names ──
        # this mutates payload.drug_currents and payload.drug_histories in place
        await enrich_items(self.repo.driver, payload.drug_currents, detail_map)
        await enrich_items(self.repo.driver, payload.drug_histories, detail_map)

        # 4) Build mapping from SUBS ID to DrugItem
        subs_to_items: Dict[str, List[DrugItem]] = {}
        for group in (payload.drug_currents, payload.drug_histories):
            for itm in group:
                for code in await codes_from_item(itm):
                    entry = detail_map.get(code)
                    if entry and entry.get("subs_codes"):
                        for sid in entry["subs_codes"]:
                            subs_to_items.setdefault(sid, []).append(itm)
                        break

        # 5) Generate unique SUBS ID pairs
        unique_sids = sorted(subs_to_items.keys())
        pairs = [list(p) for p in combinations(unique_sids, 2)]

        # 6) Fetch raw contrast records
        raw_records = await self.repo.fetch_contrasts(pairs)
        pair_to_data = { (r["sub1_id"], r["sub2_id"]): r for r in raw_records }

        # 7) Assemble ContrastItem rows
        rows: List[ContrastItem] = []
        for sid1, sid2 in pairs:
            rec = pair_to_data.get((sid1, sid2)) or pair_to_data.get((sid2, sid1))
            if not rec:
                continue

            for in_item in subs_to_items.get(sid1, []):
                for ct_item in subs_to_items.get(sid2, []):
                    input_fields    = await fill_codes("input", in_item)
                    contrast_fields = await fill_codes("contrast", ct_item)

                    rows.append(ContrastItem(
                        ref_id=str(uuid.uuid4()),
                        **input_fields,
                        **contrast_fields,
                        contrast_type=0,

                        interaction_detail_en=rec["interaction_detail_en"],
                        interaction_detail_th=rec["interaction_detail_th"],
                        onset=rec["onset"],
                        severity=rec["severity"],
                        documentation=rec["documentation"],
                        significance=rec["significance"],
                        management=rec["management"],
                        discussion=rec["discussion"],
                        reference=rec["reference"],

                        input_substances=[{
                            "code": rec["sub1_id"],
                            "name": rec["sub1_name"]
                        }],
                        contrast_substances=[{
                            "code": rec["sub2_id"],
                            "name": rec["sub2_name"]
                        }],
                    ))

        # 8) Paginate
        total = len(rows)
        start = (page - 1) * row
        end   = start + row
        page_data = rows[start:end]

        return DrugsResponse(
            status=True,
            code=200,
            message="get success",
            data=PageResponse(
                pagination=Pagination(page=page, row=len(page_data), total=total),
                data=page_data
            )
        )
