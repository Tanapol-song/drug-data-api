import os
import uuid
import ast
from itertools import combinations, product
from typing import List, Dict, Optional, Generic, TypeVar
from collections import Counter
import re
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field
from neo4j import basic_auth, AsyncGraphDatabase
from dotenv import load_dotenv
from collections import Counter
import asyncio

load_dotenv("api.env")

# --- Configuration ---
NEO4J_URI  = os.getenv("NEO4J_URI_STAGING")
NEO4J_USER = os.getenv("NEO4J_USERNAME_STAGING")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD_STAGING")


driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=basic_auth(NEO4J_USER, NEO4J_PASS), max_connection_pool_size=20)

# # 1) Create an async driver alongside your sync one
# async_driver = AsyncGraphDatabase.driver(
#     NEO4J_URI, auth=basic_auth(NEO4J_USER, NEO4J_PASS)
# )

app = FastAPI(title="Drug Interaction API", version="1.1.1")

# ── 1) SHARED INPUT MODELS ────────────────────────────────────────
class DrugItem(BaseModel):
    tpu_code:  Optional[str] = ""
    tp_code:   Optional[str] = ""
    gpu_code:  Optional[str] = ""
    gp_code:   Optional[str] = ""
    vtm_code:  Optional[str] = ""
    subs_code: Optional[str] = ""
    tpu_name:  Optional[str] = ""
    tp_name:   Optional[str] = ""
    gpu_name:  Optional[str] = ""
    gp_name:   Optional[str] = ""
    vtm_name:  Optional[str] = ""
    subs_name: Optional[str] = ""
    quantity:  Optional[int] = 0
    name: Optional[str] = ""

# ── 2) SEPARATE PAYLOADS ─────────────────────────────────────────
class DrugPayload(BaseModel):
    drug_currents:  List[DrugItem] = Field(..., min_items=1)
    drug_histories: Optional[List[DrugItem]] = []

class AllergyPayload(BaseModel):
    drug_currents:  List[DrugItem] = Field(..., min_items=1)
    drug_histories: Optional[List[DrugItem]] = []
    drug_allergies: List[DrugItem]         = Field(..., min_items=1)

# ── 3) PAGINATION & RESPONSE MODELS ──────────────────────────────
T = TypeVar("T")

class Pagination(BaseModel):
    page:  int = Field(..., ge=1)
    row:   int = Field(..., ge=0)
    total: int = Field(..., ge=0)

class PageResponse(BaseModel, Generic[T]):
    pagination: Pagination
    data:       List[T]

class ContrastItem(BaseModel):
    ref_id:                str
    input_tpu_code:        str
    input_tpu_name:        str
    input_tp_code:         str
    input_tp_name:         str
    input_gpu_code:        str
    input_gpu_name:        str
    input_gp_code:         str
    input_gp_name:         str
    input_vtm_code:        str
    input_vtm_name:        str
    input_description:     str
    contrast_tpu_code:     str
    contrast_tpu_name:     str
    contrast_tp_code:      str
    contrast_tp_name:      str
    contrast_gpu_code:     str
    contrast_gpu_name:     str
    contrast_gp_code:      str
    contrast_gp_name:      str
    contrast_vtm_code:     str
    contrast_vtm_name:     str
    contrast_description:  str
    contrast_type:         int
    interaction_detail_en: str
    interaction_detail_th: str
    onset:                 str
    severity:              str
    documentation:         str
    significance:          str
    management:            str
    discussion:            str
    reference:             str
    input_substances:      List[Dict[str,str]]
    contrast_substances:   List[Dict[str,str]]

class AllergyItem(BaseModel):
    input_tpu_code:    str
    input_tpu_name:    str
    input_tp_code:     str
    input_tp_name:     str
    input_gpu_code:    str
    input_gpu_name:    str
    input_gp_code:     str
    input_gp_name:     str
    input_vtm_code:    str
    input_vtm_name:    str
    input_description: str
    is_allergy:        bool
    allergy_type:      int    = Field(
        ..., ge=0, le=2, description="0=current only, 1=history only, 2=both"
    )
    allergy_substances: List[Dict[str, str]] 


