# File: domain/models.py

from typing import List, Dict, Optional, Generic, TypeVar
from pydantic import BaseModel, Field

T = TypeVar("T")

class DrugItem(BaseModel):
    source: Optional[str] = None
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
    name:      Optional[str] = ""

class DrugPayload(BaseModel):
    drug_currents:  List[DrugItem] = Field(..., min_items=1)
    drug_histories: Optional[List[DrugItem]] = []

class AllergyPayload(BaseModel):
    drug_currents:   List[DrugItem] = Field(..., min_items=1)
    drug_histories:  Optional[List[DrugItem]] = []
    drug_allergies:  List[DrugItem] = Field(..., min_items=1)

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
    input_substances:      List[Dict[str, str]]
    contrast_substances:   List[Dict[str, str]]

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
    allergy_type:      int = Field(
        ..., ge=0, le=2,
        description="0=current only, 1=history only, 2=both"
    )
    allergy_substances: List[Dict[str, str]]

class DrugsResponse(BaseModel):
    status:  bool
    code:    int
    message: str
    data:    PageResponse[ContrastItem]

class AllergyResponse(BaseModel):
    status:  bool
    code:    int
    message: str
    data:    PageResponse[AllergyItem]
