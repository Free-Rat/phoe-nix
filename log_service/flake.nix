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
      pkgs = import nixpkgs {
        system = "x86_64-linux";
        config = {
          allowUnfree = true;
        };
      };
      python312 = pkgs.python312;
      pythonPackages = pkgs.python312Packages;
      buildPythonPackage = pythonPackages.buildPythonPackage;

    in
    {
      packages.x86_64-linux.default = pkgs.writeScriptBin "log-service" ''
        #!${pkgs.bash}/bin/bash
        export PYTHONPATH="${self.outPath}/log_service/src:$PYTHONPATH"
        exec ${python312}/bin/python -m log_service "$@"
      '';

      devShells.x86_64-linux.default = pkgs.mkShell {
        packages =
          (with pkgs; [
            git
            python312
          ])
          ++ (with pythonPackages; [
            systemd-python
          ]);
        shellHook = ''
if test -f ~/.bash_profile; then
    source ~/.bash_profile
fi
export PS1="$PS1❄ => "

        '';

      };
    };
}
