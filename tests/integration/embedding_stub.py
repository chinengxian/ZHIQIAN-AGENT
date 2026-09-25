from http.server import ThreadingHTTPServer

from tests.test_knowledge_management_integration import LocalEmbeddingHandler


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8011), LocalEmbeddingHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
