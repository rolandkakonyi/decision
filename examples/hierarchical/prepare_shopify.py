"""Offline benchmark-data preparation; no inference or network access."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = "https://raw.githubusercontent.com/Shopify/product-taxonomy/0b9ed7a4bdd4325aa7761c54ca25955fa70fc9db/dist/en/categories.txt"
SOURCE_SHA256 = "d856f85cd51803c0ffc3d70a45761055b8169e00d271ee0adbfc2248d106c7cb"


def prepare(source):
    rows = {}
    for line in source.splitlines():
        if line.startswith("gid://shopify/TaxonomyCategory/"):
            gid, path = line.split(" : ", 1)
            rows[gid.strip().rsplit("/", 1)[1]] = path.strip()
    parents = {path.rpartition(" > ")[0] for path in rows.values()}
    leaves = {key: path for key, path in rows.items() if path not in parents}
    cases = json.loads((ROOT / "shopify-cases.json").read_text(encoding="utf-8"))
    targets = {case["expected"] for case in cases}
    if not targets <= leaves.keys():
        raise ValueError("Every expected label must be a taxonomy leaf")
    departments = sorted({leaves[key].split(" > ")[0] for key in targets})

    def closeness(path, target):
        count = 0
        for left, right in zip(path.split(" > "), target.split(" > ")):
            if left != right:
                break
            count += 1
        return count

    groups = []
    for department in departments:
        root_id = next(key for key, path in rows.items() if path == department)
        children = [key for key, path in leaves.items() if path.startswith(department + " > ")]
        local_targets = [leaves[key] for key in targets if leaves[key].startswith(department + " > ")]
        children.sort(key=lambda key: (
            key not in targets,
            -max(closeness(leaves[key], target) for target in local_targets),
            hashlib.sha256(key.encode()).hexdigest(),
        ))
        immediate = sorted(path.rsplit(" > ", 1)[1] for path in rows.values() if path.rpartition(" > ")[0] == department)
        groups.append({"id": root_id, "description": department + "; includes " + ", ".join(immediate), "children": children})
    selected = {key for group in groups for key in group["children"][:2]} | targets
    index = 0
    while len(selected) < 1024:
        for group in groups:
            if len(selected) < 1024 and index < len(group["children"]):
                selected.add(group["children"][index])
        index += 1
    for group in groups:
        group["children"] = [{"id": key, "description": leaves[key]} for key in group["children"] if key in selected]
    if any(not 2 <= len(group["children"]) <= 255 for group in groups):
        raise ValueError("A sampled group exceeds the Choice limits")
    return {"groups": groups, "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--categories", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.categories.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        parser.error("Categories do not match the pinned Shopify snapshot")
    dataset = prepare(source.decode("utf-8"))
    # One category per line keeps the snapshot reviewable.
    lines = ['{"groups": [']
    for index, group in enumerate(dataset["groups"]):
        lines.extend(['  {"id": ' + json.dumps(group["id"]) + ', "description": ' + json.dumps(group["description"]) + ', "children": [',
                      ",\n".join("    " + json.dumps(child, ensure_ascii=False) for child in group["children"]),
                      "  ]}" + ("," if index < len(dataset["groups"]) - 1 else "")])
    lines.extend(['], "cases":', json.dumps(dataset["cases"], indent=2, ensure_ascii=False), "}"])
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"source": SOURCE, "source_sha256": hashlib.sha256(source).hexdigest(),
                      "dataset_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                      "groups": len(dataset["groups"]), "leaves": sum(len(g["children"]) for g in dataset["groups"]), "cases": len(dataset["cases"])}))


if __name__ == "__main__":
    main()
