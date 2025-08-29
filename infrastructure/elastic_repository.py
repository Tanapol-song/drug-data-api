from typing import List
from elasticsearch import AsyncElasticsearch
from domain.repository import ElasticDrugRepository


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
        print("✅insert_embedding by id :", tpu_code)
        await self.client.index(index=self.index_name, document=doc)

    async def search_by_vector_knn(self, query_vector: List[float], k: int = 10):
        body = {
            "field": "embedding",
            "query_vector": query_vector,
            "k": k,
            "num_candidates": 500,
        }

        response = await self.client.search(
            index=self.index_name, knn=body, source=(["name", "tpu_codes"])
        )
        return response["hits"]["hits"]

    async def search_by_vector(self, query_vector: List[float], k: int = 10):
        body = {
            "size": k,
            "query": {
                "script_score": {
                    "query": {"match_all": {}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'embedding')",
                        "params": {"query_vector": query_vector},
                    },
                }
            },
            "_source": ["name", "tpu_codes"],
        }

        response = await self.client.search(index=self.index_name, body=body)
        return response["hits"]["hits"]
