import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from server import SourceServer, Sources


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.file = self.root / "flow.tcl"
        self.file.write_text('# 中文\nError: invalid cfg\nputs done\n', encoding="utf-8")
        self.sources = Sources([self.root])

    def test_read_and_list(self):
        result = self.sources.source_read(str(self.file), 2, 2)
        self.assertEqual(result["lines"], [{"line": 2, "text": "Error: invalid cfg"}])
        self.assertEqual(result["totalLines"], 3)
        self.assertTrue(result["hasMore"])
        self.assertEqual(self.sources.source_list(str(self.root))["entries"][0]["name"], "flow.tcl")

    def test_boundary_and_symlink(self):
        outside = Path(self.temp.name) / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        for value in (str(outside), str(self.root / ".." / "secret.txt"), "flow.tcl"):
            with self.assertRaises(ValueError):
                self.sources.source_read(value)
        link = self.root / "escape.txt"
        try:
            os.symlink(outside, link)
        except OSError:
            return  # Windows may not grant symlink privileges; traversal was still checked.
        with self.assertRaises(ValueError):
            self.sources.source_read(str(link))
        self.assertEqual(self.sources.source_search(str(self.root), "secret")["matches"], [])

    def test_literal_search_limits_and_ignored_directory(self):
        (self.root / "query.py").write_text("needle.*\nneedle.*\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "secret.py").write_text("needle.*", encoding="utf-8")
        result = self.sources.source_search(str(self.root), "NEEDLE.*", False, 1, [".py"])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["line"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["stopReason"], "result_limit")
        self.assertEqual(self.sources.source_search(str(self.root), "NEEDLE.*")["matches"], [])

    def test_invalid_and_large_files(self):
        with self.assertRaises(ValueError):
            self.sources.source_read(str(self.file), 1, 401)
        binary = self.root / "binary"
        binary.write_bytes(b"a\x00b")
        with self.assertRaises(ValueError):
            self.sources.source_read(str(binary))
        large = self.root / "large"
        with large.open("wb") as handle:
            handle.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            self.sources.source_read(str(large))
        long_line = self.root / "long"
        long_line.write_text("x" * 70000, encoding="utf-8")
        result = self.sources.source_read(str(long_line))
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["lines"][0]["text"]), 64 * 1024)

    def start_http(self, token=""):
        server = SourceServer(("127.0.0.1", 0), [self.root], token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close)
        return server.server_address[1]

    def request(self, port, message=None, headers=None, method="POST", path="/mcp"):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            defaults = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            defaults.update(headers or {})
            connection.request(method, path, None if message is None else json.dumps(message), defaults)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    def test_http_protocol_and_tools(self):
        port = self.start_http()
        status, result = self.request(port, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        self.assertEqual(status, 200)
        self.assertEqual(result["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(self.request(port, {"jsonrpc": "2.0", "method": "notifications/initialized"})[0], 202)
        _, result = self.request(port, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(result["result"]["tools"]), 4)
        call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "source_read", "arguments": {"path": str(self.file), "start_line": 2, "end_line": 2}}}
        _, result = self.request(port, call)
        self.assertEqual(result["result"]["structuredContent"]["lines"][0]["text"], "Error: invalid cfg")
        call["params"]["arguments"]["path"] = self.temp.name
        self.assertTrue(self.request(port, call)[1]["result"]["isError"])
        call["params"]["name"] = "run_shell"
        self.assertEqual(self.request(port, call)[1]["error"]["code"], -32602)
        self.assertEqual(self.request(port, method="GET")[0], 405)
        self.assertEqual(self.request(port, method="GET", path="/healthz"), (200, {"status": "ok"}))

    def test_optional_token_origin_host_and_version(self):
        port = self.start_http("test-only-token")
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.request(port, ping)[0], 401)
        auth = {"Authorization": "Bearer test-only-token"}
        self.assertEqual(self.request(port, ping, auth)[0], 200)
        self.assertEqual(self.request(port, ping, dict(auth, Origin="https://example.com"))[0], 403)
        self.assertEqual(self.request(port, ping, dict(auth, Host="evil.example"))[0], 403)
        self.assertEqual(self.request(port, ping, dict(auth, **{"MCP-Protocol-Version": "unsupported"}))[0], 400)
        self.assertEqual(self.request(port, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "source_read", "arguments": {}}}, auth)[1]["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
