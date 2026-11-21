"""Guarded operator CLI: python -m kkpilot.cloud --help."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import sys

from .config import load_config, offline_report, validate_config
from .contracts import CloudError


def main(argv=None):
    parser = argparse.ArgumentParser(description="KK operator pilot; no external action without --execute and approved configuration")
    parser.add_argument("command", choices=("plan", "preflight", "enqueue", "publish-outbox", "worker-once", "worker-run", "status"))
    parser.add_argument("--config", type=Path, required=True, help="Private explicit scope/approval JSON; no inline secrets")
    parser.add_argument("--execute", action="store_true", help="Allow the selected scoped cloud operation")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--max-jobs", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command in ("plan", "preflight") or not args.execute:
            report = offline_report(config, args.command)
            if args.command == "preflight":
                report["optional_packages_present"] = {name: importlib.util.find_spec(name) is not None for name in ("oci", "oracledb", "PIL")}
                report["python_supported"] = sys.version_info >= (3, 11)
            print(json.dumps(report, indent=2))
            return 0
        config = validate_config(config)
        if args.command == "enqueue" and (args.image is None or not args.case_id or not args.idempotency_key):
            raise CloudError("ENQUEUE_ARGUMENTS_REQUIRED")
        if not 1 <= args.max_jobs <= config["max_requests"]:
            raise CloudError("MAX_JOBS_OUTSIDE_APPROVED_SCOPE")
        from .clients import build_live
        from .workflow import Workflow
        repository, objects, queue = build_live(config, execute=True)
        if args.command == "status":
            result = repository.scope_status(config["scope_id"])
        else:
            repository.ensure_scope({name: config[name] for name in ("scope_id", "config_sha256", "max_requests")})
            workflow = Workflow(config, repository, objects, queue)
            if args.command == "enqueue":
                result = workflow.enqueue(args.image, args.case_id, args.idempotency_key)
            elif args.command == "publish-outbox":
                result = workflow.publish_once()
            elif args.command == "worker-once":
                result = workflow.worker_once()
            else:
                result = workflow.run(args.max_jobs)
        print(json.dumps(result, indent=2, allow_nan=False))
        failures = {"publish_failed", "publish_lease_lost", "failed", "lease_lost", "failure_unrecorded",
                    "ack_failed", "rejected", "budget_exhausted", "not_found", "scope_mismatch", "claim_rejected"}
        if result.get("outcome") in failures or any(result.get("counts", {}).get(name, 0) for name in failures):
            return 2
        return 0
    except CloudError as exc:
        print(json.dumps({"status": "stopped", "code": exc.code, "retryable": exc.retryable}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"status": "stopped", "code": "CLOUD_OPERATOR_FAILED"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
