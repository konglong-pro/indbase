from consoler_agent_sdk import JsonRpcServer

from indbase_agent.adapter import IndbaseAgentAdapter


def main() -> None:
    JsonRpcServer(IndbaseAgentAdapter()).run()


if __name__ == "__main__":
    main()
