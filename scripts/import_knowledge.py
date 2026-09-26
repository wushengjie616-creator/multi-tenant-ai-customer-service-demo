"""导入示例租户知识；默认 dry-run，--apply 才写入 Qdrant 与知识图谱四层存储。"""

import argparse
import asyncio
import json
from pathlib import Path

from app.clients.knowledge_client import vector_store
from app.core.database import async_session, engine
from app.services.knowledge_ingestion import build_document_chunks
from app.services.graph_store import graph_store


async def run(root: Path, apply: bool) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    tenant_id = manifest["tenant_id"]
    chunks = []
    docs = []
    for doc in manifest["documents"]:
        content = (root / doc["file"]).read_text()
        docs.append((doc, content))
        chunks.extend(build_document_chunks(
            tenant_id=tenant_id, document_id=doc["document_id"],
            title=doc["title"], source=doc["source"], content=content,
            version=doc["version"], effective_from=doc.get("effective_from"),
            effective_until=doc.get("effective_until"),
        ))
    print(f"validated {len(chunks)} chunks for tenant {tenant_id} (apply={apply})")
    if apply:
        async with async_session() as session:
            for doc, content in docs:
                await vector_store.delete_document(tenant_id, doc["document_id"])
                await graph_store.replace_document(
                    session, tenant_id,
                    document_id=doc["document_id"], title=doc["title"],
                    source=doc["source"], content=content, version=doc["version"],
                    effective_from=doc.get("effective_from"),
                    effective_until=doc.get("effective_until"),
                )
        await vector_store.upsert_chunks(tenant_id, chunks)
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--root", default="sample-data/tenants/demo-school"); parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(); asyncio.run(run(Path(args.root), args.apply))
