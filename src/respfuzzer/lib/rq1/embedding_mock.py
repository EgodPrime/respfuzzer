"""RQ1 F8：本地 mock embedding 服务（OpenAI 兼容 /v1/embeddings）。

无真实 embedding 服务时，把 config [embedding] 指向本服务即可走完整 HTTP 链路
（HTTPEmbeddingProvider → POST /v1/embeddings），用于 RAG 冒烟与测试。

运行（前台）：
    uv run python -m respfuzzer.lib.rq1.embedding_mock --port 8123

作为库使用（测试内嵌）：
    srv = EmbeddingMockServer(port=0)   # 0 → 随机端口
    srv.start()
    ... client = HTTPEmbeddingProvider(base_url=srv.base_url(), model_name="mock") ...
    srv.stop()

返回确定性 SHA-256 向量（与 ``FakeEmbeddingProvider._stable_vec`` 同算法），
保证「同文本同向量」可复现，且 mock 与 fake 结果一致便于交叉验证。
零第三方依赖（stdlib http.server + json，线程化支持并发请求）。
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from respfuzzer.lib.rq1.rag_index import _stable_vec  # 复用确定性向量

logger = logging.getLogger(__name__)

_DIM_VALIDATE = 64


class _EmbeddingHandler(BaseHTTPRequestHandler):
    """POST /v1/embeddings：OpenAI 兼容（{model, input} → {data:[{embedding}]}）。"""

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            inputs = body.get("input", [])
            if isinstance(inputs, str):
                inputs = [inputs]
            vecs = [_stable_vec(t) for t in inputs]
            data = [
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": [round(float(x), 6) for x in vec],
                }
                for i, vec in enumerate(vecs)
            ]
            payload = json.dumps(
                {
                    "object": "list",
                    "data": data,
                    "model": body.get("model", "mock-embedding"),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:  # 请求解析失败 → 400，不静默
            logger.debug("mock embeddings 请求失败: %s", exc)
            resp = json.dumps({"error": f"bad request: {exc}"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    def log_message(self, fmt: str, *args) -> None:
        logger.debug("mock server: %s", fmt % args)  # 不向 stderr 刷屏


class EmbeddingMockServer:
    """可编程的本地 mock embedding 服务（线程化）。"""

    def __init__(self, port: int = 0) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _EmbeddingHandler)
        self.port: int = int(self._httpd.server_address[1])
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def base_url(self) -> str:
        """给 HTTPEmbeddingProvider 的 base_url（含 /v1）。"""
        return f"http://127.0.0.1:{self.port}/v1"


def main() -> None:
    parser = argparse.ArgumentParser(description="RQ1 mock embedding 服务")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _EmbeddingHandler)
    print(
        f"mock embedding 服务已启动: http://{args.host}:{args.port}/v1/embeddings",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
