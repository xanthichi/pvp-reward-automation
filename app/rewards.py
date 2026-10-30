from __future__ import annotations

from typing import Any


TARGET_PAGE = "https://pvp.qq.com/cp/a20161115tyf/page2.shtml"

REWARDS: tuple[dict[str, Any], ...] = (
    {
        "id": 1,
        "name": "亲密玫瑰",
        "cost": 40,
        "flow_id": 407551,
        "description": "打开获得亲密玫瑰*1",
        "image": "https://game.gtimg.cn/images/yxzj/cp/a20161115tyf/pic_dj4.png",
    },
    {
        "id": 2,
        "name": "大型钻石福袋",
        "cost": 50,
        "flow_id": 407552,
        "description": "打开获得 30~90 钻石",
        "image": "https://game.gtimg.cn/images/yxzj/cp/a20161115tyf/pic_dj5.png",
    },
    {
        "id": 3,
        "name": "星币福袋",
        "cost": 60,
        "flow_id": 407553,
        "description": "打开可获得 6 星币",
        "image": "https://game.gtimg.cn/images/yxzj/cp/a20161115tyf/pic_dj7.png",
    },
    {
        "id": 4,
        "name": "碎片福袋",
        "cost": 80,
        "flow_id": 407554,
        "description": "自选 2 个皮肤碎片或英雄碎片",
        "image": "https://game.gtimg.cn/images/yxzj/cp/a20161115tyf/pic_dj6.png",
    },
    {
        "id": 5,
        "name": "浓情玫瑰",
        "cost": 80,
        "flow_id": 407555,
        "description": "打开获得浓情玫瑰*1",
        "image": "https://game.gtimg.cn/images/yxzj/cp/a20161115tyf/pic_dj8.png",
    },
    {
        "id": 6,
        "name": "体验服专属头像框",
        "cost": 900,
        "flow_id": 407556,
        "description": "体验服限定头像框",
        "image": "https://game.gtimg.cn/images/yxzj/cp/a20161115tyf/pic_dj9.png",
    },
)

REWARD_BY_ID = {int(item["id"]): item for item in REWARDS}


def get_reward(reward_id: int) -> dict[str, Any]:
    reward = REWARD_BY_ID.get(int(reward_id))
    if reward is None:
        raise ValueError("未知的奖励")
    return reward

