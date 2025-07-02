import os
import uuid
import ast
from itertools import combinations
from typing import List, Dict, Optional
from collections import Counter
import re
from fastapi import FastAPI, HTTPException  # <-- ไม่มี Header แล้ว
from pydantic import BaseModel, Field
from neo4j import GraphDatabase, basic_auth
from dotenv import load_dotenv


load_dotenv("api.env")

# --- Configuration ---
NEO4J_URI = os.getenv("NEO4J_URI_STAGING")
NEO4J_USER = os.getenv("NEO4J_USERNAME_STAGING")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD_STAGING")


driver = GraphDatabase.driver(
    NEO4J_URI, auth=basic_auth(NEO4J_USER, NEO4J_PASS), max_connection_pool_size=20
)

app = FastAPI(title="Drug Interaction API", version="1.1.1")


# ── 2) INPUT MODELS ─────────────────────────────────────────────
class DrugItem(BaseModel):
    tpu_code: Optional[str] = ""
    tp_code: Optional[str] = ""
    gpu_code: Optional[str] = ""
    gp_code: Optional[str] = ""
    vtm_code: Optional[str] = ""
    subs_code: Optional[str] = ""

    tpu_name: Optional[str] = ""
    tp_name: Optional[str] = ""
    gpu_name: Optional[str] = ""
    gp_name: Optional[str] = ""
    vtm_name: Optional[str] = ""
    subs_name: Optional[str] = ""

    quantity: Optional[int] = 0
    name: Optional[str] = ""  # Add name for matching


class DrugPayload(BaseModel):
    drug_currents: List[DrugItem] = Field(..., min_items=1)
    drug_histories: Optional[List[DrugItem]] = []
    drug_allergies: Optional[List[DrugItem]] = []


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

LEVELS = ["tpu", "tp", "gpu", "gp", "vtm"]  # (SUBS แสดงใน substances อยู่แล้ว)
CODE_FIELD = {l: f"{l}_code" for l in LEVELS}
NAME_FIELD = {l: f"{l}_name" for l in LEVELS}
DETAIL_CODE = CODE_FIELD  # detail_map ใช้ชื่อเดียวกัน
DETAIL_NAME = NAME_FIELD
CODE = {lv: f"{lv}_code" for lv in LEVELS}
NAME = {lv: f"{lv}_name" for lv in LEVELS}


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
        cleaned_text = re.sub(r"[^a-zA-Z0-9\s]", "", text)
        # ถ้าอยากตัดช่องว่างซ้ำให้เหลือช่องว่างเดียว
        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
        return cleaned_text
    return text


def highest_idx(it: DrugItem) -> int:
    for i, lv in enumerate(LEVELS):
        if getattr(it, CODE[lv]):
            return i
    return len(LEVELS)  # ไม่มี code เลย (SUBS เท่านั้น)


def codes_from_item(it: DrugItem) -> List[str]:
    return [
        c
        for c in [
            it.tpu_code,
            it.tp_code,
            it.gpu_code,
            it.gp_code,
            it.vtm_code,
            it.subs_code,
        ]
        if c
    ]


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


def enrich_items(
    items: List[DrugItem], detail_map: Dict[str, dict]
) -> Dict[str, DrugItem]:
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


