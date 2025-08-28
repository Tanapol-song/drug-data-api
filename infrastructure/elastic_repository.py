from elasticsearch import AsyncElasticsearch
from domain.repository import ElasticDrugRepository
from dotenv import load_dotenv
import aiohttp
import os


class ElasticDrugRepository(ElasticDrugRepository):

    def __init__(self, client: AsyncElasticsearch):
        self.client = client
        self.index_name = "vector_drugs_db"

    async def ensure_index_exists(self):
        exists = await self.client.indices.exists(index=self.index_name)
        if exists:
            return
        await self.client.indices.create(
            index=self.index_name,
            body={
                "mappings": {
                    "properties": {
                        "name": {"type": "text"},
                        "tpu_codes": {"type": "keyword"},
                        "embedding": {
                            "type": "dense_vector",
                            "dims": 4096,
                            "index": True,
                            "similarity": "cosine",
                        },
                    }
                }
            },
        )

    async def exists_by_id(self, doc_id: str) -> bool:
        # elasticsearch-py v8 returns bool already
        return bool(await self.client.exists(index=self.index_name, id=doc_id))

    async def check_exists_by_tpu(self, tpu_name: str) -> bool:
        result = await self.client.search(
            index=self.index_name, body={"query": {"term": {"name.keyword": tpu_name}}}
        )
        return result["hits"]["total"]["value"] > 0

    async def insert_embedding(
        self,
        tpu_name: str,
        embedding: list[float],
        tpu_code: str,
        id: str,
    ):
        if len(embedding) != 4096:
            raise ValueError("Embedding vector must be 4096 dimensions")

        doc = {
            "name": tpu_name,
            "tpu_codes": tpu_code,
            "embedding": embedding,
        }
        print("✅insert_embedding by id :", id)
        await self.client.index(index=self.index_name, id=id, document=doc)
