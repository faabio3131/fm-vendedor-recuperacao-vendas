"use client";

import Link from "next/link";
import { useState } from "react";
import { Shell } from "@/components/Shell";

type Model = { title: string; text: string };

const TONES: Model[] = [
  {
    title: "Loja / varejo",
    text:
      "Você é a atendente virtual da [NOME DA LOJA]. Fale de forma simpática, curta e direta, como uma boa vendedora de balcão. " +
      "Trate o cliente pelo nome quando souber. Responda só com o que está cadastrado nas ofertas e nas perguntas e respostas. " +
      "Se não souber, diga que vai confirmar com a equipe, sem inventar. Termine sempre com uma pergunta que leve o cliente ao próximo passo da compra.",
  },
  {
    title: "Serviços / clínica / escritório",
    text:
      "Você é a assistente virtual da [NOME DA EMPRESA]. Fale com educação e calma, em linguagem simples, sem termos técnicos. " +
      "Explique o serviço, os valores e como agendar usando apenas o que está cadastrado. Nunca prometa resultado nem prazo que não esteja cadastrado. " +
      "Se o cliente pedir para falar com uma pessoa, transfira.",
  },
  {
    title: "Curso / produto digital",
    text:
      "Você é a assistente do [NOME DO CURSO OU PRODUTO]. Fale de forma animada e objetiva. Explique para quem é, o que a pessoa vai aprender e como acessar. " +
      "Use só os preços, bônus e garantias cadastrados. Quando o cliente quiser comprar, envie o link de pagamento e pergunte se precisa de ajuda para finalizar.",
  },
];

const OFFER = {
  Nome: "Camiseta Algodão Premium",
  Descrição:
    "Camiseta 100% algodão, tamanhos P ao GG, cores preto, branco e azul. Entrega em até 5 dias úteis. Troca grátis em até 7 dias.",
  "Preço (R$)": "79,90",
  "Link de pagamento (https)": "https://seu-link-de-pagamento.com.br/camiseta",
};

const FAQ: Model[] = [
  { title: "Formas de pagamento", text: "Pergunta: Quais as formas de pagamento?\nResposta: Aceitamos [Pix, cartão em até X vezes, boleto]. O pagamento é feito pelo link seguro que eu envio aqui na conversa." },
  { title: "Prazo de entrega ou início", text: "Pergunta: Qual o prazo de entrega?\nResposta: A entrega é feita em até [X] dias úteis após a confirmação do pagamento. [Se for serviço: o atendimento começa em até X dias.]" },
  { title: "Garantia / troca", text: "Pergunta: Posso trocar ou devolver?\nResposta: Sim. [Você tem X dias para trocar ou pedir devolução, sem custo / explique a sua regra real.]" },
  { title: "Horário de atendimento", text: "Pergunta: Qual o horário de atendimento?\nResposta: Funcionamos de [dia] a [dia], das [hora] às [hora]. Fora desse horário eu continuo respondendo por aqui." },
  { title: "Endereço / onde atende", text: "Pergunta: Onde vocês ficam?\nResposta: Estamos em [endereço completo]. [Atendemos também online / entregamos em todo o Brasil.]" },
  { title: "Como funciona", text: "Pergunta: Como funciona?\nResposta: [Explique em 2 ou 3 frases simples o que você vende, como o cliente compra e o que recebe.]" },
  { title: "Falar com uma pessoa", text: "Pergunta: Quero falar com uma pessoa.\nResposta: Claro! Vou avisar a nossa equipe para continuar com você. [Informe o horário em que a equipe responde.]" },
  { title: "Preço e desconto", text: "Pergunta: Tem desconto?\nResposta: [Informe só os descontos que realmente existem, como Pix à vista ou combos. Se não houver, diga que o valor é o do anúncio.]" },
];

const RECOVERY: Model[] = [
  { title: "Carrinho abandonado", text: "Oi, {nome}! Vi que você começou a comprar {produto} e não finalizou. Posso te ajudar com alguma dúvida? Quando quiser, é só concluir por aqui: {link}" },
  { title: "Pix ou boleto pendente", text: "Oi, {nome}! Seu pedido de {produto} ({valor}) ainda está aguardando o pagamento. Se precisar de ajuda ou de outro jeito de pagar, me avise. Link: {link}" },
  { title: "Assinatura em atraso", text: "Oi, {nome}! Notei que o pagamento da sua assinatura de {produto} está pendente. Para continuar usando sem interrupção, regularize por aqui: {link}" },
];

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        void (async () => {
          try {
            await navigator.clipboard.writeText(text);
            setDone(true);
            setTimeout(() => setDone(false), 2000);
          } catch {
            window.prompt("Copie o texto abaixo (Ctrl+C):", text);
          }
        })();
      }}
    >
      {done ? "Copiado ✓" : "Copiar texto"}
    </button>
  );
}

function Models({ items }: { items: Model[] }) {
  return (
    <div style={{ display: "grid", gap: 10, gridTemplateColumns: "minmax(0, 1fr)" }}>
      {items.map((m) => (
        <div className="card" key={m.title}>
          <strong>{m.title}</strong>
          <p style={{ whiteSpace: "pre-wrap", margin: "8px 0" }}>{m.text}</p>
          <Copy text={m.text} />
        </div>
      ))}
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <section className="card" style={{ marginBottom: 12 }} aria-labelledby={`passo-${n}`}>
      <h2 id={`passo-${n}`} style={{ marginTop: 0 }}>
        {n}. {title}
      </h2>
      {children}
    </section>
  );
}

