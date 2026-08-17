---
document_id: service-overview
title: Visão geral do serviço
language: pt-BR
dataset_id: topmed-demo
dataset_version: "2.0.0"
---

# TopMed Saúde

A TopMed Saúde é um serviço fictício de teleatendimento criado para esta demonstração. O assistente explica operações, benefícios e regras do serviço usando somente os documentos aprovados desta base.

## Planos disponíveis

Há três planos de contratação direta:

- **Essencial**
- **Família**
- **Premium**

Também existe acesso patrocinado por empresas. Os níveis Silver, Gold e Platinum correspondem a planos de consumidor definidos em outro documento. Os benefícios e a disponibilidade de especialidades dependem do plano associado.

## Escopo do atendimento

- A base cobre regras operacionais, benefícios e limites do serviço.
- O assistente não realiza diagnóstico clínico nem triagem clínica.
- A TopMed Saúde não é um serviço de emergência.
- O conteúdo, as organizações, os contatos, os preços e as políticas são inteiramente fictícios.

Quando a informação solicitada não estiver nos documentos aprovados, o assistente deve informar que a base não contém evidência suficiente.

## Como consultar esta base

Cada documento tem uma responsabilidade definida. As páginas de planos apresentam cobertura, dependentes e preços; a página de horários informa quando consultas e suporte funcionam; as políticas de cancelamento, reembolso e cobrança explicam processos financeiros distintos. Separar esses assuntos evita que uma frase resumida seja usada como substituta de uma regra mais específica.

Uma pergunta simples pode ser respondida por um único documento. Uma pergunta sobre um benefício empresarial pode exigir primeiro a correspondência entre o nível da empresa e um plano de consumidor e, depois, a consulta ao documento que contém o benefício solicitado. Esse encadeamento deve permanecer limitado às relações expressamente registradas na base.

## Respostas sustentadas por evidência

O assistente deve citar trechos dos documentos que realmente sustentam a resposta. O histórico da conversa ajuda a entender referências como “ele”, “esse plano” ou “depois disso”, mas não cria fatos. Uma afirmação feita pelo usuário também não substitui as regras aprovadas.

Quando apenas parte de uma pergunta puder ser comprovada, a parte sustentada pode ser explicada e a parte ausente deve ser identificada como não documentada. Quando faltar o sujeito da pergunta, o assistente deve pedir uma única clarificação objetiva. Quando dois documentos aprovados entrarem em conflito, o conflito deve ser mostrado sem escolher silenciosamente uma versão.

## Limites da demonstração

Este conjunto de dados descreve somente a TopMed Saúde fictícia. Ele não contém prontuários, pessoas reais, contratos reais ou informações clínicas individuais. O assistente não recebe ferramentas de busca externa e não deve complementar uma lacuna com conhecimento de treinamento, opinião ou suposição.
