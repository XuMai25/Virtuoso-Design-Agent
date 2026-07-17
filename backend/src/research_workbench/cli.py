from __future__ import annotations

import argparse
import json
import sys

import uvicorn

from research_workbench.api.app import create_app
from research_workbench.config import AppConfig
from research_workbench.indexing.service import IndexService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-workbench", description="Research Workbench / 科研工作台"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="扫描 Vault 并启动本地服务")
    serve.add_argument("--vault", required=True, help="只读 Obsidian Vault 路径")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int)
    serve.add_argument("--data-dir")
    serve.add_argument("--no-initial-scan", action="store_true")

    check = subparsers.add_parser("check", help="无界面只读检查")
    check.add_argument("--vault", required=True)
    check.add_argument("--data-dir")
    check.add_argument("--json", action="store_true")

    report = subparsers.add_parser("export-report", help="导出只读检查报告到 reports/")
    report.add_argument("--vault", required=True)
    report.add_argument("--data-dir")

    return parser


def _config(args: argparse.Namespace) -> AppConfig:
    return AppConfig.build(
        args.vault,
        data_dir=getattr(args, "data_dir", None),
        port=getattr(args, "port", None),
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    try:
        config = _config(args)
    except (ValueError, OSError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    service = IndexService(config)
    if args.command == "serve":
        if not args.no_initial_scan:
            summary = service.scan()
            print(
                f"已只读扫描 {summary['markdown_files']} 个 Markdown；"
                f"error {summary['error']} / warning {summary['warning']} / info {summary['info']}"
            )
        app = create_app(config, service=service)
        print(f"科研工作台：http://{args.host}:{config.port}")
        uvicorn.run(app, host=args.host, port=config.port, log_level="info")
        return 0

    if args.command == "check":
        summary = service.scan()
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(
                f"扫描完成：{summary['markdown_files']} 个 Markdown；"
                f"error {summary['error']} / warning {summary['warning']} / info {summary['info']}；"
                f"失效双链 {summary['broken_links']}；无效外部路径 {summary['invalid_external_paths']}"
            )
        return 1 if summary["error"] else 0

    if args.command == "export-report":
        service.scan()
        print(service.write_report())
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
