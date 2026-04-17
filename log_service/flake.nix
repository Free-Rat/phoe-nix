{
  description = "Log Service - Collects logs from NixOS nodes";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    let
    system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config = {
          allowUnfree = true;
        };
      };
      python = pkgs.python314;
      pythonPackages = pkgs.python314Packages;

      # 1. Base Python builder (empty package set)
      pythonBase = pkgs.callPackage pyproject-nix.build.packages {
        inherit python;
      };

      # 2. Load uv workspace (reads pyproject + uv.lock)
      workspace = uv2nix.lib.workspace.loadWorkspace {
        workspaceRoot = ./.;
      };

      # 3. Generate overlay from uv.lock
      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };

      # 4. Add build-system fixes (important!)
      buildOverlay = pyproject-build-systems.overlays.wheel;

      # 5. Final Python package set
      pythonSet = pythonBase.overrideScope (
        final: prev: prev // (overlay final prev) // (buildOverlay final prev)
      );

    in
    {
      packages.${system}.default = pythonSet.mkVirtualEnv "log_service-env" workspace.deps.default;

      # Run app via `nix run`
      apps.${system}.default = {
        type = "app";
        program = "${self.packages.${system}.default}/bin/log_service";
      };

      devShells.x86_64-linux.default = pkgs.mkShell {
        packages =
          (with pkgs; [
            git
            python
            uv
          ])
          ++ (with pythonPackages; [
            systemd-python
          ]);
        shellHook = ''
          if test -f ~/.bash_profile; then
              source ~/.bash_profile
          fi
          uv sync
          export PS1="$PS1❄ => "

        '';

      };
    };
}
