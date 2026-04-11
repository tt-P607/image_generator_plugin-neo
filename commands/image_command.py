"""图片生成命令组件。

包含文生图、图生图、Vibe 管理三个命令，使用 @cmd_route 实现路由。
"""

from __future__ import annotations

import random
from typing import Optional

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image, send_text
from src.app.plugin_system.base import BaseCommand, cmd_route
from src.core.components.types import PermissionLevel

from ..services.image_service import ImageGeneratorService
from ..utils.image_utils import ImageUtils

logger = get_logger("image_generator_plugin.command")


# ═════════════════════════════════════════════════════════════════════════
#  随机回复模板（防风控）
# ═════════════════════════════════════════════════════════════════════════

_last_used: dict[str, int] = {}


def pick(templates: list[str], key: str = "") -> str:
    """从模板列表中随机选择一个，避免与上次相同。"""
    if not templates:
        return ""
    if len(templates) == 1:
        return templates[0]
    last_idx = _last_used.get(key, -1)
    available = [i for i in range(len(templates)) if i != last_idx]
    new_idx = random.choice(available)
    if key:
        _last_used[key] = new_idx
    return templates[new_idx]


def humanize_error(error: str) -> str:
    """将技术性错误信息转换为自然语言。"""
    error_str = str(error).lower()
    if "429" in error_str or "too many" in error_str or "rate limit" in error_str:
        return "请求太频繁了，服务器让我歇一会儿呢"
    elif "443" in error_str or "ssl" in error_str or "certificate" in error_str:
        return "网络连接有点问题，可能是代理或者证书的事"
    elif "503" in error_str or "service unavailable" in error_str:
        return "服务器那边在维护呢，等会儿再试试吧"
    elif "502" in error_str or "bad gateway" in error_str:
        return "服务器那边出了点小状况，稍后再试试"
    elif "500" in error_str or "internal server" in error_str:
        return "服务器内部出了点问题，不是我的锅哦"
    elif "404" in error_str or "not found" in error_str:
        return "找不到资源呢，可能链接有问题"
    elif "401" in error_str or "unauthorized" in error_str:
        return "认证失败了，可能是 API 密钥的问题"
    elif "403" in error_str or "forbidden" in error_str:
        return "没有权限访问呢，被拒绝了"
    elif "timeout" in error_str or "timed out" in error_str:
        return "等太久了，网络超时了呢"
    elif "connection" in error_str or "connect" in error_str:
        return "网络连接出了问题，检查一下网络吧"
    elif "proxy" in error_str:
        return "代理那边好像有问题"
    elif "未初始化" in error_str or "not initialized" in error_str:
        return "服务还没准备好呢，稍等一下"
    elif len(error_str) > 50:
        return "遇到了一些技术问题，稍后再试试吧"
    return str(error)


# ── 回复模板 ──

MISSING_PROMPT_HINTS = [
    "呐呐，想让我画什么呀？比如：/nai_image draw sunset, mountains",
    "诶，你还没告诉我要画什么呢",
    "画什么好呢？快告诉我嘛",
    "嗯？光是喊我画图可不行哦，给个提示词吧",
    "哈？就这样让我画？说说要画什么嘛",
    "想让我画画？那得先告诉我画什么呀",
]

UNSUPPORTED_SIZE_HINTS = [
    "唔，{size} 这个尺寸人家画不了呢，试试：方图/横图/竖图",
    "诶？{size} 这个画幅有点奇怪哦，可以用：方图、横图、竖图",
    "{size}？这个尺寸我不太会呢，试试方图或横图或竖图？",
]

SIZE_NO_PROMPT_HINTS = [
    "诶，画幅选好了，但是...画什么呀？",
    "嗯嗯，画布准备好了，那...画什么呢？",
    "好嘞，尺寸 OK，但是提示词呢？",
]

