"""命令回复文本。

同一场景准备多套说法并随机轮换，避免固定话术触发平台风控。
"""

from __future__ import annotations

import random

_last_picked: dict[str, int] = {}


def pick(templates: list[str], key: str = "") -> str:
    """从模板列表中随机取一条，尽量避开上次用过的那条。

    Args:
        templates: 候选文本
        key: 场景标识，用于记录上次选择

    Returns:
        选中的文本，列表为空时返回空串
    """
    if not templates:
        return ""
    if len(templates) == 1:
        return templates[0]

    last_index = _last_picked.get(key, -1)
    available = [index for index in range(len(templates)) if index != last_index]
    chosen = random.choice(available)
    if key:
        _last_picked[key] = chosen
    return templates[chosen]


_ERROR_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("429", "too many", "rate limit"), "请求太频繁了，服务器让我歇一会儿呢"),
    (("443", "ssl", "certificate"), "网络连接有点问题，可能是代理或者证书的事"),
    (("503", "service unavailable"), "服务器那边在维护呢，等会儿再试试吧"),
    (("502", "bad gateway"), "服务器那边出了点小状况，稍后再试试"),
    (("500", "internal server"), "服务器内部出了点问题，不是我的锅哦"),
    (("404", "not found"), "找不到资源呢，可能链接有问题"),
    (("401", "unauthorized"), "认证失败了，可能是 API 密钥的问题"),
    (("403", "forbidden"), "没有权限访问呢，被拒绝了"),
    (("timeout", "timed out"), "等太久了，网络超时了呢"),
    (("connection", "connect"), "网络连接出了问题，检查一下网络吧"),
    (("proxy",), "代理那边好像有问题"),
    (("未初始化", "not initialized"), "服务还没准备好呢，稍等一下"),
)

LONG_ERROR_THRESHOLD = 50


def humanize_error(error: str) -> str:
    """把技术性错误信息转成自然语言。

    Args:
        error: 原始错误文本

    Returns:
        面向用户的说明
    """
    lowered = str(error).lower()
    for keywords, message in _ERROR_PATTERNS:
        if any(keyword in lowered for keyword in keywords):
            return message
    if len(lowered) > LONG_ERROR_THRESHOLD:
        return "遇到了一些技术问题，稍后再试试吧"
    return str(error)


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
