"""WebUI 包。

router 负责挂载，app 定义接口，logic 承载编排，
persistence 隔离配置写回的框架内部依赖。
"""

from .router import WebUIRouter

__all__ = ["WebUIRouter"]
