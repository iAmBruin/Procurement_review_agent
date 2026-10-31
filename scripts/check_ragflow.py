"""RAGFlow 诊断：检查配置、列出数据集及其文档。"""
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.ragflow_client import configured, list_datasets
from backend import config


def main() -> None:
    print("configured:", configured())
    datasets = list_datasets()
    print("datasets:", json.dumps(datasets, ensure_ascii=False, indent=1))

    ds = next((d for d in datasets if d.get("name") == config.RAGFLOW_DATASET), None)
    if ds is None and datasets:
        ds = datasets[0]
    if not ds:
        print("no dataset found")
        return

    import requests

    headers = {"Authorization": f"Bearer {config.RAGFLOW_API_KEY}"}
    resp = requests.get(
        f"{config.RAGFLOW_BASE_URL}/api/v1/datasets/{ds['id']}/documents",
        headers=headers, timeout=30,
    )
    data = resp.json().get("data", {})
    docs = data.get("docs", [])
    print("dataset:", ds.get("name"), "total docs:", data.get("total"))
    for d in docs:
        print(
            d.get("id"), "|", d.get("name"), "|", d.get("run"), "|",
            d.get("progress"),
        )


if __name__ == "__main__":
    main()