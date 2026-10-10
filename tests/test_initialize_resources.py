"""离线模拟网络，覆盖首次恢复、重跑、损坏附件和模型补齐。"""

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

import httpx
import pytest

from chinese_legal_agent import initialize
from chinese_legal_agent import initialize_resources as resources


def checksum(value):
    return hashlib.sha256(value).hexdigest()


def index_archive(count=2, malicious=None):
    stream = io.BytesIO()
    files = {
        "law-bm25/manifest.json": json.dumps(
            {"collection": "npc_laws", "count": count}
        ).encode(),
        "law-bm25/ids.json": json.dumps([str(i) for i in range(count)]).encode(),
        **{
            f"law-bm25/{kind}/{name}": b"index-data"
            for kind in ("title", "content")
            for name in resources.INDEX_FILES
        },
    }
    if malicious:
        files[malicious] = b"outside"
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


@pytest.fixture
def law_server(monkeypatch):
    snapshot = b"fake-qdrant-snapshot" * 100
    compressed = gzip.compress(snapshot)
    blobs = {
        "part001": compressed[:17],
        "part002": compressed[17:],
        "law-bm25.tar.gz": index_archive(),
    }

    def asset(name):
        return {
            "name": name,
            "bytes": len(blobs[name]),
            "sha256": checksum(blobs[name]),
            "url": f"https://assets.test/{name}",
        }

    manifest = {
        "format_version": 1,
        "collection": "npc_laws",
        "count": 2,
        "snapshot": {
            "filename": "npc_laws.snapshot",
            "encoding": "gzip-split",
            "bytes": len(snapshot),
            "sha256": checksum(snapshot),
            "compressed_bytes": len(compressed),
            "compressed_sha256": checksum(compressed),
            "parts": [asset("part001"), asset("part002")],
        },
        "bm25": asset("law-bm25.tar.gz"),
    }
    state = {
        "count": None,
        "requests": [],
        "uploads": 0,
        "snapshot": snapshot,
        "manifest": manifest,
    }
    original_client = httpx.Client

    def serve(request):
        state["requests"].append(str(request.url))
        if request.url.host == "assets.test":
            if request.url.path == "/manifest.json":
                return httpx.Response(200, json=manifest)
            return httpx.Response(200, content=blobs[request.url.path.lstrip("/")])
        if request.url.path == "/collections/npc_laws/points/count":
            return httpx.Response(200, json={"result": {"count": state["count"]}})
        if request.url.path == "/collections/npc_laws/snapshots/upload":
            body = request.read()
            assert snapshot in body
            assert request.url.params["checksum"] == checksum(snapshot)
            assert request.url.params["wait"] == "true"
            state["uploads"] += 1
            state["count"] = 2
            return httpx.Response(200, json={"result": True})
        if state["count"] is None:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "result": {
                    "config": {
                        "params": {
                            "vectors": {"text": {"size": 1024, "distance": "Cosine"}}
                        }
                    }
                }
            },
        )

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(serve), **kwargs)

    monkeypatch.setattr(resources.httpx, "Client", client_factory)
    monkeypatch.setattr(resources.time, "sleep", lambda _: None)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.test")
    monkeypatch.setenv("LAWS_MANIFEST_URL", "https://assets.test/manifest.json")
    monkeypatch.setenv(
        "LAWS_MANIFEST_SHA256",
        checksum(json.dumps(manifest, separators=(",", ":")).encode()),
    )
    return state


def test_fresh_laws_restores_snapshot_index_and_repeat_uses_cache(tmp_path, law_server):
    resources.initialize_laws(tmp_path)
    assert law_server["uploads"] == 1
    assert resources.index_ready(tmp_path / "law-bm25", 2)
    assert (tmp_path / "law-initialize/npc_laws.snapshot").read_bytes() == law_server[
        "snapshot"
    ]
    law_server["requests"].clear()
    resources.initialize_laws(tmp_path)
    assert law_server["uploads"] == 1
    assert all("assets.test" not in url for url in law_server["requests"])


def test_existing_collection_missing_index_downloads_only_bm25(tmp_path, law_server):
    law_server["count"] = 2
    resources.initialize_laws(tmp_path)
    assert law_server["uploads"] == 0
    assert resources.index_ready(tmp_path / "law-bm25", 2)
    assert not any(
        "part001" in url or "part002" in url for url in law_server["requests"]
    )


