export function schoolInitials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

/** Monograma da escola na cor dela (--accent) com texto de contraste calculado. */
export function SchoolMonogram({ name, size = 40 }: { name: string; size?: number }) {
  return (
    <span
      aria-hidden="true"
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-accent font-bold text-accent-contrast"
      style={{ width: size, height: size, fontSize: size * 0.38, letterSpacing: "-0.02em" }}
    >
      {schoolInitials(name)}
    </span>
  );
}
