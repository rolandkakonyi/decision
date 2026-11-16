"""Prepare a reproducible held-out WDC-222 sample without inference or relabeling."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

SOURCE = "https://data.dws.informatik.uni-mannheim.de/largescaleproductcorpus/categorization/data/json/wdc_data_full.json"
SOURCE_SHA256 = "1bb82b5eba14f60bd61187eb67ab7717581ac86ffe4507b8981cbe13816ad908"
SEED = "decision-wdc222-v1"


def prepare(source, count):
    fields = json.loads(source)
    groups, categories, offers = {}, {}, []
    for row_id, label in fields["CategoryID"].items():
        ids, names = fields["pathlist_ids"][row_id], fields["pathlist_names"][row_id]
        if len(ids) != len(names) or len(ids) < 3 or ids[-1] != label or names[-1] != fields["CategoryName"][row_id]:
            raise ValueError("Inconsistent source category path")
        category = {"id": label, "description": " > ".join(names)}
        if label in categories and categories[label] != category:
            raise ValueError("Conflicting source category paths")
        categories[label] = category
        group = groups.setdefault(ids[1], {"id": ids[1], "description": " > ".join(names[:2]), "children": {}})
        group["children"][label] = category
        title, description = fields["title"][row_id], fields["desc"][row_id]
        if not isinstance(title, str) or not title.strip():
            continue
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise ValueError("Description must be text or null")
        offers.append({"id": "wdc-" + row_id,
                       "text": "Title: " + title + "\nDescription: " + description,
                       "expected": label, "gtin": str(fields["gtin"][row_id]),
                       "cluster_id": str(fields["cluster_id"][row_id]),
                       "source_row_id": row_id,
                       "text_key": hashlib.sha256(json.dumps([title.strip(), description.strip()], ensure_ascii=False).encode()).hexdigest()})
    if count < len(categories):
        raise ValueError("Count must cover every existing category")
    offers.sort(key=lambda row: hashlib.sha256((SEED + row["id"]).encode()).hexdigest())
    by_category = {key: [] for key in categories}
    for offer in offers:
        by_category[offer["expected"]].append(offer)
    selected, gtins, clusters, texts = [], set(), set(), set()

    def include(offer):
        if offer["gtin"] in gtins or offer["cluster_id"] in clusters or offer["text_key"] in texts:
            return False
        selected.append(offer)
        gtins.add(offer["gtin"])
        clusters.add(offer["cluster_id"])
        texts.add(offer["text_key"])
        return True

    for label in sorted(categories, key=lambda key: (len(by_category[key]), key)):
        if not any(include(offer) for offer in by_category[label]):
            raise ValueError("Cannot cover every category with distinct products")
    for offer in offers:
        if len(selected) >= count:
            break
        include(offer)
    if len(selected) != count:
        raise ValueError("Not enough distinct products")
    selected.sort(key=lambda row: hashlib.sha256((SEED + ":order:" + row["id"]).encode()).hexdigest())
    dataset = {"groups": [{**group, "children": sorted(group["children"].values(), key=lambda c: c["id"])} for group in sorted(groups.values(), key=lambda g: g["id"])],
               "cases": [{key: offer[key] for key in ("id", "text", "expected")} for offer in selected]}
    manifest = {"source": SOURCE, "source_sha256": SOURCE_SHA256, "seed": SEED,
                "source_offers": len(fields["CategoryID"]), "eligible_offers": len(offers),
                "selected_products": len(selected), "category_count": len(categories), "group_count": len(groups),
                "sampling": "one existing-labeled offer per category, then hash-ordered fill; unique GTIN, cluster, and input text",
                "model_input_fields": ["title", "desc"], "category_support": dict(sorted(Counter(row["expected"] for row in selected).items())),
                "selected": [{key: offer[key] for key in ("id", "source_row_id", "gtin", "cluster_id", "expected")} for offer in selected]}
    return dataset, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--count", type=int, default=256)
    args = parser.parse_args()
    source = args.source.read_bytes()
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        parser.error("Source does not match the pinned WDC-222 snapshot")
    dataset, manifest = prepare(source, args.count)
    encoded = json.dumps(dataset, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(encoded, encoding="utf-8", newline="\n")
    manifest["dataset_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    args.output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: manifest[key] for key in ("source_sha256", "dataset_sha256", "source_offers", "selected_products", "category_count", "group_count")}))


if __name__ == "__main__":
    main()
