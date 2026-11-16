"""The terminal, for the commands: UTF-8 output, and reading the clipboard."""
import base64
import subprocess
import sys


def utf8_output() -> None:
    """Print UTF-8 whatever the console. Windows uses its legacy code page (cp1252) when
    output is redirected, e.g. to /dev/null, and then a model's emoji crashes print()."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")


def read_clipboard() -> str:
    if sys.platform == "win32":
        # Base64 of the UTF-8 bytes: PowerShell's console encoding otherwise garbles "£" and "–".
        script = "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string](Get-Clipboard -Raw)))"
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, check=True, text=True).stdout
        return base64.b64decode(out.strip()).decode("utf-8")
    if sys.platform == "darwin":
        cmd = ["pbpaste"]
    else:
        cmd = ["xclip", "-selection", "clipboard", "-o"]
    return subprocess.run(cmd, capture_output=True, check=True, encoding="utf-8").stdout