class DrugsResponse(BaseModel):
    status:  bool
    code:    int
    message: str
    data:    PageResponse[ContrastItem]

class AllergyResponse(BaseModel):
    status:       bool
    code:         int
    message:      str
    data: PageResponse[AllergyItem]

# ── 3) CYPHER TEMPLATES (เหมือนเดิม) ───────────────────────────
DRUGSEARCH_CYPHER = """
UNWIND $qs AS q
WITH q WHERE trim(q) <> ""
CALL db.index.fulltext.queryNodes("DrugSearch", q) YIELD node, score
WITH q, node, score,
  CASE
    WHEN node.`TMTID(TPU)` = q OR toLower(node.`TPUNAME`) CONTAINS toLower(q) THEN "TPU"
    WHEN node.`TMTID(TP)`  = q OR toLower(node.`TPNAME`)  CONTAINS toLower(q) THEN "TP"
    WHEN node.`TMTID(GPU)` = q OR toLower(node.`GPUNAME`) CONTAINS toLower(q) THEN "GPU"
    WHEN node.`TMTID(GP)`  = q OR toLower(node.`GPNAME`)  CONTAINS toLower(q) THEN "GP"
    WHEN node.`TMTID(VTM)` = q OR toLower(node.`VTMNAME`) CONTAINS toLower(q) THEN "VTM"
    WHEN node.`TMTID(SUBS)_LIST` CONTAINS q
         OR toLower(node.`SUBSNAME_LIST`) CONTAINS toLower(q) THEN "SUBS"
    ELSE "UNKNOWN"
  END AS level
WITH *
WITH q,
     collect({
        level:level,
        tpu_code:node.`TMTID(TPU)`, tpu_name:node.`TPUNAME`,
        tp_code:node.`TMTID(TP)`,   tp_name:node.`TPNAME`,
        gpu_code:node.`TMTID(GPU)`, gpu_name:node.`GPUNAME`,
        gp_code:node.`TMTID(GP)`,   gp_name:node.`GPNAME`,
        vtm_code:node.`TMTID(VTM)`, vtm_name:node.`VTMNAME`,
        subs_codes:node.`TMTID(SUBS)_LIST`,
        subs_names:node.`SUBSNAME_LIST`,
        score:score
     })[0] AS best
RETURN q AS code, best
"""

RESOLVE_SUBS_FALLBACK = """
UNWIND $codes AS code
MATCH (n)
WHERE   (n:SUBS AND n.`TMTID(SUBS)` = code)
    OR  (n:TPU  AND n.`TMTID(TPU)`  = code)
    OR  (n:TP   AND n.`TMTID(TP)`   = code)
    OR  (n:GPU  AND n.`TMTID(GPU)`  = code)
    OR  (n:GP   AND n.`TMTID(GP)`   = code)
    OR  (n:VTM  AND n.`TMTID(VTM)`  = code)
OPTIONAL MATCH (n)<-[:TP_TO_TPU|GPU_TO_TPU|GP_TO_TP|GP_TO_GPU|VTM_TO_GP|SUBS_TO_VTM*0..5]-(subs:SUBS)
RETURN DISTINCT subs.`TMTID(SUBS)` AS sid
"""

CONTRAST_CYPHER = """
UNWIND $pairs AS p
MATCH (s1:SUBS {`TMTID(SUBS)`: p[0]})-[r:CONTRAST_WITH]-
      (s2:SUBS {`TMTID(SUBS)`: p[1]})
RETURN
  s1.`TMTID(SUBS)` AS sub1_id,
  s1.SUBSNAME      AS sub1_name,
  s2.`TMTID(SUBS)` AS sub2_id,
  s2.SUBSNAME      AS sub2_name,
  COALESCE(r.SEVERITY,"")        AS severity,
  COALESCE(r.DOCUMENTATION,"")   AS documentation,
  COALESCE(r.SUMMARY,"")         AS interaction_detail_en,
  COALESCE(r.SUMMARY_TH,"")      AS interaction_detail_th,
  COALESCE(r.ONSET,"")           AS onset,
  COALESCE(r.SIGNIFICANCE,"")    AS significance,
  COALESCE(r.MANAGEMENT,"")      AS management,
  COALESCE(r.DISCUSSION,"")      AS discussion,
  COALESCE(r.REFERENCE,"")       AS reference
"""