START_DRAWING_HINTS: dict[str, list[str]] = {
    "方形": [
        "好哒，方形画布准备好啦，开始作画",
        "方形构图，交给我吧，马上开始",
        "方图马上来，稍等一下哦",
    ],
    "横向": [
        "好哒，横向画布准备好啦，开始作画",
        "横构图，很适合风景呢，开始画",
        "横图模式启动，马上开始哦",
    ],
    "竖向": [
        "好哒，竖向画布准备好啦，开始作画",
        "竖构图，很适合人物呢，开始画",
        "竖图模式启动，马上开始哦",
    ],
}

DRAW_SUCCESS_HINTS = [
    "锵锵，画好啦，怎么样怎么样？",
    "完成，看看效果如何，满意吗？",
    "画好了，希望你会喜欢哦",
    "噔噔噔噔，作品出炉啦",
    "搞定，这就是你要的图，喜欢吗？",
]

START_EDITING_HINTS = [
    "好，让我来调整一下这张图{strength}",
    "收到，开始修改图片{strength}",
    "OK，图片编辑中{strength}，稍等哦",
]

EDIT_SUCCESS_HINTS = [
    "锵锵，图片改好啦，怎么样？",
    "改好啦，看看效果如何",
    "完成，这样可以吗？满意吗？",
]

ERROR_HINTS = [
    "诶呀，出问题了，{error}",
    "唔...出错了呢，{error}",
    "不好意思呀，出了点问题，{error}",
]

GENERATE_ERROR_HINTS = [
    "呜呜，生成失败了，{error}",
    "诶呀，图片没画出来，{error}",
    "不好意思，出了点问题，{error}",
]


# ═════════════════════════════════════════════════════════════════════════
#  画幅/预设映射
# ═════════════════════════════════════════════════════════════════════════

SIZE_ALIASES: dict[str, tuple[int, int]] = {
    "方": (1024, 1024),
    "方图": (1024, 1024),
    "square": (1024, 1024),
    "横": (1216, 832),
    "横图": (1216, 832),
    "横版": (1216, 832),
    "landscape": (1216, 832),
    "竖": (832, 1216),
    "竖图": (832, 1216),
    "竖版": (832, 1216),
    "portrait": (832, 1216),
}

PRESETS: dict[str, dict[str, object]] = {
    "人物": {
        "size": (832, 1216),
        "prefix": "masterpiece, best quality, 1girl, ",
        "suffix": ", detailed, beautiful",
    },
    "风景": {
        "size": (1216, 832),
        "prefix": "masterpiece, landscape, scenery, ",
        "suffix": ", detailed, high resolution",
    },
    "头像": {
        "size": (1024, 1024),
        "prefix": "masterpiece, portrait, close-up, ",
        "suffix": ", detailed face, high quality",
    },
}


# ═════════════════════════════════════════════════════════════════════════
#  文生图命令
# ═════════════════════════════════════════════════════════════════════════


