#include <windows.h>
#include <wchar.h>
#include <stdio.h>

static int exists(const wchar_t *path) {
    DWORD attrs = GetFileAttributesW(path);
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static void join_path(wchar_t *out, size_t size, const wchar_t *left, const wchar_t *right) {
    _snwprintf(out, size, L"%ls\\%ls", left, right);
    out[size - 1] = L'\0';
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show) {
    (void)instance;
    (void)previous;
    (void)command_line;
    (void)show;

    wchar_t exe_path[MAX_PATH * 4];
    wchar_t root[MAX_PATH * 4];
    wchar_t python[MAX_PATH * 4];
    wchar_t script[MAX_PATH * 4];
    wchar_t cmd[MAX_PATH * 8];
    DWORD length = GetModuleFileNameW(NULL, exe_path, (DWORD)(sizeof(exe_path) / sizeof(exe_path[0])));
    if (length == 0 || length >= sizeof(exe_path) / sizeof(exe_path[0])) {
        MessageBoxW(NULL, L"无法定位 GF_FMR_Server.exe。", L"GF FMR Server", MB_ICONERROR);
        return 2;
    }
    wchar_t *slash = wcsrchr(exe_path, L'\\');
    if (!slash) {
        MessageBoxW(NULL, L"无法定位项目目录。", L"GF FMR Server", MB_ICONERROR);
        return 2;
    }
    *slash = L'\0';
    wcscpy(root, exe_path);
    join_path(script, sizeof(script) / sizeof(script[0]), root, L"platform\\windows\\server_manager.py");
    if (!exists(script)) {
        wchar_t parent[MAX_PATH * 4];
        wcscpy(parent, root);
        slash = wcsrchr(parent, L'\\');
        if (slash) *slash = L'\0';
        join_path(script, sizeof(script) / sizeof(script[0]), parent, L"platform\\windows\\server_manager.py");
        if (exists(script)) wcscpy(root, parent);
    }
    join_path(python, sizeof(python) / sizeof(python[0]), root, L"state\\dependencies\\venv\\Scripts\\pythonw.exe");
    if (!exists(python)) {
        MessageBoxW(NULL, L"找不到 Python 运行时。请确认发布包包含 state\\dependencies\\venv。", L"GF FMR Server", MB_ICONERROR);
        return 3;
    }
    _snwprintf(cmd, sizeof(cmd) / sizeof(cmd[0]), L"\"%ls\" \"%ls\" --project-root \"%ls\"", python, script, root);
    cmd[(sizeof(cmd) / sizeof(cmd[0])) - 1] = L'\0';

    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    ZeroMemory(&startup, sizeof(startup));
    ZeroMemory(&process, sizeof(process));
    startup.cb = sizeof(startup);
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, root, &startup, &process)) {
        MessageBoxW(NULL, L"无法启动服务器管理界面。", L"GF FMR Server", MB_ICONERROR);
        return 4;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
