"""Installed-wheel packaging smoke for provider integration modules."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

from provider_smoke_common import build_wheel, clean_env, create_venv


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="indbase-wheel-smoke-") as raw_tmp:
        tmp = Path(raw_tmp)
        wheel = build_wheel(tmp)
        venv_python = create_venv(tmp / "venv")
        env = clean_env()
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--no-deps", str(wheel)],
            cwd=tmp,
            env=env,
            check=True,
        )
        script = textwrap.dedent(
            """
            from importlib import import_module
            from importlib.resources import files

            for name in (
                "indbase_core",
                "indbase_cli",
                "indbase_agent",
                "indbase_integrations",
                "indbase_integrations.swallow",
                "indbase_integrations.transition",
            ):
                import_module(name)

            assert files("indbase_core.migrations").joinpath("0013_provider_failure_class.sql").is_file()
            assert files("indbase_core.migrations").joinpath("0014_source_fts_lineage.sql").is_file()
            assert files("indbase_core.transition_templates").joinpath("transition-bridge.mjs").is_file()
            assert files("indbase_agent").joinpath("manifest.json").is_file()
            """
        )
        subprocess.run([str(venv_python), "-c", script], cwd=tmp, env=env, check=True)
    print("PROVIDER_INSTALLED_WHEEL_SMOKE: ok")


if __name__ == "__main__":
    sys.exit(main())
