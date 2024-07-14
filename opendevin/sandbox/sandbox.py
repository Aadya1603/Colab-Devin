#Rewritten sandbox.py to use subprocess instead of docker

import atexit
import os
import select
import subprocess
import sys
import time
import uuid
from collections import namedtuple
from typing import Dict, List, Tuple

import concurrent.futures

from opendevin import config

InputType = namedtuple("InputType", ["content"])
OutputType = namedtuple("OutputType", ["content"])

RUN_AS_DEVIN = config.get("RUN_AS_DEVIN").lower() != "false"
USER_ID = 1000
if config.get_or_none("SANDBOX_USER_ID") is not None:
    USER_ID = int(config.get_or_default("SANDBOX_USER_ID", ""))
elif hasattr(os, "getuid"):
    USER_ID = os.getuid()


class BackgroundCommand:
    def __init__(self, id: int, command: str, process: subprocess.Popen):
        self.id = id
        self.command = command
        self.process = process

    def read_logs(self) -> str:
        try:
            output, _ = self.process.communicate(timeout=0.1)
            return output.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return ""


class SubprocessInteractive:
    closed = False
    cur_background_id = 0
    background_commands: Dict[int, BackgroundCommand] = {}

    def __init__(
        self,
        workspace_dir: str | None = None,
        timeout: int = 120,
        id: str | None = None,
    ):
        if id is not None:
            self.instance_id = id
        else:
            self.instance_id = str(uuid.uuid4())
        if workspace_dir is not None:
            os.makedirs(workspace_dir, exist_ok=True)
            self.workspace_dir = os.path.abspath(workspace_dir)
        else:
            self.workspace_dir = os.getcwd()
            print(f"Workspace unspecified, using current directory: {workspace_dir}")

        self.timeout: int = timeout

        atexit.register(self.cleanup)

    def get_exec_cmd(self, cmd: str) -> List[str]:
        return ["/bin/bash", "-c", cmd]

    def read_logs(self, id) -> str:
        if id not in self.background_commands:
            raise ValueError("Invalid background command id")
        bg_cmd = self.background_commands[id]
        return bg_cmd.read_logs()

    def execute(self, cmd: str) -> Tuple[int, str]:
        def run_command(command):
            return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.workspace_dir, timeout=self.timeout)

        try:
            result = run_command(self.get_exec_cmd(cmd))
            return result.returncode, result.stdout.decode("utf-8")
        except subprocess.TimeoutExpired:
            print("Command timed out, killing process...")
            return -1, f"Command: \"{cmd}\" timed out"

    def execute_in_background(self, cmd: str) -> BackgroundCommand:
        process = subprocess.Popen(self.get_exec_cmd(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.workspace_dir)
        bg_cmd = BackgroundCommand(self.cur_background_id, cmd, process)
        self.background_commands[bg_cmd.id] = bg_cmd
        self.cur_background_id += 1
        return bg_cmd

    def kill_background(self, id: int) -> BackgroundCommand:
        if id not in self.background_commands:
            raise ValueError("Invalid background command id")
        bg_cmd = self.background_commands[id]
        bg_cmd.process.terminate()
        bg_cmd.process.wait()
        self.background_commands.pop(id)
        return bg_cmd

    def close(self):
        self.closed = True

    def cleanup(self):
        if self.closed:
            return
        for bg_cmd in self.background_commands.values():
            bg_cmd.process.terminate()
            bg_cmd.process.wait()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Interactive subprocess execution")
    parser.add_argument(
        "-d",
        "--directory",
        type=str,
        default=None,
        help="The working directory for the subprocess execution.",
    )
    args = parser.parse_args()

    try:
        subprocess_interactive = SubprocessInteractive(
            workspace_dir=args.directory,
        )
    except Exception as e:
        print(f"Failed to initialize interactive subprocess: {e}")
        sys.exit(1)

    print("Interactive subprocess execution started. Type 'exit' or use Ctrl+C to exit.")

    bg_cmd = subprocess_interactive.execute_in_background(
        "while true; do echo 'dot ' && sleep 1; done"
    )

    sys.stdout.flush()
    try:
        while True:
            try:
                user_input = input(">>> ")
            except EOFError:
                print("\nExiting...")
                break
            if user_input.lower() == "exit":
                print("Exiting...")
                break
            if user_input.lower() == "kill":
                subprocess_interactive.kill_background(bg_cmd.id)
                print("Background process killed")
                continue
            exit_code, output = subprocess_interactive.execute(user_input)
            print("Exit code:", exit_code)
            print(output + "\n", end="")
            if bg_cmd.id in subprocess_interactive.background_commands:
                logs = subprocess_interactive.read_logs(bg_cmd.id)
                print("Background logs:", logs, "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nExiting...")
    subprocess_interactive.close()
