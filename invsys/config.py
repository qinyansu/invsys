"""配置：命令行参数 > 环境变量 > 默认值。"""

import os


class Config:
    def __init__(self, port=8000, db_path="invsys.db", secret=None,
                 token_ttl=3600, rate_limit=240, rate_window=60, log_requests=True,
                 web_dir=None):
        self.port = port
        self.db_path = db_path
        self.secret = secret or os.environ.get("INVSYS_SECRET", "dev-secret-change-me")
        self.token_ttl = token_ttl
        self.rate_limit = rate_limit
        self.rate_window = rate_window
        self.log_requests = log_requests
        # 静态前端目录：invsys/config.py -> 上级目录/web
        self.web_dir = web_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
