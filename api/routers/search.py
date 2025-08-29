import os
from fastapi import APIRouter, Depends, Query, Body
from neo4j import basic_auth, AsyncGraphDatabase
from elasticsearch import AsyncElasticsearch, Elasticsearch
from dotenv import load_dotenv

from domain.models import (
    DrugPayload,
    DrugsResponse,
    DrugSearchPayload,
    DrugSearchResponse,
)
from infrastructure.elastic_repository import ElasticDrugRepository
from infrastructure.neo4j_repository import Neo4jDrugRepository
from domain.services.search_service import SearchService

load_dotenv("api.env")

# Elastic configuration
ELASTIC_URL = os.getenv("ELASTIC_URL_STAGING")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")
# ELASTIC_URL = os.getenv("ELASTIC_URL")
# ELASTIC_USER = os.getenv("ELASTIC_USER")
# ELASTIC_PASS = os.getenv("ELASTIC_PASS")

# Embedding configuration
QWEN_EMB_API = os.getenv("EMBEDDING_URL")
QWEN_EMB_AUTH = os.getenv("EMBEDDING_AUTH")

# Neo4j configuration
NEO4J_URI = os.getenv("NEO4J_URI_STAGING")
NEO4J_USER = os.getenv("NEO4J_USERNAME_STAGING")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD_STAGING")

router = APIRouter(prefix="/api/v1")

# es_client = AsyncElasticsearch(ELASTIC_URL, basic_auth=(ELASTIC_USER, ELASTIC_PASS))
es_client = AsyncElasticsearch(ELASTIC_URL, api_key=ELASTIC_API_KEY)
# es_client = Elasticsearch(ELASTIC_URL, api_key=ELASTIC_API_KEY)


driver = AsyncGraphDatabase.driver(
    NEO4J_URI, auth=basic_auth(NEO4J_USER, NEO4J_PASS), max_connection_pool_size=20
)


def get_elastic_repo() -> ElasticDrugRepository:
    return ElasticDrugRepository(es_client)


def get_search_service() -> SearchService:
    elastic_repo = ElasticDrugRepository(es_client)
    neo4j_repo = Neo4jDrugRepository(driver)
    return SearchService(
        elastic_repo=elastic_repo,
        neo4j_repo=neo4j_repo,
        embedding_url=QWEN_EMB_API,
        embedding_auth=QWEN_EMB_AUTH,
    )


@router.post("/embedding-check")
async def search_drugs_elastic(
    payload: DrugSearchPayload, service: SearchService = Depends(get_search_service)
):
    embeddings = await service.get_embedding(payload.names[0])  # ตัวอย่าง
    return {
        "debug_payload": payload.dict(),
        "embeddings": embeddings,
    }


@router.post("/search-elastic-vector")
async def search_drugs_elastic(
    payload: DrugSearchPayload, service: SearchService = Depends(get_search_service)
):
    result = await service.match_vector(payload.names[0])
    print("name :", payload.names[0])
    return {
        "debug_payload": payload.dict(),
        "matching": result,
    }


@router.get("/tpu-list")
async def get_tpu_list(service: SearchService = Depends(get_search_service)):
    data = await service.list_tpu_data()
    return {"total": len(data), "items": data}


@router.post("tpu-embedding")
async def tpu_sync_batch(
    service: SearchService = Depends(get_search_service),
):
    out = await service.embed_and_upsert_tpu_in_batches(
        batch_size=100,
        concurrency=10,
        delay_sec=0.05,
        skip_if_exists=True,
        # start_after_idx=0
    )
    return out
