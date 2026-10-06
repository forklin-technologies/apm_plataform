import { create } from "qrcode";

const QUIET = 4; // faixa branca de 4 modulos ao redor (minimo da norma)

/**
 * Desenha o QR Code no proprio navegador (e no servidor) a partir do texto do Pix. Sem chamada
 * externa e sem innerHTML: so um <path> SVG com os modulos escuros. O texto nunca sai da pagina.
 */
export function QrCode({ payload }: { payload: string }) {
  let size = 0;
  let path = "";
  try {
    const { modules } = create(payload, { errorCorrectionLevel: "M" });
    size = modules.size;
    const parts: string[] = [];
    for (let y = 0; y < size; y += 1) {
      for (let x = 0; x < size; x += 1) {
        if (modules.get(y, x)) parts.push(`M${x + QUIET} ${y + QUIET}h1v1h-1z`);
      }
    }
    path = parts.join("");
  } catch {
    return (
      <p role="alert" className="mx-auto max-w-[34ch] text-center text-sub text-ink-2">
        Não foi possível desenhar o QR Code. Use o código copia e cola abaixo.
      </p>
    );
  }
  const total = size + QUIET * 2;
  return (
    <div className="mx-auto aspect-square w-full max-w-[17rem] rounded-[var(--r-lg)] bg-white p-2 shadow-[0_0_0_1px_var(--line)]">
      <svg
        viewBox={`0 0 ${total} ${total}`}
        className="size-full"
        role="img"
        aria-label="QR Code do Pix. Aponte a câmera do app do seu banco para pagar."
        shapeRendering="crispEdges"
      >
        <path d={path} fill="#14171c" />
      </svg>
    </div>
  );
}
