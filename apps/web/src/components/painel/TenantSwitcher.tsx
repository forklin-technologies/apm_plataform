"use client";

import { useEffect, useId, useRef, useState } from "react";
import { SchoolMonogram } from "@/components/portal/SchoolMonogram";
import { CheckIcon, ChevronDownIcon } from "@/components/ui/icons";
import type { Organization, SchoolRef } from "@/lib/api/types";

interface TenantSwitcherProps {
  organizations: Organization[];
  school: SchoolRef;
  organization: Organization;
  onSelect: (schoolId: string) => void;
  compact?: boolean;
}

/**
 * Seletor VISUAL de organizacao e escola (multi-tenant, ADR-004). No sistema real o conjunto
 * de organizacoes e escolas vem do servidor, pela sessao: o cliente nunca define o tenant.
 */
export function TenantSwitcher({ organizations, school, organization, onSelect, compact = false }: TenantSwitcherProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className={`flex w-full min-h-12 items-center gap-3 rounded-[14px] text-left transition-colors hover:bg-neutral-soft ${compact ? "px-1.5 py-1" : "bg-neutral-soft/70 px-3 py-2"}`}
      >
        <SchoolMonogram name={school.name} size={compact ? 32 : 36} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sub font-semibold leading-tight text-ink">{school.name}</span>
          <span className="block truncate text-foot text-ink-2">{organization.name}</span>
        </span>
        <ChevronDownIcon size={18} className={`shrink-0 text-ink-2 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div
          id={panelId}
          className="pop absolute left-0 top-[calc(100%+8px)] z-40 w-[min(22rem,calc(100vw-2.5rem))] origin-top rounded-[var(--r-lg)] bg-raised p-2 shadow-[var(--shadow-float)] ring-1 ring-[var(--line)]"
        >
          {organizations.map((org) => (
            <div key={org.id} className="py-1" role="group" aria-label={org.name}>
              <p className="px-3 pb-1 pt-2 text-foot font-semibold text-ink-2">{org.name}</p>
              <ul>
                {org.schools.map((item) => {
                  const selected = item.id === school.id;
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        aria-current={selected ? "true" : undefined}
                        onClick={() => {
                          setOpen(false);
                          triggerRef.current?.focus();
                          if (!selected) onSelect(item.id);
                        }}
                        className="flex min-h-12 w-full items-center gap-3 rounded-[12px] px-3 py-2 text-left hover:bg-neutral-soft"
                      >
                        <SchoolMonogram name={item.name} size={32} />
                        <span className="min-w-0 flex-1 truncate text-sub font-medium text-ink">{item.name}</span>
                        {selected && <CheckIcon size={18} className="shrink-0 text-ink" strokeWidth={2.4} />}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
          <p className="mt-1 border-t border-line px-3 pb-2 pt-3 text-foot text-ink-2">
            Visual de exemplo. No sistema real, as organizações e escolas vêm da sua conta, no servidor.
          </p>
        </div>
      )}
    </div>
  );
}
