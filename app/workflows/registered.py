"""Reviewed code workflows and the built-in interpreter for saved visual flows."""

from dataclasses import replace

from app.workflows.registry import WorkflowDefinition, WorkflowFailure, WorkflowRegistry


def build_registry(session_factory=None, *, resolver_factory=None) -> WorkflowRegistry:
    """Metadata is available without opening a database connection."""

    def execute_visual_flow(context):
        if session_factory is None:
            raise WorkflowFailure("FLOW_STORAGE_UNAVAILABLE")
        from app.adapters.automation_policy import AutomationPolicy
        from app.services.flow_service import FlowService
        from app.workflows.flow_engine import execute_graph

        # The trusted worker obtains ownership from the locked transaction.
        # Execution itself narrows storage back to that tenant.
        specification = FlowService(
            session_factory, tenant_id=context.transaction.tenant_id
        ).execution_spec(context.claim.job_id)
        policy = AutomationPolicy.for_tenant(context.transaction.tenant_id)
        if resolver_factory is not None:
            policy = replace(
                policy, secret_resolver=resolver_factory(context.transaction.tenant_id)
            )
        return execute_graph(
            context,
            specification["graph"],
            specification["inputs"],
            automation_policy=policy,
        )

    return WorkflowRegistry(
        [
            WorkflowDefinition(
                name="visual_flow",
                title="Görsel akış motoru",
                description="Akış editöründe kaydedilen adımları, koşulları ve tarayıcı "
                "işlemlerini çalıştırır.",
                execute=execute_visual_flow,
            )
        ]
    )
