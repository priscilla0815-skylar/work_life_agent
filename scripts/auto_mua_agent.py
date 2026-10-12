#!/usr/bin/env python3
"""Standalone CLI for routing a prompt to the project's MUA client."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from assistant.cloud_phone.client import (
    cancel_mua_task,
    get_mua_task_result,
    get_mua_task_steps,
    start_mua_task,
    wait_mua_task,
)


LOCAL_ONLY_HINTS = (
    "代码",
    "code",
    "review",
    "部署",
    "deploy",
    "文档",
    "readme",
    "数据库",
    "sql",
    "接口",
    "api",
    "函数",
    "faas",
    "vefaas",
    "前端",
    "后端",
)

MUA_FORCE_HINTS = (
    "mua",
    "mobile use",
    "云手机",
    "cloud phone",
    "pod",
    "手机里",
    "手机上",
    "app里",
    "app上",
)

MOBILE_APP_HINTS = (
    "抖音",
    "小红书",
    "微信",
    "企业微信",
    "qq",
    "支付宝",
    "美团",
    "高德",
    "地图",
    "淘宝",
    "京东",
    "拼多多",
    "闲鱼",
    "钉钉",
    "微博",
    "哔哩哔哩",
    "b站",
    "app",
)

PHONE_ACTION_HINTS = (
    "打开",
    "搜索",
    "点击",
    "输入",
    "收藏",
    "点赞",
    "评论",
    "关注",
    "发送",
    "回复",
    "私信",
    "下单",
    "支付",
    "导航",
    "打卡",
    "截图",
    "录屏",
    "安装",
    "卸载",
    "搜索结果",
    "video",
    "search",
    "tap",
    "type",
    "reply",
    "favorite",
    "bookmark",
)


def _contains_any(text: str, hints: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [item for item in hints if item.lower() in lowered]


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _format_wait_reply(waited: dict[str, Any]) -> str:
    result = waited.get("result") or {}
    is_success = waited.get("is_success")
    content = result.get("Content")
    if is_success == 1:
        return content or "MUA task succeeded"
    if is_success == 4:
        return content or "MUA task needs human assistance"
    if content:
        return str(content)
    if waited.get("error"):
        return str(waited["error"])
    return "MUA task finished"


@dataclass
class RouteDecision:
    route: str
    confidence: float
    reason: str
    matched: list[str] = field(default_factory=list)


class RuleBasedMuaRouter:
    def decide(self, user_message: str) -> RouteDecision:
        text = _normalize_whitespace(user_message)
        local_hits = _contains_any(text, LOCAL_ONLY_HINTS)
        force_hits = _contains_any(text, MUA_FORCE_HINTS)
        app_hits = _contains_any(text, MOBILE_APP_HINTS)
        action_hits = _contains_any(text, PHONE_ACTION_HINTS)

        if force_hits and (app_hits or action_hits):
            return RouteDecision("mua", 0.98, "explicit MUA intent", force_hits + app_hits + action_hits)
        if app_hits and action_hits:
            return RouteDecision("mua", 0.93, "mobile app action intent", app_hits + action_hits)
        if local_hits and not force_hits and not app_hits:
            return RouteDecision("local", 0.90, "local engineering task", local_hits)
        if app_hits or force_hits:
            return RouteDecision("mua", 0.72, "likely cloud-phone task", force_hits + app_hits)
        return RouteDecision("local", 0.65, "no strong mobile intent", [])


class AutoMuaAgent:
    def __init__(
        self,
        *,
        router: RuleBasedMuaRouter | None = None,
        local_handler: Callable[[str], str] | None = None,
        max_step: int = 100,
        timeout: int = 600,
        retry_limit: int = 2,
        wait_timeout: int = 900,
    ) -> None:
        self.router = router or RuleBasedMuaRouter()
        self.local_handler = local_handler
        self.max_step = max_step
        self.timeout = timeout
        self.retry_limit = retry_limit
        self.wait_timeout = wait_timeout

    def decide(self, user_message: str) -> RouteDecision:
        return self.router.decide(user_message)

    def build_mua_prompt(self, user_message: str) -> str:
        normalized = _normalize_whitespace(user_message)
        suffix = (
            "。遇到登录、人脸验证、权限弹窗、搜索结果不足、或页面与预期不符时，请停止并明确说明卡在哪一步。"
            "不要执行与任务无关的点赞、评论、关注、私信或支付。"
        )
        return normalized + suffix if normalized else suffix

    def handle(
        self,
        user_message: str,
        *,
        wait: bool = True,
        product_id: str | None = None,
        pod_id: str | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        decision = self.decide(user_message)
        if decision.route != "mua":
            reply = self.local_handler(user_message) if self.local_handler else "request did not route to MUA"
            return {
                "ok": True,
                "route": "local",
                "decision": asdict(decision),
                "reply": reply,
            }

        started = start_mua_task(
            prompt=self.build_mua_prompt(user_message),
            product_id=product_id,
            pod_id=pod_id,
            max_step=self.max_step,
            timeout=self.timeout,
            retry_limit=self.retry_limit,
            system_prompt=system_prompt,
        )
        run_id = started.get("run_id")
        if not wait or not run_id:
            return {
                "ok": True,
                "route": "mua",
                "decision": asdict(decision),
                "reply": "MUA task started",
                "run_id": run_id,
                "task_result": started,
            }

        waited = wait_mua_task(run_id=run_id, wait_timeout=self.wait_timeout)
        return {
            "ok": bool(waited.get("ok")),
            "route": "mua",
            "decision": asdict(decision),
            "reply": _format_wait_reply(waited),
            "run_id": run_id,
            "task_result": waited.get("result"),
            "task_steps": waited.get("steps"),
        }

    def inspect_run(self, run_id: str) -> dict[str, Any]:
        steps = get_mua_task_steps(run_id=run_id)
        result = get_mua_task_result(run_id=run_id)
        return {
            "ok": True,
            "run_id": run_id,
            "steps": steps.get("steps"),
            "result": result.get("result"),
        }

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return cancel_mua_task(run_id=run_id)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Work Life Agent MUA router")
    parser.add_argument("message", help="user message")
    parser.add_argument("--no-wait", action="store_true", help="start task without waiting")
    parser.add_argument("--pod-id", default=None, help="override pod id")
    parser.add_argument("--product-id", default=None, help="override product id")
    parser.add_argument("--inspect-run", default=None, help="inspect existing run id")
    parser.add_argument("--cancel-run", default=None, help="cancel existing run id")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    agent = AutoMuaAgent()

    if args.inspect_run:
        print(json.dumps(agent.inspect_run(args.inspect_run), ensure_ascii=False, indent=2))
        return
    if args.cancel_run:
        print(json.dumps(agent.cancel_run(args.cancel_run), ensure_ascii=False, indent=2))
        return

    result = agent.handle(
        args.message,
        wait=not args.no_wait,
        product_id=args.product_id,
        pod_id=args.pod_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
