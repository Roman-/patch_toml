#!/usr/bin/env python3
import difflib
import shutil
import subprocess
import sys
from pathlib import Path


def run_case(name, input_file, expected_file, args):
    input_path = Path(input_file)
    expected_path = Path(expected_file)
    output_path = Path(f"/tmp/{name}.toml")
    # Prepare output by copying input so there's something to patch
    shutil.copy(input_path, output_path)
    cmd = [sys.executable, "patch_toml.py", str(output_path)] + list(args)
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.stdout:
        print(result.stdout)
    # Compare files
    with output_path.open() as got, expected_path.open() as exp:
        got_lines = got.readlines()
        exp_lines = exp.readlines()
    if got_lines != exp_lines:
        print("Expected vs Actual diff:")
        diff = difflib.unified_diff(exp_lines, got_lines, fromfile="expected", tofile="actual")
        print(''.join(diff))
        raise AssertionError(f"{name} has value mismatch")
    print(f"{name}: OK")


def main():
    tests = [
        (
            "00_output_with_int_changed_to_42",
            "tests/00_input.toml",
            "tests/00_output_with_int_changed_to_42.toml",
            ["set", "simplest_config_possible.int_value", "42"],
        ),
        (
            "01_output_change_logger_levels_to_4",
            "tests/01_input.toml",
            "tests/01_output_change_logger_levels_to_4.toml",
            ["set", "logger.stdout_level", "4", "set", "logger.file_level", "4"],
        ),
        (
            "01_output_change_stdout_level_to_4",
            "tests/01_input.toml",
            "tests/01_output_change_stdout_level_to_4.toml",
            ["set", "logger.stdout_level", "4"],
        ),
    ]

    for test in tests:
        run_case(*test)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(e)
        sys.exit(1)
