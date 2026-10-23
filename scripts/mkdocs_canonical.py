"""Point pages that duplicate optics-framework.org/docs at the site's copy, so search engines index one."""

SITE = "https://optics-framework.org/docs/"
CANONICAL = {
    "index.md": "",
    "introduction.md": "get-started/",
    "prerequisites.md": "get-started/install",
    "getting-started.md": "get-started/quickstart",
    "configuration.md": "reference/config",
    "user_workflow.md": "get-started/concepts",
    "usage/usage.md": "create/",
    "usage/CLI_usage.md": "reference/cli",
    "usage/live_usage.md": "create/record",
    "usage/keyword_usage.md": "reference/keywords/",
    "usage/library_usage.md": "create/python",
    "usage/robot_usage.md": "create/robot",
    "usage/REST_API_usage.md": "advanced/http-api",
    "usage/mcp_usage.md": "reference/mcp",
    "usage/error_detection.md": "run/error-detection",
    "api_reference.md": "reference/keywords/",
    "api_reference/action_keywords.md": "reference/keywords/actions",
    "api_reference/app_management.md": "reference/keywords/app",
    "api_reference/verification.md": "reference/keywords/verification",
    "api_reference/flow_control.md": "reference/keywords/flow",
    "architecture/engines.md": "reference/engines",
}


def on_page_context(context, page, config, nav):
    if page.file.src_uri in CANONICAL:
        page.canonical_url = SITE + CANONICAL[page.file.src_uri]
    return context
