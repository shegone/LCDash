#!/usr/bin/env python3
"""Initialize the isolated LCDash Open WebUI Computer pilot.

Run this script only on the LCDash server. It intentionally reads the existing
Open WebUI administrator password locally and never prints credentials or API
keys.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "http://127.0.0.1:8020"
OPEN_WEBUI_BASE_URL = "http://127.0.0.1:3000"
ADMIN_USERNAME = "administrator"
ADMIN_DISPLAY_NAME = "Logan 911 Administrator"
ADMIN_PASSWORD_FILE = Path("/srv/lcdash-platform/secrets/openwebui_admin_password")
GATEWAY_KEY_FILE = Path("/srv/lcdash-platform/secrets/open_webui_computer_gateway_key")
CONTAINER_NAME = "lcdash-open-webui-computer"
CONNECTION_NAME = "LCDash Local Ollama"
WORKSPACE_PATH = "/workspace/LCDash"


class ComputerClient:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url
        self.default_headers = {"Content-Type": "application/json"}
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )

    def call(self, path: str, method: str = "GET", body: object | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=self.default_headers,
        )
        with self.opener.open(request, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None


def login_or_setup(client: ComputerClient, password: str) -> None:
    try:
        status, _ = client.call(
            "/api/auth/login",
            "POST",
            {"username": ADMIN_USERNAME, "password": password},
        )
        if status == 200:
            print("Computer administrator login: OK")
            return
    except urllib.error.HTTPError as error:
        if error.code not in {401, 403}:
            raise

    logs = subprocess.check_output(
        ["docker", "logs", CONTAINER_NAME],
        stderr=subprocess.STDOUT,
        text=True,
    )
    match = re.search(r"[?&]token=([^\s&]+)", logs)
    if not match:
        raise RuntimeError("Computer setup token was not found in the container log")

    status, _ = client.call(
        "/api/auth/setup",
        "POST",
        {
            "username": ADMIN_USERNAME,
            "password": password,
            "token": match.group(1),
            "display_name": ADMIN_DISPLAY_NAME,
        },
    )
    if status != 200:
        raise RuntimeError(f"Computer administrator setup failed with status {status}")
    print("Computer administrator setup: OK")


def ensure_ollama_connection(client: ComputerClient) -> None:
    _, existing = client.call("/api/admin/connections")
    connections = existing if isinstance(existing, list) else []
    if not any(item.get("name") == CONNECTION_NAME for item in connections):
        status, _ = client.call(
            "/api/admin/connections",
            "POST",
            {
                "name": CONNECTION_NAME,
                "provider": "openai",
                "api_type": "chat_completions",
                "base_url": "http://ollama:11434/v1",
                "api_key": "local-ollama",
                "enabled": True,
            },
        )
        if status != 200:
            raise RuntimeError(f"Ollama connection creation failed with status {status}")
    print("Computer Ollama connection: OK")


def report_qwen_model_ids(client: ComputerClient) -> None:
    _, result = client.call("/api/chats/models")

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from strings(nested)

    candidates = sorted(
        {value for value in strings(result) if "qwen3.5:27b" in value.lower()}
    )
    if not candidates:
        raise RuntimeError("qwen3.5:27b was not discovered by Computer")
    print("Computer qwen3.5:27b model IDs: " + ", ".join(candidates))


def ensure_opencode_profile(client: ComputerClient) -> None:
    _, result = client.call("/api/admin/agents")
    profiles = result.get("profiles", []) if isinstance(result, dict) else []
    configured = [
        {
            key: value
            for key, value in profile.items()
            if key
            in {
                "id",
                "agent",
                "name",
                "mode",
                "command",
                "home",
                "models",
                "default_model",
                "approval_mode",
                "sandbox_mode",
                "permission_mode",
                "launch_args",
                "api_endpoint",
                "server_url",
                "server_password",
            }
        }
        for profile in profiles
        if isinstance(profile, dict) and profile.get("agent") != "opencode"
    ]
    configured.append(
        {
            "id": "lcdash-opencode",
            "agent": "opencode",
            "name": "LCDash OpenCode - Terminal Fallback",
            "mode": "disabled",
            "command": "opencode",
            "home": None,
            "models": ["ollama/qwen3.5:27b", "ollama/qwen3.5:9b"],
            "default_model": "ollama/qwen3.5:27b",
            "server_url": "",
            "server_password": "",
        }
    )
    status, _ = client.call(
        "/api/admin/agents", "PUT", {"profiles": configured}
    )
    if status != 200:
        raise RuntimeError(f"OpenCode profile update failed with status {status}")

    print("Computer OpenCode native profile: disabled; terminal fallback retained")


def ensure_workspace(client: ComputerClient) -> None:
    encoded_path = urllib.parse.quote(WORKSPACE_PATH, safe="")
    status, _ = client.call(
        f"/api/state/workspace?path={encoded_path}", "PUT", {}
    )
    if status != 200:
        raise RuntimeError(f"Workspace creation failed with status {status}")
    print(f"Computer workspace {WORKSPACE_PATH}: OK")


def ensure_gateway_key(client: ComputerClient, *, rotate: bool = False) -> None:
    _, keys = client.call("/v1/keys")
    key_list = keys if isinstance(keys, list) else []
    named_keys = [item for item in key_list if item.get("name") == "LCDash Open WebUI"]

    if rotate:
        for item in named_keys:
            key_id = item.get("id")
            if key_id:
                client.call(f"/v1/keys/{urllib.parse.quote(str(key_id), safe='')}", "DELETE")
        if GATEWAY_KEY_FILE.exists():
            GATEWAY_KEY_FILE.unlink()
        named_keys = []
        print("Computer gateway key: previous key revoked")

    if GATEWAY_KEY_FILE.exists():
        os.chmod(GATEWAY_KEY_FILE, 0o600)
        print("Computer gateway key record: OK")
        return
    if named_keys:
        raise RuntimeError(
            "A gateway key exists but its protected server record is missing; "
            "rotate the key rather than creating an untracked credential"
        )

    status, created = client.call(
        "/v1/keys", "POST", {"name": "LCDash Open WebUI"}
    )
    if status != 200 or not isinstance(created, dict):
        raise RuntimeError(f"Gateway key creation failed with status {status}")
    secret = created.get("key") or created.get("api_key") or created.get("token")
    if not secret:
        raise RuntimeError("Gateway key response did not contain a credential")

    descriptor = os.open(
        GATEWAY_KEY_FILE,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(str(secret).strip() + "\n")
    print("Computer gateway key record: created")


def validate_gateway() -> None:
    gateway_key = GATEWAY_KEY_FILE.read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        BASE_URL + "/v1/models",
        headers={"Authorization": f"Bearer {gateway_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    model_ids = [
        item.get("id") for item in result.get("data", []) if isinstance(item, dict)
    ]
    if not any(model_id and model_id.startswith("cptr/") for model_id in model_ids):
        raise RuntimeError("Computer gateway did not publish the LCDash workspace")
    print("Computer gateway workspace discovery: OK")


def validate_read_only_agent_task() -> None:
    gateway_key = GATEWAY_KEY_FILE.read_text(encoding="utf-8").strip()
    auth_headers = {
        "Authorization": f"Bearer {gateway_key}",
        "Content-Type": "application/json",
    }
    model_request = urllib.request.Request(
        BASE_URL + "/v1/models", headers=auth_headers
    )
    with urllib.request.urlopen(model_request, timeout=30) as response:
        available = json.loads(response.read())
    model_id = next(
        (
            item.get("id")
            for item in available.get("data", [])
            if isinstance(item, dict) and str(item.get("id", "")).startswith("cptr/")
        ),
        None,
    )
    if not model_id:
        raise RuntimeError("Computer workspace model is unavailable for validation")

    expected_headings = [
        "# LCDash Local Agent Charter",
        "## Mandatory local-agent execution protocol",
    ]
    payload = json.dumps(
        {
            "model": model_id,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Read AGENTS.md and return only these two heading lines in "
                        "their original order: the document title and the mandatory "
                        "local-agent execution protocol heading. Do not modify files "
                        "and do not perform any other task."
                    ),
                }
            ],
        }
    ).encode("utf-8")
    chat_request = urllib.request.Request(
        BASE_URL + "/v1/chat/completions",
        data=payload,
        method="POST",
        headers=auth_headers,
    )
    with urllib.request.urlopen(chat_request, timeout=240) as response:
        result = json.loads(response.read())
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    rendered_content = str(content)
    if not all(heading in rendered_content for heading in expected_headings):
        raise RuntimeError(
            "Computer agent did not return the expected project instruction headings"
        )
    print("Computer project-instruction read-only task: OK")


def configure_open_webui(password: str) -> None:
    gateway_key = GATEWAY_KEY_FILE.read_text(encoding="utf-8").strip()
    client = ComputerClient(OPEN_WEBUI_BASE_URL)
    status, signin = client.call(
        "/api/v1/auths/signin",
        "POST",
        {"email": "admin@logan911.local", "password": password},
    )
    if status != 200 or not isinstance(signin, dict) or not signin.get("token"):
        raise RuntimeError("Open WebUI administrator login failed")
    client.default_headers["Authorization"] = f"Bearer {signin['token']}"

    status, config = client.call("/openai/config")
    if status != 200 or not isinstance(config, dict):
        raise RuntimeError("Open WebUI connection configuration could not be read")

    target_url = "http://open-webui-computer:8000/v1"
    base_urls = list(config.get("OPENAI_API_BASE_URLS") or [])
    api_keys = list(config.get("OPENAI_API_KEYS") or [])
    api_configs = dict(config.get("OPENAI_API_CONFIGS") or {})

    if target_url in base_urls:
        index = base_urls.index(target_url)
    else:
        index = len(base_urls)
        base_urls.append(target_url)

    while len(api_keys) < len(base_urls):
        api_keys.append("")
    api_keys[index] = gateway_key

    connection_config = dict(api_configs.get(str(index)) or {})
    connection_config.update(
        {
            "enable": True,
            "connection_type": "local",
            "prefix_id": "",
            "model_ids": [],
            "headers": {
                "X-OpenWebUI-Chat-Id": "{{CHAT_ID}}",
                "X-OpenWebUI-Message-Id": "{{MESSAGE_ID}}",
                "X-OpenWebUI-User-Message-Id": "{{USER_MESSAGE_ID}}",
                "X-OpenWebUI-User-Message-Parent-Id": "{{USER_MESSAGE_PARENT_ID}}",
                "X-OpenWebUI-Task": "{{TASK}}",
            },
        }
    )
    api_configs[str(index)] = connection_config

    status, _ = client.call(
        "/openai/config/update",
        "POST",
        {
            "ENABLE_OPENAI_API": True,
            "OPENAI_API_BASE_URLS": base_urls,
            "OPENAI_API_KEYS": api_keys,
            "OPENAI_API_CONFIGS": api_configs,
        },
    )
    if status != 200:
        raise RuntimeError(f"Open WebUI connection update failed with status {status}")
    print("Open WebUI Computer gateway connection: OK")

    status, models = client.call("/api/models")
    if status != 200 or not isinstance(models, dict):
        raise RuntimeError("Open WebUI model discovery failed")
    model_ids = [
        item.get("id") for item in models.get("data", []) if isinstance(item, dict)
    ]
    if not any(model_id and model_id.startswith("cptr/") for model_id in model_ids):
        raise RuntimeError("LCDash Computer workspace is not visible in Open WebUI")
    print("Open WebUI Computer workspace discovery: OK")


def main() -> int:
    if not ADMIN_PASSWORD_FILE.is_file():
        raise RuntimeError("Existing Open WebUI administrator password record is missing")
    password = ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
    if not password:
        raise RuntimeError("Existing Open WebUI administrator password record is empty")

    client = ComputerClient()
    login_or_setup(client, password)
    ensure_ollama_connection(client)
    report_qwen_model_ids(client)
    if "--configure-opencode" in sys.argv[1:]:
        ensure_opencode_profile(client)
    ensure_workspace(client)
    ensure_gateway_key(client, rotate="--rotate-gateway-key" in sys.argv[1:])
    validate_gateway()
    configure_open_webui(password)
    if "--validate-agent" in sys.argv[1:]:
        validate_read_only_agent_task()
    print("Open WebUI Computer pilot initialization: complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Open WebUI Computer pilot initialization failed: {error}", file=sys.stderr)
        raise SystemExit(1)
