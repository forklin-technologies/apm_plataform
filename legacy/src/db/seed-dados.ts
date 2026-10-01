/**
 * Dados iniciais da escola piloto. Turmas e valor da mensalidade são EXEMPLOS
 * e devem ser ajustados pela APM no painel.
 */
import { comoSistema } from "@/db/cliente";
import { campanhas, credenciaisPix, escolas, itens, turmas, type CamposFormulario } from "@/db/schema";
import { cifrar } from "@/lib/cripto";
import { gerarSegredoWebhook } from "@/lib/ids";

export const CAMPOS_PADRAO: CamposFormulario = {
  responsavelNome: "obrigatorio", responsavelEmail: "opcional", responsavelTelefone: "opcional",
  alunoNome: "obrigatorio", turma: "obrigatorio", observacao: "opcional",
};

export async function semearEscolaPiloto() {
  return comoSistema(async (tx) => {
    const [escola] = await tx.insert(escolas).values({
      slug: "emeb-aparecida-merino-elias",
      nome: "EMEB Aparecida Merino Elias",
      nomeApm: "Associação de Pais e Mestres (APM)",
      cnpjApm: "03.604.104/0001-01",
      mensagemInicial: "Sua contribuição ajuda a escola a desenvolver projetos e melhorar as experiências dos nossos estudantes.",
      mensagemAgradecimento: "Agradecemos a sua colaboração! A APM reverte os fundos em melhorias para os nossos estudantes.",
      camposFormulario: CAMPOS_PADRAO,
    }).returning();
    const escolaId = escola!.id;

    await tx.insert(turmas).values(["1º A", "1º B", "2º A", "2º B", "3º A", "3º B", "4º A", "4º B", "5º A", "5º B"]
      .map((nome, ordem) => ({ escolaId, nome, ordem })));

    const [apm] = await tx.insert(campanhas).values({
      escolaId, slug: "apm-2026", nome: "APM 2026 – Contribuição Voluntária", status: "ATIVA",
      descricao: "Sua contribuição ajuda a escola a desenvolver projetos e melhorar as experiências dos nossos estudantes.",
      dataInicio: new Date("2026-01-01T00:00:00-03:00"), dataFim: new Date("2026-12-31T23:59:59-03:00"),
    }).returning();

    await tx.insert(itens).values([
      ...[1000, 1200, 1500, 2000, 3000, 5000].map((preco, ordem) => ({
        escolaId, campanhaId: apm!.id, tipo: "COTA" as const, nome: `Cota de R$ ${preco / 100}`, precoCentavos: preco, ordem,
      })),
      { escolaId, campanhaId: apm!.id, tipo: "VALOR_LIVRE" as const, nome: "Outro valor", ordem: 10 },
      { escolaId, campanhaId: apm!.id, tipo: "CONTRIBUICAO_MENSAL" as const, nome: "Contribuição mensal APM",
        precoCentavos: 1000, ordem: 20 },
    ]);

    const [pais] = await tx.insert(campanhas).values({
      escolaId, slug: "dia-dos-pais-2026", nome: "Presente Dia dos Pais", status: "RASCUNHO",
      dataInicio: new Date("2026-07-01T00:00:00-03:00"), dataFim: new Date("2026-08-09T23:59:59-03:00"),
    }).returning();
    await tx.insert(itens).values({ escolaId, campanhaId: pais!.id, tipo: "PRODUTO", nome: "Caneca personalizada",
      precoCentavos: 2500, controlaEstoque: true, estoqueTotal: 100, limitePorPedido: 3 });

    // Enquanto o Banco do Brasil não libera as credenciais, a escola usa o sandbox.
    await tx.insert(credenciaisPix).values({
      escolaId, provedor: "sandbox", ambiente: "homologacao", chavePix: "03604104000101",
      clientIdCifrado: cifrar("-"), clientSecretCifrado: cifrar("-"), segredoWebhook: gerarSegredoWebhook(),
    });
    return escola!;
  });
}
