{
  description = "Log Service - Collects logs from NixOS nodes";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };

  outputs =
    {
      self,
      nixpkgs,
    }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config = {
          allowUnfree = true;
        };
      };

    in
    {
      devShells.x86_64-linux.default = pkgs.mkShell {

        packages = with pkgs; [
          git
          curl
          zip
          azure-cli
          terraform
        ];

        shellHook = ''
          if test -f ~/.bash_profile; then
              source ~/.bash_profile
          fi

          ROOT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
          if test -f "$ROOT_DIR/.env"; then
            set -a
            source "$ROOT_DIR/.env"
            set +a
          fi

          export PROJECT_NAME="''${PROJECT_NAME:-project-healer}"
          export ENV="''${ENV:-dev}"
          export RG="''${RG:-rg-''${PROJECT_NAME}-''${ENV}}"
          export COSMOS_ACCOUNT="''${COSMOS_ACCOUNT:-cosmos-''${PROJECT_NAME}-''${ENV}}"
          export TOKEN_APP="''${TOKEN_APP:-func-''${PROJECT_NAME}-''${ENV}-token}"
          export ROUTER_APP="''${ROUTER_APP:-func-''${PROJECT_NAME}-''${ENV}-router}"
          export ANALYSIS_APP="''${ANALYSIS_APP:-func-''${PROJECT_NAME}-''${ENV}-analysis}"
          export DECISION_APP="''${DECISION_APP:-func-''${PROJECT_NAME}-''${ENV}-decision}"
          export TOKEN_FUNCTION_NAME="''${TOKEN_FUNCTION_NAME:-token_service}"
          export OPENCODE_API_URL="''${OPENCODE_API_URL:-https://opencode.ai/zen/go/v1/chat/completions}"
          export OPENCODE_MODEL="''${OPENCODE_MODEL:-deepseek-v4-flash}"
          export NODE_API_KEY="''${NODE_API_KEY:-''${TF_VAR_node_api_key:-}}"

          if [[ -z "''${SB_NAMESPACE:-}" ]]; then
            if [[ -n "''${AZURE_TENANT_SUFFIX:-}" ]]; then
              export SB_NAMESPACE="sb-''${PROJECT_NAME}-''${ENV}-''${AZURE_TENANT_SUFFIX}"
            else
              tenant_id="$(az account show --query tenantId -o tsv 2>/dev/null || true)"
              if [[ -n "$tenant_id" ]]; then
                export AZURE_TENANT_SUFFIX="$(printf '%s' "$tenant_id" | tr -d '-' | cut -c1-6)"
                export SB_NAMESPACE="sb-''${PROJECT_NAME}-''${ENV}-''${AZURE_TENANT_SUFFIX}"
              fi
            fi
          fi

          export PS1="$PS1❄ => "
        '';

      };
    };
}
