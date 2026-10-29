import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.ragflow_client import configured, get_or_create_dataset, list_datasets
from backend import config

print("configured:", configured())
print("datasets:", json.dumps(list_datasets(), ensure_ascii=False, indent=1))

ds = get_or_create_dataset()
print("dataset:", json.dumps(ds, ensure_ascii=False))
if ds.get("id"):
    import requests

    headers = {"Authorization": f"Bearer {config.RAGFLOW_API_KEY}"}
    resp = requests.get(
        f"{config.RAGFLOW_BASE_URL}/api/v1/datasets/{ds['id']}/documents",
        headers=headers, timeout=30,
    )
    data = resp.json().get("data", {})
    docs = data.get("docs", [])
    print("total:", data.get("total"))
    for d in docs:
        print(
            d.get("id"), "|", d.get("name"), "|", d.get("run"), "|",
            d.get("progress"), "|", d.get("process_begin_at"), "|", d.get("process_duation"),
        )
