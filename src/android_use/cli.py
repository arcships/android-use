import argparse
import json
import sys

from jsonschema import ValidationError, validate

from .backend import state_dir
from .runtime import Runtime, save_images
from .server import SCHEMAS


def main():
    parser = argparse.ArgumentParser(description="Android Use — local CLI and MCP server")
    parser.add_argument(
        "--serial", help="Explicit ADB device serial; never silently switches devices"
    )
    parser.add_argument("--output-dir", help="Where screenshot files are saved")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="Run MCP stdio server")
    sub.add_parser("tools", help="Print tool schemas")
    for name in SCHEMAS:
        command = sub.add_parser(name, help=f"Call aua_{name}")
        command.add_argument("args", nargs="?", default="{}", help="JSON arguments")
    options = parser.parse_args()
    if options.command == "serve":
        if options.serial:
            import os

            os.environ["ANDROID_SERIAL"] = options.serial
        from .server import main as serve

        serve()
        return
    if options.command == "tools":
        print(json.dumps(SCHEMAS, ensure_ascii=False, indent=2))
        return
    try:
        arguments = json.loads(options.args)
        validate(arguments, SCHEMAS[options.command])
    except (json.JSONDecodeError, ValidationError):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_arguments",
                        "message": "Arguments do not match tool schema",
                    },
                }
            )
        )
        sys.exit(2)
    result = Runtime(options.serial).call(options.command, **arguments)
    clean = save_images(result, options.output_dir or state_dir() / "screenshots")
    print(json.dumps(clean, ensure_ascii=False, indent=2))
    if not clean["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
