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
          azure-cli
          terraform
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
