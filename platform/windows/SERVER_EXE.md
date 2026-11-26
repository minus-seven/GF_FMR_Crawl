# GF_FMR_Server.exe

`GF_FMR_Server.exe` is a Windows administrator UI for this project. It starts
and stops the local services in the following order:

```text
start: PostgreSQL -> Ollama -> API -> Worker -> Web
stop:  Web -> Worker -> API -> Ollama -> PostgreSQL
```

The UI issues the start commands in order without blocking on model, database,
browser or firewall checks. It opens the first detected LAN IPv4 address as
soon as the Web process has been launched, normally:
`http://192.168.x.x:5173/`.

## Build

The build host needs MinGW-w64 (`gcc.exe` and `windres.exe`) on `PATH`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\platform\windows\build_server_exe.ps1
```

The output is `dist\GF_FMR_Server.exe`. The executable embeds a
`requireAdministrator` manifest because PostgreSQL and Windows firewall/service
operations require elevation.

## Release layout

The EXE is a launcher, not a fully static copy of every runtime. A usable
release must keep the following beside it (or preserve the same relative paths):

```text
GF_FMR_Server.exe
platform\windows\server_manager.py
platform\api\
platform\web\
state\dependencies\venv\
state\dependencies\node22\
state\dependencies\ollama\
state\platform-runtime\postgresql17\
state\runtime\postgres-superuser.txt
state\config.ini
state\data\
```

Moving only `GF_FMR_Server.exe` to another Windows machine is not sufficient;
the target must have the compatible state/dependency bundle and PostgreSQL
database. For a new machine, restore PostgreSQL through `pg_dump`/`pg_restore`
and provide the local `state` directory rather than copying a live database
directory from another operating system.

## Manual fallback

The same UI logic can be inspected without launching Tkinter:

```powershell
& .\state\dependencies\venv\Scripts\python.exe `
  .\platform\windows\server_manager.py --check
```

Ollama and API health are displayed as live status information. They do not
block the start/stop buttons. If Ollama does not contain `qwen3:4b`, model
inference will report that condition when a model-backed feature is used.
