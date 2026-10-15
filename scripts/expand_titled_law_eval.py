"""Append 170 authored, corpus-verified cases and freeze the 300 titled cases."""

import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    args = parser.parse_args()
    dataset = Path("evals/legal_retrieval.jsonl")
    cases = [
        json.loads(line)
        for line in dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    original = [
        case for case in cases if case.get("eval_group") != "titled-expansion-170"
    ]
    assert len(original) == 200, "Expected the original 200 frozen cases"
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    old_targets = {
        (item["title"], item["article_no_cn"])
        for case in original
        for item in case["expected"]
    }
    additions = []
    evidence = []
    for line in (
        Path("evals/titled_expansion_170.tsv").read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip() or line.startswith("#"):
            continue
        law, number, keywords, question = line.split("|")
        title = f"中华人民共和国{law}"
        matches = [
            row
            for row in corpus
            if row["payload"]["title"] == title
            and row["payload"].get("article_no") == int(number)
        ]
        assert matches, f"Missing target in corpus: {title} {number}"
        matches.sort(
            key=lambda row: row["payload"].get("filename_date", ""), reverse=True
        )
        row = matches[0]
        payload = row["payload"]
        target = {"title": title, "article_no_cn": payload["article_no_cn"]}
        key = (title, payload["article_no_cn"])
        assert key not in old_targets, f"Target already covered: {key}"
        assert key not in {
            (c["expected"][0]["title"], c["expected"][0]["article_no_cn"])
            for c in additions
        }
        case_id = f"titled-expansion-{['民法典', '劳动合同法', '个人信息保护法', '消费者权益保护法', '行政处罚法'].index(law) + 1}-{number}"
        additions.append(
            {
                "id": case_id,
                "issue": f"{law}：{'、'.join(keywords.split(',')[:2])}",
                "title_keywords": [law],
                "content_keywords": keywords.split(","),
                "case_facts": question,
                "expected": [target],
                "eval_group": "titled-expansion-170",
                "source_point_id": str(row["id"]),
                "source_doc_id": payload["doc_id"],
                "source_text_sha256": hashlib.sha256(
                    payload["text"].encode()
                ).hexdigest(),
            }
        )
        evidence.append(
            {
                "id": case_id,
                "expected": target,
                "source_point_id": str(row["id"]),
                "source_doc_id": payload["doc_id"],
                "filename_date": payload.get("filename_date"),
                "status": payload.get("status"),
                "text": payload["text"],
            }
        )
    assert len(additions) == 170, len(additions)
    expanded = original + additions
    titled = [case for case in expanded if case["title_keywords"]]
    assert len(titled) == 300 and len(expanded) == 370
    assert len({case["id"] for case in expanded}) == 370
    assert len({case["case_facts"] for case in titled}) == 300
    for path, rows in [
        (dataset, expanded),
        (Path("evals/legal_retrieval_titled_300.jsonl"), titled),
    ]:
        path.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                for row in rows
            )
            + "\n",
            encoding="utf-8",
        )
    evidence_path = Path("eval-results/legal-retrieval-titled-300-sources.json")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Full dataset: {len(expanded)}; titled: {len(titled)}; additions: {len(additions)}"
    )
    print(
        f"Unique titled targets: {len({(e['title'], e['article_no_cn']) for c in titled for e in c['expected']})}"
    )


if __name__ == "__main__":
    main()
