/**
 * Entrega um arquivo que a API devolveu (PDF, anexo) como download do navegador. O Blob vem de um fetch
 * com a sessao: nunca ha URL publica. A URL de objeto existe so durante o clique e e revogada em seguida.
 */
export function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
