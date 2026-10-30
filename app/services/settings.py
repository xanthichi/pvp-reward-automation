from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database import Database
from app.schemas import SettingsPayload
from app.security import SecretCipher


def _row_dict(row: Any, cipher: SecretCipher) -> dict[str, Any]:
    result = dict(row)
    result["onebot_access_token"] = cipher.decrypt(
        str(result.get("onebot_access_token", ""))
    )
    result["pushplus_token"] = cipher.decrypt(
        str(result.get("pushplus_token", ""))
    )
    try:
        reward_ids = json.loads(str(result.get("reward_ids") or "[]"))
    except json.JSONDecodeError:
        reward_ids = []
    if not isinstance(reward_ids, list):
        reward_ids = []
    normalized = []
    for value in reward_ids:
        try:
            reward_id = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= reward_id <= 6 and reward_id not in normalized:
            normalized.append(reward_id)
    result["reward_ids"] = normalized or [int(result["reward_id"])]
    return result


def get_settings(db: Database, cipher: SecretCipher) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM settings WHERE id = 1")
    if row is None:
        raise RuntimeError("设置记录不存在")
    return _row_dict(row, cipher)


def get_public_settings(
    db: Database, cipher: SecretCipher
) -> dict[str, Any]:
    settings = get_settings(db, cipher)
    return {
        "account_label": settings["account_label"],
        "scheduled_time": settings["scheduled_time"],
        "timezone": settings["timezone"],
        "auto_exchange": bool(settings["auto_exchange"]),
        "reward_ids": list(settings["reward_ids"]),
        "reward_id": int(settings["reward_ids"][0]),
        "last_login_at": settings["last_login_at"],
        "last_login_nickname": settings["last_login_nickname"],
        "onebot": {
            "enabled": bool(settings["onebot_enabled"]),
            "base_url": settings["onebot_base_url"],
            "access_token_set": bool(settings["onebot_access_token"]),
            "access_token_hint": SecretCipher.mask(
                settings["onebot_access_token"]
            ),
            "target_type": settings["onebot_target_type"],
            "target_id": settings["onebot_target_id"],
        },
        "pushplus": {
            "enabled": bool(settings["pushplus_enabled"]),
            "token_set": bool(settings["pushplus_token"]),
            "token_hint": SecretCipher.mask(settings["pushplus_token"]),
        },
    }


def save_settings(
    db: Database,
    cipher: SecretCipher,
    payload: SettingsPayload,
) -> None:
    current = get_settings(db, cipher)

    onebot_token = current["onebot_access_token"]
    if payload.clear_onebot_access_token:
        onebot_token = ""
    elif payload.onebot_access_token.strip():
        onebot_token = payload.onebot_access_token.strip()

    pushplus_token = current["pushplus_token"]
    if payload.clear_pushplus_token:
        pushplus_token = ""
    elif payload.pushplus_token.strip():
        pushplus_token = payload.pushplus_token.strip()

    db.execute(
        """
        UPDATE settings SET
            account_label = ?,
            scheduled_time = ?,
            auto_exchange = ?,
            reward_id = ?,
            reward_ids = ?,
            onebot_enabled = ?,
            onebot_base_url = ?,
            onebot_access_token = ?,
            onebot_target_type = ?,
            onebot_target_id = ?,
            pushplus_enabled = ?,
            pushplus_token = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            payload.account_label,
            payload.scheduled_time,
            int(payload.auto_exchange),
            payload.reward_ids[0],
            json.dumps(payload.reward_ids),
            int(payload.onebot_enabled),
            payload.onebot_base_url,
            cipher.encrypt(onebot_token),
            payload.onebot_target_type,
            payload.onebot_target_id,
            int(payload.pushplus_enabled),
            cipher.encrypt(pushplus_token),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def mark_login(
    db: Database,
    *,
    nickname: str,
    checked_at: str,
) -> None:
    db.execute(
        """
        UPDATE settings SET
            last_login_at = ?,
            last_login_nickname = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (checked_at, nickname, datetime.now(timezone.utc).isoformat()),
    )

