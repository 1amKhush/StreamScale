"""Failure-lab operations. The agent owns timers and recovery; the server records
every fault on the active session, which keeps that session out of comparisons."""

import time

from streamscale_console.operations import Rejected
from streamscale_console.telemetry import AgentRejected

KINDS = {
    "restart_consumer",
    "crash_after_db_commit",
    "crash_after_dlq_ack",
    "stop_consumers",
    "slow_processing",
    "restrict_cpu",
    "restrict_db_cpu",
    "postgres_outage",
    "kafka_outage",
    "memory_pressure",
}


class Component:
    def __init__(self, console):
        self.console = console
        register = console.coordinator.register
        register("fault.inject", self.inject, "Inject a fault", check=self.check)
        register("fault.recover", self.recover, "Recover a fault")
        register("fault.recover_all", self.recover_all, "Recover the pipeline")

    def snapshot(self):
        return {}

    def tasks(self):
        return []

    def routes(self, app):
        pass

    def check(self, params):
        if params.get("kind") not in KINDS:
            raise Rejected("Unknown fault")

    async def call(self, name, body):
        try:
            response = await self.console.hub.client.call(
                "POST", f"/v1/command/{name}", body, timeout=60
            )
        except AgentRejected as error:
            raise Rejected(str(error)) from None
        return response["result"]

    async def inject(self, operation):
        result = await self.call("fault.inject", operation.params)
        self.record(
            {
                "at": time.time(),
                "id": result["id"],
                "kind": operation.params["kind"],
                "params": operation.params.get("params", {}),
            }
        )
        return result

    async def recover(self, operation):
        return await self.call("fault.recover", operation.params)

    async def recover_all(self, operation):
        return await self.call("fault.recover_all", {})

    def record(self, fault):
        sales = next((item for item in self.console.components if hasattr(item, "store")), None)
        if sales is None or sales.record is None:
            return
        sales.record.setdefault("faults", []).append(fault)
        sales.store.save(sales.record)
        sales.publish()
