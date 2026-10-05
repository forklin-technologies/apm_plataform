import { deriveSchoolTheme, schoolThemeStyle } from "@/lib/color";

/**
 * Aplica a cor da escola em runtime: --accent e --accent-contrast (e a versao "texto")
 * calculados com contraste AA em claro e escuro. O chrome da plataforma fica fora daqui.
 */
export function SchoolThemeScope({
  accentColor,
  className = "",
  children,
}: {
  accentColor: string;
  className?: string;
  children: React.ReactNode;
}) {
  const style = schoolThemeStyle(deriveSchoolTheme(accentColor)) as React.CSSProperties;
  return (
    <div className={`school-theme ${className}`} style={style}>
      {children}
    </div>
  );
}
