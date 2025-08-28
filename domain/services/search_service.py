import aiohttp
import asyncio
import logging

from typing import Dict, List, Any, Tuple, Optional
from infrastructure.elastic_repository import ElasticDrugRepository
from infrastructure.neo4j_repository import Neo4jDrugRepository

logging.basicConfig(level=logging.INFO)


class SearchService:
    def __init__(
        self,
        elastic_repo: ElasticDrugRepository,
        neo4j_repo: Neo4jDrugRepository,
        embedding_url: str,
        embedding_auth: str,
    ):
        self.elastic_repo = elastic_repo
        self.neo4j_repo = neo4j_repo
        self.embedding_url = embedding_url
        self.embedding_auth = embedding_auth

    async def list_tpu_data(self) -> list[dict]:
        return await self.neo4j_repo.get_all_tpu_data()

    async def get_embedding(self, text: str):
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.embedding_auth,
        }
        payload = {"inputs": text, "truncate": True, "truncation_direction": "Right"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.embedding_url, json=payload, headers=headers
            ) as resp:
                data = await resp.json()
                return data

    async def embed_and_upsert_tpu_in_batches(
        self,
        batch_size: int = 10,
        concurrency: int = 10,
        delay_sec: float = 0.0,
        skip_if_exists: bool = True,
        start_after_idx: int = 0,
    ) -> Dict[str, Any]:

        logging.info("⏳ เริ่มตรวจสอบ index บน Elasticsearch...")
        await self.elastic_repo.ensure_index_exists()

        logging.info("📥 ดึงข้อมูล TPU จาก Neo4j...")
        rows: List[Dict[str, Any]] = await self.neo4j_repo.get_all_tpu_data()
        rows = rows[start_after_idx:] if start_after_idx > 0 else rows
        total = len(rows)
        logging.info(f"🔢 พบทั้งหมด {total} records")

        inserted = 0
        skipped = 0
        failed = 0
        detail: List[Tuple[str, str, str]] = []

        sem = asyncio.Semaphore(concurrency)

        async def _process_one(tpu_name: str, tmtid: str, id: str):
            nonlocal inserted, skipped, failed
            try:
                if skip_if_exists and await self.elastic_repo.exists_by_id(tmtid):
                    skipped += 1
                    detail.append(
                        {
                            "status": "exists",
                            "TPUNAME": tpu_name,
                            "TPUCODES": tmtid,
                            "DOC_ID": id,
                        }
                    )
                    if delay_sec:
                        await asyncio.sleep(delay_sec)
                    return

                logging.info(f"🧠 ฝัง embedding สำหรับ: {tpu_name} ({tmtid})")
                emb = await self.get_embedding(tpu_name)
                first_vector = emb[0][0]

                logging.info(f"📦 กำลัง upsert: {tpu_name} ({tmtid})")
                await self.elastic_repo.insert_embedding(
                    tpu_name=tpu_name, tpu_code=tmtid, embedding=first_vector, id=id
                )
                inserted += 1
                detail.append(("inserted", tpu_name, tmtid))
                if delay_sec:
                    await asyncio.sleep(delay_sec)
            except Exception as e:
                failed += 1
                detail.append((f"error: {e}", tpu_name, tmtid))

        for i in range(0, total, batch_size):
            batch = rows[i : i + batch_size]
            current_batch = (i // batch_size) + 1
            total_batches = (total + batch_size - 1) // batch_size

            logging.info(
                f"🚀 เริ่มประมวลผล batch {current_batch}/{total_batches} ({len(batch)} records)"
            )

            tasks = []
            for r in batch:
                tpu_name = r.get("TPUNAME")
                tmtid = r.get("TMTID")
                id = r.get("id")
                print("id :", id)
                if not tpu_name or not tmtid:
                    failed += 1
                    detail.append(("invalid", tpu_name or "", tmtid or ""))
                    continue

                async def _guarded(name=tpu_name, code=tmtid, id=id):
                    async with sem:
                        await _process_one(name, code, id)

                tasks.append(asyncio.create_task(_guarded()))

                if tasks:
                    await asyncio.gather(*tasks)

        logging.info("✅ เสร็จสิ้นการทำงานทั้งหมด")
        return {
            "total": total,
            "inserted": inserted,
            "skipped": skipped,
            "failed": failed,
            "detail": detail[:50],
        }
