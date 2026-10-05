/**
 * Cores por escola. A escola traz uma cor arbitraria; daqui sai um conjunto de tokens
 * com contraste AA calculado, em claro e escuro. Nada aqui e "bonito por sorte".
 */

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

export function parseHex(input: string): Rgb | null {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(input.trim());
  if (!match) return null;
  let hex = match[1] ?? "";
  if (hex.length === 3) hex = hex.replace(/./g, (c) => c + c);
  return {
    r: parseInt(hex.slice(0, 2), 16),
    g: parseInt(hex.slice(2, 4), 16),
    b: parseInt(hex.slice(4, 6), 16),
  };
}

export function toHex({ r, g, b }: Rgb): string {
  const part = (n: number) => Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, "0");
  return `#${part(r)}${part(g)}${part(b)}`;
}

function channel(value: number): number {
  const s = value / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** Luminancia relativa (WCAG 2.x). */
export function luminance({ r, g, b }: Rgb): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

const WHITE: Rgb = { r: 255, g: 255, b: 255 };
const BLACK: Rgb = { r: 0, g: 0, b: 0 };

/**
 * Texto sobre um fundo arbitrario: branco ou preto, o que tiver mais contraste.
 * Como o cruzamento acontece em ~4,58:1, o resultado e sempre AA (4,5:1).
 */
export function pickContrastText(background: Rgb): Rgb {
  return contrastRatio(WHITE, background) >= contrastRatio(BLACK, background) ? WHITE : BLACK;
}

/** Mistura ja no espaco de 8 bits: o contraste medido e o do hex que vai para a tela. */
function mix(from: Rgb, to: Rgb, t: number): Rgb {
  return {
    r: Math.round(from.r + (to.r - from.r) * t),
    g: Math.round(from.g + (to.g - from.g) * t),
    b: Math.round(from.b + (to.b - from.b) * t),
  };
}

/**
 * Aproxima `color` de preto ou branco (o lado que mais contrasta com os fundos)
 * ate atingir `min` contra TODOS os fundos. Muda o minimo possivel (busca binaria).
 */
export function ensureContrast(color: Rgb, backgrounds: Rgb[], min: number): Rgb {
  const worst = (c: Rgb) => Math.min(...backgrounds.map((bg) => contrastRatio(c, bg)));
  if (worst(color) >= min) return color;
  const avg = backgrounds.reduce((sum, bg) => sum + luminance(bg), 0) / backgrounds.length;
  const target = avg > 0.4 ? BLACK : WHITE;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 24; i += 1) {
    const mid = (lo + hi) / 2;
    if (worst(mix(color, target, mid)) >= min) hi = mid;
    else lo = mid;
  }
  return mix(color, target, hi);
}

// Superficies do chrome (precisam bater com globals.css).
export const SURFACES = {
  light: [parseHex("#ffffff")!, parseHex("#f3f4f6")!],
  dark: [parseHex("#1a1d23")!, parseHex("#0f1115")!],
} as const;

export interface SchoolTheme {
  accentLight: string;
  accentContrastLight: string;
  accentInkLight: string;
  accentDark: string;
  accentContrastDark: string;
  accentInkDark: string;
}

export const DEFAULT_SCHOOL_ACCENT = "#2f5fd0";

/**
 * - accent*: preenchimento (botoes, selos). Garante 3:1 contra o fundo (componente de UI).
 * - accentContrast*: texto sobre o preenchimento, 4,5:1 ou mais.
 * - accentInk*: a cor da escola usada COMO TEXTO sobre o fundo, 4,5:1 ou mais.
 */
export function deriveSchoolTheme(accentHex: string): SchoolTheme {
  const base = parseHex(accentHex) ?? parseHex(DEFAULT_SCHOOL_ACCENT)!;
  const build = (surfaces: readonly Rgb[]) => {
    const fill = ensureContrast(base, [...surfaces], 3);
    const text = pickContrastText(fill);
    const ink = ensureContrast(base, [...surfaces], 4.5);
    return { fill: toHex(fill), text: toHex(text), ink: toHex(ink) };
  };
  const light = build(SURFACES.light);
  const dark = build(SURFACES.dark);
  return {
    accentLight: light.fill,
    accentContrastLight: light.text,
    accentInkLight: light.ink,
    accentDark: dark.fill,
    accentContrastDark: dark.text,
    accentInkDark: dark.ink,
  };
}

/** Variaveis CSS aplicadas em runtime no wrapper do portal da escola. */
export function schoolThemeStyle(theme: SchoolTheme): Record<string, string> {
  return {
    "--school-accent-light": theme.accentLight,
    "--school-accent-contrast-light": theme.accentContrastLight,
    "--school-accent-ink-light": theme.accentInkLight,
    "--school-accent-dark": theme.accentDark,
    "--school-accent-contrast-dark": theme.accentContrastDark,
    "--school-accent-ink-dark": theme.accentInkDark,
  };
}
