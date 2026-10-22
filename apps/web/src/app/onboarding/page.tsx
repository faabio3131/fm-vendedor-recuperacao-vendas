"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Shell } from "@/components/Shell";
import { api, type Onboarding } from "@/lib/api";

const BLOCKS: Record<string, string> = {
  recuperacao: "ligar a recuperação",
  ia: "ligar o vendedor IA",
};
const GUIDES: Record<string, string[]> = {
  whatsapp: [
    "Abra Conexões e escolha o WhatsApp disponibilizado na sua conta: Evolution por QR Code ou Meta Cloud API.",
    "Na opção Evolution, gere o QR Code, leia com o WhatsApp autorizado e aguarde a indicação de sessão conectada. QR gerado ou conexão pendente não é confirmação.",
    "Na opção oficial da Meta, preencha os identificadores e credenciais solicitados e execute o teste de conexão. Nunca cole chaves no campo de mensagens.",
    "Faça o teste real de recebimento e envio somente depois de a operação estar liberada pela plataforma.",
  ],
  ofertas: [
    "Abra Vendedor IA e preencha nome, descrição, preço em reais e URL HTTPS oficial do produto ou checkout.",
    "Revise os dados cadastrados: a IA usa o preço e o link da oferta, não pode criar condições comerciais.",
    "Não ative ofertas de exemplo ou links fictícios. Confira o cadastro com o simulador antes de ligar o atendimento.",
  ],
  consentimento: [
    "Abra Recuperação e registre a origem do consentimento dos seus contatos.",
    "Só envie mensagens de recuperação para pessoas que autorizaram o contato; respeite pedidos de saída e as regras do canal.",
  ],
  templates: [
    "Em Recuperação, personalize as mensagens e confira os campos de nome, produto, valor e link.",
    "Quando usar a API oficial da Meta, os modelos exigidos devem ser aprovados pela Meta antes dos envios pertinentes.",
    "No WhatsApp via Evolution, a aprovação de template da Meta não se aplica, mas consentimento, limites e horários continuam obrigatórios.",
  ],
  persona: [
    "Abra Vendedor IA e escreva o nome, o tom de voz e as orientações de atendimento do seu negócio.",
    "Salve os ajustes mantendo o atendimento automático desligado até cadastrar e revisar pelo menos uma oferta.",
  ],
  checkout: [
    "Abra Conexões, escolha Cakto ou Hotmart e preencha os dados exigidos pela integração, quando disponíveis.",
    "Execute o teste indicado no painel e confira no provedor a configuração dos eventos de pagamento.",
    "A conexão de checkout é opcional para responder conversas, mas necessária para automatizar eventos de compra e recuperação vinculados ao provedor.",
  ],
  ia: [
    "Com uma oferta real ativa e o modelo disponível, teste perguntas, preços, link e transferência para humano na área Testar conversa.",
    "Depois da validação, habilite o vendedor em Vendedor IA. A franquia do plano é aplicada pelo servidor.",
  ],
  recuperacao: [
    "Confirme WhatsApp conectado, consentimento e mensagens adequadas ao canal.",
    "Revise horários de silêncio, limites de contatos e sequências. Só então habilite a recuperação para contatos autorizados.",
  ],
};


function Steps() {
  const [data, setData] = useState<Onboarding | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Onboarding>("/v1/onboarding")
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Erro ao carregar."));
  }, []);

  if (error) return <div className="alert bad">{error}</div>;
  if (!data) return <div className="muted">Carregando…</div>;

  return (
    <>
      <h1>Primeiros passos</h1>
      <p className="muted">
        O que falta para atender e vender sem ajuda. A lista olha o estado real da sua conta, então
        se atualiza sozinha.
      </p>

      <div className="card">
        <strong>
          {data.done} de {data.total} passos prontos
        </strong>
        <div
          className="progress"
          role="progressbar"
          aria-label="Progresso dos primeiros passos"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={data.percent}
        >
          <span style={{ width: `${data.percent}%` }} />
        </div>
        <div className="muted">
          {data.can_enable.recuperacao.allowed
            ? "Recuperação: pronta para ligar."
            : "Recuperação: ainda falta preparar."}{" "}
          {data.can_enable.ia.allowed
            ? "Vendedor IA: pronto para ligar."
            : "Vendedor IA: ainda falta preparar."}
        </div>
      </div>

      <ol className="steps" aria-label="Lista de passos">
        {data.steps.map((s) => (
          <li key={s.key}>
            <span className={`mark ${s.done ? "done" : ""}`} aria-hidden="true">
              {s.done ? "✓" : ""}
            </span>
            <div className="body">
              <strong>{s.title}</strong>{" "}
              <span className={`badge ${s.done ? "connected" : "pending"}`}>
                {s.done ? "Pronto" : "Falta"}
              </span>
              <div className="muted">{s.why}</div>
              {!s.done && (
                <>
                  <div>{s.todo}</div>
                  {s.blocks.length > 0 && (
                    <div className="muted">
                      Sem isso não dá para {s.blocks.map((b) => BLOCKS[b] ?? b).join(" nem ")}.
                    </div>
                  )}
                  <div className="row">
                    <Link className="btn" href={s.href}>
                      {s.action}
                    </Link>
                  </div>
                </>
              )}
              {GUIDES[s.key] && (
                <details className="guide">
                  <summary>Como configurar {s.title.toLowerCase()}</summary>
                  <ol>
                    {GUIDES[s.key].map((instruction, index) => (
                      <li key={index}>{instruction}</li>
                    ))}
                  </ol>
                  <Link href={s.href}>Abrir configuração</Link>
                </details>
              )}
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Steps />
    </Shell>
  );
}
