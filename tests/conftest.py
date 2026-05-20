from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

import indbase_core.conversion as conversion_module
from indbase_core.normalizers import normalize_tier1_source, normalize_tier2_source
from indbase_core.swallow_adapter import ArtifactManifest, ConversionCandidate, SwallowProvenance, SwallowIngestAdapter


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "no_fake_swallow_conversion: run conversion with the production feature gate instead of the test fake.",
    )
    config.addinivalue_line(
        "markers",
        "transition_smoke: real Node transition bridge smoke test (requires INDBASE_TRANSITION_SMOKE=1)",
    )


@pytest.fixture(autouse=True)
def fake_swallow_file_conversion(monkeypatch, request):
    if request.node.get_closest_marker("no_fake_swallow_conversion"):
        return

    original_load_config = conversion_module._load_indbase_config_if_present

    def load_config_with_swallow_enabled(paths):
        config = original_load_config(paths)
        if config.features.swallow_ingest:
            return config
        return replace(
            config,
            features=replace(config.features, swallow_ingest=True),
            ingest=replace(
                config.ingest,
                swallow=replace(config.ingest.swallow, min_markdown_chars=1),
            ),
        )

    def convert_file_with_local_test_swallow(self, path: Path | str) -> ConversionCandidate:
        source = Path(path)
        source_type = source.suffix.lower().lstrip(".")
        if source_type in {"md", "txt", "html", "csv", "json"}:
            normalized = normalize_tier1_source(source, source_type)
        else:
            normalized = normalize_tier2_source(source, source_type)

        job_id = "test_file_" + hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:12]
        trace_rel = f"jobs/{job_id}/trace.jsonl"
        trace_path = self.store_root / trace_rel
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text('{"event":"test_swallow_file_conversion"}\n', encoding="utf-8")

        return ConversionCandidate(
            title=source.stem,
            markdown_body=normalized.markdown,
            status="success",
            quality_score=0.95,
            warnings=normalized.warnings,
            provenance=SwallowProvenance(
                swallow_job_id=job_id,
                swallow_raw_id=f"raw_{job_id}",
                swallow_document_id=f"doc_{job_id}",
                swallow_version="test",
                primary_worker="test_swallow_file_worker",
                worker_version="test",
                worker_chain=("test_swallow_file_worker@test", "quality_checker@test"),
                trace_path=trace_rel,
                manifest_path=None,
                ingest_document_path=None,
            ),
            artifact_manifest=ArtifactManifest(required=(trace_rel,)),
        )

    monkeypatch.setattr(conversion_module, "_load_indbase_config_if_present", load_config_with_swallow_enabled)
    monkeypatch.setattr(SwallowIngestAdapter, "convert_file", convert_file_with_local_test_swallow)
