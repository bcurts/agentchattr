"""Prepare a private SDK model configuration from DSH Web, without changing Web."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile


class WebConfigError(RuntimeError):
    """A safe, secret-free description of a configuration problem."""


_ERRORS = {
    "invalid_profile": "Web 配置格式无效。",
    "missing_selection": "Web 中没有可用的 provider 和 model，请显式指定。",
    "unknown_provider": "Web 中没有所选 provider 的配置。",
    "unknown_model": "Web 中没有所选 provider 对应的模型。",
    "unsupported_expression": "选中的模型配置含动态表达式，无法安全复制。",
    "missing_credential": "所选 provider 缺少凭据，请先在 Web 配置。",
    "parse_or_read_error": "无法安全读取 Web 配置或凭据。",
}


def _baseline(paths: dict[str, Path]) -> tuple[dict[str, str | None], Path]:
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
              for name, path in paths.items()}
    folder = Path(tempfile.mkdtemp(prefix="agentchattr-dsh-audit-"))
    audit = folder / "source-hashes.json"
    audit.write_text(json.dumps(hashes, sort_keys=True), encoding="utf-8")
    return hashes, audit


def _secure_file(path: Path, content: str) -> None:
    """Create a private file before writing sensitive bytes; replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".dsh-private-", dir=path.parent)
    temp = Path(temp_name)
    try:
        if os.name == "nt":
            result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)
            sid = next(csv.reader(io.StringIO(result.stdout)))[1]
            subprocess.run(["icacls", str(temp), "/inheritance:r", "/grant:r", f"*{sid}:(F)"], capture_output=True, check=True)
        else:
            os.chmod(temp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            fd = -1
            stream.write(content)
        os.replace(temp, path)
    finally:
        if fd != -1:
            os.close(fd)
        temp.unlink(missing_ok=True)


def prepare_web_config(agent_cfg: dict, instance_dir: Path, *, runtime: dict) -> dict:
    """Snapshot only the selected model route and its credential to instance home.

    `runtime` supplies `node`, `entry`, and optionally `web_home` for isolated tests.
    The result has provider, model, patches, and audit_path; it contains no secret.
    """
    provider = agent_cfg.get("provider") or None
    model = agent_cfg.get("model") or None
    if provider == "deepseek-official" and model and os.environ.get("DEEPSEEK_API_KEY"):
        # A previously used instance may still have a managed key. DSH gives
        # that file precedence over the environment, so reset this private
        # snapshot before allowing the explicit environment route to start.
        try:
            _secure_file(Path(instance_dir) / "home" / ".credentials.yaml", '{"version": 1}')
        except (OSError, subprocess.SubprocessError):
            raise WebConfigError("无法准备隔离的 Web 模型配置。") from None
        return {"provider": provider, "model": model, "patches": [], "audit_path": None}
    home = Path(runtime.get("web_home") or os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    paths = {"web_profile": home / "profiles" / "web" / "cordis.patch.yml",
             "global_patch": home / "cordis.patch.yml", "credentials": home / ".credentials.yaml"}
    if not paths["web_profile"].is_file():
        raise WebConfigError("找不到 Web 模型配置；请指定可用的模型和凭据。")
    try:
        baseline, audit = _baseline(paths)
        payload = {"entry": str(runtime["entry"]), "webPatch": str(paths["web_profile"]),
                   "credentials": str(paths["credentials"]), "provider": provider, "model": model}
        proc = subprocess.run([str(runtime["node"]), str(Path(__file__).with_name("dsh_web_config_helper.js"))],
                              input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", check=False)
        if proc.returncode:
            raise WebConfigError(_ERRORS.get(proc.stderr.strip(), _ERRORS["parse_or_read_error"]))
        result = json.loads(proc.stdout)
        after = {name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                 for name, path in paths.items()}
        if baseline != after:
            raise WebConfigError("Web 配置在读取期间发生变化，请重试。")
        instance = Path(instance_dir)
        patch = instance / "web-model.patch.json"
        creds = instance / "home" / ".credentials.yaml"
        _secure_file(patch, json.dumps(result["patches"], ensure_ascii=False))
        _secure_file(creds, json.dumps(result["credentials"], ensure_ascii=False))
        return {"provider": result["provider"], "model": result["model"], "patches": [str(patch)], "audit_path": str(audit)}
    except WebConfigError:
        raise
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise WebConfigError("无法准备隔离的 Web 模型配置。") from None