class ImageGeneratorCommand(BaseCommand):
    """文生图命令。

    用法：
      /nai_image draw <提示词>                 - 方图 (1024x1024)
      /nai_image draw 横图 <提示词>            - 横图 (1216x832)
      /nai_image draw 竖图 <提示词>            - 竖图 (832x1216)
      /nai_image draw <提示词> ---<负面词>     - 自定义负面提示词
    """

    command_name: str = "nai_image"
    command_description: str = "NovelAI 文生图 - 根据提示词生成图片"
    permission_level: PermissionLevel = PermissionLevel.OPERATOR

    def _get_service(self) -> ImageGeneratorService | None:
        """获取服务实例。"""
        return getattr(self.plugin, "image_service", None)

    async def execute(self, message_text: str) -> tuple[bool, str]:  # type: ignore[override]
        """入口路由：/nai_image draw <提示词> 或直接 /nai_image <提示词>。"""
        text = message_text.strip()
        if not text:
            await send_text(pick(MISSING_PROMPT_HINTS, "missing_prompt"), stream_id=self.stream_id)
            return False, "缺少提示词"
        parts = text.split(maxsplit=1)
        if parts[0].lower() == "draw":
            rest = parts[1] if len(parts) > 1 else ""
        else:
            rest = text  # 没有子命令，整段视为提示词
        return await self._do_draw(rest)

    async def _do_draw(self, raw_text: str) -> tuple[bool, str]:
        """文生图核心处理：[画幅] <提示词> [---负面词]"""
        all_args = raw_text.split() if raw_text.strip() else []
        if not all_args:
            await send_text(
                pick(MISSING_PROMPT_HINTS, "missing_prompt"),
                stream_id=self.stream_id,
            )
            return False, "缺少提示词参数"

        width, height = 1024, 1024
        prompt_start_idx = 0
        preset_prefix = ""
        preset_suffix = ""

        first_arg = all_args[0].lower()

        # 检查预设
        if first_arg in PRESETS:
            preset = PRESETS[first_arg]
            width, height = preset["size"]  # type: ignore[assignment]
            preset_prefix = str(preset["prefix"])
            preset_suffix = str(preset["suffix"])
            prompt_start_idx = 1
        # 检查画幅别名
        elif first_arg in SIZE_ALIASES:
            width, height = SIZE_ALIASES[first_arg]
            prompt_start_idx = 1
        # 检查传统画幅格式 (1024x1024)
        else:
            normalized = all_args[0].replace("×", "x").replace("*", "x").replace("X", "x")
            if "x" in normalized.lower():
                try:
                    w_str, h_str = normalized.lower().split("x")
                    w, h = int(w_str.strip()), int(h_str.strip())
                    if (w, h) in [(1216, 832), (832, 1216), (1024, 1024)]:
                        width, height = w, h
                        prompt_start_idx = 1
                    else:
                        await send_text(
                            pick(UNSUPPORTED_SIZE_HINTS, "unsupported_size").format(size=f"{w}x{h}"),
                            stream_id=self.stream_id,
                        )
                        return False, "画幅不支持"
                except (ValueError, IndexError):
                    pass

        # 提取提示词
        prompt_raw = " ".join(all_args[prompt_start_idx:])
        if not prompt_raw.strip():
            await send_text(
                pick(SIZE_NO_PROMPT_HINTS, "size_no_prompt"),
                stream_id=self.stream_id,
            )
            return False, "缺少提示词"

        # 解析正负面提示词
        if "---" in prompt_raw:
            parts = prompt_raw.split("---", 1)
            prompt = parts[0].strip()
            negative_prompt: str | None = parts[1].strip() if len(parts) > 1 else None
        else:
            prompt = prompt_raw.strip()
            negative_prompt = None

        # 应用预设
        if preset_prefix or preset_suffix:
            prompt = f"{preset_prefix}{prompt}{preset_suffix}".strip()

        try:
            service = self._get_service()
            if not service:
                raise RuntimeError("ImageGeneratorService 未初始化")

            size_hints = {(1024, 1024): "方形", (1216, 832): "横向", (832, 1216): "竖向"}
            size_hint = size_hints.get((width, height), "方形")
            await send_text(
                pick(START_DRAWING_HINTS.get(size_hint, START_DRAWING_HINTS["方形"]), f"start_{size_hint}"),
                stream_id=self.stream_id,
            )

            # 简化 user_id：命令场景下无法直接获取
            user_id = "command_user"

            success, message, image_path = await service.generate_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                user_id=user_id,
                width=width,
                height=height,
                is_img2img=False,
                from_command=True,
            )

            if success and image_path:
                ok, msg, img_b64 = ImageUtils.read_image_as_base64(image_path)
                if ok and img_b64:
                    await send_image(img_b64, stream_id=self.stream_id, reply_to=self.message_id or None)
                    await send_text(
                        pick(DRAW_SUCCESS_HINTS, "draw_success"),
                        stream_id=self.stream_id,
                    )
                    ImageUtils.cleanup_temp_file(image_path, keep_file=True)
                    return True, "图片生成成功"
                await send_text(
                    pick(ERROR_HINTS, "error").format(error=humanize_error(msg)),
                    stream_id=self.stream_id,
                )
                return False, msg
            await send_text(
                pick(GENERATE_ERROR_HINTS, "gen_error").format(error=humanize_error(message)),
                stream_id=self.stream_id,
            )
            return False, message

        except Exception as e:
            logger.error(f"执行 /nai_image draw 命令时出错: {e}", exc_info=True)
            await send_text(
                pick(GENERATE_ERROR_HINTS, "gen_error").format(error=humanize_error(str(e))),
                stream_id=self.stream_id,
            )
            return False, "命令执行异常"