# ── 4) HELPERS ──────────────────────────────────────────────────

LEVELS = ["tpu", "tp", "gpu", "gp", "vtm"]   # (SUBS แสดงใน substances อยู่แล้ว)
CODE_FIELD   = {l: f"{l}_code" for l in LEVELS}
NAME_FIELD   = {l: f"{l}_name" for l in LEVELS}
DETAIL_CODE  = CODE_FIELD          # detail_map ใช้ชื่อเดียวกัน
DETAIL_NAME  = NAME_FIELD
CODE   = {lv: f"{lv}_code" for lv in LEVELS}
NAME   = {lv: f"{lv}_name" for lv in LEVELS}

# ── 5) UTILITY FUNCTIONS ────────────────────────────────────────
async def normalize_query(text: str) -> str:
    if text:
        cleaned_text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
        return cleaned_text
    return text

async def highest_idx(it: DrugItem) -> int:
    for i, lv in enumerate(LEVELS):
        if getattr(it, CODE[lv]):
            return i
    return len(LEVELS)

async def codes_from_item(it: DrugItem) -> List[str]:
    return [c for c in [
        it.tpu_code, it.tp_code, it.gpu_code,
        it.gp_code, it.vtm_code, it.subs_code
    ] if c]


# ─── Neo4j Async Queries ────────────────────────
async def resolve_subs_from_name(tx, names: List[str]) -> Dict[str, str]:
    result = await tx.run(DRUGSEARCH_CYPHER, {"qs": names})
    name_to_subs: Dict[str, List[str]] = {}
    unresolved = set(names)

    async for r in result:
        q = r["code"]
        best = r["best"]
        if best is None:
            continue
        subs_codes = best.get("subs_codes") or []
        if isinstance(subs_codes, str):
            try:
                subs_codes = ast.literal_eval(subs_codes)
            except Exception:
                subs_codes = []
        if subs_codes:
            name_to_subs.setdefault(q, []).extend(subs_codes)
            unresolved.discard(q)

    if unresolved:
        fallback = await tx.run(DRUGSEARCH_CYPHER, {"qs": list(unresolved)})
        async for r in fallback:
            q = r["code"]
            best = r["best"]
            if best is None:
                continue
            subs_codes = best.get("subs_codes") or []
            if isinstance(subs_codes, str):
                try:
                    subs_codes = ast.literal_eval(subs_codes)
                except Exception:
                    subs_codes = []
            if subs_codes:
                name_to_subs.setdefault(q, []).extend(subs_codes)

    final_map: Dict[str, str] = {}
    for name, subs_list in name_to_subs.items():
        if not subs_list:
            continue
        counts = Counter(subs_list)
        most_common = counts.most_common(1)[0][0]
        final_map[name] = most_common
    return final_map

