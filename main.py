import os
import uuid
import ast
from itertools import combinations, product
from typing import List, Dict, Optional, Generic, TypeVar
from collections import Counter
import re
from fastapi import FastAPI, Query
from pydantic import BaseModel, Field
from neo4j import GraphDatabase, basic_auth
from dotenv import load_dotenv


load_dotenv("api.env")

# --- Configuration ---
NEO4J_URI  = os.getenv("NEO4J_URI_STAGING")
NEO4J_USER = os.getenv("NEO4J_USERNAME_STAGING")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD_STAGING")


driver = GraphDatabase.driver(NEO4J_URI, auth=basic_auth(NEO4J_USER, NEO4J_PASS), max_connection_pool_size=20)

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


class DrugsResponse(BaseModel):
    status:  bool
    code:    int
    message: str
    data:    PageResponse[ContrastItem]

class AllergyResponse(BaseModel):
    status:       bool
    code:         int
    message:      str
    data_allergy: PageResponse[AllergyItem]

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
         OR toLower(node.`SUBSNAME_LIST`) CONTAINS toLower(q)              THEN "SUBS"
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


# def normalize_query(text: str) -> str:
#     """เตรียม string ให้เหมาะกับ full-text search
#        - เปลี่ยน / เป็น เว้นวรรค
#        - ใส่ช่องว่างก่อนหน่วย (mg, mL, g, mcg) เผื่อไม่มี space
#     """
#     if not text:
#         return text
#     text = re.sub(r'/', ' ', text)
#     text = re.sub(r'(\d)(mg|mL|mcg|g)', r'\1 \2', text, flags=re.I)
#     return ' '.join(text.split())      # เก็บ space เดียว

def normalize_query(text):
    if text:
        # เอาเฉพาะ a-z, A-Z, 0-9 และช่องว่าง
        cleaned_text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
        # ถ้าอยากตัดช่องว่างซ้ำให้เหลือช่องว่างเดียว
        cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
        return cleaned_text
    return text

def highest_idx(it: DrugItem) -> int:
    for i, lv in enumerate(LEVELS):
        if getattr(it, CODE[lv]):
            return i
    return len(LEVELS)  # ไม่มี code เลย (SUBS เท่านั้น)

def codes_from_item(it: DrugItem) -> List[str]:
    return [c for c in [
        it.tpu_code, it.tp_code, it.gpu_code,
        it.gp_code, it.vtm_code, it.subs_code
    ] if c]

# def resolve_subs_from_name(tx, names: List[str]) -> Dict[str, str]:
#     rows = tx.run(DRUGSEARCH_CYPHER, {"qs": names})
#     name_to_subs: Dict[str, List[str]] = {}
#     for r in rows:
#         q = r["code"]
#         best = r["best"]
#         if best is None:
#             continue
#         subs_codes = best["subs_codes"] or []
#         if isinstance(subs_codes, str):
#             try:
#                 subs_codes = ast.literal_eval(subs_codes)
#             except Exception:
#                 subs_codes = []
#         name_to_subs.setdefault(q, []).extend(subs_codes)

#     # pick most common subs per name
#     final_map: Dict[str, str] = {}
#     for name, subs_list in name_to_subs.items():
#         if not subs_list:
#             continue
#         counts = Counter(subs_list)
#         most_common = counts.most_common(1)[0][0]
#         final_map[name] = most_common
#     return final_map
def resolve_subs_from_name(tx, names: List[str]) -> Dict[str, str]:
    rows = tx.run(DRUGSEARCH_CYPHER, {"qs": names})
    name_to_subs: Dict[str, List[str]] = {}
    unresolved = set(names)

    for r in rows:
        q = r["code"]
        best = r["best"]
        if best is None:
            continue
        subs_codes = best["subs_codes"] or []
        if isinstance(subs_codes, str):
            try:
                subs_codes = ast.literal_eval(subs_codes)
            except Exception:
                subs_codes = []
        if subs_codes:
            name_to_subs.setdefault(q, []).extend(subs_codes)
            unresolved.discard(q)

    # fallback fulltext no-record match if not resolved
    if unresolved:
        fallback_rows = tx.run(DRUGSEARCH_CYPHER, {"qs": list(unresolved)})
        for r in fallback_rows:
            q = r["code"]
            best = r["best"]
            if best is None:
                continue
            subs_codes = best["subs_codes"] or []
            if isinstance(subs_codes, str):
                try:
                    subs_codes = ast.literal_eval(subs_codes)
                except Exception:
                    subs_codes = []
            if subs_codes:
                name_to_subs.setdefault(q, []).extend(subs_codes)

    # pick most common subs per name
    final_map: Dict[str, str] = {}
    for name, subs_list in name_to_subs.items():
        if not subs_list:
            continue
        counts = Counter(subs_list)
        most_common = counts.most_common(1)[0][0]
        final_map[name] = most_common
    return final_map