# ═════════════════════════════════════════════════════════════════════════
#  图生图命令
# ═════════════════════════════════════════════════════════════════════════


class ImageEditCommand(BaseCommand):
    """图生图命令。

    用法：
      /nai_edit edit <提示词> [强度:0.1-0.99]   - 标准用法
      /nai_edit <提示词> [强度:0.1-0.99]        - 省略 edit 子命令
    需要引用一张图片。
    """

    command_name: str = "nai_edit"
    command_description: str = "NovelAI 图生图 - 基于引用图片进行编辑"
    permission_level: PermissionLevel = PermissionLevel.OPERATOR

    def _get_service(self) -> ImageGeneratorService | None:
        """获取服务实例。"""
        return getattr(self.plugin, "image_service", None)

    async def execute(self, message_text: str) -> tuple[bool, str]:  # type: ignore[override]
        """入口路由：/nai_edit edit <提示词> 或直接 /nai_edit <提示词>。"""
        text = message_text.strip()
        parts = text.split(maxsplit=1)
        if parts and parts[0].lower() == "edit":
            rest = parts[1] if len(parts) > 1 else ""
        else:
            rest = text
        return await self._do_edit(rest)

    async def _do_edit(self, raw_text: str) -> tuple[bool, str]:
        """图生图核心处理：<提示词> [强度]"""
        all_args = raw_text.split() if raw_text.strip() else []
        if not all_args:
            await send_text("想改图的话，先引用一张图片然后告诉我怎么改", stream_id=self.stream_id)
            return False, "缺少参数"

        prompt, strength = self._parse_edit_args(all_args)

        try:
            service = self._get_service()
            if not service:
                raise RuntimeError("ImageGeneratorService 未初始化")

            # TODO: 从消息引用中提取图片
            image_b64 = await self._extract_image_from_reply()
            if not image_b64:
                await send_text("我需要一张图片才能帮你修改呀，记得引用图片哦", stream_id=self.stream_id)
                return False, "未找到引用图片"

            strength_text = f"，修改强度 {strength}" if strength else ""
            await send_text(
                pick(START_EDITING_HINTS, "start_edit").format(strength=strength_text),
                stream_id=self.stream_id,
            )

            user_id = "command_user"

            success, message, image_path = await service.generate_image(
                prompt=prompt,
                user_id=user_id,
                width=1024,
                height=1024,
                is_img2img=True,
                img_base64=image_b64,
                strength=strength,
            )

            if success and image_path:
                ok, msg, img_b64 = ImageUtils.read_image_as_base64(image_path)
                if ok and img_b64:
                    await send_image(img_b64, stream_id=self.stream_id, reply_to=self.message_id or None)
                    await send_text(
                        pick(EDIT_SUCCESS_HINTS, "edit_success"),
                        stream_id=self.stream_id,
                    )
                    ImageUtils.cleanup_temp_file(image_path, keep_file=True)
                    return True, "图片编辑成功"
                await send_text(
                    pick(ERROR_HINTS, "error").format(error=humanize_error(msg)),
                    stream_id=self.stream_id,
                )
                return False, msg
            await send_text(
                pick(GENERATE_ERROR_HINTS, "gen_error").format(error=humanize_error(message)),
                stream_id=self.stream_id,
            )
            return False, message

        except Exception as e:
            logger.error(f"执行 /nai_edit 命令时出错: {e}", exc_info=True)
            await send_text(
                pick(ERROR_HINTS, "error").format(error=humanize_error(str(e))),
                stream_id=self.stream_id,
            )
            return False, "命令执行异常"

    def _parse_edit_args(self, args: list[str]) -> tuple[str, Optional[float]]:
        """解析图生图命令参数。

        Returns:
            (提示词, 强度值或 None)
        """
        prompt_parts: list[str] = []
        strength: Optional[float] = None

        for arg in args:
            try:
                val = float(arg)
                if 0.1 <= val <= 0.99:
                    strength = val
                else:
                    prompt_parts.append(arg)
            except ValueError:
                prompt_parts.append(arg)

        prompt = " ".join(prompt_parts) or "masterpiece, best quality"
        return prompt, strength

    async def _extract_image_from_reply(self) -> Optional[str]:
        """从引用消息中提取图片的 base64 编码。

        TODO: 需要根据框架实际的消息结构来实现
        """
        logger.warning("图生图功能暂未实现，需要框架支持获取引用消息中的图片")
        return None


