{
  description = "Local deployment simulator";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };

  outputs = { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
      python = pkgs.python314;
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.git
          python
          pkgs.uv
          pkgs.pkg-config
          pkgs.systemd.dev
          pkgs.azure-cli
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
