#!/usr/bin/env python3
"""Run the unchanged Sizey source in an isolated result directory."""

from __future__ import annotations

import argparse
import __future__
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common import ROOT, load_config, write_json


class FutureAnnotationsLoader(importlib.machinery.SourceFileLoader):
    """Compile Python 3.10-style annotations under the available Python 3.9."""

    def source_to_code(self, data, path, *, _optimize=-1):
        return compile(
            data,
            path,
            "exec",
            flags=__future__.annotations.compiler_flag,
            dont_inherit=True,
            optimize=_optimize,
        )


class SizeySourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self, source_root: Path):
        self.source_root = source_root.resolve()

    def find_spec(self, fullname, path=None, target=None):
        search_path = path if path is not None else [str(self.source_root)]
        spec = importlib.machinery.PathFinder.find_spec(fullname, search_path)
        if (
            spec is None
            or spec.origin is None
            or not spec.origin.endswith(".py")
        ):
            return spec
        origin = Path(spec.origin).resolve()
        if self.source_root not in origin.parents:
            return spec
        spec.loader = FutureAnnotationsLoader(fullname, str(origin))
        return spec


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument(
        "--cohort",
        choices=("primary", "extended"),
        default="primary",
    )
    parser.add_argument(
        "--execute-main",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def execute_sizey_main(args: argparse.Namespace) -> None:
    config = load_config()
    source_root = (ROOT / config["sizey_source"]).resolve()
    dependency_root = ROOT / "vendor" / "python"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(dependency_root))
    sys.path.insert(0, str(source_root))
    sys.meta_path.insert(0, SizeySourceFinder(source_root))
    main_path = source_root / "main.py"
    source = main_path.read_bytes()
    code = compile(
        source,
        str(main_path),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    sys.argv = [
        str(main_path),
        f"trace_{args.workflow}.csv",
        str(config["sizey"]["alpha"]),
        str(config["sizey"]["softmax"]),
        str(config["sizey"]["error_metric"]),
        str(args.seed),
    ]
    namespace = {
        "__name__": "__main__",
        "__file__": str(main_path),
        "__package__": None,
        "__cached__": None,
    }
    exec(code, namespace)


def launch_isolated(args: argparse.Namespace) -> None:
    config = load_config()
    input_path = (
        ROOT
        / "data"
        / "sizey_inputs"
        / args.cohort
        / f"trace_{args.workflow}.csv"
    )
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    run_root = (
        ROOT
        / "results"
        / "raw_sizey"
        / args.cohort
        / f"seed_{args.seed}"
        / args.workflow
    )
    work_root = run_root / "work"
    result_root = work_root / "results"
    data_root = work_root / "data"
    log_root = ROOT / "results" / "logs" / "sizey"
    result_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    local_input = work_root / input_path.name
    if local_input.exists() or local_input.is_symlink():
        local_input.unlink()
    local_input.symlink_to(input_path)
    baseline_input = data_root / input_path.name
    if baseline_input.exists() or baseline_input.is_symlink():
        baseline_input.unlink()
    baseline_input.symlink_to(input_path)

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--workflow",
        args.workflow,
        "--seed",
        str(args.seed),
        "--cohort",
        args.cohort,
        "--execute-main",
    ]
    environment = os.environ.copy()
    dependency_root = ROOT / "vendor" / "python"
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment.update(
        {
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": (
                str(dependency_root)
                if not existing_pythonpath
                else f"{dependency_root}:{existing_pythonpath}"
            ),
        }
    )
    log_path = log_root / f"{args.cohort}_{args.workflow}_{args.seed}.log"
    start = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.run(
            command,
            cwd=work_root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    elapsed = time.time() - start
    run_manifest = {
        "workflow": args.workflow,
        "seed": args.seed,
        "cohort": args.cohort,
        "command": command,
        "sizey_source": config["sizey_source"],
        "sizey_git_commit": "e0dd09f09cd2bf5905c7143792e3500c948eb386",
        "source_modified": False,
        "python39_compatibility": (
            "source compiled with future-annotations flag; algorithm source "
            "bytes are unchanged"
        ),
        "return_code": process.returncode,
        "elapsed_seconds": elapsed,
        "log": str(log_path),
        "result_directory": str(result_root),
    }
    write_json(run_root / "run_manifest.json", run_manifest)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)
    print(json.dumps(run_manifest, indent=2))


def main() -> None:
    args = arguments()
    if args.execute_main:
        execute_sizey_main(args)
    else:
        launch_isolated(args)


if __name__ == "__main__":
    main()
