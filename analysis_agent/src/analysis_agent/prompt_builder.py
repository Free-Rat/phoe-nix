import json

from schemas import NormalizedLog, Observation


def build_log_prompt(message: NormalizedLog) -> str:
    payload = json.dumps(message.model_dump(mode="json"), indent=2, sort_keys=True)
    return (
        "You are an expert NixOS system administrator. Analyze the following normalized log entry and "
        "determine whether it indicates a configuration or system issue.\n\n"
        "For each issue found, provide JSON with these fields: error_type, severity, root_cause, "
        "suggested_action, affected_unit, confidence, analysis_text, remediation_hint.\n"
        "Allowed values:\n"
        "- error_type: service_failure, config_error, dependency_issue, disk_issue, network_issue, other\n"
        "- severity: critical, warning, info\n"
        "- suggested_action: rollback, restart_service, rebuild, no_action, "
        "or a short config-repair intent like apply_config\n"
        "- analysis_text: short human-readable diagnosis for the local agent\n"
        "- remediation_hint: concrete hint if a config-level repair is needed, "
        "such as 'services.openssh.enable = true;'\n\n"
        f"Normalized log entry:\n{payload}"
    )


def build_observation_prompt(message: Observation) -> str:
    payload = json.dumps(message.model_dump(mode="json"), indent=2, sort_keys=True)
    return (
        "You are an expert NixOS system administrator. Analyze this proactive node observation for degraded "
        "state, recurring failures, or resource pressure.\n\n"
        "Respond in JSON with fields: error_type, severity, root_cause, suggested_action, affected_unit, confidence, "
        "analysis_text, remediation_hint.\n"
        "Use no_action if the observation is informational only.\n\n"
        f"Observation:\n{payload}"
    )


def build_prompt(message: NormalizedLog | Observation) -> str:
    if isinstance(message, NormalizedLog):
        return build_log_prompt(message)
    return build_observation_prompt(message)
