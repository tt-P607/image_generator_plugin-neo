"""图片生成插件测试包初始化与动态导入支持。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]
PACKAGE_NAME = "image_generator_plugin_neo"

if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

if PACKAGE_NAME not in sys.modules:
    specification = importlib.util.spec_from_file_location(
        PACKAGE_NAME,
        PLUGIN_ROOT / "__init__.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("无法创建图片生成插件测试包")
    package = importlib.util.module_from_spec(specification)
    sys.modules[PACKAGE_NAME] = package
    specification.loader.exec_module(package)
