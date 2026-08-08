"""dashboard_server HTTP 层冒烟测试 + 批次1修复回归（404/400/403）。

用真实子进程起 dashboard_server，数据目录隔离到 tmp_path，
只测只读/幂等安全端点，绝不触碰真实 memory/ 文件或 projects.json。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def api_server(tmp_path_factory):
    """起真实 dashboard_server 子进程，数据目录隔离。返回 (base_url, tmp_data_dir)。"""
    tmp_path = tmp_path_factory.mktemp("api")
    # 预建空库：dashboard 用只读连接，DB 文件必须先存在
    sys.path.insert(0, str(SCRIPTS))
    from store import Store
    Store(db_path=str(tmp_path / "db" / "nailong.db"))

    port = _free_port()
    env = dict(os.environ)
    env["NAILONG_DATA_DIR"] = str(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "dashboard_server.py"), "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    # 等待就绪（最多 5s）
    for _ in range(50):
        try:
            with urllib.request.urlopen(base + "/api/feed", timeout=1):
                break
        except Exception:
            time.sleep(0.1)
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── 正常路径 ──────────────────────────────────────────────

def test_feed_returns_200_json(api_server):
    base = api_server
    with urllib.request.urlopen(base + "/api/feed", timeout=3) as r:
        assert r.status == 200
        data = json.loads(r.read().decode("utf-8"))
        assert "items" in data  # build_feed 的核心字段


def test_projects_returns_200_json(api_server):
    base = api_server
    with urllib.request.urlopen(base + "/api/projects", timeout=3) as r:
        assert r.status == 200
        json.loads(r.read().decode("utf-8"))


# ── 批次1修复回归：未知 /api/* → 404 JSON ─────────────────

def test_unknown_api_route_returns_404_json(api_server):
    base = api_server
    try:
        with urllib.request.urlopen(base + "/api/nope", timeout=3):
            assert False, "应返回 404 却得到 200"
    except urllib.error.HTTPError as e:
        assert e.code == 404
        body = json.loads(e.read().decode("utf-8"))
        assert "error" in body  # 是 JSON 而不是 SPA 的 HTML


# ── 批次1修复回归：坏 JSON → 400 而非 500 ─────────────────

def test_post_bad_json_returns_400(api_server):
    base = api_server
    req = urllib.request.Request(
        base + "/api/projects",
        data=b"this is not json",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3):
            assert False, "应返回 400 却得到 2xx"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        json.loads(e.read().decode("utf-8"))


def test_post_unknown_proposal_no_mutation(api_server):
    """合法 JSON 但提案不存在 → 400，不写任何文件。"""
    base = api_server
    req = urllib.request.Request(
        base + "/api/memory-proposals",
        data=json.dumps({"id": 999999, "action": "approve"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3):
            assert False, "应返回 400 却得到 2xx"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        assert "not found" in e.read().decode("utf-8").lower()


# ── 批次1修复回归：/assets/ 路径穿越 → 403 ────────────────

def test_asset_path_traversal_blocked(api_server):
    base = api_server
    # 用 http.client 直接发原始 path（不经过 urllib 的地址规范化），
    # 让服务端直面 /assets/../../... 穿越请求。
    import http.client
    from urllib.parse import urlsplit
    url = base + "/assets/../../../../../../../../etc/passwd"
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.netloc)
    conn.request("GET", parts.path)
    resp = conn.getresponse()
    try:
        assert resp.status == 403, f"穿越应被拦 403，得到 {resp.status}"
    finally:
        conn.close()


def test_asset_normal_file_still_served(api_server):
    """正常 assets 文件不受影响（用构建产物的一个文件验证存在性）。"""
    base = api_server
    dist = BASE / "dashboard" / "dist" / "assets"
    files = list(dist.glob("*.js"))
    if not files:
        pytest.skip("dist/assets 无 JS 文件，跳过")
    fname = files[0].name
    with urllib.request.urlopen(base + f"/assets/{fname}", timeout=3) as r:
        assert r.status == 200
        assert len(r.read()) > 100  # 返回了实际的 JS 内容（非空）
