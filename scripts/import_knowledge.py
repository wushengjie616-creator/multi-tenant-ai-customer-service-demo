"""导入示例租户知识；默认 dry-run，--apply 才写入 Qdrant。"""

import argparse
import asyncio
import json
from pathlib import Path

from app.clients.knowledge_client import vector_store
from app.services.knowledge_ingestion import build_document_chunks


async def run(root: Path, apply: bool) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    chunks = []
    for doc in manifest["documents"]:
        content = (root / doc["file"]).read_text()
        chunks.extend(build_document_chunks(
            tenant_id=manifest["tenant_id"], document_id=doc["document_id"],
            title=doc["title"], source=doc["source"], content=content,
            version=doc["version"], effective_from=doc.get("effective_from"),
            effective_until=doc.get("effective_until"),
        ))
    print(f"validated {len(chunks)} chunks for tenant {manifest['tenant_id']} (apply={apply})")
    if apply:
        for doc in manifest["documents"]:
            await vector_store.delete_document(manifest["tenant_id"], doc["document_id"])
        await vector_store.upsert_chunks(manifest["tenant_id"], chunks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--root", default="sample-data/tenants/demo-school"); parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(); asyncio.run(run(Path(args.root), args.apply))
