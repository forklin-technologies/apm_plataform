/**
 * Ajuste de cores da escola para garantir contraste mínimo (WCAG AA, 4,5:1).
 * A cor configurada pela escola é preservada no tom; só a luminosidade muda.
 */

type Rgb = [number, number, number];

function paraRgb(hex: string): Rgb | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1]!, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const paraHex = (c: Rgb) => "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");

function luminancia([r, g, b]: Rgb) {
  const canal = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b);
}

export function contraste(a: Rgb, b: Rgb) {
  const [l1, l2] = [luminancia(a), luminancia(b)].sort((x, y) => y - x);
  return (l1! + 0.05) / (l2! + 0.05);
}

/** Escurece (alvo claro) ou clareia (alvo escuro) até atingir o contraste mínimo com o fundo. */
function ajustar(cor: Rgb, fundo: Rgb, minimo = 4.5): Rgb {
  const escurecer = luminancia(fundo) > 0.5;
  let c = cor;
  for (let i = 0; i < 40 && contraste(c, fundo) < minimo; i++) {
    c = c.map((v) => (escurecer ? v * 0.93 : v + (255 - v) * 0.1)) as Rgb;
  }
  return c;
}

/** Cores derivadas da cor primária da escola, seguras para texto e botões. */
export function coresDaEscola(hex: string | null | undefined) {
  const base = paraRgb(hex ?? "") ?? paraRgb("#E6457A")!;
  return {
    /** Fundo de botão com texto branco, e texto sobre fundo claro. */
    marca: paraHex(ajustar(base, [255, 255, 255])),
    /** Texto/realce sobre o fundo escuro do tema escuro. */
    marcaEscuro: paraHex(ajustar(base, [24, 24, 27])),
    /** Cor original, apenas para detalhes decorativos (faixas, bordas). */
    decorativa: paraHex(base),
  };
}
