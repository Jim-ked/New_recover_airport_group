from __future__ import annotations

import argparse
import json

from .compiler import build
from .excel_reader import read_excel
from .models import ScenarioToolError
from .validator import validate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='V1.4.1 Excel 独立场景库工具（本地运行）')
    subcommands = parser.add_subparsers(dest='command', required=True)
    for command in ('validate', 'build'):
        sub = subcommands.add_parser(command)
        sub.add_argument('excel', help='V1.4.1 .xlsx 工作簿路径')
    args = parser.parse_args(argv)
    if args.command == 'validate':
        result = validate(read_excel(args.excel))
        print(json.dumps({'ok': result.ok, 'issues': [i.to_dict() for i in result.issues]}, ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    try:
        path = build(args.excel)
    except ScenarioToolError as exc:
        print(json.dumps({'ok': False, 'issues': [i.to_dict() for i in exc.issues]}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({'ok': True, 'database': str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
