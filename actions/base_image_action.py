"""图片 Action 基类。

为图片生成相关的 Action 提供通用功能：
- 获取服务实例
- 生成并发送图片的统一封装
- 画幅解析
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.api.send_api import send_image
from src.app.plugin_system.base import BaseAction

from ..image_source import extract_image_from_stream
from ..utils.image_utils import ImageUtils

if TYPE_CHECKING:
    from ..services.image_service import ImageGeneratorService

logger = get_logger("image_generator_plugin.base_image_action")


class BaseImageAction(BaseAction):
    """图片 Action 基类。

    封装图片生成和发送的通用逻辑。
    """

    # 由插件 on_plugin_loaded 注入，供 to_schema() 动态拼入参数描述
    _preset_negative_prompt: str = ""

    @classmethod
    def to_schema(cls) -> dict[str, Any]:
        """生成 LLM Tool Schema，动态注入预设负面提示词说明。"""
        schema = super().to_schema()
        props = (
            schema.get("function", {})
            .get("parameters", {})
            .get("properties", {})
        )

        # 注入负面提示词说明
        if cls._preset_negative_prompt:
            if "negative_prompt" in props:
                props["negative_prompt"]["description"] = (
                    f"场景专属额外排除词，英文逗号分隔。"
                    f"系统已内置：{cls._preset_negative_prompt}。"
                    f"此处只填本次图片特有的排除内容。"
                )

        return schema


    def get_service(self) -> Optional["ImageGeneratorService"]:
        """获取图片生成服务实例。

        Returns:
            服务实例或 None
        """
        service = getattr(self.plugin, "image_service", None)
        if not service:
            logger.error("无法获取图片生成服务")
        return service

    async def _extract_image_from_reply(self) -> Optional[str]:
        """通过共享图片来源解析器获取当前流最近图片。"""

        return await extract_image_from_stream(self.chat_stream)

    def _load_image_by_filename(self, filename: str) -> Optional[str]:
        """从 temp_images 目录按文件名加载 Bot 生成的图片。

        支持带扩展名和不带扩展名的文件名查找。

        Args:
            filename: 图片文件名（如 "my_drawing.png" 或 "my_drawing"）

        Returns:
            图片 base64 字符串，未找到时返回 None
        """
        service = self.get_service()
        if not service:
            return None

        import re

        # 规范化文件名：只保留英文/数字/下划线/连字符/点
        safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', filename.strip())
        if not safe_name:
            return None

        # 确保以 .png 结尾
        if not safe_name.lower().endswith(".png"):
            safe_name = safe_name + ".png"

        # 在 temp_images 目录中查找
        for search_dir in [service.temp_dir, service.command_images_dir]:
            filepath = search_dir / safe_name
            if filepath.exists():
                from ..utils.image_utils import ImageUtils
                success, _, img_b64 = ImageUtils.read_image_as_base64(str(filepath))
                if success and img_b64:
                    logger.info(f"通过文件名加载图片: {filepath}")
                    return img_b64

        logger.warning(f"文件名 '{filename}' 在 temp_images / command_images 中未找到")
        return None

    async def generate_and_send_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        success_message: str = "[内部：已发送图片]",
        error_prefix: str = "生成失败",
        selected_vibe_names: Optional[list[str]] = None,
        character_prompts: Optional[list[dict[str, Any]]] = None,
        reference_images: Optional[list[dict[str, Any]]] = None,
        output_filename: Optional[str] = None,
    ) -> tuple[bool, str]:
        """生成图片并发送（统一封装方法）。

        Args:
            prompt: 图片生成提示词
            negative_prompt: 负面提示词（可选）
            width: 图片宽度
            height: 图片高度
            success_message: 成功时返回的消息
            error_prefix: 错误消息前缀
            selected_vibe_names: LLM 选择的可选 Vibe 名称列表
            character_prompts: 多人物列表（仅 V4 系列模型支持），格式见 service.generate_image
            output_filename: 自定义输出文件名（不含扩展名），设置后图片以此名保存并在返回值中包含文件名

        Returns:
            (是否成功, 消息)
        """
        service = self.get_service()
        if not service:
            return False, "图片生成服务不可用"

        # 从 chat_stream 获取用户信息

        chat_context = getattr(self.chat_stream, "context", None)
        user_id = chat_context.triggering_user_id if chat_context else ""
        extra_data = getattr(self.chat_stream, "extra", {})
        group_id = extra_data.get("group_id")

        logger.info(f"生成图片 - 提示词: {prompt}")

        task_state = {"cancelled": False}

        async def _core_task() -> tuple[bool, str]:
            try:
                success, msg, image_path = await service.generate_image(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    user_id=str(user_id),
                    group_id=str(group_id) if group_id else None,
                    selected_vibe_names=selected_vibe_names,
                    character_prompts=character_prompts,
                    reference_images=reference_images,
                )

                if success and image_path:
                    from pathlib import Path as _Path

                    if output_filename:
                        import re

                        safe_stem = re.sub(
                            r"[^a-zA-Z0-9_-]",
                            "_",
                            output_filename.strip(),
                        ).strip("_")
                        if safe_stem:
                            old_path = _Path(image_path)
                            new_path = old_path.parent / f"{safe_stem}.png"
                            suffix = 2
                            while new_path.exists():
                                new_path = old_path.parent / f"{safe_stem}_{suffix}.png"
                                suffix += 1
                            try:
                                old_path.rename(new_path)
                                image_path = str(new_path)
                                logger.info(f"图片已重命名为: {new_path}")
                            except OSError as error:
                                logger.warning(f"重命名图片失败，使用原始文件名: {error}")

                    success_result, image_msg = await self.read_and_send_image(
                        image_path,
                        success_message=success_message,
                        keep_file=True,
                    )
                    # 始终在返回消息中包含实际文件名，供 LLM 后续引用
                    if success_result:
                        actual_filename = _Path(image_path).name
                        return True, f"{success_message}（文件名: {actual_filename}）"
                    return success_result, image_msg

                # Failed generate
                logger.error(f"图片生成失败: {msg}")
                err_msg = f"{error_prefix}: {msg}"
                if task_state["cancelled"]:
                    from src.app.plugin_system.api.send_api import send_text
                    await send_text(err_msg, stream_id=self.chat_stream.stream_id)  # 后台补偿发送错误给用户
                return False, err_msg

            except Exception as e:
                logger.error(f"生图动作后台异常: {e}", exc_info=True)
                err_msg = f"发生异常: {e}"
                if task_state["cancelled"]:
                    from src.app.plugin_system.api.send_api import send_text
                    await send_text(f"{error_prefix}: {err_msg}", stream_id=self.chat_stream.stream_id)
                return False, f"{error_prefix}: {e}"

        from src.kernel.concurrency import get_task_manager
        import asyncio

        tm = get_task_manager()
        task_info = tm.create_task(
            _core_task(),
            name=f"draw_action_{user_id}",
            metadata={
                "plugin": "image_generator_plugin-neo",
                "purpose": "action_draw",
                "stream_id": self.chat_stream.stream_id,
            },
        )
        register_task = getattr(self.plugin, "register_background_task", None)
        discard_task = getattr(self.plugin, "discard_background_task", None)
        if callable(register_task):
            register_task(task_info.task_id)
        if task_info.task is not None and callable(discard_task):
            task_info.task.add_done_callback(
                lambda _task, task_id=task_info.task_id: discard_task(task_id)
            )

        if task_info.task is None:
            return False, "后台生图任务创建失败"

        try:
            return await asyncio.shield(task_info.task)
        except asyncio.CancelledError:
            task_state["cancelled"] = True
            logger.warning("生成图片(_core_task)等候被取消，进入后台保护执行直至发放结果。")
            raise

    async def read_and_send_image(
        self,
        image_path: str,
        success_message: str = "[内部：已发送图片]",
        keep_file: bool = True,
    ) -> tuple[bool, str]:
        """读取图片文件并发送。

        Args:
            image_path: 图片文件路径
            success_message: 成功时返回的消息
            keep_file: 是否保留临时文件

        Returns:
            (是否成功, 消息)
        """
        strip_metadata = False
        config = getattr(self.plugin, "config", None)
        if config is not None:
            strip_metadata = getattr(config.generation, "strip_metadata_action", False)

        success, msg, img_base64 = ImageUtils.read_image_as_base64(
            image_path, strip_metadata=strip_metadata
        )

        if not success or not img_base64:
            return False, msg

        try:
            await send_image(img_base64, stream_id=self.chat_stream.stream_id)
            logger.info("图片已发送")
            ImageUtils.cleanup_temp_file(image_path, keep_file=True)
            return True, success_message
        except Exception as e:
            logger.error(f"发送图片失败: {e}", exc_info=True)
            return False, f"发送图片失败: {e}"

    def _parse_resolution(self, resolution: str, default: str = "1024x1024") -> tuple[int, int]:
        """解析画幅字符串为宽高，失败时依次回退到 service 配置的 resolution，最后是 1024x1024。

        Args:
            resolution: 画幅字符串，如 '1216x832'
            default: 方法级默认画幅（action 特有的偏好，如 selfie 偏好 '832x1216'）

        Returns:
            (width, height)
        """
        valid_sizes = {(1216, 832), (832, 1216), (1024, 1024)}

        def _try_parse(s: str) -> tuple[int, int] | None:
            try:
                w_str, h_str = s.lower().split("x")
                w, h = int(w_str.strip()), int(h_str.strip())
                return (w, h) if (w, h) in valid_sizes else None
            except Exception:
                return None

        # 1. 优先使用传入值
        result = _try_parse(resolution)
        if result:
            return result

        # 2. 回退到服务配置的 resolution
        service = self.get_service()
        if service and service.resolution:
            result = _try_parse(service.resolution)
            if result:
                logger.warning(f"画幅 {resolution!r} 无效，使用配置默认值 {service.resolution}")
                return result

        # 3. 回退到 Action 方法级默认值
        result = _try_parse(default)
        if result:
            logger.warning(f"画幅 {resolution!r} 无效，使用方法默认值 {default}")
            return result

        # 最终使用稳定的方形画幅。
        return 1024, 1024
