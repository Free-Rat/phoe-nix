{
  description = "Shared schemas package";

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
      python = pkgs.python311;
    in {
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
          export PS1="$PS1❄ => "
        '';
      };
    };
}
