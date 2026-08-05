{
  description = "Ansible for exporting, templating and applying Cisco ISE configuration";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = ["x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin"];
    forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
  in {
    devShells = forAllSystems (pkgs: {
      default = pkgs.mkShell {
        # ansible-core is enough: the playbooks use only builtin modules, so
        # there is no collection or ciscoisesdk to install. sops is here
        # because the admin password is read from the nix-config secrets.
        packages = [
          pkgs.ansible
          pkgs.sops
          pkgs.age
          pkgs.jq
        ];

        shellHook = ''
          export ANSIBLE_CONFIG="$PWD/ansible.cfg"
          echo "ise-templater: ansible-playbook export.yml | templatize.yml | apply.yml"
        '';
      };
    });

    formatter = forAllSystems (pkgs: pkgs.alejandra);
  };
}
