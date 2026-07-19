"""The image worker's stdout must carry ONLY the JSON result.

The backends (mflux/mlx) print progress + a native "⚠️  Model is pre-quantized…"
line straight to stdout. The daemon runs ``json.loads(proc.stdout)`` on the
worker's whole stdout, so any leaked backend line breaks the async image job.
The worker redirects generation-time output (Python- and fd-level) to stderr.

Run as a real subprocess: in-process, pytest replaces ``sys.stdout`` with its own
capture object, so the worker's fd-level redirect can't be observed faithfully.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap


def test_worker_stdout_is_pure_json_despite_backend_noise() -> None:
    # A fake pipeline that emits both Python-level and native (fd 1) noise, then
    # returns a result dict — the worker must keep all noise off stdout.
    driver = textwrap.dedent(
        """
        import os
        import capabledeputy.mcp_servers.image_gen_worker as worker
        import capabledeputy.mcp_servers._image_pipeline as pipeline

        def noisy_generate_image(**_kwargs):
            print("mflux: loading model", flush=True)
            os.write(1, "WARN pre-quantized 4-bit\\n".encode())
            return {"ok": True, "markdown": "![x](/p.png)", "image_path": "/p.png"}

        pipeline.generate_image = noisy_generate_image
        pipeline.load_image_gen_config = lambda **_kwargs: {}
        worker.main()
        """,
    )
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        input='{"prompt": "x"}',
        capture_output=True,
        text=True,
        check=True,
    )

    # Whole stdout parses as JSON — exactly what image_ops_handlers.py does.
    assert json.loads(proc.stdout) == {
        "ok": True,
        "markdown": "![x](/p.png)",
        "image_path": "/p.png",
    }
    # The noise was diverted to stderr, never stdout.
    assert "pre-quantized" in proc.stderr
    assert "loading model" in proc.stderr
    assert "pre-quantized" not in proc.stdout
    assert "loading model" not in proc.stdout
