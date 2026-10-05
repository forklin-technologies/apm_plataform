import type { Membership, Role } from "@/lib/api/types";

export const ROLE_LABELS: Record<Role, string> = {
  organization_admin: "Administrador da organização",
  school_admin: "Administrador da escola",
  treasurer: "Tesouraria",
  staff: "Equipe",
  viewer: "Leitura",
};

/** Nome principal de um vinculo: a escola, ou a organizacao quando o vinculo e da rede inteira. */
export function membershipTitle(membership: Membership): string {
  return membership.school?.name ?? membership.organization.name;
}

/** Segunda linha: a organizacao da escola, ou o escopo "toda a organizacao". */
export function membershipScope(membership: Membership): string {
  return membership.school ? membership.organization.name : "Toda a organização";
}
