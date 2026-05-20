import json
from pathlib import Path
from types import SimpleNamespace

from indbase_core.config import SwallowIngestConfig
from indbase_core.swallow_adapter import SwallowIngestAdapter


def test_swallow_adapter_maps_ocr_artifacts_to_page_locators(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = vault / ".indbase" / "cache" / "swallow"
    job_dir = store / "jobs" / "job_ocr"
    ocr_json = job_dir / "intermediate" / "paddleocr" / "result.json"
    pages_dir = job_dir / "intermediate" / "pages"
    ocr_json.parent.mkdir(parents=True)
    pages_dir.mkdir(parents=True)
    ocr_json.write_text(
        json.dumps(
            [
                {"page_number": 1, "entries": [{"text": "first page"}]},
                {"page_number": 2, "entries": [{"text": "second page"}]},
            ]
        ),
        encoding="utf-8",
    )
    (pages_dir / "page_0001.png").write_bytes(b"page1")
    (pages_dir / "page_0002.png").write_bytes(b"page2")
    (job_dir / "trace.jsonl").write_text("{}", encoding="utf-8")
    (job_dir / "manifest.json").write_text('{"status":"success"}', encoding="utf-8")
    (job_dir / "ingest_document.json").write_text("{}", encoding="utf-8")

    adapter = SwallowIngestAdapter(vault_path=vault, config=SwallowIngestConfig())
    candidate = adapter._candidate_from_run_result(
        _fake_result(
            job_id="job_ocr",
            markdown="# Scan\n\n## Page 1\nfirst page\n\n## Page 2\nsecond page\n",
            primary_worker="paddleocr_worker",
            artifacts=(
                {"type": "ocr_json", "path": "intermediate/paddleocr/result.json"},
                {"type": "page_image_dir", "path": "intermediate/pages"},
            ),
        )
    )

    assert [locator["kind"] for locator in candidate.source_locators] == ["ocr_page", "ocr_page"]
    assert candidate.source_locators[0]["page"] == 1
    assert candidate.source_locators[0]["artifact"] == "jobs/job_ocr/intermediate/paddleocr/result.json"
    assert candidate.source_locators[0]["page_image_artifact"] == "jobs/job_ocr/intermediate/pages/page_0001.png"
    assert "jobs/job_ocr/intermediate/pages/page_0002.png" in candidate.artifact_manifest.required


def test_swallow_adapter_maps_asr_artifacts_to_segment_locators(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    store = vault / ".indbase" / "cache" / "swallow"
    job_dir = store / "jobs" / "job_asr"
    transcript = job_dir / "intermediate" / "asr" / "transcript.json"
    audio = job_dir / "intermediate" / "audio" / "normalized.wav"
    transcript.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {
                "language": "en",
                "duration_seconds": 12.5,
                "segments": [
                    {"start": 0.0, "end": 3.2, "text": "hello"},
                    {"start": 3.2, "end": 8.0, "text": "world"},
                ],
            }
        ),
        encoding="utf-8",
    )
    audio.write_bytes(b"wav")
    (job_dir / "trace.jsonl").write_text("{}", encoding="utf-8")
    (job_dir / "manifest.json").write_text('{"status":"success"}', encoding="utf-8")
    (job_dir / "ingest_document.json").write_text("{}", encoding="utf-8")

    adapter = SwallowIngestAdapter(vault_path=vault, config=SwallowIngestConfig())
    candidate = adapter._candidate_from_run_result(
        _fake_result(
            job_id="job_asr",
            markdown="# Transcript\n\n[00:00:00.000 --> 00:00:03.200] hello\n",
            primary_worker="faster_whisper_worker",
            artifacts=(
                {"type": "normalized_audio", "path": "intermediate/audio/normalized.wav"},
                {"type": "transcript_json", "path": "intermediate/asr/transcript.json"},
            ),
        )
    )

    assert [locator["kind"] for locator in candidate.source_locators] == ["asr_segment", "asr_segment"]
    assert candidate.source_locators[0]["start_seconds"] == 0.0
    assert candidate.source_locators[0]["end_seconds"] == 3.2
    assert candidate.source_locators[0]["artifact"] == "jobs/job_asr/intermediate/asr/transcript.json"
    assert candidate.source_locators[0]["normalized_audio_artifact"] == "jobs/job_asr/intermediate/audio/normalized.wav"
    assert "jobs/job_asr/intermediate/audio/normalized.wav" in candidate.artifact_manifest.required


def _fake_result(*, job_id: str, markdown: str, primary_worker: str, artifacts: tuple[dict[str, str], ...]):
    return SimpleNamespace(
        job=SimpleNamespace(id=job_id, job_dir=f"jobs/{job_id}"),
        document=SimpleNamespace(
            raw_id=f"raw_{job_id}",
            id=f"doc_{job_id}",
            content=SimpleNamespace(title=job_id, markdown=markdown, language=None),
            provenance=SimpleNamespace(
                primary_worker=primary_worker,
                worker_version="0.1.0",
                worker_chain=(f"{primary_worker}@0.1.0", "quality_checker@0.1.0"),
                trace_path=f"jobs/{job_id}/trace.jsonl",
                artifacts=list(artifacts),
            ),
            quality=SimpleNamespace(score=0.95, warnings=[]),
        ),
        manifest_path=f"jobs/{job_id}/manifest.json",
        ingest_document_path=f"jobs/{job_id}/ingest_document.json",
        trace_path=f"jobs/{job_id}/trace.jsonl",
    )
