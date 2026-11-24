import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import server as server_mod
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
        (self.root / "z_file.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "a_dir").mkdir()
        result = self.sources.source_read(str(self.file), 2, 2)
        self.assertEqual(result["lines"], [{"line": 2, "text": "Error: invalid cfg"}])
        self.assertEqual(result["totalLines"], 3)
        self.assertEqual(result["encoding"], "utf-8")
        self.assertTrue(result["hasMore"])

        listing = self.sources.source_list(str(self.root))
        self.assertEqual(listing["totalEntries"], 3)
        # Directories come before files, sorted alphabetically.
        self.assertEqual([item["name"] for item in listing["entries"]], ["a_dir", "flow.tcl", "z_file.py"])

        filtered = self.sources.source_list(str(self.root), pattern="*.tcl")
        self.assertEqual(filtered["totalEntries"], 1)
        self.assertEqual(filtered["entries"][0]["name"], "flow.tcl")

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
        self.assertEqual(self.sources.source_find(str(self.root), "escape")["matches"], [])

    def test_literal_search_limits_and_ignored_directory(self):
        (self.root / "query.py").write_text("needle.*\nneedle.*\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "secret.py").write_text("needle.*", encoding="utf-8")
        result = self.sources.source_search(str(self.root), "NEEDLE.*", False, 1, [".PY"])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["line"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["stopReason"], "result_limit")
        self.assertEqual(self.sources.source_search(str(self.root), "NEEDLE.*")["matches"], [])

    def test_find_and_search_path_pattern_and_path_matches(self):
        bscan_dir = self.root / "src" / "feature" / "bscan"
        bscan_dir.mkdir(parents=True)
        bscan_py = bscan_dir / "CFeatureBscan.py"
        bscan_py.write_text("class CFeatureBscan:\n    def placeIo(self):\n        pass\n", encoding="utf-8")
        # Compiled artifact should not appear in source_search pathMatches or consume scan budget.
        (bscan_dir / "CFeatureBscan.pyc").write_bytes(b"\x00\x01binary")
        (self.root / "src" / "core").mkdir(parents=True)
        sdb_py = self.root / "src" / "core" / "sdb.py"
        sdb_py.write_text("def set_obj_attribute(obj, key):\n    return obj[key]\n", encoding="utf-8")

        found = self.sources.source_find(str(self.root), "CFeatureBscan.py")
        self.assertEqual([item["path"] for item in found["matches"]], [str(bscan_py.resolve())])

        found_glob = self.sources.source_find(str(self.root), "*bscan/*.py")
        self.assertEqual([item["path"] for item in found_glob["matches"]], [str(bscan_py.resolve())])

        found_dirs = self.sources.source_find(str(self.root), "bscan", kind="directory")
        self.assertEqual([item["path"] for item in found_dirs["matches"]], [str(bscan_dir.resolve())])

        # Search narrowed by path_pattern.
        narrowed = self.sources.source_search(str(self.root), "placeIo", path_pattern="*Bscan*.py")
        self.assertEqual(len(narrowed["matches"]), 1)
        self.assertEqual(narrowed["matches"][0]["path"], str(bscan_py.resolve()))
        self.assertEqual(narrowed["matches"][0]["line"], 2)
        self.assertEqual(narrowed["scannedFiles"], 1)

        # Filename matches are surfaced in pathMatches without including .pyc binaries.
        by_class = self.sources.source_search(str(self.root), "CFeatureBscan")
        self.assertEqual(by_class["pathMatches"], [str(bscan_py.resolve())])

    def test_binary_files_do_not_consume_scan_budget(self):
        (self.root / "libnative.so").write_bytes(b"\x7fELF" + b"\x00" * 1024)
        (self.root / "blob.dat").write_bytes(b"header\x00payload" * 100)
        (self.root / "target.py").write_text("def placeIo():\n    return 42\n", encoding="utf-8")
        result = self.sources.source_search(str(self.root), "placeIo")
        self.assertEqual(len(result["matches"]), 1)
        self.assertFalse(result["truncated"])
        self.assertGreaterEqual(result["skippedFiles"], 2)
        # Only flow.tcl and target.py are counted as scanned text files.
        self.assertEqual(result["scannedFiles"], 2)

    def test_gb18030_comment_file_is_searchable_and_readable(self):
        legacy = self.root / "legacy.py"
        legacy.write_bytes("# 检查时钟端口\ndef set_obj_attribute():\n    pass\n".encode("gb18030"))
        search = self.sources.source_search(str(self.root), "set_obj_attribute")
        self.assertEqual(len(search["matches"]), 1)
        self.assertEqual(search["matches"][0]["line"], 2)
        read = self.sources.source_read(str(legacy), 1, 3)
        self.assertEqual(read["encoding"], "gb18030")
        self.assertIn("检查时钟端口", read["lines"][0]["text"])

    def test_primary_source_dirs_scanned_before_low_priority_dirs(self):
        # Put many files in regression/ (alphabetically before src/) and target file deep inside src/.
        reg_dir = self.root / "regression"
        reg_dir.mkdir()
        for i in range(40):
            (reg_dir / ("case_%03d.txt" % i)).write_text("dummy log %d\n" % i, encoding="utf-8")
        deep_dir = self.root / "src" / "a" / "b" / "c"
        deep_dir.mkdir(parents=True)
        target = deep_dir / "CFeatureBscan.py"
        target.write_text("def placeIo():\n    pass\n", encoding="utf-8")

        # Even with a tight file budget, src/a/b/c/CFeatureBscan.py is reached before regression/ drains budget.
        with mock.patch.object(server_mod, "SEARCH_MAX_FILES", 5):
            result = self.sources.source_search(str(self.root), "placeIo")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["path"], str(target.resolve()))
        self.assertTrue(result["truncated"])
        self.assertEqual(result["stopReason"], "scan_limit")
        self.assertIn("hint", result)

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
        self.assertEqual(len(result["result"]["tools"]), 5)
        tool_names = [item["name"] for item in result["result"]["tools"]]
        self.assertIn("source_find", tool_names)

        find_call = {"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {"name": "source_find", "arguments": {"path": str(self.root), "pattern": "flow.tcl"}}}
        _, result = self.request(port, find_call)
        self.assertEqual(result["result"]["structuredContent"]["matches"][0]["path"], str(self.file.resolve()))

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
