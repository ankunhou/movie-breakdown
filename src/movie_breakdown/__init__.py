"""电影剧本叙事结构与制片元素拆解。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("movie-breakdown")
except PackageNotFoundError:  # pragma: no cover - 仅用于未安装源码包的交互式导入
    __version__ = "0+unknown"
