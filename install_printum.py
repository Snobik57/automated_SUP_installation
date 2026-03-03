#!/usr/bin/env python3
"""Automated installer for Printum Monitoring and PrintManager.

Features:
- Supports online and offline installation flows.
- Supports separate servers for Monitoring and PrintManager.
- Applies arbitrary environment variables to install commands.
- Waits for health endpoint and reports clear errors.
- Structured JSON config for future extensibility (e.g., Selenium post-setup).
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


class InstallError(RuntimeError):
    """Installation workflow exception with actionable message."""


@dataclass
class RemoteHost:
    host: str
    user: str = "root"
    port: int = 22
    ssh_key: str | None = None

    def ssh_base(self) -> list[str]:
        cmd = ["ssh", "-p", str(self.port)]
        if self.ssh_key:
            cmd += ["-i", self.ssh_key]
        cmd.append(f"{self.user}@{self.host}")
        return cmd


@dataclass
class ModuleConfig:
    enabled: bool
    mode: str
    module_type: str
    host: RemoteHost
    env: dict[str, str]
    health_url: str | None = None
    health_timeout_sec: int = 900
    workdir: str = "/tmp"
    online_url: str | None = None
    archive_path: str | None = None
    checksum_path: str | None = None


class Runner:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run_local(self, cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        print(f"[local]$ {' '.join(shlex.quote(x) for x in cmd)}")
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        cp = subprocess.run(cmd, text=True, capture_output=True)
        if cp.stdout:
            print(cp.stdout, end="")
        if cp.stderr:
            print(cp.stderr, end="", file=sys.stderr)
        if check and cp.returncode != 0:
            raise InstallError(f"Command failed ({cp.returncode}): {' '.join(cmd)}")
        return cp

    def run_remote_script(self, host: RemoteHost, script: str) -> None:
        cmd = host.ssh_base() + ["bash", "-s"]
        print(f"[remote {host.host}] executing script")
        if self.dry_run:
            print(script)
            return
        cp = subprocess.run(cmd, text=True, input=script, capture_output=True)
        if cp.stdout:
            print(cp.stdout, end="")
        if cp.stderr:
            print(cp.stderr, end="", file=sys.stderr)
        if cp.returncode != 0:
            raise InstallError(
                f"Remote script failed on {host.host} with code {cp.returncode}."
            )


def q(value: str) -> str:
    return shlex.quote(value)


def build_env_exports(env: dict[str, str]) -> str:
    lines = [f"export {k}={q(v)}" for k, v in env.items()]
    return "\n".join(lines)


def build_online_script(cfg: ModuleConfig) -> str:
    if not cfg.online_url:
        raise InstallError(f"online_url is required for {cfg.module_type} online mode")
    env = build_env_exports(cfg.env)
    return f"""set -euo pipefail
{env}
curl -fsSL {q(cfg.online_url)} | bash -s agent
"""


def build_offline_script(cfg: ModuleConfig) -> str:
    if not cfg.archive_path:
        raise InstallError(f"archive_path is required for {cfg.module_type} offline mode")

    archive = PurePosixPath(cfg.archive_path)
    base_dir = archive.name.replace(".tar.gz", "")
    env = build_env_exports(cfg.env)

    checksum_block = ""
    if cfg.checksum_path:
        checksum_block = f"sha512sum -c {q(cfg.checksum_path)}\n"

    return f"""set -euo pipefail
mkdir -p {q(cfg.workdir)}
cd {q(cfg.workdir)}
{checksum_block}tar xvf {q(str(archive))}
cd {q(base_dir)}
chmod u+x ./install.sh
{env}
./install.sh
"""


def build_healthcheck_script(url: str, timeout: int) -> str:
    return f"""set -euo pipefail
attempts=0
max_attempts=$(( {timeout} / 5 ))
until curl --output /dev/null --silent --head --fail {q(url)}; do
  if [ "$attempts" -ge "$max_attempts" ]; then
    echo "ERROR: timeout waiting for health endpoint: {url}" >&2
    exit 1
  fi
  printf '.'
  attempts=$((attempts+1))
  sleep 5
done
printf '\nOK: service is healthy: {url}\n'
"""


def parse_module(data: dict[str, Any], module_type: str) -> ModuleConfig:
    host_data = data.get("host", {})
    host = RemoteHost(
        host=host_data["host"],
        user=host_data.get("user", "root"),
        port=int(host_data.get("port", 22)),
        ssh_key=host_data.get("ssh_key"),
    )
    return ModuleConfig(
        enabled=bool(data.get("enabled", False)),
        mode=data.get("mode", "online"),
        module_type=module_type,
        host=host,
        env={str(k): str(v) for k, v in data.get("env", {}).items()},
        health_url=data.get("health_url"),
        health_timeout_sec=int(data.get("health_timeout_sec", 900)),
        workdir=data.get("workdir", "/tmp"),
        online_url=data.get("online_url"),
        archive_path=data.get("archive_path"),
        checksum_path=data.get("checksum_path"),
    )


def run_module(runner: Runner, cfg: ModuleConfig) -> None:
    if not cfg.enabled:
        print(f"[skip] {cfg.module_type} disabled")
        return

    print(f"\n=== {cfg.module_type.upper()} install on {cfg.host.host} ({cfg.mode}) ===")
    if cfg.mode == "online":
        script = build_online_script(cfg)
    elif cfg.mode == "offline":
        script = build_offline_script(cfg)
    else:
        raise InstallError(f"Unsupported mode for {cfg.module_type}: {cfg.mode}")

    runner.run_remote_script(cfg.host, script)

    if cfg.health_url:
        print(f"[check] waiting for health: {cfg.health_url}")
        hc_script = build_healthcheck_script(cfg.health_url, cfg.health_timeout_sec)
        runner.run_remote_script(cfg.host, hc_script)


def load_config(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated installer for Printum stack")
    parser.add_argument("--config", required=True, help="Path to JSON config")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without execution")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        runner = Runner(dry_run=args.dry_run)

        monitoring = parse_module(config.get("monitoring", {}), "monitoring")
        printmanager = parse_module(config.get("printmanager", {}), "printmanager")

        run_module(runner, monitoring)
        run_module(runner, printmanager)

        post_setup = config.get("post_setup", {})
        if post_setup.get("enabled"):
            print(
                "[info] post_setup enabled. You can plug Selenium/Playwright automation here "
                "(see post_setup block in config)."
            )

        print("\nAll requested installation steps finished successfully.")
        return 0

    except KeyError as exc:
        print(f"Configuration error: missing key {exc}", file=sys.stderr)
        return 2
    except InstallError as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
