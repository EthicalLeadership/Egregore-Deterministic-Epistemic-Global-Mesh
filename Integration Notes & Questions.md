Integration Notes & Questions

1. CapacityOrchestrator double-submission risk
CapacityOrchestrator.submit_work_unit() evaluates admission AND submits to its internal _scheduler. If you inject this adapter as the CapacityOrchestrator's scheduler, the root work unit gets routed as a Job before decomposition. If you inject it only into WorkTreeService, the root is tracked by CapacityOrchestrator's internal scheduler and leaves are routed by the adapter.
Recommendation: Inject the adapter into WorkTreeService only. Use CapacityOrchestrator for admission check. The root work unit stays in the capacity layer; leaves enter the job routing layer.
2. WorkUnitType mapping
The adapter has a stub mapping for LLM_INFERENCE. You need to add all your WorkUnitType values and their capability tags. What are the full set of work unit types in your domain?
3. JobStatus.CANCELLED
The previous audit noted CANCELLED is missing. The adapter doesn't need it, but WorkTreeService might if you want to cancel pending leaves. Is this already in your JobStatus or WorkUnitState?
4. Fallback chain
RoutingDecision.fallback_chain is a list of node IDs. The adapter currently uses IResilienceRouter.fallback() for fallback. If you want to walk the fallback_chain directly instead, I can change the logic.
5. Complexity → model policy
The chat_interpreter.py model cascade (Qwen-3B → DeepSeek → Claude) should be driven by JobClassification.complexity. The adapter doesn't handle model selection — that's execution-time. But the node registry could advertise model capabilities (e.g., NodeCapability.capabilities = ["qwen-3b", "deepseek-14b"]) and the router matches complexity to capability. Is this how you want it wired, or should model selection happen inside the adapter?
6. File location
src/egregore/application/job_router_scheduler_adapter.py — consistent with your DDD structure (application layer orchestrates domain + infrastructure).
