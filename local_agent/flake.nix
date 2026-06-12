{
  description = "Local Agent";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
    phoe-nix-src = {
      url = "path:/home/freerat/projects/phoe-nix";
      flake = false;
    };
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

  outputs = { self, nixpkgs, phoe-nix-src, pyproject-nix, uv2nix, pyproject-build-systems, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      python = pkgs.python314;
      pythonBase = pkgs.callPackage pyproject-nix.build.packages {
        inherit python;
      };
      workspace = uv2nix.lib.workspace.loadWorkspace {
        workspaceRoot = phoe-nix-src + "/local_agent";
      };
      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };
      buildOverlay = pyproject-build-systems.overlays.wheel;
      pythonSet = (pythonBase.overrideScope (
        final: prev: prev // (buildOverlay final prev) // (overlay final prev)
      ));
    in {
      packages.${system}.default = pythonSet.mkVirtualEnv "local-agent-env" workspace.deps.default;

      apps.${system}.default = {
        type = "app";
        program = "${self.packages.${system}.default}/bin/local_agent";
      };

      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.git
          python
          pkgs.uv
        ];

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
