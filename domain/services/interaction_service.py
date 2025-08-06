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
        print("curr_codes",curr_codes)
        print("hist_codes",hist_codes)
        print("all_codes",all_codes)

        #----------Fetch External----------------------------
        # ✅ เตรียมดึง external flags สำหรับ allergy drugs
        tpu_codes_cur = [it.tpu_code for it in payload.drug_currents if it.tpu_code]
        tpu_codes_his = [it.tpu_code for it in payload.drug_histories if it.tpu_code]
        all_tpu_codes = list(set(tpu_codes_cur + tpu_codes_his))
        external_flags = await self.repo.fetch_external_flags(all_tpu_codes)
        external_map = {str(code): flag for code, flag in external_flags.items()}

        # ให้แน่ใจว่า tpu_code เป็น str ทั้งหมด
        print("external_flags:", external_flags)
        print("external_map:", external_map)

        # 3) Fetch detailed drug info (including SUBS mappings)
        detail_map: Dict[str, dict] = await self.repo.query_details(all_codes)
        # print("detail_map :",detail_map)


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
        print("unique_sids :",unique_sids)
        print("pairs :",pairs)

        # 5.5) Filter out pairs that are True vs False external_flag
        subs_external: Dict[str, bool] = {}
        for tpu_code, detail in detail_map.items():
            external = external_map.get(tpu_code, False)
            for subs_code in detail.get("subs_codes", []):
                if subs_code in subs_external:
                    subs_external[subs_code] = subs_external[subs_code] or external
                else:
                    subs_external[subs_code] = external

            # -- 1) ถ้า external ของทุก subs เป็น True → ไม่ต้องแสดงอะไรเลย
        if all(subs_external.get(sid, False) for sid in unique_sids):
            valid_pairs = []
            print("🟡 Skipping: all substances are external=True")

        # -- 2 & 3) ต้องกรองตามเงื่อนไขที่เหลือ
        else:
            valid_pairs = []
            mixed_pairs = []
            for sid1, sid2 in pairs:
                ext1 = subs_external.get(sid1, False)
                ext2 = subs_external.get(sid2, False)
                
                # เงื่อนไขที่ 2: ทั้งคู่ False → เก็บไว้ก่อน (ต้องเช็คว่ามี contrast จริงทีหลัง)
                if not ext1 and not ext2:
                    valid_pairs.append([sid1, sid2])

                # เงื่อนไขที่ 3: มีคู่ True กับ False → เก็บไว้ตรวจ contrast ทีหลัง
                elif (ext1 and not ext2) or (not ext1 and ext2):
                    mixed_pairs.append([sid1, sid2])

            print("✅ valid_pairs (False vs False):", valid_pairs)
            print("⛔ mixed_pairs (True vs False):", mixed_pairs)

            # ดึงข้อมูล contrast ของ mixed_pairs เพื่อตัดออก
            if mixed_pairs:
                mixed_records = await self.repo.fetch_contrasts(mixed_pairs)
                mixed_contrasts = {(r["sub1_id"], r["sub2_id"]) for r in mixed_records}
                mixed_contrasts |= {(b, a) for a, b in mixed_contrasts}  # ทำให้หาได้แบบไม่เรียง

                for sid1, sid2 in mixed_pairs:
                    if (sid1, sid2) in mixed_contrasts:
                        print(f"❌ Skipped mixed pair (contrast found): {sid1} vs {sid2}")
                        continue  # ตัดออก
                    else:
                        print(f"✅ Kept mixed pair (no contrast): {sid1} vs {sid2}")
                        valid_pairs.append([sid1, sid2])

        # 6) Fetch raw contrast records
        raw_records = await self.repo.fetch_contrasts(valid_pairs)
        pair_to_data = { (r["sub1_id"], r["sub2_id"]): r for r in raw_records }
        print("pair_to_data :",pair_to_data)

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
