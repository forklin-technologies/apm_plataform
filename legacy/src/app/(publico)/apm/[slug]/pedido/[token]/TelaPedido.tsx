"use client";
/**
 * Mostra o QR Code e o "copia e cola" enquanto o Pix aguarda pagamento,
 * consulta a situação a cada poucos segundos e troca para o comprovante
 * assim que o pagamento é confirmado.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { formatarDataHora, formatarReais } from "../../../../_componentes/formato";

export interface PedidoPublico {
  codigo: string;
  status: "AGUARDANDO_PAGAMENTO" | "PIX_GERADO" | "PAGO" | "CANCELADO" | "EXPIRADO" | "ESTORNADO";
  emAnalise: boolean;
  totalCentavos: number;
  responsavelNome: string | null;
  criadoEm: string;
  pagoEm: string | null;
  expiraEm: string;
  itens: { descricao: string; quantidade: number; valorTotalCentavos: number }[];
  pix: { copiaECola: string | null } | null;
  endToEndId: string | null;
}

interface Props {
  slug: string;
  token: string;
  inicial: PedidoPublico;
  escola: { nome: string; nomeApm: string; cnpjApm: string; mensagemAgradecimento: string | null };
  sandbox: boolean;
}

const INTERVALO_MS = 4000;
const aguardando = (s: PedidoPublico["status"]) => s === "PIX_GERADO" || s === "AGUARDANDO_PAGAMENTO";

export function TelaPedido({ slug, token, inicial, escola, sandbox }: Props) {
  const [pedido, setPedido] = useState(inicial);
  const [aviso, setAviso] = useState("");
  const tituloRef = useRef<HTMLHeadingElement>(null);
  const base = `/api/escolas/${encodeURIComponent(slug)}/pedidos/${token}`;

  const atualizar = useCallback(async () => {
    try {
      const r = await fetch(base, { cache: "no-store" });
      if (r.ok) setPedido(await r.json());
    } catch { /* sem rede: tenta de novo no próximo ciclo */ }
  }, [base]);

  // Consulta periódica enquanto o pagamento não é concluído (pausa com a aba em segundo plano).
  useEffect(() => {
    if (!aguardando(pedido.status)) return;
    const id = setInterval(() => { if (document.visibilityState === "visible") void atualizar(); }, INTERVALO_MS);
    const aoVoltar = () => { if (document.visibilityState === "visible") void atualizar(); };
    document.addEventListener("visibilitychange", aoVoltar);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", aoVoltar); };
  }, [pedido.status, atualizar]);

  // Leva o foco ao título ao abrir a tela e quando a situação muda (ex.: pagamento confirmado).
  const statusAnterior = useRef<string | null>(null);
  useEffect(() => {
    if (statusAnterior.current !== pedido.status) tituloRef.current?.focus();
    statusAnterior.current = pedido.status;
  }, [pedido.status]);

  async function simularPagamento() {
    setAviso("Simulando pagamento…");
    const r = await fetch("/api/sandbox/pagar", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slug, token }),
    }).catch(() => null);
    setAviso(r?.ok ? "" : "Não foi possível simular o pagamento.");
    await atualizar();
  }

  const comum = { pedido, escola, tituloRef };
  return (
    <>
      <p className="so-leitor" role="status">{aviso}</p>
      {pedido.status === "PAGO" ? <Comprovante {...comum} />
        : pedido.status === "PIX_GERADO" && pedido.pix?.copiaECola ? (
          <TelaPix {...comum} copiaECola={pedido.pix.copiaECola} qrcodeUrl={`${base}/qrcode`}
            aoExpirar={atualizar} sandbox={sandbox && pedido.pix.copiaECola.startsWith("SANDBOX-")} simular={simularPagamento} />
        ) : <Situacao {...comum} slug={slug} />}
    </>
  );
}

type Comum = { pedido: PedidoPublico; escola: Props["escola"]; tituloRef: React.RefObject<HTMLHeadingElement | null> };

