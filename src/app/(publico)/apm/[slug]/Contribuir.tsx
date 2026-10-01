"use client";
/**
 * Carrinho + identificação. O navegador só informa itens, quantidades, meses e
 * valores livres; preço e total definitivos são calculados no servidor.
 */
import { useId, useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import type { vitrine } from "@/services/consultas";
import type { CamposFormulario } from "@/db/schema";
import { descreverMes, formatarReais, lerReais, nomeDoMes } from "../../_componentes/formato";

type Vitrine = NonNullable<Awaited<ReturnType<typeof vitrine>>>;
type Campanha = Vitrine["campanhas"][number];
type Item = Campanha["itens"][number];

interface Props {
  slug: string;
  escola: Vitrine["escola"];
  turmas: Vitrine["turmas"];
  campanhas: Campanha[];
  anoAtual: number;
}

type CampoId = "responsavelNome" | "responsavelEmail" | "responsavelTelefone" | "alunoNome" | "turmaId" | "observacao";
const CAMPOS: { id: CampoId; regra: keyof CamposFormulario; rotulo: string }[] = [
  { id: "responsavelNome", regra: "responsavelNome", rotulo: "Nome do responsável" },
  { id: "alunoNome", regra: "alunoNome", rotulo: "Nome do estudante" },
  { id: "turmaId", regra: "turma", rotulo: "Turma" },
  { id: "responsavelEmail", regra: "responsavelEmail", rotulo: "E-mail" },
  { id: "responsavelTelefone", regra: "responsavelTelefone", rotulo: "Telefone (WhatsApp)" },
  { id: "observacao", regra: "observacao", rotulo: "Observação" },
];

const LIMITE_PADRAO = 50;
const maximoDe = (i: Item) => Math.min(i.limitePorPedido ?? LIMITE_PADRAO, i.disponivel ?? LIMITE_PADRAO, LIMITE_PADRAO);

interface Erro { campo: string; mensagem: string }

export function Contribuir({ slug, escola, turmas, campanhas, anoAtual }: Props) {
  const router = useRouter();
  const base = useId();

  const [qtd, setQtd] = useState<Record<string, number>>({});
  const [livre, setLivre] = useState<Record<string, string>>({});
  const [meses, setMeses] = useState<Record<string, string[]>>({});
  const [ident, setIdent] = useState<Record<CampoId, string>>({
    responsavelNome: "", responsavelEmail: "", responsavelTelefone: "", alunoNome: "", turmaId: "", observacao: "",
  });
  const [anonimo, setAnonimo] = useState(false);
  const [erros, setErros] = useState<Erro[]>([]);
  const [enviando, setEnviando] = useState(false);

  const todosItens = useMemo(() => campanhas.flatMap((c) => c.itens), [campanhas]);
  const idCampo = (c: string) => `${base}-${c}`;
  const erroDe = (c: string) => erros.find((e) => e.campo === c)?.mensagem;

  // Linhas do resumo (só exibição; o servidor recalcula tudo).
  const linhas = useMemo(() => {
    const out: { chave: string; descricao: string; valor: number | null }[] = [];
    for (const i of todosItens) {
      if (i.tipo === "COTA" || i.tipo === "PRODUTO") {
        const q = qtd[i.id] ?? 0;
        if (q > 0) out.push({ chave: i.id, descricao: q > 1 ? `${q} × ${i.nome}` : i.nome, valor: q * i.precoCentavos! });
      } else if (i.tipo === "VALOR_LIVRE") {
        const t = livre[i.id]?.trim();
        if (t) out.push({ chave: i.id, descricao: i.nome, valor: lerReais(t) });
      } else {
        for (const m of meses[i.id] ?? []) out.push({ chave: `${i.id}-${m}`, descricao: `${i.nome} – ${descreverMes(m)}`, valor: i.precoCentavos! });
      }
    }
    return out;
  }, [todosItens, qtd, livre, meses]);
  const total = linhas.reduce((s, l) => s + (l.valor ?? 0), 0);
  const temProduto = todosItens.some((i) => i.tipo === "PRODUTO" && (qtd[i.id] ?? 0) > 0);
  const podeAnonimo = escola.modoIdentificacao === "ANONIMA" && !temProduto;
  const estaAnonimo = anonimo && podeAnonimo;
  const exigeObrigatorios = escola.modoIdentificacao === "IDENTIFICADA";
  const campos = CAMPOS.filter((c) => escola.camposFormulario[c.regra] !== "oculto" && (c.id !== "turmaId" || turmas.length > 0));
  const obrigatorio = (c: (typeof CAMPOS)[number]) => exigeObrigatorios && escola.camposFormulario[c.regra] === "obrigatorio";

  function validar(): Erro[] {
    const e: Erro[] = [];
    if (linhas.length === 0) e.push({ campo: "itens", mensagem: "Escolha pelo menos uma contribuição ou produto." });
    for (const i of todosItens) {
      if (i.tipo !== "VALOR_LIVRE" || !livre[i.id]?.trim()) continue;
      const v = lerReais(livre[i.id]!);
      if (v === null) e.push({ campo: `livre-${i.id}`, mensagem: `${i.nome}: digite um valor válido, por exemplo 25,00.` });
      else if (v < escola.valorMinimoLivreCentavos) e.push({ campo: `livre-${i.id}`, mensagem: `${i.nome}: o valor mínimo é ${formatarReais(escola.valorMinimoLivreCentavos)}.` });
    }
    if (!estaAnonimo) {
      for (const c of campos) {
        const v = ident[c.id].trim();
        if (obrigatorio(c) && !v) e.push({ campo: c.id, mensagem: `Preencha o campo ${c.rotulo.toLowerCase()}.` });
      }
      if (ident.responsavelEmail.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(ident.responsavelEmail.trim())) {
        e.push({ campo: "responsavelEmail", mensagem: "Digite um e-mail válido, por exemplo nome@exemplo.com." });
      }
      if (ident.responsavelTelefone.trim() && !/^[\d\s()+-]{8,20}$/.test(ident.responsavelTelefone.trim())) {
        e.push({ campo: "responsavelTelefone", mensagem: "Digite um telefone válido, com DDD. Ex.: (11) 91234-5678." });
      }
      if (temProduto && !ident.responsavelNome.trim() && !ident.alunoNome.trim()) {
        e.push({ campo: campos.some((c) => c.id === "responsavelNome") ? "responsavelNome" : "alunoNome",
          mensagem: "Informe o nome do responsável ou do estudante para a entrega do produto." });
      }
    }
    return e;
  }

  function mostrarErros(e: Erro[]) {
    setErros(e);
    // Espera o React desenhar o resumo de erros e leva o foco até ele.
    requestAnimationFrame(() => document.getElementById(idCampo("erros"))?.focus());
  }

  async function enviar(ev: FormEvent) {
    ev.preventDefault();
    if (enviando) return;
    const e = validar();
    if (e.length) return mostrarErros(e);
    setErros([]);
    setEnviando(true);

    const itens = todosItens.flatMap((i) => {
      if (i.tipo === "COTA" || i.tipo === "PRODUTO") return (qtd[i.id] ?? 0) > 0 ? [{ itemId: i.id, quantidade: qtd[i.id]! }] : [];
      if (i.tipo === "VALOR_LIVRE") return livre[i.id]?.trim() ? [{ itemId: i.id, quantidade: 1, valorCentavos: lerReais(livre[i.id]!)! }] : [];
      return (meses[i.id]?.length ?? 0) > 0 ? [{ itemId: i.id, quantidade: 1, meses: meses[i.id]! }] : [];
    });
    const identificacao = estaAnonimo ? {} : Object.fromEntries(campos.map((c) => [c.id, ident[c.id].trim()]));

    try {
      const r = await fetch(`/api/escolas/${encodeURIComponent(slug)}/pedidos`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ itens, identificacao, anonimo: estaAnonimo }),
      });
      const corpo = await r.json().catch(() => ({}));
      if (!r.ok) {
        setEnviando(false);
        return mostrarErros([{ campo: "geral", mensagem: corpo.mensagem ?? "Não foi possível gerar o Pix. Tente novamente." }]);
      }
      router.push(`/apm/${encodeURIComponent(slug)}/pedido/${corpo.token}`);
    } catch {
      setEnviando(false);
      mostrarErros([{ campo: "geral", mensagem: "Sem conexão com o servidor. Verifique a internet e tente novamente." }]);
    }
  }

  const alterarQtd = (i: Item, delta: number) =>
    setQtd((q) => ({ ...q, [i.id]: Math.max(0, Math.min(maximoDe(i), (q[i.id] ?? 0) + delta)) }));

  const alternarMes = (i: Item, m: string) =>
    setMeses((s) => {
      const atual = s[i.id] ?? [];
      return { ...s, [i.id]: atual.includes(m) ? atual.filter((x) => x !== m) : [...atual, m].sort() };
    });

  const invalido = (c: string) => (erroDe(c) ? { "aria-invalid": true as const, "aria-describedby": idCampo(`${c}-erro`) } : {});

  return (
    <form onSubmit={enviar} noValidate aria-busy={enviando}>
      {erros.length > 0 && (
        <div id={idCampo("erros")} className="alerta erro" role="alert" tabIndex={-1}>
          <h2>{erros.length === 1 ? "Corrija o item abaixo" : `Corrija os ${erros.length} itens abaixo`}</h2>
          <ul>
            {erros.map((e) => (
              <li key={e.campo + e.mensagem}>
                {e.campo === "geral" ? e.mensagem : <a href={`#${e.campo === "itens" ? idCampo("campanhas") : idCampo(e.campo)}`}>{e.mensagem}</a>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div id={idCampo("campanhas")} tabIndex={-1}>
        {campanhas.map((c) => (
          <section key={c.id} className="cartao" aria-labelledby={idCampo(`c-${c.id}`)}>
            {c.imagemUrl && <img src={c.imagemUrl} alt="" style={{ width: "100%", borderRadius: 8, marginBottom: 12 }} />}
            <h2 id={idCampo(`c-${c.id}`)}>{c.nome}</h2>
            {(c.textoApresentacao ?? c.descricao) && <p className="suave">{c.textoApresentacao ?? c.descricao}</p>}

            {c.itens.some((i) => i.tipo === "COTA" || i.tipo === "PRODUTO") && (
              <ul className="itens">
                {c.itens.filter((i) => i.tipo === "COTA" || i.tipo === "PRODUTO").map((i) => {
                  const q = qtd[i.id] ?? 0;
                  const max = maximoDe(i);
                  const esgotado = i.disponivel === 0;
                  return (
                    <li key={i.id} className="item">
                      <div>
                        <div className="nome" id={idCampo(`n-${i.id}`)}>{i.nome}</div>
                        <div className="preco">
                          {formatarReais(i.precoCentavos!)}
                          {i.tipo === "PRODUTO" && (esgotado ? " · Esgotado" : i.limitePorPedido ? ` · até ${i.limitePorPedido} por pedido` : "")}
                        </div>
                        {i.descricao && <div className="suave">{i.descricao}</div>}
                      </div>
                      <div className="contador" role="group" aria-labelledby={idCampo(`n-${i.id}`)}>
                        <button type="button" onClick={() => alterarQtd(i, -1)} disabled={q === 0}
                          aria-label={`Diminuir quantidade de ${i.nome}`}>−</button>
                        <output aria-live="polite">{q}</output>
                        <button type="button" onClick={() => alterarQtd(i, 1)} disabled={esgotado || q >= max}
                          aria-label={`Aumentar quantidade de ${i.nome}`}>+</button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}

            {c.itens.filter((i) => i.tipo === "VALOR_LIVRE").map((i) => (
              <div key={i.id} className="campo" style={{ marginTop: "1rem" }}>
                <label htmlFor={idCampo(`livre-${i.id}`)}>{i.nome}</label>
                <span className="dica" id={idCampo(`livre-${i.id}-dica`)}>
                  Digite o valor em reais. Mínimo de {formatarReais(escola.valorMinimoLivreCentavos)}.
                </span>
                <input id={idCampo(`livre-${i.id}`)} type="text" inputMode="decimal" autoComplete="off" placeholder="0,00"
                  value={livre[i.id] ?? ""} onChange={(e) => setLivre((s) => ({ ...s, [i.id]: e.target.value }))}
                  aria-describedby={[idCampo(`livre-${i.id}-dica`), erroDe(`livre-${i.id}`) && idCampo(`livre-${i.id}-erro`)].filter(Boolean).join(" ")}
                  aria-invalid={!!erroDe(`livre-${i.id}`)} style={{ maxWidth: 200 }} />
                {erroDe(`livre-${i.id}`) && <p className="erro-campo" id={idCampo(`livre-${i.id}-erro`)}>{erroDe(`livre-${i.id}`)}</p>}
              </div>
            ))}

            {c.itens.filter((i) => i.tipo === "CONTRIBUICAO_MENSAL").map((i) => (
              <fieldset key={i.id} style={{ marginTop: "1rem" }}>
                <legend>{i.nome} – {formatarReais(i.precoCentavos!)} por mês</legend>
                <p className="dica suave">Marque os meses que deseja pagar agora.</p>
                <div className="grade-meses">
                  {Array.from({ length: 12 }, (_, m) => {
                    const valor = `${anoAtual}-${String(m + 1).padStart(2, "0")}`;
                    return (
                      <label key={valor} className="opcao">
                        <input type="checkbox" checked={meses[i.id]?.includes(valor) ?? false} onChange={() => alternarMes(i, valor)} />
                        <span style={{ textTransform: "capitalize" }}>{nomeDoMes(m)}</span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>
            ))}
          </section>
        ))}
      </div>

      <section className="cartao" aria-labelledby={idCampo("t-dados")}>
        <h2 id={idCampo("t-dados")}>Seus dados</h2>
        {podeAnonimo && (
          <label className="opcao" style={{ marginBottom: "0.75rem" }}>
            <input type="checkbox" checked={anonimo} onChange={(e) => setAnonimo(e.target.checked)} />
            <span>Quero contribuir de forma anônima</span>
          </label>
        )}
        {estaAnonimo ? (
          <p className="suave">Nenhum dado pessoal será guardado.</p>
        ) : (
          <>
            {exigeObrigatorios && campos.some(obrigatorio) && (
              <p className="suave">Campos marcados com <span className="obrigatorio" aria-hidden="true">*</span><span className="so-leitor">asterisco</span> são obrigatórios.</p>
            )}
            {campos.map((c) => {
              const id = idCampo(c.id);
              const req = obrigatorio(c);
              const rotulo = (
                <label htmlFor={id}>
                  {c.rotulo}
                  {req ? <span className="obrigatorio" aria-hidden="true"> *</span> : <span className="suave"> (opcional)</span>}
                </label>
              );
              const comum = { id, value: ident[c.id], required: req, ...invalido(c.id),
                onChange: (e: { target: { value: string } }) => setIdent((s) => ({ ...s, [c.id]: e.target.value })) };
              return (
                <div key={c.id} className="campo">
                  {rotulo}
                  {c.id === "turmaId" ? (
                    <select {...comum}>
                      <option value="">Selecione a turma</option>
                      {turmas.map((t) => <option key={t.id} value={t.id}>{t.nome}</option>)}
                    </select>
                  ) : c.id === "observacao" ? (
                    <textarea {...comum} rows={3} maxLength={500} />
                  ) : (
                    <input {...comum}
                      type={c.id === "responsavelEmail" ? "email" : c.id === "responsavelTelefone" ? "tel" : "text"}
                      autoComplete={c.id === "responsavelNome" ? "name" : c.id === "responsavelEmail" ? "email" : c.id === "responsavelTelefone" ? "tel" : "off"}
                      inputMode={c.id === "responsavelTelefone" ? "tel" : c.id === "responsavelEmail" ? "email" : undefined}
                      maxLength={c.id === "responsavelEmail" ? 160 : 120} />
                  )}
                  {erroDe(c.id) && <p className="erro-campo" id={idCampo(`${c.id}-erro`)}>{erroDe(c.id)}</p>}
                </div>
              );
            })}
          </>
        )}
      </section>

      <section className="cartao resumo" aria-labelledby={idCampo("t-resumo")}>
        <h2 id={idCampo("t-resumo")}>Resumo</h2>
        {linhas.length === 0 ? (
          <p className="suave">Nenhum item escolhido ainda.</p>
        ) : (
          <ul className="linhas">
            {linhas.map((l) => (
              <li key={l.chave}><span>{l.descricao}</span><span>{l.valor === null ? "—" : formatarReais(l.valor)}</span></li>
            ))}
          </ul>
        )}
        <p className="total" aria-live="polite" aria-atomic="true">
          <span>Total</span><span>{formatarReais(total)}</span>
        </p>
        <button type="submit" className="botao largo" disabled={enviando}>
          {enviando ? "Gerando o Pix…" : "Gerar Pix"}
        </button>
      </section>
    </form>
  );
}
