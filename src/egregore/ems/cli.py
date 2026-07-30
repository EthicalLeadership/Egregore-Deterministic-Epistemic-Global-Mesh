"""EMS CLI — egregor model * commands.

Provides:
  egregor model list
  egregor model register <model_id> <model_path>
  egregor model serve <model_id>
  egregor model stop <model_id>
  egregor model status
  egregor model verify
  egregor model proxy          # Start the unified proxy server
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from egregore.ems.lifecycle import EmsLifecycle
from egregore.ems.registry import EmsRegistry, ModelStatus, build_registry_from_env


def _fmt_record(rec: Any) -> str:
    return (
        f"{rec.model_id:30s}  {rec.version:6s}  {rec.status.value:8s}  "
        f"{rec.tier:12s}  {rec.backend_type:10s}  {rec.parameters:8s}  {rec.model_path}"
    )


def cmd_list(registry: EmsRegistry, args: argparse.Namespace) -> int:
    records = registry.list_models(
        status=ModelStatus(args.status) if args.status else None,
        node=args.node,
        tier=args.tier,
    )
    if not records:
        print("No models registered.")
        return 0
    print(f"{'MODEL_ID':30s}  {'VER':6s}  {'STATUS':8s}  {'TIER':12s}  {'BACKEND':10s}  {'PARAMS':8s}  PATH")
    for rec in records:
        print(_fmt_record(rec))
    return 0


def cmd_register(registry: EmsRegistry, args: argparse.Namespace) -> int:
    rec = registry.register(
        model_id=args.model_id,
        model_path=args.model_path,
        version=args.version or "v1",
        backend_type=args.backend_type or "native",
        tier=args.tier or "general",
        context_length=args.ctx or 8192,
        parameters=args.parameters or "7B",
        chat_template=args.chat_template or "",
    )
    print(f"Registered {rec.model_id} @ {rec.model_path}")
    return 0


def cmd_serve(registry: EmsRegistry, args: argparse.Namespace) -> int:
    lifecycle = EmsLifecycle(registry)
    try:
        rec = lifecycle.start(args.model_id)
        print(f"Serving {rec.model_id} (Egregore native backend)")
    except Exception as exc:
        print(f"Failed to start {args.model_id}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_stop(registry: EmsRegistry, args: argparse.Namespace) -> int:
    lifecycle = EmsLifecycle(registry)
    lifecycle.stop(args.model_id)
    print(f"Stopped {args.model_id}")
    return 0


def cmd_delete(registry: EmsRegistry, args: argparse.Namespace) -> int:
    if registry.delete(args.model_id):
        print(f"Deleted {args.model_id}")
        return 0
    print(f"Model '{args.model_id}' not found", file=sys.stderr)
    return 1


def cmd_status(registry: EmsRegistry, args: argparse.Namespace) -> int:
    lifecycle = EmsLifecycle(registry)
    for model_id, health in lifecycle.health_all().items():
        status = health["status"]
        err = health.get("error", "")
        print(f"{model_id:30s}  {status:10s}  {err}")
    return 0


def cmd_verify(registry: EmsRegistry, args: argparse.Namespace) -> int:
    results = registry.verify_all()
    ok = all(v == "VERIFIED" for v in results.values())
    for model_id, result in results.items():
        print(f"{model_id:30s}  {result}")
    return 0 if ok else 1


def cmd_proxy(registry: EmsRegistry, args: argparse.Namespace) -> int:
    from egregore.ems.proxy import EmsProxy

    proxy = EmsProxy(registry, auto_start=args.auto_start)
    import uvicorn

    print(f"Starting EMS Proxy on {args.host}:{args.port}")
    uvicorn.run(proxy.app, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="egregor model", description="Egregore Model Service CLI")
    parser.add_argument("--db", default=None, help="Registry SQLite path")
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List registered models")
    p_list.add_argument("--status", choices=[s.value for s in ModelStatus], default=None)
    p_list.add_argument("--node", default=None)
    p_list.add_argument("--tier", default=None)

    # register
    p_reg = sub.add_parser("register", help="Register a model checkpoint")
    p_reg.add_argument("model_id")
    p_reg.add_argument("model_path")
    p_reg.add_argument("--version", default="v1")
    p_reg.add_argument("--backend-type", default="native")
    p_reg.add_argument("--tier", default="general")
    p_reg.add_argument("--ctx", type=int, default=8192)
    p_reg.add_argument("--parameters", default="7B")
    p_reg.add_argument("--chat-template", default="", help="Chat template key (deepseek, qwen2, chatml, raw)")

    # serve
    p_serve = sub.add_parser("serve", help="Load a model into the Egregore process")
    p_serve.add_argument("model_id")

    # stop
    p_stop = sub.add_parser("stop", help="Unload a model")
    p_stop.add_argument("model_id")

    # delete
    p_delete = sub.add_parser("delete", help="Unregister a model")
    p_delete.add_argument("model_id")

    # status
    sub.add_parser("status", help="Health-check all models")

    # verify
    sub.add_parser("verify", help="Verify model checkpoints")

    # proxy
    p_proxy = sub.add_parser("proxy", help="Start the unified inference proxy")
    p_proxy.add_argument("--host", default="0.0.0.0")
    p_proxy.add_argument("--port", type=int, default=8001)
    p_proxy.add_argument("--auto-start", action="store_true", default=True)
    p_proxy.add_argument("--no-auto-start", dest="auto_start", action="store_false")

    args = parser.parse_args(argv)
    registry = build_registry_from_env() if args.db is None else EmsRegistry(args.db)

    handlers = {
        "list": cmd_list,
        "register": cmd_register,
        "serve": cmd_serve,
        "stop": cmd_stop,
        "delete": cmd_delete,
        "status": cmd_status,
        "verify": cmd_verify,
        "proxy": cmd_proxy,
    }
    return handlers[args.command](registry, args)


def main_model(argv: list[str] | None = None) -> int:
    """Entry point for the `egregor model ...` console script.

    Supports both:
        egregor register ...
        egregor model register ...
    """
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "model":
        argv = argv[1:]
    return main(argv)


if __name__ == "__main__":
    sys.exit(main())
