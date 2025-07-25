# File: api/routers/drugs.py

import os
from fastapi import APIRouter, Depends, Query
from neo4j import basic_auth, AsyncGraphDatabase
from dotenv import load_dotenv

from domain.models import DrugPayload, DrugsResponse
from infrastructure.neo4j_repository import Neo4jDrugRepository
from domain.services.interaction_service import InteractionService

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

def get_interaction_service(
    repo: Neo4jDrugRepository = Depends(get_repo)
) -> InteractionService:
    return InteractionService(repo)

@router.post(
    "/drugs",
    response_model=DrugsResponse,
    summary="Get drug interaction contrasts"
)
async def get_interactions(
    payload: DrugPayload,
    page: int = Query(1, ge=1, description="Page number, default=1"),
    row:  int = Query(10, ge=1, description="Items per page, default=10"),
    service: InteractionService = Depends(get_interaction_service)
) -> DrugsResponse:
    return await service.get_interactions(payload, page, row)
