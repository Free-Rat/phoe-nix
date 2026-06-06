import json

from simulator.fixtures import build_environment, sample_log_entries, sample_observation
from simulator.pipeline import (
    run_observation_pipeline,
    run_pipeline,
    simulate_invalid_ai_response,
    simulate_malformed_log_blob,
    simulate_token_failure,
    simulate_upload_retry_and_recovery,
)


def main() -> None:
    print(
        json.dumps(
            {
                "log_pipeline": run_pipeline(build_environment(), entries=sample_log_entries()),
                "observation_pipeline": run_observation_pipeline(build_environment(), observation=sample_observation()),
                "token_failure": simulate_token_failure(build_environment(), entries=sample_log_entries()),
                "upload_retry": simulate_upload_retry_and_recovery(build_environment(), entries=sample_log_entries()),
                "malformed_log": simulate_malformed_log_blob(build_environment()),
                "invalid_ai_response": simulate_invalid_ai_response(build_environment(), entries=sample_log_entries()),
            },
            indent=2,
            sort_keys=True,
        )
    )
