from __future__ import annotations

import argparse
import asyncio
import json
import signal
from pathlib import Path
from typing import Any

from .application.services import Memory
from .bootstrap import build_runtime, build_worker


def output(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Code-first durable Agent framework")
    root.add_argument("--config", default=None)
    root.add_argument("--data-dir", default=None)
    root.add_argument("--module", action="append", default=None)
    root.add_argument("--tenant", default="local")
    root.add_argument("--actor", default="local-engineer")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("submit", "run"):
        command = commands.add_parser(name)
        command.add_argument("workflow")
        command.add_argument("--task", required=True)
        command.add_argument("--key", required=True)
        command.add_argument("--input", type=Path, required=True)
    for name in ("status", "resume", "cancel", "events", "approvals", "invocations"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        if name == "events":
            command.add_argument("--after", type=int, default=0)
    command = commands.add_parser("approve")
    command.add_argument("approval_id")
    command.add_argument("--reject", action="store_true")
    command.add_argument("--edited", type=Path)
    command.add_argument("--version", type=int, default=1)
    command.add_argument("--role", action="append", default=["approver"])
    command = commands.add_parser("reconcile")
    command.add_argument("invocation_id")
    command.add_argument("--retry", action="store_true")
    command.add_argument("--output", type=Path)
    command = commands.add_parser("memories")
    command.add_argument("namespace")
    command = commands.add_parser("review-memory")
    command.add_argument("candidate_id")
    command.add_argument("--reject", action="store_true")
    command.add_argument("--version", required=True, type=int)
    commands.add_parser("workflows")
    commands.add_parser("worker")
    return root


async def execute(args: argparse.Namespace) -> None:
    runtime = await build_runtime(
        config_path=args.config,
        data_dir=args.data_dir,
        modules=tuple(args.module) if args.module else None,
    )
    try:
        if args.command in ("submit", "run"):
            run = runtime.submit(
                args.tenant,
                args.task,
                args.workflow,
                json.loads(args.input.read_text(encoding="utf-8")),
                idempotency_key=args.key,
                actor=args.actor,
            )
            output(await runtime.execute(args.tenant, run["id"]) if args.command == "run" else run)
        elif args.command == "resume":
            output(await runtime.execute(args.tenant, args.run_id))
        elif args.command == "status":
            output(runtime.get(args.tenant, args.run_id))
        elif args.command == "cancel":
            runtime.cancel(args.tenant, args.run_id, args.actor)
            output(runtime.get(args.tenant, args.run_id))
        elif args.command == "events":
            runtime.get(args.tenant, args.run_id)
            output(runtime.repository.events(args.tenant, args.run_id, args.after))
        elif args.command in ("approvals", "invocations"):
            runtime.get(args.tenant, args.run_id)
            kind = "approval" if args.command == "approvals" else "invocation"
            output(
                [
                    record
                    for record in runtime.repository.scan(args.tenant, kind)
                    if record["run_id"] == args.run_id
                ]
            )
        elif args.command == "approve":
            runtime.approve(
                args.tenant,
                args.approval_id,
                actor=args.actor,
                roles=args.role,
                expected_version=args.version,
                accept=not args.reject,
                edited=json.loads(args.edited.read_text()) if args.edited else None,
            )
            output({"status": "decided"})
        elif args.command == "reconcile":
            if not args.retry and not args.output:
                raise ValueError("Reconciliation requires --retry or a verified --output file")
            runtime.reconcile(
                args.tenant,
                args.invocation_id,
                actor=args.actor,
                retry=args.retry,
                output=json.loads(args.output.read_text()) if args.output else None,
            )
            output({"status": "reconciled"})
        elif args.command == "memories":
            output(
                [
                    record
                    for record in runtime.repository.scan(args.tenant, "memory")
                    if record["namespace"] == args.namespace
                ]
            )
        elif args.command == "review-memory":
            Memory(runtime.repository).review(
                args.tenant,
                args.candidate_id,
                actor=args.actor,
                roles=["memory_reviewer"],
                accept=not args.reject,
                expected_version=args.version,
            )
            output({"status": "reviewed"})
        elif args.command == "workflows":
            output({name: {"version": version} for name, (version, _) in runtime.workflows.items()})
        elif args.command == "worker":
            worker = build_worker(runtime)
            loop = asyncio.get_running_loop()
            for event in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(event, worker.stopping.set)
            worker.start()
            try:
                await worker.stopping.wait()
            finally:
                await worker.stop()
                for event in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(event)
    finally:
        runtime.repository.close()


def main() -> None:
    args = parser().parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
