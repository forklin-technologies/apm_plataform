import QRCode from "qrcode";

/** Gera o QR Code (SVG) a partir do código copia e cola devolvido pelo banco. */
export function qrCodeSvg(pixCopiaECola: string): Promise<string> {
  return QRCode.toString(pixCopiaECola, { type: "svg", errorCorrectionLevel: "M", margin: 2 });
}
