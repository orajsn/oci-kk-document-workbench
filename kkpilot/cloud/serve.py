"""Explicit entrypoint for separate private web, outbox and worker services.

Without --execute this command reports offline readiness and does not import OCI
or Oracle drivers, bind a listener, read credentials, or call remote services.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading

from .config import load_config, offline_report, validate_config
from .contracts import CloudError
from .web import CloudPilotServer, validate_reviewer
from ..telemetry import safe_workflow_timing


def run_loop(workflow, role: str, stop: threading.Event, *, interval_seconds: float = 1):
    """Run one bounded operation at a time; database scope limits total attempts."""
    if role not in {"dispatcher", "worker"} or not 0.1 <= interval_seconds <= 60:
        raise CloudError("SERVICE_LOOP_CONFIGURATION_INVALID")
    operation = workflow.publish_once if role == "dispatcher" else workflow.worker_once
    while not stop.is_set():
        result = operation()
        outcome = result.get("outcome")
        if outcome == "budget_exhausted":
            raise CloudError("APPROVED_SCOPE_BUDGET_EXHAUSTED")
        # Never log records, request payloads, source paths or provider responses.
        if outcome not in {"empty", "not_ready", "busy"}:
            safe_outcomes = {"published", "publish_failed", "publish_lease_lost", "needs_review", "failed",
                             "retry_scheduled", "lease_lost", "ack_failed", "failure_unrecorded",
                             "rejected", "duplicate_terminal", "not_found", "scope_mismatch", "claim_rejected"}
            event = {"service": role, "outcome": outcome if outcome in safe_outcomes else "operation_finished"}
            timing = safe_workflow_timing(result.get("workflow_timing"))
            if role == "worker" and timing:
                event["workflow_timing"] = timing
            print(json.dumps(event), flush=True)
        delay = interval_seconds if outcome in {"published", "needs_review", "empty", "duplicate_terminal"} else workflow.cfg["backoff_seconds"]
        stop.wait(delay)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private KK cloud pilot; use an SSH tunnel on port 8765")
    parser.add_argument("role", choices=("web", "dispatcher", "worker"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reviewer-identity", help="Named SSH operator recorded in cloud review history (web only)")
    parser.add_argument("--port", type=int, default=8765, help="Loopback port; the SSH tunnel must use the same local port")
    args = parser.parse_args(argv)
    server = None
    try:
        config = load_config(args.config)
        if not args.execute:
            report = offline_report(config, "serve-" + args.role)
            report["runtime"] = "private_cloud"
            report["private_access"] = "ssh_tunnel"
            report["limitation"] = "No listener or live clients started; explicit execution and approved configuration are required."
            print(json.dumps(report, indent=2))
            return 0
        config = validate_config(config)
        if not 1 <= args.port <= 65535:
            raise CloudError("LOOPBACK_PORT_INVALID")
        if args.role == "web":
            validate_reviewer(args.reviewer_identity)
        from .clients import build_live
        from .workflow import Workflow
        repository, objects, queue = build_live(config, execute=True)
        repository.ensure_scope({name: config[name] for name in ("scope_id", "config_sha256", "max_requests")})
        workflow = Workflow(config, repository, objects, queue)
        stop = threading.Event()
        previous_signals = {}
        def stop_service(signum, frame):
            stop.set()
            if server is not None:
                # shutdown must run outside the serve_forever thread.
                threading.Thread(target=server.shutdown, daemon=True).start()
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_signals[signum] = signal.signal(signum, stop_service)
        try:
            if args.role == "web":
                from .review_repository import CloudReviewRepository
                review = CloudReviewRepository(repository.connection_factory, repository.driver,
                                               config["scope_id"], args.reviewer_identity,
                                               prior_review_scope_ids=config.get("prior_review_scope_ids", []))
                server = CloudPilotServer(config, workflow, review, execute=True, port=args.port)
                print(json.dumps({"service": "web", "status": "listening", "host": "127.0.0.1", "port": args.port,
                                  "private_access": "ssh_tunnel"}), flush=True)
                if not stop.is_set():
                    server.serve_forever(poll_interval=0.25)
            else:
                run_loop(workflow, args.role, stop)
        finally:
            for signum, handler in previous_signals.items():
                signal.signal(signum, handler)
        return 0
    except CloudError as exc:
        print(json.dumps({"status": "stopped", "code": exc.code, "retryable": exc.retryable}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"status": "stopped", "code": "CLOUD_SERVICE_FAILED"}), file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