async def query_drug_details(tx, codes: List[str]) -> Dict[str, dict]:
    rows = await tx.run(DRUGSEARCH_CYPHER, {"qs": codes})
    out: Dict[str, dict] = {}
    found_codes = set()

    async for r in rows:
        best = r["best"]
        if best is None:
            continue

        subs_codes = best["subs_codes"] or []
        subs_names = best["subs_names"] or []

        if isinstance(subs_codes, str):
            try:
                subs_codes = ast.literal_eval(subs_codes)
            except Exception:
                subs_codes = []
        if isinstance(subs_names, str):
            try:
                subs_names = ast.literal_eval(subs_names)
            except Exception:
                subs_names = []

        best["subs_codes"] = subs_codes
        best["subs_names"] = subs_names
        out[r["code"]] = best
        found_codes.add(r["code"])

    # 🔁 fallback: resolve SUBS by code match directly
    missing_codes = [code for code in codes if code not in found_codes]
    if missing_codes:
        print(f"[LOG] Fallback resolving for: {missing_codes}")
        fallback_rows = await tx.run(RESOLVE_SUBS_FALLBACK, {"codes": missing_codes})
        async for r in fallback_rows:
            sid = r.get("sid")
            if sid:
                out[sid] = {
                    "subs_codes": [sid],
                    "subs_names": [],
                    "tpu_code": "", "tp_code": "", "gpu_code": "",
                    "gp_code": "", "vtm_code": "",
                    "tpu_name": "", "tp_name": "", "gpu_name": "",
                    "gp_name": "", "vtm_name": ""
                }

    return out

# async def fallback_resolve_subs(tx, codes: List[str]) -> List[str]:
#     return [r["sid"] for r in tx.run(RESOLVE_SUBS_FALLBACK, {"codes": codes})]

async def fallback_resolve_subs(tx, codes: List[str]) -> List[str]:
    result = await tx.run(RESOLVE_SUBS_FALLBACK, {"codes": codes})
    return [r["sid"] async for r in result]

# ─── Enrichment ────────────────────────────────
async def enrich_items(driver, items: List[DrugItem], detail_map: Dict[str, dict]) -> Dict[str, List[DrugItem]]:
    mapping: Dict[str, List[DrugItem]] = {}
    codes_fallback: List[str] = []

    async with driver.session() as session:
        for it in items:
            matched = False

            codes = await codes_from_item(it)  # ✅ await ก่อน
            for code in codes:
                d = detail_map.get(code)
                if not d:
                    continue
                matched = True

                for lv in LEVELS:
                    correct_code = d.get(DETAIL_CODE[lv])
                    if correct_code:
                        setattr(it, CODE_FIELD[lv], correct_code)
                    correct_name = d.get(DETAIL_NAME[lv])
                    if correct_name:
                        setattr(it, NAME_FIELD[lv], correct_name)

                for sid in d["subs_codes"]:
                    mapping.setdefault(sid, []).append(it)

            if not matched:
                codes = await codes_from_item(it)  # ✅ ต้อง await อีกครั้ง
                codes_fallback.extend(codes)

        if codes_fallback:
            subs_ids = await session.execute_read(fallback_resolve_subs, codes_fallback)
            for sid in subs_ids:
                mapping.setdefault(sid, []).append(items[0])  # หรือจะกระจายทั้งหมดก็ได้

    return mapping

# ─── Utilities ────────────────────────────────
def choose_input_contrast(sid1: str, sid2: str, group1: str, group2: str):
    if group1 == "currents" and group2 != "currents":
        return sid1, sid2
    if group2 == "currents" and group1 != "currents":
        return sid2, sid1
    try:
        return (sid1, sid2) if int(sid1) < int(sid2) else (sid2, sid1)
    except ValueError:
        return (sid1, sid2) if sid1 < sid2 else (sid2, sid1)

async def fill_codes(prefix: str, it: DrugItem) -> Dict[str, str]:
    top = await highest_idx(it)
    out = {}
    for i, lv in enumerate(LEVELS):
        out[f"{prefix}_{lv}_code"] = getattr(it, CODE_FIELD[lv]) if i >= top else ""
        out[f"{prefix}_{lv}_name"] = getattr(it, NAME_FIELD[lv]) if i >= top else ""
    out[f"{prefix}_description"] = ""
    return out

