import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from vcfa_folder_replication.server import AppState, make_server

TOKEN = "testtoken"


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("VCFA_HOME", str(tmp_path))
    state = AppState()
    httpd = make_server(state, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    yield base
    httpd.shutdown()


def call(base, path, body=None, token=TOKEN, host=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-VCFA-Token": token} if token else {})
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_auth_required(srv):
    assert call(srv, "/api/state", token=None)[0] == 403
    assert call(srv, "/api/state", token="wrong")[0] == 403
    assert call(srv, "/api/state", host="evil.example.com")[0] == 403
    assert call(srv, "/api/state")[0] == 200


def test_index_needs_token(srv):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(srv + "/")
    assert e.value.code == 403
    assert b"vCenter Folder Replication" in urllib.request.urlopen(srv + "/?t=" + TOKEN).read()


def test_full_demo_flow_and_undo(srv):
    assert call(srv, "/api/demo", {})[1]["demo"] is True
    assert call(srv, "/api/capture", {"role": "source"})[1]["source"]["captured"]["folders"] > 20
    plan = call(srv, "/api/plan", {})[1]["plan"]
    assert plan["summary"]["create"] == 16
    # real run is refused without the typed confirmation
    code, body = call(srv, "/api/apply", {"dry_run": False, "confirm": "yes"})
    assert code == 400 and "REPLICATE" in body["error"]

    def wait():
        for _ in range(100):
            st = call(srv, "/api/state")[1]
            if st["job"] and st["job"]["state"] != "running":
                return st
            time.sleep(0.1)
        raise AssertionError("job did not finish")

    call(srv, "/api/apply", {"dry_run": True})
    st = wait()
    assert st["job"]["result"]["counts"]["would_create"] == 16 and st["plan"]["summary"]["create"] == 16
    call(srv, "/api/apply", {"dry_run": False, "confirm": "REPLICATE"})
    st = wait()
    assert st["job"]["result"]["counts"]["created"] == 16 and st["plan"]["summary"]["create"] == 0
    assert st["can_undo"]
    assert call(srv, "/api/undo", {"confirm": "nope"})[0] == 400
    call(srv, "/api/undo", {"confirm": "UNDO"})
    st = wait()
    assert st["job"]["result"]["counts"]["removed"] == 16 and st["plan"]["summary"]["create"] == 16


def test_exports_and_bad_input(srv):
    call(srv, "/api/demo", {})
    call(srv, "/api/capture", {"role": "source"})
    req = urllib.request.Request(srv + "/api/export?role=source&format=csv&t=" + TOKEN)
    assert urllib.request.urlopen(req).read().startswith(b"datacenter,type")
    assert call(srv, "/api/connect", {"role": "source", "host": "x"})[0] == 400  # demo blocks real connect
    call(srv, "/api/demo", {"enable": False})
    assert call(srv, "/api/load-capture", {"inventory": {"schema": 7}})[0] == 400
    assert call(srv, "/api/connect", {"role": "source", "host": "", "user": "", "password": ""})[0] == 400