def query_drug_details(tx, codes: List[str]) -> Dict[str, dict]:
    rows = tx.run(DRUGSEARCH_CYPHER, {"qs": codes})
    out: Dict[str, dict] = {}
    for r in rows:
        best = r["best"]
        if best is None:
            continue
        # แปลง SUBS list ถ้าเก็บเป็นสตริง
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
    return out


def fallback_resolve_subs(tx, codes: List[str]) -> List[str]:
    return [r["sid"] for r in tx.run(RESOLVE_SUBS_FALLBACK, {"codes": codes})]


def enrich_items(items: List[DrugItem], detail_map: Dict[str, dict]) -> Dict[str, DrugItem]:
    """
    • เติม code / name ทุก level ให้ครบที่สุด
    • คืน mapping  SUBS-id → DrugItem  สำหรับใช้ประกอบผลลัพธ์
    """
    mapping: Dict[str, DrugItem] = {}
    codes_fallback: List[str] = []

    with driver.session() as sess:
        for it in items:
            matched = False
            # --- ❶ loop code ทุกตัวที่มี ไม่สนว่า it.name ว่างหรือไม่ ---
            for code in codes_from_item(it):
                d = detail_map.get(code)
                if not d:
                    continue
                matched = True

                # --- ❷ เติม code / name ครบทั้ง 5 level (TPU..VTM) ---
                for lv in LEVELS:
                    if not getattr(it, CODE_FIELD[lv]) and d.get(DETAIL_CODE[lv]):
                        setattr(it, CODE_FIELD[lv], d[DETAIL_CODE[lv]])
                    if not getattr(it, NAME_FIELD[lv]) and d.get(DETAIL_NAME[lv]):
                        setattr(it, NAME_FIELD[lv], d[DETAIL_NAME[lv]])

                # map SUBS → DrugItem
                for sid in d["subs_codes"]:
                    mapping.setdefault(sid, it)

            # --- ❸ ถ้าไม่ match เลยเก็บไว้ fallback หา SUBS ---
            if not matched:
                codes_fallback.extend(codes_from_item(it))

        # -- Fallback SUBS mapping (เหมือนเดิม) --
        if codes_fallback:
            subs_ids = sess.read_transaction(fallback_resolve_subs, codes_fallback)
            for sid in subs_ids:
                mapping.setdefault(sid, items[0])

    return mapping

def choose_input_contrast(sid1: str, sid2: str, group1: str, group2: str):
    if group1 == "currents" and group2 != "currents":
        return sid1, sid2
    if group2 == "currents" and group1 != "currents":
        return sid2, sid1
    # กรณีที่กลุ่มเท่ากัน → เอา sid ที่มีเลขน้อยกว่าเป็น input
    try:
        return (sid1, sid2) if int(sid1) < int(sid2) else (sid2, sid1)
    except ValueError:
        return (sid1, sid2) if sid1 < sid2 else (sid2, sid1)


