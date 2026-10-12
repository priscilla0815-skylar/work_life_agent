"""Main AgentKit agent for personal work organization and cloud-phone actions."""

from __future__ import annotations

import os

from veadk import Agent

from .tools import (
    add_todo,
    cancel_my_cloud_phone_run,
    complete_todo,
    get_daily_summary,
    get_my_cloud_phone,
    inspect_my_cloud_phone_run,
    list_todos,
    log_meal,
    log_sleep,
    operate_my_cloud_phone,
    switch_my_cloud_phone,
)


MODEL_NAME = os.getenv("MODEL_AGENT_NAME", os.getenv("MODEL_NAME", ""))
MODEL_PROVIDER = os.getenv("MODEL_AGENT_PROVIDER", os.getenv("MODEL_PROVIDER", ""))
MODEL_API_BASE = os.getenv("MODEL_AGENT_API_BASE", os.getenv("MODEL_API_BASE", ""))
MODEL_API_KEY = os.getenv("MODEL_AGENT_API_KEY", os.getenv("MODEL_API_KEY", ""))


INSTRUCTION = """
你是一个个人工作整理主 Agent，帮助用户管理当天工作、生活打卡和云手机操作。

核心职责：
1. 每日待办：记录、查询、完成待办事项，并按日期、优先级和完成状态整理。
2. 每日吃饭打卡：记录早餐、午餐、晚餐、加餐等饮食打卡。
3. 每日睡觉日期：记录睡眠日期、入睡时间、起床时间、睡眠质量和备注。
4. 每日汇总：用户问“今天怎么样”“帮我整理今天”时，调用工具汇总待办、吃饭和睡眠记录。
5. 云手机操作：用户要求打开 App、搜索、点击、输入、收藏、截图、查看手机状态、操作云手机或提到 MUA 时，只调用当前登录用户的云手机工具，不要只口头承诺。

执行规范：
- 能调用工具落地记录时必须调用工具，不要只在对话中说已经记录。
- 用户没有给日期时，默认使用今天。
- 用户表达“吃饭了”“午饭打卡”“晚饭已吃”等，使用吃饭打卡工具。
- 用户表达“睡了”“记录睡觉”“昨晚睡眠”等，使用睡眠记录工具。
- 操作云手机前，把用户目标整理为清晰、边界明确的 MUA prompt，保留“不要点赞、不要评论、不要关注、不要私信、不要支付”等限制。
- 不得列举云手机池、其他用户绑定或接受用户指定的 ProductId。
- 只有用户明确要求切换绑定并提供完整 PodId 时，才调用 switch_my_cloud_phone；目标 Pod 必须由服务端验证存在且未被其他用户绑定。
- 用户未提供 PodId 时不要猜测或列出可选 Pod，只询问目标 PodId。
- 如果云手机任务遇到登录、验证码、人脸验证、权限弹窗或账号限制，明确说明卡点，不要宣称完成。
- 取消、删除、支付、发消息、下单等高影响动作，只有用户明确要求时才执行。
- 回答要简洁，给出已执行的工具结果、记录 ID、run_id 或下一步需要用户确认的信息。
""".strip()


agent_kwargs = {
    "name": "work_life_agent",
    "description": "Personal work organizer with todo, meal, sleep, and MUA cloud-phone tools.",
    "instruction": INSTRUCTION,
    "tools": [
        add_todo,
        list_todos,
        complete_todo,
        log_meal,
        log_sleep,
        get_daily_summary,
        operate_my_cloud_phone,
        inspect_my_cloud_phone_run,
        cancel_my_cloud_phone_run,
        get_my_cloud_phone,
        switch_my_cloud_phone,
    ],
}

if MODEL_NAME:
    agent_kwargs["model_name"] = MODEL_NAME
if MODEL_PROVIDER:
    agent_kwargs["model_provider"] = MODEL_PROVIDER
if MODEL_API_BASE:
    agent_kwargs["model_api_base"] = MODEL_API_BASE
if MODEL_API_KEY:
    agent_kwargs["model_api_key"] = MODEL_API_KEY


root_agent = Agent(**agent_kwargs)