# ── 5) ENDPOINT ────────────────────────────────────────────────
@app.post("/api/v1/drugs")
def get_interactions(payload: DrugPayload):
    # 1) Handle name resolution for histories and allergies
    names_to_resolve: List[str] = []
    hist_name_map, allergy_name_map = {}, {}

    for it in payload.drug_histories:
        if not any(codes_from_item(it)) and it.name:
            it.name = normalize_query(it.name)  # ✅ Clean name
            names_to_resolve.append(it.name)
            hist_name_map[it.name] = it

    for it in payload.drug_allergies:
        if not any(codes_from_item(it)) and it.name:
            it.name = normalize_query(it.name)  # ✅ Clean name
            names_to_resolve.append(it.name)
            allergy_name_map[it.name] = it

    print(f"Names to resolve for histories and allergies: {names_to_resolve}")  # log

    if names_to_resolve:
        with driver.session() as s:
            name_to_subs = s.read_transaction(resolve_subs_from_name, names_to_resolve)
            print(f"Resolved names to subs: {name_to_subs}")  # log

        for n, sid in name_to_subs.items():
            if n in hist_name_map:
                hist_name_map[n].subs_code = sid
            if n in allergy_name_map:
                allergy_name_map[n].subs_code = sid

    # 2) Collect all drug codes (after filling subs_code)
    all_codes = {
        c
        for grp in [
            payload.drug_currents,
            payload.drug_histories,
            payload.drug_allergies,
        ]
        for item in grp
        for c in codes_from_item(item)
    }
    print(f"All drug codes collected: {all_codes}")  # log

    with driver.session() as s:
        detail_map = s.read_transaction(query_drug_details, list(all_codes))
        # print(f"Detail map retrieved: {detail_map}")  # log

    # 3) Enrich drug items with the detail map and map substances
    current_map = enrich_items(payload.drug_currents, detail_map)
    history_map = enrich_items(payload.drug_histories, detail_map)
    allergy_map = enrich_items(payload.drug_allergies, detail_map)

    # print(f"Enriched current drugs: {current_map}")  # log
    # print(f"Enriched history drugs: {history_map}")  # log
    # print(f"Enriched allergy drugs: {allergy_map}")  # log

    # Identify which group the substance comes from
    subs_origin = {sid: "currents" for sid in current_map}
    subs_origin.update(
        {sid: "histories" for sid in history_map if sid not in subs_origin}
    )
    subs_origin.update(
        {sid: "allergies" for sid in allergy_map if sid not in subs_origin}
    )

    # 4) Prepare to generate interaction pairs
    all_subs = sorted(subs_origin)
    pairs = [list(p) for p in combinations(all_subs, 2)]

    # No pairs to process
    if not pairs:
        return {
            "status": True,
            "code": 200,
            "message": "get success",
            "data": {"pagination": {"page": 1, "row": 0, "total": 0}, "data": []},
        }

    # 5) Query for contrast details from the database
    with driver.session() as s:
        contrast_rows = [r.data() for r in s.run(CONTRAST_CYPHER, {"pairs": pairs})]

    rows = []
    for rec in contrast_rows:
        sid1, sid2 = rec["sub1_id"], rec["sub2_id"]
        s1_grp, s2_grp = subs_origin[sid1], subs_origin[sid2]
        in_id, ct_id = choose_input_contrast(sid1, sid2, s1_grp, s2_grp)

        if in_id == rec["sub1_id"]:
            in_name = rec["sub1_name"]
            ct_name = rec["sub2_name"]
        else:
            in_name = rec["sub2_name"]
            ct_name = rec["sub1_name"]

        in_item = (
            current_map.get(in_id)
            or history_map.get(in_id)
            or allergy_map.get(in_id)
            or DrugItem()
        )
        ct_item = (
            current_map.get(ct_id)
            or history_map.get(ct_id)
            or allergy_map.get(ct_id)
            or DrugItem()
        )

        print(f"Found contrast: {in_name} vs {ct_name}")  # log

        rows.append(
            {
                "ref_id": str(uuid.uuid4()),
                **fill_codes("input", in_item),
                **fill_codes("contrast", ct_item),
                "interaction_detail_en": rec["interaction_detail_en"],
                "interaction_detail_th": rec["interaction_detail_th"],
                "onset": rec["onset"],
                "severity": rec["severity"],
                "documentation": rec["documentation"],
                "significance": rec["significance"],
                "management": rec["management"],
                "discussion": rec["discussion"],
                "reference": rec["reference"],
                "input_substances": [{"code": in_id, "name": in_name}],
                "contrast_substances": [{"code": ct_id, "name": ct_name}],
            }
        )

    return {
        "status": True,
        "code": 200,
        "message": "get success",
        "data": {
            "pagination": {"page": 1, "row": len(rows), "total": len(rows)},
            "data": rows,
        },
    }