def fill_codes(prefix: str, it: DrugItem) -> Dict[str, str]:
    top = highest_idx(it)
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
def get_interactions(
    payload: DrugPayload,
    page: int = Query(1, ge=1, description="Page number, default=1"),
    row:  int = Query(10, ge=1, description="Items per page, default=10"),
):
    # 1) Name resolution for histories only
    names_to_resolve = []
    for it in payload.drug_histories:
        if not any(codes_from_item(it)) and it.name:
            it.name = normalize_query(it.name)
            names_to_resolve.append(it.name)
    print(f"[LOG] names to resolve:       {names_to_resolve}")

    if names_to_resolve:
        with driver.session() as s:
            name_to_subs = s.read_transaction(resolve_subs_from_name, names_to_resolve)
        print(f"[LOG] resolved names to subs:  {name_to_subs}")
        for it in payload.drug_histories:
            if it.name in name_to_subs:
                it.subs_code = name_to_subs[it.name]

    # 2) Build code‐sets from currents + histories
    curr_codes = {c for it in payload.drug_currents  for c in codes_from_item(it)}
    hist_codes = {c for it in payload.drug_histories for c in codes_from_item(it)}
    print(f"[LOG] raw currents codes:    {sorted(curr_codes)}")
    print(f"[LOG] raw histories codes:    {sorted(hist_codes)}")

    # 3) Query & enrich
    all_codes = curr_codes | hist_codes
    with driver.session() as s:
        detail_map = s.read_transaction(query_drug_details, list(all_codes))
    current_map = enrich_items(payload.drug_currents,  detail_map)
    history_map = enrich_items(payload.drug_histories, detail_map)

    # 4) Origin map
    subs_origin = {sid: "currents" for sid in current_map}
    subs_origin.update({sid: "histories" for sid in history_map})
    
    # 5) Build contrast pairs
    pairs_cc = [(a,b,0) for a,b in combinations(sorted(curr_codes),2)]
    pairs_ch = [(a,b,1) for a,b in product(sorted(curr_codes),sorted(hist_codes)) if a!=b]
    pairs_hh = [(a,b,2) for a,b in combinations(sorted(hist_codes),2)]
    print(f"[LOG] current count:           {len(curr_codes)}")
    print(f"[LOG] history count:           {len(hist_codes)}")
    print(f"[LOG] current × history pairs: {len(pairs_ch)}")

    # 6) Query each batch
    contrast_data = []
    with driver.session() as s:
        for batch in (pairs_cc, pairs_ch, pairs_hh):
            rows = s.run(CONTRAST_CYPHER, {"pairs":[ [x,y] for x,y,_ in batch ]})
            for rec, (_,_,ctype) in zip(rows, batch):
                d = rec.data()
                d["contrast_type"] = ctype
                contrast_data.append(d)

    # 7) Build response rows
    rows = []
    for rec in contrast_data:
        sid1, sid2 = rec["sub1_id"], rec["sub2_id"]
        grp1, grp2 = subs_origin[sid1], subs_origin[sid2]
        in_id, ct_id = choose_input_contrast(sid1,sid2,grp1,grp2)
        in_item = current_map.get(in_id) or history_map.get(in_id)
        ct_item = current_map.get(ct_id) or history_map.get(ct_id)
        in_name = rec["sub1_name"] if in_id==rec["sub1_id"] else rec["sub2_name"]
        ct_name = rec["sub2_name"] if in_id==rec["sub1_id"] else rec["sub1_name"]

        rows.append(ContrastItem(
            ref_id=str(uuid.uuid4()),
            **fill_codes("input",in_item),
            **fill_codes("contrast",ct_item),
            contrast_type=rec["contrast_type"],
            interaction_detail_en=rec["interaction_detail_en"],
            interaction_detail_th=rec["interaction_detail_th"],
            onset=rec["onset"],
            severity=rec["severity"],
            documentation=rec["documentation"],
            significance=rec["significance"],
            management=rec["management"],
            discussion=rec["discussion"],
            reference=rec["reference"],
            input_substances=[{"code":in_id,"name":in_name}],
            contrast_substances=[{"code":ct_id,"name":ct_name}],
        ) )
    total = len(rows)
    start = (page - 1) * row
    end   = start + row
    page_rs = rows[start:end]
    
    return DrugsResponse(
        status=True,
        code=200,
        message="get success",
        data=PageResponse(
            pagination=Pagination(page=page, row=len(page_rs), total=total),
            data= page_rs
        )
    )