function Itens({ pedido }: { pedido: PedidoPublico }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: "0.75rem" }}>
      <caption className="so-leitor">Itens do pedido</caption>
      <thead className="so-leitor"><tr><th scope="col">Item</th><th scope="col">Valor</th></tr></thead>
      <tbody>
        {pedido.itens.map((i, n) => (
          <tr key={n} style={{ borderTop: "1px solid var(--borda)" }}>
            <td style={{ padding: "0.35rem 0" }}>{i.quantidade > 1 ? `${i.quantidade} × ` : ""}{i.descricao}</td>
            <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>{formatarReais(i.valorTotalCentavos)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr style={{ borderTop: "2px solid var(--borda-forte)", fontWeight: 700 }}>
          <th scope="row" style={{ textAlign: "left", padding: "0.35rem 0" }}>Total</th>
          <td style={{ textAlign: "right" }}>{formatarReais(pedido.totalCentavos)}</td>
        </tr>
      </tfoot>
    </table>
  );
}

function useRestante(expiraEm: string) {
  const [agora, setAgora] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setAgora(Date.now()), 1000); return () => clearInterval(id); }, []);
  return Math.max(0, Math.floor((new Date(expiraEm).getTime() - agora) / 1000));
}

function TelaPix({ pedido, tituloRef, copiaECola, qrcodeUrl, aoExpirar, sandbox, simular }: Comum & {
  copiaECola: string; qrcodeUrl: string; aoExpirar: () => void; sandbox: boolean; simular: () => void;
}) {
  const id = useId();
  const [copiado, setCopiado] = useState("");
  const restante = useRestante(pedido.expiraEm);
  const campoRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { if (restante === 0) aoExpirar(); }, [restante, aoExpirar]);

  async function copiar() {
    try {
      await navigator.clipboard.writeText(copiaECola);
      setCopiado("Código Pix copiado. Agora cole no aplicativo do seu banco.");
    } catch {
      campoRef.current?.select();
      setCopiado("Não foi possível copiar automaticamente. O código foi selecionado: use a opção copiar do seu aparelho.");
    }
    setTimeout(() => setCopiado(""), 8000);
  }

  const min = Math.floor(restante / 60), seg = restante % 60;
  return (
    <>
      <h1 ref={tituloRef} tabIndex={-1}>Pague com Pix</h1>
      <div className="cartao" style={{ textAlign: "center" }}>
        <p className="suave" style={{ margin: 0 }}>Pedido {pedido.codigo} · valor a pagar</p>
        <p className="valor-destaque">{formatarReais(pedido.totalCentavos)}</p>
        <img className="qrcode" src={qrcodeUrl} width={260} height={260}
          alt={`QR Code do Pix de ${formatarReais(pedido.totalCentavos)}. Se não puder ler o QR Code, use o botão copiar código Pix.`} />
        <p aria-hidden="true" style={{ fontWeight: 700 }}>
          {restante > 0 ? `Expira em ${min}:${String(seg).padStart(2, "0")}` : "Pix expirado"}
        </p>
        <p className="so-leitor">Este Pix vale até {formatarDataHora(pedido.expiraEm)}.</p>
      </div>

      <div className="cartao">
        <label htmlFor={`${id}-cc`} className="rotulo">Pix copia e cola</label>
        <textarea id={`${id}-cc`} ref={campoRef} className="copia-cola" readOnly rows={3} value={copiaECola}
          onFocus={(e) => e.currentTarget.select()} />
        <div className="acoes" style={{ marginTop: "0.75rem" }}>
          <button type="button" className="botao largo" onClick={copiar}>Copiar código Pix</button>
        </div>
        <p role="status" aria-live="polite" style={{ marginTop: "0.5rem", minHeight: "1.5em", color: "var(--sucesso)" }}>{copiado}</p>

        <h2>Como pagar</h2>
        <ol className="passos">
          <li>Abra o aplicativo do seu banco e escolha <strong>Pix</strong>.</li>
          <li>Escolha <strong>ler QR Code</strong> ou <strong>Pix copia e cola</strong>.</li>
          <li>Confira o valor e o recebedor ({"APM"}) e confirme.</li>
          <li>Mantenha esta página aberta: ela mostra o comprovante assim que o pagamento for confirmado.</li>
        </ol>
        <p className="suave" style={{ margin: 0 }}>Aguardando pagamento… a situação é verificada automaticamente.</p>
      </div>

      <Itens pedido={pedido} />

      {sandbox && (
        <div className="alerta aviso nao-imprimir">
          <h2>Ambiente de teste</h2>
          <p>Este Pix é de teste e não pode ser pago em um banco real.</p>
          <button type="button" className="botao secundario" onClick={simular}>Simular pagamento</button>
        </div>
      )}
    </>
  );
}