# ═════════════════════════════════════════════════════════════════════════
#  Vibe 管理命令
# ═════════════════════════════════════════════════════════════════════════


class VibeManagementCommand(BaseCommand):
    """Vibe 参考图管理命令。

    用法：
      /nai_vibe list              - 查看素材库
      /nai_vibe add <文件名>      - 加载素材
      /nai_vibe status            - 当前 Vibe 设置
      /nai_vibe clear             - 清空 Vibe
      /nai_vibe info              - 查询账号信息
    """

    command_name: str = "nai_vibe"
    command_description: str = "Vibe 参考图管理"
    permission_level: PermissionLevel = PermissionLevel.OPERATOR

    def _get_service(self) -> ImageGeneratorService | None:
        """获取服务实例。"""
        return getattr(self.plugin, "image_service", None)

    @cmd_route("list")
    async def handle_list(self) -> tuple[bool, str]:
        """列出素材库文件。"""
        service = self._get_service()
        if not service:
            return False, "服务未初始化"

        success, message = service.list_vibe_files()
        await send_text(message, stream_id=self.stream_id)
        return success, message

    @cmd_route("add")
    async def handle_add(self, filename: str) -> tuple[bool, str]:
        """添加 Vibe 素材：/nai_vibe add <文件名>（含空格的文件名请用引号括起）"""
        if not filename:
            await send_text("请提供文件名呀", stream_id=self.stream_id)
            return False, "缺少文件名参数"

        service = self._get_service()
        if not service:
            return False, "服务未初始化"

        file_name = filename
        user_id = "command_user"

        success, message = await service.load_vibe_from_file(user_id, file_name)
        await send_text(message, stream_id=self.stream_id)
        return success, message

    @cmd_route("status")
    async def handle_status(self) -> tuple[bool, str]:
        """查看当前 Vibe 设置。"""
        service = self._get_service()
        if not service:
            return False, "服务未初始化"

        user_id = "command_user"
        message = service.get_vibe_status(user_id)
        await send_text(message, stream_id=self.stream_id)
        return True, message

    @cmd_route("clear")
    async def handle_clear(self) -> tuple[bool, str]:
        """清空所有 Vibe。"""
        service = self._get_service()
        if not service:
            return False, "服务未初始化"

        user_id = "command_user"
        message = service.clear_vibes(user_id)
        await send_text(message, stream_id=self.stream_id)
        return True, message

    @cmd_route("info")
    async def handle_info(self) -> tuple[bool, str]:
        """查询账号信息。"""
        service = self._get_service()
        if not service:
            return False, "服务未初始化"

        success, message = await service.get_user_info()
        await send_text(message, stream_id=self.stream_id)
        return success, message