def test_nonempty_different_collection_is_preserved(tmp_path, law_server):
    law_server["count"] = 1
    with pytest.raises(ValueError, match="不会覆盖非空集合"):
        resources.initialize_laws(tmp_path)
    assert law_server["uploads"] == 0
    assert law_server["count"] == 1
    assert not (tmp_path / "law-bm25").exists()


def test_bad_snapshot_does_not_publish_index_or_restore(
    tmp_path, law_server, monkeypatch
):
    manifest = law_server["manifest"]
    manifest["snapshot"]["compressed_sha256"] = "0" * 64
    content = json.dumps(manifest, separators=(",", ":")).encode()
    # fixture's HTTP response follows the changed manifest.
    monkeypatch.setenv("LAWS_MANIFEST_SHA256", checksum(content))
    with pytest.raises(ValueError, match="拼接校验失败"):
        resources.initialize_laws(tmp_path)
    assert law_server["uploads"] == 0
    assert not (tmp_path / "law-bm25").exists()


def test_download_resumes_and_verified_cache_never_connects(tmp_path):
    content = b"download-body"
    destination = tmp_path / "asset"
    destination.with_name("asset.partial").write_bytes(content[:4])
    requests = []

    def serve(request):
        requests.append(request)
        assert request.headers["Range"] == "bytes=4-"
        return httpx.Response(
            206, headers={"content-range": "bytes 4-12/13"}, content=content[4:]
        )

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        resources.download(
            client,
            "https://assets.test/asset",
            destination,
            checksum(content),
            len(content),
        )
        resources.download(
            client,
            "https://assets.test/asset",
            destination,
            checksum(content),
            len(content),
        )
    assert len(requests) == 1
    assert destination.read_bytes() == content


def test_download_checksum_failure_never_commits_file(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.time, "sleep", lambda _: None)
    with (
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"wrong")
            )
        ) as client,
        pytest.raises(ValueError, match="校验失败"),
    ):
        resources.download(
            client,
            "https://assets.test/asset",
            tmp_path / "asset",
            checksum(b"right"),
            5,
        )
    assert not (tmp_path / "asset").exists()


def test_index_archive_rejects_path_traversal(tmp_path):
    archive = tmp_path / "index.tar.gz"
    archive.write_bytes(index_archive(malicious="law-bm25/../../outside"))
    with pytest.raises(ValueError, match="无效路径"):
        resources.extract_index(archive, tmp_path / "staging", 2)
    assert not (tmp_path / "outside").exists()


def fake_model_file(name):
    if name == "model.safetensors":
        header = json.dumps(
            {"weight": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}
        ).encode()
        return len(header).to_bytes(8, "little") + header + b"1234"
    return b"{}" if name.endswith(".json") else b"sentencepiece"


def test_reranker_downloads_only_missing_and_corrupt_files(tmp_path, monkeypatch):
    for name in resources.MODEL_FILES:
        (tmp_path / name).write_bytes(fake_model_file(name))
    calls = []

    def download(**kwargs):
        calls.append(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)
        (Path(kwargs["local_dir"]) / kwargs["filename"]).write_bytes(
            fake_model_file(kwargs["filename"])
        )

    monkeypatch.setattr("huggingface_hub.hf_hub_download", download)
    resources.initialize_reranker(tmp_path)
    assert not calls
    (tmp_path / "tokenizer.json").unlink()
    (tmp_path / "model.safetensors").write_bytes(b"truncated")
    resources.initialize_reranker(tmp_path)
    assert {call["filename"] for call in calls} == {
        "tokenizer.json",
        "model.safetensors",
    }
    assert next(call for call in calls if call["filename"] == "model.safetensors")[
        "force_download"
    ]
    assert resources.model_ready(tmp_path)
    calls.clear()
    resources.initialize_reranker(tmp_path / "fresh-model")
    assert {call["filename"] for call in calls} == set(resources.MODEL_FILES)
    assert resources.model_ready(tmp_path / "fresh-model")


def test_resource_failure_returns_nonzero_without_echoing_credentials(
    tmp_path, monkeypatch, capsys
):
    def fail(_):
        raise RuntimeError("private-key-in-external-error")

    monkeypatch.setattr(resources, "initialize_laws", fail)
    monkeypatch.setattr(
        "sys.argv",
        [
            "initialize",
            "--config-dir",
            str(tmp_path / "config"),
            "--data-dir",
            str(tmp_path / "data"),
            "--laws",
            "--no-reranker",
        ],
    )
    assert initialize.main() == 1
    captured = capsys.readouterr()
    assert "法律数据初始化失败" in captured.err
    assert "private-key-in-external-error" not in captured.out + captured.err
