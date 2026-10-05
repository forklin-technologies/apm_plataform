import { SchoolMonogram } from "@/components/portal/SchoolMonogram";
import { CheckIcon } from "@/components/ui/icons";
import type { Membership, OrganizationRef } from "@/lib/api/types";
import { ROLE_LABELS, membershipTitle } from "@/lib/roles";

interface MembershipListProps {
  memberships: Membership[];
  activeId: string | null;
  disabled?: boolean;
  onSelect: (membershipId: string) => void;
}

/** Agrupa os vinculos reais da pessoa por organizacao (a ordem e a que a API devolveu). */
function groupByOrganization(memberships: Membership[]): { organization: OrganizationRef; items: Membership[] }[] {
  const groups: { organization: OrganizationRef; items: Membership[] }[] = [];
  for (const membership of memberships) {
    const group = groups.find((g) => g.organization.id === membership.organization.id);
    if (group) group.items.push(membership);
    else groups.push({ organization: membership.organization, items: [membership] });
  }
  return groups;
}

/**
 * Lista de vinculos (organizacao, escola e papel) vindos da sessao. O cliente so devolve o id de
 * um vinculo que a API listou; quem confere se ele e mesmo da pessoa e o servidor.
 */
export function MembershipList({ memberships, activeId, disabled = false, onSelect }: MembershipListProps) {
  return (
    <>
      {groupByOrganization(memberships).map(({ organization, items }) => (
        <div key={organization.id} className="py-1" role="group" aria-label={organization.name}>
          <p className="px-3 pb-1 pt-2 text-foot font-semibold text-ink-2">{organization.name}</p>
          <ul>
            {items.map((membership) => {
              const selected = membership.membershipId === activeId;
              return (
                <li key={membership.membershipId}>
                  <button
                    type="button"
                    aria-current={selected ? "true" : undefined}
                    disabled={disabled}
                    onClick={() => {
                      if (!selected) onSelect(membership.membershipId);
                    }}
                    className="flex min-h-12 w-full items-center gap-3 rounded-[12px] px-3 py-2 text-left hover:bg-neutral-soft disabled:opacity-60"
                  >
                    <SchoolMonogram name={membershipTitle(membership)} size={32} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sub font-medium text-ink">
                        {membership.school ? membership.school.name : "Toda a organização"}
                      </span>
                      <span className="block truncate text-foot text-ink-2">{ROLE_LABELS[membership.role]}</span>
                    </span>
                    {selected && <CheckIcon size={18} className="shrink-0 text-ink" strokeWidth={2.4} />}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </>
  );
}