function Guide() {
  return (
    <>
      <h1>Ajuda e modelos prontos</h1>
      <p className="muted">
        Siga na ordem. Em cerca de 30 minutos a sua atendente está conectada, com o jeito de falar do seu
        negócio e pronta para vender. A qualidade do atendimento depende do que você cadastra: quanto mais
        claro e completo, melhor ela responde.
      </p>

      <Step n={0} title="Separe antes de começar">
        <ul>
          <li>O celular com o WhatsApp que vai atender os clientes (o ideal é um número só do negócio).</li>
          <li>Nome, preço e o que inclui cada produto ou serviço que você vende.</li>
          <li>O link de pagamento de cada um (Asaas, Mercado Pago, Cakto, Hotmart ou o que você usa).</li>
          <li>As 10 perguntas que os clientes mais fazem e as suas respostas reais.</li>
          <li>Seu horário de atendimento, prazo de entrega e regra de troca.</li>
        </ul>
      </Step>

      <Step n={1} title="Conecte o WhatsApp (QR Code)">
        <ol>
          <li>
            No menu, abra <Link href="/connections">Conexões</Link> e escolha o WhatsApp com QR Code.
          </li>
          <li>Clique para gerar o QR Code. Ele aparece na tela e vale por poucos segundos.</li>
          <li>
            No celular, abra o WhatsApp: no Android toque nos três pontinhos, no iPhone toque em
            Configurações. Depois vá em <strong>Aparelhos conectados</strong> e{" "}
            <strong>Conectar um aparelho</strong>.
          </li>
          <li>Aponte a câmera do celular para o QR Code da tela.</li>
          <li>
            Espere aparecer <strong>conectado</strong>. Se o QR Code vencer, gere outro. QR gerado não quer
            dizer conectado: só vale quando o painel mostra a conexão ativa.
          </li>
        </ol>
        <p className="muted">
          Não desconecte o aparelho no celular depois. Se desconectar, volte aqui e leia um novo QR Code.
        </p>
      </Step>

      <Step n={2} title="Defina o jeito de falar da atendente">
        <p>
          Abra <Link href="/seller">Vendedor IA</Link>, vá em <strong>Tom de voz</strong>, cole um dos modelos
          abaixo e troque o que está entre colchetes pelos dados do seu negócio.
        </p>
        <Models items={TONES} />
      </Step>

      <Step n={3} title="Cadastre o que você vende (ofertas)">
        <p>
          Em <Link href="/seller">Vendedor IA</Link>, em <strong>Ofertas</strong>, preencha os quatro campos. A
          atendente só usa o nome, o preço e o link que estiverem aqui. Ela nunca inventa valor nem condição.
        </p>
        <div className="card">
          <strong>Exemplo de oferta preenchida</strong>
          {Object.entries(OFFER).map(([k, v]) => (
            <p key={k} style={{ margin: "6px 0" }}>
              <span className="muted">{k}:</span> {v}
            </p>
          ))}
          <Copy text={Object.entries(OFFER).map(([k, v]) => `${k}: ${v}`).join("\n")} />
        </div>
        <p className="muted">
          Dica: escreva na descrição o que está incluso, tamanhos, prazo e garantia. Isso responde a maioria
          das dúvidas sem precisar de mais nada.
        </p>
      </Step>

      <Step n={4} title="Ensine as respostas do seu negócio (perguntas e respostas)">
        <p>
          Em <Link href="/seller">Vendedor IA</Link>, no quadro de perguntas e respostas, cadastre uma por uma.
          Esta é a parte que dá “sabedoria” à atendente: o que está aqui ela responde com exatidão. Copie os
          modelos, troque o que está entre colchetes e escreva só o que é verdade.
        </p>
        <Models items={FAQ} />
        <p className="muted">
          Ao ver uma pergunta nova nas Conversas que ela não soube responder, cadastre a resposta aqui. Em
          poucas semanas ela cobre quase tudo.
        </p>
      </Step>

      <Step n={5} title="Teste antes de ligar">
        <ol>
          <li>Em Vendedor IA, use a área de testar conversa.</li>
          <li>Pergunte preço, prazo, pagamento, troca e “quero comprar”.</li>
          <li>Confira se os valores e o link estão certos e se o tom é o seu.</li>
          <li>Corrija o que estiver errado nas ofertas ou nas respostas e teste de novo.</li>
          <li>Só então ligue o atendimento automático.</li>
        </ol>
      </Step>

      <Step n={6} title="Recuperação de vendas">
        <p>
          A recuperação chama de volta quem deixou a compra pela metade (carrinho, Pix ou boleto sem pagamento,
          assinatura atrasada). Em <Link href="/recovery">Recuperação</Link>:
        </p>
        <ol>
          <li>
            <strong>Consentimento:</strong> registre como cada contato autorizou receber mensagens. Envie só
            para quem autorizou, e respeite quem pedir para sair (lista “Não contatar”).
          </li>
          <li>
            <strong>Horário de silêncio:</strong> deixe sem envio à noite, por exemplo das 20h às 8h.
          </li>
          <li>
            <strong>Limites:</strong> comece com poucos contatos por dia e poucas mensagens por pessoa. Aumente
            aos poucos, para proteger o seu número.
          </li>
          <li>
            <strong>Mensagens:</strong> já vêm mensagens prontas. Ajuste ao seu jeito usando as variáveis{" "}
            <code>{"{nome}"}</code> <code>{"{produto}"}</code> <code>{"{valor}"}</code> <code>{"{link}"}</code>.
          </li>
          <li>Ative uma sequência por vez e acompanhe os resultados em Relatórios.</li>
        </ol>
        <p>Exemplos de mensagens que funcionam:</p>
        <Models items={RECOVERY} />
      </Step>

      <p className="muted">
        Precisa de ajuda? Acompanhe o que falta em <Link href="/onboarding">Primeiros passos</Link>.
      </p>
    </>
  );
}

export default function Page() {
  return (
    <Shell>
      <Guide />
    </Shell>
  );
}
