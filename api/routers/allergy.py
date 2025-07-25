# File: api/routers/allergy.py

import os
from fastapi import APIRouter, Depends, Query
from neo4j import basic_auth, AsyncGraphDatabase
from dotenv import load_dotenv

from domain.models import AllergyPayload, AllergyResponse
from infrastructure.neo4j_repository import Neo4jDrugRepository
from domain.services.allergy_service import AllergyService

# Load environment
load_dotenv("api.env")

# Neo4j configuration
NEO4J_URI  = os.getenv("NEO4J_URI_STAGING")
NEO4J_USER = os.getenv("NEO4J_USERNAME_STAGING")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD_STAGING")

driver = AsyncGraphDatabase.driver(
    NEO4J_URI,
    auth=basic_auth(NEO4J_USER, NEO4J_PASS),
    max_connection_pool_size=20
)

router = APIRouter(prefix="/api/v1")

def get_repo() -> Neo4jDrugRepository:
    return Neo4jDrugRepository(driver)

def get_allergy_service(
    repo: Neo4jDrugRepository = Depends(get_repo)
) -> AllergyService:
    return AllergyService(repo)

@router.post(
    "/allergy",
    response_model=AllergyResponse,
    summary="Get allergy summary"
)
async def get_allergy(
    payload: AllergyPayload,
    page: int = Query(1, ge=1, description="Page number"),
    row:  int = Query(10, ge=1, description="Items per page"),
    service: AllergyService = Depends(get_allergy_service)
) -> AllergyResponse:
    return await service.get_allergy(payload, page, row)