@app.post(
    "/api/v1/drugs",
    response_model=DrugsResponse,
    summary="Get drug interaction contrasts"
)
async def get_interactions(
    payload: DrugPayload,
    page: int = Query(1, ge=1, description="Page number, default=1"),
    row:  int = Query(10, ge=1, description="Items per page, default=10"),
):
    names_to_resolve = []
    for it in payload.drug_histories:
        if not any(await codes_from_item(it)):
            search_name = it.name or it.tpu_name or it.tp_name or it.gpu_name or it.gp_name or it.vtm_name
            if search_name:
                search_name = await normalize_query(search_name)
                print(f"[LOG] Resolving name: {search_name}")
                names_to_resolve.append(search_name)
                it.name = search_name

    print(f"[LOG] names to resolve: {names_to_resolve}")

    if names_to_resolve:
        async with driver.session() as s:
            name_to_subs = await s.execute_read(resolve_subs_from_name, names_to_resolve)
        print(f"[LOG] resolved names to subs: {name_to_subs}")
        for it in payload.drug_histories:
            if it.name in name_to_subs:
                it.subs_code = name_to_subs[it.name]

    curr_codes = set()
    for it in payload.drug_currents:
        codes = await codes_from_item(it)
        curr_codes.update(codes)

    hist_codes = set()
    for it in payload.drug_histories:
        codes = await codes_from_item(it)
        hist_codes.update(codes)

    print(f"[LOG] raw currents codes:  {sorted(curr_codes)}")
    print(f"[LOG] raw histories codes: {sorted(hist_codes)}")

    all_codes = curr_codes | hist_codes
    async with driver.session() as s:
        detail_map = await s.execute_read(query_drug_details, list(all_codes))

    current_map = await enrich_items(driver, payload.drug_currents, detail_map)
    history_map = await enrich_items(driver, payload.drug_histories, detail_map)

    subs_to_items: Dict[str, List[DrugItem]] = {}
    for group in (payload.drug_currents, payload.drug_histories):
        for it in group:
            codes = await codes_from_item(it)
            for code in codes:
                d = detail_map.get(code)
                if d and d.get("subs_codes"):
                    for sid in d["subs_codes"]:
                        subs_to_items.setdefault(sid, []).append(it)
                    break

    unique_sids = sorted(subs_to_items.keys())
    jobs = []
    for sid1, sid2 in combinations(unique_sids, 2):
        items1, items2 = subs_to_items[sid1], subs_to_items[sid2]
        for in_item in items1:
            for ct_item in items2:
                jobs.append({"pair": [sid1, sid2], "in_item": in_item, "ct_item": ct_item})

    print(f"[LOG] jobs count: {len(jobs)} pairs: {[j['pair'] for j in jobs]}")

    unique_pairs = []
    seen = set()
    for j in jobs:
        p = tuple(j["pair"])
        if p not in seen:
            seen.add(p)
            unique_pairs.append(p)

    async with driver.session() as sess:
        result = await sess.run(CONTRAST_CYPHER, {"pairs": [list(p) for p in unique_pairs]})
        records = [r async for r in result]

    pair_to_data = {}
    for rec in records:
        d = rec.data()
        pair_to_data[(d["sub1_id"], d["sub2_id"])] = d

    rows: List[ContrastItem] = []
    for job in jobs:
        sid1, sid2 = job["pair"]
        d = pair_to_data.get((sid1, sid2)) or pair_to_data.get((sid2, sid1))
        if not d:
            continue
        rows.append(ContrastItem(
            ref_id=str(uuid.uuid4()),
            **await fill_codes("input", job["in_item"]),
            **await fill_codes("contrast", job["ct_item"]),
            interaction_detail_en=d["interaction_detail_en"],
            interaction_detail_th=d["interaction_detail_th"],
            onset=d["onset"],
            severity=d["severity"],
            documentation=d["documentation"],
            significance=d["significance"],
            management=d["management"],
            discussion=d["discussion"],
            reference=d["reference"],
            input_substances=[{"code": d["sub1_id"], "name": d["sub1_name"]}],
            contrast_substances=[{"code": d["sub2_id"], "name": d["sub2_name"]}],
            contrast_type=0
        ))

    total = len(rows)
    start = (page - 1) * row
    end = start + row
    page_rs = rows[start:end]

    return DrugsResponse(
        status=True,
        code=200,
        message="get success",
        data=PageResponse(
            pagination=Pagination(page=page, row=len(page_rs), total=total),
            data=page_rs
        )
    )


