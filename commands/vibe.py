"""Vibe 参考图管理命令。"""

from __future__ import annotations

from src.app.plugin_system.base import cmd_route

from .base import BaseImageCommand


class VibeManagementCommand(BaseImageCommand):
    """Vibe 参考图管理命令。

    用法：
      /nai_vibe list           - 查看素材库
      /nai_vibe add <文件名>   - 加载素材
      /nai_vibe status         - 查看当前 Vibe
      /nai_vibe clear          - 清空 Vibe
      /nai_vibe info           - 查询账号信息
      /风格 列表/添加/状态/清空/账号 - 中文别名
    """

    name: str = "nai_vibe"
    description: str = "Vibe 参考图管理"
    command_aliases: list[str] = ["风格"]

    @cmd_route("list")
    async def handle_list(self) -> tuple[bool, str]:
        """列出素材库文件。"""

        engine = self.engine
        if engine is None:
            return False, "服务未初始化"

        message = engine.list_vibe_library()
        await self.reply(message)
        return True, message

    @cmd_route("add")
    async def handle_add(self, filename: str) -> tuple[bool, str]:
        """加载素材：``/nai_vibe add <文件名>``（含空格的文件名请用引号括起）。"""

        engine = self.engine
        if engine is None:
            return False, "服务未初始化"
        if not filename:
            await self.reply("请提供文件名呀")
            return False, "缺少文件名参数"

        success, message = await engine.load_user_vibe(self.user_scope, filename)
        await self.reply(message)
        return success, message

    @cmd_route("status")
    async def handle_status(self) -> tuple[bool, str]:
        """查看当前已加载的 Vibe。"""

        engine = self.engine
        if engine is None:
            return False, "服务未初始化"

        message = engine.get_user_vibe_status(self.user_scope)
        await self.reply(message)
        return True, message

    @cmd_route("clear")
    async def handle_clear(self) -> tuple[bool, str]:
        """清空已加载的 Vibe。"""

        engine = self.engine
        if engine is None:
            return False, "服务未初始化"

        message = engine.clear_user_vibes(self.user_scope)
        await self.reply(message)
        return True, message

    @cmd_route("info")
    async def handle_info(self) -> tuple[bool, str]:
        """查询账号订阅信息。"""

        engine = self.engine
        if engine is None:
            return False, "服务未初始化"

        success, message = await engine.get_user_info()
        await self.reply(message)
        return success, message

    @cmd_route("列表")
    async def handle_list_cn(self) -> tuple[bool, str]:
        """列出素材库文件（中文别名）。"""

        return await self.handle_list()

    @cmd_route("添加")
    async def handle_add_cn(self, filename: str) -> tuple[bool, str]:
        """加载素材（中文别名）。"""

        return await self.handle_add(filename)

    @cmd_route("状态")
    async def handle_status_cn(self) -> tuple[bool, str]:
        """查看当前已加载的 Vibe（中文别名）。"""

        return await self.handle_status()

    @cmd_route("清空")
    async def handle_clear_cn(self) -> tuple[bool, str]:
        """清空已加载的 Vibe（中文别名）。"""

        return await self.handle_clear()

    @cmd_route("账号")
    async def handle_info_cn(self) -> tuple[bool, str]:
        """查询账号订阅信息（中文别名）。"""

        return await self.handle_info()
