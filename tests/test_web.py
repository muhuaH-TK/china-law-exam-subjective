from http.client import HTTPConnection
import json
import re
from pathlib import Path
import tempfile
import threading
import unittest

from lawstudy.web import make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        temporary_root=Path(__file__).resolve().parent/".tmp"
        temporary_root.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=temporary_root)
        assert Path(self.tmp.name).resolve().is_relative_to(temporary_root.resolve())
        self.server=make_server(Path(self.tmp.name)/"web",0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()

    def request(self,method,path,body=None,headers=None):
        connection=HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse();result=(response.status,response.read(),dict(response.getheaders()))
        connection.close();return result

    def test_local_page_assets_and_protected_api(self):
        status,html,headers=self.request("GET","/")
        self.assertEqual(status,200)
        self.assertIn("frame-ancestors 'none'",headers["Content-Security-Policy"])
        token=re.search(rb'name="app-token" content="([^"]+)"',html)[1].decode()
        self.assertEqual(self.request("GET","/api/state")[0],403)
        status,raw,_=self.request("GET","/api/state",headers={"X-App-Token":token})
        self.assertEqual(status,200)
        state=json.loads(raw)
        self.assertEqual(state["profile"]["materials"],[])
        self.assertEqual(self.request("GET","/app.js")[0],200)
        self.assertEqual(self.request("GET","/../../instance.json")[0],404)
        self.assertEqual(self.request("GET","/",headers={"Host":"malicious.example"})[0],403)
        self.assertEqual(self.request("POST","/api/config",body="{}",headers={"X-App-Token":token,"Origin":"https://outside.example","Content-Type":"application/json"})[0],403)

    def test_configuration_round_trip_and_stale_conflict(self):
        _,html,_=self.request("GET","/")
        token=re.search(rb'name="app-token" content="([^"]+)"',html)[1].decode()
        headers={"X-App-Token":token,"Content-Type":"application/json"}
        _,raw,_=self.request("GET","/api/state",headers=headers)
        version=json.loads(raw)["version"]
        body=json.dumps({"version":version,"config":{"year":2027,"participation":"participating","stage_override":3}})
        status,raw,_=self.request("POST","/api/config",body,headers)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(raw)["state"]["plan"]["case_target"],5)
        self.assertEqual(self.request("POST","/api/config",body,headers)[0],409)

    def test_new_operations_routes_and_client_files_do_not_enable_sources(self):
        _, html, _ = self.request("GET", "/")
        token = re.search(rb'name="app-token" content="([^"]+)"', html)[1].decode()
        headers = {"X-App-Token": token, "Content-Type": "application/json"}
        _, raw, _ = self.request("GET", "/api/state", headers=headers)
        version = json.loads(raw)["version"]
        body = json.dumps({"version": version, "operation": "source", "payload": {"action": "presets", "payload": {}}})
        status, raw, _ = self.request("POST", "/api/operate", body, headers)
        self.assertEqual(status, 200)
        state = json.loads(raw)["state"]
        self.assertTrue(all(not s["requested"] for s in state["profile"]["sources"]))
        self.assertEqual(self.request("POST", "/api/operate", body, headers)[0], 409)
        status, raw, _ = self.request("POST", "/api/integration", "{}", headers)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["result"]["installed"])
        status, raw, _ = self.request("POST", "/api/monitor", "{}", headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["result"]["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