# ── 6) /api/v1/allergy ────────────────────────────────────────────
@app.post(
    "/api/v1/allergy",
    response_model=AllergyResponse,
    summary="Get allergy summary"
)
def get_allergy(
    payload: AllergyPayload,
    page:     int = Query(1, ge=1, description="Page number"),
    row:      int = Query(10, ge=1, description="Items per page"),
):
    # 0) Cypher to resolve any level code up to all SUBS ancestors
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

    def resolve_to_subs(tx, codes: List[str]) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        for record in tx.run(SEARCHSUBS_CYPHER, {"codes": codes}):
            raw = record["raw_code"]
            subs_list = [s for s in record["subs_ids"] if s is not None]
            mapping[raw] = subs_list
        for c in codes:
            mapping.setdefault(c, [])
        return mapping

    # 1) Resolve names → subs_code on history & allergy items
    names_to_resolve = []
    for it in payload.drug_histories + payload.drug_allergies:
        if not any(codes_from_item(it)) and it.name:
            it.name = normalize_query(it.name)
            names_to_resolve.append(it.name)
    print(f"[LOG] names to resolve:       {names_to_resolve}")

    if names_to_resolve:
        with driver.session() as s:
            name_to_subs = s.read_transaction(resolve_subs_from_name, names_to_resolve)
        print(f"[LOG] resolved names to subs:  {name_to_subs}")
        for it in payload.drug_histories + payload.drug_allergies:
            if it.name in name_to_subs:
                it.subs_code = name_to_subs[it.name]

    # 2) Raw code‐sets (could be any level)
    curr_codes    = {c for it in payload.drug_currents  for c in codes_from_item(it)}
    hist_codes    = {c for it in payload.drug_histories for c in codes_from_item(it)}
    allergy_codes = {c for it in payload.drug_allergies  for c in codes_from_item(it)}
    print(f"[LOG] raw currents codes:     {sorted(curr_codes)}")
    print(f"[LOG] raw histories codes:    {sorted(hist_codes)}")
    print(f"[LOG] raw allergies codes:    {sorted(allergy_codes)}")

    # 2.1) Resolve each raw code → all its SUBS IDs
    with driver.session() as sess:
        subs_curr_map    = sess.read_transaction(resolve_to_subs, list(curr_codes))
        subs_hist_map    = sess.read_transaction(resolve_to_subs, list(hist_codes))
        subs_allergy_map = sess.read_transaction(resolve_to_subs, list(allergy_codes))

    print("[LOG] code → subs (currents):")
    for code, lst in subs_curr_map.items():
        print(f"   {code} → {lst!r}")
    print("[LOG] code → subs (histories):")
    for code, lst in subs_hist_map.items():
        print(f"   {code} → {lst!r}")
    print("[LOG] code → subs (allergies):")
    for code, lst in subs_allergy_map.items():
        print(f"   {code} → {lst!r}")

    # flatten sets of resolved SUBS IDs
    subs_curr_set    = {s for lst in subs_curr_map.values()    for s in lst}
    subs_hist_set    = {s for lst in subs_hist_map.values()    for s in lst}
    subs_allergy_set = {s for lst in subs_allergy_map.values() for s in lst}
    print(f"[LOG] subs currents IDs:      {sorted(subs_curr_set)}")
    print(f"[LOG] subs histories IDs:     {sorted(subs_hist_set)}")
    print(f"[LOG] subs allergies IDs:     {sorted(subs_allergy_set)}")

    # 3) Enrich details for all raw codes
    all_codes = curr_codes | hist_codes | allergy_codes
    with driver.session() as s:
        detail_map = s.read_transaction(query_drug_details, list(all_codes))
    current_map = enrich_items(payload.drug_currents,  detail_map)
    history_map = enrich_items(payload.drug_histories, detail_map)
    allergy_map = enrich_items(payload.drug_allergies, detail_map)

    # 4) Build full AllergyItem list by SUBS ID
    allergy_rows: List[AllergyItem] = []
    all_subs_ids = set(current_map) | set(history_map)
    for subs_id in sorted(all_subs_ids):
        itm = current_map.get(subs_id) or history_map.get(subs_id)
        data = fill_codes("input", itm)

        in_curr    = subs_id in subs_curr_set
        in_hist    = subs_id in subs_hist_set
        is_allergy = subs_id in subs_allergy_set

        data["is_allergy"]   = is_allergy
        data["allergy_type"] = 2 if (in_curr and in_hist) else (0 if in_curr else 1)

        allergy_rows.append(AllergyItem(**data))

    print(f"[LOG] allergy summary rows:   {len(allergy_rows)} items")

    # 5) Paginate
    total = len(allergy_rows)
    start = (page - 1) * row
    end   = start + row
    page_rows = allergy_rows[start:end]

    return AllergyResponse(
        status=True,
        code=200,
        message="get success",
        data_allergy=PageResponse(
            pagination=Pagination(page=page, row=len(page_rows), total=total),
            data=page_rows
        )
    )