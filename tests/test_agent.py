"""Tests for the agent API: JSON contract, error codes and the MCP server.

The contract tests need no game; the data ones skip when Warcraft III is absent.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wc3studio import agent as agent_module
from wc3studio.agent import Api, capabilities


class TestContract(unittest.TestCase):
    def test_capabilities_manifest(self):
        manifest = capabilities()
        self.assertEqual(manifest["server"], "wc3studio")
        self.assertTrue(manifest["version"])
        names = [op["name"] for op in manifest["operations"]]
        self.assertGreaterEqual(len(names), 12)
        for expected in ("capabilities", "assets.search", "model.info", "model.render",
                         "model.export", "tables.query", "units.query", "ui.frame",
                         "audio.list", "bundle.build"):
            self.assertIn(expected, names)
        for op in manifest["operations"]:
            self.assertTrue(op["description"], op["name"])
            self.assertIsInstance(op["params"], dict)

    def test_unknown_operation(self):
        reply = Api().call({"op": "nope"})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "UNKNOWN_OP")
        self.assertTrue(any("known:" in hint for hint in reply["error"]["hints"]))

    def test_missing_operation(self):
        reply = Api().call({})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "BAD_REQUEST")

    def test_missing_required_parameter(self):
        reply = Api().call({"op": "model.info"})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "BAD_PARAM")
        self.assertIn("model", reply["error"]["message"])

    def test_unknown_parameter_is_reported_with_the_allowed_list(self):
        reply = Api().call({"op": "model.info", "model": "sheep", "colour": "red"})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "BAD_PARAM")
        self.assertIn("colour", reply["error"]["message"])
        self.assertTrue(any("accepts" in hint for hint in reply["error"]["hints"]))

    def test_every_reply_carries_ok(self):
        for request in ({"op": "capabilities"}, {"op": "nope"}, {}):
            reply = Api().call(request)
            self.assertIn("ok", reply)
            json.dumps(reply)  # must always be serialisable


class TestCli(unittest.TestCase):
    def _run(self, argv):
        """dispatch_cli writes JSON to stdout; feed it a fake stdin when needed."""
        buffer = io.StringIO()
        previous = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            with redirect_stdout(buffer):
                code = agent_module.dispatch_cli(argv)
        finally:
            sys.stdin = previous
        text = buffer.getvalue().strip()
        return code, (json.loads(text) if text else None)

    def test_capabilities_flag(self):
        code, payload = self._run(["--capabilities"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["server"], "wc3studio")

    def test_unknown_op_exit_code(self):
        code, payload = self._run(["--call", "nope"])
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"]["code"], "UNKNOWN_OP")

    def test_call_with_params(self):
        code, payload = self._run(["--call", "model.info", "--params", '{"model": "sheep"}'])
        self.assertIn(code, (0, 3, 4))
        self.assertEqual(payload["op"], "model.info")


class TestMcp(unittest.TestCase):
    def test_handshake_and_tools(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "capabilities", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        ]
        stdin = io.StringIO("\n".join(json.dumps(item) for item in requests) + "\n")
        buffer = io.StringIO()
        previous = sys.stdin
        sys.stdin = stdin
        try:
            with redirect_stdout(buffer):
                agent_module.serve_mcp()
        finally:
            sys.stdin = previous

        replies = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(replies), 4, "the notification must not be answered")
        by_id = {reply["id"]: reply for reply in replies}
        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "wc3studio")
        tools = by_id[2]["result"]["tools"]
        self.assertTrue(any(tool["name"] == "model.info" for tool in tools))
        info = next(tool for tool in tools if tool["name"] == "model.info")
        self.assertIn("model", info["inputSchema"]["properties"])
        self.assertEqual(info["inputSchema"]["required"], ["model"])
        text = by_id[3]["result"]["content"][0]["text"]
        self.assertIn("wc3studio", text)
        self.assertEqual(by_id[4]["result"], {})


# --------------------------------------------------------------------------- real game

def _api_or_skip() -> Api:
    from wc3studio.stormlib import StormLibError
    api = Api()
    try:
        api.call({"op": "doctor"})
        session = agent_module._session(api)
        if not session.names():
            raise StormLibError("empty")
    except (StormLibError, agent_module.ApiError) as exc:
        api.close()
        raise unittest.SkipTest(f"no Warcraft III installation: {exc}") from exc
    return api


class TestRealData(unittest.TestCase):
    def setUp(self):
        self.api = _api_or_skip()

    def tearDown(self):
        self.api.close()

    def test_model_info(self):
        reply = self.api.call({"op": "model.info", "model": "sheep"})
        self.assertTrue(reply["ok"], reply)
        data = reply["data"]
        self.assertTrue(data["path"].lower().endswith("sheep.mdx"))
        self.assertGreater(data["triangles"], 10)
        self.assertTrue(data["sequences"])
        low, high = data["bounds"]["min"], data["bounds"]["max"]
        for axis in range(3):
            self.assertLess(low[axis], high[axis], "bounds must be ordered")

    def test_assets_search_finds_the_footman(self):
        reply = self.api.call({"op": "assets.search", "query": "footman", "limit": 10})
        self.assertTrue(reply["ok"], reply)
        paths = [match["path"] for match in reply["data"]["matches"]]
        self.assertTrue(any(path.lower().endswith(r"units\human\footman\footman.mdx")
                            for path in paths))
        self.assertTrue(all(match["size"] > 0 for match in reply["data"]["matches"]))

    def test_units_query_returns_localised_names(self):
        reply = self.api.call({"op": "units.query", "filter": "hfoo", "limit": 1})
        self.assertTrue(reply["ok"], reply)
        columns = reply["data"]["columns"]
        row = reply["data"]["rows"][0]
        self.assertEqual(row[columns.index("id")], "hfoo")
        self.assertEqual(row[columns.index("hp")], "420")

    def test_tables_query_with_filter(self):
        reply = self.api.call({"op": "tables.query", "table": "UnitBalance",
                               "columns": ["unitBalanceID", "HP"], "where": {"HP": "420"},
                               "limit": 5})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["data"]["columns"], ["unitBalanceID", "HP"])
        self.assertTrue(all(row[1] == "420" for row in reply["data"]["rows"]))

    def test_tables_query_unknown_column_suggests_alternatives(self):
        reply = self.api.call({"op": "tables.query", "table": "UnitBalance",
                               "where": {"nope": "1"}})
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["error"]["code"], "BAD_PARAM")
        self.assertTrue(any("columns:" in hint for hint in reply["error"]["hints"]))

    def test_ui_frame(self):
        reply = self.api.call({"op": "ui.frame", "name": "StandardHeavyBackdropTemplate"})
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["data"]["kind"], "BACKDROP")
        self.assertIn("BackdropBackground", reply["data"]["properties"])

    def test_audio_inventory(self):
        reply = self.api.call({"op": "audio.list", "category": "music", "limit": 3})
        self.assertTrue(reply["ok"], reply)
        self.assertGreater(reply["data"]["total"], 10)
        self.assertTrue(all(item["category"] == "music" for item in reply["data"]["items"]))
        self.assertGreater(reply["data"]["categories"]["music"]["bytes"], 0)

    def test_model_render_writes_a_png(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sheep.png"
            reply = self.api.call({"op": "model.render", "model": "sheep",
                                   "out": str(target), "size": 128})
            self.assertTrue(reply["ok"], reply)
            self.assertTrue(target.is_file())
            self.assertGreater(reply["data"]["bytes"], 100)
            self.assertEqual((reply["data"]["width"], reply["data"]["height"]), (128, 128))


if __name__ == "__main__":
    unittest.main(verbosity=2)
