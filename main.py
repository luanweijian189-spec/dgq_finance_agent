from __future__ import annotations

from dgq_finance_agent import AgentCommandHandler, FinanceResearchService


def main() -> None:
    service = FinanceResearchService()
    agent = AgentCommandHandler(service)

    print("DGQ Finance Agent CLI，输入 /help 查看示例，输入 exit 退出")
    print("示例：/add 600519 业绩持续增长 by 张三")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break

        if user_input in {"exit", "quit"}:
            print("bye")
            break

        if user_input == "/help":
            print("可用：/status 代码 | /who 昵称 | /top n | /worst n | /add 代码 逻辑 by 昵称")
            continue

        print(agent.handle(user_input))


if __name__ == "__main__":
    main()
