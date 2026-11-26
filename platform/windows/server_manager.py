"""GF FMR Windows server manager.

The native launcher starts this module elevated.  The manager deliberately
keeps PostgreSQL, Ollama and the API on localhost; only Vite/Web is exposed
on the LAN.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox, ttk
from urllib.error import URLError
from urllib.request import Request, urlopen


CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Use a second loopback address for manager health probes.  The original
# 127.0.0.1 endpoint accumulated thousands of TIME_WAIT entries during the
# old polling storm; 127.0.0.2 reaches the same local listeners without
# competing for those stale 4-tuples.
HEALTH_HOST = "127.0.0.2"


class ManagerError(RuntimeError):
    """A user-facing startup or shutdown error."""


class ServerManager:
    """Own the ordered Windows server lifecycle."""

    def __init__(self, project_root: Path, publish):
        self.root = project_root.resolve()
        self.state_root = self.root / "state"
        self.runtime = self.state_root / "runtime"
        self.platform_runtime = self.state_root / "platform-runtime"
        self.python = self.state_root / "dependencies" / "venv" / "Scripts" / "python.exe"
        self.node = self.state_root / "dependencies" / "node22" / "node.exe"
        self.vite = self.root / "platform" / "web" / "node_modules" / "vite" / "bin" / "vite.js"
        self.ollama = self.state_root / "dependencies" / "ollama" / "ollama.exe"
        self.pg_root = self.state_root / "platform-runtime" / "postgresql17"
        self.pg_bin = self.pg_root / "pgsql" / "bin"
        self.pg_data = self.pg_root / "data"
        self.pg_ctl = self.pg_bin / "pg_ctl.exe"
        self.postgres = self.pg_bin / "postgres.exe"
        self.psql = self.pg_bin / "psql.exe"
        self.password_file = self.runtime / "postgres-superuser.txt"
        self.publish = publish
        self.processes: dict[str, subprocess.Popen] = {}
        self.owned: set[str] = set()
        self.service_started = False
        self.busy = False
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.manager_log = self.runtime / "server-manager.log"

    def emit(self, kind: str, payload) -> None:
        try:
            self.publish(kind, payload)
        except Exception:
            # A diagnostic callback (for example a console using a legacy
            # code page) must never abort the actual service lifecycle.
            pass

    def log(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.manager_log.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} {message}\n")
        except OSError:
            pass
        self.emit("log", message)

    def state(self, name: str, status: str, detail: str = "") -> None:
        self.emit("state", (name, status, detail))

    def env(self) -> dict[str, str]:
        if not self.password_file.exists():
            raise ManagerError(f"PostgreSQL 密码文件不存在：{self.password_file}")
        password = self.password_file.read_text(encoding="utf-8").strip()
        result = os.environ.copy()
        result.update(
            {
                "FACTSHEET_PROJECT_ROOT": str(self.root),
                "FACTSHEET_STATE_ROOT": str(self.state_root),
                "FACTSHEET_DATA_ROOT": str(self.state_root / "data"),
                "FACTSHEET_RUNTIME_ROOT": str(self.runtime),
                "PLATFORM_RUNTIME_ROOT": str(self.platform_runtime),
                "FACTSHEET_CONFIG": str(self.state_root / "config.ini"),
                "PYTHONPATH": os.pathsep.join(
                    [str(self.root / "platform" / "api"), str(self.root / "code")]
                ),
                "PLATFORM_DATABASE_URL": (
                    f"postgresql+psycopg://postgres:{password}@127.0.0.1:5432/fund_research"
                ),
                "PLATFORM_API_HOST": "127.0.0.1",
                "PLATFORM_API_PORT": "8000",
                "PLATFORM_WEB_HOST": "0.0.0.0",
                "PLATFORM_WEB_PORT": "5173",
                "PLATFORM_API_PROXY_TARGET": "http://127.0.0.1:8000",
                "PLAYWRIGHT_BROWSERS_PATH": str(self.runtime / "ms-playwright"),
                "PLATFORM_BROWSER_PREFLIGHT": "required",
                "PYTHONUNBUFFERED": "1",
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_NUM_PARALLEL": "2",
                "OLLAMA_MODELS": str(self.state_root / "dependencies" / "ollama" / "models"),
                "OLLAMA_NO_CLOUD": "1",
            }
        )
        return result

    @staticmethod
    def run(command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            env=env,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )

    def spawn(self, name: str, command: list[str], cwd: Path, logfile: Path, env: dict[str, str]) -> None:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        handle = logfile.open("a", encoding="utf-8", errors="replace")
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        )
        handle.close()
        self.processes[name] = process
        self.owned.add(name)
        self.log(f"{name} 已启动，PID {process.pid}")

    @staticmethod
    def port_open(port: int, host: str = "127.0.0.1") -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    @staticmethod
    def http_json(url: str, timeout: float = 2.0) -> dict:
        request = Request(url, headers={"User-Agent": "GF-FMR-Server/1.0"})
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def http_ok(url: str, timeout: float = 2.0) -> bool:
        try:
            request = Request(url, headers={"User-Agent": "GF-FMR-Server/1.0"})
            with urlopen(request, timeout=timeout) as response:
                return 200 <= response.status < 400
        except (OSError, URLError):
            return False

    def wait_for(self, check, timeout: float, description: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel_event.is_set():
                raise ManagerError("操作已取消")
            if check():
                return
            time.sleep(0.5)
        raise ManagerError(f"健康检查超时：{description}")

    def service_state(self, name: str) -> str:
        result = self.run(["sc.exe", "query", name])
        output = f"{result.stdout}\n{result.stderr}"
        if "RUNNING" in output:
            return "RUNNING"
        if "STOPPED" in output:
            return "STOPPED"
        if "1060" in output or "does not exist" in output.lower():
            return "MISSING"
        return "UNKNOWN"

    def start_postgres(self) -> None:
        self.state("PostgreSQL", "starting")
        if self.port_open(5432):
            self.state("PostgreSQL", "healthy", "5432 已有数据库服务")
            return
        service = "postgresql-x64-17-gf"
        if self.service_state(service) in {"STOPPED", "UNKNOWN"}:
            result = self.run(["sc.exe", "start", service])
            if result.returncode == 0:
                self.service_started = True
                self.log("正在启动 PostgreSQL Windows 服务")
                try:
                    self.wait_for(lambda: self.port_open(5432), 45, "PostgreSQL Windows 服务")
                except ManagerError:
                    self.log("PostgreSQL 服务未在预期时间内监听，继续检查本地运行时")
        if not self.port_open(5432) and self.postgres.exists():
            pid_file = self.pg_data / "postmaster.pid"
            if pid_file.exists() and self.pg_ctl.exists():
                status = self.run([str(self.pg_ctl), "status", "-D", str(self.pg_data)])
                if status.returncode != 0:
                    pid_file.unlink(missing_ok=True)
            self.spawn(
                "PostgreSQL",
                [str(self.postgres), "-D", str(self.pg_data), "-p", "5432"],
                self.pg_data,
                self.runtime / "postgres-manager.log",
                self.env(),
            )
        self.wait_for(lambda: self.port_open(5432), 45, "PostgreSQL TCP 5432")
        if self.psql.exists():
            env = self.env()
            env["PGPASSWORD"] = self.password_file.read_text(encoding="utf-8").strip()
            result = self.run(
                [str(self.psql), "-h", "127.0.0.1", "-U", "postgres", "-d", "fund_research", "-c", "SELECT 1"],
                env=env,
            )
            if result.returncode != 0:
                self.log("PostgreSQL 已监听，但 psql 查询未通过；API 健康检查会再次验证数据库")
        self.state("PostgreSQL", "healthy", "数据库可连接")

    def start_ollama(self) -> None:
        self.state("Ollama", "starting")
        try:
            tags = self.http_json(f"http://{HEALTH_HOST}:11434/api/tags")
            existing = True
        except (OSError, ValueError, URLError):
            existing = False
            tags = {}
        if not existing:
            if self.port_open(11434):
                self.stop_image("ollama.exe", "异常 Ollama")
                try:
                    self.wait_for(lambda: not self.port_open(11434), 10, "旧 Ollama 退出")
                except ManagerError:
                    raise ManagerError("Ollama 端口 11434 已被异常进程占用，无法重启")
            if not self.ollama.exists():
                raise ManagerError(f"找不到 Ollama：{self.ollama}")
            self.spawn(
                "Ollama",
                [str(self.ollama), "serve"],
                self.ollama.parent,
                self.runtime / "ollama-manager.log",
                self.env(),
            )
            self.wait_for(lambda: self.http_ok(f"http://{HEALTH_HOST}:11434/api/tags"), 45, "Ollama API")
            tags = self.http_json(f"http://{HEALTH_HOST}:11434/api/tags")
        model_names = {str(item.get("name", "")) for item in tags.get("models", []) if isinstance(item, dict)}
        if not any(name == "qwen3:4b" or name.startswith("qwen3:4b-") for name in model_names):
            raise ManagerError("Ollama 已启动，但未找到模型 qwen3:4b；请先执行 ollama pull qwen3:4b")
        self.state("Ollama", "healthy", "qwen3:4b 可用")

    def api_healthy(self) -> bool:
        try:
            data = self.http_json(f"http://{HEALTH_HOST}:8000/api/health")
            payload = data.get("data", {})
            return payload.get("status") == "ok" and payload.get("database") == "ok"
        except (OSError, ValueError, URLError):
            return False

    def ollama_healthy(self) -> bool:
        try:
            tags = self.http_json(f"http://{HEALTH_HOST}:11434/api/tags")
            names = {str(item.get("name", "")) for item in tags.get("models", []) if isinstance(item, dict)}
            return any(name == "qwen3:4b" or name.startswith("qwen3:4b-") for name in names)
        except (OSError, ValueError, URLError):
            return False

    def worker_healthy(self) -> bool:
        log = self.runtime / "worker-manager.log"
        if not log.exists():
            return False
        return "Native browser preflight passed" in log.read_text(encoding="utf-8", errors="replace")

    def start_api(self) -> None:
        self.state("API", "starting")
        if not self.api_healthy():
            if self.port_open(8000):
                self.log("API 端口已被占用，等待现有 API 恢复，不重复启动进程")
            else:
                self.spawn(
                    "API",
                    [str(self.python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                    self.root,
                    self.runtime / "api-manager.log",
                    self.env(),
                )
        self.wait_for(self.api_healthy, 60, "API /api/health + PostgreSQL")
        self.state("API", "healthy", "数据库健康检查通过")

    def start_worker(self) -> None:
        self.state("Worker", "starting")
        worker_service = self.service_state("FundResearchWorker")
        if worker_service == "RUNNING":
            self.state("Worker", "healthy", "FundResearchWorker Windows 服务已运行")
            return
        current = self.processes.get("Worker")
        if current and current.poll() is None:
            self.state("Worker", "healthy", "Worker 已由管理器运行")
            return
        log = self.runtime / "worker-manager.log"
        self.spawn(
            "Worker",
            [str(self.python), "-m", "app.worker.main", "--browser-preflight", "--worker-id", "worker-gui"],
            self.root,
            log,
            self.env(),
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            process = self.processes["Worker"]
            if process.poll() is not None:
                details = log.read_text(encoding="utf-8", errors="replace")[-2000:] if log.exists() else ""
                raise ManagerError(f"Worker 启动失败。\n{details}")
            contents = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
            if "Native browser preflight passed" in contents:
                self.state("Worker", "healthy", "Playwright 浏览器预检通过")
                return
            time.sleep(0.5)
        raise ManagerError("Worker 浏览器预检超时；请查看 state\\runtime\\worker-manager.log")

    def lan_addresses(self) -> list[str]:
        values: set[str] = set()
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            value = info[4][0]
            if not value.startswith(("127.", "169.254.")):
                values.add(value)
        return sorted(values) or ["127.0.0.1"]

    def web_healthy(self) -> bool:
        return (
            self.http_ok(f"http://{HEALTH_HOST}:5173/", timeout=3)
            and self.http_ok(f"http://{HEALTH_HOST}:5173/api/health", timeout=5)
            and self.http_ok(f"http://{HEALTH_HOST}:5173/api/jobs?page=1&page_size=5", timeout=8)
        )

    def ensure_lan_firewall(self) -> bool:
        rule = "GF FMR Workbench TCP 5173"
        command = (
            "$ErrorActionPreference='Stop'; "
            "$n='" + rule + "'; "
            "$r=Get-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue; "
            "if ($null -eq $r) { "
            "New-NetFirewallRule -DisplayName $n -Direction Inbound -Action Allow -ErrorAction Stop "
            "-Protocol TCP -LocalPort 5173 -Profile Any -RemoteAddress LocalSubnet | Out-Null "
            "} else { Set-NetFirewallRule -DisplayName $n -Profile Any -Enabled True "
            "-Action Allow -Direction Inbound -ErrorAction Stop }; "
            "if ($null -eq (Get-NetFirewallRule -DisplayName $n -ErrorAction Stop)) { throw 'Firewall rule verification failed' }"
        )
        result = self.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command])
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            self.log(f"警告：无法配置局域网防火墙规则，请确认 EXE 已通过 UAC 提权。\n{details}")
            return False
        self.log("局域网防火墙规则已启用：TCP 5173 / LocalSubnet")
        return True

    def start_web(self) -> None:
        self.state("Web", "starting")
        if not self.web_healthy():
            if self.port_open(5173):
                self.stop_port(5173, "旧 Web")
            if self.port_open(5173):
                self.log("旧 Web 进程未退出，等待其反向代理恢复，不重复启动进程")
            else:
                self.spawn(
                    "Web",
                    [str(self.node), str(self.vite), "--host", "0.0.0.0", "--port", "5173"],
                    self.root / "platform" / "web",
                    self.runtime / "web-manager.log",
                    self.env(),
                )
        self.wait_for(self.web_healthy, 60, "Web 首页与 /api 反向代理")
        firewall_ok = self.ensure_lan_firewall()
        address = self.lan_addresses()[0]
        url = f"http://{address}:5173/"
        self.state("Web", "healthy", url if firewall_ok else f"{url}（防火墙未配置）")
        self.emit("url", url)

    # Minimal mode intentionally does not wait for health checks.  The UI is
    # a power switch: issue each command in order, report the command as sent,
    # and let the services finish booting in the background.
    def start_postgres_minimal(self) -> None:
        self.state("PostgreSQL", "starting")
        if self.port_open(5432):
            self.state("PostgreSQL", "healthy", "已在运行")
            return
        service = "postgresql-x64-17-gf"
        if self.service_state(service) in {"STOPPED", "UNKNOWN"}:
            result = self.run(["sc.exe", "start", service])
            if result.returncode == 0:
                self.service_started = True
                self.log("已发出 PostgreSQL 服务启动命令")
                self.state("PostgreSQL", "healthy", "启动命令已发出")
                return
        if self.postgres.exists():
            pid_file = self.pg_data / "postmaster.pid"
            if pid_file.exists() and self.pg_ctl.exists():
                status = self.run([str(self.pg_ctl), "status", "-D", str(self.pg_data)])
                if status.returncode != 0:
                    pid_file.unlink(missing_ok=True)
            self.spawn("PostgreSQL", [str(self.postgres), "-D", str(self.pg_data), "-p", "5432"], self.pg_data, self.runtime / "postgres-manager.log", self.env())
            self.state("PostgreSQL", "healthy", "进程启动命令已发出")
            return
        raise ManagerError("找不到 PostgreSQL 服务或运行文件")

    def start_ollama_minimal(self) -> None:
        self.state("Ollama", "starting")
        if self.port_open(11434):
            self.state("Ollama", "healthy", "已在运行")
            return
        if not self.ollama.exists():
            raise ManagerError(f"找不到 Ollama：{self.ollama}")
        self.spawn("Ollama", [str(self.ollama), "serve"], self.ollama.parent, self.runtime / "ollama-manager.log", self.env())
        self.state("Ollama", "healthy", "启动命令已发出")

    def start_api_minimal(self) -> None:
        self.state("API", "starting")
        if self.port_open(8000):
            self.wait_for(self.api_healthy, 60, "API /api/health + PostgreSQL")
            self.state("API", "healthy", "已在运行且数据库健康")
            return
        self.spawn("API", [str(self.python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"], self.root, self.runtime / "api-manager.log", self.env())
        self.wait_for(self.api_healthy, 60, "API /api/health + PostgreSQL")
        self.state("API", "healthy", "数据库健康检查通过")

    def start_worker_minimal(self) -> None:
        self.state("Worker", "starting")
        if self.service_state("FundResearchWorker") == "RUNNING":
            self.state("Worker", "healthy", "Windows 服务已在运行")
            return
        worker_env = self.env()
        worker_env["PLATFORM_BROWSER_PREFLIGHT"] = "0"
        self.spawn("Worker", [str(self.python), "-m", "app.worker.main", "--worker-id", "worker-gui"], self.root, self.runtime / "worker-manager.log", worker_env)
        self.state("Worker", "healthy", "启动命令已发出")

    def start_web_minimal(self) -> None:
        self.state("Web", "starting")
        if self.port_open(5173):
            # A stale Vite process can keep serving index.html while its API
            # proxy has stuck sockets after an API restart.  Reuse it only
            # when the proxy itself answers; otherwise replace the process.
            if self.http_ok(f"http://{HEALTH_HOST}:5173/api/health", timeout=3):
                address = self.lan_addresses()[0]
                url = f"http://{address}:5173/"
                self.state("Web", "healthy", url)
                self.emit("url", url)
                return
            self.log("旧 Web 的 API 代理无响应，正在重启 Web")
            self.stop_port(5173, "旧 Web")
            self.wait_for(lambda: not self.port_open(5173), 10, "旧 Web 退出")
        self.spawn("Web", [str(self.node), str(self.vite), "--host", "0.0.0.0", "--port", "5173"], self.root / "platform" / "web", self.runtime / "web-manager.log", self.env())
        self.wait_for(
            lambda: self.http_ok(f"http://{HEALTH_HOST}:5173/", timeout=3)
            and self.http_ok(f"http://{HEALTH_HOST}:5173/api/health", timeout=5),
            60,
            "Web 首页与 API 反向代理",
        )
        address = self.lan_addresses()[0]
        url = f"http://{address}:5173/"
        self.state("Web", "healthy", url)
        self.emit("url", url)

    def terminate_process_now(self, name: str) -> None:
        process = self.processes.get(name)
        if process and process.poll() is None:
            self.log(f"立即停止 {name}，PID {process.pid}")
            self.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"])

    def stop_orphan_workers(self) -> None:
        python_path = str(self.python).replace("'", "''")
        command = (
            "$ErrorActionPreference='SilentlyContinue'; "
            "$target='" + python_path + "'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.ExecutablePath -eq $target -and $_.CommandLine -match 'app\\.worker\\.main' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
        )
        self.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command])

    def start_all(self) -> None:
        with self._lock:
            if self.busy:
                return
            self.busy = True
        try:
            self.log(f"项目根目录：{self.root}")
            self.log(f"状态目录：{self.state_root}")
            for operation in (self.start_postgres_minimal, self.start_ollama_minimal, self.start_api_minimal, self.start_worker_minimal, self.start_web_minimal):
                if self.cancel_event.is_set():
                    raise ManagerError("启动已取消")
                operation()
            self.log("全部服务启动命令已按顺序发出，正在打开局域网工作台")
            self.emit("complete", None)
        except Exception as exc:  # noqa: BLE001 - surface all startup failures in UI
            if self.cancel_event.is_set():
                self.log("启动已取消，正在按停止顺序清理已启动服务")
                self.emit("cancelled", None)
            else:
                self.log(f"启动异常：{exc}\n{traceback.format_exc()}")
                self.emit("error", str(exc))
        finally:
            with self._lock:
                self.busy = False

    def terminate_process(self, name: str) -> None:
        process = self.processes.get(name)
        if not process or process.poll() is not None:
            return
        self.log(f"正在停止 {name}，PID {process.pid}")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], creationflags=CREATE_NO_WINDOW)

    def listening_pid(self, port: int) -> int | None:
        result = self.run(["netstat.exe", "-ano", "-p", "tcp"])
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[0].upper() == "TCP" and fields[3].upper() == "LISTENING":
                local = fields[1].rsplit(":", 1)[-1]
                if local == str(port):
                    try:
                        return int(fields[4])
                    except ValueError:
                        return None
        return None

    def stop_port(self, port: int, label: str) -> None:
        pid = self.listening_pid(port)
        if pid is None:
            return
        self.log(f"停止 {label} 监听进程 PID {pid}")
        self.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"])

    def stop_image(self, image: str, label: str) -> None:
        result = self.run(["taskkill.exe", "/IM", image, "/T", "/F"])
        if result.returncode == 0:
            self.log(f"已停止 {label} 进程")

    def stop_postgres(self) -> None:
        self.state("PostgreSQL", "stopping")
        if self.service_state("postgresql-x64-17-gf") == "RUNNING":
            self.run(["sc.exe", "stop", "postgresql-x64-17-gf"])
        if self.pg_ctl.exists() and self.port_open(5432):
            self.run([str(self.pg_ctl), "stop", "-D", str(self.pg_data), "-m", "fast", "-w"])
        self.terminate_process("PostgreSQL")
        if self.port_open(5432):
            self.stop_port(5432, "PostgreSQL")
        self.state("PostgreSQL", "stopped")

    def stop_all(self) -> None:
        # Stopping is allowed while a start operation is waiting for a health
        # check.  Set cancellation first so the start thread cannot continue
        # to the next dependency while this cleanup runs.
        self.cancel_event.set()
        with self._lock:
            self.busy = True
        try:
            self.state("Web", "stopping")
            self.terminate_process_now("Web")
            self.stop_port(5173, "Web")
            self.state("Web", "stopped")

            self.state("Worker", "stopping")
            self.terminate_process_now("Worker")
            if self.service_state("FundResearchWorker") == "RUNNING":
                self.run(["sc.exe", "stop", "FundResearchWorker"])
            self.stop_orphan_workers()
            self.state("Worker", "stopped")

            self.state("API", "stopping")
            self.terminate_process_now("API")
            self.stop_port(8000, "API")
            self.state("API", "stopped")

            self.state("Ollama", "stopping")
            self.terminate_process_now("Ollama")
            self.stop_image("ollama.exe", "Ollama")
            self.state("Ollama", "stopped")
            self.state("PostgreSQL", "stopping")
            if self.service_state("postgresql-x64-17-gf") == "RUNNING":
                self.run(["sc.exe", "stop", "postgresql-x64-17-gf"])
            if self.pg_ctl.exists() and self.port_open(5432):
                self.run([str(self.pg_ctl), "stop", "-D", str(self.pg_data), "-m", "immediate"])
            self.terminate_process_now("PostgreSQL")
            self.stop_port(5432, "PostgreSQL")
            self.state("PostgreSQL", "stopped")
            self.log("已按 Web → Worker → API → Ollama → PostgreSQL 顺序发出停止命令")
            self.emit("complete_stop", None)
        except Exception as exc:  # noqa: BLE001
            self.log(f"停止异常：{exc}\n{traceback.format_exc()}")
            self.emit("error", str(exc))
        finally:
            with self._lock:
                self.busy = False


class ServerWindow:
    """Tkinter management UI."""

    SERVICE_NAMES = ("PostgreSQL", "Ollama", "API", "Worker", "Web")

    def __init__(self, project_root: Path):
        self.root = tk.Tk()
        self.root.title("GF FMR Server")
        self.root.geometry("760x600")
        self.root.minsize(680, 500)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.manager = ServerManager(project_root, self.events.put)
        self.ui_log_path = self.manager.runtime / "server-manager-ui.log"
        self.cards: dict[str, tuple[tk.Label, tk.Label]] = {}
        self.url = ""
        self.operation_active = False
        self.operation_mode = ""
        self.completion_seen = False
        self._auto_opened = False
        self._build()
        self.root.after(100, self._drain_events)
        self.root.after(500, self._refresh_status)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="GF FMR Server", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Windows 服务器管理 · 按依赖顺序启动与停止", foreground="#5c6570").pack(anchor="w", pady=(2, 14))

        cards = ttk.Frame(outer)
        cards.pack(fill="x")
        for index, name in enumerate(self.SERVICE_NAMES):
            frame = ttk.LabelFrame(cards, text=f" {index + 1}. {name} ", padding=10)
            frame.grid(row=index // 3, column=index % 3, sticky="nsew", padx=5, pady=5)
            cards.columnconfigure(index % 3, weight=1)
            status = tk.Label(frame, text="未启动", fg="#68717c")
            status.pack(anchor="w")
            detail = tk.Label(frame, text="等待操作", fg="#8a939d", wraplength=190, justify="left")
            detail.pack(anchor="w", pady=(4, 0))
            self.cards[name] = (status, detail)

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(14, 8))
        self.start_button = ttk.Button(controls, text="启动服务器", command=self._start)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="停止服务器", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        self.open_button = ttk.Button(controls, text="打开工作台", command=self._open, state="disabled")
        self.open_button.pack(side="left")

        self.url_label = ttk.Label(outer, text="工作台地址：未启动", foreground="#1769aa")
        self.url_label.pack(anchor="w", pady=(2, 8))
        ttk.Label(outer, text="运行日志", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.log_text = tk.Text(outer, height=14, state="disabled", wrap="word", bg="#111827", fg="#d1d5db", relief="flat")
        self.log_text.pack(fill="both", expand=True, pady=(6, 0))

    def _append(self, message: str) -> None:
        try:
            with self.ui_log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _start(self) -> None:
        self.operation_active = True
        self.operation_mode = "start"
        self.completion_seen = False
        self._auto_opened = False
        self.manager.cancel_event.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.open_button.configure(state="disabled")
        self._append("开始按 PostgreSQL → Ollama → API → Worker → Web 启动")
        threading.Thread(target=self.manager.start_all, daemon=True).start()

    def _stop(self) -> None:
        self.operation_active = True
        self.operation_mode = "stop"
        self.completion_seen = False
        self.manager.cancel_event.set()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self._append("开始按 Web → Worker → API → Ollama → PostgreSQL 停止")
        threading.Thread(target=self.manager.stop_all, daemon=True).start()

    def _open(self) -> None:
        if self.url:
            try:
                subprocess.Popen(["explorer.exe", self.url], creationflags=CREATE_NO_WINDOW)
            except OSError as first_error:
                try:
                    os.startfile(self.url)  # type: ignore[attr-defined]
                except OSError as exc:
                    self._append(f"无法打开浏览器，请手动访问 {self.url}：{exc or first_error}")

    def _set_card(self, name: str, status: str, detail: str = "") -> None:
        status_text = {"starting": "启动中", "healthy": "健康", "stopping": "停止中", "stopped": "已停止"}.get(status, status)
        color = {"healthy": "#15803d", "starting": "#b45309", "stopping": "#b45309", "stopped": "#68717c"}.get(status, "#b91c1c")
        self.cards[name][0].configure(text=status_text, fg=color)
        self.cards[name][1].configure(text=detail or "")

    def _mark_start_complete(self) -> None:
        if self.completion_seen:
            return
        self.completion_seen = True
        self.operation_active = False
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.open_button.configure(state="normal")
        if self.url and not self._auto_opened:
            self._auto_opened = True
            self._open()

    def _refresh_status(self) -> None:
        try:
            if self.operation_mode == "start":
                checks = {
                    "PostgreSQL": self.manager.port_open(5432),
                    "Ollama": self.manager.port_open(11434),
                    "API": self.manager.port_open(8000),
                    "Worker": (
                        self.manager.service_state("FundResearchWorker") == "RUNNING"
                        or (self.manager.processes.get("Worker") is not None and self.manager.processes["Worker"].poll() is None)
                    ),
                    "Web": self.manager.port_open(5173),
                }
                details = {
                    "PostgreSQL": "5432 已监听",
                    "Ollama": "11434 已监听",
                    "API": "8000 已监听",
                    "Worker": "进程启动命令已发出",
                    "Web": self.url or "5173 已监听",
                }
                for name, healthy in checks.items():
                    if healthy:
                        if name == "Web" and not self.url:
                            address = self.manager.lan_addresses()[0]
                            self.url = f"http://{address}:5173/"
                            self.url_label.configure(text=f"工作台地址：{self.url}")
                        self._set_card(name, "healthy", details[name])
                if all(checks.values()) and not self.manager.busy:
                    self._mark_start_complete()
            elif self.operation_mode == "stop":
                stopped = (
                    not self.manager.port_open(5173)
                    and not self.manager.port_open(8000)
                    and not self.manager.port_open(11434)
                    and not self.manager.port_open(5432)
                    and self.manager.service_state("postgresql-x64-17-gf") != "RUNNING"
                    and self.manager.service_state("FundResearchWorker") != "RUNNING"
                )
                if stopped and not self.manager.busy:
                    for name in self.SERVICE_NAMES:
                        self._set_card(name, "stopped", "")
                    self.operation_active = False
                    self.operation_mode = ""
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.open_button.configure(state="disabled")
        except Exception as exc:  # noqa: BLE001
            self._append(f"状态轮询异常：{exc}")
        self.root.after(1000, self._refresh_status)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                try:
                    if kind == "log":
                        self._append(str(payload))
                    elif kind == "state":
                        name, status, detail = payload  # type: ignore[misc]
                        self._set_card(name, status, detail)
                    elif kind == "url":
                        self.url = str(payload)
                        self.url_label.configure(text=f"工作台地址：{self.url}")
                    elif kind == "complete":
                        self._mark_start_complete()
                    elif kind == "complete_stop":
                        self.operation_active = False
                        self.operation_mode = ""
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                        self.open_button.configure(state="disabled")
                    elif kind == "cancelled":
                        self._append("启动已取消，等待停止操作完成")
                    elif kind == "error":
                        self.operation_active = False
                        self.operation_mode = ""
                        self._append(f"失败：{payload}")
                        messagebox.showerror("服务器启动/停止失败", str(payload), parent=self.root)
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="normal")
                except Exception as exc:  # noqa: BLE001 - keep the event pump alive
                    self._append(f"UI 事件处理异常：{exc}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def run(self) -> None:
        self.root.mainloop()


def discover_project_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="输出运行时路径检查结果并退出")
    args = parser.parse_args()
    project_root = (args.project_root or discover_project_root()).resolve()
    if args.check:
        manager = ServerManager(project_root, lambda *_: None)
        print(json.dumps({"project_root": str(project_root), "state_root": str(manager.state_root), "python": str(manager.python), "ollama": str(manager.ollama), "postgres": str(manager.postgres), "vite": str(manager.vite)}, ensure_ascii=False, indent=2))
        return 0
    ServerWindow(project_root).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
