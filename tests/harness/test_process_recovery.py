import json
import os
import subprocess
import sys
import textwrap


def test_process_crash_requires_reconciliation_and_does_not_repeat_effect(tmp_path):
    script = textwrap.dedent("""
        import asyncio
        import json
        import os
        from pathlib import Path
        from app.harness.kernel import AgentDefinition, Runtime, SQLiteRepository, Step

        root = Path(os.environ["RECOVERY_ROOT"])
        repository = SQLiteRepository(root / "state.sqlite")
        runtime = Runtime(repository, workspace_root=root / "workspaces", lease_seconds=1)

        def work(value, context):
            marker = root / "external_effect.txt"
            count = int(marker.read_text()) if marker.exists() else 0
            marker.write_text(str(count + 1))
            os._exit(70)

        runtime.register("test", Step("work", AgentDefinition("work", handler=work)))

        async def main():
            run = runtime.submit("tenant", "task", "test", {}, idempotency_key="one")
            if os.environ["RECOVERY_PHASE"] == "crash":
                await runtime.execute("tenant", run["id"])
            else:
                run["lease_until"] = 0
                repository.put("tenant", "run", run["id"], run)
                uncertain = await runtime.execute("tenant", run["id"])
                invocation = next(repository.scan("tenant", "invocation"))
                runtime.reconcile("tenant", invocation["id"], actor="operator", output="verified")
                final = await runtime.execute("tenant", run["id"])
                print(json.dumps({"before": uncertain["status"], "after": final["status"],
                                  "output": final["output"]}))

        asyncio.run(main())
    """)
    environment = {**os.environ, "RECOVERY_ROOT": str(tmp_path), "RECOVERY_PHASE": "crash"}
    crashed = subprocess.run(
        [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=20
    )
    assert crashed.returncode == 70, crashed.stderr
    environment["RECOVERY_PHASE"] = "recover"
    recovered = subprocess.run(
        [sys.executable, "-c", script], env=environment, capture_output=True, text=True, timeout=20
    )
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {
        "before": "uncertain",
        "after": "succeeded",
        "output": "verified",
    }
    assert (tmp_path / "external_effect.txt").read_text() == "1"