# ── 6) /api/v1/allergy ────────────────────────────────────────────────────
@app.post("/api/v1/allergy", response_model=AllergyResponse, summary="Get allergy summary")
async def get_allergy(
    payload: AllergyPayload,
    page: int = Query(1, ge=1, description="Page number"),
    row: int = Query(10, ge=1, description="Items per page"),
):
    # Cypher query for resolving subs
    SEARCHSUBS_CYPHER = """ 
UNWIND $codes AS code
MATCH (n)
  WHERE (n:TPU  AND n.`TMTID(TPU)`  = code)
     OR (n:TP   AND n.`TMTID(TP)`   = code)
     OR (n:GPU  AND n.`TMTID(GPU)`  = code)
     OR (n:GP   AND n.`TMTID(GP)`   = code)
     OR (n:VTM  AND n.`TMTID(VTM)`  = code)
     OR (n:SUBS AND n.`TMTID(SUBS)` = code)
OPTIONAL MATCH (n)<-[:TP_TO_TPU|GPU_TO_TPU|GP_TO_TP|GP_TO_GPU|VTM_TO_GP|SUBS_TO_VTM*0..5]-(subs:SUBS)
WITH code, collect(DISTINCT COALESCE(subs.`TMTID(SUBS)`, n.`TMTID(SUBS)`)) AS subs_ids
RETURN code AS raw_code, subs_ids
    """

    async def resolve_to_subs(tx, codes: List[str]) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        result = await tx.run(SEARCHSUBS_CYPHER, {"codes": codes})
        async for record in result:
            raw = record["raw_code"]
            subs_list = [s for s in record["subs_ids"] if s is not None]
            mapping[raw] = subs_list
        for c in codes:
            mapping.setdefault(c, [])
        return mapping

    # Cache codes_from_item
    item_code_cache = {}

    async def get_cached_codes(it):
        key = id(it)
        if key not in item_code_cache:
            item_code_cache[key] = await codes_from_item(it)
        return item_code_cache[key]

    # Normalize and resolve names
    # names_to_resolve = []
    # for it in payload.drug_histories + payload.drug_allergies:
    #     if not any(await get_cached_codes(it)) and it.name:
    #         it.name = await normalize_query(it.name)
    #         names_to_resolve.append(it.name)
    
    # Step 1: เตรียมชื่อที่ยังไม่มี code เพื่อนำไป resolve
    names_to_resolve = []
    for it in payload.drug_histories + payload.drug_allergies:
        if not any(await get_cached_codes(it)) and it.name:
            it.name = await normalize_query(it.name)
            names_to_resolve.append(it.name)

    print(f"[LOG] names to resolve: {names_to_resolve}")

    # Step 2: resolve subs จากชื่อ
    name_to_subs = {}
    if names_to_resolve:
        async with driver.session() as s:
            name_to_subs = await s.execute_read(resolve_subs_from_name, names_to_resolve)
        print(f"[LOG] resolved names to subs: {name_to_subs}")

    # Step 3: assign subs_code และ clear cache
    for it in payload.drug_histories + payload.drug_allergies:
        if it.name in name_to_subs:
            it.subs_code = name_to_subs[it.name]
            item_code_cache.pop(id(it), None)  # ✅ refresh cache to reflect new subs_code

    print(f"[LOG] names to resolve: {names_to_resolve}")
    if names_to_resolve:
        async with driver.session() as s:
            name_to_subs = await s.execute_read(resolve_subs_from_name, names_to_resolve)
        print(f"[LOG] resolved names to subs: {name_to_subs}")
        for it in payload.drug_histories + payload.drug_allergies:
            if it.name in name_to_subs:
                it.subs_code = name_to_subs[it.name]
                

    # Collect all codes
    all_items = payload.drug_currents + payload.drug_histories + payload.drug_allergies
    curr_codes = {code for it in payload.drug_currents for code in await get_cached_codes(it)}
    hist_codes = {code for it in payload.drug_histories for code in await get_cached_codes(it)}
    allergy_codes = {code for it in payload.drug_allergies for code in await get_cached_codes(it)}
    all_codes = curr_codes | hist_codes | allergy_codes

    print(f"[LOG] raw currents codes: {sorted(curr_codes)}")
    print(f"[LOG] raw histories codes: {sorted(hist_codes)}")
    print(f"[LOG] raw allergies codes: {sorted(allergy_codes)}")

    # Run Neo4j queries in parallel
    async with driver.session() as sess1, driver.session() as sess2:
        subs_map, detail_map = await asyncio.gather(
            sess1.execute_read(resolve_to_subs, list(all_codes)),
            sess2.execute_read(query_drug_details, list(all_codes))
        )


    # Separate subs
    subs_curr_set = {s for c in curr_codes for s in subs_map.get(c, [])}
    subs_hist_set = {s for c in hist_codes for s in subs_map.get(c, [])}
    subs_allergy_set = {s for c in allergy_codes for s in subs_map.get(c, [])}

    print(f"[LOG] subs currents IDs: {sorted(subs_curr_set)}")
    print(f"[LOG] subs histories IDs: {sorted(subs_hist_set)}")
    print(f"[LOG] subs allergies IDs: {sorted(subs_allergy_set)}")

    # Enrich
    current_map = await enrich_items(driver, payload.drug_currents, detail_map)
    history_map = await enrich_items(driver, payload.drug_histories, detail_map)
    allergy_map = await enrich_items(driver, payload.drug_allergies, detail_map)
    
    
   # 1) fetch the SUBSNAME map once
    SUBS_NAME_CYPHER = """
    UNWIND $subs_ids AS sid
    MATCH (s:SUBS {`TMTID(SUBS)`: sid})
    RETURN sid AS code, s.SUBSNAME AS name
    """
    async with driver.session() as sess:
        result = await sess.run(SUBS_NAME_CYPHER, {"subs_ids": list(subs_allergy_set)})
        subs_name_map = {record["code"]: record["name"] async for record in result}

    # 2) Build response
    allergy_rows: List[AllergyItem] = []
    combined_map = {**current_map, **history_map}

    # Use an ordered dict to preserve insertion order, keyed by the object id of itm
    from collections import OrderedDict
    grouped: "OrderedDict[int, dict]" = OrderedDict()

    for subs_id, itm_list in combined_map.items():
        # only care about true allergies
        if subs_id not in subs_allergy_set:
            continue

        for itm in (itm_list if isinstance(itm_list, list) else [itm_list]):
            key = id(itm)

            # if first time seeing this itm, initialize its data
            if key not in grouped:
                data = await fill_codes("input", itm)
                in_curr = key in {id(x) for x in payload.drug_currents}
                in_hist = key in {id(x) for x in payload.drug_histories}
                data["is_allergy"]   = True
                data["allergy_type"] = 2 if (in_curr and in_hist) else (0 if in_curr else 1)
                data["allergy_substances"] = []
                grouped[key] = data

            # append this subs to its allergy_substances
            grouped[key]["allergy_substances"].append({
                "code": subs_id,
                "name": subs_name_map.get(subs_id, "")
            })

    # finally, build the list of AllergyItem from grouped values
    allergy_rows = [AllergyItem(**vals) for vals in grouped.values()]

    print(f"[LOG] allergy summary rows: {len(allergy_rows)} items")
    allergy_rows = [row for row in allergy_rows if row.is_allergy]
    print(f"[LOG] filtered allergy rows (only True): {len(allergy_rows)} items")

    # Paginate
    total = len(allergy_rows)
    start = (page - 1) * row
    end = start + row
    page_rows = allergy_rows[start:end]

    return AllergyResponse(
        status=True,
        code=200,
        message="get success",
        data=PageResponse(
            pagination=Pagination(page=page, row=len(page_rows), total=total),
            data=page_rows
        )
    )