function Comprovante({ pedido, escola, tituloRef }: Comum) {
  return (
    <>
      <div className="alerta sucesso">
        <h1 ref={tituloRef} tabIndex={-1} style={{ fontSize: "1.4rem" }}>Pagamento confirmado</h1>
        <p style={{ margin: 0 }}>{escola.mensagemAgradecimento ?? "Obrigado pela sua contribuição!"}</p>
      </div>
      {pedido.emAnalise && (
        <div className="alerta aviso">
          <p style={{ margin: 0 }}>O pagamento foi recebido e está em análise pela tesouraria da APM. Se necessário, entraremos em contato.</p>
        </div>
      )}
      <section className="cartao comprovante" aria-labelledby="titulo-comprovante">
        <h2 id="titulo-comprovante">Comprovante</h2>
        <dl>
          <dt>Pedido</dt><dd>{pedido.codigo}</dd>
          <dt>Valor</dt><dd>{formatarReais(pedido.totalCentavos)}</dd>
          {pedido.pagoEm && (<><dt>Pago em</dt><dd>{formatarDataHora(pedido.pagoEm)}</dd></>)}
          {pedido.responsavelNome && (<><dt>Responsável</dt><dd>{pedido.responsavelNome}</dd></>)}
          <dt>Recebedor</dt><dd>{escola.nomeApm} – {escola.nome}</dd>
          <dt>CNPJ</dt><dd>{escola.cnpjApm}</dd>
          {pedido.endToEndId && (<><dt>Identificador do Pix</dt><dd style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}>{pedido.endToEndId}</dd></>)}
        </dl>
        <Itens pedido={pedido} />
        <div className="acoes nao-imprimir">
          <button type="button" className="botao" onClick={() => window.print()}>Imprimir ou salvar em PDF</button>
        </div>
      </section>
    </>
  );
}

function Situacao({ pedido, tituloRef, slug }: Comum & { slug: string }) {
  const textos: Record<string, { titulo: string; texto: string; tipo: "aviso" | "erro" }> = {
    AGUARDANDO_PAGAMENTO: { titulo: "Gerando o Pix", texto: "Aguarde alguns segundos…", tipo: "aviso" },
    PIX_GERADO: { titulo: "Gerando o Pix", texto: "Aguarde alguns segundos…", tipo: "aviso" },
    EXPIRADO: { titulo: "Pix expirado", texto: "O prazo para pagar este Pix terminou e nenhum valor foi cobrado. Você pode fazer um novo pedido.", tipo: "aviso" },
    CANCELADO: { titulo: "Pedido cancelado", texto: "Este pedido foi cancelado e nenhum valor foi cobrado. Você pode fazer um novo pedido.", tipo: "erro" },
    ESTORNADO: { titulo: "Pagamento devolvido", texto: "O valor deste pedido foi devolvido. Em caso de dúvida, fale com a APM.", tipo: "aviso" },
  };
  const t = textos[pedido.status]!;
  return (
    <>
      <div className={`alerta ${t.tipo}`}>
        <h1 ref={tituloRef} tabIndex={-1} style={{ fontSize: "1.4rem" }}>{t.titulo}</h1>
        <p style={{ margin: 0 }}>{t.texto}</p>
      </div>
      <div className="cartao">
        <p className="suave">Pedido {pedido.codigo} · criado em {formatarDataHora(pedido.criadoEm)}</p>
        <Itens pedido={pedido} />
      </div>
      {!aguardando(pedido.status) && <Link className="botao" href={`/apm/${encodeURIComponent(slug)}`}>Fazer novo pedido</Link>}
    </>
  );
}
