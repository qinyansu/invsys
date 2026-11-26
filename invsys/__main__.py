"""启动入口。

    python -m invsys --port 8000 --db invsys.db
"""

import argparse

from .api import create_server
from .config import Config
from .service import InventoryService


def main():
    parser = argparse.ArgumentParser(description="进销存后端服务")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="invsys.db")
    parser.add_argument("--secret", default=None)
    parser.add_argument("--token-ttl", type=int, default=3600)
    parser.add_argument("--rate-limit", type=int, default=240)
    parser.add_argument("--rate-window", type=int, default=60)
    args = parser.parse_args()

    config = Config(port=args.port, db_path=args.db, secret=args.secret,
                    token_ttl=args.token_ttl, rate_limit=args.rate_limit,
                    rate_window=args.rate_window)
    service = InventoryService(config)
    print(f"listening on http://127.0.0.1:{config.port}  (db={config.db_path})")
    print(f"schema: {service.schema_from} -> {service.schema_to}")
    print("默认账号：admin/admin123（管理员）、clerk/clerk123（业务员）")
    server = create_server(service, config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
